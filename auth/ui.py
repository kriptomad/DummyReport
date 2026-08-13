"""
auth/ui.py
==========
Streamlit UI for the login/registration gate and the logged-in user's
sidebar widget. Kept separate from app.py to keep the main script focused.
"""
import base64
import functools
from pathlib import Path
from typing import Optional

import streamlit as st

from auth.user_store import authenticate, register_user, change_password
from auth import session_store
from auth import audit_log
from config import app_settings
from i18n import t, language_selector

_LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "app_logo.png"
SESSION_COOKIE_NAME = "ilt_session"

# ── Deferred cookie writes ────────────────────────────────────────────
# `streamlit_cookies_controller` sets/removes cookies by mounting a hidden
# component iframe that runs its cookie JS asynchronously (postMessage).
# If we call `cookie_controller.set()`/`.remove()` and then immediately
# `st.rerun()` in the SAME script run (as the login/logout buttons used
# to do), the rerun tears down that just-mounted iframe before its JS has
# a chance to actually execute — so the browser cookie is silently never
# written/removed. This was confirmed via Playwright: `ctx.cookies()`
# never showed the session cookie after login, and the widget's iframe
# never appeared in `page.frames` at all by the time of the next render.
#
# Fix: never call `.set()`/`.remove()` right before a `st.rerun()`.
# Instead, stash *what* needs to happen in `session_state`, rerun, and
# then perform the actual cookie write on the FOLLOWING run via
# `apply_pending_cookie_actions()` — called once near the top of every
# entrypoint (app.py/psld_app.py/portal_app.py), after the auth gate, on
# a run that isn't about to immediately rerun again, so the component has
# time to actually mount and fire its JS.
_PENDING_SET_KEY = "_pending_session_cookie_set"
_PENDING_REMOVE_KEY = "_pending_session_cookie_remove"


def apply_pending_cookie_actions(cookie_controller) -> None:
    """
    Performs any session-cookie set/remove that was queued on a PRIOR run
    (see the module-level comment above for why this two-step dance is
    necessary). Call this once, unconditionally, near the top of every
    entrypoint script — it's a no-op when nothing is pending.
    """
    pending_set = st.session_state.pop(_PENDING_SET_KEY, None)
    if pending_set:
        cookie_controller.set(
            SESSION_COOKIE_NAME, pending_set["token"],
            max_age=pending_set["max_age"], same_site="strict",
        )
    if st.session_state.pop(_PENDING_REMOVE_KEY, False):
        cookie_controller.remove(SESSION_COOKIE_NAME)



@functools.lru_cache(maxsize=1)
def _get_logo_html() -> str:
    """
    Returns an <img> tag for the Acme Logistics logo, base64-embedded so it
    renders inline with the rest of the login page's custom HTML. Falls
    back to a compass emoji if the image file isn't available for some
    reason (e.g. not deployed alongside the app).
    """
    try:
        data = base64.b64encode(_LOGO_PATH.read_bytes()).decode("ascii")
        return (
            '<img src="data:image/png;base64,' + data + '" '
            'class="login-logo-img" alt="Demo Apps Portal" '
            'style="width:64px;max-width:64px;height:auto;" />'
        )
    except Exception:
        return "🧭"


