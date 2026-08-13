"""
portal_app.py
===============
Shared login gateway + Central Admin Dashboard — the new recommended
front door for BOTH ILT Troubleshooter (app.py) and PSLD - Parts
(psld_app.py).

WHAT IT DOES
-------------
1. Shows ONE login screen (same accounts/passwords as the other two
   apps — same `auth/user_store.py`).
2. After login, routes the user based on their permission flags:
     - Admins land on the **Central Admin Dashboard** (system overview,
       user/permission management, global announcement broadcast,
       audit/console log) and can also choose to "Enter" either portal
       from here.
     - A user flagged "Parts - Brasil" (`is_psld_parts`) only -> a short
       auto-redirect page linking straight to PSLD - Parts.
     - A user flagged "ILT - Transportation" (`is_ilt_transportation`)
       only -> a short auto-redirect page linking straight to ILT
       Troubleshooter.
     - A user with BOTH flags -> a simple picker page ("which portal do
       you want to open?").
     - A user with NEITHER flag -> a message explaining they need at
       least one portal flag, with the admin's contact info.

HOW CROSS-APP LOGIN WORKS
----------------------------
All three processes (this one, app.py, psld_app.py) share the same
on-disk session-token store (`auth/session_store.py`, backed by
`data/active_sessions.json`) and the same browser cookie name — logging
in HERE is enough; opening ILT Troubleshooter or PSLD - Parts in another
tab recognizes the same session automatically (true single sign-on),
no separate login needed on each one. See auth/session_store.py's module
docstring for the one deliberate exception (the end-to-end-messaging
decryption key, which never leaves the process the password was typed
into, by design).

HOW TO RUN
-----------
    .venv\\Scripts\\python.exe -m streamlit run portal_app.py --server.port 8500

(Point people at this URL first; app.py/psld_app.py remain directly
reachable too, for anyone who bookmarks a direct link — they still each
enforce their own login gate if there's no valid shared session yet.)
"""
from __future__ import annotations

import streamlit as st

from auth import user_store
from auth import presence
from auth import ui as auth_ui
from auth import session_store
from auth import audit_log
from streamlit_cookies_controller import CookieController

from config import app_settings
from i18n import t, language_selector
from ui.theme_manager import inject_theme_css, render_theme_selector
from ui.announcement_banner import render_global_announcement_banner
from ui.central_admin_dashboard import render_central_admin_dashboard

st.set_page_config(page_title="Demo Apps Portal", page_icon="🧭", layout="wide", initial_sidebar_state="expanded")
inject_theme_css()

# ─────────────────────────────────────────────
#  AUTH GATE — identical pattern to app.py / psld_app.py
# ─────────────────────────────────────────────
if "auth_user" not in st.session_state:
    st.session_state["auth_user"] = None
if "s" in st.query_params:
    del st.query_params["s"]

_cookies_already_cached = "cookies" in st.session_state
cookie_controller = CookieController()
if st.session_state["auth_user"] is None and _cookies_already_cached:
    # See app.py's matching comment: force a fresh browser-cookie read
    # (but only on a rerun, not the very first run, or the underlying
    # component gets called twice with the same key in one pass and
    # crashes with StreamlitDuplicateElementKey).
    cookie_controller.refresh()

# Flush any cookie set/remove queued by a login/logout on the PRIOR run —
# see the `_PENDING_SET_KEY`/`_PENDING_REMOVE_KEY` comment in auth/ui.py.
auth_ui.apply_pending_cookie_actions(cookie_controller)

if st.session_state["auth_user"] is None:
    _restore_token = cookie_controller.get(auth_ui.SESSION_COOKIE_NAME)
    if _restore_token:
        _restored_user = session_store.get_session(_restore_token)
        if _restored_user:
            st.session_state["auth_user"] = _restored_user
            st.session_state["_session_token"] = _restore_token
            session_store.touch_session(_restore_token, app_settings.get_setting("session_timeout_minutes", 480))
        else:
            st.session_state[auth_ui._PENDING_REMOVE_KEY] = True


# See app.py's matching comment for why an explicit `st.empty()` slot
# (created unconditionally, every run) is needed here — without it, the
# login form's markup was observed lingering under the real app on every
# subsequent run once logged in.
_auth_gate_slot = st.empty()
if st.session_state["auth_user"] is None:
    with _auth_gate_slot.container():
        with st.sidebar:
            language_selector()
        auth_ui.render_login_gate(cookie_controller, subtitle="ILT Portal")
    st.stop()

