"""
troubleshooter/psld_mock_tickets.py
======================================
EXPERIMENTAL (Lab Test tab -> "📦 PSLD - Parts" sub-menu -> "🧪 Mock Data").

Since ServiceNow login for this team is still not production-usable
(see the "🔐 ServiceNow Login (testing)" sub-menu — Azure AD app
registration/consent from IT is still pending), this module lets the
team hand-type a small set of FAKE/sample incidents — a mix of
open/new tickets to analyze and closed/resolved/cancelled ones to match
against — so they can dry-run the whole similarity + self-learning
pipeline (troubleshooter/psld_semantic_engine.py,
troubleshooter/servicenow_resolution_kb.py) end-to-end without needing
real ServiceNow access at all.

Data lives in `data/psld_mock_tickets.json` (gitignored — sample/dummy
data for local testing, never real production ticket content).

Each mock ticket mirrors the same shape returned by
integrations/servicenow_poc.py's real fetch functions
(number/state/short_description/description/...), so it's a drop-in
substitute anywhere the app expects a ServiceNow ticket dict — swapping
between "real fetched tickets" and "mock tickets" for a dry run is just
swapping which list gets passed into servicenow_resolution_kb.find_similar_tickets().
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MOCK_PATH = DATA_DIR / "psld_mock_tickets.json"

_LOCK = threading.Lock()

# Matches the state values ServiceNow itself uses for "task" records
# (see integrations/servicenow_poc.py's sysparm_query state filter) —
# keeping the same vocabulary makes mock tickets behave identically to
# real ones anywhere state is checked/displayed.
STATE_OPTIONS = [
    "New",
    "In Progress",
    "On Hold",
    "Resolved",
    "Closed",
    "Cancelled",
]

# States that represent "done" tickets — i.e. the pool a new incoming
# ticket gets matched AGAINST (mirrors the closed/resolved states the
# real fetch_tickets_by_state() call already filters for).
RESOLVED_STATES = ("Resolved", "Closed", "Cancelled")
OPEN_STATES = ("New", "In Progress", "On Hold")


def _load() -> List[Dict[str, Any]]:
    if not MOCK_PATH.exists():
        return []
    try:
        return json.loads(MOCK_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(tickets: List[Dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MOCK_PATH.write_text(json.dumps(tickets, indent=2, ensure_ascii=False), encoding="utf-8")


def list_tickets() -> List[Dict[str, Any]]:
    """Returns all mock tickets, most recently added first."""
    with _LOCK:
        tickets = _load()
    return sorted(tickets, key=lambda t: t.get("created_at", ""), reverse=True)


def add_ticket(
    number: str,
    state: str,
    short_description: str,
    description: str,
    created_by: str = "",
    extra: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Adds one mock ticket. Raises ValueError if required fields are
    missing or `number` is already used by another mock ticket (keeps
    dry-run test data unambiguous). `extra` holds optional real-export
    metadata (category, caller, service, configuration_item, priority,
    created, assignment_group, assigned_to) that isn't used for
    similarity matching but is shown for context/traceability when
    importing a real ServiceNow list-view export."""
    number = (number or "").strip()
    short_description = (short_description or "").strip()
    if not number or not short_description:
        raise ValueError("Ticket number and short description are required.")
    if state not in STATE_OPTIONS:
        raise ValueError(f"Invalid state '{state}'.")

    with _LOCK:
        tickets = _load()
        if any(t.get("number", "").strip().lower() == number.lower() for t in tickets):
            raise ValueError(f"A mock ticket numbered '{number}' already exists.")

        ticket = {
            "id": uuid.uuid4().hex[:12],
            "number": number,
            "state": state,
            "short_description": short_description,
            "description": (description or "").strip(),
            "created_by": created_by or "SYSTEM",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "extra": {k: str(v).strip() for k, v in (extra or {}).items() if str(v or "").strip()},
        }
        tickets.append(ticket)
        _save(tickets)
    return ticket


def delete_ticket(ticket_id: str) -> bool:
    with _LOCK:
        tickets = _load()
        remaining = [t for t in tickets if t.get("id") != ticket_id]
        if len(remaining) == len(tickets):
            return False
        _save(remaining)
    return True


def clear_all() -> int:
    """Deletes every mock ticket. Returns how many were removed."""
    with _LOCK:
        tickets = _load()
        _save([])
    return len(tickets)


def seed_sample_data() -> List[Dict[str, Any]]:
    """
    Populates a tiny, CLEARLY-labeled placeholder set (one resolved
    ticket + one new one that resembles it) so the team can see the
    similarity engine mechanics working immediately, without hand-typing
    anything first. Intentionally domain-neutral generic text — NOT a
    guess at real PSLD - Parts problem categories (the team's real
    historical incidents should be imported via `import_from_excel()`
    once available). No-ops (returns []) if any mock tickets already
    exist, to avoid clobbering real dry-run data.
    """
    if list_tickets():
        return []

    samples = [
        dict(
            number="EXAMPLE-0001", state="Resolved",
            short_description="[SAMPLE] Example resolved incident — replace with real data",
            description=(
                "This is placeholder text only, to demonstrate how the similarity engine "
                "ranks a new incident against past resolved ones. Delete this once you've "
                "imported (or hand-typed) your team's real historical incidents."
            ),
        ),
        dict(
            number="EXAMPLE-0002", state="New",
            short_description="[SAMPLE] Example new incident that resembles EXAMPLE-0001",
            description=(
                "Placeholder text worded similarly to EXAMPLE-0001 on purpose, so running "
                "'Find similar resolutions' against it shows a real, non-zero match score."
            ),
        ),
    ]
    created = [add_ticket(created_by="SYSTEM", **s) for s in samples]
    return created


