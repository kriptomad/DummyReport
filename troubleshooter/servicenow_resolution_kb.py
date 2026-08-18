"""
troubleshooter/servicenow_resolution_kb.py
============================================
EXPERIMENTAL (Lab Test tab) — a curated "problem -> resolution" knowledge
base for ServiceNow tickets, separate from the existing Troubleshooter KB
(troubleshooter/loader.py, which is about DB/shipment-audit ERR_MSG
patterns). This one is fed manually by the support team with:
  - a short title/description
  - a long description
  - step-by-step resolution instructions (OPTIONAL — see below)
  - an attached runbook file (PDF, DOCX, or legacy DOC)

WHY "steps" IS NOW OPTIONAL
-----------------------------
Most real-world runbooks the team already has ARE the step-by-step
resolution — re-typing them into the "steps" field would just duplicate
what's already in the PDF/DOCX. So when an attachment is provided,
troubleshooter/document_extractor.py pulls its full text out and
distills it into a handful of "key points" (a local, dependency-light
extractive summary — see that module's docstring for why this isn't a
real LLM call) automatically at upload time. Those key points — plus
the full extracted text — are also folded into the similarity-search
corpus, so matching a new ticket against this KB benefits from the
ENTIRE document's content, not just what a human typed into the title/
description fields.

A new incoming ServiceNow ticket's text is matched against this KB (and,
separately, against a list of already-fetched past ServiceNow tickets)
using the same local TF-IDF + cosine-similarity approach already used by
troubleshooter/local_intelligence.py for the main KB — no new ML
dependency needed, everything here runs 100% locally.

Data lives in `data/servicenow_resolution_kb.json` (gitignored — real
team problem/resolution data, not source). Attachments are stored under
`data/servicenow_kb_pdfs/<entry_id>_<original_filename>` (also
gitignored; the directory name is a historical holdover from when only
PDFs were supported — kept as-is to avoid a data migration).
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from troubleshooter import document_extractor

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
KB_PATH = DATA_DIR / "servicenow_resolution_kb.json"
PDF_DIR = DATA_DIR / "servicenow_kb_pdfs"

# Folder the team drops .docx/.doc/.pdf resolution runbooks into for
# one-click bulk import (see bulk_import_from_folder below). Lives at the
# project root, NOT under data/, so it's easy for non-technical team
# members to find and drop files into directly. Named "ResolutionDocs"
# rather than "Docs" specifically to avoid colliding with the existing
# (lowercase) docs/ folder — Windows filesystems are case-insensitive.
RESOLUTION_DOCS_INBOX = Path(__file__).resolve().parent.parent / "ResolutionDocs"
_SUPPORTED_INBOX_EXTENSIONS = (".docx", ".doc", ".pdf", ".xlsx", ".xls", ".txt")

_LOCK = threading.Lock()

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    SKLEARN_AVAILABLE = True
except ImportError:  # pragma: no cover - sklearn is already a project dependency
    SKLEARN_AVAILABLE = False


def _load() -> List[Dict[str, Any]]:
    if not KB_PATH.exists():
        return []
    try:
        return json.loads(KB_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(entries: List[Dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    KB_PATH.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")


def list_entries() -> List[Dict[str, Any]]:
    """Returns all resolution KB entries, most recently added first."""
    with _LOCK:
        entries = _load()
    return sorted(entries, key=lambda e: e.get("created_at", ""), reverse=True)


def get_entry(entry_id: str) -> Optional[Dict[str, Any]]:
    for e in list_entries():
        if e.get("id") == entry_id:
            return e
    return None


def add_entry(
    title: str,
    description_long: str,
    steps: str,
    created_by: str,
    pdf_bytes: Optional[bytes] = None,
    pdf_original_name: Optional[str] = None,
    source: str = "manual",
    category: Optional[str] = None,
    source_relpath: Optional[str] = None,
) -> Dict[str, Any]:
    """Adds a new problem -> resolution entry. If `pdf_bytes`/
    `pdf_original_name` are given (any of .pdf/.docx/.doc — the
    parameter names are historical, kept for backward compatibility),
    the file is saved under PDF_DIR AND its text is extracted +
    distilled into "key points" automatically (see
    troubleshooter/document_extractor.py). `steps` may be left blank
    when an attachment is provided — the attachment's own step-by-step
    content, once extracted, stands in for it. Raises ValueError if
    there's neither a manually-typed `steps` value NOR an attachment to
    extract steps from (an entry needs a resolution from SOME source).
    `source` is "manual" (typed in via the UI form, the default),
    "folder_import" (bulk-ingested from RESOLUTION_DOCS_INBOX via
    bulk_import_from_folder), or "docs_folder_import" (bulk-ingested
    from the team's real docs/ folder tree via
    bulk_import_from_docs_root) — used for dedup on repeated scans and
    shown in the UI so it's clear where an entry came from. `category`
    is an optional free-text grouping label (e.g. the top-level docs/
    subfolder an entry was imported from, like "CITRIX" or "Legacy Batch")
    used for KB filtering/organization. `source_relpath` is the
    original file's path relative to its import root — used (instead
    of bare filename) to dedupe docs/ folder imports where the same
    filename can legitimately appear under multiple category
    subfolders.
    """
    title = (title or "").strip()
    description_long = (description_long or "").strip()
    steps = (steps or "").strip()
    has_attachment = bool(pdf_bytes and pdf_original_name)
    if not title:
        raise ValueError("Title is required.")
    if not steps and not has_attachment:
        raise ValueError(
            "Provide either the step-by-step field or an attached "
            "runbook file (PDF/DOCX/DOC) — at least one resolution "
            "source is required."
        )

    entry_id = uuid.uuid4().hex[:12]
    pdf_filename = None
    extracted_text = ""
    key_points: List[str] = []
    extraction_error: Optional[str] = None

    if has_attachment:
        PDF_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", pdf_original_name)
        pdf_filename = f"{entry_id}_{safe_name}"
        (PDF_DIR / pdf_filename).write_bytes(pdf_bytes)

        try:
            extracted_text = document_extractor.extract_text(pdf_bytes, pdf_original_name)
            key_points = document_extractor.extract_key_points(extracted_text)
        except document_extractor.ExtractionError as e:
            # Don't block saving the entry over an extraction failure —
            # the file itself is still stored and downloadable, and the
            # user can still see/fix the reason from the KB list. It
            # just won't benefit from auto key-points/search-boost until
            # re-uploaded in a supported way.
            extraction_error = str(e)

    entry = {
        "id": entry_id,
        "title": title,
        "description_long": description_long,
        "steps": steps,
        "pdf_filename": pdf_filename,
        "pdf_original_name": pdf_original_name if pdf_filename else None,
        "extracted_text": extracted_text,
        "key_points": key_points,
        "extraction_error": extraction_error,
        "source": source,
        "category": (category or "").strip() or None,
        "source_relpath": source_relpath,
        "created_by": created_by,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    with _LOCK:
        entries = _load()
        entries.append(entry)
        _save(entries)
    return entry


def delete_entry(entry_id: str) -> bool:
    """Removes an entry (and its PDF file, if any). Returns False if the
    id wasn't found."""
    with _LOCK:
        entries = _load()
        remaining = [e for e in entries if e.get("id") != entry_id]
        if len(remaining) == len(entries):
            return False
        removed = next((e for e in entries if e.get("id") == entry_id), None)
        _save(remaining)
    if removed and removed.get("pdf_filename"):
        try:
            (PDF_DIR / removed["pdf_filename"]).unlink(missing_ok=True)
        except Exception:
            pass
    return True


