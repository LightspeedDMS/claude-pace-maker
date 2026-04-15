"""
Unit tests for session_registry.registry module.

Tests:
- register_session: inserts or replaces a row in the sessions table
- heartbeat_and_purge: updates last_seen, purges stale rows, atomically
- list_siblings: returns other sessions in same workspace, excluding caller
- unregister_session: removes the caller's row
"""

import multiprocessing
import sqlite3
import sys
import time

import pytest

# ── Module paths for cache-busting ───────────────────────────────────────────
MOD_REGISTRY = "pacemaker.session_registry.registry"
MOD_DB = "pacemaker.session_registry.db"
MOD_PACKAGE = "pacemaker.session_registry"

# ── Environment variable names ────────────────────────────────────────────────
ENV_REGISTRY_PATH = "PACEMAKER_SESSION_REGISTRY_PATH"
ENV_TEST_MODE = "PACEMAKER_TEST_MODE"
TEST_MODE_ENABLED = "1"

# ── Test data constants (synthetic, not tied to any real filesystem path) ─────
SESSION_A = "session-aaa"
SESSION_B = "session-bbb"
SESSION_C = "session-ccc"
WORKSPACE_X = "workspace-x"
WORKSPACE_Y = "workspace-y"
PID_A = 1001
PID_B = 1002
PID_C = 1003

# ── Purge cutoff constant matching registry spec ──────────────────────────────
PURGE_CUTOFF_SECONDS = 1200  # 20 minutes

# ── Time offset constants (no magic numbers in test bodies) ──────────────────
ONE_MINUTE_SECONDS = 60
TEN_MINUTES_SECONDS = 600
START_TIME_OFFSET_EARLIER = 20  # seconds before base for ordering tests
START_TIME_OFFSET_LATER = 10  # seconds before base for ordering tests

# ── Allowed columns for _update_session_timestamp ────────────────────────────
_ALLOWED_TIMESTAMP_COLUMNS = frozenset({"last_seen", "start_time"})


# ── Private test helpers ──────────────────────────────────────────────────────


def _fetch_session_row(db, db_path, session_id):
    """Fetch a full sessions row for session_id, or None if not found.

    Returns (session_id, workspace_root, pid, start_time, last_seen) or None.
    """
    conn = db.get_connection(db_path)
    try:
        cursor = conn.execute(
            "SELECT session_id, workspace_root, pid, start_time, last_seen "
            "FROM sessions WHERE session_id=?",
            (session_id,),
        )
        return cursor.fetchone()
    finally:
        conn.close()


def _fetch_all_session_ids(db, db_path):
    """Return a set of all session_ids currently in the sessions table."""
    conn = db.get_connection(db_path)
    try:
        cursor = conn.execute("SELECT session_id FROM sessions")
        return {row[0] for row in cursor.fetchall()}
    finally:
        conn.close()


def _count_session_rows(db, db_path, session_id):
    """Return the exact count of rows for session_id (detects duplicates)."""
    conn = db.get_connection(db_path)
    try:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE session_id=?", (session_id,)
        )
        return cursor.fetchone()[0]
    finally:
        conn.close()


def _update_session_timestamp(db_path, session_id, column, value):
    """Directly update a timestamp column for a session via a raw connection.

    Centralises connection boilerplate for last_seen and start_time updates.
    Validates column against an allowed set to prevent SQL injection via
    column name interpolation.

    Args:
        db_path: Path to the SQLite file.
        session_id: The session to update.
        column: Must be one of _ALLOWED_TIMESTAMP_COLUMNS.
        value: The float timestamp to write.

    Raises:
        ValueError: if column is not in _ALLOWED_TIMESTAMP_COLUMNS.
    """
    if column not in _ALLOWED_TIMESTAMP_COLUMNS:
        raise ValueError(
            f"column must be one of {_ALLOWED_TIMESTAMP_COLUMNS}, got {column!r}"
        )
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            f"UPDATE sessions SET {column}=? WHERE session_id=?",  # noqa: S608
            (value, session_id),
        )
        conn.commit()
    finally:
        conn.close()


