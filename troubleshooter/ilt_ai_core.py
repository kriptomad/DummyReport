"""
troubleshooter/ilt_ai_core.py
==============================
ILT Troubleshooter's own multi-level neural/intelligence pipeline —
architecturally IDENTICAL to troubleshooter/ai_core.py (PSLD - Parts'
brain), but trained on a COMPLETELY SEPARATE corpus and feedback stream,
with its own persisted state files. This module exists specifically so
both apps' AI have the same level of sophistication/precision, per an
explicit requirement: the two must be independent AIs/neural networks,
never sharing data, but built to the same standard.

WHY A SEPARATE MODULE INSTEAD OF EXTENDING ai_core.py DIRECTLY
-----------------------------------------------------------------
Keeping this as its own file (rather than adding "if psld/if ilt"
branches inside ai_core.py) is a deliberate isolation boundary: there is
no shared state, no shared module-level cache, and no code path where a
bug in one accidentally reads/writes the other's files. The two modules
happen to share the SAME algorithmic recipe (by design, for parity), but
that's the only thing they share.

DATA SOURCES (deliberately disjoint from ai_core.py's)
-----------------------------------------------------------------
  - Corpus (Level 1, unsupervised): the ILT Troubleshooter's own
    Knowledge Base (assets/stepsdummy.xlsx "Main" sheet, loaded via
    troubleshooter.loader.load_troubleshoot_db()) — error pattern +
    meaning + how-to-check + action text. This NEVER includes anything
    from servicenow_resolution_kb (PSLD's KB) or the ResolutionDocs/
    folder.
  - Labels (Level 2, supervised): troubleshooter.feedback_store.
    all_correction_feedback() — confirmed (err_msg -> matched KB error
    pattern) pairs logged whenever an analyst's correction
    (submit_correction()) gets attached to an EXISTING KB entry via the
    ILT Troubleshooter's own "report a correction" flow. This is
    entirely separate from psld_semantic_engine.all_feedback() (PSLD's
    "Confirm this match"/Double-Check reviewer flow).

MULTI-LEVEL ARCHITECTURE (identical recipe to ai_core.py, for parity)
-----------------------------------------------------------------
  LEVEL 1 — Unsupervised whole-KB clustering (`train_cluster_model`,
  `cluster_status`, `cluster_affinity`): TF-IDF -> TruncatedSVD (latent-
  semantic) -> KMeans over every ILT KB entry's full text. Needs no
  feedback/labels — useful from day one.

  LEVEL 2 — Supervised ensemble (`train_neural_matcher`,
  `neural_status`): an MLPClassifier (256->128->64) AND a
  RandomForestClassifier (400 trees, class-balanced), both fit on the
  same TF-IDF features of confirmed (err_msg -> KB pattern) pairs, with
  their class-probability outputs averaged.

  LEVEL 3 — Adaptive consensus (`neural_predict_scores`): blends Level
  1's cluster affinity with Level 2's ensemble probability, shifting
  weight towards Level 2 as more confirmed feedback accumulates.

Everything runs 100% locally via scikit-learn — no GPU, no external API,
identical constraints to ai_core.py (see that module's docstring for
why a transformer/PyTorch embedding model isn't installed in this repo).

`force_full_deep_learn()` retrains both levels together — wired to the
"Force full deep-learn" button in the ILT section of the admin AI
Control Center, exactly mirroring PSLD's button of the same name.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
NEURAL_STATE_PATH = ASSETS_DIR / "ilt_neural_state.joblib"
CLUSTER_STATE_PATH = ASSETS_DIR / "ilt_cluster_state.joblib"

# Identical thresholds to ai_core.py, on purpose — this is the crux of
# "same level of precision": neither AI trains a supervised classifier
# on less data than the other, and neither clusters a too-small KB.
MIN_TOTAL_FEEDBACK_SAMPLES = 6
MIN_DISTINCT_CLASSES = 2

MIN_ENTRIES_FOR_CLUSTERING = 8
MAX_CLUSTERS = 20
ENTRIES_PER_CLUSTER = 15

# Safety cap for the "mega deep-learn" DB scan (troubleshooter/
# error_normalizer.py's DB grouping) — this is a genuine full-history
# scan, not a small sample, but still bounded so a single click can't
# run an unbounded query against production Oracle. 50k distinct
# ERR_MSG values is comfortably beyond anything this schema has ever
# needed in practice; raise if a future environment's table is bigger.
DEFAULT_MEGA_DB_LIMIT = 50_000

# An autonomous fix is only auto-drafted when BOTH the confidence is
# high enough (see autonomous_fix.py) AND the pattern has actually
# recurred at least this many times in the DB scan — a true one-off is
# left for a human to triage via the existing "Pending" gap worklist
# instead of auto-drafting anything from a single occurrence.
MIN_OCCURRENCES_FOR_AUTONOMOUS_FIX = 2

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

from troubleshooter.error_normalizer import aggregate_by_normalized_pattern, normalize_error_text

_state: Optional[Dict[str, Any]] = None  # in-memory cache of the loaded Level 2 (supervised) state
_cluster_state: Optional[Dict[str, Any]] = None  # in-memory cache of the loaded Level 1 (clustering) state


def _kb_corpus_text(row) -> str:
    from troubleshooter.loader import COL_ACTION, COL_ERROR_PATTERN, COL_HOW_TO_CHECK, COL_MEANING
    parts = [
        str(row.get(COL_ERROR_PATTERN, "")),
        str(row.get(COL_MEANING, "")),
        str(row.get(COL_HOW_TO_CHECK, "")),
        str(row.get(COL_ACTION, "")),
    ]
    return " ".join(p for p in parts if p and p.lower() != "nan")


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
            logger.exception("ilt_ai_core: failed to load persisted neural state, starting fresh")
    _state = {"trained": False}
    return _state


def _save_state(state: Dict[str, Any]) -> None:
    global _state
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(state, NEURAL_STATE_PATH)
    _state = state


def neural_status() -> Dict[str, Any]:
    """Read-only snapshot of Level 2's supervised ensemble state. Never
    trains anything."""
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
        "training_log": state.get("training_log", []),
        "train_duration_seconds": state.get("train_duration_seconds"),
    }


def train_neural_matcher(on_step: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    """
    Trains (or retrains from scratch) Level 2's supervised ensemble on
    the ENTIRE ILT Troubleshooter self-learning feedback history so far
    (troubleshooter.feedback_store.all_correction_feedback()): features
    are TF-IDF vectors of each confirmed correction's err_msg text,
    labels are the KB error pattern that correction was attached to.

    Returns {"trained": False, "reason": "insufficient_data", ...}
    below MIN_TOTAL_FEEDBACK_SAMPLES/MIN_DISTINCT_CLASSES, same as
    ai_core.train_neural_matcher() — never trains garbage silently.

    Every real step is individually timed (see `training_log` in the
    returned/persisted state) — concrete, inspectable proof (row counts,
    feature counts, tree counts, wall-clock seconds per step) that this
    is a real scikit-learn fit and not a mock, for anyone (dev/support)
    who wants to verify it in the AI Control Center.
    """
    if not SKLEARN_AVAILABLE:
        return {"trained": False, "reason": "scikit-learn/joblib not available"}

    from troubleshooter import feedback_store

    run_started = time.perf_counter()
    training_log: List[Dict[str, Any]] = []

    t0 = time.perf_counter()
    rows = feedback_store.all_correction_feedback()
    distinct_classes = {r["matched_pattern"] for r in rows}
    training_log.append({
        "step": "load_feedback",
        "detail": f"{len(rows)} confirmed corrections, {len(distinct_classes)} distinct KB pattern classes",
        "seconds": round(time.perf_counter() - t0, 3),
    })
    if on_step:
        on_step(training_log[-1])

    if len(rows) < MIN_TOTAL_FEEDBACK_SAMPLES or len(distinct_classes) < MIN_DISTINCT_CLASSES:
        return {
            "trained": False,
            "reason": "insufficient_data",
            "samples": len(rows),
            "classes": len(distinct_classes),
            "needed_samples": MIN_TOTAL_FEEDBACK_SAMPLES,
            "needed_classes": MIN_DISTINCT_CLASSES,
        }

    texts = [r["err_msg"] for r in rows]
    labels = [r["matched_pattern"] for r in rows]

    t0 = time.perf_counter()
    vectorizer = TfidfVectorizer(max_features=3000, ngram_range=(1, 2), stop_words="english", min_df=1)
    X = vectorizer.fit_transform(texts)
    training_log.append({
        "step": "tfidf_vectorize",
        "detail": f"{X.shape[0]} documents x {X.shape[1]} features",
        "seconds": round(time.perf_counter() - t0, 3),
    })
    if on_step:
        on_step(training_log[-1])

    # Identical architecture to ai_core.py's Level 2, on purpose.
    hidden_layers = (256, 128, 64)
    t0 = time.perf_counter()
    mlp = MLPClassifier(
        hidden_layer_sizes=hidden_layers,
        activation="relu",
        solver="adam",
        max_iter=2000,
        early_stopping=len(rows) >= 10,
        random_state=42,
    )
    mlp.fit(X, labels)
    training_log.append({
        "step": "mlp_fit",
        "detail": f"hidden_layers={hidden_layers}, {getattr(mlp, 'n_iter_', '?')} iterations to converge",
        "seconds": round(time.perf_counter() - t0, 3),
    })
    if on_step:
        on_step(training_log[-1])

    t0 = time.perf_counter()
    rf = RandomForestClassifier(
        n_estimators=400,
        class_weight="balanced",
        n_jobs=-1,
        random_state=42,
    )
    rf.fit(X, labels)
    training_log.append({
        "step": "random_forest_fit",
        "detail": "400 trees, class_weight=balanced, n_jobs=-1 (all CPU cores)",
        "seconds": round(time.perf_counter() - t0, 3),
    })
    if on_step:
        on_step(training_log[-1])

    total_seconds = round(time.perf_counter() - run_started, 3)
    state = {
        "trained": True,
        "vectorizer": vectorizer,
        "mlp": mlp,
        "rf": rf,
        "classes": len(distinct_classes),
        "samples": len(rows),
        "architecture": f"MLP{hidden_layers} + RandomForest(400 trees)",
        "last_trained_at": datetime.now().isoformat(timespec="seconds"),
        "training_log": training_log,
        "train_duration_seconds": total_seconds,
    }
    _save_state(state)
    return neural_status()


def _ensemble_predict_scores(query_text: str, patterns: List[str]) -> Dict[str, float]:
    """Level 2 only: averaged MLP+RandomForest class probabilities for
    whichever of `patterns` both classifiers recognize as a known
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
        for p in patterns:
            m = mlp_scores.get(p)
            r = rf_scores.get(p)
            vals = [v for v in (m, r) if v is not None]
            if vals:
                out[p] = float(sum(vals) / len(vals))
        return out
    except Exception:
        logger.exception("ilt_ai_core._ensemble_predict_scores: prediction failed")
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
            logger.exception("ilt_ai_core: failed to load persisted cluster state, starting fresh")
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
        "kb_entries": state.get("n_kb_entries", state.get("n_entries", 0)),
        "clusters": state.get("n_clusters", 0),
        "db_docs_scanned": state.get("db_docs_scanned", 0),
        "db_groups_added": state.get("db_groups_added", 0),
        "db_error": state.get("db_error"),
        "last_trained_at": state.get("last_trained_at"),
        "architecture": state.get("architecture"),
        "training_log": state.get("training_log", []),
        "train_duration_seconds": state.get("train_duration_seconds"),
    }


