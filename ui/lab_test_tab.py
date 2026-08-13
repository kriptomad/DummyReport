"""
ui/lab_test_tab.py
=====================
"🧪 Lab Test" — a sandbox tab visible ONLY to the root admin (DEMOADMIN),
where in-progress/experimental features get a place to live and be
tried out before (if ever) they're wired into the real app for everyone.
Nothing added here should be assumed production-ready — that's the
whole point of a lab area.

This tab is organized into two independent sub-menus (a plain radio at
the top), because they serve different audiences:
  A. "🔐 ServiceNow Login (testing)" — shared login-method experiments
     (Azure AD/Entra ID SSO, Basic Auth, cookie reuse, real-browser
     session capture). Still not production-usable for the cookie-based
     options (see their "CONFIRMED DEAD END" notes below); Azure AD works
     once IT provisions tenant/client IDs.
  B. "📦 PSLD - Parts" — a separate, dedicated ticket-resolution
     assistant for the PSLD - Parts team (see ui/psld_parts_tab.py):
     curated PDF-runbook knowledge base + local self-learning similarity
     matching against ServiceNow ticket numbers/descriptions. Uses
     whichever ServiceNow login sub-menu A already produced in this
     session (if any), but never shows login UI of its own.
"""
import streamlit as st

from auth.user_store import ROOT_ADMIN_CWS
from i18n import t
from integrations import servicenow_azure_ad, servicenow_browser_session, servicenow_poc
from ui.psld_parts_tab import render_psld_parts_tab


def _current_cws() -> str:
    user = st.session_state.get("auth_user") or {}
    return (user.get("cws", "") or "").strip().upper()


def current_user_email_hint() -> str:
    """Best-effort guess of the signed-in user's corporate e-mail (from
    their app profile), just to pre-fill the Integrated Windows Auth
    username field so they don't have to type it every time."""
    user = st.session_state.get("auth_user") or {}
    return user.get("email_teams", "") or ""


def render_lab_test_tab() -> None:
    # Defense-in-depth: app.py only shows this tab in the nav for
    # DEMOADMIN, but re-check here too so this function can never be
    # reached by anyone else even if the tab-gating logic in app.py is
    # ever bypassed or changes.
    if _current_cws() != ROOT_ADMIN_CWS:
        st.error(t("admin.access_denied"), icon="🚫")
        return

    st.markdown(f'<div class="section-title">{t("lab.title")}</div>', unsafe_allow_html=True)
    st.caption(t("lab.caption"))

    submenu = st.radio(
        t("lab.submenu_picker"),
        options=[t("lab.submenu_sn_login"), t("lab.submenu_psld_parts")],
        horizontal=True,
        key="lab_submenu",
    )
    st.divider()

    if submenu == t("lab.submenu_sn_login"):
        with st.container(border=True):
            st.subheader(t("lab.servicenow_title"))
            st.caption(t("lab.servicenow_caption"))
            _render_servicenow_experiment()

        with st.container(border=True):
            st.subheader(t("lab.sn_basic_title"))
            st.caption(t("lab.sn_basic_caption"))
            _render_servicenow_basic_auth_test()

        with st.container(border=True):
            st.subheader(t("lab.sn_cookie_title"))
            st.caption(t("lab.sn_cookie_caption"))
            _render_servicenow_cookie_test()

        with st.container(border=True):
            st.subheader(t("lab.sn_browser_title"))
            st.caption(t("lab.sn_browser_caption"))
            _render_servicenow_browser_capture_test()
    else:
        render_psld_parts_tab()


