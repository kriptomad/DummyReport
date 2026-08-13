"""
Cross-platform, multi-process-safe JSON persistence helpers.

Why this exists
----------------
The app persists most of its state (users, messages, KB ownership, fix
requests, connection profiles, history logs, etc.) as flat JSON files
under ``data/``. With several people using the same Streamlit deployment
at once, two users can end up saving to the same file at (almost) the
same time. Without any locking, that's a classic lost-update / corrupted
-file race condition: one write can clobber another, or a reader can see
a half-written file if the process is interrupted mid `json.dump()`.

This module provides small drop-in replacements — ``load_json`` /
``save_json`` — that:

1. **Lock** the file (via ``filelock``, which works the same way on
   Windows and Linux — no reliance on ``fcntl``, which doesn't exist on
   Windows) so concurrent readers/writers serialize instead of racing.
2. **Write atomically**: data is written to a temporary file in the same
   directory and then swapped into place with ``os.replace()``, which is
   atomic on both Windows and POSIX. A crash or crash-like interruption
   mid-write can never leave a half-written / corrupted JSON file behind.
3. **Log failures** instead of silently swallowing them, so a failed
   save is visible in the app/server logs instead of disappearing.

Usage is a straight swap for the old ad-hoc per-module `_load`/`_save`
helpers scattered across `auth/`, `troubleshooter/`, `database/`, and
`utils/`:

    from utils.safe_json import load_json, save_json

    data = load_json(PATH, default=[])
    save_json(PATH, data)
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

logger = logging.getLogger(__name__)

# Locks live next to the target file (e.g. "users.json.lock") so different
# JSON files never contend with each other's locks.
_LOCK_SUFFIX = ".lock"
_LOCK_TIMEOUT_SECONDS = 10


def _lock_path(path: Path) -> str:
    return str(path) + _LOCK_SUFFIX


def load_json(path: str | Path, default: Any = None) -> Any:
    """Safely load JSON from `path`, returning `default` if missing/corrupt.

    Takes a shared-ish lock (filelock doesn't distinguish read/write locks,
    but the critical section is tiny) so a concurrent writer can't be
    observed mid-write.
    """
    path = Path(path)
    if default is None:
        default = {}
    if not path.exists():
        return default

    try:
        with FileLock(_lock_path(path), timeout=_LOCK_TIMEOUT_SECONDS):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Timeout:
        logger.warning("safe_json.load_json: lock timeout on %s; reading without lock", path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logger.exception("safe_json.load_json: failed to read %s", path)
            return default
    except (json.JSONDecodeError, OSError):
        logger.exception("safe_json.load_json: failed to read/parse %s", path)
        return default


def _atomic_write(path: Path, data: Any) -> None:
    """Write `data` as JSON to `path` via temp-file + atomic replace.
    Caller is responsible for holding any necessary lock."""
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)  # atomic on Windows & POSIX
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def save_json(path: str | Path, data: Any) -> bool:
    """Safely save `data` as JSON to `path`.

    Writes to a temp file in the same directory then atomically replaces
    the target (`os.replace`), guarded by a file lock so concurrent
    writers serialize instead of interleaving/corrupting the file.

    Returns True on success, False on failure (failure is also logged,
    unlike the old silent `except: pass` pattern this replaces).

    NOTE: this only guards the write itself. If your update is a
    read-modify-write (load → mutate → save), use `json_transaction()`
    instead — otherwise two concurrent load/modify/save cycles can each
    read stale data and the second save silently clobbers the first
    writer's change ("lost update"), even though neither file gets
    physically corrupted.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with FileLock(_lock_path(path), timeout=_LOCK_TIMEOUT_SECONDS):
            _atomic_write(path, data)
            return True
    except Timeout:
        logger.error("safe_json.save_json: could not acquire lock for %s within %ss — save skipped", path, _LOCK_TIMEOUT_SECONDS)
        return False
    except OSError:
        logger.exception("safe_json.save_json: failed to write %s", path)
        return False


class json_transaction:
    """Context manager for atomic read-modify-write JSON updates.

    Holds a single file lock across the *entire* load → mutate → save
    cycle, so concurrent callers are fully serialized instead of racing
    (which `load_json()` + `save_json()` used back-to-back does NOT
    guarantee — two users could both load the same stale snapshot before
    either saves, and the second save would silently discard the first
    user's change).

    Usage:
        with json_transaction(MESSAGES_PATH, default=[]) as data:
            data.append(new_message)
        # automatically saved on clean exit; not saved if an exception
        # is raised inside the `with` block.
    """

    def __init__(self, path: str | Path, default: Any = None):
        self.path = Path(path)
        self.default = [] if default is None else default
        self._lock = FileLock(_lock_path(self.path), timeout=_LOCK_TIMEOUT_SECONDS)
        self._data = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock.acquire()
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            else:
                self._data = self.default
        except (json.JSONDecodeError, OSError):
            logger.exception("json_transaction: failed to read %s, starting from default", self.path)
            self._data = self.default
        return self._data

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is None:
                try:
                    _atomic_write(self.path, self._data)
                except OSError:
                    logger.exception("json_transaction: failed to write %s", self.path)
        finally:
            self._lock.release()
        return False  # never suppress exceptions