def pdf_path_for(entry: Dict[str, Any]) -> Optional[Path]:
    """Returns the on-disk Path to `entry`'s attached runbook file (PDF,
    DOCX, or DOC), or None if it has none (or the file is somehow
    missing). Name kept for backward compatibility even though it's no
    longer PDF-only."""
    if not entry.get("pdf_filename"):
        return None
    path = PDF_DIR / entry["pdf_filename"]
    return path if path.exists() else None


def _title_from_filename(filename: str) -> str:
    """Turns a dropped-in file's name into a reasonable KB entry title,
    e.g. "DEMOJCL1_Fix_ABEND_S000.docx" -> "DEMOJCL1 Fix ABEND S000"."""
    stem = Path(filename).stem
    cleaned = re.sub(r"[_\-]+", " ", stem).strip()
    return cleaned or stem


def scan_resolution_docs_inbox(folder: Optional[Path] = None) -> List[Path]:
    """Lists supported files (.docx/.doc/.pdf) currently sitting in the
    ResolutionDocs/ inbox folder, ready to be bulk-imported. Returns an
    empty list if the folder doesn't exist yet (nothing dropped in)."""
    folder = folder or RESOLUTION_DOCS_INBOX
    if not folder.exists():
        return []
    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in _SUPPORTED_INBOX_EXTENSIONS
    )


