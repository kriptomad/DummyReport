import re
from difflib import SequenceMatcher
from typing import List, Dict, Any
import numpy as np

import pandas as pd
from troubleshooter.loader import (
    load_troubleshoot_db,
    classify_by_keyword,
    get_next_step_hint,
    get_steps_for_category,
    get_error_types_df,
    is_success_message,
    COL_HOW_TO_CHECK,
    COL_ACTION,
)
from troubleshooter import local_intelligence
from database.support_queries import get_auto_queries_for_category, QUERY_CATALOGUE

# Lazy import para TF-IDF (opcional)
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    TFIDF_AVAILABLE = True
except ImportError:
    TFIDF_AVAILABLE = False

try:
    from rapidfuzz import fuzz as _rapidfuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False

# ── TF-IDF index cache ──────────────────────────────────────────
# PERFORMANCE: previously, `_calculate_tfidf_scores()` re-instantiated and
# re-fit a brand new TfidfVectorizer against the *entire* KB for every
# single unique error message being analyzed. For a batch of 100+
# shipments (each with several distinct error messages), this meant
# hundreds of full vectorizer fits per run — noticeably slow.
#
# Fix: fit the vectorizer + transform the KB patterns ONCE per KB
# version (cached below, keyed by a cheap fingerprint of the pattern
# list), then each error message only needs a fast `.transform()` (not
# a refit) plus a cosine similarity against the already-computed matrix.
_tfidf_index_cache: dict = {"fingerprint": None, "vectorizer": None, "matrix": None}
_SUSPICIOUS_KB_REGEX = re.compile(
    r"\((?:[^()\\]|\\.){0,200}[+*](?:[^()\\]|\\.){0,200}\)\s*(?:[+*]|\{\d+(?:,\d*)?\})"
)
_MAX_KB_REGEX_LENGTH = 500


def _kb_fingerprint(df_ts: pd.DataFrame) -> tuple:
    """Cheap, order-sensitive fingerprint of the KB patterns to detect changes
    (KB reload after an edit/merge) without re-hashing full row contents."""
    patterns = tuple(df_ts["_pattern_normalized"].fillna("").tolist())
    return (len(patterns), hash(patterns))


def _get_tfidf_index(df_ts: pd.DataFrame):
    """Returns (vectorizer, kb_matrix), fitting only once per KB version."""
    global _tfidf_index_cache
    fingerprint = _kb_fingerprint(df_ts)
    if _tfidf_index_cache["fingerprint"] == fingerprint:
        return _tfidf_index_cache["vectorizer"], _tfidf_index_cache["matrix"]

    patterns = df_ts["_pattern_normalized"].fillna("").tolist()
    vectorizer = TfidfVectorizer(
        max_features=100,
        ngram_range=(1, 3),
        min_df=1,
        stop_words='english'
    )
    matrix = vectorizer.fit_transform(patterns)

    _tfidf_index_cache = {
        "fingerprint": fingerprint,
        "vectorizer": vectorizer,
        "matrix": matrix,
    }
    return vectorizer, matrix


def _is_safe_kb_regex(pattern: str) -> bool:
    if len(pattern) > _MAX_KB_REGEX_LENGTH:
        return False
    # Stdlib `re` has no portable timeout on Windows threads, so reject a small
    # set of high-risk nested-quantifier shapes from KB-authored patterns.
    return _SUSPICIOUS_KB_REGEX.search(pattern) is None


def _tokenize(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) >= 3}


