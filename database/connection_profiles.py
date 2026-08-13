"""
database/connection_profiles.py
================================
Saved database-connection profiles, so that day-to-day connecting to a
familiar Oracle instance only requires a username + password — the host,
port, service name, and a friendly display name are saved and reusable.

Design:
- Persisted to data/db_connections.json — ENCRYPTED AT REST (see
  utils/encrypted_json.py) using a locally-generated key under
  data/.secrets/. The file on disk is unreadable ciphertext; the app
  transparently decrypts it in memory using the local key, so normal
  usage (list/add/edit/delete profiles) is completely unaffected — only
  someone with direct filesystem access to data/db_connections.json
  (without the key file) is blocked from reading the saved host/port/
  service configuration.
- The PASSWORD IS NEVER STORED — only host/port/service/name/default
  username are saved. Password is always typed fresh at connect time.
- Users can add, edit, and delete profiles freely — useful as this tool
  grows beyond DEMO_AUDIT into other databases over time.
"""
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from utils.encrypted_json import (
    encrypted_json_transaction,
    load_encrypted_json,
)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
PROFILES_PATH = os.path.join(DATA_DIR, "db_connections.json")
_KEY_NAME = "db_connections"


def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _load() -> List[Dict[str, Any]]:
    # Lock-protected atomic load of the encrypted file (see
    # utils/encrypted_json.py). Transparently migrates a pre-encryption
    # plaintext file the first time it's read, if one exists.
    _ensure_data_dir()
    return load_encrypted_json(PROFILES_PATH, _KEY_NAME, default=[])


def list_profiles() -> List[Dict[str, Any]]:
    """Returns all saved connection profiles, sorted by name."""
    return sorted(_load(), key=lambda p: p.get("name", "").lower())


def get_profile(profile_id: str) -> Optional[Dict[str, Any]]:
    for p in _load():
        if p["id"] == profile_id:
            return p
    return None


def add_profile(name: str, host: str, port: str, service: str, default_username: str = "") -> Dict[str, Any]:
    profile = {
        "id": uuid.uuid4().hex[:12],
        "name": (name or "").strip() or host,
        "host": (host or "").strip(),
        "port": (port or "").strip(),
        "service": (service or "").strip(),
        "default_username": (default_username or "").strip(),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    with encrypted_json_transaction(PROFILES_PATH, _KEY_NAME, default=[]) as profiles:
        profiles.append(profile)
    return profile


def update_profile(
    profile_id: str,
    name: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[str] = None,
    service: Optional[str] = None,
    default_username: Optional[str] = None,
) -> bool:
    with encrypted_json_transaction(PROFILES_PATH, _KEY_NAME, default=[]) as profiles:
        for p in profiles:
            if p["id"] == profile_id:
                if name is not None:
                    p["name"] = name.strip() or p["name"]
                if host is not None:
                    p["host"] = host.strip()
                if port is not None:
                    p["port"] = port.strip()
                if service is not None:
                    p["service"] = service.strip()
                if default_username is not None:
                    p["default_username"] = default_username.strip()
                p["updated_at"] = datetime.now().isoformat(timespec="seconds")
                return True
    return False


def delete_profile(profile_id: str) -> bool:
    with encrypted_json_transaction(PROFILES_PATH, _KEY_NAME, default=[]) as profiles:
        original_len = len(profiles)
        profiles[:] = [p for p in profiles if p["id"] != profile_id]
        return len(profiles) != original_len
