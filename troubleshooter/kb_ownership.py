"""
troubleshooter/kb_ownership.py
===============================
Tracks WHO owns each knowledge-base entry (Main sheet row, identified by its
normalized error pattern text) and WHEN it was created/last updated, without
touching the existing `stepsdummy.xlsx` column contract that `loader.py` /
`engine.py` already depend on.

Design: a parallel JSON store (data/kb_ownership.json) keyed by the
normalized error pattern string. This keeps the KB merge/matching code
(loader.py, feedback_store.py) completely untouched in terms of the xlsx
schema, while still allowing:
  - "created_by" / "created_at" / "updated_by" / "updated_at" per entry.
  - A short version history (previous action text + who/when) so superseded
    fixes aren't silently lost.
  - A "freshness" badge: green (<3 months since last update), yellow
    (3-12 months), red (>=1 year, or explicitly marked historical/superseded).

All entries that existed in the KB before this feature was introduced are
migrated to a synthetic "SYSTEM" owner (see `migrate_defaults`).
"""
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from config import app_settings
from utils.safe_json import (
    json_transaction,
    load_json as _safe_load_json,
    save_json as _safe_save_json,
)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
OWNERSHIP_PATH = os.path.join(DATA_DIR, "kb_ownership.json")

SYSTEM_OWNER = "SYSTEM"

GREEN_DAYS = 90     # < 3 months -> green
YELLOW_DAYS = 365   # 3-12 months -> yellow, >= 1 year -> red


def _normalize(pattern: str) -> str:
    return (pattern or "").strip().lower()


def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _load() -> Dict[str, Any]:
    # Lock-protected atomic load (see utils/safe_json.py).
    _ensure_data_dir()
    return _safe_load_json(OWNERSHIP_PATH, default={})


def _save(store: Dict[str, Any]) -> None:
    _ensure_data_dir()
    _safe_save_json(OWNERSHIP_PATH, store)


def get_meta(pattern: str) -> Dict[str, Any]:
    """Returns ownership metadata for a pattern, defaulting to SYSTEM if unknown."""
    store = _load()
    key = _normalize(pattern)
    entry = store.get(key)
    if entry is None:
        now = datetime.now().isoformat(timespec="seconds")
        return {
            "created_by": SYSTEM_OWNER,
            "created_at": now,
            "updated_by": SYSTEM_OWNER,
            "updated_at": now,
            "history": [],
        }
    return entry


def get_owner(pattern: str) -> str:
    """The current 'owner' of an entry is whoever last updated it (or created it)."""
    meta = get_meta(pattern)
    return meta.get("updated_by") or meta.get("created_by") or SYSTEM_OWNER


def stamp_created(pattern: str, cws: str) -> None:
    with json_transaction(OWNERSHIP_PATH, default={}) as store:
        key = _normalize(pattern)
        now = datetime.now().isoformat(timespec="seconds")
        store[key] = {
            "created_by": cws or SYSTEM_OWNER,
            "created_at": now,
            "updated_by": cws or SYSTEM_OWNER,
            "updated_at": now,
            "history": [],
        }


def stamp_updated(pattern: str, cws: str, previous_action_snapshot: Optional[str] = None) -> None:
    """
    Marks a pattern as updated by `cws` now, pushing the previous action text
    (if any) into the history list so it can be shown as a superseded/old
    version (rendered red) rather than being lost.
    """
    with json_transaction(OWNERSHIP_PATH, default={}) as store:
        key = _normalize(pattern)
        now = datetime.now().isoformat(timespec="seconds")
        entry = store.get(key)
        if entry is None:
            entry = {
                "created_by": cws or SYSTEM_OWNER,
                "created_at": now,
                "updated_by": cws or SYSTEM_OWNER,
                "updated_at": now,
                "history": [],
            }
        else:
            if previous_action_snapshot is not None:
                entry.setdefault("history", []).append({
                    "by": entry.get("updated_by", SYSTEM_OWNER),
                    "at": entry.get("updated_at", now),
                    "action_snapshot": previous_action_snapshot,
                })
            entry["updated_by"] = cws or SYSTEM_OWNER
            entry["updated_at"] = now
        store[key] = entry


def migrate_defaults(patterns: List[str], default_owner: str = SYSTEM_OWNER) -> int:
    """
    Ensures every given pattern has an ownership entry. Patterns not yet
    tracked are created attributed to `default_owner` (SYSTEM) with "now" as
    the timestamp. Returns how many new entries were created.
    """
    created = 0
    with json_transaction(OWNERSHIP_PATH, default={}) as store:
        now = datetime.now().isoformat(timespec="seconds")
        for p in patterns:
            key = _normalize(p)
            if not key:
                continue
            if key not in store:
                store[key] = {
                    "created_by": default_owner,
                    "created_at": now,
                    "updated_by": default_owner,
                    "updated_at": now,
                    "history": [],
                }
                created += 1
    return created


