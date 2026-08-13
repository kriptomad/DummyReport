"""
integrations/servicenow_poc.py
================================
PROOF OF CONCEPT ONLY — not wired into app.py, not imported by anything in
the running app. This exists purely to demonstrate that a future
ServiceNow integration is technically feasible using plain Python + the
ServiceNow REST "Table API", answering the question: "é possível conectar
ao Service Now e utilizar o login individual de cada pessoa, buscar
tickets fechados, cancelados, abertos e resolvidos?"

Instance (from the user): https://demo.service-now.com

UPDATE — this instance requires Azure AD (Microsoft Entra ID) SSO: the
user confirmed the ServiceNow login screen asks for the corporate
Microsoft e-mail instead of a native ServiceNow password. Use
`fetch_tickets_by_state_aad()` below (paired with
`integrations/servicenow_azure_ad.py` for the actual Microsoft sign-in)
— NOT `fetch_tickets_by_state()` (Basic Auth), which only applies to a
ServiceNow instance without SSO enabled.

WHAT THIS DEMONSTRATES
----------------------
1. Connecting to ServiceNow from Python needs nothing exotic — the Table
   API is plain HTTPS + JSON, reachable with `requests` (already a
   transitive dependency via other libs in this project; add it to
   requirements.txt explicitly if this ever gets productionized).
2. "Login individual de cada pessoa" (per-user login) — for THIS org,
   that means each user signs in with their own Microsoft/Entra ID
   account (Azure AD), not a ServiceNow-native password. See
   `integrations/servicenow_azure_ad.py` for the full sign-in flow
   (device code flow via `msal`) — it returns an access token that
   `fetch_tickets_by_state_aad()` sends as a Bearer token. A plain Basic
   Auth path (`fetch_tickets_by_state()`) is kept in this file too, only
   as a reference for a hypothetical non-SSO ServiceNow instance.
3. Filtering by ticket state (open / resolved / closed / cancelled) is a
   single query-string filter (`sysparm_query=state=...`) against
   whichever table the tickets actually live in (commonly `incident` for
   INC records, but this org may use a custom `case`/`sc_request` table —
   THIS MUST BE CONFIRMED with the ServiceNow admin before real use).

WHAT IS INTENTIONALLY *NOT* DONE HERE (needs a real decision first)
--------------------------------------------------------------------
- No real credentials, table names, or state-value mappings are
  hardcoded — the state codes below are ServiceNow's common defaults
  (1=New, 2=In Progress, 6=Resolved, 7=Closed, 8=Cancelled) but every
  instance can customize these; must be verified against the real
  instance's `sys_choice` records for whichever table is used.
- Not imported/wired into app.py, auth/, or any UI — this is a standalone
  script an admin/developer can run manually to validate connectivity
  once real credentials + table name are available.
- No token/password persistence — a real integration would need to
  reuse this repo's existing encrypted-storage pattern (see
  auth/user_store.py's credential handling) rather than anything here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover - POC only, not a hard dependency yet
    requests = None

# Confirmed instance base URL (from the user). Everything else below is a
# placeholder until confirmed against the real ServiceNow configuration.
SERVICENOW_INSTANCE_URL = "https://demo.service-now.com"

# CONFIRMED (from a real ServiceNow classic UI list link the user shared:
# .../incident_list.do?sysparm_query=assignment_group=22e54378db5c5f040175f1771d9619d2^state!=6^state!=7^state!=8):
#   - Table really used day-to-day is `incident`, not the generic `task`.
#   - The real workflow filters by TEAM assignment_group(s) (everyone's
#     tickets), not `assigned_to=me` (just the individual's own).
#   - The state codes excluded there (6/7/8) are exactly Resolved/Closed/
#     Cancelled — confirms INCIDENT_STATE_LABELS below was already correct.
# NOTE: that specific sys_id was only an EXAMPLE the user shared to show
# the URL format — it is NOT hardcoded as a default anywhere below. Real
# usage needs to support searching for ANY number of groups BY NAME (via
# search_assignment_groups_aad()) rather than typing a raw sys_id, since
# nobody memorizes those GUIDs and there can be several relevant teams.
DEFAULT_TABLE = "incident"

# ServiceNow's common OOTB state codes for the base `task` table — MUST
# be verified for this instance (Settings > System Definition > Choice
# Lists > table "task", field "state", or ask the ServiceNow admin team)
# since orgs frequently customize these. The `incident` table specifically
# often uses a different set (see INCIDENT_STATE_LABELS below).
STATE_LABELS = {
    "-5": "Pending",
    "1": "Open",
    "2": "Work in Progress",
    "3": "Closed Complete",
    "4": "Closed Incomplete",
    "7": "Closed Skipped",
}

# Common OOTB state codes specifically for the `incident` table — CONFIRMED
# correct for this instance (6/7/8 = Resolved/Closed/Cancelled matches the
# real ServiceNow link the user shared, see DEFAULT_TABLE note above).
INCIDENT_STATE_LABELS = {
    "1": "New",
    "2": "In Progress",
    "3": "On Hold",
    "6": "Resolved",
    "7": "Closed",
    "8": "Cancelled",
}


@dataclass
class ServiceNowCredentials:
    """Per-user credentials for Basic Auth. In a real rollout, `password`
    should never be stored in plaintext — reuse this repo's existing
    encrypted-credential pattern (see auth/user_store.py) instead of a
    bare dataclass field like this POC does."""
    username: str
    password: str


def _auth_headers(creds: ServiceNowCredentials) -> Dict[str, str]:
    """Basic Auth headers — kept for reference/instances where ServiceNow
    still allows native login. THIS ORG'S INSTANCE REQUIRES AZURE AD SSO
    instead (confirmed by the user: the ServiceNow login screen redirects
    to the corporate Microsoft account) — use
    `_bearer_headers()` + `integrations.servicenow_azure_ad.login_device_flow()`
    for that, not this function."""
    import base64
    token = base64.b64encode(f"{creds.username}:{creds.password}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
    }


def _bearer_headers(access_token: str) -> Dict[str, str]:
    """Auth headers for an Azure AD-issued access token (this org's real
    path — see integrations/servicenow_azure_ad.py for how to obtain
    `access_token` via each user's own Microsoft sign-in)."""
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }


