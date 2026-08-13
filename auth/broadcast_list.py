"""
auth/broadcast_list.py
=======================
Admin-managed list of "broadcast recipients" — people the root
administrator (DEMOADMIN) wants to be able to notify all at once (e.g. "new
KB fixes added", "maintenance window", "new feature released") without
composing individual messages one by one.

This is intentionally a thin layer on top of the existing internal
messaging system (auth/messaging.py): sending a broadcast just loops over
the recipient list and calls send_message() for each one, so broadcasts
inherit the same end-to-end encryption, inbox/outbox, and read/unread
tracking as regular 1:1 messages — no separate storage format needed.

Persisted to data/broadcast_list.json: a simple list of CWS strings.
"""
from __future__ import annotations

import os
from typing import List, Tuple

from utils.safe_json import load_json as _safe_load_json, json_transaction

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
BROADCAST_LIST_PATH = os.path.join(DATA_DIR, "broadcast_list.json")


def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def get_recipients() -> List[str]:
    """Returns the current broadcast recipient list (CWS strings, uppercase)."""
    _ensure_data_dir()
    return _safe_load_json(BROADCAST_LIST_PATH, default=[])


def add_recipient(cws: str) -> Tuple[bool, str]:
    cws = (cws or "").strip().upper()
    if not cws:
        return False, "CWS is required."
    _ensure_data_dir()
    with json_transaction(BROADCAST_LIST_PATH, default=[]) as recipients:
        if cws in recipients:
            return False, f"'{cws}' is already in the broadcast list."
        recipients.append(cws)
    return True, f"'{cws}' added to the broadcast list."


def remove_recipient(cws: str) -> Tuple[bool, str]:
    cws = (cws or "").strip().upper()
    _ensure_data_dir()
    with json_transaction(BROADCAST_LIST_PATH, default=[]) as recipients:
        if cws not in recipients:
            return False, f"'{cws}' is not in the broadcast list."
        recipients.remove(cws)
    return True, f"'{cws}' removed from the broadcast list."
