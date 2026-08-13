"""
integrations/servicenow_browser_session.py
============================================
CONFIRMED DEAD END (kept only as reference/diagnostic tooling) —
"option 5": the idea was to let a REAL human complete the normal Entra
ID / SSO login in an actual browser window (controlled by Playwright),
then capture and persist the resulting authenticated session (cookies)
so subsequent requests could reuse it — no client_id/tenant_id/scope
needed from IT.

RESULT: TESTED AND CONFIRMED NOT VIABLE for this ServiceNow instance.
Even `fetch_tickets_via_browser()` — which runs the Table API call as a
`fetch()` executed INSIDE the actual logged-in page (same cookies, same
origin, same headers/fingerprint as ServiceNow's own UI JavaScript,
the most faithful possible reuse of a captured session) — still gets a
401 "User is not authenticated". Combined with the earlier 401 on the
plain cookie-paste test (option 4, in servicenow_poc.fetch_tickets_by_cookie)
and the domain-matching bug fix (which was verified NOT to be the cause
here), this is conclusive: this org's ServiceNow enforces OAuth2-only
access to the Table API, completely independent of any valid browser
session cookie. Session/cookie-based auth (options 4 and 5) are BOTH
dead ends for this instance — the only real path is Azure AD OAuth2 via
`integrations/servicenow_azure_ad.py` (client_id/tenant_id/scope from
IT), or a dedicated ServiceNow service account (option 2) if IT can
provision one.

WHY THIS WAS EXPERIMENTAL / NEVER PRODUCTION-READY (kept for history)
------------------------------------------------
- The captured session is a real, live, authenticated ServiceNow session
  cookie. If the JSON file this saves to leaks, whoever has it can act as
  the signed-in user in ServiceNow until the session naturally expires.
  It is saved LOCALLY ONLY (gitignored, never uploaded/committed) but
  that is still a real risk to be aware of before relying on this.
- Session cookies expire (typically hours), so this needs periodic
  re-capture (the UI surfaces the file's age and lets you redo it).
- Fragile: any change to the SSO flow (new redirect step, new domain)
  can break the "wait until logged in" detection below.
- This purely reuses whatever access the signed-in human already has in
  the ServiceNow UI — it does not grant any new permission.

HOW IT WORKS
------------
1. `capture_session_interactive()` launches a REAL (visible, non-headless)
   Chromium window via Playwright, navigates to the ServiceNow instance,
   and waits for the user to complete their normal corporate login
   (Entra ID / SSO / MFA, whatever the org already requires) in that
   window. Once the browser lands back on the ServiceNow domain (not the
   login provider anymore), it captures the cookies and also persists a
   full `storage_state` JSON (cookies + localStorage) so it could later
   be reloaded straight into another Playwright browser context if ever
   needed.
2. `load_saved_session()` reads that persisted file back into a plain
   {cookie_name: value} dict, ready to pass into
   `integrations.servicenow_poc.fetch_tickets_by_cookie()`.
3. `session_file_age_minutes()` / `clear_saved_session()` are small
   helpers so the UI can show "captured 12 minutes ago" and offer a
   "forget this session" button.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency, installed on demand
    PLAYWRIGHT_AVAILABLE = False

from integrations.servicenow_poc import SERVICENOW_INSTANCE_URL

# Local-only, gitignored — never committed, never uploaded anywhere. This
# holds a live authenticated session; treat the file itself as a secret.
DEFAULT_STORAGE_PATH = Path(".servicenow_session_state.json")


def _domain_of(instance_url: str) -> str:
    return instance_url.split("//", 1)[-1].split("/")[0]


def _cookies_for_domain(cookies: List[dict], domain: str) -> Dict[str, str]:
    """Matches cookies whose `domain` attribute applies to `domain`
    (e.g. instance "demo.service-now.com"). Browsers commonly set
    ServiceNow's session cookies on the PARENT domain (".service-now.com",
    with a leading dot, meaning "this cookie + all subdomains") rather
    than the exact host — a naive `domain in cookie_domain` substring
    check gets that backwards and silently drops every cookie set that
    way (the real bug that caused every captured/pasted session to look
    empty and produce a 401 "not authenticated" even with a fresh,
    valid, human-completed login)."""
    result: Dict[str, str] = {}
    for c in cookies:
        cookie_domain = (c.get("domain") or "").lstrip(".")
        if not cookie_domain:
            continue
        if domain == cookie_domain or domain.endswith("." + cookie_domain):
            result[c["name"]] = c["value"]
    return result


def capture_session_interactive(
    instance_url: str = SERVICENOW_INSTANCE_URL,
    storage_path: Path = DEFAULT_STORAGE_PATH,
    timeout_seconds: int = 300,
) -> Dict[str, str]:
    """
    Opens a real, visible Chromium window pointed at `instance_url` and
    BLOCKS until the browser navigates back to the ServiceNow domain
    (i.e. the human finished logging in via whatever SSO/MFA flow the
    org enforces) or `timeout_seconds` elapses.

    This is meant to be called from a Streamlit button click — the
    Streamlit script thread will simply pause (showing a spinner) while
    the human interacts with the separate browser window that pops up
    on their own machine. Nothing here talks to the ServiceNow API on
    the human's behalf during the login itself.

    Returns the captured cookies as a plain dict AND writes a full
    Playwright `storage_state` JSON to `storage_path` for reuse (locally
    only — this file is gitignored).
    """
    if not PLAYWRIGHT_AVAILABLE:
        raise RuntimeError(
            "The 'playwright' package (and its Chromium browser) aren't "
            "installed. Run:\n  pip install playwright\n  playwright install chromium"
        )

    domain = _domain_of(instance_url)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(instance_url)
            try:
                # Wait until we're back on the ServiceNow domain and NOT
                # still sitting on a Microsoft/Entra ID login page — i.e.
                # the human finished the SSO dance.
                page.wait_for_url(
                    lambda url: domain in url and "login.microsoftonline.com" not in url,
                    timeout=timeout_seconds * 1000,
                )
            except Exception as e:
                raise RuntimeError(
                    f"Timed out after {timeout_seconds}s waiting for login to "
                    f"finish in the browser window ({e})"
                )
            # Give the app a moment to finish setting all its session
            # cookies after the redirect lands.
            page.wait_for_timeout(1500)
            storage_path.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(storage_path))
            cookies = context.cookies()
        finally:
            browser.close()

    return _cookies_for_domain(cookies, domain)


def load_saved_session(
    storage_path: Path = DEFAULT_STORAGE_PATH,
    instance_url: str = SERVICENOW_INSTANCE_URL,
) -> Dict[str, str]:
    """Reads a previously captured session file back into a plain
    {cookie_name: value} dict, ready for
    `integrations.servicenow_poc.fetch_tickets_by_cookie()`. Returns an
    empty dict if nothing has been captured yet."""
    if not storage_path.exists():
        return {}
    domain = _domain_of(instance_url)
    data = json.loads(storage_path.read_text(encoding="utf-8"))
    return _cookies_for_domain(data.get("cookies", []), domain)


def session_file_age_minutes(storage_path: Path = DEFAULT_STORAGE_PATH) -> Optional[float]:
    """Minutes since the session was last (re)captured, or None if it was
    never captured. Useful for the UI to nudge "this may be stale, redo
    the login capture" once it's been a while (session cookies typically
    expire in hours)."""
    if not storage_path.exists():
        return None
    return (time.time() - storage_path.stat().st_mtime) / 60.0


def clear_saved_session(storage_path: Path = DEFAULT_STORAGE_PATH) -> None:
    """Deletes the locally captured session file (e.g. on explicit
    "forget this session" / sign-out from the lab UI)."""
    if storage_path.exists():
        storage_path.unlink()


def fetch_tickets_via_browser(
    states: Optional[List[str]] = None,
    table: str = "incident",
    assignment_groups: Optional[List[str]] = None,
    assigned_to_me: bool = False,
    limit: int = 100,
    instance_url: str = SERVICENOW_INSTANCE_URL,
    storage_path: Path = DEFAULT_STORAGE_PATH,
) -> List[Dict]:
    """
    STRONGER version of the cookie-extraction approach
    (`integrations.servicenow_poc.fetch_tickets_by_cookie`) — instead of
    lifting cookie name=value pairs out and replaying them through a
    plain `requests.Session()` (which can subtly fail: wrong domain
    matching — the earlier bug — missing a CSRF/user token header some
    instances require, different TLS/HTTP fingerprint than a real
    browser, etc.), this loads the captured session straight into a
    REAL (headless) Chromium context and runs the Table API call via
    `fetch()` executed INSIDE an actual loaded ServiceNow page — i.e.
    exactly the same mechanism ServiceNow's own UI uses internally to
    talk to its own API (same-origin, same cookies, same headers/
    fingerprint). This is the most faithful possible test of whether a
    captured human login session can be reused for API calls at all.

    If this STILL returns 401, that's strong, fairly conclusive
    evidence the org enforces OAuth2-only access to the Table API,
    independent of any valid browser/session cookie — i.e. options
    4 and 5 are both genuinely dead ends and Azure AD OAuth2
    (client_id/tenant_id/scope) really is required.
    """
    if not PLAYWRIGHT_AVAILABLE:
        raise RuntimeError(
            "The 'playwright' package (and its Chromium browser) aren't "
            "installed. Run:\n  pip install playwright\n  playwright install chromium"
        )
    if not storage_path.exists():
        raise RuntimeError("No captured session yet — capture a login first.")

    states = states or ["1", "2", "3", "6", "7", "8"]
    query_parts = [f"stateIN{','.join(states)}"]
    if assignment_groups:
        query_parts.append(f"assignment_groupIN{','.join(assignment_groups)}")
    if assigned_to_me:
        query_parts.append("assigned_to=javascript:gs.getUserID()")

    import urllib.parse
    params = {
        "sysparm_query": "^".join(query_parts),
        "sysparm_limit": str(limit),
        "sysparm_display_value": "true",
    }
    api_url = f"{instance_url}/api/now/table/{table}?{urllib.parse.urlencode(params)}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(storage_state=str(storage_path))
            page = context.new_page()
            # Land on the instance first so the fetch() below is
            # same-origin (uses the page's own cookies/headers exactly
            # like ServiceNow's own UI JavaScript would).
            page.goto(instance_url, wait_until="domcontentloaded", timeout=30000)
            result = page.evaluate(
                """
                async (url) => {
                    const res = await fetch(url, {
                        credentials: 'include',
                        headers: {'Accept': 'application/json'},
                    });
                    const text = await res.text();
                    return {status: res.status, statusText: res.statusText, body: text};
                }
                """,
                api_url,
            )
        finally:
            browser.close()

    if result["status"] < 200 or result["status"] >= 300:
        detail = result["body"][:300]
        try:
            parsed = json.loads(result["body"])
            err = parsed.get("error") or {}
            joined = " — ".join(x for x in [err.get("message"), err.get("detail")] if x)
            if joined:
                detail = joined
        except Exception:
            pass
        raise RuntimeError(f"{result['status']} {result['statusText']}: {detail}")

    data = json.loads(result["body"])
    return data.get("result", [])
