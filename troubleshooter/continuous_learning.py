"""
troubleshooter/continuous_learning.py
======================================
"Deixar a IA rodando no meu PC" — an actual background thread (not a
one-shot button) that periodically re-runs
troubleshooter.ilt_ai_core.mega_deep_learn() for as long as an admin
chooses to keep it going, so the AI keeps absorbing new production
errors/corrections between manual visits to the AI Control Center.

Design notes / honesty:
- This is a real Python `threading.Thread` doing real work (DB query +
  TF-IDF/SVD/KMeans + MLP/RandomForest retraining) on this machine's
  CPU, at a fixed interval — not a timer that "looks busy" without
  doing anything.
- It needs a live Oracle connection object to read the DB. Since
  Streamlit reruns the whole script on every interaction and
  connections belong to a specific user's session, the thread is
  started with (and keeps re-using) the connection object that was
  active at the moment "Start" was clicked. If that connection is
  closed/invalidated (e.g. the user disconnects, or the underlying
  session drops), the loop will fail gracefully, record the error in
  its status file, and stop by itself rather than raising into a
  Streamlit rerun on an unrelated request.
- Only one continuous-learning run loop exists per process (module-level
  singleton) — starting it twice just returns "already running".
- State (running flag, interval, run counter, last result, last error)
  is persisted to data/continuous_learning_status.json so the admin UI
  can show accurate status across Streamlit reruns without holding a
  reference to the thread object itself.
- Minimum interval is capped at 10 minutes so a mega DB scan (up to
  DEFAULT_MEGA_DB_LIMIT rows) can't be accidentally hammered back-to-back
  against the production database.
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
STATUS_PATH = DATA_DIR / "continuous_learning_status.json"

MIN_INTERVAL_MINUTES = 10
DEFAULT_INTERVAL_MINUTES = 30

_thread: Optional[threading.Thread] = None
_stop_event: Optional[threading.Event] = None
_lock = threading.Lock()


def _load_status() -> Dict[str, Any]:
    if not STATUS_PATH.exists():
        return {"running": False}
    try:
        with open(STATUS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"running": False}


def _save_status(data: Dict[str, Any]) -> None:
    tmp_path = str(STATUS_PATH) + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, STATUS_PATH)


def status() -> Dict[str, Any]:
    """Current continuous-learning status for the admin UI. `running`
    reflects the in-process thread (accurate for this worker process);
    the rest of the fields are read from disk so they survive Streamlit
    reruns."""
    data = _load_status()
    data["running"] = _thread is not None and _thread.is_alive()
    return data


def _run_loop(conn: Any, interval_minutes: int, created_by: str) -> None:
    from troubleshooter import ilt_ai_core

    stop_event = _stop_event
    run_count = 0
    while stop_event is not None and not stop_event.is_set():
        started_at = datetime.now().isoformat(timespec="seconds")
        try:
            result = ilt_ai_core.mega_deep_learn(conn=conn, created_by=created_by)
            run_count += 1
            _save_status({
                "running": True,
                "interval_minutes": interval_minutes,
                "started_by": created_by,
                "run_count": run_count,
                "last_run_started_at": started_at,
                "last_run_finished_at": datetime.now().isoformat(timespec="seconds"),
                "last_result": {
                    "db_docs_scanned": result["cluster"].get("db_docs_scanned", 0),
                    "db_groups_added": result["cluster"].get("db_groups_added", 0),
                    "fixes_drafted": result["autonomous_fixes"].get("drafted", 0),
                },
                "last_error": None,
            })
        except Exception as exc:  # keep the loop's own crash out of any Streamlit rerun
            error_detail = f"{exc}\n{traceback.format_exc(limit=3)}"
            _save_status({
                "running": False,
                "interval_minutes": interval_minutes,
                "started_by": created_by,
                "run_count": run_count,
                "last_run_started_at": started_at,
                "last_error": error_detail,
            })
            try:
                from auth import audit_log
                audit_log.record_event(
                    "ai_continuous_learning_error", cws=created_by,
                    detail=f"Background continuous-learning loop crashed after {run_count} run(s): {exc}",
                    app="ilt", category="ai_training", severity="error",
                )
            except Exception:
                pass  # never let audit logging itself take down the background thread
            return  # stop the loop; a broken/closed connection won't self-heal
        # Sleep in small increments so "stop" is responsive instead of
        # waiting out the full interval.
        remaining = interval_minutes * 60
        while remaining > 0 and stop_event is not None and not stop_event.is_set():
            chunk = min(5, remaining)
            time.sleep(chunk)
            remaining -= chunk
    _save_status({**_load_status(), "running": False})


def start(conn: Any, interval_minutes: int = DEFAULT_INTERVAL_MINUTES, created_by: str = "SYSTEM") -> Dict[str, Any]:
    global _thread, _stop_event
    with _lock:
        if _thread is not None and _thread.is_alive():
            return {"ok": False, "reason": "already_running"}
        interval_minutes = max(MIN_INTERVAL_MINUTES, int(interval_minutes))
        _stop_event = threading.Event()
        _thread = threading.Thread(
            target=_run_loop,
            args=(conn, interval_minutes, created_by),
            daemon=True,
            name="ilt-continuous-learning",
        )
        _save_status({
            "running": True,
            "interval_minutes": interval_minutes,
            "started_by": created_by,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "run_count": 0,
            "last_result": None,
            "last_error": None,
        })
        _thread.start()
        return {"ok": True}


def stop() -> Dict[str, Any]:
    global _thread, _stop_event
    with _lock:
        if _thread is None or not _thread.is_alive():
            _save_status({**_load_status(), "running": False})
            return {"ok": False, "reason": "not_running"}
        if _stop_event is not None:
            _stop_event.set()
        return {"ok": True}
