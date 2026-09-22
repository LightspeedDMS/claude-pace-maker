#!/usr/bin/env python3
"""
Tests for issue #145 — rising "database is locked" errors on usage.db.

Evidence (see CLAUDE.md / issue #145 for the full trail):
- `initialize_database()` was the ONLY writer function in database.py that did
  NOT go through `execute_with_retry()` — a single OperationalError("database
  is locked") failed it outright with no retry, unlike every other write path
  in this module. This matches the "Failed to initialize database" log lines.
- `initialize_database()` ran `cursor.executescript(SCHEMA)` (9 CREATE TABLE +
  ~8 CREATE INDEX statements) on every call whose db_path was not already in
  the current PROCESS's in-memory `_initialized_dbs` cache. Since each hook
  invocation is a fresh `python3 -m pacemaker.hook <event>` process, this
  cache never survives across invocations in production — so real DDL
  execution was attempted on every single hook call, not just the first ever.
- `cleanup_old_snapshots()` (and friends) issued one unbounded DELETE per
  call, holding the WAL writer lock for however long that DELETE took,
  starving other concurrent writers.

These tests use real SQLite in tmp paths (no mocking of sqlite3, and no
patching of any FUNCTION or method in `pacemaker.database` — the system
under test). Two tests use `monkeypatch.setattr()` on plain module-level
CONSTANTS (`database.DB_TIMEOUT`, `database.CLEANUP_MAX_BATCHES`) — this is
parameterization of a real, unmodified code path (shrinking a busy-wait
window / a batch cap to keep the test fast and deterministic), not a
behavior replacement of any function.

Every behavior is proven by black-box input/output on real files:
- The retry fix is proven by forcing GENUINE lock contention on a real,
  never-before-initialized db path (so real first-time DDL is required and
  must fight for the writer lock), and checking the return value.
- The "skip DDL when schema is current" fast path is proven by seeding a
  file whose `PRAGMA user_version` already claims "current" but which has
  NO tables at all — if the fast path is real, `initialize_database()` will
  trust the marker and the tables will still be absent afterward. That
  outcome is only possible if `executescript(SCHEMA)` was never called.
- The batching fix is proven by seeding more old rows than
  `CLEANUP_MAX_BATCHES * CLEANUP_BATCH_SIZE` can delete in one call and
  observing the bounded, partial result — a single unbounded DELETE could
  never produce that outcome.
"""

import os
import sqlite3
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path

import pytest

import pacemaker.database as database
from pacemaker.database import (
    CLEANUP_BATCH_SIZE,
    CLEANUP_MAX_BATCHES,
    SCHEMA_VERSION,
    cleanup_old_activity,
    cleanup_old_governance_events,
    cleanup_old_snapshots,
    get_db_connection,
    initialize_database,
    record_activity_event,
    reset_initialized_dbs,
)

# Named constants (avoid magic numbers scattered through the test bodies).
_SQLITE_CONNECT_TIMEOUT_SECONDS = 5.0
_WAIT_FOR_LOCK_HOLDER_SECONDS = 2.0
_BRIEF_LOCK_HOLD_SECONDS = 0.3
_CONTENDED_FIRST_INIT_LOCK_HOLD_SECONDS = 0.25
_SHRUNK_DB_TIMEOUT_SECONDS = 0.05
_HOOK_WRITE_BUDGET_SECONDS = 4.0
_ACTIVITY_MAX_AGE_SECONDS = 60
_GOVERNANCE_MAX_AGE_SECONDS = 86400
_SNAPSHOT_RETENTION_DAYS = 60
_SECONDS_PER_DAY = 86400
_OLD_ACTIVITY_AGE_SECONDS = 120  # older than _ACTIVITY_MAX_AGE_SECONDS
_OLD_GOVERNANCE_AGE_SECONDS = 90000  # older than 24h
_RECENT_GOVERNANCE_AGE_SECONDS = 10  # newer than 24h -- must be preserved
_OLD_SNAPSHOT_AGE_DAYS = 200  # older than _SNAPSHOT_RETENTION_DAYS
_SHRUNK_CLEANUP_MAX_BATCHES = 3  # keeps the bound-hit test's seed data small
_EXTRA_ROWS_BEYOND_CAP = 250
# Row counts chosen so a single cap (CLEANUP_BATCH_SIZE) is not enough to
# delete everything in one batch, forcing the loop to run more than once.
_ACTIVITY_BATCH_FORCING_REMAINDER = 137
_SNAPSHOT_BATCH_FORCING_REMAINDER = 50
_EXPECTED_GOVERNANCE_OLD_ROWS_DELETED = 1


