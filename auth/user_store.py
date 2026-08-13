"""
auth/user_store.py
===================
Basic internal user registration/authentication for the DummyReport app.

Design goals (per requirements):
- Local-only, no external identity provider — this app runs internally.
- Users register with: Nome, CWS (unique login id), Senha (>=8 chars +
  special char), E-mail Teams, Cargo.
- Passwords are NEVER stored in plain text: PBKDF2-HMAC-SHA256 with a random
  per-user salt (stdlib only, no extra dependency required).
- Persisted to a JSON file on disk (data/users.json) so it survives restarts
  and is NOT cleared like a browser/streamlit cache would be.
"""
import hashlib
import os
import re
import secrets
import time
from datetime import datetime
from typing import Optional, Tuple, Dict, Any, List

from auth import crypto_messaging
from config import app_settings
from i18n import t
from utils.safe_json import load_json as _safe_load_json, save_json as _safe_save_json, json_transaction

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
USERS_PATH = os.path.join(DATA_DIR, "users.json")

PBKDF2_ITERATIONS = 200_000
SPECIAL_CHARS = set("!@#$%^&*()_+-=[]{}|;:,.<>?/~`\"'\\")

# ── Basic login rate-limiting (in-process, server-wide) ─────────────────
# Not a substitute for a real WAF/IDS, but stops trivial online password
# guessing against a single CWS: after MAX_FAILED_ATTEMPTS wrong passwords
# within FAILURE_WINDOW_SECONDS, that CWS is locked out for LOCKOUT_SECONDS.
# State is process-memory only (module-level dict) — resets on app
# restart, which is fine for this internal, low-traffic tool.
_MAX_FAILED_ATTEMPTS = 5
_FAILURE_WINDOW_SECONDS = 5 * 60
_LOCKOUT_SECONDS = 5 * 60
_failed_attempts: Dict[str, List[float]] = {}
_lockout_until: Dict[str, float] = {}


def _login_key(cws: str) -> str:
    return (cws or "").strip().lower()


def _is_locked_out(cws: str) -> Optional[float]:
    """Returns remaining lockout seconds if `cws` is currently locked out, else None."""
    key = _login_key(cws)
    until = _lockout_until.get(key)
    if until is None:
        return None
    remaining = until - time.monotonic()
    if remaining <= 0:
        _lockout_until.pop(key, None)
        _failed_attempts.pop(key, None)
        return None
    return remaining


def _record_failed_login(cws: str) -> None:
    key = _login_key(cws)
    now = time.monotonic()
    attempts = [t for t in _failed_attempts.get(key, []) if now - t < _FAILURE_WINDOW_SECONDS]
    attempts.append(now)
    _failed_attempts[key] = attempts
    if len(attempts) >= _MAX_FAILED_ATTEMPTS:
        _lockout_until[key] = now + _LOCKOUT_SECONDS


def _clear_failed_logins(cws: str) -> None:
    key = _login_key(cws)
    _failed_attempts.pop(key, None)
    _lockout_until.pop(key, None)


# ─────────────────────────────────────────────────────────────
#  Low-level storage helpers
# ─────────────────────────────────────────────────────────────

def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _load_users() -> List[Dict[str, Any]]:
    # Lock-protected, atomic-write-safe load/save (utils/safe_json.py) —
    # prevents corruption when multiple users register/update at once.
    _ensure_data_dir()
    return _safe_load_json(USERS_PATH, default=[])


def _save_users(users: List[Dict[str, Any]]) -> None:
    _ensure_data_dir()
    _safe_save_json(USERS_PATH, users)


# ─────────────────────────────────────────────────────────────
#  Password hashing (stdlib pbkdf2, no extra dependency)
# ─────────────────────────────────────────────────────────────

def _hash_password(password: str, salt: Optional[bytes] = None) -> Tuple[str, str]:
    """Returns (hash_hex, salt_hex)."""
    if salt is None:
        salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return digest.hex(), salt.hex()


def _verify_password(password: str, hash_hex: str, salt_hex: str) -> bool:
    salt = bytes.fromhex(salt_hex)
    candidate_hash, _ = _hash_password(password, salt)
    return secrets.compare_digest(candidate_hash, hash_hex)


# ─────────────────────────────────────────────────────────────
#  Validation
# ─────────────────────────────────────────────────────────────

def validate_password_strength(password: str) -> Tuple[bool, str]:
    """Password must be >= 8 chars and contain at least one special character."""
    if not password or len(password) < 8:
        return False, t("us.password_min_length")
    if not any(c in SPECIAL_CHARS for c in password):
        return False, t("us.password_special_char")
    return True, ""