def _fresh_modules(monkeypatch, db_path):
    """Set env vars and return freshly imported (registry, db) modules."""
    monkeypatch.setenv(ENV_REGISTRY_PATH, db_path)
    monkeypatch.setenv(ENV_TEST_MODE, TEST_MODE_ENABLED)
    for mod in (MOD_REGISTRY, MOD_DB, MOD_PACKAGE):
        sys.modules.pop(mod, None)
    import pacemaker.session_registry.registry as reg
    import pacemaker.session_registry.db as db

    return reg, db


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """Return (registry_module, db_module, db_path) with a fresh temp DB."""
    db_path = str(tmp_path / "registry.db")
    reg, db = _fresh_modules(monkeypatch, db_path)
    db.init_schema(db_path)
    return reg, db, db_path


class TestRegisterSession:
    """Tests for register_session()."""

    def test_register_creates_row(self, registry):
        """register_session inserts a row visible in the sessions table."""
        reg, db, db_path = registry
        reg.register_session(SESSION_A, WORKSPACE_X, PID_A, db_path)

        row = _fetch_session_row(db, db_path, SESSION_A)
        assert row is not None
        assert row[0] == SESSION_A
        assert row[1] == WORKSPACE_X
        assert row[2] == PID_A

    def test_register_sets_start_time_and_last_seen(self, registry):
        """register_session sets start_time and last_seen to approximately now."""
        reg, db, db_path = registry
        before = time.time()
        reg.register_session(SESSION_A, WORKSPACE_X, PID_A, db_path)
        after = time.time()

        row = _fetch_session_row(db, db_path, SESSION_A)
        assert row is not None
        start_time = row[3]
        last_seen = row[4]
        assert before <= start_time <= after
        assert before <= last_seen <= after

    def test_register_replaces_existing_row(self, registry):
        """register_session with same session_id replaces the existing row (INSERT OR REPLACE).

        Exactly one row must exist after the second call, with updated values.
        Uses _count_session_rows (COUNT(*) query) to detect duplicate rows correctly,
        since _fetch_all_session_ids returns a set which cannot detect duplicates.
        """
        reg, db, db_path = registry
        reg.register_session(SESSION_A, WORKSPACE_X, PID_A, db_path)
        reg.register_session(SESSION_A, WORKSPACE_Y, PID_B, db_path)

        assert _count_session_rows(db, db_path, SESSION_A) == 1

        row = _fetch_session_row(db, db_path, SESSION_A)
        assert row is not None
        assert row[1] == WORKSPACE_Y
        assert row[2] == PID_B


