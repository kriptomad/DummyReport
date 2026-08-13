"""
auth/session_store.py
======================
Server-side session-token store so a logged-in user stays logged in
across a browser refresh (F5) — Streamlit normally starts a fresh
session on reconnect, which would otherwise show the login screen again
every time.

WHY THIS IS NOW A SHARED FILE (not just in-memory per process)
------------------------------------------------------------------
This app is split across THREE independent Streamlit processes now:
ILT Troubleshooter (app.py), the standalone PSLD - Parts app
(psld_app.py), and the shared login/portal gateway (portal_app.py) —
each its own Python interpreter with its own memory space. A purely
in-memory session dict (the original design) would mean logging in on
one process is invisible to the other two — i.e. no real single sign-on,
defeating the whole point of a shared portal gateway that's supposed to
route you to the right app after ONE login.

So sessions are now persisted to `data/active_sessions.json`, guarded by
the same file-locking (`utils/safe_json.json_transaction`) already used
for every other shared/concurrent-write JSON store in this app (users,
app settings, connection profiles, etc.) — any of the three processes
can create/read/touch/destroy a session and the others see it
immediately (well, on their next Streamlit rerun).

SECURITY NOTE — what does NOT get persisted:
`auth_user` (as returned by `auth/user_store.py::authenticate()`)
includes a transient, DECRYPTED "_private_key_pem" field used only for
reading/composing end-to-end-encrypted messages (see
auth/crypto_messaging.py) — it's derived from the user's password at
login time and is intentionally never written to disk anywhere else in
this app, precisely so that nobody (not even whoever hosts this app) can
read message contents merely by having filesystem access. Persisting
sessions to disk must not break that guarantee, so `_private_key_pem`
(and anything else prefixed with "_", the app's own convention for
transient/non-persisted fields) is stripped before writing and is simply
absent from a session restored by a DIFFERENT process than the one the
user actually typed their password into. `ui/messaging_widget.py`
already handles a missing private key gracefully (shows an "unlock"
notice) — logging in again directly on that specific app re-derives it.

Sessions still expire after `timeout_minutes` of inactivity (refreshed
on each successful lookup — a sliding expiration), and stale/expired
entries are pruned opportunistically on every read/write.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from utils.safe_json import json_transaction

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
SESSIONS_PATH = os.path.join(DATA_DIR, "active_sessions.json")

DEFAULT_TIMEOUT_MINUTES = 480  # 8 hours, matches app_settings.session_timeout_minutes default


def _strip_transient_fields(user: Dict[str, Any]) -> Dict[str, Any]:
    """Removes any field starting with "_" (this app's convention for
    transient, session-only data — currently just "_private_key_pem")
    before a user dict is written to the shared, on-disk session store."""
    return {k: v for k, v in user.items() if not k.startswith("_")}


def _prune_expired(sessions: Dict[str, Any]) -> None:
    now = datetime.now().isoformat()
    expired = [tok for tok, rec in sessions.items() if rec.get("expires_at", "") < now]
    for tok in expired:
        sessions.pop(tok, None)


def create_session(user: Dict[str, Any], timeout_minutes: int = DEFAULT_TIMEOUT_MINUTES) -> str:
    """Stores `user` (the full auth_user dict, minus transient fields —
    see module docstring) under a new random token, visible to all three
    apps, and returns the token."""
    token = secrets.token_urlsafe(32)
    with json_transaction(SESSIONS_PATH, default={}) as sessions:
        _prune_expired(sessions)
        sessions[token] = {
            "user": _strip_transient_fields(user),
            "expires_at": (datetime.now() + timedelta(minutes=timeout_minutes)).isoformat(),
        }
    return token


def get_session(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Returns the stored auth_user dict for `token` (without the
    transient messaging private key — see module docstring), or None if
    missing/expired."""
    if not token:
        return None
    with json_transaction(SESSIONS_PATH, default={}) as sessions:
        record = sessions.get(token)
        if not record:
            return None
        if record.get("expires_at", "") < datetime.now().isoformat():
            sessions.pop(token, None)
            return None
        return dict(record["user"])


def touch_session(token: Optional[str], timeout_minutes: int = DEFAULT_TIMEOUT_MINUTES) -> None:
    """Extends a session's expiry on activity (sliding session)."""
    if not token:
        return
    with json_transaction(SESSIONS_PATH, default={}) as sessions:
        record = sessions.get(token)
        if record:
            record["expires_at"] = (datetime.now() + timedelta(minutes=timeout_minutes)).isoformat()


def update_session(token: Optional[str], user: Dict[str, Any]) -> None:
    """Overwrites the cached `user` dict for an already-active session
    (expiry left untouched) — needed whenever something changes a field
    of the CURRENTLY logged-in user's own record after their session was
    already created (e.g. successfully completing the forced
    password-change gate, which flips `must_change_password` back to
    False). Without this, the next page reload/new tab would call
    get_session() and restore the STALE cached copy (still flagged
    must_change_password=True from login time), permanently re-trapping
    the user on the forced-change screen even though they already changed
    their password — the login form would then reject their real new
    password's temp-password field, looking like "can't log in at all"."""
    if not token:
        return
    with json_transaction(SESSIONS_PATH, default={}) as sessions:
        record = sessions.get(token)
        if record:
            record["user"] = _strip_transient_fields(user)


def destroy_session(token: Optional[str]) -> None:
    """Removes a session (used on logout) — signs the user out of ALL
    three apps at once, since they share this same store."""
    if not token:
        return
    with json_transaction(SESSIONS_PATH, default={}) as sessions:
        sessions.pop(token, None)


def list_active_sessions() -> List[Dict[str, Any]]:
    """Read-only snapshot of every currently-valid session, for the
    Central Admin Dashboard's Console tab — one row per signed-in
    browser (a user with two tabs/devices open shows up twice, which is
    the point: it reflects real active sessions, not unique users)."""
    with json_transaction(SESSIONS_PATH, default={}) as sessions:
        _prune_expired(sessions)
        return [
            {
                "cws": (rec.get("user") or {}).get("cws", ""),
                "name": (rec.get("user") or {}).get("name", ""),
                "expires_at": rec.get("expires_at", ""),
            }
            for rec in sessions.values()
        ]

