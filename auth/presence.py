"""
auth/presence.py
=================
Lightweight "who's online" tracking for the Administration tab and sidebar.

There is no persistent socket subscription exposed to Streamlit app code, so
this is implemented as a heartbeat instead of true real-time presence:
every time a logged-in user's browser triggers a script rerun (opening the
app, switching tabs, clicking a button, submitting a form, etc.) we stamp
their CWS with the current timestamp in a small on-disk JSON file. Anyone
whose heartbeat is more recent than ONLINE_THRESHOLD_SECONDS is considered
"online" — a solid approximation for an internal tool without needing a
websocket/presence server.

Persisted to data/presence.json (safe_json: locked + atomic writes, so it's
safe with several users hitting the app at once).
"""
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from utils.safe_json import load_json as _safe_load_json, json_transaction

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
PRESENCE_PATH = os.path.join(DATA_DIR, "presence.json")

# A heartbeat older than this is considered "offline". Kept generous (a few
# minutes) since Streamlit only reruns the script on interaction — someone
# quietly reading the screen without clicking anything still counts as
# online for a while after their last action.
ONLINE_THRESHOLD_SECONDS = 5 * 60


def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def heartbeat(cws: str, name: str = "") -> None:
    """Stamps `cws` as active right now. Call once per page render for the
    logged-in user — cheap (single small JSON file, atomic write)."""
    if not cws:
        return
    _ensure_data_dir()
    key = cws.strip().upper()
    with json_transaction(PRESENCE_PATH, default={}) as presence:
        presence[key] = {
            "name": name or presence.get(key, {}).get("name", key),
            "last_seen": datetime.now().isoformat(timespec="seconds"),
        }


def _load_presence() -> Dict[str, Any]:
    _ensure_data_dir()
    return _safe_load_json(PRESENCE_PATH, default={})


def _parse(entry: Dict[str, Any]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(entry["last_seen"])
    except (KeyError, ValueError, TypeError):
        return None


def is_online(cws: str) -> bool:
    presence = _load_presence()
    entry = presence.get((cws or "").strip().upper())
    if not entry:
        return False
    last_seen = _parse(entry)
    if last_seen is None:
        return False
    return datetime.now() - last_seen <= timedelta(seconds=ONLINE_THRESHOLD_SECONDS)


def last_seen(cws: str) -> Optional[str]:
    """Raw ISO timestamp string of the last heartbeat for `cws`, or None."""
    entry = _load_presence().get((cws or "").strip().upper())
    return entry.get("last_seen") if entry else None


def humanize_last_seen(cws: str) -> str:
    """Small human-friendly string like 'online now', '3 min ago', '2h ago'."""
    entry = _load_presence().get((cws or "").strip().upper())
    if not entry:
        return "never"
    last = _parse(entry)
    if last is None:
        return "never"
    delta = datetime.now() - last
    if delta <= timedelta(seconds=ONLINE_THRESHOLD_SECONDS):
        return "online now"
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def list_online_users() -> List[Dict[str, Any]]:
    """Returns [{"cws":..., "name":..., "last_seen":...}] for everyone whose
    heartbeat is fresh, most-recently-active first."""
    presence = _load_presence()
    now = datetime.now()
    online = []
    for cws, entry in presence.items():
        last = _parse(entry)
        if last is not None and now - last <= timedelta(seconds=ONLINE_THRESHOLD_SECONDS):
            online.append({"cws": cws, "name": entry.get("name", cws), "last_seen": entry["last_seen"]})
    online.sort(key=lambda e: e["last_seen"], reverse=True)
    return online
