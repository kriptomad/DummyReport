"""
auth/email_utils.py
====================
Minimal SMTP e-mail sending used by the admin-triggered "reset password"
flow (see ui/admin_tab.py). Uses only the stdlib `smtplib`/`email` so no new
dependency is required.

Configuration lives in app_settings (smtp_host, smtp_port, smtp_use_tls,
smtp_username, smtp_password, smtp_from_address, smtp_from_name) and is
editable from Administration -> App Settings. Until an admin fills in at
least the host and from-address, send_email() fails fast with a clear
message instead of raising, so callers can show the temporary password
on-screen as a fallback.
"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Tuple

from config import app_settings

# If set, this environment variable always wins over the plaintext
# `smtp_password` stored in app_settings — lets a deployer keep the real
# SMTP credential out of settings.json (which is just a JSON file on disk)
# by supplying it via the process environment / secrets manager instead.
SMTP_PASSWORD_ENV = "SMTP_PASSWORD"


def is_configured() -> bool:
    """Whether enough SMTP settings are present to attempt sending."""
    settings = app_settings.get_settings()
    return bool(settings.get("smtp_host") and settings.get("smtp_from_address"))


def send_email(to_address: str, subject: str, body: str) -> Tuple[bool, str]:
    """
    Sends a plain-text e-mail via the configured SMTP relay.
    Returns (success, message) — never raises.
    """
    if not to_address or not to_address.strip():
        return False, "No recipient e-mail address on file."

    settings = app_settings.get_settings()
    host = (settings.get("smtp_host") or "").strip()
    from_address = (settings.get("smtp_from_address") or "").strip()

    if not host or not from_address:
        return False, (
            "SMTP is not configured yet. Ask an administrator to fill in "
            "the SMTP settings under Administration -> App Settings."
        )

    port = int(settings.get("smtp_port") or 587)
    use_tls = bool(settings.get("smtp_use_tls", True))
    username = (settings.get("smtp_username") or "").strip()
    password = os.environ.get(SMTP_PASSWORD_ENV) or settings.get("smtp_password") or ""
    from_name = (settings.get("smtp_from_name") or "").strip()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_address}>" if from_name else from_address
    msg["To"] = to_address.strip()
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            if use_tls:
                server.starttls()
            if username:
                server.login(username, password)
            server.send_message(msg)
        return True, "E-mail sent."
    except Exception as exc:  # noqa: BLE001 - surface any SMTP failure to the caller
        return False, f"Failed to send e-mail: {exc}"