class TestHeartbeatAndPurge:
    """Tests for heartbeat_and_purge()."""

    def test_heartbeat_updates_last_seen(self, registry):
        """heartbeat_and_purge updates last_seen for an existing row."""
        reg, db, db_path = registry
        reg.register_session(SESSION_A, WORKSPACE_X, PID_A, db_path)
        _update_session_timestamp(
            db_path, SESSION_A, "last_seen", time.time() - TEN_MINUTES_SECONDS
        )

        before_heartbeat = time.time()
        reg.heartbeat_and_purge(SESSION_A, WORKSPACE_X, PID_A, db_path)
        after_heartbeat = time.time()

        row = _fetch_session_row(db, db_path, SESSION_A)
        assert row is not None
        last_seen = row[4]
        assert before_heartbeat <= last_seen <= after_heartbeat

    def test_heartbeat_inserts_if_missing(self, registry):
        """heartbeat_and_purge inserts a row when session_id is not registered yet."""
        reg, db, db_path = registry
        reg.heartbeat_and_purge(SESSION_A, WORKSPACE_X, PID_A, db_path)

        row = _fetch_session_row(db, db_path, SESSION_A)
        assert row is not None

    def test_heartbeat_purges_stale_sessions(self, registry):
        """heartbeat_and_purge deletes sessions with last_seen older than purge_cutoff_seconds."""
        reg, db, db_path = registry
        reg.register_session(SESSION_A, WORKSPACE_X, PID_A, db_path)
        reg.register_session(SESSION_B, WORKSPACE_X, PID_B, db_path)

        stale_time = time.time() - (PURGE_CUTOFF_SECONDS + ONE_MINUTE_SECONDS)
        _update_session_timestamp(db_path, SESSION_B, "last_seen", stale_time)

        reg.heartbeat_and_purge(SESSION_A, WORKSPACE_X, PID_A, db_path)

        remaining = _fetch_all_session_ids(db, db_path)
        assert SESSION_A in remaining
        assert SESSION_B not in remaining

    def test_heartbeat_does_not_purge_fresh_sessions(self, registry):
        """heartbeat_and_purge keeps sessions with last_seen within purge window."""
        reg, db, db_path = registry
        reg.register_session(SESSION_A, WORKSPACE_X, PID_A, db_path)
        reg.register_session(SESSION_B, WORKSPACE_X, PID_B, db_path)

        fresh_time = time.time() - (PURGE_CUTOFF_SECONDS - ONE_MINUTE_SECONDS)
        _update_session_timestamp(db_path, SESSION_B, "last_seen", fresh_time)

        reg.heartbeat_and_purge(SESSION_A, WORKSPACE_X, PID_A, db_path)

        remaining = _fetch_all_session_ids(db, db_path)
        assert SESSION_A in remaining
        assert SESSION_B in remaining

    def test_heartbeat_and_purge_is_atomic(self, registry):
        """Both last_seen update and stale-row deletion occur in the same heartbeat call.

        After a single heartbeat_and_purge call, two effects must both be visible:
        - the caller's last_seen is refreshed (update effect)
        - a stale sibling is deleted (purge effect)
        This verifies atomicity: neither effect is left out when both are expected.
        """
        reg, db, db_path = registry
        reg.register_session(SESSION_A, WORKSPACE_X, PID_A, db_path)
        reg.register_session(SESSION_B, WORKSPACE_X, PID_B, db_path)

        now = time.time()
        _update_session_timestamp(
            db_path, SESSION_A, "last_seen", now - TEN_MINUTES_SECONDS
        )
        _update_session_timestamp(
            db_path,
            SESSION_B,
            "last_seen",
            now - (PURGE_CUTOFF_SECONDS + ONE_MINUTE_SECONDS),
        )

        before_call = time.time()
        reg.heartbeat_and_purge(SESSION_A, WORKSPACE_X, PID_A, db_path)
        after_call = time.time()

        remaining = _fetch_all_session_ids(db, db_path)
        row_a = _fetch_session_row(db, db_path, SESSION_A)

        assert row_a is not None
        assert before_call <= row_a[4] <= after_call
        assert SESSION_B not in remaining


class TestListSiblings:
    """Tests for list_siblings()."""

    def test_list_siblings_returns_other_sessions_in_same_workspace(self, registry):
        """list_siblings returns sessions in same workspace excluding the caller."""
        reg, db, db_path = registry
        reg.register_session(SESSION_A, WORKSPACE_X, PID_A, db_path)
        reg.register_session(SESSION_B, WORKSPACE_X, PID_B, db_path)

        siblings = reg.list_siblings(WORKSPACE_X, SESSION_A, db_path)
        session_ids = [s["session_id"] for s in siblings]

        assert SESSION_B in session_ids
        assert SESSION_A not in session_ids

    def test_list_siblings_excludes_other_workspace(self, registry):
        """list_siblings does not return sessions from a different workspace."""
        reg, db, db_path = registry
        reg.register_session(SESSION_A, WORKSPACE_X, PID_A, db_path)
        reg.register_session(SESSION_B, WORKSPACE_Y, PID_B, db_path)

        siblings = reg.list_siblings(WORKSPACE_X, SESSION_A, db_path)
        session_ids = [s["session_id"] for s in siblings]

        assert SESSION_B not in session_ids

    def test_list_siblings_returns_empty_when_alone(self, registry):
        """list_siblings returns an empty list when no other sessions share the workspace."""
        reg, db, db_path = registry
        reg.register_session(SESSION_A, WORKSPACE_X, PID_A, db_path)

        siblings = reg.list_siblings(WORKSPACE_X, SESSION_A, db_path)
        assert siblings == []

    def test_list_siblings_returns_required_fields(self, registry):
        """Each sibling dict contains session_id, start_time, last_seen, pid."""
        reg, db, db_path = registry
        reg.register_session(SESSION_A, WORKSPACE_X, PID_A, db_path)
        reg.register_session(SESSION_B, WORKSPACE_X, PID_B, db_path)

        siblings = reg.list_siblings(WORKSPACE_X, SESSION_A, db_path)
        assert len(siblings) == 1
        sibling = siblings[0]

        assert "session_id" in sibling
        assert "start_time" in sibling
        assert "last_seen" in sibling
        assert "pid" in sibling
        assert "workspace_root" in sibling
        assert sibling["workspace_root"] == WORKSPACE_X

    def test_list_siblings_ordered_by_start_time(self, registry):
        """list_siblings returns rows in ascending start_time order."""
        reg, db, db_path = registry
        reg.register_session(SESSION_A, WORKSPACE_X, PID_A, db_path)
        reg.register_session(SESSION_B, WORKSPACE_X, PID_B, db_path)
        reg.register_session(SESSION_C, WORKSPACE_X, PID_C, db_path)

        base = time.time()
        _update_session_timestamp(
            db_path, SESSION_B, "start_time", base - START_TIME_OFFSET_EARLIER
        )
        _update_session_timestamp(
            db_path, SESSION_C, "start_time", base - START_TIME_OFFSET_LATER
        )

        siblings = reg.list_siblings(WORKSPACE_X, SESSION_A, db_path)
        start_times = [s["start_time"] for s in siblings]
        assert start_times == sorted(start_times)


