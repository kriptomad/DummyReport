"""
troubleshooter/document_viewer.py
====================================
Renders PDF/DOCX/Excel/TXT resolution runbooks straight in the BROWSER —
no download step, and no dependency on any local filesystem path on the
analyst's machine — for the Resolution KB
(troubleshooter/servicenow_resolution_kb.py) and the "Double-Check"
review queue (troubleshooter/psld_review_queue.py).

Two viewing modes, both built from the file's raw BYTES only (never a
local path):
  - `render_inline()` — a compact, height-capped preview embedded
    directly in the Streamlit page (via st.components.v1.html(), which
    Streamlit renders through its own iframe `srcdoc` mechanism — this
    is NOT a user-initiated browser navigation, so it isn't affected by
    the "new tab" issue described below).
  - `build_new_tab_url()` — a real, same-origin HTTP URL (served by
    Streamlit's own static file route, see below) for a plain
    `<a target="_blank">` link that opens the SAME content as a full,
    standalone browser tab (better for long/dense documents — native
    PDF zoom/search/print, full-width tables, etc.).

WHY REAL URLS INSTEAD OF `data:` URIs FOR THE "OPEN IN NEW TAB" LINK
---------------------------------------------------------------------
The first version of this module used `data:` URIs for the "open in a
new tab" link. In practice, Microsoft Edge (and hardened/managed Chrome
profiles, common on corporate machines) blocks user-initiated
navigation to `data:` URIs outright ("This page has been blocked by
Microsoft Edge") — a security hardening feature, not a bug in this app,
but one that made the feature simply not work for real users. The fix:
Streamlit itself can serve real files over plain HTTP from a `static/`
folder next to the app (enabled via `server.enableStaticServing = true`
in `.streamlit/config.toml`, route `/app/static/<path>`). So instead of
inlining the whole file as a giant `data:` URI, this module writes the
bytes (or, for non-PDF types, the converted HTML) to a small per-view
cache file under `static/psld_view_cache/` with an unguessable UUID4
name, and returns a normal same-origin URL to that file — which every
browser (Edge included) opens exactly like any other webpage/PDF link.

WHY THIS APPROACH OTHERWISE (no extra native-desktop dependency)
------------------------------------------------------------------
- .pdf: browsers already know how to render PDF bytes natively.
- .docx: converted to HTML via `mammoth` (pure-Python docx->HTML —
  chosen over heavier tools to avoid the Windows MAX_PATH dependency
  issue documented in psld_semantic_engine.py). Falls back to plain
  extracted text if mammoth isn't available or fails.
- .doc (legacy binary Word): no reliable pure-Python HTML conversion
  exists; reports "not supported inline" and points at download/open
  locally instead.
- .xlsx/.xls: read via `pandas`/`openpyxl` (already project
  dependencies) and rendered as one styled HTML table per sheet — no
  Excel installation needed on the viewer's machine.
- .txt: decoded with a fallback encoding chain (utf-8-sig -> utf-8 ->
  cp1252 -> latin-1, first one that doesn't raise) and shown
  preformatted.

SECURITY NOTE on the static cache: files under `static/psld_view_cache/`
are served by Streamlit's raw static-file route, which does NOT go
through this app's own login/session check — anyone with the exact
cached URL could fetch that one file without logging in. This is
mitigated by (a) filenames being random UUID4s (128 bits of entropy —
not guessable/enumerable), (b) files never being linked from anywhere
except a logged-in user's own current page, and (c) `cleanup_stale_
cache()` deleting cached files after a short TTL so old links stop
working. Acceptable for an internal team tool; revisit if this app is
ever exposed outside the corporate network.
"""
from __future__ import annotations

import base64
import html
import io
import logging
import time
import uuid
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import mammoth
    MAMMOTH_AVAILABLE = True
except ImportError:  # pragma: no cover
    MAMMOTH_AVAILABLE = False

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:  # pragma: no cover
    PANDAS_AVAILABLE = False

VIEWABLE_EXTENSIONS = (".pdf", ".docx", ".xlsx", ".xls", ".txt")

# Per-sheet row cap for Excel previews — big spreadsheets rendered as a
# single giant HTML table get slow/unwieldy in a browser tab; truncate
# and say so rather than hang the page.
MAX_EXCEL_ROWS_PREVIEWED = 500

_TEXT_DECODE_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")

# Streamlit's built-in static file route serves whatever sits in
# <project root>/static/ at /app/static/<relpath> when
# server.enableStaticServing = true (set in .streamlit/config.toml).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STATIC_CACHE_DIR = _PROJECT_ROOT / "static" / "psld_view_cache"
_STATIC_CACHE_TTL_SECONDS = 24 * 60 * 60  # 1 day — plenty for a single viewing session