def validate_cws(cws: str) -> Tuple[bool, str]:
    if not cws or not cws.strip():
        return False, t("us.cws_required")
    if not re.match(r"^[A-Za-z0-9_.\-]{2,20}$", cws.strip()):
        return False, t("us.cws_invalid_format")
    return True, ""


def validate_email(email: str) -> Tuple[bool, str]:
    if not email or not email.strip():
        return False, t("us.email_required")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email.strip()):
        return False, t("us.email_invalid")
    return True, ""


# ─────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────

def get_user(cws: str) -> Optional[Dict[str, Any]]:
    cws_norm = (cws or "").strip().lower()
    for u in _load_users():
        if u["cws"].lower() == cws_norm:
            return u
    return None


_SECRET_FIELDS = ("password_hash", "salt", "encrypted_private_key", "priv_key_salt", "escrowed_private_key")

# CWS that is meant to be the designated owner/creator of this app once
# properly claimed — see _root_setup_key_matches() below for how that
# claim is gated.
ROOT_ADMIN_CWS = "DEMOADMIN"

# Registering with ROOT_ADMIN_CWS auto-approves + grants admin ONLY if the
# caller also supplies the matching secret from this environment variable.
# If the env var isn't set, root-admin auto-grant is disabled entirely (the
# CWS is then treated like any other registration) — this closes what would
# otherwise be a trivial pre-auth privilege-escalation / account-squatting
# hole: without this gate, whoever registers "DEMOADMIN" first (attacker or
# legitimate owner) would silently get permanent, non-revocable admin.
ROOT_ADMIN_SETUP_KEY_ENV = "ROOT_ADMIN_SETUP_KEY"


def _is_root_admin(cws: str) -> bool:
    return (cws or "").strip().upper() == ROOT_ADMIN_CWS


def _root_setup_key_matches(provided_key: Optional[str]) -> bool:
    """
    Whether `provided_key` matches the deployer-configured
    ROOT_ADMIN_SETUP_KEY environment variable. Returns False (i.e. the
    root-admin auto-grant is disabled) if that env var isn't set at all —
    fail closed by default rather than trusting a bare CWS string.
    """
    required_key = os.environ.get(ROOT_ADMIN_SETUP_KEY_ENV)
    if not required_key:
        return False
    return secrets.compare_digest((provided_key or ""), required_key)


def list_users() -> List[Dict[str, Any]]:
    """Returns all users WITHOUT password hash/salt/private-key material (safe for display).
    Note: "public_key" IS included — it's meant to be shared so others can
    encrypt messages TO this user."""
    return [
        {k: v for k, v in u.items() if k not in _SECRET_FIELDS}
        for u in _load_users()
    ]


def is_admin(cws: str) -> bool:
    """Whether `cws` has administrator privileges (Administration tab)."""
    if _is_root_admin(cws):
        return True
    user = get_user(cws)
    return bool(user and user.get("is_admin"))


def is_approved(user: Dict[str, Any]) -> bool:
    """
    Accounts created before the approval workflow existed have no "status"
    field at all — they're grandfathered in as approved so nobody who could
    already log in gets suddenly locked out.
    """
    status = user.get("status")
    return status is None or status == "approved"


