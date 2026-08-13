"""
integrations/servicenow_azure_ad.py
=====================================
PROOF OF CONCEPT — Azure AD (Microsoft Entra ID) login for the ServiceNow
integration, since this org's ServiceNow instance is configured to
authenticate via the corporate Microsoft account (SSO) instead of a
native ServiceNow username/password.

This replaces the Basic Auth approach in `servicenow_poc.py` with a real
Azure AD sign-in per user, using Microsoft's official `msal` library
(Microsoft Authentication Library for Python) — the same library
Microsoft itself recommends for any first-party Azure AD integration.

HOW IT WORKS
------------
Each user authenticates individually with their own Microsoft/Entra ID
account (their Acme Logistics e-mail + corporate password/MFA) via the
"device code" flow: the app prints a short code + a microsoft.com URL,
the user opens it in any browser (already logged into their corporate
Microsoft account, so usually just one click + MFA if needed), and MSAL
receives back an access token for that specific user — never touching or
storing their password.

That access token is then sent as a Bearer token to ServiceNow's REST
API, exactly like `servicenow_poc.fetch_tickets_by_state()` does with
Basic Auth today, just swapping the Authorization header.

WHAT MUST BE CONFIRMED BEFORE THIS CAN ACTUALLY RUN
-----------------------------------------------------
This is the real blocker — none of this can be tested end-to-end without
these three things from your Azure AD / ServiceNow admin team:

1. **AZURE_TENANT_ID** — the Acme Logistics Azure AD tenant ID (GUID) or
   domain (e.g. "example.onmicrosoft.com" or the actual tenant GUID).

2. **AZURE_CLIENT_ID** — an App Registration in Azure AD for THIS
   integration. If ServiceNow's own Azure AD SSO integration already
   exists (which it must, since your ServiceNow login screen redirects
   to Microsoft), there are two paths:
     a) Reuse ServiceNow's existing Azure AD Enterprise Application by
        requesting its Application (client) ID from your IT/ServiceNow
        admin team, if it allows a "public client" (no secret) native
        app to request tokens on its behalf — needs to be explicitly
        confirmed/allowed by whoever manages that App Registration.
     b) Register a brand-new, dedicated App Registration for this
        Streamlit tool (simpler to get right, keeps this app's access
        auditable/separate from ServiceNow's own SSO app), then have
        ServiceNow's admin configure it as a trusted client for its API
        (or issue API access via an OAuth inbound provider record in
        ServiceNow pointing at this same Azure AD app).
   Either way, this step requires a Acme Logistics Azure AD admin — it is
   NOT something that can be guessed or bypassed from code.

3. **SERVICENOW_API_SCOPE** — the OAuth scope / Application ID URI that
   Azure AD needs to mint a token ServiceNow will actually accept as
   valid (e.g. "api://<servicenow-app-id-in-azure>/.default" or a
   specific exposed scope like "api://<id>/user_impersonation"). This
   comes from how ServiceNow's "Multi-Provider SSO" / OIDC plugin is
   configured on the ServiceNow side.

Everything below is fully functional Python/MSAL code — it is NOT
placeholder logic — but the three constants above are placeholders
because they're specific to Acme Logistics's real Azure AD tenant and must
be provided by an admin. Fill them in (ideally via environment variables
or `.streamlit/secrets.toml`, never hardcoded) and this becomes usable
as-is.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

try:
    import msal
    MSAL_AVAILABLE = True
except ImportError:  # pragma: no cover - POC only
    MSAL_AVAILABLE = False

# ── Placeholders — MUST be provided by a Acme Logistics Azure AD admin ─────
# Never hardcode real values here; read from environment variables /
# .streamlit/secrets.toml, matching how this repo already handles other
# provider API keys (see troubleshooter/feedback_store.py's OPENAI_API_KEY
# / ANTHROPIC_API_KEY / GEMINI_API_KEY pattern).
#
# TENANT ID: confirmed real value, captured from the SAML SSO redirect URL
# ServiceNow's login screen sends the browser to
# (https://login.microsoftonline.com/<TENANT_ID>/saml2?...) — the tenant
# GUID is exposed right there in the URL path, so unlike client_id/scope it
# does NOT need to be requested from IT. IMPORTANT CAVEAT: that URL is a
# SAML2 request (used for the browser UI login), a different protocol from
# the OAuth2/OIDC flow this module (via msal) uses for API access. Knowing
# the tenant is correct either way (tenant ID doesn't change per protocol),
# but it does NOT confirm an OAuth2 App Registration exists for API access
# — client_id/scope below still must come from whoever manages that
# (possibly nobody yet, if only SAML/UI SSO was ever configured).
AZURE_TENANT_ID = os.getenv("SERVICENOW_AAD_TENANT_ID", "11111111-2222-3333-4444-555555555555")
AZURE_CLIENT_ID = os.getenv("SERVICENOW_AAD_CLIENT_ID", "")
SERVICENOW_API_SCOPE = os.getenv("SERVICENOW_AAD_SCOPE", "")

AUTHORITY_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}"


def is_configured(tenant_id: str = "", client_id: str = "", scope: str = "") -> bool:
    """True once the three required Azure AD values are available — either
    passed explicitly (e.g. typed into the Lab Test tab for a quick trial)
    or via the SERVICENOW_AAD_* environment variables. Callers should
    check this before attempting any sign-in and show a clear "not
    configured yet" message otherwise, rather than a confusing MSAL
    error."""
    tenant_id = tenant_id or AZURE_TENANT_ID
    client_id = client_id or AZURE_CLIENT_ID
    scope = scope or SERVICENOW_API_SCOPE
    return bool(tenant_id and client_id and scope)


def _build_app(
    client_id: str,
    tenant_id: str,
    cache: Optional["msal.SerializableTokenCache"] = None,
) -> "msal.PublicClientApplication":
    if not MSAL_AVAILABLE:
        raise RuntimeError(
            "The 'msal' package isn't installed. Run "
            "`pip install msal` (already added to requirements.txt) first."
        )
    authority = AUTHORITY_TEMPLATE.format(tenant_id=tenant_id)
    return msal.PublicClientApplication(
        client_id=client_id,
        authority=authority,
        token_cache=cache,
    )


def start_device_flow(
    tenant_id: str = "",
    client_id: str = "",
    scope: str = "",
) -> Dict[str, Any]:
    """
    Non-blocking first half of the sign-in: asks Azure AD for a device
    code + verification URL for the user to open in a browser. Doesn't
    wait for the user to actually finish signing in — pair with
    `complete_device_flow()` right after showing the returned
    `flow["message"]` to the user, so a UI (like the Lab Test tab) can
    display the code immediately instead of freezing while waiting.

    `tenant_id`/`client_id`/`scope` override the SERVICENOW_AAD_* env
    vars, for quickly trying different values from a UI without
    restarting the app.
    """
    tenant_id = tenant_id or AZURE_TENANT_ID
    client_id = client_id or AZURE_CLIENT_ID
    scope = scope or SERVICENOW_API_SCOPE
    if not is_configured(tenant_id, client_id, scope):
        raise RuntimeError(
            "Azure AD isn't configured yet. Provide SERVICENOW_AAD_TENANT_ID, "
            "SERVICENOW_AAD_CLIENT_ID and SERVICENOW_AAD_SCOPE (env vars, "
            ".streamlit/secrets.toml, or typed directly into the Lab Test "
            "tab) — these must come from your Azure AD / ServiceNow admin "
            "team. See this module's docstring for what each one means."
        )

    app = _build_app(client_id, tenant_id)
    flow = app.initiate_device_flow(scopes=[scope])
    if "user_code" not in flow:
        raise RuntimeError(f"Failed to start Azure AD device flow: {flow}")
    return {"app": app, "flow": flow}


def complete_device_flow(started: Dict[str, Any]) -> Dict[str, Any]:
    """
    Blocking second half: waits for the user to finish signing in via the
    browser (polls Azure AD until they do, or the code expires — usually
    ~15 minutes). Call this right after `start_device_flow()`, once its
    `flow["message"]` has already been shown to the user.

    Returns the MSAL token result dict on success: {"access_token": ...,
    "expires_in": ..., "id_token_claims": {"preferred_username": "<their
    corporate e-mail>", ...}, ...} — the caller passes
    result["access_token"] as a Bearer token to ServiceNow (see
    servicenow_poc.fetch_tickets_by_state_aad).
    """
    app = started["app"]
    flow = started["flow"]
    result = app.acquire_token_by_device_flow(flow)  # blocks until user signs in
    if "access_token" not in result:
        raise RuntimeError(
            f"Azure AD sign-in failed: {result.get('error')} — "
            f"{result.get('error_description')}"
        )
    return result


def login_device_flow(
    cache: Optional["msal.SerializableTokenCache"] = None,
    tenant_id: str = "",
    client_id: str = "",
    scope: str = "",
) -> Dict[str, Any]:
    """
    Starts an interactive Azure AD sign-in for ONE individual user using
    the "device code" flow: prints a short code + a microsoft.com URL the
    user opens in any browser to sign in with their own Acme Logistics
    Microsoft account (supports MFA/Conditional Access transparently,
    since it's a real browser sign-in, not a password sent by this app).

    Blocks until the user completes sign-in in their browser (or the code
    expires — typically ~15 minutes). For a UI that needs to show the
    code without freezing, use `start_device_flow()` +
    `complete_device_flow()` instead.

    Returns the MSAL token result dict on success — see
    `complete_device_flow()`'s docstring for its shape.

    Raises RuntimeError if the required Azure AD values (tenant/client
    id/scope) haven't been configured yet — see module docstring.
    """
    tenant_id = tenant_id or AZURE_TENANT_ID
    client_id = client_id or AZURE_CLIENT_ID
    scope = scope or SERVICENOW_API_SCOPE
    if not is_configured(tenant_id, client_id, scope):
        raise RuntimeError(
            "Azure AD isn't configured yet. Set SERVICENOW_AAD_TENANT_ID, "
            "SERVICENOW_AAD_CLIENT_ID and SERVICENOW_AAD_SCOPE (env vars "
            "or .streamlit/secrets.toml) — these must come from your "
            "Azure AD / ServiceNow admin team. See this module's "
            "docstring for what each one means and how to get them."
        )

    app = _build_app(client_id, tenant_id, cache)
    scopes = [scope]

    # Reuse a cached/refreshed token silently if this user already signed
    # in recently in this session, instead of prompting again every time.
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(scopes, account=accounts[0])
        if result:
            return result

    flow = app.initiate_device_flow(scopes=scopes)
    if "user_code" not in flow:
        raise RuntimeError(f"Failed to start Azure AD device flow: {flow}")

    print(flow["message"])  # e.g. "To sign in, use a web browser to open
    # the page https://microsoft.com/devicelogin and enter the code
    # ABCD-EFGH to authenticate."

    result = app.acquire_token_by_device_flow(flow)  # blocks until user signs in
    if "access_token" not in result:
        raise RuntimeError(
            f"Azure AD sign-in failed: {result.get('error')} — "
            f"{result.get('error_description')}"
        )
    return result


def get_signed_in_email(token_result: Dict[str, Any]) -> str:
    """Convenience accessor for the signed-in user's Microsoft e-mail,
    to display/log which corporate account is being used (never log the
    access token itself)."""
    claims = token_result.get("id_token_claims") or {}
    return claims.get("preferred_username") or claims.get("upn") or ""


def login_interactive_browser(
    tenant_id: str = "",
    client_id: str = "",
    scope: str = "",
) -> Dict[str, Any]:
    """
    "SSO" sign-in: opens the machine's default web browser straight to
    the Microsoft login page instead of showing a device code to type
    in manually. On a Acme Logistics corporate machine that's already
    signed into Microsoft 365 in that browser (the normal case), this
    is usually a single click on the already-listed account (or fully
    silent if the browser session is fresh) — no code copying needed.

    IMPORTANT — this still needs the exact same 3 Azure AD values as the
    device code flow (tenant/client id/scope); "SSO" here is about a
    smoother sign-in *experience* (reusing the browser's existing
    Microsoft session), not a way to skip the Azure AD app registration
    requirement. It ALSO needs one extra thing configured on the Azure
    AD App Registration itself: a "Mobile and desktop applications"
    platform redirect URI of `http://localhost` (a checkbox + one URI
    field in the Azure Portal, takes the admin 30 seconds when creating
    the app) — the device code flow doesn't need this, which is why it
    was implemented first as the zero-extra-setup option.

    Blocks until the browser flow completes (or the user closes the
    window without finishing).
    """
    tenant_id = tenant_id or AZURE_TENANT_ID
    client_id = client_id or AZURE_CLIENT_ID
    scope = scope or SERVICENOW_API_SCOPE
    if not is_configured(tenant_id, client_id, scope):
        raise RuntimeError(
            "Azure AD isn't configured yet. Provide SERVICENOW_AAD_TENANT_ID, "
            "SERVICENOW_AAD_CLIENT_ID and SERVICENOW_AAD_SCOPE — see this "
            "module's docstring."
        )

    app = _build_app(client_id, tenant_id)
    scopes = [scope]

    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(scopes, account=accounts[0])
        if result:
            return result

    result = app.acquire_token_interactive(scopes=scopes)
    if "access_token" not in result:
        raise RuntimeError(
            f"Azure AD sign-in failed: {result.get('error')} — "
            f"{result.get('error_description')}"
        )
    return result


def login_windows_integrated(
    tenant_id: str = "",
    client_id: str = "",
    scope: str = "",
    username: str = "",
) -> Dict[str, Any]:
    """
    Fully silent "SSO" sign-in using Integrated Windows Authentication
    (IWA): reuses the Kerberos ticket from the user's already-logged-in
    Windows session — no browser window, no code, no password prompt at
    all, IF it works. This is the closest thing to true zero-click SSO.

    CAVEAT (important): this only works when the Acme Logistics Azure AD
    tenant has "Seamless SSO" (or a federated setup like ADFS) enabled
    for domain-joined/Entra-joined machines — that's an org-wide Azure AD
    setting only IT can turn on, not something this app controls. If it
    isn't enabled, this call will simply fail/prompt nothing useful — in
    that case, use `login_interactive_browser()` (one click) or
    `login_device_flow()` (device code) instead, both of which work
    regardless of Seamless SSO being configured.

    `username` (the Acme Logistics e-mail) is required — Kerberos needs to
    know which account's ticket to use.
    """
    tenant_id = tenant_id or AZURE_TENANT_ID
    client_id = client_id or AZURE_CLIENT_ID
    scope = scope or SERVICENOW_API_SCOPE
    if not is_configured(tenant_id, client_id, scope):
        raise RuntimeError(
            "Azure AD isn't configured yet. Provide SERVICENOW_AAD_TENANT_ID, "
            "SERVICENOW_AAD_CLIENT_ID and SERVICENOW_AAD_SCOPE — see this "
            "module's docstring."
        )
    if not username:
        raise RuntimeError("Integrated Windows Auth needs your Acme Logistics e-mail (username).")

    app = _build_app(client_id, tenant_id)
    result = app.acquire_token_by_integrated_windows_auth(scopes=[scope], username=username)
    if "access_token" not in result:
        raise RuntimeError(
            f"Integrated Windows Auth failed (this usually just means "
            f"Seamless SSO isn't enabled for this tenant — try "
            f"'Interactive browser' instead): {result.get('error')} — "
            f"{result.get('error_description')}"
        )
    return result


if __name__ == "__main__":
    if not is_configured():
        print(
            "Azure AD not configured yet — this is expected out of the "
            "box. Before this can actually sign anyone in, an Azure AD "
            "admin needs to provide 3 values (see this file's docstring "
            "for exactly what each one means and why):\n\n"
            "  SERVICENOW_AAD_TENANT_ID   = <Acme Logistics Azure AD tenant id>\n"
            "  SERVICENOW_AAD_CLIENT_ID   = <App Registration client id>\n"
            "  SERVICENOW_AAD_SCOPE       = <ServiceNow API scope / Application ID URI>\n\n"
            "Once set (as environment variables or in "
            ".streamlit/secrets.toml), run this file again to try a real "
            "interactive sign-in."
        )
    else:
        result = login_device_flow()
        print(f"Signed in as: {get_signed_in_email(result)}")
        print("Access token acquired (not printed — treat it like a password).")

