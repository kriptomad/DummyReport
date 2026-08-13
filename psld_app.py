"""
psld_app.py
=============
Standalone entrypoint for **PSLD - Parts**, fully decoupled from ILT
Troubleshooter (app.py) — run as its own separate Streamlit process, on
its own port, with its own URL.

WHY THIS EXISTS
----------------
ILT Troubleshooter (app.py) started as a single-purpose tool and grew a
large "🧪 Lab Test" experimentation area, inside which the PSLD - Parts
AI ticket-resolution assistant was built and matured into a real,
actively-used feature (mock data inserter, ABEND registry, multi-level
AI/neural resolution matching, Double-Check reviewer queue, AI Control
Center, resolution-doc KB with native viewer, etc.). The user asked to
split PSLD - Parts out into its own standalone app "before ILT
Troubleshooter turns into an irreversible monster" — i.e. before the two
very different tools become so tangled together that separating them
later becomes risky/impossible.

WHAT IS SHARED vs. WHAT IS SEPARATE
-------------------------------------
Shared (reused as-is, unmodified — same login accounts, same encrypted
DB profiles, same Oracle connections, same language switch):
  - `auth/` — user_store (login/registration/roles/flags), session_store
    (server-side session tokens), presence (online users), ui (login
    gate + user sidebar widgets). Logging into either app uses the exact
    same user database and password hashes.
  - `database/connection.py`, `database/connection_profiles.py` — the
    same (AES-encrypted) db_connections.json profile store and Oracle
    connection logic. A user who's added/tested an Oracle connection
    profile in ILT Troubleshooter sees the exact same profile here.
  - `config/app_settings.py` — the same runtime settings store (feature
    flags, maintenance mode, etc.).
  - `i18n/` — the same translation catalogue and language switch.
  - `ui/app_theme.py` — the exact same dark-theme CSS, so the two apps
    look and feel identical (see app.py, which now also calls
    `inject_base_css()` from this shared module instead of duplicating
    the CSS block that used to live directly in app.py).

NOT shared (PSLD - Parts owns these outright, no ILT Troubleshooter code
is imported at all):
  - `ui/psld_parts_tab.py` and everything under `troubleshooter/` used
    only by PSLD - Parts (servicenow_resolution_kb.py, ai_core.py,
    document_viewer.py, document_extractor.py, psld_review_queue.py,
    abend registry modules, mock-data inserter, etc.) — none of ILT
    Troubleshooter's own tabs (Query Builder, SQL Glossary, AI Query,
    Schema Manager, Knowledge Base, Pending, Help) are imported or
    mounted here at all.

HOW TO RUN
-----------
    .venv\\Scripts\\python.exe -m streamlit run psld_app.py --server.port 8502

(ILT Troubleshooter keeps running unmodified on its usual port, e.g.
8501 — the two are independent processes that just happen to share the
same on-disk auth/DB-profile/settings files.)
"""
import streamlit as st

from config import app_settings
from auth import user_store
from auth import presence
from auth import ui as auth_ui
from auth import session_store
from streamlit_cookies_controller import CookieController

from i18n import t, language_selector
from ui.theme_manager import inject_theme_css, render_theme_selector
from ui.announcement_banner import render_global_announcement_banner
from ui.psld_parts_tab import render_psld_parts_tab
from ui.messaging_widget import render_floating_messenger
from ui.servicenow_login_widget import render_servicenow_login_menu

# ─────────────────────────────────────────────
#  PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="PSLD - Parts",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────
#  CUSTOM CSS — same shared theme system as ILT Troubleshooter (see
#  ui/theme_manager.py) — light/dark/colorblind-friendly, per-user
# ─────────────────────────────────────────────
inject_theme_css()

# ─────────────────────────────────────────────
#  AUTH GATE — must sign in / register before anything else
#  (identical logic to app.py's gate — same user accounts, same cookie-
#  based session persistence, same server-side session store)
# ─────────────────────────────────────────────
if "auth_user" not in st.session_state:
    st.session_state["auth_user"] = None

# SECURITY: never honor a leftover "s" (session token) URL param — see
# the matching note in app.py for the full history of why.
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
        auth_ui.render_login_gate(cookie_controller)
    st.stop()

if st.session_state["auth_user"].get("must_change_password"):
    with _auth_gate_slot.container():
        auth_ui.render_force_password_change_gate(cookie_controller)
    st.stop()

current_user = st.session_state["auth_user"] or {}
current_cws = current_user.get("cws", "")
settings = app_settings.get_settings()
is_admin_user = user_store.is_admin(current_cws)

presence.heartbeat(current_cws, current_user.get("name", ""))

if settings.get("maintenance_mode_enabled") and not is_admin_user:
    st.error(settings.get("maintenance_mode_message") or "⚠️ App is under maintenance.")
    st.stop()

# ─────────────────────────────────────────────
#  SIDEBAR — same widgets as ILT Troubleshooter (language, user menu,
#  online users, floating messenger) — kept for a consistent experience
#  across both apps sharing the same login/user base.
# ─────────────────────────────────────────────
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

    render_servicenow_login_menu()
    st.divider()

if settings.get("enable_messaging", True):
    render_floating_messenger(st.session_state["auth_user"])

# ─────────────────────────────────────────────
#  HEADER
# ─────────────────────────────────────────────
st.markdown("""
<div class="main-header">
    <h1>PSLD - Parts</h1>
</div>
""", unsafe_allow_html=True)
render_global_announcement_banner()
if settings.get("maintenance_mode_enabled") and is_admin_user:
    st.caption("⚠️ Maintenance mode is enabled. Admin access is bypassing the public lockout.")

# ─────────────────────────────────────────────
#  MAIN CONTENT — PSLD - Parts only (no ILT Troubleshooter tabs at all)
# ─────────────────────────────────────────────
render_psld_parts_tab()