def _match_score(err_normalized: str, pattern: str, tfidf_score: float = 0.0) -> float:
    """
    Return a score in [0, 1] indicating how similar pattern is to err message.

    Multi-stage matching:
    1. Exact substring match (1.0)
    2. Regex match (0.95)
    3. Best of: TF-IDF cosine similarity, token overlap + sequence/fuzzy
       similarity
    """
    if not pattern:
        return 0.0

    # Stage 1: Literal substring is a strong signal
    if pattern in err_normalized:
        return 1.0

    # Stage 2: Regex (best-effort guarded; stdlib `re` has no timeout here)
    if _is_safe_kb_regex(pattern):
        try:
            regex = re.compile(pattern)
        except re.error:
            regex = None
        if regex and regex.search(err_normalized):
            return 0.95

    # Stage 3: Sequence similarity + token overlap + fuzzy (typo-tolerant)
    # ratio for fuzzy matching. rapidfuzz's partial_ratio is C-accelerated
    # and much more forgiving of small real-world variations (typos,
    # extra IDs/punctuation, reordering) than pure difflib, which is why
    # it gets the heaviest weight when available.
    #
    # IMPORTANT: this is always computed and combined with tfidf_score via
    # max() rather than short-circuited whenever tfidf_score > 0. A weak
    # (but nonzero) TF-IDF score — e.g. from a vectorizer whose IDF
    # weights got diluted by a large real-world vocabulary — used to
    # silently suppress a much stronger fuzzy/token match, making
    # obviously-similar errors report no match at all.
    seq_ratio = SequenceMatcher(None, err_normalized, pattern).ratio()

    err_tokens  = _tokenize(err_normalized)
    patt_tokens = _tokenize(pattern)
    if not err_tokens or not patt_tokens:
        token_overlap = 0.0
    else:
        inter = len(err_tokens.intersection(patt_tokens))
        token_overlap = inter / max(1, len(patt_tokens))

    if RAPIDFUZZ_AVAILABLE:
        fuzzy_ratio = _rapidfuzz.partial_ratio(err_normalized, pattern) / 100.0
        fallback_score = (0.35 * token_overlap) + (0.2 * seq_ratio) + (0.45 * fuzzy_ratio)
    else:
        # Weighted blend: tokens capture business keywords; sequence captures shape
        fallback_score = (0.6 * token_overlap) + (0.4 * seq_ratio)

    return max(tfidf_score, fallback_score)


def _get_error_type_meta(category: str) -> dict:
    """Return metadata row from 'Type of errors' sheet for a given category."""
    try:
        df = get_error_types_df()
        if df is None or df.empty or "ERROR_CATEGORY" not in df.columns:
            return {}
        match = df[df["ERROR_CATEGORY"].str.lower().str.strip() == category.lower().strip()]
        if match.empty:
            # try partial match
            match = df[df["ERROR_CATEGORY"].str.lower().str.contains(
                category.lower().split("/")[0].strip(), na=False
            )]
        if not match.empty:
            return match.iloc[0].to_dict()
    except Exception:
        pass
    return {}


def _calculate_tfidf_scores(err_msg: str, df_ts: pd.DataFrame) -> tuple[Dict[int, float], bool]:
    """
    Calcula TF-IDF similarity scores entre err_msg e todos os padrões da KB.

    Prefers the persisted "local AI" index (troubleshooter/local_intelligence.py)
    when one has been trained (via the admin "Processar/Alimentar IA
    Interna" button) — its vectorizer vocabulary/IDF weights are adapted
    from real production error history, not just the KB text, so it
    generalizes better to real-world phrasing. Falls back to the
    ephemeral per-process KB-only index (fit once per KB version, cached
    below) when no local AI model has been trained yet, OR when the
    persisted index is stale (KB size changed since training —
    score_against_kb() detects this and returns {} rather than risk
    misaligned scores).

    Args:
        err_msg: Mensagem de erro a comparar
        df_ts: DataFrame da troubleshoot KB

    Returns:
        Tuple of (scores dict mapeando index → score de similaridade,
        whether the persisted local AI index was actually used for this
        call — used to label matches "IA Interna" vs "TF-IDF" honestly,
        instead of trusting the global "trained" flag which can be true
        even while every individual lookup is silently falling back).
    """
    local_scores = local_intelligence.score_against_kb(err_msg, df_ts)
    if local_scores:
        return local_scores, True

    if not TFIDF_AVAILABLE:
        return {}, False

    try:
        vectorizer, kb_matrix = _get_tfidf_index(df_ts)
        err_vector = vectorizer.transform([err_msg])
        cosine_scores = cosine_similarity(err_vector, kb_matrix).flatten()
        return {idx: float(score) for idx, score in enumerate(cosine_scores)}, False

    except Exception:
        # Se TF-IDF falhar, retorna vazio (fallback para métodos antigos)
        return {}, False