# ── Common ServiceNow/Excel column-name variants recognized when
# bulk-importing real incidents (see import_from_excel below). Covers
# both the raw ServiceNow field names (e.g. exported from a list view,
# such as "NUMBER, CATEGORY, CALLER, SERVICE, CONFIGURATION ITEM,
# PRIORITY, CREATED, STATE, ASSIGNMENT GROUP, ASSIGNED TO, SHORT DESC.,
# RESOLUTION NOTES") and friendlier renamed variants a human might use.
_COLUMN_ALIASES: Dict[str, List[str]] = {
    "number": ["number", "ticket number", "ticket", "incident", "incident number", "task number"],
    "state": ["state", "status", "incident state"],
    "short_description": ["short description", "short_description", "short desc.", "short desc", "title", "summary"],
    "description": [
        "description", "long description", "details", "notes",
        "resolution notes", "resolution note", "resolution",
    ],
}

# Extra metadata columns that don't map onto our 4 similarity-matching
# fields but are still worth keeping around for context/traceability
# when a real ServiceNow list-view export is imported (see `extra` on
# add_ticket()). Not required — any column not matched here or above is
# simply ignored.
_EXTRA_COLUMN_ALIASES: Dict[str, List[str]] = {
    "category": ["category"],
    "caller": ["caller"],
    "service": ["service"],
    "configuration_item": ["configuration item", "configuration_item", "ci"],
    "priority": ["priority"],
    "created": ["created", "created on", "opened"],
    "assignment_group": ["assignment group", "assignment_group"],
    "assigned_to": ["assigned to", "assigned_to"],
}


def guess_column_mapping(columns: List[str]) -> Dict[str, Optional[str]]:
    """
    Given a spreadsheet's actual column names, tries to auto-detect
    which one corresponds to each of our 4 fields (number/state/
    short_description/description) via case-insensitive matching
    against `_COLUMN_ALIASES`. Returns None for any field it couldn't
    confidently guess — the UI then asks the user to pick that column
    manually via a dropdown instead of silently guessing wrong.
    """
    normalized = {c: c.strip().lower() for c in columns}
    mapping: Dict[str, Optional[str]] = {}
    for field, aliases in _COLUMN_ALIASES.items():
        found = None
        for col, norm in normalized.items():
            if norm in aliases:
                found = col
                break
        mapping[field] = found
    return mapping


def guess_extra_column_mapping(columns: List[str]) -> Dict[str, str]:
    """Same idea as guess_column_mapping() but for the optional
    "extra"/context-only metadata columns (category, caller, service,
    configuration item, priority, created, assignment group, assigned
    to). Only fields that were actually found are included."""
    normalized = {c: c.strip().lower() for c in columns}
    mapping: Dict[str, str] = {}
    for field, aliases in _EXTRA_COLUMN_ALIASES.items():
        for col, norm in normalized.items():
            if norm in aliases:
                mapping[field] = col
                break
    return mapping



def _normalize_state(raw_state: str) -> str:
    """Maps a free-text state value (as exported by ServiceNow, which
    can vary by instance config, e.g. 'Resolved', 'resolved', '6') onto
    one of our STATE_OPTIONS. Falls back to 'New' for anything
    unrecognized, rather than raising — bulk imports of real, messy
    ServiceNow exports shouldn't fail row-by-row over a state label."""
    raw = (raw_state or "").strip().lower()
    for option in STATE_OPTIONS:
        if option.lower() == raw:
            return option
    # ServiceNow's raw numeric task.state values, in case a raw export
    # (not the display value) is pasted in.
    numeric_map = {"1": "New", "2": "In Progress", "3": "On Hold", "6": "Resolved", "7": "Closed", "8": "Cancelled"}
    return numeric_map.get(raw, "New")


def import_from_excel(
    rows: List[Dict[str, Any]],
    column_mapping: Dict[str, str],
    created_by: str = "",
    extra_column_mapping: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Bulk-imports mock tickets from spreadsheet rows (as produced by
    `pandas.DataFrame.to_dict('records')` after reading an uploaded
    Excel/CSV file). `column_mapping` maps our 4 logical fields
    (number/state/short_description/description) to the ACTUAL column
    names in `rows` — built by the UI from `guess_column_mapping()`
    plus any manual corrections. `extra_column_mapping` (optional) maps
    context-only metadata fields (category/caller/service/etc, see
    `guess_extra_column_mapping()`) the same way. Skips rows with no
    ticket number or short description (rather than failing the whole
    batch), and skips numbers that already exist. Returns
    {"created": int, "skipped_existing": int, "skipped_invalid": int}.
    """
    created = 0
    skipped_existing = 0
    skipped_invalid = 0
    extra_column_mapping = extra_column_mapping or {}
    for row in rows:
        number = str(row.get(column_mapping.get("number", ""), "") or "").strip()
        short_desc = str(row.get(column_mapping.get("short_description", ""), "") or "").strip()
        if not number or not short_desc:
            skipped_invalid += 1
            continue
        state_col = column_mapping.get("state")
        raw_state = str(row.get(state_col, "")) if state_col else ""
        state = _normalize_state(raw_state)
        desc_col = column_mapping.get("description")
        description = str(row.get(desc_col, "") or "") if desc_col else ""
        extra = {
            field: row.get(col, "")
            for field, col in extra_column_mapping.items()
            if col
        }

        try:
            add_ticket(
                number=number, state=state, short_description=short_desc,
                description=description, created_by=created_by, extra=extra,
            )
            created += 1
        except ValueError:
            skipped_existing += 1

    return {"created": created, "skipped_existing": skipped_existing, "skipped_invalid": skipped_invalid}
