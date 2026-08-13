"""
troubleshooter/ai_core.py
==========================
Central "AI brain" module for the app — ties together every self-learning
/ intelligence subsystem that previously lived scattered across separate
files with no shared vocabulary or single status view:

  - troubleshooter/local_intelligence.py — main ILT Troubleshooter's
    TF-IDF + k-NN index over the DB-error Knowledge Base, fed with real
    production error frequency from the QA database.
  - troubleshooter/psld_semantic_engine.py — PSLD - Parts' TF-IDF + LSA
    similarity engine, blended with a self-learning feedback boost.
  - troubleshooter/servicenow_resolution_kb.py — PSLD - Parts Resolution
    KB (+ ResolutionDocs / docs/ folder bulk .docx/.doc/.pdf deep-learn
    pipelines).
  - troubleshooter/psld_abend_registry.py — ABEND registry (not itself
    "AI", but part of the same background team-intelligence surface).
  - troubleshooter/psld_review_queue.py — the "double-check" human
    confirmation queue that feeds psld_semantic_engine's feedback log
    (and, through it, this module's supervised classifiers) with
    reviewer-approved ticket -> resolution matches.

MULTI-LEVEL NEURAL / INTELLIGENCE ARCHITECTURE
------------------------------------------------
This module deliberately layers several DIFFERENT machine-learning
paradigms rather than relying on a single model, because each paradigm
"understands" the KB in a different way and none of them alone is
reliable at every stage of the self-learning lifecycle:

  LEVEL 1 — Unsupervised whole-KB clustering (`train_cluster_model`,
  `cluster_status`, `cluster_affinity`): fits a TF-IDF -> TruncatedSVD
  (latent-semantic) -> KMeans pipeline over EVERY Resolution KB entry
  (title + description + steps + full extracted attachment text),
  regardless of whether any human has confirmed a single match yet.
  This is the "deep-learn the whole archive's structure" layer — it
  discovers which KB entries are topically related to one another
  (and, per query, which entries live in the same neighborhood as a
  new ticket) purely from the documents themselves. Because it needs
  no feedback/labels at all, it's the only signal that's useful from
  day one, even on a freshly bulk-imported KB of hundreds of real
  runbooks.

  LEVEL 2 — Supervised ensemble (`train_neural_matcher`,
  `neural_status`): once the team has confirmed enough matches (via
  the Analyze tab's "Confirm this match" button or the
  psld_review_queue "Double-Check" reviewer flow), TWO different
  supervised classifiers are trained on the SAME TF-IDF features of
  confirmed (ticket_text -> entry_id) pairs:
    - `MLPClassifier` (256->128->64 hidden layers) — a genuine
      backprop-trained multi-layer perceptron (deliberately heavier
      than strictly necessary; the team explicitly said it's fine to
      spend real Ryzen CPU cycles here).
    - `RandomForestClassifier` (400 trees, class-balanced) — a
      different high-performance ensemble algorithm (bootstrap
      aggregation over decision trees), included specifically because
      averaging predictions from two structurally different model
      families ("ensemble of ensembles") is more robust than trusting
      either one alone, especially on the small/skewed sample counts
      typical early in a self-learning system's life.
  Their class-probability outputs for the SAME candidate entry are
  simply averaged — a standard, well-understood ensembling technique.

  LEVEL 3 — Adaptive consensus layer (`neural_predict_scores`): the
  final blended "neural" confidence returned to
  psld_semantic_engine.blended_kb_matches() combines Level 1's cluster
  affinity with Level 2's ensemble probability, but the WEIGHT given to
  each shifts as more feedback accumulates: with little/no supervised
  training data, the consensus leans on Level 1 (pure content
  similarity across the whole KB); as confirmed matches pile up, it
  progressively trusts Level 2's learned classifiers more. This keeps
  the system honest about what it actually knows at any given moment,
  rather than pretending a classifier trained on 6 samples is as
  reliable as one trained on 600.

Everything above runs 100% locally via scikit-learn (no GPU, no
external API) — see psld_semantic_engine.py's module docstring for why
a real transformer/PyTorch embedding model can't be installed in this
project (Windows MAX_PATH failure caused by this repo's deeply-nested
OneDrive path).

OTHER RESPONSIBILITIES
-----------------------
`get_unified_ai_status()` — a single call that aggregates the status of
every AI subsystem above (including both Level 1 and Level 2 of this
module) into one dict, so the admin AI Control Center (ui/admin_tab.py)
has one source of truth instead of importing several different modules
and hand-assembling a dashboard.

`force_full_deep_learn()` — the "tie all the learns together" entry
point: re-processes every KB attachment's extracted text/key points
from scratch, retrains Level 1's cluster model over the (possibly much
larger, after a bulk docs/ import) KB corpus, AND retrains Level 2's
supervised ensemble on the full feedback history so far. Wired to the
"Force full deep-learn" button in the admin AI Control Center.
Intentionally allowed to be slow.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
NEURAL_STATE_PATH = ASSETS_DIR / "psld_neural_state.joblib"
CLUSTER_STATE_PATH = ASSETS_DIR / "psld_cluster_state.joblib"

# Below these thresholds a supervised classifier would just memorize
# noise (or fail to fit at all with too few samples per class) —
# report "insufficient data" instead of training garbage silently.
MIN_TOTAL_FEEDBACK_SAMPLES = 6
MIN_DISTINCT_CLASSES = 2

# Level 1 (unsupervised clustering) needs a reasonably sized KB corpus
# to form meaningful clusters at all — below this, every entry is
# effectively its own cluster and the signal is meaningless.
MIN_ENTRIES_FOR_CLUSTERING = 8
MAX_CLUSTERS = 20
ENTRIES_PER_CLUSTER = 15  # roughly one cluster per this many KB entries

try:
    import joblib
    from sklearn.cluster import KMeans
    from sklearn.decomposition import TruncatedSVD
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    from sklearn.neural_network import MLPClassifier
    SKLEARN_AVAILABLE = True
except ImportError:  # pragma: no cover - scikit-learn/joblib already project dependencies
    SKLEARN_AVAILABLE = False

_state: Optional[Dict[str, Any]] = None  # in-memory cache of the loaded Level 2 (supervised) state
_cluster_state: Optional[Dict[str, Any]] = None  # in-memory cache of the loaded Level 1 (clustering) state


# ────────────────────────────────────────────────────────────────────
# Level 2 — supervised ensemble (MLP + RandomForest)
# ────────────────────────────────────────────────────────────────────

def _load_state() -> Dict[str, Any]:
    global _state
    if _state is not None:
        return _state
    if NEURAL_STATE_PATH.exists():
        try:
            _state = joblib.load(NEURAL_STATE_PATH)
            return _state
        except Exception:
            logger.exception("ai_core: failed to load persisted neural state, starting fresh")
    _state = {"trained": False}
    return _state


def _save_state(state: Dict[str, Any]) -> None:
    global _state
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(state, NEURAL_STATE_PATH)
    _state = state


def neural_status() -> Dict[str, Any]:
    """Read-only snapshot of Level 2's supervised ensemble state, for
    display in the admin AI Control Center. Never trains anything."""
    if not SKLEARN_AVAILABLE:
        return {"available": False, "trained": False, "reason": "scikit-learn/joblib not available"}
    state = _load_state()
    if not state.get("trained"):
        return {"available": True, "trained": False}
    return {
        "available": True,
        "trained": True,
        "samples": state.get("samples", 0),
        "classes": state.get("classes", 0),
        "last_trained_at": state.get("last_trained_at"),
        "architecture": state.get("architecture"),
    }


def train_neural_matcher() -> Dict[str, Any]:
    """
    Trains (or retrains from scratch) Level 2's supervised ensemble — an
    MLPClassifier AND a RandomForestClassifier, both fit on the SAME
    TF-IDF features — on the ENTIRE PSLD self-learning feedback history
    so far (psld_semantic_engine.all_feedback()): features are TF-IDF
    vectors of each confirmed ticket's text, labels are the KB entry_id
    that was confirmed as the correct match for it.

    Returns a status dict. If there isn't enough confirmed feedback yet
    (see MIN_TOTAL_FEEDBACK_SAMPLES / MIN_DISTINCT_CLASSES), does NOT
    train and returns {"trained": False, "reason": "insufficient_data", ...}
    instead of silently producing a useless/overfit model.
    """
    if not SKLEARN_AVAILABLE:
        return {"trained": False, "reason": "scikit-learn/joblib not available"}

    from troubleshooter import psld_semantic_engine

    rows = psld_semantic_engine.all_feedback()
    rows = [r for r in rows if (r.get("ticket_text") or "").strip() and r.get("entry_id")]

    distinct_classes = {r["entry_id"] for r in rows}
    if len(rows) < MIN_TOTAL_FEEDBACK_SAMPLES or len(distinct_classes) < MIN_DISTINCT_CLASSES:
        return {
            "trained": False,
            "reason": "insufficient_data",
            "samples": len(rows),
            "classes": len(distinct_classes),
            "needed_samples": MIN_TOTAL_FEEDBACK_SAMPLES,
            "needed_classes": MIN_DISTINCT_CLASSES,
        }

    texts = [r["ticket_text"] for r in rows]
    labels = [r["entry_id"] for r in rows]

    vectorizer = TfidfVectorizer(max_features=3000, ngram_range=(1, 2), stop_words="english", min_df=1)
    X = vectorizer.fit_transform(texts)

    # Deliberately a "heavier than strictly necessary" multi-layer
    # architecture — the team explicitly said it's fine to spend real
    # CPU time (Ryzen) here rather than keep this trivially small.
    # early_stopping carves out an internal validation split so training
    # doesn't run needlessly long once it stops improving.
    hidden_layers = (256, 128, 64)
    mlp = MLPClassifier(
        hidden_layer_sizes=hidden_layers,
        activation="relu",
        solver="adam",
        max_iter=2000,
        early_stopping=len(rows) >= 10,  # needs enough samples for a held-out split
        random_state=42,
    )
    mlp.fit(X, labels)

    # A second, structurally different classifier trained on the exact
    # same features/labels — averaging its predictions with the MLP's
    # is a standard ensembling technique that's more robust than either
    # model alone, especially on the small/skewed sample counts typical
    # early in a self-learning system's life. n_jobs=-1 uses every CPU
    # core available (again, explicitly OK per the team).
    rf = RandomForestClassifier(
        n_estimators=400,
        class_weight="balanced",
        n_jobs=-1,
        random_state=42,
    )
    rf.fit(X, labels)

    state = {
        "trained": True,
        "vectorizer": vectorizer,
        "mlp": mlp,
        "rf": rf,
        "classes": len(distinct_classes),
        "samples": len(rows),
        "architecture": f"MLP{hidden_layers} + RandomForest(400 trees)",
        "last_trained_at": datetime.now().isoformat(timespec="seconds"),
    }
    _save_state(state)
    return neural_status()


def _ensemble_predict_scores(query_text: str, entry_ids: List[str]) -> Dict[str, float]:
    """Level 2 only: averaged MLP+RandomForest class probabilities for
    whichever of `entry_ids` both classifiers recognize as a known
    class. Returns {} if untrained/unavailable."""
    state = _load_state()
    if not state.get("trained"):
        return {}
    try:
        vectorizer = state["vectorizer"]
        vec = vectorizer.transform([query_text])

        mlp_proba = state["mlp"].predict_proba(vec)[0]
        mlp_scores = dict(zip(state["mlp"].classes_, mlp_proba))

        rf_proba = state["rf"].predict_proba(vec)[0]
        rf_scores = dict(zip(state["rf"].classes_, rf_proba))

        out = {}
        for eid in entry_ids:
            m = mlp_scores.get(eid)
            r = rf_scores.get(eid)
            vals = [v for v in (m, r) if v is not None]
            if vals:
                out[eid] = float(sum(vals) / len(vals))
        return out
    except Exception:
        logger.exception("ai_core._ensemble_predict_scores: prediction failed")
        return {}


# ────────────────────────────────────────────────────────────────────
# Level 1 — unsupervised whole-KB clustering
# ────────────────────────────────────────────────────────────────────

def _load_cluster_state() -> Dict[str, Any]:
    global _cluster_state
    if _cluster_state is not None:
        return _cluster_state
    if CLUSTER_STATE_PATH.exists():
        try:
            _cluster_state = joblib.load(CLUSTER_STATE_PATH)
            return _cluster_state
        except Exception:
            logger.exception("ai_core: failed to load persisted cluster state, starting fresh")
    _cluster_state = {"trained": False}
    return _cluster_state


def _save_cluster_state(state: Dict[str, Any]) -> None:
    global _cluster_state
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(state, CLUSTER_STATE_PATH)
    _cluster_state = state


def cluster_status() -> Dict[str, Any]:
    """Read-only snapshot of Level 1's clustering state. Never trains
    anything."""
    if not SKLEARN_AVAILABLE:
        return {"available": False, "trained": False, "reason": "scikit-learn/joblib not available"}
    state = _load_cluster_state()
    if not state.get("trained"):
        return {"available": True, "trained": False}
    return {
        "available": True,
        "trained": True,
        "entries": state.get("n_entries", 0),
        "clusters": state.get("n_clusters", 0),
        "last_trained_at": state.get("last_trained_at"),
        "architecture": state.get("architecture"),
    }


def train_cluster_model() -> Dict[str, Any]:
    """
    Fits Level 1's TF-IDF -> TruncatedSVD -> KMeans pipeline over the
    ENTIRE current Resolution KB corpus (every entry's title +
    description + steps + full extracted attachment text), and stores
    each entry's own reduced (latent-semantic) vector so
    cluster_affinity() can score new queries against the whole KB with
    a single matrix multiply — no need to refit anything per query,
    which matters once the KB has hundreds of real imported runbooks.

    Needs no human feedback/labels at all — purely unsupervised, so
    it's useful from the moment the KB has enough entries, even before
    a single ticket match has been confirmed. Returns {"trained": False,
    "reason": "insufficient_data", ...} if the KB is still too small
    for clustering to mean anything.
    """
    if not SKLEARN_AVAILABLE:
        return {"trained": False, "reason": "scikit-learn/joblib not available"}

    from troubleshooter import servicenow_resolution_kb

    entries = servicenow_resolution_kb.list_entries()
    if len(entries) < MIN_ENTRIES_FOR_CLUSTERING:
        return {
            "trained": False,
            "reason": "insufficient_data",
            "entries": len(entries),
            "needed_entries": MIN_ENTRIES_FOR_CLUSTERING,
        }

    entry_ids = [e["id"] for e in entries]
    texts = [servicenow_resolution_kb.corpus_text(e) for e in entries]

    vectorizer = TfidfVectorizer(max_features=6000, ngram_range=(1, 2), stop_words="english", min_df=1)
    try:
        X = vectorizer.fit_transform(texts)
    except ValueError:
        return {"trained": False, "reason": "empty_corpus"}

    n_components = max(2, min(64, X.shape[1] - 1, len(entries) - 1))
    svd = TruncatedSVD(n_components=n_components, random_state=42)
    reduced = svd.fit_transform(X)

    n_clusters = max(2, min(MAX_CLUSTERS, len(entries) // ENTRIES_PER_CLUSTER or 1))
    kmeans = KMeans(n_clusters=n_clusters, n_init=10, random_state=42)
    cluster_labels = kmeans.fit_predict(reduced)

    entry_vectors = {eid: reduced[i] for i, eid in enumerate(entry_ids)}
    entry_clusters = {eid: int(cluster_labels[i]) for i, eid in enumerate(entry_ids)}

    state = {
        "trained": True,
        "vectorizer": vectorizer,
        "svd": svd,
        "kmeans": kmeans,
        "entry_vectors": entry_vectors,
        "entry_clusters": entry_clusters,
        "n_entries": len(entries),
        "n_clusters": n_clusters,
        "architecture": f"TFIDF+SVD({n_components})+KMeans(k={n_clusters})",
        "last_trained_at": datetime.now().isoformat(timespec="seconds"),
    }
    _save_cluster_state(state)
    return cluster_status()


def cluster_affinity(query_text: str, entry_ids: List[str]) -> Dict[str, float]:
    """
    Level 1 only: for each of `entry_ids`, the cosine similarity
    between `query_text`'s projection into the whole-KB latent-semantic
    space and that entry's own precomputed vector in the same space —
    a "how close is this ticket to this KB entry, relative to the
    ENTIRE archive's structure" signal, distinct from (and computed
    completely independently of) psld_semantic_engine's own per-query
    LSA similarity (which only ever looks at the ~20 TF-IDF-shortlisted
    candidates for a single query, refit from scratch each time).
    Returns {} if the cluster model hasn't been trained yet or the
    query is empty.
    """
    if not SKLEARN_AVAILABLE or not (query_text or "").strip():
        return {}
    state = _load_cluster_state()
    if not state.get("trained"):
        return {}
    try:
        vec = state["vectorizer"].transform([query_text])
        reduced_query = state["svd"].transform(vec)
        entry_vectors = state["entry_vectors"]
        out: Dict[str, float] = {}
        for eid in entry_ids:
            ev = entry_vectors.get(eid)
            if ev is None:
                continue
            sim = float(cosine_similarity(reduced_query, ev.reshape(1, -1))[0][0])
            # TF-IDF is non-negative but SVD components can still yield
            # small negative cosine values for unrelated documents —
            # clip to keep this a clean [0, 1] signal like the others.
            out[eid] = max(0.0, min(1.0, sim))
        return out
    except Exception:
        logger.exception("ai_core.cluster_affinity: scoring failed")
        return {}


# ────────────────────────────────────────────────────────────────────
# Level 3 — adaptive consensus (combines Level 1 + Level 2)
# ────────────────────────────────────────────────────────────────────

def neural_predict_scores(query_text: str, entry_ids: List[str]) -> Dict[str, float]:
    """
    The single entry point psld_semantic_engine.blended_kb_matches()
    calls for "the AI's opinion" on each candidate entry — internally a
    3-level pipeline (see module docstring):

      1. Level 1 cluster_affinity() — whole-KB content-similarity,
         always available once the KB is big enough, no feedback
         needed.
      2. Level 2 _ensemble_predict_scores() — averaged MLP +
         RandomForest probability, available once enough feedback has
         been confirmed.
      3. This function blends the two, weighting Level 2 more heavily
         the more confirmed feedback it was trained on (capped at 80%
         weight once well past the minimum threshold) and falling back
         entirely to Level 1 (or returning {} if neither is available)
         otherwise — an "adaptive consensus" that's honest about how
         much the system actually knows at any given moment.

    Returns {} if neither level has anything to say yet (e.g. a brand
    new KB with no entries and no feedback) — callers must treat that
    as "no neural signal", not an error.
    """
    if not SKLEARN_AVAILABLE or not (query_text or "").strip() or not entry_ids:
        return {}

    cluster_scores = cluster_affinity(query_text, entry_ids)
    ensemble_scores = _ensemble_predict_scores(query_text, entry_ids)

    if not cluster_scores and not ensemble_scores:
        return {}

    # Ramp Level 2's weight up from 0 (untrained) to 0.8 (well-trained)
    # as its training-sample count grows past the minimum threshold —
    # capped so Level 1's whole-archive content signal always has SOME
    # say, even on a heavily-confirmed system.
    supervised_state = _load_state()
    samples = supervised_state.get("samples", 0) if supervised_state.get("trained") else 0
    ramp_target = MIN_TOTAL_FEEDBACK_SAMPLES * 10  # fully ramped up by 10x the minimum
    ensemble_weight = 0.8 * min(1.0, samples / ramp_target) if ensemble_scores else 0.0

    all_ids = set(cluster_scores) | set(ensemble_scores)
    out: Dict[str, float] = {}
    for eid in all_ids:
        c = cluster_scores.get(eid)
        e = ensemble_scores.get(eid)
        if c is not None and e is not None:
            out[eid] = ensemble_weight * e + (1 - ensemble_weight) * c
        elif e is not None:
            out[eid] = e
        elif c is not None:
            out[eid] = c
    return out


def force_full_deep_learn(created_by: str = "SYSTEM") -> Dict[str, Any]:
    """
    The single "tie all the learns together" action wired to the admin
    AI Control Center's "Force full deep-learn" button:
      1. Re-processes every existing Resolution KB entry's attachment
         from scratch (servicenow_resolution_kb.reprocess_all_entries())
         — refreshes extracted_text/key_points even for entries already
         processed before, in case the extraction logic has improved.
      2. Retrains Level 1's unsupervised cluster model
         (train_cluster_model()) over the current (possibly much
         larger, after a bulk docs/ folder import) KB corpus.
      3. Retrains Level 2's supervised ensemble
         (train_neural_matcher()) on the full self-learning feedback
         history, so newly confirmed matches are reflected immediately
         rather than waiting for the next incidental retrain.
    Intentionally allowed to take a while — correctness/completeness of
    the re-analysis matters more than speed for a manually-triggered
    admin action.
    """
    from troubleshooter import servicenow_resolution_kb

    reprocess_result = servicenow_resolution_kb.reprocess_all_entries()
    cluster_result = train_cluster_model()
    neural_result = train_neural_matcher()
    return {
        "reprocess": reprocess_result,
        "cluster": cluster_result,
        "neural": neural_result,
    }


def get_unified_ai_status() -> Dict[str, Any]:
    """
    Aggregates the status of every AI/self-learning subsystem in the app
    into one dict — the single source of truth for the admin AI Control
    Center dashboard. Read-only; never trains/mutates anything.
    """
    from troubleshooter import (
        local_intelligence,
        psld_abend_registry,
        psld_review_queue,
        psld_semantic_engine,
        servicenow_resolution_kb,
    )

    try:
        ilt_status = local_intelligence.get_status()
    except Exception:
        logger.exception("get_unified_ai_status: local_intelligence status failed")
        ilt_status = {"trained": False, "error": "status unavailable"}

    try:
        psld_sem_status = psld_semantic_engine.semantic_status()
    except Exception:
        psld_sem_status = {"available": False, "reason": "status unavailable"}

    try:
        kb_stats = servicenow_resolution_kb.kb_stats()
    except Exception:
        kb_stats = {}

    try:
        fb_count = psld_semantic_engine.feedback_count()
    except Exception:
        fb_count = 0

    try:
        abend_total = len(psld_abend_registry.list_abends())
        abend_pending = len(psld_abend_registry.list_pending_program_abends())
    except Exception:
        abend_total, abend_pending = 0, 0

    try:
        review_stats = psld_review_queue.queue_stats()
    except Exception:
        review_stats = {}

    return {
        "ilt_local_intelligence": ilt_status,
        "psld_semantic_engine": psld_sem_status,
        "psld_neural": neural_status(),
        "psld_cluster": cluster_status(),
        "psld_kb_stats": kb_stats,
        "psld_feedback_count": fb_count,
        "psld_abend_total": abend_total,
        "psld_abend_pending": abend_pending,
        "psld_review_queue": review_stats,
    }
