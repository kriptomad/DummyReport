"""
troubleshooter/psld_review_queue.py
====================================
EXPERIMENTAL (Lab Test tab -> "📦 PSLD - Parts" sub-menu -> "🔍 Double-Check").

Implements the "double-check" self-learning safety net requested by the
team: the AI itself scans already-closed tickets (Resolved/Closed/
Cancelled) and proposes which resolution KB entry was probably the fix
used for each one (same blended TF-IDF + semantic + feedback + neural
scoring as the live Analyze tab — see
troubleshooter/psld_semantic_engine.py). Instead of trusting that guess
automatically, each suggestion is queued here as PENDING and only counts
as real self-learning feedback (i.e. only gets fed into
psld_semantic_engine.record_feedback(), which in turn trains the local
neural classifier — see troubleshooter/ai_core.py) once a human with the
"Parts Reviewer" flag (auth/user_store.is_parts_reviewer) explicitly
approves it.

This is deliberately a SEPARATE, slower-moving feedback path from the
"✅ Confirm this match" button on the Analyze tab (which is an analyst
confirming a match for a ticket they're actively working RIGHT NOW).
The double-check queue instead lets the AI proactively review its own
guesses against the team's history of already-closed work, batching
those guesses for a reviewer to rubber-stamp or reject in bulk — raising
match accuracy and "incident fix delivery" over time without requiring
every single past ticket to be manually re-analyzed one at a time.

Data lives in `data/psld_review_queue.json` (gitignored — no secrets,
but no reason to version dry-run/test content either).
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from troubleshooter import psld_mock_tickets, psld_semantic_engine, servicenow_resolution_kb

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
QUEUE_PATH = DATA_DIR / "psld_review_queue.json"

_LOCK = threading.Lock()

# Below this blended score, a ticket->entry guess is too weak to be
# worth a reviewer's time — silently skipped rather than queued.
MIN_SUGGESTION_SCORE = 0.35


def _load() -> List[Dict[str, Any]]:
    if not QUEUE_PATH.exists():
        return []
    try:
        return json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(rows: List[Dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    QUEUE_PATH.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")


def _ticket_text(tk: Dict[str, Any]) -> str:
    return f"{tk.get('short_description', '')}\n\n{tk.get('description', '')}".strip()


def generate_pending_reviews(
    tickets: Optional[List[Dict[str, Any]]] = None,
    source: str = "mock",
) -> Dict[str, Any]:
    """
    Scans already-closed tickets and, for each one not already in the
    queue, computes the AI's best-guess resolution KB match. If that
    guess scores at/above MIN_SUGGESTION_SCORE, it's added to the queue
    as a PENDING double-check item for a Parts Reviewer to confirm or
    reject.

    `tickets` defaults to the team's mock Resolved/Closed/Cancelled
    tickets (the only closed-ticket pool available today, since real
    ServiceNow login is still not production-usable) — but callers can
    pass real cached tickets (e.g. st.session_state["lab_sn_tickets"]
    filtered to closed states) once that's live, tagging `source="real"`.

    Returns a summary dict: {"scanned", "new_pending", "skipped_low_score",
    "skipped_existing", "skipped_no_kb"}.
    """
    if tickets is None:
        tickets = [
            tk for tk in psld_mock_tickets.list_tickets()
            if tk.get("state") in psld_mock_tickets.RESOLVED_STATES
        ]

    kb_entries = servicenow_resolution_kb.list_entries()
    summary = {"scanned": 0, "new_pending": 0, "skipped_low_score": 0, "skipped_existing": 0, "skipped_no_kb": 0}

    if not kb_entries or not tickets:
        summary["skipped_no_kb"] = 0 if kb_entries else len(tickets)
        return summary

    with _LOCK:
        rows = _load()
        existing_ticket_numbers = {r["ticket_number"] for r in rows}

        for tk in tickets:
            summary["scanned"] += 1
            ticket_number = str(tk.get("number", "")).strip()
            if not ticket_number or ticket_number in existing_ticket_numbers:
                summary["skipped_existing"] += 1
                continue

            text = _ticket_text(tk)
            if not text:
                summary["skipped_low_score"] += 1
                continue

            tfidf_matches = servicenow_resolution_kb.find_similar(text, top_n=10)
            if not tfidf_matches:
                summary["skipped_low_score"] += 1
                continue

            blended = psld_semantic_engine.blended_kb_matches(text, tfidf_matches)
            if not blended:
                summary["skipped_low_score"] += 1
                continue

            best_entry, best_score, breakdown = blended[0]
            if best_score < MIN_SUGGESTION_SCORE:
                summary["skipped_low_score"] += 1
                continue

            rows.append({
                "id": str(uuid.uuid4()),
                "ticket_number": ticket_number,
                "ticket_text": text,
                "source": source,
                "entry_id": best_entry["id"],
                "entry_title": best_entry["title"],
                "score": round(float(best_score), 4),
                "breakdown": {k: round(float(v), 4) for k, v in breakdown.items()},
                "status": "pending",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "reviewed_by": None,
                "reviewed_at": None,
                "review_note": None,
            })
            existing_ticket_numbers.add(ticket_number)
            summary["new_pending"] += 1

        _save(rows)

    return summary


def list_pending() -> List[Dict[str, Any]]:
    """Pending double-check suggestions, highest confidence first."""
    rows = [r for r in _load() if r.get("status") == "pending"]
    return sorted(rows, key=lambda r: r.get("score", 0), reverse=True)


def list_history(limit: int = 50) -> List[Dict[str, Any]]:
    """Already-reviewed (approved/rejected) items, most recently reviewed first."""
    rows = [r for r in _load() if r.get("status") != "pending"]
    return sorted(rows, key=lambda r: r.get("reviewed_at") or "", reverse=True)[:limit]


def queue_stats() -> Dict[str, Any]:
    rows = _load()
    return {
        "pending": sum(1 for r in rows if r.get("status") == "pending"),
        "approved": sum(1 for r in rows if r.get("status") == "approved"),
        "rejected": sum(1 for r in rows if r.get("status") == "rejected"),
        "total": len(rows),
    }


def approve_review(review_id: str, reviewer_cws: str) -> Dict[str, Any]:
    """
    A Parts Reviewer confirms the AI's guess was correct. This is what
    actually turns the guess into self-learning feedback: it calls
    psld_semantic_engine.record_feedback() exactly as if the reviewer had
    clicked "Confirm this match" live on the Analyze tab — feeding both
    the semantic feedback-boost and (once enough accumulates) the local
    neural classifier's training set.
    """
    with _LOCK:
        rows = _load()
        for r in rows:
            if r["id"] == review_id and r.get("status") == "pending":
                r["status"] = "approved"
                r["reviewed_by"] = reviewer_cws
                r["reviewed_at"] = datetime.now().isoformat(timespec="seconds")
                _save(rows)
                psld_semantic_engine.record_feedback(
                    ticket_text=r["ticket_text"],
                    entry_id=r["entry_id"],
                    entry_title=r["entry_title"],
                    confirmed_by=reviewer_cws,
                )
                return {"ok": True, "review": r}
    return {"ok": False, "reason": "not_found_or_already_reviewed"}


def reject_review(review_id: str, reviewer_cws: str, note: str = "") -> Dict[str, Any]:
    """
    A Parts Reviewer disagrees with the AI's guess — marked rejected for
    audit but deliberately NOT fed into record_feedback(), since doing so
    would reinforce a wrong pairing rather than a correct one.
    """
    with _LOCK:
        rows = _load()
        for r in rows:
            if r["id"] == review_id and r.get("status") == "pending":
                r["status"] = "rejected"
                r["reviewed_by"] = reviewer_cws
                r["reviewed_at"] = datetime.now().isoformat(timespec="seconds")
                r["review_note"] = (note or "").strip() or None
                _save(rows)
                return {"ok": True, "review": r}
    return {"ok": False, "reason": "not_found_or_already_reviewed"}
