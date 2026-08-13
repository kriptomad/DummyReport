"""
troubleshooter/pending_errors.py
==================================
Catalog of ERR_MSG values seen during Troubleshooter analysis (or the
admin "Process & Feed Internal AI" DB scan — see
troubleshooter/local_intelligence.py) that don't match anything in the
Knowledge Base yet. Surfaced in the "📌 Pendências" tab so the technical
team/analysts get a prioritized worklist of gaps instead of these
silently vanishing after each analysis.

The internal AI can draft a suggested fix (see
troubleshooter.feedback_store.suggest_new_kb_fix — reuses the closest
existing KB entry and/or an LLM if one is configured), but NOTHING is
ever written to the KB automatically: a human must review the draft
(editing it if needed) and explicitly click "Aprovar e postar no KB"
(see approve() below) before it becomes a real fix. This keeps the
self-learning loop human-in-the-loop by design, per the explicit request
that AI-suggested fixes always wait for review/approval.
"""
import os
import json
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional

from filelock import FileLock, Timeout

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
PENDING_PATH = DATA_DIR / "pending_errors.json"
_LOCK_PATH = str(PENDING_PATH) + ".lock"
_LOCK_TIMEOUT_SECONDS = 10


def _normalize_key(err_msg: str) -> str:
    """Case/whitespace-insensitive dedupe key so trivial formatting
    differences (extra spaces, casing) don't create duplicate pending
    entries for what's really the same error."""
    return " ".join(str(err_msg or "").strip().lower().split())


def _load() -> Dict[str, Any]:
    if not PENDING_PATH.exists():
        return {"items": {}}
    try:
        with open(PENDING_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"items": {}}
    if not isinstance(data, dict) or "items" not in data:
        return {"items": {}}
    return data


def _save(data: Dict[str, Any]) -> None:
    tmp_path = str(PENDING_PATH) + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, PENDING_PATH)


def register_unmatched_batch(err_msgs: List[str], counts: Optional[Dict[str, int]] = None) -> None:
    """
    Upserts every given ERR_MSG (deduplicated, case/whitespace-insensitive)
    into the pending catalog: increments the occurrence count + updates
    last_seen_at for already-known entries, creates a fresh "pending"
    record for new ones. `counts`, if given, maps err_msg -> how many
    times it was actually seen (e.g. from a DB frequency scan) instead of
    assuming 1 per call.

    Entries already "approved"/"dismissed" are left alone (status isn't
    reset to "pending") — re-surfacing something a human already decided
    on would just be noise for the worklist.
    """
    clean = [str(m).strip() for m in (err_msgs or []) if str(m or "").strip()]
    if not clean:
        return
    counts = counts or {}
    now = datetime.now().isoformat(timespec="seconds")

    try:
        with FileLock(_LOCK_PATH, timeout=_LOCK_TIMEOUT_SECONDS):
            data = _load()
            items = data["items"]
            for msg in clean:
                key = _normalize_key(msg)
                if not key:
                    continue
                inc = int(counts.get(msg, 1))
                if key in items:
                    items[key]["occurrences"] = int(items[key].get("occurrences", 0)) + inc
                    items[key]["last_seen_at"] = now
                else:
                    items[key] = {
                        "err_msg": msg,
                        "occurrences": inc,
                        "first_seen_at": now,
                        "last_seen_at": now,
                        "status": "pending",
                        "suggestion": None,
                        "similar": None,
                        "suggestion_source": None,
                        "suggestion_generated_at": None,
                    }
            _save(data)
    except Timeout:
        # Best-effort logging only — never let a lock contention block the
        # actual troubleshooting analysis the user is waiting on.
        pass


def list_pending(include_resolved: bool = False) -> List[Dict[str, Any]]:
    """
    Returns pending catalog entries (each includes its dict key as
    "key", needed for generate_suggestion/approve/dismiss), sorted by
    occurrence count so the most frequent/impactful gaps surface first.
    """
    data = _load()
    result = []
    for key, item in data["items"].items():
        if not include_resolved and item.get("status") != "pending":
            continue
        entry = dict(item)
        entry["key"] = key
        result.append(entry)
    result.sort(key=lambda x: x.get("occurrences", 0), reverse=True)
    return result


def count_pending() -> int:
    return sum(1 for item in _load()["items"].values() if item.get("status") == "pending")


def generate_suggestion(key: str) -> Dict[str, Any]:
    """Computes (or refreshes) the AI-assisted draft suggestion for a
    pending item and persists it. Never touches the KB."""
    from troubleshooter.feedback_store import suggest_new_kb_fix

    try:
        with FileLock(_LOCK_PATH, timeout=_LOCK_TIMEOUT_SECONDS):
            data = _load()
            item = data["items"].get(key)
            if item is None:
                return {"ok": False, "reason": "not_found"}
            result = suggest_new_kb_fix(item["err_msg"])
            item["suggestion"] = result["suggestion"]
            item["similar"] = result["similar"]
            item["suggestion_source"] = result["source"]
            item["suggestion_generated_at"] = datetime.now().isoformat(timespec="seconds")
            _save(data)
            return {"ok": True, **result}
    except Timeout:
        return {"ok": False, "reason": "locked"}


def approve(
    key: str,
    cws: str,
    meaning: str,
    how_to_check: str,
    action: str,
    responsible: str = "",
    category: str = "",
) -> Dict[str, Any]:
    """
    Posts the (human-reviewed, possibly AI-drafted-then-edited) fix to
    the Knowledge Base and marks this pending item resolved. This is the
    ONLY path that ever writes a pending/AI-suggested fix to the KB —
    it always requires this explicit call, made from the "Pendências"
    tab's "Aprovar e postar no KB" button after a human reviews the text.
    """
    from troubleshooter.feedback_store import create_kb_entry_from_pending

    try:
        with FileLock(_LOCK_PATH, timeout=_LOCK_TIMEOUT_SECONDS):
            data = _load()
            item = data["items"].get(key)
            if item is None:
                return {"ok": False, "reason": "not_found"}

            kb_result = create_kb_entry_from_pending(
                err_msg=item["err_msg"],
                meaning=meaning,
                how_to_check=how_to_check,
                action=action,
                cws=cws,
                responsible=responsible,
                category=category,
            )
            item["status"] = "approved"
            item["approved_by"] = cws
            item["approved_at"] = datetime.now().isoformat(timespec="seconds")
            item["kb_action"] = kb_result.get("action")
            _save(data)
            return {"ok": True, "kb_result": kb_result}
    except Timeout:
        return {"ok": False, "reason": "locked"}


def dismiss(key: str, cws: str, reason: str = "") -> Dict[str, Any]:
    """Marks a pending item as dismissed (noise/duplicate/won't-fix) —
    kept in history but removed from the active worklist."""
    try:
        with FileLock(_LOCK_PATH, timeout=_LOCK_TIMEOUT_SECONDS):
            data = _load()
            item = data["items"].get(key)
            if item is None:
                return {"ok": False, "reason": "not_found"}
            item["status"] = "dismissed"
            item["dismissed_by"] = cws
            item["dismissed_at"] = datetime.now().isoformat(timespec="seconds")
            item["dismiss_reason"] = reason
            _save(data)
            return {"ok": True}
    except Timeout:
        return {"ok": False, "reason": "locked"}