def _render_servicenow_experiment() -> None:
    if not servicenow_azure_ad.MSAL_AVAILABLE:
        st.error(t("lab.msal_missing"), icon="🚫")
        return

    # Values can come from env vars (SERVICENOW_AAD_TENANT_ID / _CLIENT_ID
    # / _SCOPE) if already set, or be typed in here for a quick trial —
    # kept in session_state only, never written to disk.
    st.markdown(f"**{t('lab.aad_config')}**")
    c1, c2, c3 = st.columns(3)
    tenant_id = c1.text_input(
        "SERVICENOW_AAD_TENANT_ID",
        value=st.session_state.get("lab_aad_tenant_id", servicenow_azure_ad.AZURE_TENANT_ID),
        key="lab_aad_tenant_id",
        help=t("lab.aad_tenant_help"),
    )
    client_id = c2.text_input(
        "SERVICENOW_AAD_CLIENT_ID",
        value=st.session_state.get("lab_aad_client_id", servicenow_azure_ad.AZURE_CLIENT_ID),
        key="lab_aad_client_id",
        help=t("lab.aad_client_help"),
    )
    scope = c3.text_input(
        "SERVICENOW_AAD_SCOPE",
        value=st.session_state.get("lab_aad_scope", servicenow_azure_ad.SERVICENOW_API_SCOPE),
        key="lab_aad_scope",
        help=t("lab.aad_scope_help"),
    )

    configured = servicenow_azure_ad.is_configured(tenant_id, client_id, scope)
    if not configured:
        st.info(t("lab.aad_not_configured"), icon="ℹ️")

    st.divider()

    flow_state = st.session_state.get("lab_sn_device_flow")
    token_result = st.session_state.get("lab_sn_token_result")

    if token_result:
        email = servicenow_azure_ad.get_signed_in_email(token_result)
        st.success(t("lab.signed_in_as", email=email or "?"), icon="✅")
        if st.button(t("lab.sign_out"), key="lab_sn_signout"):
            st.session_state.pop("lab_sn_token_result", None)
            st.session_state.pop("lab_sn_device_flow", None)
            st.rerun()

        st.markdown(f"**{t('lab.fetch_tickets_title')}**")
        table = st.text_input(t("lab.table_name"), value=servicenow_poc.DEFAULT_TABLE, key="lab_sn_table")

        st.caption(t("lab.assignment_group_search_caption"))
        gcol1, gcol2 = st.columns([3, 1])
        group_query = gcol1.text_input(
            t("lab.assignment_group_search"), key="lab_sn_group_query", label_visibility="collapsed",
            placeholder=t("lab.assignment_group_search_placeholder"),
        )
        if gcol2.button(t("lab.assignment_group_search_button"), key="lab_sn_group_search_btn"):
            with st.spinner(t("lab.fetching")):
                try:
                    found = servicenow_poc.search_assignment_groups_aad(group_query, token_result["access_token"])
                    st.session_state["lab_sn_group_results"] = found
                    if not found:
                        st.info(t("lab.assignment_group_no_results"))
                except Exception as e:
                    st.error(t("lab.fetch_failed", reason=str(e)))

        group_results = st.session_state.get("lab_sn_group_results") or []
        # Accumulate picks across multiple searches (e.g. searching "audit"
        # then "freight" separately) so several teams' queues can be
        # watched together, instead of only the results of the last search.
        picked = st.session_state.setdefault("lab_sn_groups_picked", {})
        if group_results:
            selected_names = st.multiselect(
                t("lab.assignment_group_pick"),
                options=[g["name"] for g in group_results],
                key="lab_sn_group_pick",
            )
            for g in group_results:
                if g["name"] in selected_names:
                    picked[g["sys_id"]] = g["name"]

        if picked:
            st.caption(t("lab.assignment_group_selected", names=", ".join(picked.values())))
            if st.button(t("lab.assignment_group_clear"), key="lab_sn_group_clear"):
                st.session_state["lab_sn_groups_picked"] = {}
                st.rerun()

        assignment_groups = list(picked.keys()) or None

        # `task` is ServiceNow's generic base table (covers incident/
        # sc_task/change_task/problem_task all at once); `incident` uses
        # a different set of state codes — swap the label set to match
        # whichever table name is currently typed in.
        active_labels = (
            servicenow_poc.INCIDENT_STATE_LABELS if table.strip().lower() == "incident"
            else servicenow_poc.STATE_LABELS
        )
        states = st.multiselect(
            t("lab.states_filter"),
            options=list(active_labels.keys()),
            default=list(active_labels.keys()),
            format_func=lambda code: f"{code} - {active_labels.get(code, code)}",
            key="lab_sn_states",
        )
        if st.button(t("lab.fetch_tickets_button"), key="lab_sn_fetch", type="primary"):
            with st.spinner(t("lab.fetching")):
                try:
                    tickets = servicenow_poc.fetch_tickets_by_state_aad(
                        token_result["access_token"],
                        states=states or None,
                        table=table.strip() or servicenow_poc.DEFAULT_TABLE,
                        assignment_groups=assignment_groups,
                    )
                    st.session_state["lab_sn_tickets"] = tickets
                    st.success(t("lab.fetch_success", count=len(tickets)))
                except Exception as e:
                    st.error(t("lab.fetch_failed", reason=str(e)))

        tickets = st.session_state.get("lab_sn_tickets")
        if tickets:
            st.dataframe(tickets, width="stretch")
        return

    if flow_state:
        st.info(flow_state["flow"]["message"])
        col_a, col_b = st.columns(2)
        if col_a.button(t("lab.check_signin"), key="lab_sn_check"):
            with st.spinner(t("lab.waiting_signin")):
                try:
                    result = servicenow_azure_ad.complete_device_flow(flow_state)
                    st.session_state["lab_sn_token_result"] = result
                    st.session_state.pop("lab_sn_device_flow", None)
                    st.rerun()
                except Exception as e:
                    st.error(t("lab.signin_failed", reason=str(e)))
        if col_b.button(t("lab.cancel"), key="lab_sn_cancel"):
            st.session_state.pop("lab_sn_device_flow", None)
            st.rerun()
        return

    st.markdown(f"**{t('lab.signin_method')}**")
    method = st.radio(
        t("lab.signin_method"),
        options=["browser", "device", "iwa"],
        format_func=lambda k: {
            "browser": t("lab.method_browser"),
            "device": t("lab.method_device"),
            "iwa": t("lab.method_iwa"),
        }[k],
        key="lab_sn_method",
        label_visibility="collapsed",
        horizontal=True,
    )
    st.caption({
        "browser": t("lab.method_browser_help"),
        "device": t("lab.method_device_help"),
        "iwa": t("lab.method_iwa_help"),
    }[method])

    iwa_username = ""
    if method == "iwa":
        iwa_username = st.text_input(t("lab.iwa_username"), value=current_user_email_hint(), key="lab_sn_iwa_user")

    if st.button(t("lab.start_signin"), key="lab_sn_start", type="primary", disabled=not configured):
        try:
            if method == "browser":
                with st.spinner(t("lab.waiting_signin")):
                    result = servicenow_azure_ad.login_interactive_browser(tenant_id, client_id, scope)
                st.session_state["lab_sn_token_result"] = result
                st.rerun()
            elif method == "iwa":
                with st.spinner(t("lab.waiting_signin")):
                    result = servicenow_azure_ad.login_windows_integrated(tenant_id, client_id, scope, iwa_username)
                st.session_state["lab_sn_token_result"] = result
                st.rerun()
            else:
                started = servicenow_azure_ad.start_device_flow(tenant_id, client_id, scope)
                st.session_state["lab_sn_device_flow"] = started
                st.rerun()
        except Exception as e:
            st.error(t("lab.signin_failed", reason=str(e)))