def freshness(updated_at_iso: str) -> Tuple[str, str]:
    """
    Returns (color, label) where color in {"green", "yellow", "red"}.
      green  = updated < 3 months ago
      yellow = updated 3-12 months ago
      red    = updated >= 1 year ago
    """
    try:
        updated_at = datetime.fromisoformat(updated_at_iso)
    except (ValueError, TypeError):
        return "red", "unknown age"

    yellow_days = max(int(app_settings.get_setting("kb_freshness_yellow_days", GREEN_DAYS)), 1)
    red_days = max(int(app_settings.get_setting("kb_freshness_red_days", YELLOW_DAYS)), yellow_days + 1)

    days = (datetime.now() - updated_at).days
    if days < yellow_days:
        return "green", f"updated {days}d ago"
    if days < red_days:
        return "yellow", f"updated {days}d ago"
    return "red", f"updated {days}d ago (stale)"


def all_meta() -> Dict[str, Any]:
    return _load()


def delete_meta(pattern: str) -> None:
    """Removes ownership metadata for a pattern (call when the KB row itself is deleted)."""
    with json_transaction(OWNERSHIP_PATH, default={}) as store:
        key = _normalize(pattern)
        if key in store:
            del store[key]


def _cws_eq(a: Optional[str], b: Optional[str]) -> bool:
    """Case-insensitive CWS comparison (users may type CWS in different case)."""
    return (a or "").strip().lower() == (b or "").strip().lower()


def can_edit_directly(pattern: str, cws: str) -> bool:
    """
    A user may edit a KB entry directly if:
      - they are the current owner (last updater), OR
      - the entry is still SYSTEM-owned (nobody has claimed it yet).
    Otherwise, changes must go through the fix-request/approval workflow.
    """
    owner = get_owner(pattern)
    return owner == SYSTEM_OWNER or _cws_eq(owner, cws)


def transfer_ownership(pattern: str, new_owner_cws: str) -> None:
    """
    Reassigns BOTH created_by and updated_by for a pattern to `new_owner_cws`,
    keeping existing timestamps/history untouched. Used e.g. to attribute the
    baseline SYSTEM-owned entries to the real person who curated them.
    """
    with json_transaction(OWNERSHIP_PATH, default={}) as store:
        key = _normalize(pattern)
        entry = store.get(key)
        if entry is None:
            now = datetime.now().isoformat(timespec="seconds")
            store[key] = {
                "created_by": new_owner_cws or SYSTEM_OWNER,
                "created_at": now,
                "updated_by": new_owner_cws or SYSTEM_OWNER,
                "updated_at": now,
                "history": [],
            }
            return
        entry["created_by"] = new_owner_cws
        entry["updated_by"] = new_owner_cws
        store[key] = entry


def transfer_all_system_ownership(new_owner_cws: str) -> int:
    """
    Reassigns every entry currently owned by SYSTEM (both created_by AND
    updated_by still SYSTEM, i.e. never claimed/edited by anyone else) to
    `new_owner_cws`. Returns how many entries were transferred.
    """
    n = 0
    with json_transaction(OWNERSHIP_PATH, default={}) as store:
        for key, entry in store.items():
            if entry.get("created_by") == SYSTEM_OWNER and entry.get("updated_by") == SYSTEM_OWNER:
                entry["created_by"] = new_owner_cws
                entry["updated_by"] = new_owner_cws
                n += 1
    return n


def rename_pattern(old_pattern: str, new_pattern: str, cws: str) -> None:
    """
    Moves the ownership/history metadata from `old_pattern` to `new_pattern`
    (used when a user edits/renames a KB entry's error-pattern text so its
    ownership record follows the row instead of becoming orphaned), and
    stamps the move as an update by `cws`.
    """
    with json_transaction(OWNERSHIP_PATH, default={}) as store:
        old_key = _normalize(old_pattern)
        new_key = _normalize(new_pattern)
        now = datetime.now().isoformat(timespec="seconds")
        if old_key == new_key:
            entry = store.get(new_key)
            if entry is None:
                entry = {
                    "created_by": cws or SYSTEM_OWNER,
                    "created_at": now,
                    "updated_by": cws or SYSTEM_OWNER,
                    "updated_at": now,
                    "history": [],
                }
            else:
                entry["updated_by"] = cws or SYSTEM_OWNER
                entry["updated_at"] = now
            store[new_key] = entry
            return

        entry = store.pop(old_key, None)
        if entry is None:
            entry = {
                "created_by": cws or SYSTEM_OWNER,
                "created_at": now,
                "updated_by": cws or SYSTEM_OWNER,
                "updated_at": now,
                "history": [],
            }
        else:
            entry["updated_by"] = cws or SYSTEM_OWNER
            entry["updated_at"] = now
        store[new_key] = entry