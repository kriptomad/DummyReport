"""
Application-wide settings persisted to data/app_settings.json.
"""
from __future__ import annotations

import os
from typing import Any, Dict

from utils.safe_json import json_transaction, load_json as _safe_load_json, save_json as _safe_save_json

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
APP_SETTINGS_PATH = os.path.join(DATA_DIR, "app_settings.json")
_settings_cache: Dict[str, Any] | None = None
_settings_cache_mtime: float | None = None

DEFAULT_SETTINGS: Dict[str, Any] = {
    "app_name": "ILT Troubleshooter",
    "support_contact_cws": "DEMOADMIN",
    "maintenance_mode_enabled": False,
    "maintenance_mode_message": "",
    "kb_freshness_yellow_days": 90,
    "kb_freshness_red_days": 365,
    "max_shipment_history": 50,
    "max_query_history": 50,
    "enable_ai_query_builder": False,
    "enable_messaging": True,
    "enable_broadcast": True,
    "enable_copilot_chat": True,
    "enable_schema_manager": True,
    "default_language": "en",
    "session_timeout_minutes": 480,
    "require_admin_approval_for_new_users": True,
    # SMTP relay used to e-mail users a temporary password when an admin
    # resets their account (see auth/email_utils.py, ui/admin_tab.py).
    # Left blank by default — must be filled in under Administration ->
    # App Settings before password-reset e-mails can actually be sent.
    "smtp_host": "",
    "smtp_port": 587,
    "smtp_use_tls": True,
    "smtp_username": "",
    "smtp_password": "",
    "smtp_from_address": "",
    "smtp_from_name": "ILT Troubleshooter",
    # Portal routing (see portal_app.py) — base URLs the shared login
    # gateway links users to, based on their "Parts - Brasil" /
    # "ILT - Transportation" permission flags. Defaults assume both apps
    # run on localhost during development; change these under
    # Administration -> App Settings once each app has a real hostname.
    "ilt_app_url": "http://localhost:8501",
    "psld_app_url": "http://localhost:8502",
    # Global announcement banner (see auth/announcements.py) — a
    # dismissible top banner shown in every app (ILT Troubleshooter,
    # PSLD - Parts, and the portal gateway), separate from
    # maintenance_mode_* above (which actively BLOCKS access for
    # non-admins; this is just an informational notice, e.g. "scheduled
    # maintenance tonight at 22:00").
    "global_announcement_enabled": False,
    "global_announcement_message": "",
    "global_announcement_severity": "info",  # info | warning | error
}


def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _load(force: bool = False) -> Dict[str, Any]:
    global _settings_cache, _settings_cache_mtime
    _ensure_data_dir()
    current_mtime = os.path.getmtime(APP_SETTINGS_PATH) if os.path.exists(APP_SETTINGS_PATH) else None
    if not force and _settings_cache is not None and _settings_cache_mtime == current_mtime:
        return dict(_settings_cache)
    data = _safe_load_json(APP_SETTINGS_PATH, default={})
    data = data if isinstance(data, dict) else {}
    _settings_cache = dict(data)
    _settings_cache_mtime = current_mtime
    return dict(data)


def _merge_settings(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in (overrides or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def get_settings() -> Dict[str, Any]:
    return _merge_settings(DEFAULT_SETTINGS, _load())


def get_setting(key: str, default: Any = None) -> Any:
    settings = get_settings()
    if default is None:
        default = DEFAULT_SETTINGS.get(key)
    return settings.get(key, default)


def update_settings(partial: Dict[str, Any]) -> None:
    _ensure_data_dir()
    with json_transaction(APP_SETTINGS_PATH, default={}) as settings:
        if not isinstance(settings, dict):
            raise TypeError("App settings storage must be a JSON object.")
        updated = _merge_settings(_merge_settings(DEFAULT_SETTINGS, settings), partial or {})
        settings.clear()
        settings.update(updated)
    _load(force=True)


def reset_to_defaults() -> None:
    _ensure_data_dir()
    _safe_save_json(APP_SETTINGS_PATH, dict(DEFAULT_SETTINGS))
    _load(force=True)