def _render_servicenow_basic_auth_test() -> None:
    """Option 1: test whether the Table API accepts plain Basic Auth
    (username/password) even though the browser UI redirects to Azure AD
    SSO. Some ServiceNow instances only enforce SSO for the UI login
    screen and leave the REST API open to Basic Auth / API keys for
    integration accounts — this is a quick way to find out without
    waiting on IT."""
    c1, c2 = st.columns(2)
    username = c1.text_input(t("lab.sn_username"), key="lab_sn_basic_user")
    password = c2.text_input(t("lab.sn_password"), type="password", key="lab_sn_basic_pass")
    table = st.text_input(
            t("lab.table_name"), value=servicenow_poc.DEFAULT_TABLE, key="lab_sn_basic_table"
    )
    if st.button(t("lab.sn_basic_test_button"), key="lab_sn_basic_fetch", type="primary"):
            if not username or not password:
                st.warning(t("lab.sn_basic_missing_creds"), icon="⚠️")
            else:
                with st.spinner(t("lab.fetching")):
                    try:
                        creds = servicenow_poc.ServiceNowCredentials(username=username, password=password)
                        tickets = servicenow_poc.fetch_tickets_by_state(
                            creds, table=table.strip() or servicenow_poc.DEFAULT_TABLE
                        )
                        st.session_state["lab_sn_basic_tickets"] = tickets
                        st.success(t("lab.fetch_success", count=len(tickets)))
                    except Exception as e:
                        # A 401/302-to-login-page here is the expected outcome
                        # if the org truly enforces SSO for the API too — that
                        # answers the question either way.
                        st.error(t("lab.fetch_failed", reason=str(e)))

    tickets = st.session_state.get("lab_sn_basic_tickets")
    if tickets:
            st.dataframe(tickets, width="stretch")


def _render_servicenow_cookie_test() -> None:
    """Option 4 — CONFIRMED DEAD END for this instance (kept as
    diagnostic tooling / history): reuse an already-logged-in browser
    session's cookies to see if the Table API honors it the same way the
    browser UI does. Tested with a genuinely valid session (including
    the stronger in-page fetch() variant in the section below) — always
    401. This org enforces OAuth2-only on the Table API."""
    st.error(t("lab.sn_confirmed_dead_end"), icon="🚫")
    st.warning(t("lab.sn_cookie_warning"), icon="⚠️")
    cookie_raw = st.text_area(
            t("lab.sn_cookie_input"),
            placeholder="JSESSIONID=abc123; glide_user_route=xyz789",
            key="lab_sn_cookie_raw",
            height=80,
    )
    table = st.text_input(
            t("lab.table_name"), value=servicenow_poc.DEFAULT_TABLE, key="lab_sn_cookie_table"
    )
    if st.button(t("lab.sn_cookie_test_button"), key="lab_sn_cookie_fetch", type="primary"):
            cookies = {}
            for part in cookie_raw.split(";"):
                part = part.strip()
                if "=" in part:
                    name, _, value = part.partition("=")
                    cookies[name.strip()] = value.strip()
            if not cookies:
                st.warning(t("lab.sn_cookie_missing"), icon="⚠️")
            else:
                with st.spinner(t("lab.fetching")):
                    try:
                        tickets = servicenow_poc.fetch_tickets_by_cookie(
                            cookies, table=table.strip() or servicenow_poc.DEFAULT_TABLE
                        )
                        st.session_state["lab_sn_cookie_tickets"] = tickets
                        st.success(t("lab.fetch_success", count=len(tickets)))
                    except Exception as e:
                        st.error(t("lab.fetch_failed", reason=str(e)))

    tickets = st.session_state.get("lab_sn_cookie_tickets")
    if tickets:
            st.dataframe(tickets, width="stretch")


