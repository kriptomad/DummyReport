"""
ui/announcement_banner.py
============================
Shared, dismissible top banner rendered by every app (ILT Troubleshooter,
PSLD - Parts, and the portal gateway) when an admin has an active
"global announcement" set (see the Central Admin Dashboard in
portal_app.py). Separate from `maintenance_mode_*` in
config/app_settings.py, which actively BLOCKS non-admin access — this is
just an informational notice (e.g. "scheduled maintenance tonight at
22:00", "KB import running, search may be slower than usual").
"""
import html

import streamlit as st

from config import app_settings

_SEVERITY_ICON = {"info": "ℹ️", "warning": "⚠️", "error": "🚫"}


def render_global_announcement_banner() -> None:
    settings = app_settings.get_settings()
    if not settings.get("global_announcement_enabled"):
        return
    message = (settings.get("global_announcement_message") or "").strip()
    if not message:
        return

    severity = settings.get("global_announcement_severity") or "info"
    icon = _SEVERITY_ICON.get(severity, "ℹ️")
    dismiss_key = "_announcement_dismissed_for"
    # Re-shows automatically if the admin changes the message text (a
    # genuinely new announcement), even if an older one was dismissed.
    if st.session_state.get(dismiss_key) == message:
        return

    col_msg, col_dismiss = st.columns([20, 1])
    with col_msg:
        st.markdown(
            f'<div class="global-banner">{icon} {html.escape(message)}</div>',
            unsafe_allow_html=True,
        )
    with col_dismiss:
        if st.button("✕", key="_dismiss_announcement", help="Dismiss"):
            st.session_state[dismiss_key] = message
            st.rerun()