def register_user(
    name: str,
    cws: str,
    password: str,
    confirm_password: str,
    email_teams: str,
    cargo: str,
    root_setup_key: Optional[str] = None,
    wants_psld_parts: bool = False,
    wants_ilt_transportation: bool = False,
    oracle_username: str = "",
) -> Tuple[bool, str, bool]:
    """
    Registers a new user. Returns (success, message, pending_approval).

    `pending_approval` is True only when registration succeeded but the
    account still needs an administrator to approve it before first login
    — callers should use this flag instead of pattern-matching the
    (translated, language-dependent) message text.

    `root_setup_key` only matters when registering the reserved
    ROOT_ADMIN_CWS ("DEMOADMIN"): it must match the ROOT_ADMIN_SETUP_KEY
    environment variable for the account to be auto-approved + granted
    admin. Without a matching key, that CWS registers as a normal user
    subject to the regular approval workflow — see _root_setup_key_matches().

    `wants_psld_parts` / `wants_ilt_transportation` are the applicant's own
    self-declared portal requests from the registration form's profile
    picker — stored directly as the `is_psld_parts`/`is_ilt_transportation`
    flags (same fields an admin can later grant/revoke from the
    Administration tab), so an admin reviewing a pending account can see
    what they asked for immediately instead of having to set flags from
    scratch. `oracle_username` is their own personal Oracle DB account
    (see auth/user_store.get_oracle_username/set_oracle_username and
    app.py's connection_dialog()) — always optional, relevant only if
    either portal flag is requested, never a password.
    """
    name = (name or "").strip()
    cws = (cws or "").strip()
    cargo = (cargo or "").strip()
    email_teams = (email_teams or "").strip()

    if not name:
        return False, t("us.name_required"), False

    ok, msg = validate_cws(cws)
    if not ok:
        return False, msg, False

    ok, msg = validate_email(email_teams)
    if not ok:
        return False, msg, False

    if not cargo:
        return False, t("us.cargo_required"), False

    if password != confirm_password:
        return False, t("us.passwords_do_not_match"), False

    ok, msg = validate_password_strength(password)
    if not ok:
        return False, msg, False

    if cws.upper() == "SYSTEM":
        return False, t("us.cws_system_reserved"), False

    if get_user(cws) is not None:
        return False, t("us.cws_already_registered", cws=cws), False

    hash_hex, salt_hex = _hash_password(password)

    # Generate a personal RSA keypair for end-to-end encrypted messaging.
    # The private key is encrypted with a key derived from this same
    # password (different salt from the login-hash salt above) and is
    # only ever decrypted in-memory at login time — see auth/crypto_messaging.py.
    public_key_pem, encrypted_private_key_b64, priv_key_salt, escrowed_private_key_b64 = crypto_messaging.generate_keypair_for_user(password)

    # Registration/approval workflow: the designated root admin (DEMOADMIN)
    # is auto-approved + granted admin ONLY if the correct setup key was
    # supplied (see _root_setup_key_matches). Everyone else follows the
    # app setting that decides whether new accounts require admin approval.
    root = _is_root_admin(cws) and _root_setup_key_matches(root_setup_key)
    require_admin_approval = bool(app_settings.get_setting("require_admin_approval_for_new_users", True))
    auto_approve = root or not require_admin_approval
    status = "approved" if auto_approve else "pending"

    # Use a transaction (single lock spanning load+append+save) instead of
    # a plain load/append/save so two users registering the same CWS at the
    # exact same moment can't both "win" (one silently overwriting the
    # other's record) — the second one re-checks uniqueness under the lock.
    # NOTE: we set `duplicate` instead of returning inside the `with` block
    # so a duplicate-CWS attempt doesn't still trigger a (no-op) file
    # rewrite + lock hold on every retry.
    duplicate = False
    with json_transaction(USERS_PATH, default=[]) as users:
        if any(u.get("cws", "").lower() == cws.lower() for u in users):
            duplicate = True
        else:
            users.append({
                "cws": cws,
                "name": name,
                "email_teams": email_teams,
                "cargo": cargo,
                "password_hash": hash_hex,
                "salt": salt_hex,
                "public_key": public_key_pem,
                "encrypted_private_key": encrypted_private_key_b64,
                "priv_key_salt": priv_key_salt,
                "escrowed_private_key": escrowed_private_key_b64,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "status": status,
                "is_admin": root,
                "approved_by": ROOT_ADMIN_CWS if root else ("SYSTEM" if auto_approve else None),
                "approved_at": datetime.now().isoformat(timespec="seconds") if auto_approve else None,
                "is_psld_parts": bool(wants_psld_parts),
                "is_ilt_transportation": bool(wants_ilt_transportation),
                "oracle_username": (oracle_username or "").strip(),
            })

    if duplicate:
        return False, t("us.cws_already_registered", cws=cws), False
    if root:
        return True, t("us.registered_as_admin", cws=cws), False
    if auto_approve:
        return True, t("us.registered_success", cws=cws), False
    return True, t("us.registered_pending", cws=cws), True


