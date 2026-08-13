"""
troubleshooter/fix_requests.py
================================
Collaboration workflow for the knowledge base: a user can send the OWNER of
a KB fix one of three request types:
  - "new_fix"     : propose a brand-new fix for an error not yet in the KB.
  - "question"    : ask a clarifying question about an existing/proposed fix.
  - "improvement" : propose an improvement to an existing solution.

The owner of the targeted fix (see troubleshooter.kb_ownership.get_owner)
can Accept or Reject each request:
  - Accepting a "new_fix"/"improvement" request applies the proposed action
    text to the knowledge base (via troubleshooter.feedback_store) and marks
    the requester as the new owner (they authored the accepted change).
  - Accepting a "question" simply records the owner's answer.
  - Rejecting ANY request requires a reason, which is stored and shown back
    to the requester.

Persisted to data/fix_requests.json (JSON list, never lost).
"""
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from utils.safe_json import (
    json_transaction,
    load_json as _safe_load_json,
    save_json as _safe_save_json,
)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
REQUESTS_PATH = os.path.join(DATA_DIR, "fix_requests.json")

REQUEST_TYPES = ("new_fix", "question", "improvement")
STATUSES = ("pending", "accepted", "rejected")


def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _load() -> List[Dict[str, Any]]:
    # Lock-protected atomic load (see utils/safe_json.py).
    _ensure_data_dir()
    return _safe_load_json(REQUESTS_PATH, default=[])


def _save(requests: List[Dict[str, Any]]) -> None:
    _ensure_data_dir()
    _safe_save_json(REQUESTS_PATH, requests)


def create_request(
    requester_cws: str,
    requester_name: str,
    owner_cws: str,
    err_pattern: str,
    request_type: str,
    message: str,
    proposed_action: Optional[str] = None,
) -> Dict[str, Any]:
    if request_type not in REQUEST_TYPES:
        raise ValueError(f"Invalid request_type: {request_type}")

    with json_transaction(REQUESTS_PATH, default=[]) as requests:
        new_id = (max((r["id"] for r in requests), default=0)) + 1
        entry = {
            "id": new_id,
            "requester_cws": requester_cws,
            "requester_name": requester_name,
            "owner_cws": owner_cws,
            "err_pattern": err_pattern,
            "request_type": request_type,
            "message": message,
            "proposed_action": proposed_action,
            "status": "pending",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "responded_at": None,
            "response_reason": None,
        }
        requests.append(entry)
    return entry


def get_all() -> List[Dict[str, Any]]:
    return _load()


def _cws_eq(a: Optional[str], b: Optional[str]) -> bool:
    return (a or "").strip().lower() == (b or "").strip().lower()


def get_incoming(owner_cws: str) -> List[Dict[str, Any]]:
    """Requests targeting fixes owned by `owner_cws` (newest first)."""
    items = [r for r in _load() if _cws_eq(r["owner_cws"], owner_cws)]
    return list(reversed(items))


def get_outgoing(requester_cws: str) -> List[Dict[str, Any]]:
    """Requests submitted BY `requester_cws` (newest first)."""
    items = [r for r in _load() if _cws_eq(r["requester_cws"], requester_cws)]
    return list(reversed(items))


def get_pending_count(owner_cws: str) -> int:
    return sum(1 for r in _load() if _cws_eq(r["owner_cws"], owner_cws) and r["status"] == "pending")


def respond_to_request(request_id: int, responder_cws: str, accept: bool, reason: str = "") -> Dict[str, Any]:
    """
    Owner (or admin) responds to a pending request. Returns the updated
    request dict, or raises ValueError if not found / not authorized /
    already resolved.

    NOTE: applying an accepted new_fix/improvement to the actual KB (calling
    feedback_store) is the CALLER's responsibility (typically the UI layer),
    since that needs access to Streamlit-level context. This function only
    manages the request's lifecycle/state.
    """
    with json_transaction(REQUESTS_PATH, default=[]) as requests:
        for r in requests:
            if r["id"] == request_id:
                if r["status"] != "pending":
                    raise ValueError("This request has already been resolved.")
                if not _cws_eq(r["owner_cws"], responder_cws):
                    raise ValueError("Only the fix owner can respond to this request.")
                if not accept and not reason.strip():
                    raise ValueError("A reason is required when rejecting a request.")
                r["status"] = "accepted" if accept else "rejected"
                r["responded_at"] = datetime.now().isoformat(timespec="seconds")
                r["response_reason"] = reason.strip() or ("Approved." if accept else "")
                return r
    raise ValueError(f"Request #{request_id} not found.")