def can_view_inline(filename: str) -> bool:
    """Whether a browser-based preview can be produced for this file
    type — used by the UI to decide whether to show "👁️ View" /
    "🔗 Open in new tab" buttons at all."""
    return Path(filename or "").suffix.lower() in VIEWABLE_EXTENSIONS


def cleanup_stale_cache(max_age_seconds: int = _STATIC_CACHE_TTL_SECONDS) -> int:
    """Deletes cached view files older than `max_age_seconds`. Called
    opportunistically every time a new file is cached (see
    _write_static_cache_file()) so the cache never grows unbounded —
    no separate scheduled job needed. Returns how many files were
    removed."""
    if not _STATIC_CACHE_DIR.exists():
        return 0
    removed = 0
    cutoff = time.time() - max_age_seconds
    for f in _STATIC_CACHE_DIR.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def _write_static_cache_file(content: bytes, suffix: str) -> str:
    """Writes `content` to a new UUID4-named file under the static view
    cache and returns the same-origin URL to fetch it (e.g.
    "/app/static/psld_view_cache/<uuid>.pdf"). Opportunistically prunes
    old cached files first."""
    _STATIC_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_stale_cache()
    filename = f"{uuid.uuid4().hex}{suffix}"
    (_STATIC_CACHE_DIR / filename).write_bytes(content)
    return f"/app/static/psld_view_cache/{filename}"


def _decode_text(file_bytes: bytes) -> str:
    for enc in _TEXT_DECODE_ENCODINGS:
        try:
            return file_bytes.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return file_bytes.decode("utf-8", errors="replace")


def _docx_body_html(file_bytes: bytes, filename: str) -> str:
    """Returns just the inner body HTML for a DOCX (mammoth conversion,
    falling back to preformatted plain text) — reused by both the
    inline preview and the standalone new-tab page."""
    if MAMMOTH_AVAILABLE:
        try:
            result = mammoth.convert_to_html(io.BytesIO(file_bytes))
            return result.value
        except Exception:
            logger.exception("document_viewer: mammoth conversion failed for %s, falling back to plain text", filename)

    from troubleshooter import document_extractor
    try:
        text = document_extractor.extract_text(file_bytes, filename)
    except document_extractor.ExtractionError as e:
        return f'<div style="color:#c00;">⚠️ {html.escape(str(e))}</div>'
    escaped = html.escape(text) if text else "(no extractable text)"
    return f'<pre style="white-space:pre-wrap;font-family:Consolas,monospace;">{escaped}</pre>'


def _excel_body_html(file_bytes: bytes, filename: str) -> str:
    """Returns inner body HTML for an Excel file — one styled HTML
    table per sheet, truncated to MAX_EXCEL_ROWS_PREVIEWED rows each
    (with a note when truncated) so a huge workbook can't hang the
    page."""
    if not PANDAS_AVAILABLE:
        return '<div style="color:#c00;">⚠️ pandas isn\'t available — cannot preview Excel files.</div>'
    try:
        sheets = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None)
    except Exception as e:
        logger.exception("document_viewer: failed to read Excel file %s", filename)
        return f'<div style="color:#c00;">⚠️ Could not read this Excel file: {html.escape(str(e))}</div>'

    parts = []
    for sheet_name, df in sheets.items():
        total_rows = len(df)
        truncated = total_rows > MAX_EXCEL_ROWS_PREVIEWED
        shown_df = df.head(MAX_EXCEL_ROWS_PREVIEWED)
        table_html = shown_df.to_html(index=False, na_rep="", border=0, classes="dv-table", escape=True)
        note = (
            f'<p style="color:#888;font-size:0.85em;">Showing first {MAX_EXCEL_ROWS_PREVIEWED} of {total_rows} rows.</p>'
            if truncated else ""
        )
        parts.append(
            f'<h3 style="margin-top:28px;">📄 {html.escape(str(sheet_name))}</h3>{note}{table_html}'
        )
    return (
        "<style>.dv-table{border-collapse:collapse;width:100%;font-size:0.9em;}"
        ".dv-table th{background:#eee;text-align:left;padding:6px 10px;border-bottom:2px solid #ccc;position:sticky;top:0;}"
        ".dv-table td{padding:6px 10px;border-bottom:1px solid #eee;}"
        ".dv-table tr:nth-child(even){background:#fafafa;}</style>"
        + "".join(parts)
    )


def _txt_body_html(file_bytes: bytes) -> str:
    text = _decode_text(file_bytes)
    escaped = html.escape(text) if text else "(empty file)"
    return f'<pre style="white-space:pre-wrap;font-family:Consolas,monospace;">{escaped}</pre>'