def authenticate(cws: str, password: str) -> Tuple[bool, Any]:
    """
    Returns (True, user_dict_without_secrets) on success,
    or (False, error_message) on failure.

    The returned dict includes a transient "_private_key_pem" field
    (the user's decrypted RSA private key, PEM string) so the caller can
    stash it in st.session_state for that session only — used to decrypt
    incoming encrypted messages. It is never persisted anywhere.
    """
    lockout_remaining = _is_locked_out(cws)
    if lockout_remaining is not None:
        minutes = max(1, int(lockout_remaining // 60) + 1)
        return False, t("us.login_too_many_attempts", minutes=minutes)

    user = get_user(cws)
    if user is None:
        _record_failed_login(cws)
        # Same message as a wrong password (below) — deliberately not
        # revealing whether the CWS exists, to avoid user-enumeration via
        # differing error text.
        return False, t("us.login_invalid")

    if not _verify_password(password, user["password_hash"], user["salt"]):
        _record_failed_login(cws)
        return False, t("us.login_invalid")

    _clear_failed_logins(cws)

    # Approval gate: block login until an administrator approves the
    # account (grandfathered accounts with no "status" field are treated
    # as already approved — see is_approved()).
    if not is_approved(user):
        status = user.get("status")
        if status == "rejected":
            reason = user.get("rejected_reason") or t("us.no_reason_given")
            return False, t("us.login_rejected", reason=reason)
        return False, t("us.login_pending")

    safe_user = {k: v for k, v in user.items() if k not in ("password_hash", "salt")}
    safe_user["must_change_password"] = bool(user.get("must_change_password"))

    # Legacy accounts created before messaging encryption was added won't
    # have a keypair yet — generate one transparently on next login.
    if not user.get("encrypted_private_key") or not user.get("public_key"):
        public_key_pem, encrypted_private_key_b64, priv_key_salt, escrowed_private_key_b64 = crypto_messaging.generate_keypair_for_user(password)
        # Transactional read-modify-write: two concurrent logins for the
        # same legacy account must not race and have one overwrite the
        # other's freshly-generated keypair.
        with json_transaction(USERS_PATH, default=[]) as users:
            for u in users:
                if u["cws"].lower() == cws.strip().lower():
                    u["public_key"] = public_key_pem
                    u["encrypted_private_key"] = encrypted_private_key_b64
                    u["priv_key_salt"] = priv_key_salt
                    u["escrowed_private_key"] = escrowed_private_key_b64
        safe_user["public_key"] = public_key_pem
        safe_user["_private_key_pem"] = crypto_messaging.decrypt_private_key(
            password, encrypted_private_key_b64, priv_key_salt
        )
    else:
        safe_user.pop("encrypted_private_key", None)
        safe_user.pop("priv_key_salt", None)
        private_pem = crypto_messaging.decrypt_private_key(
            password, user["encrypted_private_key"], user["priv_key_salt"]
        )
        safe_user["_private_key_pem"] = private_pem

        # Backfill a key-escrow copy for accounts that predate the escrow
        # mechanism, so a FUTURE admin password reset can recover this same
        # keypair instead of destroying it. This is the only moment the app
        # ever has the plaintext private key in hand outside of an active
        # session, so it's the only safe opportunity to do this.
        if private_pem and not user.get("escrowed_private_key"):
            escrowed_b64 = crypto_messaging.escrow_wrap_existing_private_key(private_pem)
            with json_transaction(USERS_PATH, default=[]) as users:
                for u in users:
                    if u["cws"].lower() == cws.strip().lower():
                        u["escrowed_private_key"] = escrowed_b64

    return True, safe_user


def update_user_profile(cws: str, name: Optional[str] = None, email_teams: Optional[str] = None,
                         cargo: Optional[str] = None) -> Tuple[bool, str]:
    if email_teams:
        ok, msg = validate_email(email_teams)
        if not ok:
            return False, msg
    found = False
    with json_transaction(USERS_PATH, default=[]) as users:
        for u in users:
            if u["cws"].lower() == cws.strip().lower():
                found = True
                if name:
                    u["name"] = name.strip()
                if email_teams:
                    u["email_teams"] = email_teams.strip()
                if cargo:
                    u["cargo"] = cargo.strip()
                break
    if not found:
        return False, t("us.user_not_found")
    return True, t("us.profile_updated")


def change_password(cws: str, old_password: str, new_password: str, confirm_new_password: str) -> Tuple[bool, str]:
    if new_password != confirm_new_password:
        return False, t("us.new_passwords_do_not_match")
    ok, msg = validate_password_strength(new_password)
    if not ok:
        return False, msg

    result: Tuple[bool, str] = (False, t("us.user_not_found"))
    with json_transaction(USERS_PATH, default=[]) as users:
        for u in users:
            if u["cws"].lower() == cws.strip().lower():
                if not _verify_password(old_password, u["password_hash"], u["salt"]):
                    result = (False, t("us.current_password_incorrect"))
                    break

                # Re-wrap the messaging private key with the new password so
                # encrypted messages stay readable after the change.
                if u.get("encrypted_private_key") and u.get("priv_key_salt"):
                    rewrapped = crypto_messaging.reencrypt_private_key(
                        old_password, new_password, u["encrypted_private_key"], u["priv_key_salt"]
                    )
                    if rewrapped is not None:
                        u["encrypted_private_key"], u["priv_key_salt"] = rewrapped

                hash_hex, salt_hex = _hash_password(new_password)
                u["password_hash"] = hash_hex
                u["salt"] = salt_hex
                u["must_change_password"] = False
                result = (True, t("us.password_changed"))
                break
    return result


# ─────────────────────────────────────────────────────────────
#  Administration (Administration tab — see ui/admin_tab.py)
# ─────────────────────────────────────────────────────────────

def list_pending_users() -> List[Dict[str, Any]]:
    """Users whose registration is awaiting admin approval."""
    return [u for u in list_users() if u.get("status") == "pending"]


def approve_user(cws: str, admin_cws: str) -> Tuple[bool, str]:
    """Approves a pending registration, letting them log in from now on."""
    with json_transaction(USERS_PATH, default=[]) as users:
        for u in users:
            if u["cws"].lower() == (cws or "").strip().lower():
                u["status"] = "approved"
                u["approved_by"] = admin_cws
                u["approved_at"] = datetime.now().isoformat(timespec="seconds")
                u.pop("rejected_reason", None)
                return True, t("us.user_approved", cws=cws)
    return False, t("us.user_not_found")


def reject_user(cws: str, admin_cws: str, reason: str) -> Tuple[bool, str]:
    """Rejects a pending registration with a reason shown to the user on login attempt."""
    with json_transaction(USERS_PATH, default=[]) as users:
        for u in users:
            if u["cws"].lower() == (cws or "").strip().lower():
                u["status"] = "rejected"
                u["rejected_by"] = admin_cws
                u["rejected_at"] = datetime.now().isoformat(timespec="seconds")
                u["rejected_reason"] = (reason or "").strip() or t("us.no_reason_given")
                return True, t("us.user_rejected", cws=cws)
    return False, t("us.user_not_found")


def remove_user(cws: str) -> Tuple[bool, str]:
    """Permanently deletes a user account (registration record, keys, etc.)."""
    if _is_root_admin(cws):
        return False, t("us.root_admin_cannot_be_removed")
    with json_transaction(USERS_PATH, default=[]) as users:
        filtered = [u for u in users if u["cws"].lower() != (cws or "").strip().lower()]
        if len(filtered) == len(users):
            return False, t("us.user_not_found")
        users[:] = filtered
    return True, t("us.user_removed", cws=cws)


def _generate_temp_password() -> str:
    """
    Generates a random temporary password that already satisfies
    validate_password_strength() (>=8 chars, at least one special char).
    """
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    special = "!@#$%^&*"
    core = "".join(secrets.choice(alphabet) for _ in range(10))
    return core + secrets.choice(special)


def admin_reset_password(cws: str, admin_cws: str) -> Tuple[bool, str, Optional[str]]:
    """
    Admin-triggered password reset (Administration tab -> "Reset password").
    Generates a random temporary password, stores its hash, flags the
    account with must_change_password so the user is forced to pick a new
    one on next login, and returns the plaintext temp password so the
    caller can e-mail it (see auth/email_utils.py) and/or show it on-screen
    as a fallback if the e-mail fails to send.

    The account's RSA private key (used for encrypted messaging) is
    normally wrapped with the OLD password, which the admin doesn't know.
    Instead of generating a brand-new keypair (which used to silently and
    permanently destroy every previously-received message), we recover the
    SAME private key via its local escrow copy (see
    auth/crypto_messaging.py's key-escrow mechanism) and re-wrap it with the
    new temp password — so the user's message history stays fully readable
    after the reset. Only accounts created before escrow existed (no
    `escrowed_private_key` on record) fall back to the old
    generate-a-new-keypair behavior, which is logged as a warning.

    Returns (success, message, temp_password_or_None).
    """
    temp_password = _generate_temp_password()
    hash_hex, salt_hex = _hash_password(temp_password)

    with json_transaction(USERS_PATH, default=[]) as users:
        for u in users:
            if u["cws"].lower() == (cws or "").strip().lower():
                recovered_private_pem = crypto_messaging.escrow_unwrap_private_key(
                    u.get("escrowed_private_key")
                )
                if recovered_private_pem is not None:
                    # Same keypair, just re-wrapped with the new password —
                    # public_key is untouched, so old messages stay readable.
                    encrypted_private_key_b64, priv_key_salt = crypto_messaging.rewrap_private_key_with_new_password(
                        recovered_private_pem, temp_password
                    )
                    u["encrypted_private_key"] = encrypted_private_key_b64
                    u["priv_key_salt"] = priv_key_salt
                    keypair_preserved = True
                else:
                    # No escrow copy on record (pre-escrow legacy account) —
                    # fall back to generating a fresh keypair as before.
                    public_key_pem, encrypted_private_key_b64, priv_key_salt, escrowed_private_key_b64 = (
                        crypto_messaging.generate_keypair_for_user(temp_password)
                    )
                    u["public_key"] = public_key_pem
                    u["encrypted_private_key"] = encrypted_private_key_b64
                    u["priv_key_salt"] = priv_key_salt
                    u["escrowed_private_key"] = escrowed_private_key_b64
                    keypair_preserved = False

                u["password_hash"] = hash_hex
                u["salt"] = salt_hex
                u["must_change_password"] = True
                u["password_reset_by"] = admin_cws
                u["password_reset_at"] = datetime.now().isoformat(timespec="seconds")
                message = (
                    t("us.password_reset_done", cws=cws)
                    if keypair_preserved
                    else t("us.password_reset_done", cws=cws) + " " + t("us.password_reset_keys_lost_warning")
                )
                return True, message, temp_password
    return False, t("us.user_not_found"), None


def set_admin(cws: str, make_admin: bool) -> Tuple[bool, str]:
    """Grants or revokes administrator privileges for a user."""
    if _is_root_admin(cws) and not make_admin:
        return False, t("us.root_admin_cannot_revoke")
    with json_transaction(USERS_PATH, default=[]) as users:
        for u in users:
            if u["cws"].lower() == (cws or "").strip().lower():
                u["is_admin"] = bool(make_admin)
                return True, (
                    t("us.admin_granted", cws=cws) if make_admin else t("us.admin_revoked", cws=cws)
                )
    return False, t("us.user_not_found")


def set_psld_parts_flag(cws: str, enabled: bool) -> Tuple[bool, str]:
    """
    Grants or revokes the "PSLD - Parts" team flag for a user — this is
    what makes them selectable as the "Responsible Contact" dropdown in
    the PSLD - Parts -> ABEND registry (troubleshooter/psld_abend_registry.py)
    without needing a separate admin role or hard-coded user list. Any
    existing app administrator can toggle this from the Administration
    tab's user list, same pattern as set_admin() above.
    """
    with json_transaction(USERS_PATH, default=[]) as users:
        for u in users:
            if u["cws"].lower() == (cws or "").strip().lower():
                u["is_psld_parts"] = bool(enabled)
                return True, (
                    t("us.psld_flag_granted", cws=cws) if enabled else t("us.psld_flag_revoked", cws=cws)
                )
    return False, t("us.user_not_found")


def is_psld_parts(cws: str) -> bool:
    """Whether `cws` is flagged as a member of the PSLD - Parts team."""
    user = get_user(cws)
    return bool(user and user.get("is_psld_parts"))


def list_psld_parts_users() -> List[Dict[str, Any]]:
    """
    Returns all approved users flagged "PSLD - Parts" — this is exactly
    the population the ABEND registry's "Responsible Contact" dropdown
    is restricted to (see troubleshooter/psld_abend_registry.py /
    ui/psld_parts_tab.py's ABEND sub-menu).
    """
    return [
        u for u in list_users()
        if u.get("is_psld_parts") and is_approved(u)
    ]


def set_ilt_transportation_flag(cws: str, enabled: bool) -> Tuple[bool, str]:
    """
    Grants or revokes the "ILT - Transportation" portal-access flag —
    this (together with `is_psld_parts`, which now doubles as the
    "Parts - Brasil" portal flag) is what the shared portal router
    (portal_app.py) uses to decide which app(s) a user should land on
    after login. Same toggle pattern as set_psld_parts_flag() above.
    """
    with json_transaction(USERS_PATH, default=[]) as users:
        for u in users:
            if u["cws"].lower() == (cws or "").strip().lower():
                u["is_ilt_transportation"] = bool(enabled)
                return True, (
                    t("us.ilt_flag_granted", cws=cws) if enabled else t("us.ilt_flag_revoked", cws=cws)
                )
    return False, t("us.user_not_found")


def is_ilt_transportation(cws: str) -> bool:
    """Whether `cws` is flagged for "ILT - Transportation" portal access."""
    user = get_user(cws)
    return bool(user and user.get("is_ilt_transportation"))


def list_ilt_transportation_users() -> List[Dict[str, Any]]:
    """Returns all approved users flagged "ILT - Transportation"."""
    return [
        u for u in list_users()
        if u.get("is_ilt_transportation") and is_approved(u)
    ]


def set_business_flag(cws: str, enabled: bool) -> Tuple[bool, str]:
    """
    Grants or revokes the "Business" flag for a user — this is what makes
    the one-click "Connect with application account" button appear in
    ILT Troubleshooter's Oracle connection dialog (see app.py's
    connection_dialog() and config/db_config.get_app_account()), letting
    them connect with a pre-defined shared service account instead of
    typing personal DB credentials every time. Same toggle pattern as
    set_psld_parts_flag()/set_ilt_transportation_flag() above.
    """
    with json_transaction(USERS_PATH, default=[]) as users:
        for u in users:
            if u["cws"].lower() == (cws or "").strip().lower():
                u["is_business"] = bool(enabled)
                return True, (
                    t("us.business_flag_granted", cws=cws) if enabled else t("us.business_flag_revoked", cws=cws)
                )
    return False, t("us.user_not_found")


def is_business_user(cws: str) -> bool:
    """Whether `cws` is flagged as a member of the "Business" team,
    granting access to the shared Oracle application account."""
    user = get_user(cws)
    return bool(user and user.get("is_business"))


def list_business_users() -> List[Dict[str, Any]]:
    """Returns all approved users flagged "Business"."""
    return [
        u for u in list_users()
        if u.get("is_business") and is_approved(u)
    ]


def set_oracle_username(cws: str, oracle_username: str) -> Tuple[bool, str]:
    """
    Registers the individual Oracle DB account that belongs to this user
    (their own personal DEMODB01/etc. login) — set once by an admin (or
    the user themselves, via "My Account"). This is what lets the Oracle
    connection dialog (app.py's connection_dialog()) warn someone if
    they're about to connect using a *different* Oracle username than
    the one on file for them — the exact scenario DBA account-sharing
    monitoring flags (one person's DB session showing another person's
    DB username). Never stores a password — only the username, purely
    for this mismatch check / bookkeeping.
    """
    with json_transaction(USERS_PATH, default=[]) as users:
        for u in users:
            if u["cws"].lower() == (cws or "").strip().lower():
                u["oracle_username"] = (oracle_username or "").strip()
                return True, t("us.oracle_username_saved", cws=cws)
    return False, t("us.user_not_found")


def get_oracle_username(cws: str) -> str:
    """The Oracle DB username registered as belonging to this user, if any."""
    user = get_user(cws)
    return (user or {}).get("oracle_username", "") or ""


def get_user_theme(cws: str) -> str:
    """The signed-in user's saved UI theme preference (see
    ui/theme_manager.py) — empty string if never set, in which case the
    caller falls back to a session/default theme."""
    user = get_user(cws)
    return (user or {}).get("ui_theme", "") or ""


def set_user_theme(cws: str, theme_name: str) -> Tuple[bool, str]:
    """Persists the user's chosen UI theme so it follows them across
    logins/devices and between ILT Troubleshooter / PSLD - Parts /
    the portal app — all three read this same field."""
    with json_transaction(USERS_PATH, default=[]) as users:
        for u in users:
            if u["cws"].lower() == (cws or "").strip().lower():
                u["ui_theme"] = theme_name
                return True, "ok"
    return False, t("us.user_not_found")


def set_parts_reviewer_flag(cws: str, enabled: bool) -> Tuple[bool, str]:
    """
    Grants or revokes the "Parts Reviewer" flag — this is the population
    allowed into PSLD - Parts's "🔍 Double-Check" sub-menu
    (troubleshooter/psld_review_queue.py / ui/psld_parts_tab.py), where the
    AI's suggested ticket->resolution matches for already-closed tickets
    wait for a human to confirm or reject them before they count as
    self-learning feedback. Same toggle pattern as set_psld_parts_flag()
    above — any existing app administrator can grant/revoke this from the
    Administration tab's user list.
    """
    with json_transaction(USERS_PATH, default=[]) as users:
        for u in users:
            if u["cws"].lower() == (cws or "").strip().lower():
                u["is_parts_reviewer"] = bool(enabled)
                return True, (
                    t("us.parts_reviewer_granted", cws=cws) if enabled else t("us.parts_reviewer_revoked", cws=cws)
                )
    return False, t("us.user_not_found")


def is_parts_reviewer(cws: str) -> bool:
    """Whether `cws` is flagged as a "Parts Reviewer" (double-check access)."""
    user = get_user(cws)
    return bool(user and user.get("is_parts_reviewer"))


def list_parts_reviewer_users() -> List[Dict[str, Any]]:
    """Returns all approved users flagged as "Parts Reviewer"."""
    return [
        u for u in list_users()
        if u.get("is_parts_reviewer") and is_approved(u)
    ]


def set_ilt_support_flag(cws: str, enabled: bool) -> Tuple[bool, str]:
    """
    Grants or revokes the "ILT Support" flag — the ILT Troubleshooter
    equivalent of set_parts_reviewer_flag() above: this is the
    population (alongside root/app admins) allowed to review and
    approve/reject the AI's auto-drafted "Autonomous Fix" proposals
    (troubleshooter/autonomous_fix.py, "Autonomous Fix" tab) before they
    count as confirmed self-learning feedback. Same toggle pattern —
    any existing app administrator can grant/revoke this from the
    Administration tab's user list.
    """
    with json_transaction(USERS_PATH, default=[]) as users:
        for u in users:
            if u["cws"].lower() == (cws or "").strip().lower():
                u["is_ilt_support"] = bool(enabled)
                return True, (
                    t("us.ilt_support_granted", cws=cws) if enabled else t("us.ilt_support_revoked", cws=cws)
                )
    return False, t("us.user_not_found")


def is_ilt_support(cws: str) -> bool:
    """Whether `cws` is flagged as "ILT Support" (Autonomous Fix approval access)."""
    user = get_user(cws)
    return bool(user and user.get("is_ilt_support"))


def can_approve_autonomous_fixes(cws: str) -> bool:
    """Whether `cws` may review/approve/reject Autonomous Fix proposals —
    root admin, any app admin, or anyone explicitly flagged "ILT Support"."""
    return is_admin(cws) or is_ilt_support(cws)


def list_ilt_support_users() -> List[Dict[str, Any]]:
    """Returns all approved users flagged as "ILT Support" (for the
    Autonomous Fix "assign to" / Learn Center picker)."""
    return [
        u for u in list_users()
        if u.get("is_ilt_support") and is_approved(u)
    ]


# ── Per-screen access control ────────────────────────────────────────────
# Fine-grained, per-user tab/screen lock-down on top of the coarser portal
# flags (is_psld_parts / is_ilt_transportation) above — e.g. a user can
# have "Parts - Brasil" access but still be denied the ABEND registry
# specifically. Stored as a single dict on the user record:
#   user["screen_access"] = {"psld.abend": False, "ilt.schema": False, ...}
# Opt-OUT model on purpose: a screen key simply absent from the dict (the
# default for every existing/new user) means "allowed" — so this feature
# rolling out never silently locks anyone out of anything they already
# had, and new screens added later are visible by default too.
SCREEN_REGISTRY: Dict[str, Dict[str, str]] = {
    "ilt": {
        "ilt.report": "Report",
        "ilt.troubleshooter": "Troubleshooter",
        "ilt.batch": "Batch",
        "ilt.knowledge_base": "Knowledge Base",
        "ilt.pending": "Pending",
        "ilt.sql": "SQL Queries",
        "ilt.ai": "AI Query",
        "ilt.chat": "Copilot Chat",
        "ilt.schema": "Schema Manager",
        "ilt.learning": "Learning",
        "ilt.autonomous_fix": "Autonomous Fix",
        "ilt.qbuilder": "Query Builder",
        "ilt.glossary": "SQL Glossary",
        "ilt.help": "Help",
    },
    "psld": {
        "psld.analyze": "Analyze",
        "psld.kb": "Knowledge Base",
        "psld.mock": "Mock Data",
        "psld.abend": "ABEND Registry",
        "psld.stats": "Stats",
        "psld.review": "Double-Check",
    },
}


def get_screen_access(cws: str) -> Dict[str, bool]:
    """Raw per-user screen-override dict (only keys that were explicitly
    changed from the default-allowed state are present)."""
    user = get_user(cws)
    raw = (user or {}).get("screen_access") or {}
    return {k: bool(v) for k, v in raw.items()}


def is_screen_enabled(cws: str, screen_key: str) -> bool:
    """Whether `cws` may see the given screen. Admins always see
    everything (defense-in-depth for role changes); everyone else is
    allowed unless explicitly denied (opt-out model — see module note)."""
    if is_admin(cws):
        return True
    overrides = get_screen_access(cws)
    return overrides.get(screen_key, True)


def set_screen_access(cws: str, screen_key: str, enabled: bool) -> Tuple[bool, str]:
    """Grants (default/True) or denies (False) one screen for one user."""
    with json_transaction(USERS_PATH, default=[]) as users:
        for u in users:
            if u["cws"].lower() == (cws or "").strip().lower():
                access = dict(u.get("screen_access") or {})
                if enabled:
                    # Allowed is the default — drop the override entirely
                    # instead of piling up redundant `True` entries.
                    access.pop(screen_key, None)
                else:
                    access[screen_key] = False
                u["screen_access"] = access
                return True, "ok"
    return False, t("us.user_not_found")
