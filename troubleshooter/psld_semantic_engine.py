"""
troubleshooter/psld_semantic_engine.py
========================================
EXPERIMENTAL (Lab Test tab, "PSLD - Parts" sub-menu) — a local, self-
learning semantic layer for matching a new ServiceNow ticket against the
PSLD - Parts resolution KB (troubleshooter/servicenow_resolution_kb.py)
and past Resolved/Closed/Cancelled tickets.

Two similarity signals are blended:
  1. TF-IDF + cosine similarity (already implemented in
     servicenow_resolution_kb.py) — fast, zero extra dependency, good at
     catching literal keyword overlap.
  2. Local LSA (Latent Semantic Analysis: TF-IDF -> TruncatedSVD) —
     a lightweight, 100% local "understands relationships between words"
     model, built on scikit-learn (already a project dependency, nothing
     new to install). It learns latent topics from the KB/ticket corpus
     itself and catches PARAPHRASED/similar-meaning text that shares few
     literal words with the query — the same class of problem a neural
     embedding model solves, without needing one.

     NOTE: a real sentence-embedding model (sentence-transformers +
     PyTorch, "all-MiniLM-L6-v2") was tried first, since the team said a
     locally-run model is welcome even if heavier. It could NOT be
     installed in this project: pip failed extracting PyTorch's package
     ([WinError 206] filename/path too long) because this project lives
     under a deeply nested OneDrive path and exceeds Windows' 260-char
     MAX_PATH, and enabling Windows' long-path support requires an admin
     registry change this machine doesn't have. LSA is the practical
     local alternative that needs no such install. If IT ever enables
     long-path support (or the project moves to a shorter path), swapping
     `_embed()` below to load a real sentence-transformers model is a
     contained, drop-in change — everything else here (feedback loop,
     blending, UI) stays the same.

Self-learning loop: whenever an analyst confirms "yes, this suggested
match/resolution was actually the right one for this ticket", we record
(ticket_text, matched entry id) in a small local feedback log
(data/psld_feedback.json, gitignored). Future queries get a similarity
BOOST toward KB entries that have previously been confirmed correct for
similar-looking tickets (measured via the same LSA vectors) — over time,
as the team confirms more matches, the ranking keeps getting better
tuned to this team's actual ticket patterns, without retraining any
model weights (a lightweight, explainable form of "self-learning" that
doesn't require a GPU or a labeled training run). The more entries/
tickets accumulate, the better LSA's latent topics get too — this
whole layer keeps improving with usage, unsupervised.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FEEDBACK_PATH = DATA_DIR / "psld_feedback.json"

_LOCK = threading.Lock()

_MODEL_NAME = "local-lsa (TF-IDF + TruncatedSVD, scikit-learn)"

try:
    import numpy as np
    from sklearn.decomposition import TruncatedSVD
    from sklearn.feature_extraction.text import TfidfVectorizer
    LSA_AVAILABLE = True
except ImportError:  # pragma: no cover - scikit-learn is already a project dependency
    LSA_AVAILABLE = False
    np = None  # type: ignore


def semantic_status() -> Dict[str, Any]:
    """Diagnostic info for the UI: is the local semantic layer actually
    usable right now, and if not, why."""
    if not LSA_AVAILABLE:
        return {"available": False, "reason": "scikit-learn is not installed.", "model": _MODEL_NAME}
    return {"available": True, "reason": None, "model": _MODEL_NAME}


def _raw_tfidf_similarities(query_text: str, corpus_texts: List[str]) -> Optional[List[float]]:
    """Fallback for when the corpus is too small for a meaningful SVD
    (e.g. only 1-2 past confirmed feedback tickets so far) — plain
    TF-IDF cosine similarity, still 100% local, better than reporting no
    signal at all while the self-learning log is still tiny."""
    try:
        from sklearn.metrics.pairwise import cosine_similarity
        vectorizer = TfidfVectorizer(max_features=2000, ngram_range=(1, 2), stop_words="english")
        matrix = vectorizer.fit_transform(corpus_texts + [query_text])
        sims = cosine_similarity(matrix[-1], matrix[:-1]).flatten()
        return [float(s) for s in sims]
    except ValueError:
        return None


def semantic_similarities(query_text: str, corpus_texts: List[str]) -> Optional[List[float]]:
    """Cosine similarity of `query_text` against each of `corpus_texts`
    in LSA (latent topic) space instead of raw TF-IDF space — catches
    paraphrased/similar-meaning text that shares few literal words.
    Returns None if the local semantic layer isn't available at all;
    falls back to plain TF-IDF cosine similarity (not None) when the
    corpus is merely too small for SVD (e.g. early self-learning
    feedback with just 1-2 samples), so a small feedback log still gives
    a usable, if less nuanced, signal."""
    if not LSA_AVAILABLE or not corpus_texts or not (query_text or "").strip():
        return None

    texts = corpus_texts + [query_text]
    n_samples, features_cap = len(texts), 2000
    # TruncatedSVD needs n_components < min(n_samples, n_features); with
    # very few KB entries/tickets there isn't enough data for a
    # meaningful latent-topic space, so we bail out to plain TF-IDF.
    n_components = min(100, n_samples - 1, features_cap - 1)
    if n_components < 2:
        return _raw_tfidf_similarities(query_text, corpus_texts)

    try:
        vectorizer = TfidfVectorizer(max_features=features_cap, ngram_range=(1, 2), stop_words="english")
        tfidf_matrix = vectorizer.fit_transform(texts)
        svd = TruncatedSVD(n_components=n_components, random_state=42)
        lsa_matrix = svd.fit_transform(tfidf_matrix)
    except ValueError:
        return _raw_tfidf_similarities(query_text, corpus_texts)

    norms = np.linalg.norm(lsa_matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normalized = lsa_matrix / norms
    query_vec = normalized[-1]
    sims = [float(np.dot(query_vec, v)) for v in normalized[:-1]]
    # Cosine similarity in LSA space can be (mildly) negative for
    # unrelated topics — clip to [0, 1] so it blends sanely with TF-IDF.
    return [max(0.0, s) for s in sims]


# ---------------------------------------------------------------------
# Self-learning feedback log
# ---------------------------------------------------------------------

def _load_feedback() -> List[Dict[str, Any]]:
    if not FEEDBACK_PATH.exists():
        return []
    try:
        return json.loads(FEEDBACK_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_feedback(rows: List[Dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FEEDBACK_PATH.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")


def record_feedback(ticket_text: str, entry_id: str, entry_title: str, confirmed_by: str) -> None:
    """Records that `entry_id` was confirmed by a human as the correct
    resolution match for a ticket whose text was `ticket_text`. Used to
    boost future similar-looking tickets toward this entry."""
    row = {
        "ticket_text": (ticket_text or "").strip(),
        "entry_id": entry_id,
        "entry_title": entry_title,
        "confirmed_by": confirmed_by,
        "confirmed_at": datetime.now().isoformat(timespec="seconds"),
    }
    with _LOCK:
        rows = _load_feedback()
        rows.append(row)
        _save_feedback(rows)


def feedback_count() -> int:
    return len(_load_feedback())


def all_feedback() -> List[Dict[str, Any]]:
    """Every recorded confirmed match, unsorted — used by
    troubleshooter/ai_core.py to train the local neural classifier over
    the full self-learning history (list_feedback() below is meant for
    UI display and truncates to a recent window)."""
    return _load_feedback()


def list_feedback(limit: int = 50) -> List[Dict[str, Any]]:
    """Most recent confirmed ticket->KB-entry matches, most recent first —
    the actual "cruzamentos" (cross-references) the self-learning loop has
    recorded so far. Used by the admin AI Control Center to show what the
    system has been learning in the background."""
    rows = _load_feedback()
    return sorted(rows, key=lambda r: r.get("confirmed_at", ""), reverse=True)[:limit]


def feedback_boosts(query_text: str, entry_ids: List[str]) -> Dict[str, float]:
    """
    For each entry_id in `entry_ids`, returns a boost in [0, 1] based on
    how similar `query_text` is to past confirmed tickets that were
    resolved by that same entry — the actual "self-learning" part: the
    more the team confirms matches, the more future similar tickets get
    nudged toward the entries that history shows actually work for that
    kind of problem. Returns an empty dict if there's no feedback yet or
    no embedding model available (feedback boosting relies on semantic
    similarity, since past ticket wording rarely repeats verbatim).
    """
    rows = _load_feedback()
    if not rows or not (query_text or "").strip() or not LSA_AVAILABLE:
        return {}

    boosts: Dict[str, float] = {}
    for entry_id in entry_ids:
        past_texts = [r["ticket_text"] for r in rows if r.get("entry_id") == entry_id and r.get("ticket_text")]
        if not past_texts:
            continue
        sims = semantic_similarities(query_text, past_texts)
        if sims:
            boosts[entry_id] = max(sims)
    return boosts


def blended_kb_matches(
    query_text: str,
    tfidf_matches: List[Tuple[Dict[str, Any], float]],
    semantic_weight: float = 0.5,
    feedback_weight: float = 0.2,
    neural_weight: float = 0.15,
) -> List[Tuple[Dict[str, Any], float, Dict[str, float]]]:
    """
    Takes the TF-IDF-ranked KB matches (from
    servicenow_resolution_kb.find_similar()) and re-scores them by
    blending in the local semantic-embedding similarity, the self-learning
    feedback boost, AND (once enough confirmed feedback has accumulated)
    a real local neural network's confidence score — see
    troubleshooter/ai_core.py — then re-sorts. Returns
    (entry, blended_score, {"tfidf":.., "semantic":.., "feedback":..,
    "neural":..}) tuples so the UI can show the full breakdown
    transparently.
    """
    if not tfidf_matches:
        return []

    entries = [e for e, _ in tfidf_matches]
    tfidf_scores = {e["id"]: s for e, s in tfidf_matches}

    def corpus_text(e: Dict[str, Any]) -> str:
        return " ".join([str(e.get("title", "")), str(e.get("description_long", "")), str(e.get("steps", ""))])

    semantic_scores: Dict[str, float] = {}
    sims = semantic_similarities(query_text, [corpus_text(e) for e in entries])
    if sims is not None:
        for e, s in zip(entries, sims):
            semantic_scores[e["id"]] = s

    boosts = feedback_boosts(query_text, [e["id"] for e in entries])

    # Local import to avoid a circular import at module load time:
    # troubleshooter.ai_core itself calls into this module (all_feedback)
    # to train the neural classifier.
    neural_scores: Dict[str, float] = {}
    try:
        from troubleshooter import ai_core
        neural_scores = ai_core.neural_predict_scores(query_text, [e["id"] for e in entries])
    except Exception:
        neural_scores = {}

    results = []
    for e in entries:
        eid = e["id"]
        tfidf_s = tfidf_scores.get(eid, 0.0)
        semantic_s = semantic_scores.get(eid, 0.0)
        feedback_s = boosts.get(eid, 0.0)
        neural_s = neural_scores.get(eid, 0.0)
        if semantic_scores:
            base = (1 - semantic_weight) * tfidf_s + semantic_weight * semantic_s
        else:
            base = tfidf_s
        blended = min(1.0, base + feedback_weight * feedback_s + neural_weight * neural_s)
        results.append((e, blended, {"tfidf": tfidf_s, "semantic": semantic_s, "feedback": feedback_s, "neural": neural_s}))

    results.sort(key=lambda x: x[1], reverse=True)
    return results