@pytest.fixture
def temp_db():
    """Create a temporary database path with an initialized schema."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    reset_initialized_dbs()
    assert initialize_database(path) is True
    yield path
    reset_initialized_dbs()
    for suffix in ("", "-wal", "-shm"):
        Path(path + suffix).unlink(missing_ok=True)


def _hold_write_lock_briefly(
    db_path: str, hold_seconds: float, started: threading.Event
):
    """Open a real connection, take BEGIN IMMEDIATE against an EXISTING
    table, hold it, then commit.

    Runs in a background thread to simulate a concurrent writer (another
    hook process) briefly occupying the single WAL writer slot. Always
    closes the connection, even if an assertion/exception fires mid-hold.
    """
    with closing(
        sqlite3.connect(db_path, timeout=_SQLITE_CONNECT_TIMEOUT_SECONDS)
    ) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO activity_events (timestamp, event_code, status, session_id) "
            "VALUES (?, 'XX', 'green', 'lock-holder')",
            (time.time(),),
        )
        started.set()
        time.sleep(hold_seconds)
        conn.commit()


def _hold_write_lock_on_fresh_db(
    db_path: str, hold_seconds: float, started: threading.Event
):
    """Same idea as `_hold_write_lock_briefly`, but for a db path that has
    NOT been initialized yet — creates its own placeholder table so the
    held transaction is a genuine write (real DDL + a row), forcing any
    concurrent first-time `initialize_database()` DDL to contend for the
    writer lock for real.
    """
    with closing(
        sqlite3.connect(db_path, timeout=_SQLITE_CONNECT_TIMEOUT_SECONDS)
    ) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("CREATE TABLE placeholder (x INTEGER)")
        conn.execute("INSERT INTO placeholder VALUES (1)")
        started.set()
        time.sleep(hold_seconds)
        conn.commit()


class TestHookWritePathSucceedsUnderBriefContention:
    """The hook write path must succeed within budget, not error, when
    another connection briefly holds the write lock (issue #145 TDD ask #1).
    """

    def test_record_activity_event_succeeds_while_lock_briefly_held(self, temp_db):
        started = threading.Event()
        t = threading.Thread(
            target=_hold_write_lock_briefly,
            args=(temp_db, _BRIEF_LOCK_HOLD_SECONDS, started),
        )
        t.start()
        try:
            assert started.wait(
                timeout=_WAIT_FOR_LOCK_HOLDER_SECONDS
            ), "lock-holder thread never signaled readiness"

            t0 = time.monotonic()
            result = record_activity_event(temp_db, "PA", "green", "session-under-test")
            elapsed = time.monotonic() - t0

            assert result is True
            # Must resolve well inside the retry/busy-timeout budget, not hang.
            assert elapsed < _HOOK_WRITE_BUDGET_SECONDS
        finally:
            t.join()


class TestInitializeDatabaseSkipsDDLWhenSchemaCurrent:
    """initialize_database() must trust an already-current schema marker
    and skip DDL entirely (issue #145 TDD ask #2)."""

    def test_skips_ddl_when_user_version_already_marks_schema_current(self, tmp_path):
        """Seed a db file with ONLY the version pragma set (no tables at
        all). If the fast path genuinely trusts PRAGMA user_version and
        skips executescript(SCHEMA), the tables stay absent afterward — an
        outcome only possible if the DDL never ran, proven without patching
        anything.
        """
        db_path = str(tmp_path / "fake_current_schema.db")
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
        reset_initialized_dbs()

        result = initialize_database(db_path)
        assert result is True

        with closing(sqlite3.connect(db_path)) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert tables == set(), "DDL ran even though the version marker said current"

    def test_first_ever_call_still_creates_schema(self, tmp_path):
        """A genuinely new db_path (user_version defaults to 0) must still
        get its schema created for real."""
        db_path = str(tmp_path / "brand_new.db")
        reset_initialized_dbs()

        assert initialize_database(db_path) is True

        with closing(sqlite3.connect(db_path)) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert "usage_snapshots" in tables
        assert "activity_events" in tables

    def test_schema_version_pragma_is_set_after_first_ever_call(self, tmp_path):
        db_path = str(tmp_path / "version_marked.db")
        reset_initialized_dbs()

        assert initialize_database(db_path) is True

        with closing(sqlite3.connect(db_path)) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == SCHEMA_VERSION


class TestInitializeDatabaseIsRetriedOnLock:
    """initialize_database() must go through the same retry machinery as
    every other writer in this module — a transient lock must be retried,
    not fail outright on the first attempt (issue #145 TDD ask: 'is
    init_database covered by execute_with_retry at all?' — it was NOT,
    before this fix).

    This is proven with GENUINE lock contention on a never-before-
    initialized db path, so `initialize_database()` must attempt real,
    first-time DDL that genuinely needs the writer lock (an already-current
    schema's idempotent DDL needs no lock at all and would not reproduce
    the bug this test guards against).
    """

    def test_first_time_schema_creation_recovers_from_transient_lock(
        self, tmp_path, monkeypatch
    ):
        # Shrink the per-attempt busy-wait window (a plain module constant,
        # not a function) so the held write lock actually surfaces as
        # OperationalError to the retry loop, instead of being silently
        # absorbed by SQLite's own busy_timeout. The retry loop's own
        # sleeps (0.1s, 0.2s) then give the background lock-holder time to
        # release before a later attempt succeeds.
        monkeypatch.setattr(database, "DB_TIMEOUT", _SHRUNK_DB_TIMEOUT_SECONDS)
        db_path = str(tmp_path / "contended_fresh.db")
        reset_initialized_dbs()

        started = threading.Event()
        t = threading.Thread(
            target=_hold_write_lock_on_fresh_db,
            args=(db_path, _CONTENDED_FIRST_INIT_LOCK_HOLD_SECONDS, started),
        )
        t.start()
        try:
            assert started.wait(
                timeout=_WAIT_FOR_LOCK_HOLDER_SECONDS
            ), "lock-holder thread never signaled readiness"

            result = initialize_database(db_path)

            assert result is True
            with closing(sqlite3.connect(db_path)) as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
            assert "usage_snapshots" in tables
        finally:
            t.join()


class TestCleanupOldActivityBatching:
    """cleanup_old_activity() must delete in bounded batches, not one
    unbounded DELETE per call (issue #145 TDD ask #3)."""

    def test_deletes_all_matching_rows_when_under_the_batch_cap(self, temp_db):
        now = time.time()
        n_old_rows = CLEANUP_BATCH_SIZE * 2 + _ACTIVITY_BATCH_FORCING_REMAINDER
        with closing(
            sqlite3.connect(temp_db, timeout=_SQLITE_CONNECT_TIMEOUT_SECONDS)
        ) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executemany(
                "INSERT INTO activity_events "
                "(timestamp, event_code, status, session_id) "
                "VALUES (?, 'IV', 'green', 'seed')",
                [(now - _OLD_ACTIVITY_AGE_SECONDS - i,) for i in range(n_old_rows)],
            )
            conn.commit()

        deleted = cleanup_old_activity(
            temp_db, max_age_seconds=_ACTIVITY_MAX_AGE_SECONDS
        )

        assert deleted == n_old_rows
        with closing(sqlite3.connect(temp_db)) as conn:
            remaining = conn.execute("SELECT COUNT(*) FROM activity_events").fetchone()[
                0
            ]
        assert remaining == 0

    def test_returns_zero_when_nothing_to_delete(self, temp_db):
        assert (
            cleanup_old_activity(temp_db, max_age_seconds=_ACTIVITY_MAX_AGE_SECONDS)
            == 0
        )

    def test_returns_negative_on_error(self):
        assert cleanup_old_activity("/nonexistent-dir-145/activity.db") == -1


class TestCleanupBatchingIsBoundedAndTerminates:
    """The batch loop must be bounded (Messi Rule 14): seeding more old
    rows than CLEANUP_MAX_BATCHES * CLEANUP_BATCH_SIZE must leave a
    remainder rather than draining the table in one call. A single
    unbounded DELETE could never produce this outcome — this is direct
    proof that batching (and its cap) are real, without patching anything.
    """

    def test_cleanup_old_activity_stops_at_the_bound_and_leaves_remainder(
        self, temp_db, monkeypatch
    ):
        # Shrink the cap (a plain module constant, not a function) to keep
        # the seeded row count small and the test fast.
        monkeypatch.setattr(
            database, "CLEANUP_MAX_BATCHES", _SHRUNK_CLEANUP_MAX_BATCHES
        )
        cap = _SHRUNK_CLEANUP_MAX_BATCHES * CLEANUP_BATCH_SIZE
        now = time.time()

        with closing(
            sqlite3.connect(temp_db, timeout=_SQLITE_CONNECT_TIMEOUT_SECONDS)
        ) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executemany(
                "INSERT INTO activity_events "
                "(timestamp, event_code, status, session_id) "
                "VALUES (?, 'IV', 'green', 'seed')",
                [
                    (now - _OLD_ACTIVITY_AGE_SECONDS - i,)
                    for i in range(cap + _EXTRA_ROWS_BEYOND_CAP)
                ],
            )
            conn.commit()

        deleted = cleanup_old_activity(
            temp_db, max_age_seconds=_ACTIVITY_MAX_AGE_SECONDS
        )

        assert deleted == cap
        with closing(sqlite3.connect(temp_db)) as conn:
            remaining = conn.execute("SELECT COUNT(*) FROM activity_events").fetchone()[
                0
            ]
        assert remaining == _EXTRA_ROWS_BEYOND_CAP

    def test_batch_constants_are_positive_and_bounded(self):
        """Termination proof (Messi Rule 14): both constants are positive
        finite ints, so the batch loop always terminates within
        CLEANUP_MAX_BATCHES * CLEANUP_BATCH_SIZE deleted rows per call."""
        assert isinstance(CLEANUP_BATCH_SIZE, int) and CLEANUP_BATCH_SIZE > 0
        assert isinstance(CLEANUP_MAX_BATCHES, int) and CLEANUP_MAX_BATCHES > 0


class TestCleanupOldSnapshotsAndGovernanceEventsShareTheSameFix:
    """cleanup_old_snapshots() and cleanup_old_governance_events() must
    keep their existing correctness contract under the shared bounded
    batch helper (issue #145 TDD ask #3)."""

    def test_cleanup_old_snapshots_deletes_all_old_rows(self, temp_db):
        old_ts = int(time.time()) - _OLD_SNAPSHOT_AGE_DAYS * _SECONDS_PER_DAY
        n_old_rows = CLEANUP_BATCH_SIZE + _SNAPSHOT_BATCH_FORCING_REMAINDER
        with closing(
            sqlite3.connect(temp_db, timeout=_SQLITE_CONNECT_TIMEOUT_SECONDS)
        ) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executemany(
                "INSERT INTO usage_snapshots "
                "(timestamp, five_hour_util, seven_day_util, session_id) "
                "VALUES (?, 1.0, 1.0, 'seed')",
                [(old_ts - i,) for i in range(n_old_rows)],
            )
            conn.commit()

        deleted = cleanup_old_snapshots(
            temp_db, retention_days=_SNAPSHOT_RETENTION_DAYS
        )
        assert deleted == n_old_rows

        with closing(sqlite3.connect(temp_db)) as conn:
            remaining = conn.execute("SELECT COUNT(*) FROM usage_snapshots").fetchone()[
                0
            ]
        assert remaining == 0

    def test_cleanup_old_snapshots_returns_negative_on_error(self):
        assert cleanup_old_snapshots("/nonexistent-dir-145/usage.db") == -1

    def test_cleanup_old_governance_events_preserves_recent_deletes_old(self, temp_db):
        now = time.time()
        with closing(
            sqlite3.connect(temp_db, timeout=_SQLITE_CONNECT_TIMEOUT_SECONDS)
        ) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT INTO governance_events "
                "(timestamp, event_type, project_name, session_id, feedback_text) "
                "VALUES (?, 'IV', 'proj', 'sess', 'old')",
                (now - _OLD_GOVERNANCE_AGE_SECONDS,),
            )
            conn.execute(
                "INSERT INTO governance_events "
                "(timestamp, event_type, project_name, session_id, feedback_text) "
                "VALUES (?, 'IV', 'proj', 'sess', 'recent')",
                (now - _RECENT_GOVERNANCE_AGE_SECONDS,),
            )
            conn.commit()

        deleted = cleanup_old_governance_events(
            temp_db, max_age_seconds=_GOVERNANCE_MAX_AGE_SECONDS
        )
        assert deleted == _EXPECTED_GOVERNANCE_OLD_ROWS_DELETED

        with closing(sqlite3.connect(temp_db)) as conn:
            rows = conn.execute(
                "SELECT feedback_text FROM governance_events"
            ).fetchall()
        assert rows == [("recent",)]


class TestBusyTimeoutAlreadyReal:
    """Documents/locks in that get_db_connection() already sets a real
    SQLite busy_timeout (via sqlite3.connect(timeout=DB_TIMEOUT)) — issue
    #145 asked to verify this; it was already correct, so this is a
    regression guard, not a behavior change. Calls the real, unpatched
    helper directly."""

    def test_busy_timeout_matches_db_timeout_constant(self, temp_db):
        with get_db_connection(temp_db) as conn:
            value = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert value == int(database.DB_TIMEOUT * 1000)
