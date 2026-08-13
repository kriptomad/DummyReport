"""
utils/history_store.py — Lightweight persistence for query & shipment-ID search history.

Two JSON-backed logs, stored in data/ (outside the assets/ knowledge base):
  - query_history.json     : every query run (type, params, row_count, timestamp)
  - shipment_history.json  : every distinct Shipment ID searched (id, last_searched, times_searched)

Both are capped to the most recent N entries to avoid unbounded growth.
This is intentionally simple (no DB) — good enough for a single-user desktop-style app.
"""
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional

from config import app_settings
from utils.safe_json import (
    json_transaction,
    load_json as _safe_load_json,
    save_json as _safe_save_json,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

QUERY_HISTORY_PATH = DATA_DIR / "query_history.json"
SHIPMENT_HISTORY_PATH = DATA_DIR / "shipment_history.json"

MAX_QUERY_HISTORY = 300
MAX_SHIPMENT_HISTORY = 500


def _max_query_history() -> int:
    return max(int(app_settings.get_setting("max_query_history", MAX_QUERY_HISTORY)), 1)


def _max_shipment_history() -> int:
    return max(int(app_settings.get_setting("max_shipment_history", MAX_SHIPMENT_HISTORY)), 1)


def _load_json(path: Path) -> list:
    # Lock-protected, atomic-write-safe load (see utils/safe_json.py) —
    # prevents corruption when multiple users save history concurrently.
    return _safe_load_json(path, default=[])


def _save_json(path: Path, data) -> None:
    # Lock-protected atomic save. Failures are logged (not silently
    # swallowed) by utils.safe_json.save_json.
    _safe_save_json(path, data)


# ── Query history ────────────────────────────────────────────────

def log_query(query_type: str, params: Dict[str, Any], row_count: Optional[int] = None, error: Optional[str] = None) -> None:
    """Append a query execution record to the persistent query history."""
    with json_transaction(QUERY_HISTORY_PATH, default=[]) as history:
        entry = {
            "type": query_type,
            "params": {k: v for k, v in (params or {}).items() if v not in (None, "")},
            "row_count": row_count,
            "error": error,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        history.append(entry)
        history[:] = history[-_max_query_history():]


def get_query_history(limit: int = 20, query_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return the most recent query history entries (newest first)."""
    history = _load_json(QUERY_HISTORY_PATH)
    if query_type:
        history = [h for h in history if h.get("type") == query_type]
    return list(reversed(history))[:limit]


def clear_query_history() -> None:
    _save_json(QUERY_HISTORY_PATH, [])


# ── Shipment ID search history ───────────────────────────────────

def log_shipment_search(shipment_ids: List[str], source: str = "report") -> None:
    """Record that the given shipment IDs were searched (increments counters)."""
    if not shipment_ids:
        return
    with json_transaction(SHIPMENT_HISTORY_PATH, default=[]) as history:
        by_id = {h["shipment_id"]: h for h in history}
        now = datetime.now().isoformat(timespec="seconds")

        for sid in shipment_ids:
            sid = str(sid).strip()
            if not sid:
                continue
            if sid in by_id:
                by_id[sid]["times_searched"] = by_id[sid].get("times_searched", 0) + 1
                by_id[sid]["last_searched"] = now
                by_id[sid]["last_source"] = source
            else:
                by_id[sid] = {
                    "shipment_id": sid,
                    "times_searched": 1,
                    "first_searched": now,
                    "last_searched": now,
                    "last_source": source,
                }

        merged = sorted(by_id.values(), key=lambda h: h["last_searched"], reverse=True)
        history[:] = merged[:_max_shipment_history()]


def get_shipment_history(limit: int = 20) -> List[Dict[str, Any]]:
    """Return the most recently searched shipment IDs (newest first)."""
    history = _load_json(SHIPMENT_HISTORY_PATH)
    history = sorted(history, key=lambda h: h.get("last_searched", ""), reverse=True)
    return history[:limit]


def clear_shipment_history() -> None:
    _save_json(SHIPMENT_HISTORY_PATH, [])


def parse_ids(raw: Optional[str]) -> List[str]:
    """Split a comma/space/newline separated string into a clean list of IDs."""
    if not raw:
        return []
    import re
    parts = re.split(r"[,\s]+", str(raw).strip())
    return [p for p in parts if p]
