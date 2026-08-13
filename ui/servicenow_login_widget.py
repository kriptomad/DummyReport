"""
ui/servicenow_login_widget.py
================================
Compact, standalone ServiceNow (Azure AD / Entra ID) sign-in widget for
the sidebar — meant to sit right next to "Online users" so users always
know where to find it.

WHY THIS EXISTS
---------------
Before PSLD - Parts was split into its own standalone process
(`psld_app.py`), it silently relied on `st.session_state["lab_sn_token_result"]`
having already been populated by the ILT Troubleshooter's "🧪 Lab Test ->
🔐 ServiceNow Login" sub-menu *in the same browser session* — which only
worked because both were tabs inside the same single Streamlit process
sharing one `session_state`. Once PSLD - Parts became its own process (its
own `session_state`, no "Lab Test" tab at all), that login path
disappeared with no replacement — "onde fica o login de service now?".

This module re-hosts the one login method that's actually
production-viable (Azure AD/Entra ID SSO — confirmed working once IT
provisions the tenant/client IDs; see `ui/lab_test_tab.py`'s docstring for
why Basic Auth / cookie-reuse / browser-session-capture are dead ends for
this ServiceNow instance) as a small, self-contained sidebar menu, so
PSLD - Parts (and, if ever wanted, ILT Troubleshooter) can sign in and
sync ticket history without depending on any other tab.

It intentionally stores the result under the SAME session_state key
(`lab_sn_token_result` / `lab_sn_tickets`) the rest of the app already
reads (`ui/psld_parts_tab.py::_render_analyze`), so nothing else needs to
change.
"""
from __future__ import annotations

import streamlit as st

from auth import audit_log
from i18n import t
from integrations import servicenow_azure_ad, servicenow_poc


def _current_user_email_hint() -> str:
    user = st.session_state.get("auth_user") or {}
    return user.get("email_teams", "") or ""


def render_servicenow_login_menu(expanded: bool = False) -> None:
    """Renders a "🔐 ServiceNow" sidebar expander: sign in/out, plus (once
    signed in) a compact "sync ticket history" action so ticket-similarity
    matching has real Resolved/Closed tickets to compare against instead
    of only mock data."""
    token_result = st.session_state.get("lab_sn_token_result")
    signed_in = bool(token_result)
    icon = "🟢" if signed_in else "⚪"
    with st.expander(f"{icon} {t('snlogin.title')}", expanded=expanded):
        if not servicenow_azure_ad.MSAL_AVAILABLE:
            st.error(t("lab.msal_missing"), icon="🚫")
            return

        if signed_in:
            _render_signed_in(token_result)
        else:
            _render_signin_form()


def _render_signed_in(token_result: dict) -> None:
    email = servicenow_azure_ad.get_signed_in_email(token_result)
    st.success(t("lab.signed_in_as", email=email or "?"), icon="✅")

    tickets = st.session_state.get("lab_sn_tickets") or []
    st.caption(t("snlogin.synced_count", count=len(tickets)))

    c1, c2 = st.columns(2)
    if c1.button(t("snlogin.sync_button"), key="sn_widget_sync", width="stretch"):
        with st.spinner(t("lab.fetching")):
            try:
                fetched = servicenow_poc.fetch_tickets_by_state_aad(
                    token_result["access_token"],
                    states=None,
                    table=servicenow_poc.DEFAULT_TABLE,
                    assignment_groups=None,
                )
                st.session_state["lab_sn_tickets"] = fetched
                st.success(t("lab.fetch_success", count=len(fetched)))
                audit_log.record_event(
                    "servicenow_sync", cws=(st.session_state.get("auth_user") or {}).get("cws", ""),
                    detail=f"Fetched {len(fetched)} tickets from ServiceNow ({servicenow_poc.DEFAULT_TABLE})",
                    category="integration", severity="info",
                )
            except Exception as e:
                st.error(t("lab.fetch_failed", reason=str(e)))
                audit_log.record_event(
                    "servicenow_sync_failed", cws=(st.session_state.get("auth_user") or {}).get("cws", ""),
                    detail=f"ServiceNow ticket fetch failed: {e}",
                    category="integration", severity="error",
                )
    if c2.button(t("lab.sign_out"), key="sn_widget_signout", width="stretch"):
        st.session_state.pop("lab_sn_token_result", None)
        st.session_state.pop("lab_sn_device_flow", None)
        st.session_state.pop("lab_sn_tickets", None)
        st.rerun()


def _render_signin_form() -> None:
    tenant_id = servicenow_azure_ad.AZURE_TENANT_ID
    client_id = servicenow_azure_ad.AZURE_CLIENT_ID
    scope = servicenow_azure_ad.SERVICENOW_API_SCOPE
    configured = servicenow_azure_ad.is_configured(tenant_id, client_id, scope)
    if not configured:
        st.info(t("lab.aad_not_configured"), icon="ℹ️")

    flow_state = st.session_state.get("lab_sn_device_flow")
    if flow_state:
        st.info(flow_state["flow"]["message"])
        col_a, col_b = st.columns(2)
        if col_a.button(t("lab.check_signin"), key="sn_widget_check", width="stretch"):
            with st.spinner(t("lab.waiting_signin")):
                try:
                    result = servicenow_azure_ad.complete_device_flow(flow_state)
                    st.session_state["lab_sn_token_result"] = result
                    st.session_state.pop("lab_sn_device_flow", None)
                    st.rerun()
                except Exception as e:
                    st.error(t("lab.signin_failed", reason=str(e)))
        if col_b.button(t("lab.cancel"), key="sn_widget_cancel", width="stretch"):
            st.session_state.pop("lab_sn_device_flow", None)
            st.rerun()
        return

    method = st.radio(
        t("lab.signin_method"),
        options=["device", "browser", "iwa"],
        format_func=lambda k: {
            "browser": t("lab.method_browser"),
            "device": t("lab.method_device"),
            "iwa": t("lab.method_iwa"),
        }[k],
        key="sn_widget_method",
        label_visibility="collapsed",
    )
    st.caption({
        "browser": t("lab.method_browser_help"),
        "device": t("lab.method_device_help"),
        "iwa": t("lab.method_iwa_help"),
    }[method])

    iwa_username = ""
    if method == "iwa":
        iwa_username = st.text_input(
            t("lab.iwa_username"), value=_current_user_email_hint(), key="sn_widget_iwa_user",
        )

    if st.button(
        t("lab.start_signin"), key="sn_widget_start", type="primary",
        disabled=not configured, width="stretch",
    ):
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
