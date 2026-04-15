"""
Unit tests for pace-maker sessions CLI command (_execute_sessions).

Tests call _execute_sessions directly (not via execute_command dispatcher)
to verify the function's own contract.

Tests:
- test_list_with_sessions: active session in DB returns success with session_id/workspace/pid
- test_list_empty: empty DB returns success with "no active sessions" message
- test_no_subcommand: None subcommand returns error mentioning "list"
- test_db_error: sqlite3.Error during access returns success=False with error message
- test_list_multiple_sessions: two sessions both appear in output
- test_list_db_not_exist: missing DB file handled gracefully (success, no active sessions)
- test_unknown_subcommand: unrecognized subcommand returns success=False
- test_list_output_header: output contains all three column headers (SESSION, WORKSPACE, PID)
"""

import sqlite3
import time
from unittest.mock import patch

# ── Environment variable ──────────────────────────────────────────────────────
ENV_REGISTRY_PATH = "PACEMAKER_SESSION_REGISTRY_PATH"

# ── Test constants (neutral values, no absolute filesystem paths) ─────────────
SESSION_A = "session-aaa-111"
SESSION_B = "session-bbb-222"
WORKSPACE_X = "workspace-x"
WORKSPACE_Y = "workspace-y"
PID_A = 1234
PID_B = 5678

# ── Timing constants ──────────────────────────────────────────────────────────
DEFAULT_SESSION_AGE_SECONDS = 60  # sessions inserted "1 minute ago" by default

# ── Schema DDL (single source of truth for tests) ────────────────────────────
_DDL_SESSIONS = (
    "CREATE TABLE IF NOT EXISTS sessions "
    "(session_id TEXT PRIMARY KEY, workspace_root TEXT NOT NULL, "
    "pid INTEGER, start_time REAL, last_seen REAL NOT NULL)"
)


# ── Private helpers ───────────────────────────────────────────────────────────


