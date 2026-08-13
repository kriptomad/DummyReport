# Default connection values (optional pre-fill for the connection dialog)
DEFAULT_HOST = ""
DEFAULT_PORT = "1521"
DEFAULT_SERVICE = ""
DEFAULT_USER = ""


# ─────────────────────────────────────────────────────────────
#  Business "application account" (shared service account)
# ─────────────────────────────────────────────────────────────
# Lets users flagged as "Business" (see auth/user_store.is_business_user)
# connect to Oracle with one click, using a pre-defined service account
# instead of typing personal DB credentials every time.
#
# IMPORTANT: these values must NEVER be hard-coded here or committed to
# source control. They are only ever read from (in this order):
#   1. st.secrets["app_account"][...]   — Streamlit's recommended secrets
#      store (.streamlit/secrets.toml, gitignored, or platform secrets).
#   2. Environment variables            — APP_ACCOUNT_HOST / _PORT /
#      _SERVICE / _USER / _PASSWORD.
# If neither is set, the feature is simply unavailable (the one-click
# button is hidden) — everyone falls back to the existing manual
# username/password flow. See Documentation/APPLICATION_ACCOUNT.md for
# setup instructions.
import os


def _app_account_value(key: str, env_var: str, default: str = "") -> str:
    try:
        import streamlit as st
        secrets_section = st.secrets.get("app_account", {}) if hasattr(st, "secrets") else {}
        value = secrets_section.get(key)
        if value:
            return str(value)
    except Exception:
        pass
    return os.getenv(env_var, default)


def get_app_account() -> dict:
    """
    Returns the configured Business application account, read fresh each
    call (never cached) from st.secrets / environment variables — see
    module docstring above for precedence and security notes.
    """
    return {
        "host":     _app_account_value("host", "APP_ACCOUNT_HOST"),
        "port":     _app_account_value("port", "APP_ACCOUNT_PORT", "1521"),
        "service":  _app_account_value("service", "APP_ACCOUNT_SERVICE"),
        "user":     _app_account_value("user", "APP_ACCOUNT_USER"),
        "password": _app_account_value("password", "APP_ACCOUNT_PASSWORD"),
    }


def is_app_account_configured() -> bool:
    """Whether all required application-account fields are present."""
    acct = get_app_account()
    return bool(acct["host"] and acct["service"] and acct["user"] and acct["password"])