def render_login_gate(cookie_controller, subtitle: Optional[str] = None) -> None:
    """
    Renders a full-page login/registration screen. Call this when
    st.session_state["auth_user"] is None, then st.stop() right after.

    `cookie_controller` is a `streamlit_cookies_controller.CookieController`
    instance (created once in app.py) — used to persist the session token
    as a browser cookie instead of a URL query param (see SECURITY note in
    auth/session_store.py for why: a token in the URL travels with the
    page link itself, so sharing/copying that URL handed your logged-in
    session to whoever opened it).

    `subtitle` optionally overrides the small caption under "Sign in"
    (defaults to the shared `auth.subtitle` translation, "ILT
    Troubleshooter tool") — used by portal_app.py to show "ILT Portal"
    instead, since this same login screen is now shared by all three
    entrypoints.
    """
    # Scoped CSS: centers a clean, modern card and hides the sidebar/menu
    # while the user isn't authenticated yet (nothing to show them there).
    # Uses the same Acme Logistics black/yellow palette (--ilt-primary etc.)
    # defined globally in app.py.
    st.markdown("""
    <style>
        [data-testid="stSidebar"], [data-testid="collapsedControl"] { display: none !important; }
        [data-testid="stDecoration"] { display: none !important; }
        .login-wrap { max-width: 420px; margin: 3.2rem auto 0; }
        .login-logo { text-align: center; margin: 0.4rem 0 1rem; }
        .login-logo-img { width: 64px !important; max-width: 64px !important; height: auto !important; display: inline-block; }
        .login-title { text-align: center; font-size: 1.5rem; font-weight: 700;
            color: var(--ilt-ink, #f5f5f5) !important; margin: 0 0 0.2rem; }
        .login-subtitle { text-align: center; font-size: 0.88rem;
            color: var(--ilt-primary, #FFCD11) !important; font-weight: 600;
            margin-bottom: 0.2rem; }
        .login-card { }

        /* Login/Register tabs — remove the default full-width underline
           bar and style the active tab as a small yellow pill instead. */
        .login-card div[data-baseweb="tab-list"] {
            gap: 6px;
            background: transparent;
            border-bottom: 1px solid var(--ilt-border, #333333);
            justify-content: center;
        }
        .login-card button[data-baseweb="tab"] {
            border-radius: 999px 999px 0 0 !important;
            font-weight: 600 !important;
            color: var(--ilt-muted, #a3a3a3) !important;
            background: transparent !important;
        }
        .login-card button[data-baseweb="tab"][aria-selected="true"] {
            color: var(--ilt-primary, #FFCD11) !important;
            border-bottom: 2px solid var(--ilt-primary, #FFCD11) !important;
        }
        .login-card div[data-baseweb="tab-highlight"] { background-color: var(--ilt-primary, #FFCD11) !important; height: 2px !important; }
        .login-card div[data-baseweb="tab-border"]    { display: none !important; }

        /* Fixed footer — always pinned to the very bottom of the screen. */
        .login-footer-fixed {
            position: fixed; left: 0; bottom: 0; width: 100%;
            text-align: center; font-size: 0.72rem;
            color: var(--ilt-muted, #a3a3a3);
            background: rgba(10,10,10,0.85);
            padding: 0.4rem 0 0.5rem; letter-spacing: 0.2px;
            z-index: 999;
        }
        .login-footer-fixed strong { color: var(--ilt-primary, #FFCD11); font-weight: 700; }
    </style>
    """, unsafe_allow_html=True)

    st.markdown('<div class="login-wrap">', unsafe_allow_html=True)

    # Language switcher — must be available here since the sidebar (where it
    # normally lives) is hidden until the user logs in.
    lang_col, _ = st.columns([1, 2])
    with lang_col:
        language_selector(location="inline", key="login_language_selector")

    st.markdown(f'<div class="login-title">{t("auth.title")}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="login-subtitle">{subtitle if subtitle is not None else t("auth.subtitle")}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="login-logo">{_get_logo_html()}</div>', unsafe_allow_html=True)

    st.markdown('<div class="login-card">', unsafe_allow_html=True)
    tab_login, tab_register = st.tabs([t("auth.tab_login"), t("auth.tab_register")])

    with tab_login:
        _, login_mid, _ = st.columns([1, 3, 1])
        with login_mid:
            with st.form("login_form"):
                cws = st.text_input(t("auth.cws"), key="login_cws", placeholder=t("auth.cws"))
                password = st.text_input(t("auth.password"), type="password", key="login_password", placeholder=t("auth.password"))
                submitted = st.form_submit_button(t("auth.login_button"), type="primary", width="stretch")

        if submitted:
            ok, result = authenticate(cws, password)
            if ok:
                st.session_state["auth_user"] = result
                # Keep the user logged in across a page refresh (F5): mint a
                # session token, stash it (in RAM only) alongside the auth
                # data, and store the token as a browser cookie (NOT the
                # URL) so a reload can restore this same session — see
                # auth/session_store.py. A cookie never travels with a
                # shared/copied page link, unlike a URL query param.
                timeout_minutes = app_settings.get_setting("session_timeout_minutes", session_store.DEFAULT_TIMEOUT_MINUTES)
                token = session_store.create_session(result, timeout_minutes)
                st.session_state["_session_token"] = token
                # Don't call cookie_controller.set() here — see the
                # `_PENDING_SET_KEY` comment near the top of this file for
                # why writing the cookie right before st.rerun() silently
                # loses it. Queue it; apply_pending_cookie_actions() (called
                # from the entrypoint right after this rerun) does the
                # actual write on a run that isn't about to immediately
                # tear itself down again.
                st.session_state[_PENDING_SET_KEY] = {
                    "token": token,
                    "max_age": int(timeout_minutes) * 60,
                }
                audit_log.record_event("login_success", cws=cws, detail=result.get("name", ""), category="auth", severity="info")
                st.success(t("auth.login_success", name=result["name"]))
                st.rerun()
            else:
                audit_log.record_event("login_failed", cws=cws, detail=str(result), category="auth", severity="warning")
                st.error(t("auth.login_failed", msg=result))

    with tab_register:
        # Profile picker: OUTSIDE the st.form below on purpose — widgets
        # inside a form don't trigger a rerun until submit, so the Oracle
        # account field couldn't dynamically appear/disappear as these
        # checkboxes are toggled if they lived inside the form too.
        st.markdown(f"**{t('auth.portal_picker_label')}**")
        pcol1, pcol2 = st.columns(2)
        reg_wants_psld = pcol1.checkbox(t("auth.portal_psld"), key="reg_wants_psld")
        reg_wants_ilt = pcol2.checkbox(t("auth.portal_ilt"), key="reg_wants_ilt")
        show_oracle_field = reg_wants_psld or reg_wants_ilt

        with st.form("register_form"):
            name = st.text_input(t("auth.name"), key="reg_name")
            reg_cws = st.text_input(t("auth.cws"), key="reg_cws")
            email_teams = st.text_input(t("auth.email_teams"), key="reg_email")
            cargo = st.text_input(t("auth.cargo"), key="reg_cargo")
            col1, col2 = st.columns(2)
            password = col1.text_input(t("auth.password"), type="password", key="reg_password")
            confirm_password = col2.text_input(t("auth.confirm_password"), type="password", key="reg_confirm_password")
            st.caption(t("auth.password_hint"))

            # Personal Oracle DB account — only shown/prompted when a
            # portal that actually connects to Oracle was picked above,
            # but always optional (nobody is forced to fill it in at
            # registration; it can be set up later — see app.py's
            # connection_dialog(), which requires it before a manual DB
            # connection either way).
            oracle_username_input = ""
            if show_oracle_field:
                oracle_username_input = st.text_input(
                    t("auth.oracle_username_label"),
                    key="reg_oracle_username",
                    placeholder="e.g. demo_user_db",
                    help=t("auth.oracle_username_help"),
                )

            with st.expander(t("auth.setup_key_expander")):
                root_setup_key = st.text_input(
                    t("auth.setup_key_label"), type="password", key="reg_setup_key",
                    help=t("auth.setup_key_help"),
                )
            reg_submitted = st.form_submit_button(t("auth.register_button"), type="primary", width="stretch")

        if reg_submitted:
            ok, msg, pending_approval = register_user(
                name=name,
                cws=reg_cws,
                password=password,
                confirm_password=confirm_password,
                email_teams=email_teams,
                cargo=cargo,
                root_setup_key=root_setup_key,
                wants_psld_parts=reg_wants_psld,
                wants_ilt_transportation=reg_wants_ilt,
                oracle_username=oracle_username_input,
            )
            if ok:
                if pending_approval:
                    st.success(f"✅ {msg}")
                else:
                    st.success(t("auth.register_success", msg=msg))
            else:
                st.error(t("auth.register_failed", msg=msg))

    st.markdown('</div>', unsafe_allow_html=True)

    # ── Support contact (always visible, even before login) ──────────
    mailto = _build_support_mailto()
    st.markdown(
        f'<div style="text-align:center;font-size:0.82rem;color:var(--ilt-muted,#a3a3a3);margin-top:1rem;">'
        f'{t("auth.need_help")} '
        f'<strong>{t("auth.contact_bruno_teams")}</strong> '
        f'{t("auth.or")} '
        f'<a href="{mailto}" target="_blank" style="color:var(--ilt-primary,#FFCD11);">demo.admin@example.com</a>'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.markdown('</div>', unsafe_allow_html=True)

    # ── Confidential notice — pinned to the very bottom of the page ──────
    st.markdown(
        f'<div class="login-footer-fixed">{t("auth.footer_confidential")} <strong>Yellow</strong></div>',
        unsafe_allow_html=True,
    )


def render_force_password_change_gate(cookie_controller) -> None:
    """
    Renders a full-page "you must change your password" screen. Shown when
    st.session_state["auth_user"]["must_change_password"] is True — i.e.
    an administrator reset this account's password and issued a temporary
    one. Call this instead of the normal app UI, then st.stop() right after.
    """
    user = st.session_state.get("auth_user") or {}
    cws = user.get("cws", "")

    st.markdown('<div class="login-wrap">', unsafe_allow_html=True)
    st.markdown(f'<div class="login-logo">{_get_logo_html()}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="login-title">{t("auth.force_change_title")}</div>', unsafe_allow_html=True)
    st.info(t("auth.force_change_caption"), icon="🔐")

    _, mid, _ = st.columns([1, 3, 1])
    with mid:
        with st.form("force_change_password_form"):
            old_password = st.text_input(t("auth.force_change_temp_password"), type="password", key="fc_old_password")
            new_password = st.text_input(t("auth.new_password"), type="password", key="fc_new_password")
            confirm_new_password = st.text_input(t("auth.confirm_new_password"), type="password", key="fc_confirm_new_password")
            st.caption(t("auth.password_hint"))
            submitted = st.form_submit_button(t("auth.force_change_submit"), type="primary", width="stretch")

        if submitted:
            ok, msg = change_password(cws, old_password, new_password, confirm_new_password)
            if ok:
                user["must_change_password"] = False
                st.session_state["auth_user"] = user
                # Also refresh the SHARED, on-disk session cache (see
                # auth/session_store.py's create_session docstring) — it
                # was populated at login time (before this change), still
                # flagged must_change_password=True, and would otherwise
                # keep restoring that stale copy on the next reload/new
                # tab/other app, trapping the user on this same gate again
                # even after they've already legitimately changed their
                # password (a real bug users hit — "não deixa nem fazer
                # login").
                session_store.update_session(st.session_state.get("_session_token"), user)
                audit_log.record_event(
                    "password_changed_by_user", cws=cws, detail="Completed forced password change (temp password replaced)",
                    category="auth", severity="info",
                )
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)

        if st.button(t("auth.logout"), key="force_change_logout"):
            session_store.destroy_session(st.session_state.get("_session_token"))
            st.session_state["auth_user"] = None
            st.session_state["_session_token"] = None
            # See the `_PENDING_REMOVE_KEY` comment near the top of this
            # file — calling cookie_controller.remove() right before
            # st.rerun() silently loses the cookie removal.
            st.session_state[_PENDING_REMOVE_KEY] = True
            st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)


