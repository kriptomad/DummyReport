"""
troubleshooter/autonomous_fix.py
==================================
The "autonomous fix" pipeline: after a mega deep-learn
(troubleshooter.ilt_ai_core.mega_deep_learn), this module looks at
every real, recurring error the DB scan found that DOESN'T already
exactly match a Knowledge Base pattern (see
ilt_ai_core.train_cluster_model()'s db_error_groups), and — using the
same 3-level AI (whole-KB clustering + supervised MLP/RandomForest
ensemble) that powers normal Troubleshooter matching — decides whether
it's confident enough to say "this is really just the existing KB fix
for pattern X, applied to a new shipment/date/location variant".

When it is confident enough (AND the pattern has genuinely recurred,
not a one-off), it drafts an "autonomous fix" proposal: not a new
Knowledge Base entry, and NOT a live decision on its own — just a
pending item in the "Autonomous Fix" tab, carrying the AI's reasoning
(which KB pattern it matched, the confidence score, how many real
occurrences it found, and up to 5 example real error variants) for a
Support or Admin user to approve or reject with one click. Nothing
here EVER writes to the Knowledge Base or counts as confirmed
self-learning feedback until a human explicitly approves it — same
human-in-the-loop guarantee as troubleshooter/pending_errors.py's
existing "Pendências" worklist, just for the "I recognize this, want me
to auto-apply the known fix?" case instead of the "I've never seen this
before" case.

Storage: data/autonomous_fixes.json (same JSON+filelock pattern as
pending_errors.py), keyed by the normalized error pattern so re-running
the mega deep-learn is idempotent — an already-pending/approved/
rejected pattern is never re-drafted.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from filelock import FileLock, Timeout

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
FIXES_PATH = DATA_DIR / "autonomous_fixes.json"
_LOCK_PATH = str(FIXES_PATH) + ".lock"
_LOCK_TIMEOUT_SECONDS = 10

# How confident the blended cluster+neural score must be before an
# autonomous fix is drafted at all — deliberately high, since this skips
# the "human reviews everything a priori" step that troubleshooter.
# pending_errors.py's gap worklist has; below this, a recurring error
# still surfaces (through the normal DB-scan gap-detection path in
# local_intelligence.retrain()) but only as a suggestion for a human to
# draft from scratch, never a one-click "approve the AI's guess" item.
AUTONOMOUS_FIX_CONFIDENCE_THRESHOLD = 0.55


def _load() -> Dict[str, Any]:
    if not FIXES_PATH.exists():
        return {"items": {}}
    try:
        with open(FIXES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"items": {}}
    if not isinstance(data, dict) or "items" not in data:
        return {"items": {}}
    return data


def _save(data: Dict[str, Any]) -> None:
    tmp_path = str(FIXES_PATH) + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, FIXES_PATH)


def _kb_lookup() -> Dict[str, Dict[str, str]]:
    """{pattern_text: {"meaning":.., "how_to_check":.., "action":..,
    "responsible":.., "category":..}} for every current KB row —
    fresh every call since the KB can change between deep-learns."""
    from troubleshooter.loader import (
        COL_ACTION,
        COL_CATEGORY,
        COL_ERROR_PATTERN,
        COL_HOW_TO_CHECK,
        COL_MEANING,
        COL_RESPONSIBLE,
        load_troubleshoot_db,
    )

    df_ts = load_troubleshoot_db()
    out: Dict[str, Dict[str, str]] = {}
    for _, row in df_ts.iterrows():
        pattern = str(row.get(COL_ERROR_PATTERN, "") or "").strip()
        if not pattern or pattern in out:
            continue
        out[pattern] = {
            "meaning": str(row.get(COL_MEANING, "") or ""),
            "how_to_check": str(row.get(COL_HOW_TO_CHECK, "") or ""),
            "action": str(row.get(COL_ACTION, "") or ""),
            "responsible": str(row.get(COL_RESPONSIBLE, "") or ""),
            "category": str(row.get(COL_CATEGORY, "") or "") if COL_CATEGORY in df_ts.columns else "",
        }
    return out


def generate_autonomous_fixes(created_by: str = "SYSTEM") -> Dict[str, Any]:
    """
    Reads the DB-error groups cached by the most recent
    ilt_ai_core.train_cluster_model(conn=..., ...) run (no fresh DB
    query here — the mega deep-learn already did the one expensive scan;
    this just reasons over its results), scores each group against every
    KB pattern via the same cluster+neural blend used for live
    Troubleshooter matching, and drafts a pending autonomous-fix
    proposal for every group that clears AUTONOMOUS_FIX_CONFIDENCE_THRESHOLD
    and MIN_OCCURRENCES_FOR_AUTONOMOUS_FIX. Idempotent: a normalized
    pattern already recorded (pending, approved, or rejected) is never
    re-drafted.

    Groups that recur often enough but where the AI ISN'T confident
    enough to auto-draft a fix are not silently discarded: they are
    persisted as "needs_teaching" candidates (see list_teaching_candidates
    / teach_fix) for the Learn Center — a human picks the correct KB
    pattern, and that becomes a confirmed training example the same way
    an approved autonomous fix does.

    Returns {"drafted": int, "skipped_low_confidence": int,
    "skipped_already_known": int, "reason": str|None (if the cluster
    model hasn't been mega-trained with a DB scan yet)}.
    """
    from troubleshooter import ilt_ai_core

    cluster_state = ilt_ai_core._load_cluster_state()
    if not cluster_state.get("trained"):
        return {"drafted": 0, "reason": "cluster_model_not_trained"}

    db_error_groups: Dict[str, Any] = cluster_state.get("db_error_groups") or {}
    if not db_error_groups:
        return {"drafted": 0, "reason": "no_db_scan_yet"}

    kb_patterns = cluster_state.get("kb_patterns") or []
    kb_info = _kb_lookup()

    data = _load()
    items = data["items"]
    now = datetime.now().isoformat(timespec="seconds")

    drafted = 0
    skipped_low_confidence = 0
    skipped_already_known = 0

    for norm, group in db_error_groups.items():
        if norm in items:
            skipped_already_known += 1
            continue
        occurrences = int(group.get("occurrences", 0))
        if occurrences < ilt_ai_core.MIN_OCCURRENCES_FOR_AUTONOMOUS_FIX:
            skipped_low_confidence += 1
            continue

        query_text = group.get("representative_err_msg", "")
        scores = ilt_ai_core.neural_predict_scores(query_text, kb_patterns)
        if not scores:
            skipped_low_confidence += 1
            continue

        best_pattern = max(scores, key=scores.get)
        best_score = scores[best_pattern]
        if best_score < AUTONOMOUS_FIX_CONFIDENCE_THRESHOLD:
            # Not confident enough to auto-draft — but still a real,
            # recurring pattern worth a human teaching the AI what it
            # actually is (Learn Center), so keep its best guess(es).
            top3 = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:3]
            items[norm] = {
                "normalized_pattern": norm,
                "representative_err_msg": query_text,
                "example_variants": group.get("example_variants", []),
                "distinct_variants": group.get("distinct_variants", 0),
                "occurrences": occurrences,
                "suggested_patterns": [{"pattern": p, "confidence": round(float(s), 3)} for p, s in top3],
                "status": "needs_teaching",
                "assigned_to": None,
                "created_at": now,
                "created_by": created_by,
            }
            skipped_low_confidence += 1
            continue

        kb_entry = kb_info.get(best_pattern, {})
        items[norm] = {
            "normalized_pattern": norm,
            "representative_err_msg": group.get("representative_err_msg", ""),
            "example_variants": group.get("example_variants", []),
            "distinct_variants": group.get("distinct_variants", 0),
            "occurrences": occurrences,
            "matched_kb_pattern": best_pattern,
            "confidence": round(float(best_score), 3),
            "proposed_meaning": kb_entry.get("meaning", ""),
            "proposed_how_to_check": kb_entry.get("how_to_check", ""),
            "proposed_action": kb_entry.get("action", ""),
            "proposed_responsible": kb_entry.get("responsible", ""),
            "proposed_category": kb_entry.get("category", ""),
            "status": "pending_approval",
            "assigned_to": None,
            "created_at": now,
            "created_by": created_by,
        }
        drafted += 1

    _save(data)
    return {
        "drafted": drafted,
        "skipped_low_confidence": skipped_low_confidence,
        "skipped_already_known": skipped_already_known,
        "reason": None,
    }


def list_teaching_candidates(assigned_to: Optional[str] = None) -> List[Dict[str, Any]]:
    """"Learn Center" worklist: real, recurring errors the AI genuinely
    isn't confident about yet (below AUTONOMOUS_FIX_CONFIDENCE_THRESHOLD),
    each carrying the AI's best guess(es) so a human teacher has a
    starting point rather than a blank page. See teach_fix()."""
    data = _load()
    result = []
    for key, item in data["items"].items():
        if item.get("status") != "needs_teaching":
            continue
        if assigned_to and (item.get("assigned_to") or "").lower() != assigned_to.lower():
            continue
        entry = dict(item)
        entry["key"] = key
        result.append(entry)
    result.sort(key=lambda x: x.get("occurrences", 0), reverse=True)
    return result


def count_teaching_candidates() -> int:
    return sum(1 for item in _load()["items"].values() if item.get("status") == "needs_teaching")


def teach_fix(key: str, cws: str, correct_pattern: str) -> Dict[str, Any]:
    """
    A human (typically a Support user routed here through the Learn
    Center's "assign to" feature) tells the AI which existing KB pattern
    this recurring error actually corresponds to. This is real teaching,
    not just approval: the AI had LOW confidence here (that's exactly
    why it's a teaching candidate instead of an autonomous-fix draft),
    so the human's answer is the new ground truth — logged the same way
    an approved autonomous fix is (troubleshooter.feedback_store.
    log_confirmed_match), directly feeding Level 2's supervised ensemble
    so next mega deep-learn the AI is more confident on this exact
    pattern family.
    """
    from troubleshooter.feedback_store import log_confirmed_match

    correct_pattern = (correct_pattern or "").strip()
    if not correct_pattern:
        return {"ok": False, "reason": "no_pattern_selected"}
    try:
        with FileLock(_LOCK_PATH, timeout=_LOCK_TIMEOUT_SECONDS):
            data = _load()
            item = data["items"].get(key)
            if item is None:
                return {"ok": False, "reason": "not_found"}
            log_confirmed_match(
                err_msg=item["representative_err_msg"],
                matched_pattern=correct_pattern,
                cws=cws,
                source="learn_center",
            )
            item["status"] = "taught"
            item["taught_pattern"] = correct_pattern
            item["taught_by"] = cws
            item["taught_at"] = datetime.now().isoformat(timespec="seconds")
            _save(data)
            return {"ok": True}
    except Timeout:
        return {"ok": False, "reason": "locked"}


def list_pending_fixes(assigned_to: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns pending autonomous-fix proposals (each includes its dict
    key as "key"), sorted by occurrence count (most-impactful first).
    If `assigned_to` is given, only that user's assigned items are
    returned (the "Learn Center" routing feature — see assign_fix())."""
    data = _load()
    result = []
    for key, item in data["items"].items():
        if item.get("status") != "pending_approval":
            continue
        if assigned_to and (item.get("assigned_to") or "").lower() != assigned_to.lower():
            continue
        entry = dict(item)
        entry["key"] = key
        result.append(entry)
    result.sort(key=lambda x: x.get("occurrences", 0), reverse=True)
    return result


def count_pending() -> int:
    return sum(1 for item in _load()["items"].values() if item.get("status") == "pending_approval")


def assign_fix(key: str, assigned_to: str, assigned_by: str) -> Dict[str, Any]:
    """
    "Learn Center" routing: an admin/support user points a specific
    pending autonomous-fix proposal at a specific person (typically
    someone being onboarded/taught how a given error class is resolved)
    so that person reviews and approves/rejects it themselves — the
    "select a user/support to teach the AI at each step" workflow.
    Purely a routing hint (list_pending_fixes(assigned_to=...) filters
    on it); anyone with approval rights can still act on any item
    regardless of assignment.
    """
    try:
        with FileLock(_LOCK_PATH, timeout=_LOCK_TIMEOUT_SECONDS):
            data = _load()
            item = data["items"].get(key)
            if item is None:
                return {"ok": False, "reason": "not_found"}
            item["assigned_to"] = assigned_to
            item["assigned_by"] = assigned_by
            item["assigned_at"] = datetime.now().isoformat(timespec="seconds")
            _save(data)
            return {"ok": True}
    except Timeout:
        return {"ok": False, "reason": "locked"}


def approve_fix(key: str, cws: str) -> Dict[str, Any]:
    """
    Human confirms the AI's guess was right: logs a confirmed
    (err_msg -> matched_kb_pattern) training example for Level 2's
    supervised ensemble (troubleshooter.feedback_store.log_confirmed_match)
    WITHOUT touching the KB entry's text — nothing new to add, the
    existing fix already covers this. This is the ONLY path that marks
    an autonomous fix "approved"."""
    from troubleshooter.feedback_store import log_confirmed_match

    try:
        with FileLock(_LOCK_PATH, timeout=_LOCK_TIMEOUT_SECONDS):
            data = _load()
            item = data["items"].get(key)
            if item is None:
                return {"ok": False, "reason": "not_found"}
            log_confirmed_match(
                err_msg=item["representative_err_msg"],
                matched_pattern=item["matched_kb_pattern"],
                cws=cws,
                source="autonomous_fix",
            )
            item["status"] = "approved"
            item["approved_by"] = cws
            item["approved_at"] = datetime.now().isoformat(timespec="seconds")
            _save(data)
            return {"ok": True}
    except Timeout:
        return {"ok": False, "reason": "locked"}


def reject_fix(key: str, cws: str, reason: str = "") -> Dict[str, Any]:
    """Human disagrees with the AI's guess — marks the item rejected
    (kept in history, removed from the active worklist, never re-drafted
    for this normalized pattern again)."""
    try:
        with FileLock(_LOCK_PATH, timeout=_LOCK_TIMEOUT_SECONDS):
            data = _load()
            item = data["items"].get(key)
            if item is None:
                return {"ok": False, "reason": "not_found"}
            item["status"] = "rejected"
            item["rejected_by"] = cws
            item["rejected_at"] = datetime.now().isoformat(timespec="seconds")
            item["reject_reason"] = reason
            _save(data)
            return {"ok": True}
    except Timeout:
        return {"ok": False, "reason": "locked"}
