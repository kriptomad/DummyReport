"""
auth/messaging.py
==================
Lightweight internal messaging system between app users (no external
email server involved — this is a self-contained inbox/outbox persisted
to data/messages.json, so it survives restarts just like users.json and
kb_ownership.json).

Lets any registered user send a message to another user by CWS or name
(e.g. to discuss a fix, ask a question about a troubleshoot entry, or
just reach out) without needing an active shipment search or Teams/email.

Encryption
----------
Message content (subject + body) is end-to-end encrypted with hybrid
RSA+AES (see auth/crypto_messaging.py) so that anyone inspecting
data/messages.json — including the person hosting the app — sees only
opaque ciphertext, never the plaintext content, unless they have the
sender's or recipient's own login password (which unlocks their private
key). Because the SENDER also needs to be able to re-read their own sent
messages, the payload is encrypted twice per message: once with the
recipient's public key, once with the sender's own public key.

Sender/recipient CWS themselves are stored in the clear (needed to route
messages to the right inbox) — see the module docstring in
crypto_messaging.py for the full disclosure of this limitation.
"""
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from auth import crypto_messaging
from auth.user_store import get_user, list_users
from utils.safe_json import load_json as _safe_load_json, save_json as _safe_save_json, json_transaction

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
MESSAGES_PATH = os.path.join(DATA_DIR, "messages.json")


def _cws_eq(a: Optional[str], b: Optional[str]) -> bool:
    return (a or "").strip().lower() == (b or "").strip().lower()


def _load() -> List[Dict[str, Any]]:
    # Lock-protected atomic load (see utils/safe_json.py).
    return _safe_load_json(MESSAGES_PATH, default=[])


