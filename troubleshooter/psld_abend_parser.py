"""
troubleshooter/psld_abend_parser.py
=======================================
EXPERIMENTAL (Lab Test tab -> "📦 PSLD - Parts" sub-menu -> "🚨 ABEND").

Lightweight, regex-based "is this ticket an ABEND, and if so what
program caused it?" detector — this is the "intelligence" the Excel/
Mock Data importer runs over every row so the team doesn't have to
manually flag which ServiceNow tickets are ABENDs.

Demo example used to illustrate the parser format:

    Incident DEMO42 ABEND=S000 U0004 DEMOJCL1(JOB12345)
    JCL=OPS.DEMOJCL1 DEMO-SCHED-01 - 1 Jul 2026 05:01:30

Here:
  - "ABEND=S000 U0004"     -> the abend code (system code + optional
                              user completion code).
  - "DEMOJCL1(JOB12345)"   -> the job name and its run/job number.
  - "JCL=OPS.DEMOJCL1"       -> the JCL library.member reference — the
                              PROGRAM that reported the abend is the
                              part after the last '.' (or the whole
                              token if there's no '.').

Resolution notes often instead (or additionally) name the program as
free text, e.g. "JOB DEMOPGM1" — used as a fallback when the short
description has no JCL= reference.

Neither pattern is guaranteed to be present on every real ticket (the
scheduler alert format can vary) — when NEITHER yields a program, the
caller (troubleshooter/psld_abend_registry.py's ingest_ticket_for_abend)
still registers the ABEND, just with an empty program, so it shows up
in the "⏳ Abend Pendente" list for an analyst to fill in by hand.
"""
from __future__ import annotations

import re
from typing import Optional, TypedDict

# Matches "ABEND=S000" or "ABEND: S000" or "ABEND S000", optionally
# followed by a user completion code like "U0004" on some tickets.
_ABEND_CODE_RE = re.compile(
    r"ABEND[=:\s]+([A-Z]\d[A-Z0-9]{0,3})(?:\s+(U\d{3,4}))?",
    re.IGNORECASE,
)

# A bare "ABEND" mention (case-insensitive) — used as a fallback signal
# when the code itself couldn't be parsed out, so the ticket still gets
# flagged for the "Abend Pendente" bucket instead of being silently
# ignored.
_ABEND_WORD_RE = re.compile(r"\bABEND\b", re.IGNORECASE)

# "JCL=OPS.DEMOJCL1" -> captures "B.H1CB9999"; program is whatever comes
# after the last '.' (or the whole thing if there's no '.').
_JCL_RE = re.compile(r"JCL[=:]\s*([A-Za-z0-9_.\-]+)", re.IGNORECASE)

# The job name + run number in parens right after it, e.g.
# "DEMOJCL1(JOB12345)".
_JOB_PAREN_RE = re.compile(r"([A-Za-z0-9]{4,10})\((JOB\d+)\)", re.IGNORECASE)

# Fallback: resolution notes calling out the program as free text,
# e.g. "JOB DEMOPGM1" (requires whitespace between "JOB" and the code,
# so it doesn't accidentally match a job-number token like "JOB12345").
_RESOLUTION_JOB_RE = re.compile(r"\bJOB\s+([A-Za-z0-9]{4,10})\b", re.IGNORECASE)


class AbendInfo(TypedDict):
    is_abend: bool
    abend_code: str
    program: str
    program_source: Optional[str]  # "jcl" | "resolution_notes" | None
    job_number: str


def is_abend_text(text: str) -> bool:
    """Quick yes/no: does this text look like it's reporting an ABEND?"""
    text = text or ""
    return bool(_ABEND_CODE_RE.search(text) or _ABEND_WORD_RE.search(text))


def extract_abend_info(short_description: str, resolution_notes: str = "") -> AbendInfo:
    """
    Parses a ticket's short description (+ optional resolution notes)
    for ABEND details. Never raises — returns `is_abend=False` and
    empty fields if the text doesn't look like an ABEND at all.
    """
    short_description = short_description or ""
    resolution_notes = resolution_notes or ""

    code_match = _ABEND_CODE_RE.search(short_description) or _ABEND_CODE_RE.search(resolution_notes)
    abend_code = ""
    if code_match:
        abend_code = code_match.group(1).strip().upper()
        if code_match.group(2):
            abend_code = f"{abend_code} {code_match.group(2).strip().upper()}"

    if not (code_match or _ABEND_WORD_RE.search(short_description) or _ABEND_WORD_RE.search(resolution_notes)):
        return {
            "is_abend": False, "abend_code": "", "program": "",
            "program_source": None, "job_number": "",
        }

    program = ""
    program_source: Optional[str] = None

    jcl_match = _JCL_RE.search(short_description) or _JCL_RE.search(resolution_notes)
    if jcl_match:
        raw = jcl_match.group(1)
        program = raw.rsplit(".", 1)[-1].strip().upper()
        program_source = "jcl"

    if not program:
        res_match = _RESOLUTION_JOB_RE.search(resolution_notes)
        if res_match:
            program = res_match.group(1).strip().upper()
            program_source = "resolution_notes"

    job_number = ""
    job_match = _JOB_PAREN_RE.search(short_description) or _JOB_PAREN_RE.search(resolution_notes)
    if job_match:
        job_number = job_match.group(2).strip().upper()

    return {
        "is_abend": True,
        "abend_code": abend_code,
        "program": program,
        "program_source": program_source,
        "job_number": job_number,
    }
