"""
auth/audit_log.py
====================
Lightweight, append-only audit trail shared by all three apps (ILT
Troubleshooter, PSLD - Parts, the portal gateway) — powers the "Console
/ Audit" view in the Central Admin Dashboard (portal_app.py). Records
security/administratively-relevant events: logins (success/failure),
admin permission changes, portal entries, etc.

Deliberately NOT a full request/transaction logger (that would be a much
bigger undertaking — a proper structured logging + log-shipping setup).
This is a simple, good-enough activity trail for a small internal tool:
a flat JSON list, capped at `MAX_ENTRIES` (oldest dropped first) so it
can't grow unbounded, guarded by the same file-locking used everywhere
else in this app.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from utils.safe_json import json_transaction

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
AUDIT_LOG_PATH = os.path.join(DATA_DIR, "audit_log.json")
MAX_ENTRIES = 5000

# Known categories, purely for the Audit log UI's filter dropdown — new
# categories can still be recorded freely, this is just for a nice picker.
KNOWN_CATEGORIES = [
    "auth",
    "admin",
    "ai_training",
    "db_connection",
    "autonomous_fix",
    "batch_processing",
    "integration",
    "queue",
]


def record_event(
    event_type: str,
    cws: str = "",
    detail: str = "",
    app: str = "",
    category: str = "general",
    severity: str = "info",
) -> None:
    """Appends one audit entry. `event_type` is a short machine-readable
    tag (e.g. "login_success", "login_failed", "flag_changed",
    "portal_entered", "admin_action", "ai_training", "db_connection",
    "autonomous_fix", "queue_error", "integration_error"); `detail` is a
    short human-readable free-text description; `app` names which of the
    three processes logged the event ("ilt", "psld", "portal").

    `category` groups related event_types for the Audit log UI (e.g.
    "auth", "ai_training", "db_connection", "autonomous_fix",
    "batch_processing", "integration", "queue", "admin"). `severity` is
    one of "info"/"warning"/"error" — lets support/dev quickly filter for
    "did anything actually fail" without reading every row."""
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "event_type": event_type,
        "cws": (cws or "").strip().upper(),
        "detail": detail,
        "app": app,
        "category": category,
        "severity": severity,
    }
    with json_transaction(AUDIT_LOG_PATH, default=[]) as log:
        log.append(entry)
        if len(log) > MAX_ENTRIES:
            del log[: len(log) - MAX_ENTRIES]


def list_events(
    limit: int = 200,
    event_type: Optional[str] = None,
    cws: Optional[str] = None,
    category: Optional[str] = None,
    severity: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Most recent events first, optionally filtered by type/user/category/severity."""
    with json_transaction(AUDIT_LOG_PATH, default=[]) as log:
        events = list(log)
    if event_type:
        events = [e for e in events if e.get("event_type") == event_type]
    if cws:
        cws_up = cws.strip().upper()
        events = [e for e in events if e.get("cws") == cws_up]
    if category:
        events = [e for e in events if e.get("category") == category]
    if severity:
        events = [e for e in events if e.get("severity") == severity]
    return list(reversed(events))[:limit]