def pending_inbox_files(folder: Optional[Path] = None) -> List[Path]:
    """Files in the ResolutionDocs/ inbox that have NOT been imported yet
    (i.e. what bulk_import_from_folder() would actually create entries
    for on the next run) — used by the UI to show "N new files found"
    and to enable/disable the scan button without doing a dry-run import."""
    already = _already_imported_filenames()
    return [p for p in scan_resolution_docs_inbox(folder) if p.name.strip().lower() not in already]


def _already_imported_filenames() -> set:
    """Original filenames of every entry previously created via
    bulk_import_from_folder() — used to skip files already imported on
    repeated scans (adding more files to the folder over time and
    re-running the import shouldn't duplicate entries for ones already
    ingested)."""
    return {
        (e.get("pdf_original_name") or "").strip().lower()
        for e in _load()
        if e.get("source") == "folder_import" and e.get("pdf_original_name")
    }


def bulk_import_from_folder(folder: Optional[Path] = None, created_by: str = "SYSTEM") -> Dict[str, Any]:
    """
    Scans `folder` (default: ResolutionDocs/ at the project root) for
    .docx/.doc/.pdf files and creates one Resolution KB entry per file
    NOT already imported before (matched by original filename — safe to
    re-run after adding more files). Title is derived from the filename;
    description/steps are left blank on purpose — the attachment's own
    extracted text + auto key-points (via document_extractor, same as a
    manual single upload) become the entry's searchable resolution
    content. This is the "deep-learn the whole folder at once" bulk path
    for teams handing over a large batch of existing runbooks. Returns
    {"created": int, "skipped_existing": int, "failed": [(filename, reason), ...]}.
    """
    files = scan_resolution_docs_inbox(folder)
    already = _already_imported_filenames()
    created = 0
    skipped_existing = 0
    failed: List[Tuple[str, str]] = []
    for path in files:
        if path.name.strip().lower() in already:
            skipped_existing += 1
            continue
        try:
            file_bytes = path.read_bytes()
            add_entry(
                title=_title_from_filename(path.name),
                description_long="", steps="", created_by=created_by,
                pdf_bytes=file_bytes, pdf_original_name=path.name,
                source="folder_import",
            )
            created += 1
        except Exception as e:
            failed.append((path.name, str(e)))
    return {"created": created, "skipped_existing": skipped_existing, "failed": failed}


# Root of the team's REAL, already-existing runbook archive — the
# project's own docs/ folder, organized into many category subfolders
# (CITRIX, EDI856, Legacy Batch, Protection Production Level Data/<year>,
# etc.), each containing real .docx/.pdf/.xlsx/.txt resolution documents
# named after the incident they resolved (e.g. "INC2812345 ...docx").
# Unlike RESOLUTION_DOCS_INBOX (an empty inbox teams drop NEW files
# into), this is a one-time (repeatable) bulk-ingest of the team's
# EXISTING archive — see bulk_import_from_docs_root() below.
DOCS_KB_ROOT = Path(__file__).resolve().parent.parent / "docs"
_DOCS_KB_EXTENSIONS = (".docx", ".pdf", ".xlsx", ".xls", ".txt")