def train_cluster_model(conn=None, db_limit: int = DEFAULT_MEGA_DB_LIMIT, on_step: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    """
    Fits Level 1's TF-IDF -> TruncatedSVD -> KMeans pipeline over the
    ENTIRE current ILT Troubleshooter KB (assets/stepsdummy.xlsx "Main"
    sheet) — every entry's error pattern + meaning + how-to-check +
    action text. Uses the error pattern string itself as the stable
    "entry_id" (consistent with how troubleshooter.kb_ownership already
    keys KB rows by pattern text elsewhere in this codebase).

    When `conn` (a live Oracle connection) is given, this ALSO mega-scans
    the real QA database's entire distinct ERR_MSG history (bounded only
    by `db_limit`, a safety cap — not a "sample", the whole table's
    distinct error text up to that many rows), throws out anything
    is_success_message() flags as a success/completion note (cataloging
    a success as a "failure" makes no sense), and normalizes each
    remaining error (error_normalizer.normalize_error_text) so that
    "the same real problem" reported with a different shipment
    number/date/location code collapses into ONE representative document
    instead of hundreds of near-duplicates. Each surviving normalized
    group that doesn't already exactly match an existing KB pattern is
    added to the clustering corpus as its own unlabeled entry (id
    "db::<normalized text>"), broadening what Level 1 has "seen" far
    beyond the (much smaller) curated KB — this is the "mega learn on
    the DB" requested for the deep-learn button. The grouped DB data
    itself is cached in the returned/persisted state
    (state["db_error_groups"]) so autonomous_fix.py can reuse it without
    re-querying the database.

    Needs no human feedback/labels — purely unsupervised. Returns
    {"trained": False, "reason": "insufficient_data", ...} if the KB (+
    any DB groups found) is still too small for clustering to mean
    anything.
    """
    if not SKLEARN_AVAILABLE:
        return {"trained": False, "reason": "scikit-learn/joblib not available"}

    from troubleshooter.loader import COL_ERROR_PATTERN, is_success_message, load_troubleshoot_db

    run_started = time.perf_counter()
    training_log: List[Dict[str, Any]] = []

    t0 = time.perf_counter()
    df_ts = load_troubleshoot_db()
    patterns = [str(p).strip() for p in df_ts.get(COL_ERROR_PATTERN, [])]
    # De-duplicate while preserving the row <-> text pairing (a pattern
    # could theoretically repeat) — keep the first occurrence's text.
    seen = set()
    entry_ids: List[str] = []
    texts: List[str] = []
    for i, (_, row) in enumerate(df_ts.iterrows()):
        pattern = patterns[i] if i < len(patterns) else ""
        if not pattern or pattern in seen:
            continue
        seen.add(pattern)
        entry_ids.append(pattern)
        texts.append(_kb_corpus_text(row) or "empty")
    training_log.append({
        "step": "load_kb",
        "detail": f"{len(seen)} distinct KB entries loaded from stepsdummy.xlsx",
        "seconds": round(time.perf_counter() - t0, 3),
    })
    if on_step:
        on_step(training_log[-1])

    kb_normalized_patterns = {normalize_error_text(p) for p in entry_ids}

    db_docs_scanned = 0
    db_groups_added = 0
    db_error = None
    db_error_groups: Dict[str, Any] = {}
    if conn is not None:
        t0 = time.perf_counter()
        try:
            from troubleshooter.local_intelligence import _fetch_db_error_frequencies

            freq_df = _fetch_db_error_frequencies(conn, limit=db_limit)
            db_query_seconds = round(time.perf_counter() - t0, 3)
            rows_before_filter = int(freq_df["N"].sum()) if not freq_df.empty else 0
            distinct_before_filter = len(freq_df)
            if not freq_df.empty:
                success_mask = freq_df["ERR_MSG"].apply(is_success_message)
                freq_df = freq_df.loc[~success_mask].reset_index(drop=True)
            db_docs_scanned = int(freq_df["N"].sum()) if not freq_df.empty else 0
            training_log.append({
                "step": "db_query",
                "detail": (
                    f"{distinct_before_filter} distinct ERR_MSG rows read (limit={db_limit}), "
                    f"{rows_before_filter} total occurrences before success-filter, "
                    f"{db_docs_scanned} occurrences kept after filtering out success/completion messages"
                ),
                "seconds": db_query_seconds,
            })
            if on_step:
                on_step(training_log[-1])

            t0 = time.perf_counter()
            grouped = aggregate_by_normalized_pattern(freq_df)
            for g in grouped:
                norm = g["normalized_pattern"]
                if norm in kb_normalized_patterns:
                    # Already an exact (post-normalization) match for an
                    # existing KB pattern — nothing new for Level 1 to
                    # learn from this group; skip adding a duplicate doc.
                    continue
                db_error_groups[norm] = g
                entry_ids.append(f"db::{norm}")
                texts.append(g["representative_err_msg"])
                db_groups_added += 1
            training_log.append({
                "step": "normalize_and_group",
                "detail": (
                    f"{len(grouped)} distinct normalized error patterns found in the DB scan, "
                    f"{db_groups_added} of them are genuinely new (not already an existing KB pattern)"
                ),
                "seconds": round(time.perf_counter() - t0, 3),
            })
            if on_step:
                on_step(training_log[-1])
        except Exception as e:
            logger.exception("ilt_ai_core.train_cluster_model: DB mega-scan failed")
            db_error = f"{type(e).__name__}: {e}"
            training_log.append({"step": "db_query", "detail": f"FAILED: {db_error}", "seconds": round(time.perf_counter() - t0, 3)})
            if on_step:
                on_step(training_log[-1])

    if len(entry_ids) < MIN_ENTRIES_FOR_CLUSTERING:
        return {
            "trained": False,
            "reason": "insufficient_data",
            "entries": len(entry_ids),
            "needed_entries": MIN_ENTRIES_FOR_CLUSTERING,
        }

    t0 = time.perf_counter()
    vectorizer = TfidfVectorizer(max_features=6000, ngram_range=(1, 2), stop_words="english", min_df=1)
    try:
        X = vectorizer.fit_transform(texts)
    except ValueError:
        return {"trained": False, "reason": "empty_corpus"}
    training_log.append({
        "step": "tfidf_vectorize",
        "detail": f"{X.shape[0]} documents x {X.shape[1]} features",
        "seconds": round(time.perf_counter() - t0, 3),
    })
    if on_step:
        on_step(training_log[-1])

    t0 = time.perf_counter()
    n_components = max(2, min(64, X.shape[1] - 1, len(entry_ids) - 1))
    svd = TruncatedSVD(n_components=n_components, random_state=42)
    reduced = svd.fit_transform(X)
    training_log.append({
        "step": "svd_reduce",
        "detail": f"{n_components} latent components, explained variance ratio sum={svd.explained_variance_ratio_.sum():.3f}",
        "seconds": round(time.perf_counter() - t0, 3),
    })
    if on_step:
        on_step(training_log[-1])

    t0 = time.perf_counter()
    n_clusters = max(2, min(MAX_CLUSTERS, len(entry_ids) // ENTRIES_PER_CLUSTER or 1))
    kmeans = KMeans(n_clusters=n_clusters, n_init=10, random_state=42)
    cluster_labels = kmeans.fit_predict(reduced)
    training_log.append({
        "step": "kmeans_fit",
        "detail": f"k={n_clusters}, n_init=10, inertia={kmeans.inertia_:.2f}",
        "seconds": round(time.perf_counter() - t0, 3),
    })
    if on_step:
        on_step(training_log[-1])

    entry_vectors = {eid: reduced[i] for i, eid in enumerate(entry_ids)}
    entry_clusters = {eid: int(cluster_labels[i]) for i, eid in enumerate(entry_ids)}

    total_seconds = round(time.perf_counter() - run_started, 3)
    state = {
        "trained": True,
        "vectorizer": vectorizer,
        "svd": svd,
        "kmeans": kmeans,
        "entry_vectors": entry_vectors,
        "entry_clusters": entry_clusters,
        "kb_patterns": list(seen),
        "n_entries": len(entry_ids),
        "n_kb_entries": len(seen),
        "n_clusters": n_clusters,
        "db_docs_scanned": db_docs_scanned,
        "db_groups_added": db_groups_added,
        "db_error": db_error,
        "db_error_groups": db_error_groups,
        "architecture": f"TFIDF+SVD({n_components})+KMeans(k={n_clusters})",
        "last_trained_at": datetime.now().isoformat(timespec="seconds"),
        "training_log": training_log,
        "train_duration_seconds": total_seconds,
    }
    _save_cluster_state(state)
    return cluster_status()


def cluster_affinity(query_text: str, patterns: List[str]) -> Dict[str, float]:
    """
    Level 1 only: cosine similarity between query_text's projection
    into the whole-KB latent-semantic space and each of `patterns`'
    precomputed vector in that same space. Returns {} if the cluster
    model hasn't been trained yet or the query is empty.
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
        for p in patterns:
            ev = entry_vectors.get(p)
            if ev is None:
                continue
            sim = float(cosine_similarity(reduced_query, ev.reshape(1, -1))[0][0])
            out[p] = max(0.0, min(1.0, sim))
        return out
    except Exception:
        logger.exception("ilt_ai_core.cluster_affinity: scoring failed")
        return {}


# ────────────────────────────────────────────────────────────────────
# Level 3 — adaptive consensus (combines Level 1 + Level 2)
# ────────────────────────────────────────────────────────────────────

def neural_predict_scores(query_text: str, patterns: List[str]) -> Dict[str, float]:
    """
    The single entry point troubleshooter.local_intelligence.
    score_against_kb() calls for "the AI's opinion" on each candidate KB
    pattern — internally the same 3-level blend as ai_core.
    neural_predict_scores() (see module docstring), just fed from ILT's
    own KB/feedback instead of PSLD's.

    Returns {} if neither level has anything to say yet.
    """
    if not SKLEARN_AVAILABLE or not (query_text or "").strip() or not patterns:
        return {}

    cluster_scores = cluster_affinity(query_text, patterns)
    ensemble_scores = _ensemble_predict_scores(query_text, patterns)

    if not cluster_scores and not ensemble_scores:
        return {}

    supervised_state = _load_state()
    samples = supervised_state.get("samples", 0) if supervised_state.get("trained") else 0
    ramp_target = MIN_TOTAL_FEEDBACK_SAMPLES * 10
    ensemble_weight = 0.8 * min(1.0, samples / ramp_target) if ensemble_scores else 0.0

    all_patterns = set(cluster_scores) | set(ensemble_scores)
    out: Dict[str, float] = {}
    for p in all_patterns:
        c = cluster_scores.get(p)
        e = ensemble_scores.get(p)
        if c is not None and e is not None:
            out[p] = ensemble_weight * e + (1 - ensemble_weight) * c
        elif e is not None:
            out[p] = e
        elif c is not None:
            out[p] = c
    return out


def force_full_deep_learn(on_step: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    """
    The ILT-side "tie all the learns together" action, mirroring
    ai_core.force_full_deep_learn() for PSLD: retrains Level 1's cluster
    model over the current KB (KB-only, no DB scan), then Level 2's
    supervised ensemble on the full confirmed-correction feedback
    history. Wired to the (lighter/faster) "Force full deep-learn"
    button in the ILT section of the admin AI Control Center.

    For the heavier "read the ENTIRE production DB + auto-draft fixes"
    variant, see mega_deep_learn() below — kept as a SEPARATE, explicitly
    slower/heavier action with its own dedicated button, since scanning
    every distinct real error in the QA database and re-scoring each one
    against the KB is a fundamentally bigger job than this KB-only retrain.

    `on_step`, if given, is called once per real training step completed
    (see train_cluster_model()/train_neural_matcher() docstrings) — lets
    the UI show live, step-by-step progress (with elapsed seconds) instead
    of a plain spinner, and doubles as inspectable proof that a real
    computation actually ran and wasn't a mock/no-op.
    """
    cluster_result = train_cluster_model(on_step=on_step)
    neural_result = train_neural_matcher(on_step=on_step)
    return {
        "cluster": cluster_result,
        "neural": neural_result,
    }


def mega_deep_learn(conn, db_limit: int = DEFAULT_MEGA_DB_LIMIT, created_by: str = "SYSTEM", on_step: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    """
    The "real, functional (not illustrative)" mega deep-learn wired to
    its own dedicated button in the admin AI Control Center: this
    genuinely reads the entire production QA database's distinct error
    history (bounded by DEFAULT_MEGA_DB_LIMIT as a safety cap, not a
    small illustrative sample), throws out anything flagged as a
    success/completion message, normalizes every remaining error so
    "the same problem, different shipment/date/location" collapses
    together, retrains Level 1's clustering over the combined KB+DB
    corpus, retrains Level 2's supervised ensemble on every confirmed
    correction logged so far, and finally uses everything just learned
    to draft autonomous fix proposals for recurring real errors the AI
    is confident enough about (see troubleshooter/autonomous_fix.py) —
    each one lands as a pending item in the "Autonomous Fix" tab,
    awaiting a Support/Admin user's one-click approval before it's ever
    treated as a confirmed resolution. Nothing here writes to the
    Knowledge Base automatically; approval is always a separate,
    explicit human step.

    Runs entirely on this machine's CPU — scikit-learn's KMeans/
    RandomForest are already parallelized across every CPU core
    (n_jobs=-1), which is the real, usable form of "consuming the
    machine's compute" available today (no GPU-accelerated ML library
    is installed in this environment; adding one, e.g. PyTorch or RAPIDS
    cuML, would be a multi-GB dependency change that should be a
    separate, explicitly-requested decision, not a silent side effect of
    this button).
    """
    from troubleshooter import autonomous_fix

    run_started = time.perf_counter()
    cluster_result = train_cluster_model(conn=conn, db_limit=db_limit, on_step=on_step)
    neural_result = train_neural_matcher(on_step=on_step)

    t0 = time.perf_counter()
    if on_step:
        on_step({"step": "autonomous_fix_scan", "detail": "Scanning grouped DB errors and drafting autonomous fix proposals…", "seconds": None})
    fix_result = autonomous_fix.generate_autonomous_fixes(created_by=created_by)
    fix_seconds = round(time.perf_counter() - t0, 3)
    total_seconds = round(time.perf_counter() - run_started, 3)
    if on_step:
        on_step({
            "step": "autonomous_fix_scan",
            "detail": f"drafted={fix_result.get('drafted', 0)}, skipped_low_confidence={fix_result.get('skipped_low_confidence', 0)}",
            "seconds": fix_seconds,
        })

    return {
        "cluster": cluster_result,
        "neural": neural_result,
        "autonomous_fixes": fix_result,
        "total_duration_seconds": total_seconds,
        "autonomous_fixes_duration_seconds": fix_seconds,
    }