if st.session_state["auth_user"].get("must_change_password"):
    with _auth_gate_slot.container():
        auth_ui.render_force_password_change_gate(cookie_controller)
    st.stop()

current_user = st.session_state["auth_user"] or {}
current_cws = current_user.get("cws", "")
settings = app_settings.get_settings()
is_admin_user = user_store.is_admin(current_cws)
has_parts = user_store.is_psld_parts(current_cws)
has_ilt = user_store.is_ilt_transportation(current_cws)

presence.heartbeat(current_cws, current_user.get("name", ""))

with st.sidebar:
    language_selector()
    render_theme_selector()
    st.divider()
    auth_ui.render_user_sidebar(cookie_controller)
    st.divider()
    online_users = presence.list_online_users()
    with st.expander(f"🟢 {t('online.title')} ({len(online_users)})", expanded=False):
        st.caption(t("online.caption"))
        if not online_users:
            st.caption(t("online.none"))
        else:
            for u in online_users:
                is_me = u["cws"].strip().upper() == current_cws.strip().upper()
                suffix = f" — {t('online.you')}" if is_me else ""
                st.markdown(f"🟢 **{u['name']}** ({u['cws']}){suffix}")

st.markdown('<div class="main-header"><h1>Demo Apps Portal</h1></div>', unsafe_allow_html=True)
render_global_announcement_banner()

ilt_url = settings.get("ilt_app_url") or "http://localhost:8501"
psld_url = settings.get("psld_app_url") or "http://localhost:8502"


def _portal_link_card(title: str, description: str, url: str, key: str) -> None:
    with st.container(border=True):
        st.markdown(f"**{title}**")
        st.caption(description)
        st.link_button(f"Enter {title}", url, width="stretch", type="primary", key=key)


# ─────────────────────────────────────────────
#  ADMIN: Central Admin Dashboard (own portal-choice buttons included)
# ─────────────────────────────────────────────
if is_admin_user:
    st.caption(f"Signed in as **{current_user.get('name', current_cws)}** ({current_cws}) — administrator.")
    col1, col2 = st.columns(2)
    with col1:
        _portal_link_card("ILT Troubleshooter", "Shipment/tariff DB error troubleshooting.", ilt_url, "portal_enter_ilt")
    with col2:
        _portal_link_card("PSLD - Parts", "Ticket-resolution assistant for the Parts team.", psld_url, "portal_enter_psld")
    st.divider()
    render_central_admin_dashboard()

# ─────────────────────────────────────────────
#  NON-ADMIN: route based on permission flags
# ─────────────────────────────────────────────
elif has_parts and has_ilt:
    st.subheader("Which app do you want to open?")
    st.caption(f"Signed in as **{current_user.get('name', current_cws)}** ({current_cws}) — you have access to both.")
    col1, col2 = st.columns(2)
    with col1:
        _portal_link_card("ILT Troubleshooter", "Shipment/tariff DB error troubleshooting.", ilt_url, "pick_ilt")
    with col2:
        _portal_link_card("PSLD - Parts", "Ticket-resolution assistant for the Parts team.", psld_url, "pick_psld")

elif has_parts:
    st.subheader("Opening PSLD - Parts…")
    st.caption(f"Signed in as **{current_user.get('name', current_cws)}** ({current_cws}).")
    st.link_button("Enter PSLD - Parts", psld_url, type="primary")
    audit_log.record_event("portal_auto_route", cws=current_cws, detail="single-flag: psld", app="portal", category="auth", severity="info")

elif has_ilt:
    st.subheader("Opening ILT Troubleshooter…")
    st.caption(f"Signed in as **{current_user.get('name', current_cws)}** ({current_cws}).")
    st.link_button("Enter ILT Troubleshooter", ilt_url, type="primary")
    audit_log.record_event("portal_auto_route", cws=current_cws, detail="single-flag: ilt", app="portal", category="auth", severity="info")

else:
    st.warning(
        "Your account doesn't have access to either app yet. Ask an administrator to grant "
        "\"Parts - Brasil\" (PSLD - Parts) and/or \"ILT - Transportation\" access.",
        icon="⚠️",
    )
    support_cws = settings.get("support_contact_cws", "")
    if support_cws:
        st.caption(f"Support contact: {support_cws}")