class TestUnregisterSession:
    """Tests for unregister_session()."""

    def test_unregister_removes_row(self, registry):
        """unregister_session deletes the session row."""
        reg, db, db_path = registry
        reg.register_session(SESSION_A, WORKSPACE_X, PID_A, db_path)
        reg.unregister_session(SESSION_A, db_path)

        row = _fetch_session_row(db, db_path, SESSION_A)
        assert row is None

    def test_unregister_nonexistent_does_not_raise(self, registry):
        """unregister_session on a non-existent session_id is a no-op (no exception)."""
        reg, _db, db_path = registry
        reg.unregister_session("nonexistent-session", db_path)  # Must not raise

    def test_unregister_leaves_other_sessions_intact(self, registry):
        """unregister_session removes only the specified session, not others."""
        reg, db, db_path = registry
        reg.register_session(SESSION_A, WORKSPACE_X, PID_A, db_path)
        reg.register_session(SESSION_B, WORKSPACE_X, PID_B, db_path)
        reg.unregister_session(SESSION_A, db_path)

        row = _fetch_session_row(db, db_path, SESSION_B)
        assert row is not None


# ── AC8: Concurrent heartbeats from 3 real processes ─────────────────────────

# ── Concurrent test constants ─────────────────────────────────────────────────
_CONCURRENT_WORKSPACE = "workspace-concurrent"
_CONCURRENT_SESSIONS = (
    "session-concurrent-0",
    "session-concurrent-1",
    "session-concurrent-2",
)
_CONCURRENT_PIDS = (7001, 7002, 7003)
_BARRIER_TIMEOUT_SECONDS = 5.0
_PROCESS_JOIN_TIMEOUT_SECONDS = 10

# ── Module tuple for cache-busting (shared by parent and child) ───────────────
_SESSION_REGISTRY_MODULES = (
    "pacemaker.session_registry.db",
    "pacemaker.session_registry.registry",
    "pacemaker.session_registry",
)


def _clear_session_registry_modules() -> None:
    """Remove session registry modules from sys.modules to force a fresh import."""
    for mod in _SESSION_REGISTRY_MODULES:
        sys.modules.pop(mod, None)


