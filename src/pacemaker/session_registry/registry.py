"""
Session Registry — public API and internal helpers.

Public API:
- register_session(session_id, workspace_root, pid, db_path): INSERT OR REPLACE a session row
- heartbeat_and_purge(session_id, workspace_root, pid, db_path): update last_seen and purge stale rows
- list_siblings(workspace_root, exclude_session_id, db_path): return sibling sessions in same workspace
- unregister_session(session_id, db_path): delete the session row on shutdown

Internal helpers:
- _open_conn(db_path): context manager yielding an open DB connection
- _validate_session_args(session_id, workspace_root, pid, db_path): common arg validator
"""

import os
import time
from contextlib import contextmanager
from typing import Any, Dict, Generator, List

from .db import get_connection
from ..logger import log_warning

# ── SQL: register ─────────────────────────────────────────────────────────────
_SQL_REGISTER = (
    "INSERT OR REPLACE INTO sessions "
    "(session_id, workspace_root, pid, start_time, last_seen) "
    "VALUES (?, ?, ?, ?, ?)"
)

# ── SQL: heartbeat update/insert/purge ───────────────────────────────────────
_SQL_HEARTBEAT_UPDATE = "UPDATE sessions SET last_seen = ? WHERE session_id = ?"
_SQL_HEARTBEAT_INSERT = (
    "INSERT INTO sessions "
    "(session_id, workspace_root, pid, start_time, last_seen) "
    "VALUES (?, ?, ?, ?, ?)"
)
_SQL_PURGE = "DELETE FROM sessions WHERE last_seen < ?"
_SQL_LIST_SIBLINGS = (
    "SELECT session_id, workspace_root, start_time, last_seen, pid "
    "FROM sessions "
    "WHERE workspace_root = ? AND session_id != ? "
    "ORDER BY start_time ASC"
)
_SQL_UNREGISTER = "DELETE FROM sessions WHERE session_id = ?"

# ── Purge cutoff: env var override or 20-minute default ──────────────────────
_DEFAULT_PURGE_CUTOFF_SECONDS = int(
    os.environ.get("PACEMAKER_PURGE_CUTOFF_SECONDS", "1200")
)


@contextmanager
def _open_conn(db_path: str) -> Generator:
    """Open a registry DB connection, yield it, and always close it."""
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


def _validate_session_args(
    session_id: str,
    workspace_root: str,
    pid: int,
    db_path: str,
) -> bool:
    """Validate the 4 common session arguments. Log warning and return False on failure.

    pid validation uses type(pid) is not int (not isinstance) to reject bool,
    since bool is a subclass of int in Python.
    """
    if not session_id or not isinstance(session_id, str):
        log_warning("session_registry", "session_id must be a non-empty string")
        return False
    if not workspace_root or not isinstance(workspace_root, str):
        log_warning("session_registry", "workspace_root must be a non-empty string")
        return False
    if type(pid) is not int or pid <= 0:
        log_warning(
            "session_registry",
            f"pid={pid!r} must be a positive integer (bool not accepted)",
        )
        return False
    if not db_path or not isinstance(db_path, str):
        log_warning("session_registry", "db_path must be a non-empty string")
        return False
    return True


def register_session(
    session_id: str,
    workspace_root: str,
    pid: int,
    db_path: str,
) -> None:
    """Insert or replace a session row with start_time and last_seen set to now."""
    if not _validate_session_args(session_id, workspace_root, pid, db_path):
        return
    try:
        now = time.time()
        with _open_conn(db_path) as conn:
            conn.execute(_SQL_REGISTER, (session_id, workspace_root, pid, now, now))
            conn.commit()
    except Exception as e:
        log_warning("session_registry", f"register_session failed for {session_id}", e)


def heartbeat_and_purge(
    session_id: str,
    workspace_root: str,
    pid: int,
    db_path: str,
    purge_cutoff_seconds: int = _DEFAULT_PURGE_CUTOFF_SECONDS,
) -> None:
    """Update last_seen and purge stale sessions atomically via BEGIN IMMEDIATE."""
    if not _validate_session_args(session_id, workspace_root, pid, db_path):
        return
    if not isinstance(purge_cutoff_seconds, (int, float)) or purge_cutoff_seconds < 0:
        log_warning(
            "session_registry",
            f"purge_cutoff_seconds={purge_cutoff_seconds!r} must be >= 0",
        )
        return
    try:
        now = time.time()
        cutoff = now - purge_cutoff_seconds
        with _open_conn(db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(_SQL_HEARTBEAT_UPDATE, (now, session_id))
            if cursor.rowcount == 0:
                conn.execute(
                    _SQL_HEARTBEAT_INSERT,
                    (session_id, workspace_root, pid, now, now),
                )
            conn.execute(_SQL_PURGE, (cutoff,))
            conn.commit()
    except Exception as e:
        log_warning(
            "session_registry", f"heartbeat_and_purge failed for {session_id}", e
        )


def list_siblings(
    workspace_root: str,
    exclude_session_id: str,
    db_path: str,
) -> List[Dict[str, Any]]:
    """Return sessions in the same workspace, excluding the caller.

    Returns an empty list on invalid input or any DB error (fail-open).
    Rows are ordered by start_time ascending (oldest sibling first).
    """
    if not workspace_root or not isinstance(workspace_root, str):
        log_warning(
            "session_registry",
            "list_siblings: workspace_root must be a non-empty string",
        )
        return []
    if not exclude_session_id or not isinstance(exclude_session_id, str):
        log_warning(
            "session_registry",
            "list_siblings: exclude_session_id must be a non-empty string",
        )
        return []
    if not db_path or not isinstance(db_path, str):
        log_warning(
            "session_registry", "list_siblings: db_path must be a non-empty string"
        )
        return []
    try:
        with _open_conn(db_path) as conn:
            cursor = conn.execute(
                _SQL_LIST_SIBLINGS, (workspace_root, exclude_session_id)
            )
            rows = cursor.fetchall()
        return [
            {
                "session_id": row[0],
                "workspace_root": row[1],
                "start_time": row[2],
                "last_seen": row[3],
                "pid": row[4],
            }
            for row in rows
        ]
    except Exception as e:
        log_warning(
            "session_registry",
            f"list_siblings failed for workspace {workspace_root}",
            e,
        )
        return []


def unregister_session(session_id: str, db_path: str) -> None:
    """Delete the session row. No-op if the row does not exist (best-effort)."""
    if not session_id or not isinstance(session_id, str):
        log_warning(
            "session_registry",
            "unregister_session: session_id must be a non-empty string",
        )
        return
    if not db_path or not isinstance(db_path, str):
        log_warning(
            "session_registry", "unregister_session: db_path must be a non-empty string"
        )
        return
    try:
        with _open_conn(db_path) as conn:
            conn.execute(_SQL_UNREGISTER, (session_id,))
            conn.commit()
    except Exception as e:
        log_warning(
            "session_registry", f"unregister_session failed for {session_id}", e
        )