def _init_sessions_db(db_path):
    """Create the sessions table schema in the given DB (idempotent)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(_DDL_SESSIONS)
        conn.commit()
    finally:
        conn.close()


def _insert_session(
    db_path, session_id, workspace_root, pid, start_time=None, last_seen=None
):
    """Insert a session row directly into the DB for test setup."""
    now = time.time()
    if start_time is None:
        start_time = now - DEFAULT_SESSION_AGE_SECONDS
    if last_seen is None:
        last_seen = now
    _init_sessions_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO sessions "
            "(session_id, workspace_root, pid, start_time, last_seen) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, workspace_root, pid, start_time, last_seen),
        )
        conn.commit()
    finally:
        conn.close()


def _empty_db(db_path):
    """Create DB with schema but no rows."""
    _init_sessions_db(db_path)


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_list_with_sessions(tmp_path, monkeypatch):
    """_execute_sessions('list') returns success with session_id, workspace, and pid."""
    db_path = str(tmp_path / "sessions.db")
    monkeypatch.setenv(ENV_REGISTRY_PATH, db_path)
    _insert_session(db_path, SESSION_A, WORKSPACE_X, PID_A)

    from pacemaker.user_commands import _execute_sessions

    result = _execute_sessions("list", db_path)

    assert result["success"] is True
    assert SESSION_A in result["message"]
    assert WORKSPACE_X in result["message"]
    assert str(PID_A) in result["message"]


def test_list_empty(tmp_path, monkeypatch):
    """_execute_sessions('list') returns success with 'no active sessions' when table is empty."""
    db_path = str(tmp_path / "sessions.db")
    monkeypatch.setenv(ENV_REGISTRY_PATH, db_path)
    _empty_db(db_path)

    from pacemaker.user_commands import _execute_sessions

    result = _execute_sessions("list", db_path)

    assert result["success"] is True
    msg_lower = result["message"].lower()
    assert (
        "no active" in msg_lower
        or "no sessions" in msg_lower
        or "0 session" in msg_lower
    )


def test_no_subcommand(tmp_path, monkeypatch):
    """_execute_sessions(None) returns success=False with a usage hint mentioning 'list'."""
    db_path = str(tmp_path / "sessions.db")
    monkeypatch.setenv(ENV_REGISTRY_PATH, db_path)

    from pacemaker.user_commands import _execute_sessions

    result = _execute_sessions(None, db_path)

    assert result["success"] is False
    assert (
        "list" in result["message"].lower() or "subcommand" in result["message"].lower()
    )


def test_db_error(tmp_path, monkeypatch):
    """sqlite3.Error during DB access returns success=False with an error message."""
    db_path = str(tmp_path / "sessions.db")
    monkeypatch.setenv(ENV_REGISTRY_PATH, db_path)

    # Create the DB file so _load_session_rows passes the os.path.exists() check
    # and reaches the get_connection() call (which we then mock to raise).
    open(db_path, "w").close()

    with patch(
        "pacemaker.session_registry.db.get_connection",
        side_effect=sqlite3.Error("disk I/O error"),
    ):
        from pacemaker.user_commands import _execute_sessions

        result = _execute_sessions("list", db_path)

    assert result["success"] is False
    assert "error" in result["message"].lower() or "fail" in result["message"].lower()


def test_list_multiple_sessions(tmp_path, monkeypatch):
    """_execute_sessions('list') includes all sessions when multiple rows are present."""
    db_path = str(tmp_path / "sessions.db")
    monkeypatch.setenv(ENV_REGISTRY_PATH, db_path)
    _insert_session(db_path, SESSION_A, WORKSPACE_X, PID_A)
    _insert_session(db_path, SESSION_B, WORKSPACE_Y, PID_B)

    from pacemaker.user_commands import _execute_sessions

    result = _execute_sessions("list", db_path)

    assert result["success"] is True
    assert SESSION_A in result["message"]
    assert SESSION_B in result["message"]
    assert WORKSPACE_X in result["message"]
    assert WORKSPACE_Y in result["message"]


def test_list_db_not_exist(tmp_path, monkeypatch):
    """_execute_sessions('list') returns success gracefully when DB file does not yet exist."""
    db_path = str(tmp_path / "nonexistent_sessions.db")
    monkeypatch.setenv(ENV_REGISTRY_PATH, db_path)

    from pacemaker.user_commands import _execute_sessions

    result = _execute_sessions("list", db_path)

    assert result["success"] is True
    msg_lower = result["message"].lower()
    assert (
        "no active" in msg_lower
        or "no sessions" in msg_lower
        or "0 session" in msg_lower
    )


def test_unknown_subcommand(tmp_path, monkeypatch):
    """_execute_sessions('bogus') returns success=False with an informative message."""
    db_path = str(tmp_path / "sessions.db")
    monkeypatch.setenv(ENV_REGISTRY_PATH, db_path)

    from pacemaker.user_commands import _execute_sessions

    result = _execute_sessions("bogus", db_path)

    assert result["success"] is False
    assert "bogus" in result["message"] or "unknown" in result["message"].lower()


def test_list_output_header(tmp_path, monkeypatch):
    """_execute_sessions('list') output contains all three column headers: SESSION, WORKSPACE, PID."""
    db_path = str(tmp_path / "sessions.db")
    monkeypatch.setenv(ENV_REGISTRY_PATH, db_path)
    _insert_session(db_path, SESSION_A, WORKSPACE_X, PID_A)

    from pacemaker.user_commands import _execute_sessions

    result = _execute_sessions("list", db_path)

    assert result["success"] is True
    msg_upper = result["message"].upper()
    assert "SESSION" in msg_upper
    assert "WORKSPACE" in msg_upper
    assert "PID" in msg_upper


def test_list_filters_stale_sessions(tmp_path, monkeypatch):
    """_execute_sessions('list') only shows sessions with last_seen within the last 20 minutes.

    Seeds one fresh session (last_seen=now) and one stale session (last_seen=21 minutes ago).
    Verifies that only the fresh session appears and the stale session is filtered out.
    """
    db_path = str(tmp_path / "sessions.db")
    monkeypatch.setenv(ENV_REGISTRY_PATH, db_path)

    stale_last_seen = time.time() - (21 * 60)  # 21 minutes ago
    fresh_last_seen = time.time()  # now

    _insert_session(db_path, SESSION_A, WORKSPACE_X, PID_A, last_seen=stale_last_seen)
    _insert_session(db_path, SESSION_B, WORKSPACE_Y, PID_B, last_seen=fresh_last_seen)

    from pacemaker.user_commands import _execute_sessions

    result = _execute_sessions("list", db_path)

    assert result["success"] is True
    assert SESSION_B in result["message"], "fresh session should appear"
    assert (
        SESSION_A not in result["message"]
    ), "stale session (21 min old) should be filtered out"