def _build_support_mailto() -> str:
    """
    Builds a `mailto:` link to Demo Admin pre-filled with a subject that
    includes the current date/time (SYSDATE-style) and an auto-generated
    short ticket ID, so support emails are easy to track/correlate.
    """
    import urllib.parse
    import uuid
    from datetime import datetime

    sysdate = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ticket_id = uuid.uuid4().hex[:8].upper()
    subject = f"Help ILT Troubleshooter - {sysdate} - {ticket_id}"
    return f"mailto:demo.admin@example.com?subject={urllib.parse.quote(subject)}"


def render_user_sidebar(cookie_controller) -> None:
    """Renders the logged-in user's info + logout button in the sidebar."""
    user = st.session_state.get("auth_user")
    if not user:
        return
    st.markdown(f"**{t('auth.logged_in_as')}:** {user['name']} ({user['cws']})")
    st.caption(user.get("cargo", ""))
    if st.button(t("auth.logout"), width="stretch", key="logout_button"):
        session_store.destroy_session(st.session_state.get("_session_token"))
        st.session_state["auth_user"] = None
        st.session_state["_session_token"] = None
        # See the `_PENDING_REMOVE_KEY` comment near the top of this file.
        st.session_state[_PENDING_REMOVE_KEY] = True
        st.rerun()


