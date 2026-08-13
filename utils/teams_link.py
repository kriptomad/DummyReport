"""
utils/teams_link.py
====================
Builds a Microsoft Teams deep link that opens a 1:1 chat with a person,
identified by their e-mail address, and optionally pre-fills a message —
so a single click takes you straight into a Teams conversation with that
person instead of having to search for them yourself.

This works because Teams resolves the `users=` parameter against Azure AD /
Exchange e-mail addresses; it does NOT require any Microsoft Graph API
credentials or app registration — it's just a URL scheme Teams recognizes
(opens the desktop app if installed, otherwise teams.microsoft.com in the
browser). See:
https://learn.microsoft.com/microsoftteams/platform/concepts/build-and-test/deep-link-application
"""
from urllib.parse import quote

TEAMS_CHAT_BASE = "https://teams.microsoft.com/l/chat/0/0"


def teams_chat_link(email: str, message: str = "") -> str:
    """Returns a Teams deep link that opens (or starts) a 1:1 chat with
    `email`, with `message` (optional) pre-filled into the compose box."""
    email = (email or "").strip()
    url = f"{TEAMS_CHAT_BASE}?users={quote(email)}"
    if message:
        url += f"&message={quote(message)}"
    return url