def _raise_for_status_with_detail(response) -> None:
    """Like response.raise_for_status(), but includes ServiceNow's own
    error detail from the JSON body (e.g. {"error": {"message": "...",
    "detail": "..."}}) in the exception message — the bare HTTP status
    alone (e.g. "401 Unauthorized") doesn't say WHY, and ServiceNow
    usually explains (e.g. "User Not Authenticated", "Required to provide
    Auth information", insufficient rights, etc.)."""
    if response.ok:
        return
    detail = ""
    try:
        body = response.json()
        err = body.get("error") or {}
        detail = " — ".join(x for x in [err.get("message"), err.get("detail")] if x)
    except Exception:
        detail = (response.text or "")[:300]
    reason = f"{response.status_code} {response.reason}"
    if detail:
        reason = f"{reason}: {detail}"
    raise RuntimeError(reason)


def _query_tickets(
    headers: Dict[str, str],
    states: Optional[List[str]],
    table: str,
    assigned_to_me: bool,
    limit: int,
    instance_url: str,
    assignment_groups: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    if requests is None:
        raise RuntimeError(
            "The 'requests' package isn't installed. Add it to "
            "requirements.txt (`pip install requests`) before running "
            "this POC for real."
        )

    states = states or list(STATE_LABELS.keys())
    query_parts = [f"stateIN{','.join(states)}"]
    # Real confirmed usage (from the user's actual ServiceNow list link) is
    # filtering by TEAM assignment_group(s) — everyone's tickets, not just
    # the signed-in user's own. Supports any number of groups at once
    # (assignment_groupIN<sys_id1>,<sys_id2>,...) since a user may need to
    # watch several teams' queues, not just one. `assigned_to_me` is kept
    # as an alternative/narrower filter, combinable with the group filter.
    if assignment_groups:
        query_parts.append(f"assignment_groupIN{','.join(assignment_groups)}")
    if assigned_to_me:
        query_parts.append("assigned_to=javascript:gs.getUserID()")

    url = f"{instance_url}/api/now/table/{table}"
    params = {
        "sysparm_query": "^".join(query_parts),
        "sysparm_limit": str(limit),
        "sysparm_display_value": "true",
    }

    response = requests.get(url, headers=headers, params=params, timeout=30)
    _raise_for_status_with_detail(response)
    return response.json().get("result", [])


def search_assignment_groups_aad(
    name_query: str,
    access_token: str,
    limit: int = 20,
    instance_url: str = SERVICENOW_INSTANCE_URL,
) -> List[Dict[str, str]]:
    """
    Looks up assignment groups BY NAME (e.g. "Shipment Audit", "Freight
    Ops") instead of requiring the raw sys_id GUID — queries ServiceNow's
    `sys_user_group` table (where every group's name <-> sys_id mapping
    lives) with a case-insensitive "contains" filter. Returns a list of
    {"sys_id": ..., "name": ...} dicts for the caller to let the user pick
    from (supports finding/selecting several groups at once).
    """
    if requests is None:
        raise RuntimeError(
            "The 'requests' package isn't installed. Add it to "
            "requirements.txt (`pip install requests`) before running "
            "this POC for real."
        )
    name_query = (name_query or "").strip()
    if not name_query:
        return []

    url = f"{instance_url}/api/now/table/sys_user_group"
    params = {
        "sysparm_query": f"nameLIKE{name_query}^ORDERBYname",
        "sysparm_limit": str(limit),
        "sysparm_fields": "sys_id,name",
        "sysparm_display_value": "true",
    }
    response = requests.get(url, headers=_bearer_headers(access_token), params=params, timeout=30)
    _raise_for_status_with_detail(response)
    return [
        {"sys_id": row.get("sys_id", ""), "name": row.get("name", "")}
        for row in response.json().get("result", [])
    ]


def fetch_tickets_by_state(
    creds: ServiceNowCredentials,
    states: Optional[List[str]] = None,
    table: str = DEFAULT_TABLE,
    assigned_to_me: bool = False,
    assignment_groups: Optional[List[str]] = None,
    limit: int = 100,
    instance_url: str = SERVICENOW_INSTANCE_URL,
) -> List[Dict[str, Any]]:
    """
    Fetches tickets from ServiceNow's Table API using Basic Auth (native
    ServiceNow username/password). NOT applicable to this org — its
    ServiceNow instance requires Azure AD SSO instead (the login screen
    redirects to the corporate Microsoft account), so use
    `fetch_tickets_by_state_aad()` below instead. Kept here for reference
    / in case a different ServiceNow instance without SSO is ever needed.

    `states` defaults to all statuses. Pass ServiceNow's raw state codes
    (see STATE_LABELS/INCIDENT_STATE_LABELS).

    `assignment_groups` — list of one or more assignment_group sys_ids
    (find them by name first via search_assignment_groups_aad(), never
    type the raw GUID by hand). Matches the actual ServiceNow list view
    link the user uses day-to-day (team's tickets, not just their own).
    Set `assigned_to_me=True` (optionally combined with assignment_groups,
    or with assignment_groups=None) to narrow to only the signed-in
    user's own tickets instead.

    Returns the raw list of ticket dicts from ServiceNow's JSON response
    (field selection/formatting is intentionally left to the caller since
    the real field names needed depend on the actual table schema).
    """
    return _query_tickets(
        _auth_headers(creds), states, table, assigned_to_me, limit, instance_url,
        assignment_groups=assignment_groups,
    )


def fetch_tickets_by_state_aad(
    access_token: str,
    states: Optional[List[str]] = None,
    table: str = DEFAULT_TABLE,
    assigned_to_me: bool = False,
    assignment_groups: Optional[List[str]] = None,
    limit: int = 100,
    instance_url: str = SERVICENOW_INSTANCE_URL,
) -> List[Dict[str, Any]]:
    """
    Same as fetch_tickets_by_state(), but authenticates with an Azure AD
    (Microsoft Entra ID) access token instead of a ServiceNow
    username/password — THIS is the path for this org, since its
    ServiceNow instance requires signing in with the corporate Microsoft
    e-mail.

    `access_token` comes from
    `integrations.servicenow_azure_ad.login_device_flow()["access_token"]`
    — one individual interactive Microsoft sign-in per user, per this
    module's docstring for what Azure AD setup is needed first.
    """
    return _query_tickets(
        _bearer_headers(access_token), states, table, assigned_to_me, limit, instance_url,
        assignment_groups=assignment_groups,
    )


def fetch_ticket_by_number_aad(
    ticket_number: str,
    access_token: str,
    table: str = DEFAULT_TABLE,
    instance_url: str = SERVICENOW_INSTANCE_URL,
) -> Optional[Dict[str, Any]]:
    """
    Looks up a SINGLE ticket by its human-readable number (e.g.
    "INC0012345", "TASK0045678") via Azure AD auth — used by the PSLD -
    Parts triage tool so an analyst can paste just the ticket number
    instead of copy/pasting its full description by hand. Returns None
    if not found. `table` defaults to the generic "task" table (covers
    incident/task/case numbers alike); pass "incident" if the org's
    numbers are strictly incidents.
    """
    if requests is None:
        raise RuntimeError(
            "The 'requests' package isn't installed. Add it to "
            "requirements.txt (`pip install requests`) before running "
            "this POC for real."
        )
    ticket_number = (ticket_number or "").strip()
    if not ticket_number:
        return None

    url = f"{instance_url}/api/now/table/{table}"
    params = {
        "sysparm_query": f"number={ticket_number}",
        "sysparm_limit": "1",
        "sysparm_display_value": "true",
    }
    response = requests.get(url, headers=_bearer_headers(access_token), params=params, timeout=30)
    _raise_for_status_with_detail(response)
    results = response.json().get("result", [])
    return results[0] if results else None


def fetch_tickets_by_cookie(
    cookies: Dict[str, str],
    states: Optional[List[str]] = None,
    table: str = DEFAULT_TABLE,
    assigned_to_me: bool = False,
    assignment_groups: Optional[List[str]] = None,
    limit: int = 100,
    instance_url: str = SERVICENOW_INSTANCE_URL,
) -> List[Dict[str, Any]]:
    """
    CONFIRMED DEAD END for this ServiceNow instance (kept only as
    reference/diagnostic tooling) — reuses an already logged-in browser
    session's cookies (e.g. `JSESSIONID`, `glide_user_route`,
    `glide_session_store`) instead of any real auth flow.

    RESULT: tested with a genuinely valid, freshly captured, human-
    completed login session (see integrations/servicenow_browser_session.py)
    — including the strongest possible variant, running the same API call
    as a `fetch()` executed INSIDE the actual logged-in page (same
    cookies/origin/headers as ServiceNow's own UI) — and it STILL returns
    401 "User is not authenticated". This conclusively confirms this org's
    ServiceNow enforces OAuth2-only access to the Table API, regardless of
    any valid browser/session cookie. Do not spend more time on cookie-
    based approaches (options 4 and 5) for this instance — use
    `fetch_tickets_by_state_aad()` (Azure AD OAuth2) instead, or a
    dedicated ServiceNow service account (option 2) if IT can provision
    one.
    
    Why this is only good for a manual, one-off test and NOT a real
    integration path:
      - Session cookies typically expire in minutes/hours, so this can't
        be relied on for any automated/background job — a human has to
        keep re-copying fresh cookie values.
      - Fragile: tied to the exact browser session (IP, user-agent, etc.)
        that created it; can be invalidated at any time.
      - Likely against corporate security policy to lift and reuse
        session tokens outside the normal login flow, even from your own
        session — treat this purely as a quick diagnostic, not something
        to build on.

    How to get the cookie values to pass in `cookies` (Chrome/Edge):
      1. Log into https://demo.service-now.com normally in the browser.
      2. Press F12 -> Application tab -> Cookies -> the demo.service-now.com
         entry.
      3. Copy the values for cookies whose name starts with `glide_` and/or
         `JSESSIONID` into a dict, e.g.
         {"JSESSIONID": "...", "glide_user_route": "..."}.
    """
    if requests is None:
        raise RuntimeError(
            "The 'requests' package isn't installed. Add it to "
            "requirements.txt (`pip install requests`) before running "
            "this POC for real."
        )
    session = requests.Session()
    for name, value in cookies.items():
        session.cookies.set(name, value, domain=instance_url.split("//", 1)[-1])

    states = states or list(STATE_LABELS.keys())
    query_parts = [f"stateIN{','.join(states)}"]
    if assignment_groups:
        query_parts.append(f"assignment_groupIN{','.join(assignment_groups)}")
    if assigned_to_me:
        query_parts.append("assigned_to=javascript:gs.getUserID()")

    url = f"{instance_url}/api/now/table/{table}"
    params = {
        "sysparm_query": "^".join(query_parts),
        "sysparm_limit": str(limit),
        "sysparm_display_value": "true",
    }
    response = session.get(url, params=params, timeout=30, headers={"Accept": "application/json"})
    _raise_for_status_with_detail(response)
    return response.json().get("result", [])


if __name__ == "__main__":
    print(
        "This is a proof-of-concept module only — it is not wired into "
        "the running app. This org's ServiceNow requires Azure AD SSO, "
        "so the real path is:\n\n"
        "  1. Get an Azure AD access token for the signed-in user — see "
        "integrations/servicenow_azure_ad.py (needs SERVICENOW_AAD_TENANT_ID / "
        "SERVICENOW_AAD_CLIENT_ID / SERVICENOW_AAD_SCOPE from your Azure AD "
        "admin first).\n"
        "  2. Confirm the real ticket table name (may not be 'incident').\n"
        "  3. Confirm the real state codes for that table.\n\n"
        "Example (once the above is known):\n\n"
        "    from integrations.servicenow_azure_ad import login_device_flow\n"
        "    from integrations.servicenow_poc import fetch_tickets_by_state_aad\n"
        "    token_result = login_device_flow()  # opens a Microsoft sign-in prompt\n"
        "    tickets = fetch_tickets_by_state_aad(token_result['access_token'])\n"
        "    print(tickets)\n"
    )