def match_errors(err_msg_series: pd.Series) -> list[dict]:
    """
    For each unique ERR_MSG, finds matching rows in the troubleshoot DB,
    classifies the error, fetches step-by-step actions and type metadata.

    Uses multi-stage matching with TF-IDF for improved accuracy.

    Returns list of dicts:
    {
        "err_msg":       str,
        "category":      str  — from Logic sheet keyword match,
        "matches":       list[dict]  — top-5 scored rows from Main sheet,
        "steps":         list[str]   — from Steps sheet,
        "error_type":    dict        — row from Type of errors sheet,
        "needs_tariff":  bool,
    }
    """
    df_ts = load_troubleshoot_db()
    unique_errors = err_msg_series.dropna().unique()
    results = []

    for err_msg in unique_errors:
        err_str        = str(err_msg).strip()
        err_normalized = err_str.lower()

        # A "successfully"/"sucesso" status note isn't an error — skip it
        # entirely so it's never matched against the KB, never listed as
        # unmatched, and never counted anywhere as an "error".
        if is_success_message(err_str):
            continue

        # 1 — Classify using Logic keywords
        category = classify_by_keyword(err_str)

        # 2 — Calculate similarity scores using the local AI index (if
        # trained) or the ephemeral KB-only TF-IDF fallback
        tfidf_scores, local_ai_active = _calculate_tfidf_scores(err_normalized, df_ts)

        # 3 — Score every row in Main sheet
        scored_matches = []
        for idx, row in df_ts.iterrows():
            pattern = str(row.get("_pattern_normalized", "")).strip()
            tfidf_score = tfidf_scores.get(idx, 0.0)
            score = _match_score(err_normalized, pattern, tfidf_score)

            if score >= 0.45:
                row_copy = row.copy()
                row_copy["_match_score"] = round(score, 3)
                if tfidf_score >= score - 1e-9:
                    # The TF-IDF signal was the (or tied for) winning
                    # component of this score — label accordingly.
                    row_copy["_match_method"] = "Internal AI" if local_ai_active else "TF-IDF"
                else:
                    row_copy["_match_method"] = "Token+Fuzzy" if RAPIDFUZZ_AVAILABLE else "Token+Seq"
                scored_matches.append(row_copy)

        scored_matches.sort(key=lambda r: float(r.get("_match_score", 0.0)), reverse=True)
        matched_rows = scored_matches[:5]

        # If Logic didn't classify, try Main sheet category
        if not category and matched_rows:
            category = str(matched_rows[0].get("Categoria", "")).strip()

        # 4 — Steps for this category (specific "next step" hint + detailed
        # group steps when available, falling back to the matched row's own
        # validate/action text so every category gets tailored guidance)
        next_step_hint = get_next_step_hint(err_str)
        top_row = matched_rows[0] if matched_rows else {}
        steps = get_steps_for_category(
            category,
            next_step_hint=next_step_hint,
            fallback_how_to_check=str(top_row.get(COL_HOW_TO_CHECK, "")).strip(),
            fallback_action=str(top_row.get(COL_ACTION, "")).strip(),
        )

        # 5 — Error type metadata
        error_type = _get_error_type_meta(category)

        # 6 — Which support queries should auto-run for this category
        auto_queries = get_auto_queries_for_category(category)

        needs_tariff = any(bool(r.get("_needs_tariff", False)) for r in matched_rows)

        results.append({
            "err_msg":      err_msg,
            "category":     category,
            "matches":      matched_rows,
            "steps":        steps,
            "error_type":   error_type,
            "auto_queries": auto_queries,
            "needs_tariff": needs_tariff,
            "tfidf_used":   bool(tfidf_scores),  # Indica se TF-IDF foi usado
        })

    return results
