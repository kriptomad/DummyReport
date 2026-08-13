"""
troubleshooter/psld_abend_registry.py
=========================================
EXPERIMENTAL (Lab Test tab -> "📦 PSLD - Parts" sub-menu -> "🚨 ABEND").

A simple registry of legacy batch/batch ABENDs the PSLD - Parts team has
seen, so the team can quickly look up "we've hit this ABEND before, in
this program, and here's how it was fixed" without digging through
ServiceNow or tribal memory. Each entry records:

  - Abend Number          (e.g. "S0C7", "U4038")
  - Abend Program         (the job/program name where it occurred)
  - Abend Resolution      (free text: what fixed it)
  - Responsible Contact   (CWS of a user flagged "PSLD - Parts" —
                            see auth.user_store.list_psld_parts_users() /
                            set_psld_parts_flag() — rendered as a
                            clickable Teams chat link via
                            utils.teams_link.teams_chat_link())

Data lives in `data/psld_abends.json` (gitignored), same JSON-store
pattern as troubleshooter/psld_mock_tickets.py.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ABENDS_PATH = DATA_DIR / "psld_abends.json"

_LOCK = threading.Lock()


def _load() -> List[Dict[str, Any]]:
    if not ABENDS_PATH.exists():
        return []
    try:
        return json.loads(ABENDS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(abends: List[Dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ABENDS_PATH.write_text(json.dumps(abends, indent=2, ensure_ascii=False), encoding="utf-8")


def list_abends() -> List[Dict[str, Any]]:
    """Returns all registered ABENDs, most recently added first."""
    with _LOCK:
        abends = _load()
    return sorted(abends, key=lambda a: a.get("created_at", ""), reverse=True)


def get_abend(abend_id: str) -> Optional[Dict[str, Any]]:
    for a in _load():
        if a.get("id") == abend_id:
            return a
    return None


def add_abend(
    abend_number: str,
    abend_program: str,
    resolution: str,
    responsible_cws: str,
    created_by: str = "",
) -> Dict[str, Any]:
    """Registers one ABEND via the manual form. Raises ValueError if a
    required field is missing. Multiple entries with the same
    abend_number ARE allowed (the same abend code can occur in
    different programs / with different fixes over time) — uniqueness
    is only enforced on the generated `id`."""
    abend_number = (abend_number or "").strip()
    abend_program = (abend_program or "").strip()
    resolution = (resolution or "").strip()
    responsible_cws = (responsible_cws or "").strip().upper()
    if not abend_number or not abend_program or not resolution:
        raise ValueError("Abend number, program and resolution are all required.")
    if not responsible_cws:
        raise ValueError("A responsible contact is required.")

    with _LOCK:
        abends = _load()
        entry = {
            "id": uuid.uuid4().hex[:12],
            "abend_number": abend_number,
            "abend_program": abend_program,
            "resolution": resolution,
            "responsible_cws": responsible_cws,
            "ticket_number": "",
            "source": "manual",
            "created_by": created_by or "SYSTEM",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        abends.append(entry)
        _save(abends)
    return entry


def status_of(entry: Dict[str, Any]) -> str:
    """Computes an entry's status on the fly (not stored) so editing a
    field elsewhere can never leave a stale status behind:
      - "pending_program": auto-detected ABEND with no known program yet
        (see ingest_ticket_for_abend) — needs an analyst to fill it in.
      - "pending_contact": program known but no Responsible Contact
        assigned yet.
      - "complete": program + resolution + contact all present.
    """
    if not (entry.get("abend_program") or "").strip():
        return "pending_program"
    if not (entry.get("responsible_cws") or "").strip():
        return "pending_contact"
    return "complete"


def list_pending_program_abends() -> List[Dict[str, Any]]:
    """ABENDs auto-detected from imported tickets that still need an
    analyst to identify which job/program actually caused them — the
    "⏳ Abend Pendente" list."""
    return [a for a in list_abends() if status_of(a) == "pending_program"]


def list_programs() -> List[str]:
    """Distinct known Abend Programs (non-empty), sorted — used to
    populate the "Filter by Program" dropdown in the ABEND tab."""
    programs = {a.get("abend_program", "").strip() for a in _load()}
    programs.discard("")
    return sorted(programs)


def ingest_ticket_for_abend(
    ticket_number: str,
    short_description: str,
    resolution_notes: str = "",
    created_by: str = "",
) -> Optional[Dict[str, Any]]:
    """
    Runs troubleshooter.psld_abend_parser over a ticket's short
    description (+ resolution notes) and, if it looks like an ABEND,
    auto-registers it here. Returns None (no-op) if:
      - the text doesn't look like an ABEND at all, or
      - a ticket with this `ticket_number` was already ingested before
        (avoids duplicate entries on repeated Excel imports of the same
        export).
    The created entry may have an empty `abend_program` (and thus show
    up in list_pending_program_abends()) if neither the "JCL=" pattern
    in the short description nor the "JOB <program>" pattern in the
    resolution notes could be found — an analyst then fills it in via
    complete_pending_program().
    """
    from troubleshooter import psld_abend_parser

    ticket_number = (ticket_number or "").strip()
    info = psld_abend_parser.extract_abend_info(short_description, resolution_notes)
    if not info["is_abend"]:
        return None

    with _LOCK:
        abends = _load()
        if ticket_number and any(a.get("ticket_number", "") == ticket_number for a in abends):
            return None

        entry = {
            "id": uuid.uuid4().hex[:12],
            "abend_number": info["abend_code"] or ticket_number or "UNKNOWN",
            "abend_program": info["program"],
            "resolution": (resolution_notes or "").strip(),
            "responsible_cws": "",
            "ticket_number": ticket_number,
            "source": "auto_import",
            "program_source": info["program_source"],
            "job_number": info["job_number"],
            "created_by": created_by or "SYSTEM",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        abends.append(entry)
        _save(abends)
    return entry


def complete_pending_program(
    abend_id: str,
    abend_program: str,
    responsible_cws: str = "",
    resolution: Optional[str] = None,
) -> bool:
    """Analyst-supplied follow-up for an auto-detected ABEND whose
    program couldn't be parsed automatically (see
    ingest_ticket_for_abend). Fills in the missing Abend Program (and,
    optionally, the Responsible Contact / resolution text) so the entry
    moves out of the "Abend Pendente" list. Returns False if not found
    or if `abend_program` is blank."""
    abend_program = (abend_program or "").strip()
    if not abend_program:
        return False
    with _LOCK:
        abends = _load()
        for a in abends:
            if a.get("id") == abend_id:
                a["abend_program"] = abend_program
                if responsible_cws:
                    a["responsible_cws"] = responsible_cws.strip().upper()
                if resolution is not None:
                    a["resolution"] = resolution.strip()
                a["updated_at"] = datetime.now().isoformat(timespec="seconds")
                _save(abends)
                return True
    return False



def update_abend(
    abend_id: str,
    abend_number: Optional[str] = None,
    abend_program: Optional[str] = None,
    resolution: Optional[str] = None,
    responsible_cws: Optional[str] = None,
) -> bool:
    """Edits an existing ABEND entry in place. Returns False if not found."""
    with _LOCK:
        abends = _load()
        for a in abends:
            if a.get("id") == abend_id:
                if abend_number is not None:
                    a["abend_number"] = abend_number.strip()
                if abend_program is not None:
                    a["abend_program"] = abend_program.strip()
                if resolution is not None:
                    a["resolution"] = resolution.strip()
                if responsible_cws is not None:
                    a["responsible_cws"] = responsible_cws.strip().upper()
                a["updated_at"] = datetime.now().isoformat(timespec="seconds")
                _save(abends)
                return True
    return False


def delete_abend(abend_id: str) -> bool:
    with _LOCK:
        abends = _load()
        remaining = [a for a in abends if a.get("id") != abend_id]
        if len(remaining) == len(abends):
            return False
        _save(remaining)
    return True


def search_abends(query: str) -> List[Dict[str, Any]]:
    """Simple case-insensitive substring search across abend number,
    PROGRAM, ticket number and resolution text — good enough for a "did
    we already fix this ABEND (or this program)?" quick lookup without
    needing the full similarity engine. Combine with filter_by_program()
    for an exact-match program filter dropdown."""
    q = (query or "").strip().lower()
    if not q:
        return list_abends()
    return [
        a for a in list_abends()
        if q in a.get("abend_number", "").lower()
        or q in a.get("abend_program", "").lower()
        or q in a.get("ticket_number", "").lower()
        or q in a.get("resolution", "").lower()
    ]


def filter_by_program(abends: List[Dict[str, Any]], program: str) -> List[Dict[str, Any]]:
    """Exact (case-insensitive) filter by Abend Program — used by the
    "Filter by Program" dropdown alongside the free-text search above."""
    program = (program or "").strip().lower()
    if not program:
        return abends
    return [a for a in abends if a.get("abend_program", "").strip().lower() == program]