def _concurrent_worker(
    barrier: multiprocessing.Barrier,
    result_queue: multiprocessing.Queue,
    db_path: str,
    session_id: str,
    workspace: str,
    pid: int,
) -> None:
    """Worker function: register then barrier-sync then heartbeat_and_purge.

    Uses fork context (Linux default). Child processes inherit open file
    descriptors from the parent but NOT sqlite3 connection objects (registry.py
    uses _open_conn which opens and closes a new connection per call). Each child
    therefore gets a clean connection on first registry call.

    Environment variables are inherited via fork. sys.modules entries for the
    registry package are cleared so the child re-imports from scratch, avoiding
    any module-level db_path caching.

    Returns success/failure via result_queue so the main process can assert.
    """
    try:
        _clear_session_registry_modules()

        from pacemaker.session_registry import registry as reg_mod
        from pacemaker.session_registry.db import init_schema

        # Schema is already initialized by parent; init_schema is idempotent
        init_schema(db_path)

        # Pre-register so heartbeat_and_purge can UPDATE (not only INSERT)
        reg_mod.register_session(session_id, workspace, pid, db_path)

        # Synchronize: all 3 workers arrive here before any fires heartbeat
        barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)

        # Fire concurrent heartbeat — the core AC8 operation
        reg_mod.heartbeat_and_purge(session_id, workspace, pid, db_path)

        result_queue.put({"session_id": session_id, "success": True})
    except Exception as exc:
        result_queue.put(
            {"session_id": session_id, "success": False, "error": str(exc)}
        )


class TestConcurrentHeartbeats:
    """AC8: 3 concurrent heartbeats do not corrupt the DB or crash hooks."""

    def test_three_concurrent_heartbeats_succeed(self, monkeypatch, tmp_path):
        """3 processes fire heartbeat_and_purge within a barrier-synchronized window.

        Verifies:
        - All 3 heartbeats succeed (no exception, no hook crash)
        - DB contains exactly 3 valid records afterwards
        - No DB corruption (each row has the correct session_id and workspace_root)
        """
        db_path = str(tmp_path / "concurrent_registry.db")
        monkeypatch.setenv("PACEMAKER_TEST_MODE", "1")
        monkeypatch.setenv("PACEMAKER_SESSION_REGISTRY_PATH", db_path)

        # Initialize schema in parent process; clear module cache first
        _clear_session_registry_modules()
        import pacemaker.session_registry.db as db_mod

        db_mod.init_schema(db_path)

        # Fork-based context: children inherit env vars and cleared sys.modules
        ctx = multiprocessing.get_context("fork")
        barrier = ctx.Barrier(len(_CONCURRENT_SESSIONS))
        result_queue = ctx.Queue()

        processes = [
            ctx.Process(
                target=_concurrent_worker,
                args=(
                    barrier,
                    result_queue,
                    db_path,
                    _CONCURRENT_SESSIONS[i],
                    _CONCURRENT_WORKSPACE,
                    _CONCURRENT_PIDS[i],
                ),
            )
            for i in range(len(_CONCURRENT_SESSIONS))
        ]

        for p in processes:
            p.start()
        for p in processes:
            p.join(timeout=_PROCESS_JOIN_TIMEOUT_SECONDS)
            assert not p.is_alive(), (
                f"Process {p.pid} did not terminate within {_PROCESS_JOIN_TIMEOUT_SECONDS}s "
                "— heartbeat_and_purge may have deadlocked on SQLite lock contention"
            )

        # Collect results
        results = {}
        while not result_queue.empty():
            r = result_queue.get_nowait()
            results[r["session_id"]] = r

        # All 3 workers must have reported success
        for session_id in _CONCURRENT_SESSIONS:
            assert (
                session_id in results
            ), f"Worker for {session_id} did not report a result — may have crashed silently"
            assert results[session_id][
                "success"
            ], f"Worker for {session_id} failed: {results[session_id].get('error')}"

        # All 3 processes must have exited cleanly (exit code 0)
        for p in processes:
            assert (
                p.exitcode == 0
            ), f"Process {p.pid} exited with code {p.exitcode} — hook crash or unhandled exception"

        # DB must contain exactly 3 records with correct workspace
        conn = db_mod.get_connection(db_path)
        try:
            rows = conn.execute(
                "SELECT session_id, workspace_root FROM sessions WHERE workspace_root=?",
                (_CONCURRENT_WORKSPACE,),
            ).fetchall()
        finally:
            conn.close()

        assert (
            len(rows) == 3
        ), f"Expected 3 registry records after concurrent heartbeats, got {len(rows)}: {rows}"
        session_ids_in_db = {r[0] for r in rows}
        assert session_ids_in_db == set(
            _CONCURRENT_SESSIONS
        ), f"DB session_ids mismatch: expected {set(_CONCURRENT_SESSIONS)}, got {session_ids_in_db}"
