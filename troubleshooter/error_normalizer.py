"""
troubleshooter/error_normalizer.py
====================================
Heuristic error-text normalization shared by ilt_ai_core.py and
autonomous_fix.py, so the "mega deep-learn" can recognize that two real
production errors are the SAME underlying problem even when the literal
text differs only by a shipment ID, tracking number, date/time, or a
location/destination code — exactly the "só muda o número do shipment,
location, destination etc" requirement.

Approach (deliberately simple/heuristic, not a full NER model — there's
no such model vendored in this project, see ilt_ai_core.py's module
docstring for why): any token that contains at least one digit gets
replaced with a single `<id>` placeholder. This catches:
  - Shipment/tracking numbers ("8823991", "SHP-004471")
  - Dates/times in almost any format ("03-08-2026", "09:37:12")
  - Alphanumeric location/terminal/leg codes ("BR01", "LAX22", "DEMODB01")
Pure-word tokens (e.g. a city name spelled out, "SAO PAULO") are left
alone — those are legitimate content, not the noisy identifiers this is
meant to strip. This is intentionally conservative: it would rather
under-merge (treat two things as different when they're actually the
same) than over-merge (treat two genuinely different errors as
identical), since over-merging would silently blend unrelated
resolutions together.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List, Optional

import pandas as pd

# Any token with at least one digit in it (```\w``` includes underscore,
# but not hyphens — a hyphenated ID like "SHP-004471" is handled by first
# splitting on non-alphanumeric boundaries via \b, so each hyphen-joined
# piece is evaluated independently; "SHP" survives, "004471" is masked).
_TOKEN_WITH_DIGIT_RE = re.compile(r"\b\w*\d\w*\b")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_error_text(text: Any) -> str:
    """
    Returns a normalized form of `text` with every digit-containing token
    replaced by a single `<id>` placeholder, lowercased, and whitespace
    collapsed. Two real errors that are "the same" apart from a shipment
    number/date/location code will normalize to the exact same string.
    """
    s = str(text or "").strip().lower()
    if not s:
        return ""
    s = _TOKEN_WITH_DIGIT_RE.sub("<id>", s)
    s = _WHITESPACE_RE.sub(" ", s).strip()
    return s


def aggregate_by_normalized_pattern(
    freq_df: pd.DataFrame,
    err_col: str = "ERR_MSG",
    count_col: str = "N",
    max_examples: int = 5,
) -> List[Dict[str, Any]]:
    """
    Collapses a (possibly huge) DataFrame of [err_msg, occurrence_count]
    rows -- e.g. straight from a full-DB scan, where the same underlying
    problem can appear as hundreds of literally-distinct ERR_MSG strings
    (one per shipment/date/location) -- into one record per normalized
    pattern:
        {
          "normalized_pattern": "...",
          "representative_err_msg": "<the most frequent raw variant>",
          "example_variants": ["...up to max_examples raw strings..."],
          "distinct_variants": <int>,
          "occurrences": <summed count across all variants>,
        }
    Sorted by occurrences, descending (most-impactful patterns first).
    """
    if freq_df is None or freq_df.empty:
        return []

    groups: Dict[str, Dict[str, Any]] = {}
    for _, row in freq_df.iterrows():
        raw = str(row.get(err_col, "") or "")
        if not raw.strip():
            continue
        cnt = int(row.get(count_col, 1) or 1)
        norm = normalize_error_text(raw)
        if not norm:
            continue
        g = groups.get(norm)
        if g is None:
            groups[norm] = {
                "normalized_pattern": norm,
                "_variant_counts": defaultdict(int),
            }
            g = groups[norm]
        g["_variant_counts"][raw] += cnt

    results = []
    for norm, g in groups.items():
        variant_counts: Dict[str, int] = g["_variant_counts"]
        total = sum(variant_counts.values())
        # Most-frequent literal variant stands in as "the" example error text.
        sorted_variants = sorted(variant_counts.items(), key=lambda kv: kv[1], reverse=True)
        representative = sorted_variants[0][0]
        examples = [v for v, _ in sorted_variants[:max_examples]]
        results.append({
            "normalized_pattern": norm,
            "representative_err_msg": representative,
            "example_variants": examples,
            "distinct_variants": len(variant_counts),
            "occurrences": total,
        })

    results.sort(key=lambda r: r["occurrences"], reverse=True)
    return results
