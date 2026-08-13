"""
troubleshooter/local_intelligence.py
=====================================
"Internal AI" that runs 100% locally on this machine's CPU (Ryzen AI /
future Xeon on Azure) — no external API calls, no internet download of
model weights. A full generative LLM (llama.cpp/Ollama-style, multi-GB
weights) isn't practical to ship inside this app today: this environment
has no local model runtime installed and pulling multi-GB weights over
the corporate network on every deploy is unreliable. Instead, this module
gives the Troubleshooter a real, self-improving local ML pipeline built
entirely from already-vendored dependencies (scikit-learn + rapidfuzz):

  1. A TF-IDF + k-NN semantic index over the *whole* KB text (pattern +
     meaning + how-to-check + action, not just the raw pattern) — this
     alone catches matches that share meaning/vocabulary even when the
     literal error string differs.
  2. Vocabulary/IDF weights are adapted using real production error text
     mined from the QA database (read-only SELECT against
     ACME_OMS.DEMO_AUDIT.ERR_MSG), so real-world phrasing that
     never appears in the KB itself still gets recognized.
  3. A persisted "gap report": the most frequent real errors that still
     don't match anything well — a prioritized worklist for a human to
     turn into new KB entries (via the Knowledge Base tab).

State is persisted to disk (joblib) under assets/local_ai_state.joblib so
training survives app restarts. It's rebuilt on demand via `retrain()`,
wired to the "🧠 Processar / Alimentar IA Interna" button in
Administration → Settings (root admin only).

This module never mutates the KB itself — matching/ranking only.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, Optional

import joblib
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

from troubleshooter.loader import (
    COL_ACTION,
    COL_ERROR_PATTERN,
    COL_HOW_TO_CHECK,
    COL_MEANING,
    is_success_message,
)

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.neighbors import NearestNeighbors
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

try:
    from rapidfuzz import fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False

ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
MODEL_PATH = os.path.join(ASSETS_DIR, "local_ai_state.joblib")

# How many distinct/most-frequent real ERR_MSG values to pull from the DB
# per feed cycle — bounded so a single click can't run an unbounded scan
# against production.
DEFAULT_DB_LIMIT = 3000

# A real match is one that beats this score; anything below is treated as
# a "gap" candidate when it comes from a frequent real-world error.
GAP_SCORE_THRESHOLD = 0.45

_state: Optional[Dict[str, Any]] = None  # in-memory cache of the loaded state


def _kb_corpus_text(row: pd.Series) -> str:
    parts = [
        str(row.get(COL_ERROR_PATTERN, "")),
        str(row.get(COL_MEANING, "")),
        str(row.get(COL_HOW_TO_CHECK, "")),
        str(row.get(COL_ACTION, "")),
    ]
    return " ".join(p for p in parts if p and p.lower() != "nan")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _load_state() -> Dict[str, Any]:
    global _state
    if _state is not None:
        return _state
    if os.path.exists(MODEL_PATH):
        try:
            _state = joblib.load(MODEL_PATH)
            return _state
        except Exception:
            pass
    _state = {"trained": False}
    return _state


def _save_state(state: Dict[str, Any]) -> None:
    global _state
    os.makedirs(ASSETS_DIR, exist_ok=True)
    joblib.dump(state, MODEL_PATH)
    _state = state


def get_status(current_kb_size: Optional[int] = None) -> Dict[str, Any]:
    """Read-only snapshot of the local AI's current state, for display in
    the admin panel. Never trains anything.

    If `current_kb_size` is given (the live KB row count), also reports
    whether the persisted index is "stale" — i.e. the KB has changed
    (rows added/removed, e.g. via KB edits or an approved Pendência) since
    the last training, which silently disables the trained index for
    real-time matching (troubleshooter.engine falls back to the ephemeral
    per-process index instead) until the admin retrains. Previously this
    went completely unnoticed by the admin panel.
    """
    state = _load_state()
    if not state.get("trained"):
        return {
            "trained": False,
            "sklearn_available": SKLEARN_AVAILABLE,
            "rapidfuzz_available": RAPIDFUZZ_AVAILABLE,
        }
    stale = (
        current_kb_size is not None
        and state.get("kb_fingerprint") != current_kb_size
    )
    return {
        "trained": True,
        "sklearn_available": SKLEARN_AVAILABLE,
        "rapidfuzz_available": RAPIDFUZZ_AVAILABLE,
        "last_trained_at": state.get("last_trained_at"),
        "kb_docs": state.get("kb_docs", 0),
        "db_docs_scanned": state.get("db_docs_scanned", 0),
        "db_match_rate": state.get("db_match_rate"),
        "db_error": state.get("db_error"),
        "db_feed_requested": state.get("db_feed_requested", False),
        "gap_candidates": state.get("gap_candidates", []),
        "stale": stale,
    }


def _fetch_db_error_frequencies(conn, limit: int = DEFAULT_DB_LIMIT) -> pd.DataFrame:
    """
    Read-only aggregate over the QA database's real error history —
    grouped/counted server-side (no row-level PII pulled), bounded by
    `limit` distinct patterns. Returns columns [ERR_MSG, N].
    """
    query = (
        "SELECT ERR_MSG, COUNT(*) AS N "
        "FROM ACME_OMS.DEMO_AUDIT "
        "WHERE ERR_MSG IS NOT NULL "
        "GROUP BY ERR_MSG "
        "ORDER BY COUNT(*) DESC "
        "FETCH FIRST :row_limit ROWS ONLY"
    )
    cursor = conn.cursor()
    try:
        cursor.execute(query, {"row_limit": int(limit)})
        columns = [c[0] for c in cursor.description]
        rows = cursor.fetchall()
    finally:
        cursor.close()
    return pd.DataFrame(rows, columns=columns)


def retrain(df_ts: pd.DataFrame, conn=None, db_limit: int = DEFAULT_DB_LIMIT) -> Dict[str, Any]:
    """
    Rebuilds the local AI index from the current KB, optionally enriched
    with real error frequency/vocabulary mined from the QA database (only
    if `conn` is given — this is what the admin "process & feed" button
    passes in). Persists the result and returns the same stats dict as
    get_status() so the caller can show immediate feedback.
    """
    if not SKLEARN_AVAILABLE:
        return {"trained": False, "error": "scikit-learn not available"}

    kb_texts = [_kb_corpus_text(row) for _, row in df_ts.iterrows()]
    kb_texts = [t if t.strip() else "empty" for t in kb_texts]

    db_docs_scanned = 0
    db_match_rate = None
    gap_candidates: list[dict] = []

    if conn is not None:
        try:
            freq_df = _fetch_db_error_frequencies(conn, limit=db_limit)
            # Purge "successfully"/"completed"/etc. status rows HERE, right
            # after the raw fetch — not just later at gap-detection time.
            # Previously these rows were skipped only inside the gap loop
            # below, but were still counted into `db_docs_scanned`/the
            # match-rate denominator, silently deflating the reported
            # match rate and (more importantly) still being available to
            # anything else that might read `freq_df` in the future. Now
            # the local AI's "real error" view of the DB never contains
            # non-errors at all, from the very first step of the pipeline.
            if not freq_df.empty:
                success_mask = freq_df["ERR_MSG"].apply(is_success_message)
                freq_df = freq_df.loc[~success_mask].reset_index(drop=True)
            db_docs_scanned = int(freq_df["N"].sum()) if not freq_df.empty else 0
        except Exception as e:
            # Best-effort: DB might be unreachable/permission-limited from
            # this session — the KB-only index below still gets built, but
            # the caller/UI MUST be told the DB feed didn't actually happen
            # (previously this was silently swallowed into gap_candidates,
            # which the admin UI never rendered — the button reported
            # "trained successfully" even though no real DB data was used).
            freq_df = pd.DataFrame(columns=["ERR_MSG", "N"])
            db_error = f"{type(e).__name__}: {e}"
        else:
            db_error = None
    else:
        freq_df = pd.DataFrame(columns=["ERR_MSG", "N"])
        db_error = None

    # NOTE: the vectorizer's vocabulary/IDF is fit ONLY on the KB text —
    # NOT blended with real DB error text. An earlier version blended in
    # tens of thousands of production error messages here to "adapt
    # vocabulary to real-world phrasing", but that silently wrecked match
    # quality: IDF weights for words that appear in almost every
    # production error (e.g. "shipment", "load", "leg") collapse toward
    # zero once diluted by a huge background corpus, which tanks cosine
    # similarity even for near-identical strings (verified: a KB pattern
    # scored 0.06 against a lightly-reworded version of itself once
    # trained with 30k+ DB docs, vs. 1.0 with a KB-only vectorizer).
    # DB error text is still used below for gap-detection matching
    # (blended with rapidfuzz, which isn't sensitive to this), just not
    # for building the vocabulary/weights of the KB-matching vectorizer.
    vectorizer = TfidfVectorizer(
        max_features=2000,
        ngram_range=(1, 2),
        min_df=1,
        stop_words="english",
    )
    vectorizer.fit(kb_texts)
    kb_matrix = vectorizer.transform(kb_texts)

    n_neighbors = min(5, kb_matrix.shape[0]) or 1
    nn_index = NearestNeighbors(n_neighbors=n_neighbors, metric="cosine")
    nn_index.fit(kb_matrix)

    # Evaluate real DB errors against the freshly-built KB index to find
    # the highest-frequency "gaps" (real errors nothing matches well) —
    # a prioritized worklist for the Knowledge Base curator.
    if not freq_df.empty:
        patterns_normalized = df_ts["_pattern_normalized"].fillna("").tolist() if "_pattern_normalized" in df_ts.columns else kb_texts
        matched = 0
        scored_gaps = []
        for _, r in freq_df.iterrows():
            err_msg = str(r["ERR_MSG"])
            cnt = int(r["N"])

            # A "successfully"/"sucesso" status note isn't an error —
            # never treat it as a gap (it would never get a real KB fix,
            # so counting it would just be noise in the worklist).
            if is_success_message(err_msg):
                continue

            err_norm = _normalize(err_msg)
            score = 0.0
            if kb_matrix.shape[0] > 0:
                vec = vectorizer.transform([err_norm])
                dist, _idx = nn_index.kneighbors(vec, n_neighbors=1)
                score = float(max(0.0, 1.0 - dist[0][0]))
            if RAPIDFUZZ_AVAILABLE and patterns_normalized:
                best_fuzzy = max(
                    (fuzz.partial_ratio(err_norm, p) / 100.0 for p in patterns_normalized if p),
                    default=0.0,
                )
                score = max(score, best_fuzzy)
            if score >= GAP_SCORE_THRESHOLD:
                matched += cnt
            else:
                scored_gaps.append({"err_msg": err_msg, "count": cnt, "best_score": round(score, 3)})

        total = int(freq_df["N"].sum())
        db_match_rate = round(matched / total, 3) if total else None
        scored_gaps.sort(key=lambda g: g["count"], reverse=True)
        gap_candidates = scored_gaps[:20]

        # Feed every gap found (not just the top 20 shown here) into the
        # "Pendências" worklist so analysts can review/fix them there —
        # this is the "cataloga ERR_MSG que não existe" part of the
        # internal-AI feature: the DB scan itself discovers gaps, not
        # just ad-hoc Troubleshooter runs.
        try:
            from troubleshooter import pending_errors
            all_gap_msgs = [g["err_msg"] for g in scored_gaps]
            gap_counts = {g["err_msg"]: g["count"] for g in scored_gaps}
            pending_errors.register_unmatched_batch(all_gap_msgs, counts=gap_counts)
        except Exception:
            pass

    state = {
        "trained": True,
        "last_trained_at": datetime.now().isoformat(timespec="seconds"),
        "kb_docs": len(kb_texts),
        "db_docs_scanned": db_docs_scanned,
        "db_match_rate": db_match_rate,
        "db_error": db_error,
        "db_feed_requested": conn is not None,
        "gap_candidates": gap_candidates,
        "vectorizer": vectorizer,
        "kb_matrix": kb_matrix,
        "nn_index": nn_index,
        "kb_fingerprint": len(kb_texts),
    }
    _save_state(state)
    return get_status()


def score_against_kb(err_normalized: str, df_ts: pd.DataFrame) -> Dict[int, float]:
    """
    Returns {row_index: score in [0,1]} for every KB row, using the
    persisted local AI index if one has been trained, else {} (caller
    should fall back to its own ephemeral TF-IDF/token matching).

    Once computed, the base cosine-similarity scores are further
    blended with troubleshooter.ilt_ai_core's own 3-level neural
    pipeline (Level 1 whole-KB clustering + Level 2 supervised
    MLP/RandomForest ensemble — see that module's docstring) — the ILT
    Troubleshooter's equivalent of how psld_semantic_engine.
    blended_kb_matches() blends in troubleshooter.ai_core's neural
    score for PSLD - Parts. Same blend weight (0.15) as PSLD, for parity
    between the two independently-trained AIs.
    """
    state = _load_state()
    if not state.get("trained") or not SKLEARN_AVAILABLE:
        return {}
    # If the KB has grown/shrunk since the last training, the persisted
    # matrix's row indices no longer line up with df_ts — bail out to the
    # caller's fallback rather than risk mis-aligned scores.
    if state.get("kb_fingerprint") != len(df_ts):
        return {}
    try:
        vectorizer = state["vectorizer"]
        kb_matrix = state["kb_matrix"]
        vec = vectorizer.transform([err_normalized])
        from sklearn.metrics.pairwise import cosine_similarity
        sims = cosine_similarity(vec, kb_matrix).flatten()
        base_scores = {i: float(s) for i, s in enumerate(sims)}
    except Exception:
        return {}

    try:
        from troubleshooter import ilt_ai_core
        row_patterns = {i: str(p).strip() for i, p in enumerate(df_ts.get(COL_ERROR_PATTERN, []))}
        distinct_patterns = list({p for p in row_patterns.values() if p})
        neural_by_pattern = ilt_ai_core.neural_predict_scores(err_normalized, distinct_patterns)
        if neural_by_pattern:
            neural_weight = 0.15
            for i, score in base_scores.items():
                p = row_patterns.get(i, "")
                neural_s = neural_by_pattern.get(p)
                if neural_s is not None:
                    base_scores[i] = min(1.0, score + neural_weight * neural_s)
    except Exception:
        logger.exception("local_intelligence.score_against_kb: ilt_ai_core blend failed")

    return base_scores