def _save(messages: List[Dict[str, Any]]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    _safe_save_json(MESSAGES_PATH, messages)


def _next_id(messages: List[Dict[str, Any]]) -> int:
    return (max((m["id"] for m in messages), default=0)) + 1


def search_users(query: str, exclude_cws: Optional[str] = None) -> List[Dict[str, str]]:
    """
    Searches registered users by CWS or name (case-insensitive substring
    match). Returns a list of {"cws": ..., "name": ...} dicts, optionally
    excluding the current user (so you can't message yourself by mistake).
    """
    q = (query or "").strip().lower()
    results = []
    for u in list_users():
        if exclude_cws and _cws_eq(u["cws"], exclude_cws):
            continue
        if not q or q in u["cws"].lower() or q in u.get("name", "").lower():
            results.append({"cws": u["cws"], "name": u.get("name", ""), "email_teams": u.get("email_teams", "")})
    return results


def _encrypt_payload(subject: str, body: str, public_key_pem: str) -> Optional[Dict[str, str]]:
    if not public_key_pem:
        return None
    payload = json.dumps({"subject": subject or "", "body": body or ""})
    return crypto_messaging.encrypt_message(payload, public_key_pem)


def _decrypt_payload(encrypted: Optional[Dict[str, str]], private_key_pem: Optional[str]) -> Optional[Dict[str, str]]:
    if not encrypted or not private_key_pem:
        return None
    plaintext = crypto_messaging.decrypt_message(encrypted, private_key_pem)
    if plaintext is None:
        return None
    try:
        return json.loads(plaintext)
    except (json.JSONDecodeError, TypeError):
        return None


def recipient_ready(to_cws: str) -> bool:
    """
    Returns True if the recipient already has an encryption keypair (i.e.
    has logged in at least once since encrypted messaging was introduced),
    meaning a message sent to them right now WILL be decryptable by them.
    """
    recipient = get_user(to_cws)
    return bool(recipient and recipient.get("public_key"))


def send_message(
    from_cws: str,
    from_name: str,
    to_cws: str,
    to_name: str,
    subject: str,
    body: str,
    related_pattern: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Sends an internal message, end-to-end encrypted. Returns the created
    message dict (encrypted form — as stored, not the plaintext), or None
    if the recipient doesn't have an encryption keypair yet (they need to
    log in at least once first) — in that case nothing is persisted, so we
    never create a message the recipient could never decrypt.
    """
    sender = get_user(from_cws)
    recipient = get_user(to_cws)
    sender_pub = sender.get("public_key") if sender else None
    recipient_pub = recipient.get("public_key") if recipient else None
    if not recipient_pub:
        return None

    # json_transaction holds the lock across the whole
    # load→next-id→append→save cycle so two messages sent at the exact
    # same instant can't be assigned the same id / silently overwrite
    # each other (a plain _load()+_save() pair would only guarantee the
    # file itself isn't corrupted, not that no message gets lost).
    with json_transaction(MESSAGES_PATH, default=[]) as messages:
        msg = {
            "id": _next_id(messages),
            "from_cws": from_cws,
            "from_name": from_name,
            "to_cws": to_cws,
            "to_name": to_name,
            "related_pattern": related_pattern,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "read": False,
            "encrypted": True,
            # Two ciphertexts of the SAME {subject, body} payload: one only the
            # recipient can open, one only the sender can open (so the sender
            # can still read their own Sent items).
            "payload_for_recipient": _encrypt_payload(subject, body, recipient_pub),
            "payload_for_sender": _encrypt_payload(subject, body, sender_pub),
        }
        messages.append(msg)
    return msg


def _decorate(msg: Dict[str, Any], private_key_pem: Optional[str], as_recipient: bool) -> Dict[str, Any]:
    """Adds decrypted 'subject'/'body' fields (best-effort) to a message dict for display."""
    out = dict(msg)
    payload_key = "payload_for_recipient" if as_recipient else "payload_for_sender"
    decrypted = _decrypt_payload(msg.get(payload_key), private_key_pem)
    if decrypted is not None:
        out["subject"] = decrypted.get("subject", "")
        out["body"] = decrypted.get("body", "")
        out["_decrypted"] = True
    else:
        out["subject"] = "🔒 (encrypted — unlock by logging in)"
        out["body"] = "🔒 (encrypted — this message can only be read while logged in as the sender/recipient)"
        out["_decrypted"] = False
    return out


def get_inbox(cws: str, private_key_pem: Optional[str] = None, unread_only: bool = False) -> List[Dict[str, Any]]:
    """Returns messages received by `cws`, most recent first, decrypted if a private key is supplied."""
    messages = [m for m in _load() if _cws_eq(m.get("to_cws"), cws)]
    if unread_only:
        messages = [m for m in messages if not m.get("read")]
    messages = sorted(messages, key=lambda m: m["created_at"], reverse=True)
    return [_decorate(m, private_key_pem, as_recipient=True) for m in messages]


def get_sent(cws: str, private_key_pem: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns messages sent by `cws`, most recent first, decrypted if a private key is supplied."""
    messages = [m for m in _load() if _cws_eq(m.get("from_cws"), cws)]
    messages = sorted(messages, key=lambda m: m["created_at"], reverse=True)
    return [_decorate(m, private_key_pem, as_recipient=False) for m in messages]


def get_unread_count(cws: str) -> int:
    return len([m for m in _load() if _cws_eq(m.get("to_cws"), cws) and not m.get("read")])


def mark_read(msg_id: int, cws: str) -> bool:
    """Marks a message as read — only the recipient can do this."""
    with json_transaction(MESSAGES_PATH, default=[]) as messages:
        for m in messages:
            if m["id"] == msg_id and _cws_eq(m.get("to_cws"), cws):
                m["read"] = True
                return True
    return False


def delete_message(msg_id: int, cws: str) -> bool:
    """
    Deletes a message — either the sender or the recipient may remove
    their own conversation copy (this is a simple 2-party internal
    message, not a shared/broadcast one).
    """
    with json_transaction(MESSAGES_PATH, default=[]) as messages:
        filtered = [
            m for m in messages
            if not (m["id"] == msg_id and (_cws_eq(m.get("to_cws"), cws) or _cws_eq(m.get("from_cws"), cws)))
        ]
        if len(filtered) == len(messages):
            return False
        messages[:] = filtered
        return True
