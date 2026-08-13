"""
utils/encrypted_json.py
=========================
Transparent at-rest encryption for sensitive local JSON config files.
First (and currently only) consumer: data/db_connections.json — saved
Oracle DB connection profiles (database/connection_profiles.py): host,
port, service name, default username, friendly display name.

WHY
---
Even though the PASSWORD is never stored in these profiles, the host/
port/service name IS real internal infrastructure configuration that
shouldn't sit around in a plaintext file on disk. Anyone who can read
the data/ folder (a stray backup, a misconfigured file share, someone
poking around the deployment host, etc.) would otherwise see internal
Oracle instance details in the clear. This wraps the exact same
file-locked, atomic-write pattern already used everywhere else in the
app (utils/safe_json.py) but encrypts the JSON payload at rest with
Fernet (AES-128-CBC + HMAC-SHA256, via the `cryptography` package
already used by auth/crypto_messaging.py).

Key management
--------------
- The key lives at data/.secrets/<key_name>.key, auto-generated on first
  use (os.urandom via Fernet.generate_key()), and is itself gitignored
  (data/.secrets/ — see .gitignore) — nobody has to remember or type a
  passphrase for this; it's a machine/deployment-local secret, not a
  user-facing one.
- Restricted to owner-only permissions where the OS supports it
  (POSIX chmod 0600 — a no-op on Windows, where NTFS ACLs would be the
  real equivalent and are out of scope for this internal tool).
- If this key file is ever lost, the encrypted file becomes
  unrecoverable — acceptable here because the content (host/port/
  service profiles) is trivially re-enterable by an admin, unlike a
  real user secret.

Backward compatibility
-----------------------
If an existing file is found already in PLAINTEXT JSON (e.g. from
before this module shipped), it's transparently read as plaintext once
and silently re-encrypted the next time it's saved through this module
— no manual migration step required.
"""
from __future__ import annotations

import json
import logging
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from filelock import FileLock, Timeout

logger = logging.getLogger(__name__)

SECRETS_DIR = Path(__file__).resolve().parent.parent / "data" / ".secrets"
_LOCK_TIMEOUT_SECONDS = 10


def _lock_path(path: Path) -> str:
    return str(path) + ".lock"


def _key_path(key_name: str) -> Path:
    return SECRETS_DIR / f"{key_name}.key"


def _get_or_create_key(key_name: str) -> bytes:
    """Loads the Fernet key for `key_name`, generating + persisting a new
    one on first use. Safe to call concurrently (lock-guarded)."""
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    path = _key_path(key_name)
    lock = FileLock(_lock_path(path), timeout=_LOCK_TIMEOUT_SECONDS)
    with lock:
        if path.exists():
            return path.read_bytes().strip()
        key = Fernet.generate_key()
        path.write_bytes(key)
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600; no-op on Windows
        except OSError:
            pass
        return key


def load_encrypted_json(path: str | Path, key_name: str, default: Any = None) -> Any:
    """Reads `path`, decrypts it with the named local key, and returns the
    parsed JSON. Returns `default` if the file doesn't exist. Transparently
    falls back to parsing the file as plain JSON if decryption fails —
    covers both a genuinely corrupt file AND the one-time migration from a
    pre-encryption plaintext file (the next save re-encrypts it)."""
    path = Path(path)
    if default is None:
        default = {}
    if not path.exists():
        return default

    key = _get_or_create_key(key_name)
    fernet = Fernet(key)

    try:
        with FileLock(_lock_path(path), timeout=_LOCK_TIMEOUT_SECONDS):
            raw = path.read_bytes()
    except Timeout:
        logger.warning("encrypted_json.load: lock timeout on %s; reading without lock", path)
        raw = path.read_bytes()

    if not raw:
        return default

    try:
        decrypted = fernet.decrypt(raw)
        return json.loads(decrypted.decode("utf-8"))
    except InvalidToken:
        # Not a Fernet token — likely a pre-encryption plaintext file.
        # Read it as-is; the next save() call will encrypt it going forward.
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.exception("encrypted_json.load: %s is neither valid ciphertext nor valid JSON", path)
            return default
    except Exception:
        logger.exception("encrypted_json.load: failed to read/decrypt %s", path)
        return default


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_path, path)  # atomic on Windows & POSIX
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def save_encrypted_json(path: str | Path, key_name: str, data: Any) -> bool:
    """Encrypts `data` (any JSON-serializable value) and atomically writes
    it to `path`. Returns True on success, False on failure (logged)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = _get_or_create_key(key_name)
    fernet = Fernet(key)

    try:
        plaintext = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        ciphertext = fernet.encrypt(plaintext)
        with FileLock(_lock_path(path), timeout=_LOCK_TIMEOUT_SECONDS):
            _atomic_write_bytes(path, ciphertext)
        return True
    except Timeout:
        logger.error("encrypted_json.save: could not acquire lock for %s within %ss — save skipped", path, _LOCK_TIMEOUT_SECONDS)
        return False
    except OSError:
        logger.exception("encrypted_json.save: failed to write %s", path)
        return False


class encrypted_json_transaction:
    """Context manager for atomic read-modify-write updates on an
    encrypted JSON file — the encrypted-at-rest equivalent of
    utils.safe_json.json_transaction. Holds a single file lock across the
    entire load -> mutate -> save cycle so concurrent callers can't race
    each other into a lost update.

    Usage:
        with encrypted_json_transaction(PROFILES_PATH, "db_connections", default=[]) as profiles:
            profiles.append(new_profile)
        # automatically re-encrypted and saved on clean exit.
    """

    def __init__(self, path: str | Path, key_name: str, default: Any = None):
        self.path = Path(path)
        self.key_name = key_name
        self.default = [] if default is None else default
        self._lock = FileLock(_lock_path(self.path), timeout=_LOCK_TIMEOUT_SECONDS)
        self._data = None
        self._key = _get_or_create_key(key_name)

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock.acquire()
        fernet = Fernet(self._key)
        try:
            if self.path.exists():
                raw = self.path.read_bytes()
                if not raw:
                    self._data = self.default
                else:
                    try:
                        self._data = json.loads(fernet.decrypt(raw).decode("utf-8"))
                    except InvalidToken:
                        # Pre-encryption plaintext migration path (see load_encrypted_json).
                        self._data = json.loads(raw.decode("utf-8"))
            else:
                self._data = self.default
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            logger.exception("encrypted_json_transaction: failed to read %s, starting from default", self.path)
            self._data = self.default
        return self._data

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is None:
                try:
                    fernet = Fernet(self._key)
                    plaintext = json.dumps(self._data, ensure_ascii=False, indent=2).encode("utf-8")
                    _atomic_write_bytes(self.path, fernet.encrypt(plaintext))
                except OSError:
                    logger.exception("encrypted_json_transaction: failed to write %s", self.path)
        finally:
            self._lock.release()
        return False  # never suppress exceptions
