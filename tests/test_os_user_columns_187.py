"""Story #187 AC4/AC6: additive nullable ``os_user`` columns in usage.db.

Real SQLite files, no mocks. A "pre-upgrade" DB is built with the unchanged
``database.SCHEMA`` at ``user_version = 1`` (exactly what shipped before).
"""

import sqlite3
import threading
from datetime import datetime, timezone

import pytest

from pacemaker import database as db
from schema_v1_support import v1_schema
from pacemaker.data_dir import os_user

OS_USER_TABLES = [
    "usage_snapshots",
    "pacing_decisions",
    "blockage_events",
    "activity_events",
    "governance_events",
    "accumulated_costs",
]


def _columns(path, table):
    conn = sqlite3.connect(path)
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def _make_v1_db(path):
    """A database exactly as the previous release left it (version 1)."""
    conn = sqlite3.connect(path)
    conn.executescript(v1_schema())
    conn.execute("PRAGMA user_version = 1")
    conn.execute(
        "INSERT INTO activity_events (timestamp, event_code, status, session_id) "
        "VALUES (1.0, 'IV', 'green', 'old-session')"
    )
    conn.execute(
        "INSERT INTO usage_snapshots (timestamp, five_hour_util, seven_day_util, "
        "session_id) VALUES (1, 10.0, 20.0, 'old-session')"
    )
    conn.commit()
    conn.close()


class TestMigration:
    def test_schema_version_bumped_for_migration(self):
        assert db.SCHEMA_VERSION == 2

    def test_fresh_db_has_os_user_columns(self, tmp_path):
        path = str(tmp_path / "usage.db")
        assert db.initialize_database(path)
        for table in OS_USER_TABLES:
            assert "os_user" in _columns(path, table), table

    def test_v1_db_is_upgraded_in_place_and_rows_survive(self, tmp_path):
        path = str(tmp_path / "usage.db")
        _make_v1_db(path)
        assert "os_user" not in _columns(path, "activity_events")

        assert db.initialize_database(path)

        for table in OS_USER_TABLES:
            assert "os_user" in _columns(path, table), table
        conn = sqlite3.connect(path)
        try:
            row = conn.execute(
                "SELECT event_code, status, session_id, os_user FROM activity_events"
            ).fetchone()
            assert row == ("IV", "green", "old-session", None)
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        finally:
            conn.close()

    def test_migration_is_idempotent(self, tmp_path):
        path = str(tmp_path / "usage.db")
        _make_v1_db(path)
        assert db.initialize_database(path)
        db.reset_initialized_dbs()
        # Force the migration step again even though the version is current.
        with db.get_db_connection(path) as conn:
            db.migrate_os_user_columns(conn)
            db.migrate_os_user_columns(conn)
        assert _columns(path, "activity_events").count("os_user") == 1

    def test_migration_tolerates_concurrent_openers(self, tmp_path):
        path = str(tmp_path / "usage.db")
        _make_v1_db(path)
        errors = []

        def opener():
            try:
                # Production path: every writer goes through execute_with_retry
                # (the first-ever journal_mode=WAL switch can report "locked").
                db.execute_with_retry(path, db.migrate_os_user_columns)
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=opener) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert errors == []
        assert _columns(path, "usage_snapshots").count("os_user") == 1

    def test_old_style_insert_without_os_user_still_works(self, tmp_path):
        """Old code (or the monitor's fixtures) inserting without os_user."""
        path = str(tmp_path / "usage.db")
        assert db.initialize_database(path)
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO activity_events (timestamp, event_code, status, session_id) "
            "VALUES (2.0, 'TD', 'red', 's')"
        )
        conn.commit()
        assert conn.execute("SELECT os_user FROM activity_events").fetchone() == (None,)
        conn.close()

    def test_old_reader_queries_still_work_on_upgraded_db(self, tmp_path):
        """The monitor selects explicit columns; extra column must not matter."""
        path = str(tmp_path / "usage.db")
        _make_v1_db(path)
        db.initialize_database(path)
        recent = db.get_recent_activity(path, window_seconds=10**10)
        assert recent == [{"event_code": "IV", "status": "green"}]


class TestWritersRecordOsUser:
    def test_activity_event(self, tmp_path):
        path = str(tmp_path / "usage.db")
        db.initialize_database(path)
        assert db.record_activity_event(path, "IV", "green", "s1")
        conn = sqlite3.connect(path)
        assert conn.execute("SELECT os_user FROM activity_events").fetchone() == (
            os_user(),
        )
        conn.close()

    def test_governance_event(self, tmp_path):
        path = str(tmp_path / "usage.db")
        db.initialize_database(path)
        assert db.record_governance_event(path, "IV", "proj", "s1", "text")
        conn = sqlite3.connect(path)
        assert conn.execute("SELECT os_user FROM governance_events").fetchone() == (
            os_user(),
        )
        conn.close()

    def test_usage_snapshot(self, tmp_path):
        path = str(tmp_path / "usage.db")
        db.initialize_database(path)
        assert db.insert_usage_snapshot(
            path, datetime.now(timezone.utc), 1.0, None, 2.0, None, "s1"
        )
        conn = sqlite3.connect(path)
        assert conn.execute("SELECT os_user FROM usage_snapshots").fetchone() == (
            os_user(),
        )
        conn.close()

    def test_pacing_decision(self, tmp_path):
        path = str(tmp_path / "usage.db")
        db.initialize_database(path)
        assert db.insert_pacing_decision(
            path, datetime.now(timezone.utc), True, 5, "s1"
        )
        conn = sqlite3.connect(path)
        assert conn.execute("SELECT os_user FROM pacing_decisions").fetchone() == (
            os_user(),
        )
        conn.close()

    def test_blockage_event(self, tmp_path):
        path = str(tmp_path / "usage.db")
        db.initialize_database(path)
        assert db.record_blockage(path, "intent_validation", "r", "pre_tool_use", "s1")
        conn = sqlite3.connect(path)
        assert conn.execute("SELECT os_user FROM blockage_events").fetchone() == (
            os_user(),
        )
        conn.close()

    def test_writer_on_unmigrated_v1_db_degrades_loudly_not_silently(self, tmp_path):
        """A writer opened on a v1 DB via initialize_database migrates first."""
        path = str(tmp_path / "usage.db")
        _make_v1_db(path)
        db.initialize_database(path)
        assert db.record_activity_event(path, "CC", "red", "s2")
        conn = sqlite3.connect(path)
        rows = conn.execute(
            "SELECT session_id, os_user FROM activity_events ORDER BY id"
        ).fetchall()
        conn.close()
        assert rows == [("old-session", None), ("s2", os_user())]


def test_writer_migrates_v1_db_on_first_open_without_initialize(tmp_path):
    """Another user's older install left the shared DB at v1; a writer that never
    called initialize_database() must still succeed (not 'no such column')."""
    path = str(tmp_path / "usage.db")
    _make_v1_db(path)
    assert db.record_activity_event(path, "PA", "green", "s3")
    assert "os_user" in _columns(path, "activity_events")


@pytest.mark.parametrize("table", OS_USER_TABLES)
def test_os_user_column_is_nullable(tmp_path, table):
    path = str(tmp_path / "usage.db")
    db.initialize_database(path)
    conn = sqlite3.connect(path)
    info = {r[1]: r for r in conn.execute(f"PRAGMA table_info({table})")}
    conn.close()
    assert info["os_user"][3] == 0  # notnull == 0