def scan_docs_kb_root(root: Optional[Path] = None) -> List[Path]:
    """Recursively lists every .docx/.pdf/.xlsx/.xls/.txt file anywhere
    under `root` (default: DOCS_KB_ROOT), across all category
    subfolders — the candidate pool for bulk_import_from_docs_root()."""
    root = root or DOCS_KB_ROOT
    if not root.exists():
        return []
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in _DOCS_KB_EXTENSIONS
    )


def _already_imported_docs_relpaths(root: Path) -> set:
    """Relative paths (to `root`) of every entry previously imported via
    bulk_import_from_docs_root() — dedup key is the relative path
    (not just filename), since the same filename can legitimately
    appear under multiple category subfolders (e.g. different years)."""
    return {
        (e.get("source_relpath") or "").strip().lower()
        for e in _load()
        if e.get("source") == "docs_folder_import" and e.get("source_relpath")
    }


def list_categories() -> List[str]:
    """Distinct non-empty `category` values across the KB, sorted — used
    to populate a category filter in the Resolution KB UI tab."""
    entries = _load()
    return sorted({e["category"] for e in entries if e.get("category")})


def bulk_import_from_docs_root(
    root: Optional[Path] = None,
    created_by: str = "SYSTEM",
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Recursively imports every .docx/.pdf file under `root` (default:
    the project's own docs/ folder tree) into the Resolution KB — the
    "ingest the team's whole existing runbook archive" bulk path,
    distinct from bulk_import_from_folder() (which only watches the
    empty ResolutionDocs/ inbox for newly-dropped files).

    Each file's immediate top-level subfolder under `root` (e.g.
    "CITRIX", "Legacy Batch", "Protection Production Level Data") becomes
    that entry's `category`, so the KB stays organized/filterable even
    at hundreds of imported files. Files sitting directly in `root`
    (no subfolder) get category=None. Safe to re-run repeatedly as more
    files are added to the archive over time — already-imported files
    (tracked by path relative to `root`, not just filename) are skipped.

    `limit` caps how many NEW files get imported in this call (useful
    for chunking an initial huge backlog import across several button
    clicks/admin actions instead of blocking the UI for a long time in
    one go); default None imports everything pending.

    Returns {"scanned": int, "created": int, "skipped_existing": int,
    "failed": [(relpath, reason), ...]}.
    """
    root = root or DOCS_KB_ROOT
    files = scan_docs_kb_root(root)
    already = _already_imported_docs_relpaths(root)
    scanned = len(files)
    created = 0
    skipped_existing = 0
    failed: List[Tuple[str, str]] = []

    for path in files:
        relpath = str(path.relative_to(root))
        if relpath.strip().lower() in already:
            skipped_existing += 1
            continue
        if limit is not None and created >= limit:
            break
        try:
            parts = path.relative_to(root).parts
            category = parts[0] if len(parts) > 1 else None
            file_bytes = path.read_bytes()
            add_entry(
                title=_title_from_filename(path.name),
                description_long="", steps="", created_by=created_by,
                pdf_bytes=file_bytes, pdf_original_name=path.name,
                source="docs_folder_import",
                category=category,
                source_relpath=relpath,
            )
            created += 1
        except Exception as e:
            failed.append((relpath, str(e)))

    return {"scanned": scanned, "created": created, "skipped_existing": skipped_existing, "failed": failed}


def reprocess_all_entries() -> Dict[str, Any]:
    """
    "Force deep-learn" over the ENTIRE existing KB: re-runs
    document_extractor over every entry's stored attachment (if any),
    even ones already successfully processed before, and overwrites
    extracted_text/key_points/extraction_error in place. Doesn't create
    or delete any entries.

    Why this exists: unlike bulk_import_from_folder() (which only
    ingests files the team hasn't imported yet), this is for when the
    extraction/summary logic itself improves, or the admin just wants to
    be sure every attachment's content is being fully leveraged for
    similarity search right now — deliberately allowed to be slow (it
    re-reads and re-parses every attachment on disk) in exchange for the
    most complete/up-to-date key points possible. Wired to the "Force
    deep-learn" button in the admin AI Control Center.

    Returns {"processed": int, "updated": int, "failed": [{"title", "reason"}]}.
    """
    with _LOCK:
        entries = _load()
        processed = 0
        updated = 0
        failed: List[Dict[str, str]] = []
        for e in entries:
            if not e.get("pdf_filename"):
                continue
            path = PDF_DIR / e["pdf_filename"]
            if not path.exists():
                failed.append({"title": e.get("title", "?"), "reason": "attachment file missing on disk"})
                continue
            processed += 1
            try:
                raw = path.read_bytes()
                original_name = e.get("pdf_original_name") or e["pdf_filename"]
                text = document_extractor.extract_text(raw, original_name)
                points = document_extractor.extract_key_points(text)
                e["extracted_text"] = text
                e["key_points"] = points
                e["extraction_error"] = None
                e["reprocessed_at"] = datetime.now().isoformat(timespec="seconds")
                updated += 1
            except document_extractor.ExtractionError as ex:
                e["extraction_error"] = str(ex)
                failed.append({"title": e.get("title", "?"), "reason": str(ex)})
        _save(entries)
    return {"processed": processed, "updated": updated, "failed": failed}


def kb_stats() -> Dict[str, Any]:
    """Read-only summary of the Resolution KB for the admin AI Control
    Center dashboard: total entries, breakdown by source, how many have
    an attachment/extracted key points, and the most recent activity
    timestamp (for a "last background activity" indicator)."""
    entries = _load()
    manual = sum(1 for e in entries if e.get("source", "manual") == "manual")
    folder_import = sum(1 for e in entries if e.get("source") == "folder_import")
    docs_folder_import = sum(1 for e in entries if e.get("source") == "docs_folder_import")
    with_attachment = sum(1 for e in entries if e.get("pdf_filename"))
    with_key_points = sum(1 for e in entries if e.get("key_points"))
    categories = len({e["category"] for e in entries if e.get("category")})
    timestamps = [e.get("reprocessed_at") or e.get("created_at") for e in entries if e.get("reprocessed_at") or e.get("created_at")]
    last_activity = max(timestamps) if timestamps else None
    return {
        "total": len(entries),
        "manual": manual,
        "folder_import": folder_import,
        "docs_folder_import": docs_folder_import,
        "with_attachment": with_attachment,
        "with_key_points": with_key_points,
        "categories": categories,
        "last_activity": last_activity,
    }





def find_entries_mentioning_filename(text: str) -> List[Dict[str, Any]]:
    """
    Scans `text` (e.g. a ServiceNow ticket's Resolution Notes) for a
    mention of any KB entry's attached file name — a certain, non-fuzzy
    shortcut for "this ticket was resolved using THIS EXACT runbook",
    meant for the workflow where the team starts writing the resolution
    doc's filename directly into ServiceNow's Resolution Notes field for
    faster self-learning. Matches on either the full original filename or
    its stem (without extension), case-insensitively, requiring at least
    4 characters to avoid matching on trivially short/generic stems.
    Returns every entry that matches (there could be more than one if
    multiple attachments share a very generic name).
    """
    text_low = (text or "").strip().lower()
    if not text_low:
        return []
    matches = []
    for e in list_entries():
        original_name = (e.get("pdf_original_name") or "").strip()
        if not original_name:
            continue
        stem = Path(original_name).stem.lower()
        if len(stem) >= 4 and (original_name.lower() in text_low or stem in text_low):
            matches.append(e)
    return matches


# Alias with an accurate, non-PDF-specific name for new call sites.
attachment_path_for = pdf_path_for


def _corpus_text(entry: Dict[str, Any]) -> str:
    return " ".join([
        str(entry.get("title", "")),
        str(entry.get("description_long", "")),
        str(entry.get("steps", "")),
        # Folding in the full extracted attachment text (and its
        # distilled key points once more, for a small TF-IDF weight
        # boost) means matching benefits from everything in the
        # uploaded runbook, not just what a human manually typed above.
        str(entry.get("extracted_text", "")),
        " ".join(entry.get("key_points", []) or []),
    ])


def corpus_text(entry: Dict[str, Any]) -> str:
    """Public alias of _corpus_text() — used by troubleshooter/ai_core.py
    to build the whole-KB embedding space for its clustering layer
    without duplicating the "what text represents this entry" logic."""
    return _corpus_text(entry)


def find_similar(query_text: str, top_n: int = 5) -> List[Tuple[Dict[str, Any], float]]:
    """
    Returns up to `top_n` (entry, score in [0,1]) pairs ranked by TF-IDF
    cosine similarity of `query_text` (a new ticket's title+description)
    against each resolution KB entry's combined title/description/steps
    text — the same approach as the main Troubleshooter's Internal AI
    (troubleshooter/local_intelligence.py). Falls back to a crude
    keyword-overlap ratio if scikit-learn isn't available.
    """
    entries = list_entries()
    if not entries or not (query_text or "").strip():
        return []

    if SKLEARN_AVAILABLE:
        texts = [_corpus_text(e) for e in entries] + [query_text]
        vectorizer = TfidfVectorizer(max_features=2000, ngram_range=(1, 2), stop_words="english")
        try:
            matrix = vectorizer.fit_transform(texts)
        except ValueError:
            return []
        sims = cosine_similarity(matrix[-1], matrix[:-1]).flatten()
        ranked = sorted(zip(entries, sims), key=lambda x: x[1], reverse=True)
        return [(e, float(s)) for e, s in ranked[:top_n]]

    query_words = set(re.findall(r"\w+", query_text.lower()))
    scored = []
    for e in entries:
        words = set(re.findall(r"\w+", _corpus_text(e).lower()))
        overlap = len(query_words & words) / max(1, len(query_words | words))
        scored.append((e, overlap))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_n]


def find_similar_tickets(
    query_text: str,
    tickets: List[Dict[str, Any]],
    top_n: int = 5,
) -> List[Tuple[Dict[str, Any], float]]:
    """
    Same idea as find_similar(), but against a list of raw ServiceNow
    ticket dicts (as returned by integrations.servicenow_poc's fetch
    functions, e.g. already-fetched Resolved/Closed/Cancelled tickets)
    instead of the curated resolution KB — surfaces "this looks like
    ticket INC0012345, already Resolved" even before/without a formal
    resolution KB entry existing for that kind of problem.
    """
    if not tickets or not (query_text or "").strip():
        return []

    def ticket_text(tk: Dict[str, Any]) -> str:
        return " ".join([str(tk.get("short_description", "")), str(tk.get("description", ""))])

    texts = [ticket_text(tk) for tk in tickets]
    if SKLEARN_AVAILABLE:
        all_texts = texts + [query_text]
        vectorizer = TfidfVectorizer(max_features=2000, ngram_range=(1, 2), stop_words="english")
        try:
            matrix = vectorizer.fit_transform(all_texts)
        except ValueError:
            return []
        sims = cosine_similarity(matrix[-1], matrix[:-1]).flatten()
        ranked = sorted(zip(tickets, sims), key=lambda x: x[1], reverse=True)
        return [(tk, float(s)) for tk, s in ranked[:top_n]]

    query_words = set(re.findall(r"\w+", query_text.lower()))
    scored = []
    for tk, txt in zip(tickets, texts):
        words = set(re.findall(r"\w+", txt.lower()))
        overlap = len(query_words & words) / max(1, len(query_words | words))
        scored.append((tk, overlap))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_n]