def _render_servicenow_browser_capture_test() -> None:
    """Option 5 (experimental, "sequestro" de sessão): opens a REAL,
    visible browser window pointed at ServiceNow, lets the human complete
    the normal Entra ID / SSO login exactly as they always do, then
    captures the resulting session cookies and persists them locally
    (gitignored) so subsequent requests can reuse that session — no
    client_id/tenant_id/scope needed from IT, since it's just "logging in
    like a person" and keeping the receipt. See
    integrations/servicenow_browser_session.py for the full explanation
    of how it works and why it's not production-ready as-is."""
    if not servicenow_browser_session.PLAYWRIGHT_AVAILABLE:
        st.error(t("lab.sn_browser_missing"), icon="🚫")
        return

    st.error(t("lab.sn_confirmed_dead_end"), icon="🚫")
    st.warning(t("lab.sn_browser_warning"), icon="⚠️")

    age = servicenow_browser_session.session_file_age_minutes()
    if age is not None:
        st.caption(t("lab.sn_browser_captured_ago", minutes=int(age)))
    else:
        st.caption(t("lab.sn_browser_not_captured"))

    col_a, col_b = st.columns(2)
    if col_a.button(t("lab.sn_browser_capture_button"), key="lab_sn_browser_capture"):
        with st.spinner(t("lab.sn_browser_waiting")):
            try:
                cookies = servicenow_browser_session.capture_session_interactive()
                st.session_state["lab_sn_browser_cookie_count"] = len(cookies)
                st.session_state["lab_sn_browser_cookie_names"] = sorted(cookies.keys())
                st.success(t("lab.sn_browser_capture_success", count=len(cookies)))
            except Exception as e:
                st.error(t("lab.fetch_failed", reason=str(e)))

    cookie_names = st.session_state.get("lab_sn_browser_cookie_names")
    if cookie_names:
        st.caption(t("lab.sn_browser_cookie_names", names=", ".join(cookie_names)))

    if age is not None and col_b.button(t("lab.sn_browser_clear_button"), key="lab_sn_browser_clear"):
        servicenow_browser_session.clear_saved_session()
        st.session_state.pop("lab_sn_browser_cookie_count", None)
        st.session_state.pop("lab_sn_browser_cookie_names", None)
        st.session_state.pop("lab_sn_browser_tickets", None)
        st.rerun()

    st.divider()

    table = st.text_input(
        t("lab.table_name"), value=servicenow_poc.DEFAULT_TABLE, key="lab_sn_browser_table"
    )
    col_fetch1, col_fetch2 = st.columns(2)
    if col_fetch1.button(t("lab.sn_browser_fetch_button"), key="lab_sn_browser_fetch", type="primary"):
        cookies = servicenow_browser_session.load_saved_session()
        if not cookies:
            st.warning(t("lab.sn_browser_no_session"), icon="⚠️")
        else:
            st.caption(t("lab.sn_browser_cookie_names", names=", ".join(sorted(cookies.keys()))))
            with st.spinner(t("lab.fetching")):
                try:
                    tickets = servicenow_poc.fetch_tickets_by_cookie(
                        cookies, table=table.strip() or servicenow_poc.DEFAULT_TABLE
                    )
                    st.session_state["lab_sn_browser_tickets"] = tickets
                    st.success(t("lab.fetch_success", count=len(tickets)))
                except Exception as e:
                    st.error(t("lab.fetch_failed", reason=str(e)))

    if col_fetch2.button(t("lab.sn_browser_fetch_inpage_button"), key="lab_sn_browser_fetch_inpage"):
        with st.spinner(t("lab.fetching")):
            try:
                tickets = servicenow_browser_session.fetch_tickets_via_browser(
                    table=table.strip() or servicenow_poc.DEFAULT_TABLE
                )
                st.session_state["lab_sn_browser_tickets"] = tickets
                st.success(t("lab.fetch_success", count=len(tickets)))
            except Exception as e:
                st.error(t("lab.fetch_failed", reason=str(e)))

    tickets = st.session_state.get("lab_sn_browser_tickets")
    if tickets:
        st.dataframe(tickets, width="stretch")