def _body_html_for(file_bytes: bytes, filename: str) -> Tuple[Optional[str], Optional[str]]:
    """Dispatches to the right converter for non-PDF types (PDF is
    handled separately since it isn't converted to HTML). Returns
    (body_html, error_reason) — exactly one non-None."""
    ext = Path(filename or "").suffix.lower()
    try:
        if ext == ".docx":
            return _docx_body_html(file_bytes, filename), None
        if ext in (".xlsx", ".xls"):
            return _excel_body_html(file_bytes, filename), None
        if ext == ".txt":
            return _txt_body_html(file_bytes), None
    except Exception as e:
        logger.exception("document_viewer: failed to build preview for %s", filename)
        return None, f"Could not preview this file: {e}"
    return None, f"Preview isn't supported for '{ext or '?'}' files — use download instead."


def render_inline(file_bytes: bytes, filename: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (html, error_reason) — exactly one is non-None. `html` (when
    present) is meant to be passed straight to
    st.components.v1.html(html, height=..., scrolling=True) — Streamlit
    renders that through its own component iframe (`srcdoc`), which
    isn't a user-navigated `data:`/URL load, so it isn't affected by
    the Edge "blocked data: URI" issue that build_new_tab_url() works
    around. `error_reason` (when present) is a short, user-facing
    explanation of why no inline preview is available for this file
    (e.g. legacy .doc, corrupt file).
    """
    ext = Path(filename or "").suffix.lower()
    if ext == ".pdf":
        try:
            b64 = base64.b64encode(file_bytes).decode("ascii")
            return (
                f'<iframe src="data:application/pdf;base64,{b64}" '
                f'width="100%" height="900" style="border:1px solid #444;border-radius:6px;">'
                f"</iframe>"
            ), None
        except Exception as e:
            logger.exception("document_viewer: PDF inline render failed for %s", filename)
            return None, f"Could not render this PDF inline: {e}"

    body_html, error_reason = _body_html_for(file_bytes, filename)
    if body_html is None:
        return None, error_reason
    return (
        '<div style="background:#ffffff;color:#111;padding:24px;'
        'border:1px solid #444;border-radius:6px;max-height:900px;'
        'overflow-y:auto;font-family:Calibri,Segoe UI,sans-serif;">'
        f"{body_html}</div>"
    ), None


def _standalone_html_page(body_html: str, filename: str) -> str:
    """Wraps `body_html` as a complete standalone HTML document (full
    <html>/<head>/<body>) — needed because the cached HTML file opened
    as its own browser tab has no surrounding Streamlit page to inherit
    styling/encoding from."""
    safe_title = html.escape(Path(filename or "document").stem)
    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        f"<title>{safe_title}</title>"
        "<style>body{background:#ffffff;color:#111;font-family:Calibri,'Segoe UI',sans-serif;"
        "max-width:1100px;margin:32px auto;padding:0 24px 64px;line-height:1.5;}"
        "img{max-width:100%;}</style></head>"
        f"<body>{body_html}</body></html>"
    )


def build_new_tab_url(file_bytes: bytes, filename: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Alternative to render_inline(): writes the file (PDF: as-is; DOCX/
    Excel/TXT: converted to a standalone HTML page) into Streamlit's
    static file cache and returns a real, same-origin URL meant to be
    opened in a BRAND NEW browser tab (via a plain <a target="_blank">
    link — see ui/psld_parts_tab.py's "🔗 Open in new tab" button).
    Deliberately NOT a `data:` URI — see the module docstring for why
    (Edge/managed-Chrome block those for user-initiated navigation).

    Why this exists as a SEPARATE option from the inline preview: the
    inline embedded-HTML preview is deliberately height-capped and sits
    inside Streamlit's own page chrome, which can feel cramped for a
    long/dense document or a wide spreadsheet. Opening in a full new
    tab instead:
      - for PDFs: hands the browser's own full-featured native PDF
        viewer the WHOLE window — proper zoom, search (Ctrl+F), page
        thumbnails, printing.
      - for DOCX/Excel/TXT: renders the same converted HTML as a
        complete, standalone, full-width page instead of a small
        scrolling box.
    Returns (url, error_reason) — exactly one is non-None.
    """
    ext = Path(filename or "").suffix.lower()
    if ext == ".pdf":
        try:
            return _write_static_cache_file(file_bytes, ".pdf"), None
        except Exception as e:
            logger.exception("document_viewer: PDF static cache write failed for %s", filename)
            return None, f"Could not prepare this PDF for a new tab: {e}"

    body_html, error_reason = _body_html_for(file_bytes, filename)
    if body_html is None:
        return None, error_reason
    try:
        page_html = _standalone_html_page(body_html, filename)
        return _write_static_cache_file(page_html.encode("utf-8"), ".html"), None
    except Exception as e:
        logger.exception("document_viewer: static cache write failed for %s", filename)
        return None, f"Could not prepare this file for a new tab: {e}"
