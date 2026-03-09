#!/usr/bin/env python3
"""
Unit tests for activity_events database functions.

Tests record_activity_event, get_recent_activity, and cleanup_old_activity
using real SQLite (no mocking). Follows TDD red-green-refactor cycle.
"""

import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

import pytest

from src.pacemaker.database import (
    initialize_database,
    record_activity_event,
    get_recent_activity,
    cleanup_old_activity,
)


@pytest.fixture
def temp_db():
    """Create temporary database path with initialized schema."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    initialize_database(path)
    yield path
    Path(path).unlink(missing_ok=True)


class TestRecordActivityEvent:
    """Tests for record_activity_event function."""

    def test_record_returns_true_on_success(self, temp_db):
        """record_activity_event returns True when insert succeeds."""
        result = record_activity_event(temp_db, "IV", "green", "session-1")
        assert result is True

    def test_record_persists_event_code(self, temp_db):
        """record_activity_event stores the event_code correctly."""
        record_activity_event(temp_db, "TD", "red", "session-1")

        conn = sqlite3.connect(temp_db)
        try:
            row = conn.execute("SELECT event_code FROM activity_events").fetchone()
            assert row is not None
            assert row[0] == "TD"
        finally:
            conn.close()

    def test_record_persists_status(self, temp_db):
        """record_activity_event stores the status correctly."""
        record_activity_event(temp_db, "CC", "blue", "session-1")

        conn = sqlite3.connect(temp_db)
        try:
            row = conn.execute("SELECT status FROM activity_events").fetchone()
            assert row is not None
            assert row[0] == "blue"
        finally:
            conn.close()

    def test_record_persists_session_id(self, temp_db):
        """record_activity_event stores the session_id correctly."""
        record_activity_event(temp_db, "ST", "green", "my-session-42")

        conn = sqlite3.connect(temp_db)
        try:
            row = conn.execute("SELECT session_id FROM activity_events").fetchone()
            assert row is not None
            assert row[0] == "my-session-42"
        finally:
            conn.close()

    def test_record_persists_timestamp(self, temp_db):
        """record_activity_event stores a recent timestamp."""
        before = time.time()
        record_activity_event(temp_db, "LF", "blue", "session-1")
        after = time.time()

        conn = sqlite3.connect(temp_db)
        try:
            row = conn.execute("SELECT timestamp FROM activity_events").fetchone()
            assert row is not None
            assert before <= row[0] <= after
        finally:
            conn.close()

    def test_record_all_13_event_codes(self, temp_db):
        """record_activity_event accepts all 13 valid event codes."""
        event_codes = [
            "IV",
            "TD",
            "CC",
            "ST",
            "CX",
            "PA",
            "PL",
            "LF",
            "SS",
            "SM",
            "SE",
            "SA",
            "UP",
        ]
        for code in event_codes:
            result = record_activity_event(temp_db, code, "green", "session-1")
            assert result is True, f"Failed for event code: {code}"

        conn = sqlite3.connect(temp_db)
        try:
            count = conn.execute("SELECT COUNT(*) FROM activity_events").fetchone()[0]
            assert count == 13
        finally:
            conn.close()

    def test_record_all_valid_statuses(self, temp_db):
        """record_activity_event accepts green, red, and blue statuses."""
        for status in ["green", "red", "blue"]:
            result = record_activity_event(temp_db, "IV", status, "session-1")
            assert result is True, f"Failed for status: {status}"

    def test_record_multiple_events_same_session(self, temp_db):
        """record_activity_event can record multiple events for same session."""
        record_activity_event(temp_db, "IV", "green", "session-1")
        record_activity_event(temp_db, "TD", "red", "session-1")
        record_activity_event(temp_db, "CC", "blue", "session-1")

        conn = sqlite3.connect(temp_db)
        try:
            count = conn.execute("SELECT COUNT(*) FROM activity_events").fetchone()[0]
            assert count == 3
        finally:
            conn.close()

    def test_record_multiple_sessions(self, temp_db):
        """record_activity_event supports events from different sessions."""
        record_activity_event(temp_db, "IV", "green", "session-A")
        record_activity_event(temp_db, "IV", "red", "session-B")
        record_activity_event(temp_db, "IV", "blue", "session-C")

        conn = sqlite3.connect(temp_db)
        try:
            count = conn.execute("SELECT COUNT(*) FROM activity_events").fetchone()[0]
            assert count == 3
        finally:
            conn.close()

    def test_record_returns_false_on_missing_db_directory(self, tmp_path):
        """record_activity_event returns False gracefully when DB path invalid."""
        bad_path = str(tmp_path / "nonexistent" / "subdir" / "db.db")
        # Should not raise - returns False on error
        result = record_activity_event(bad_path, "IV", "green", "session-1")
        assert result is False


class TestGetRecentActivity:
    """Tests for get_recent_activity function."""

    def test_returns_empty_list_when_no_events(self, temp_db):
        """get_recent_activity returns empty list when table is empty."""
        result = get_recent_activity(temp_db, window_seconds=10)
        assert result == []

    def test_returns_recent_events(self, temp_db):
        """get_recent_activity returns events within the time window."""
        record_activity_event(temp_db, "IV", "green", "session-1")

        result = get_recent_activity(temp_db, window_seconds=10)
        assert len(result) == 1
        assert result[0]["event_code"] == "IV"
        assert result[0]["status"] == "green"

    def test_returns_most_recent_per_event_code(self, temp_db):
        """get_recent_activity returns only most recent event per code across all sessions."""
        # Insert same code twice - only most recent should appear
        t1 = time.time() - 5
        t2 = time.time() - 1

        conn = sqlite3.connect(temp_db)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            conn.execute(
                "INSERT INTO activity_events (timestamp, event_code, status, session_id) VALUES (?, ?, ?, ?)",
                (t1, "IV", "red", "session-1"),
            )
            conn.execute(
                "INSERT INTO activity_events (timestamp, event_code, status, session_id) VALUES (?, ?, ?, ?)",
                (t2, "IV", "green", "session-2"),
            )
            conn.commit()
        finally:
            conn.close()

        result = get_recent_activity(temp_db, window_seconds=10)

        # Should have exactly one IV entry - the most recent one
        iv_events = [e for e in result if e["event_code"] == "IV"]
        assert len(iv_events) == 1
        assert iv_events[0]["status"] == "green"  # Most recent is t2 with green

    def test_excludes_events_outside_window(self, temp_db):
        """get_recent_activity excludes events older than window_seconds."""
        old_timestamp = time.time() - 100  # 100s ago

        conn = sqlite3.connect(temp_db)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            conn.execute(
                "INSERT INTO activity_events (timestamp, event_code, status, session_id) VALUES (?, ?, ?, ?)",
                (old_timestamp, "IV", "green", "session-1"),
            )
            conn.commit()
        finally:
            conn.close()

        result = get_recent_activity(temp_db, window_seconds=10)
        assert result == []

    def test_returns_multiple_event_codes(self, temp_db):
        """get_recent_activity returns one entry per unique event code."""
        record_activity_event(temp_db, "IV", "green", "session-1")
        record_activity_event(temp_db, "TD", "red", "session-1")
        record_activity_event(temp_db, "LF", "blue", "session-1")

        result = get_recent_activity(temp_db, window_seconds=10)
        codes = {e["event_code"] for e in result}
        assert codes == {"IV", "TD", "LF"}

    def test_result_contains_required_fields(self, temp_db):
        """get_recent_activity result dicts have event_code and status fields."""
        record_activity_event(temp_db, "SE", "green", "session-1")

        result = get_recent_activity(temp_db, window_seconds=10)
        assert len(result) == 1
        assert "event_code" in result[0]
        assert "status" in result[0]

    def test_handles_events_across_sessions(self, temp_db):
        """get_recent_activity aggregates events across all sessions."""
        record_activity_event(temp_db, "SA", "green", "session-A")
        record_activity_event(temp_db, "SA", "green", "session-B")
        record_activity_event(temp_db, "UP", "green", "session-C")

        result = get_recent_activity(temp_db, window_seconds=10)
        codes = {e["event_code"] for e in result}
        # SA appears in two sessions but should only be counted once
        assert "SA" in codes
        assert "UP" in codes
        assert len([e for e in result if e["event_code"] == "SA"]) == 1

    def test_returns_empty_list_on_db_error(self, tmp_path):
        """get_recent_activity returns empty list on database error."""
        bad_path = str(tmp_path / "nonexistent.db")
        result = get_recent_activity(bad_path, window_seconds=10)
        assert result == []

    def test_default_window_is_10_seconds(self, temp_db):
        """get_recent_activity default window_seconds is 10."""
        # Insert event 5 seconds ago (within default 10s window)
        recent_ts = time.time() - 5
        conn = sqlite3.connect(temp_db)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            conn.execute(
                "INSERT INTO activity_events (timestamp, event_code, status, session_id) VALUES (?, ?, ?, ?)",
                (recent_ts, "ST", "green", "session-1"),
            )
            conn.commit()
        finally:
            conn.close()

        # Call without explicit window_seconds
        result = get_recent_activity(temp_db)
        assert len(result) == 1
        assert result[0]["event_code"] == "ST"


class TestCleanupOldActivity:
    """Tests for cleanup_old_activity function."""

    def test_returns_count_of_deleted_rows(self, temp_db):
        """cleanup_old_activity returns number of deleted rows."""
        # Insert old event (90s ago)
        old_ts = time.time() - 90
        conn = sqlite3.connect(temp_db)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            conn.execute(
                "INSERT INTO activity_events (timestamp, event_code, status, session_id) VALUES (?, ?, ?, ?)",
                (old_ts, "IV", "green", "session-1"),
            )
            conn.commit()
        finally:
            conn.close()

        result = cleanup_old_activity(temp_db, max_age_seconds=60)
        assert result == 1

    def test_deletes_old_events(self, temp_db):
        """cleanup_old_activity removes events older than max_age_seconds."""
        old_ts = time.time() - 120  # 2 minutes ago

        conn = sqlite3.connect(temp_db)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            conn.execute(
                "INSERT INTO activity_events (timestamp, event_code, status, session_id) VALUES (?, ?, ?, ?)",
                (old_ts, "IV", "green", "session-1"),
            )
            conn.commit()
        finally:
            conn.close()

        cleanup_old_activity(temp_db, max_age_seconds=60)

        conn = sqlite3.connect(temp_db)
        try:
            count = conn.execute("SELECT COUNT(*) FROM activity_events").fetchone()[0]
            assert count == 0
        finally:
            conn.close()

    def test_preserves_recent_events(self, temp_db):
        """cleanup_old_activity preserves events within max_age_seconds."""
        recent_ts = time.time() - 10  # 10 seconds ago

        conn = sqlite3.connect(temp_db)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            conn.execute(
                "INSERT INTO activity_events (timestamp, event_code, status, session_id) VALUES (?, ?, ?, ?)",
                (recent_ts, "LF", "blue", "session-1"),
            )
            conn.commit()
        finally:
            conn.close()

        deleted = cleanup_old_activity(temp_db, max_age_seconds=60)
        assert deleted == 0

        conn = sqlite3.connect(temp_db)
        try:
            count = conn.execute("SELECT COUNT(*) FROM activity_events").fetchone()[0]
            assert count == 1
        finally:
            conn.close()

    def test_deletes_old_preserves_recent(self, temp_db):
        """cleanup_old_activity deletes old events while preserving recent ones."""
        now = time.time()
        old_ts = now - 120  # 2 minutes ago - should be deleted
        recent_ts = now - 10  # 10 seconds ago - should be preserved

        conn = sqlite3.connect(temp_db)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            conn.execute(
                "INSERT INTO activity_events (timestamp, event_code, status, session_id) VALUES (?, ?, ?, ?)",
                (old_ts, "IV", "green", "session-1"),
            )
            conn.execute(
                "INSERT INTO activity_events (timestamp, event_code, status, session_id) VALUES (?, ?, ?, ?)",
                (recent_ts, "LF", "blue", "session-1"),
            )
            conn.commit()
        finally:
            conn.close()

        deleted = cleanup_old_activity(temp_db, max_age_seconds=60)
        assert deleted == 1

        conn = sqlite3.connect(temp_db)
        try:
            rows = conn.execute("SELECT event_code FROM activity_events").fetchall()
            assert len(rows) == 1
            assert rows[0][0] == "LF"
        finally:
            conn.close()

    def test_returns_zero_when_nothing_to_delete(self, temp_db):
        """cleanup_old_activity returns 0 when no old events exist."""
        result = cleanup_old_activity(temp_db, max_age_seconds=60)
        assert result == 0

    def test_default_max_age_is_60_seconds(self, temp_db):
        """cleanup_old_activity default max_age_seconds is 60."""
        old_ts = time.time() - 90  # 90s ago - should be deleted with default 60s

        conn = sqlite3.connect(temp_db)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            conn.execute(
                "INSERT INTO activity_events (timestamp, event_code, status, session_id) VALUES (?, ?, ?, ?)",
                (old_ts, "SE", "green", "session-1"),
            )
            conn.commit()
        finally:
            conn.close()

        # Call without explicit max_age_seconds
        deleted = cleanup_old_activity(temp_db)
        assert deleted == 1

    def test_returns_negative_on_error(self, tmp_path):
        """cleanup_old_activity returns -1 on database error."""
        bad_path = str(tmp_path / "nonexistent.db")
        result = cleanup_old_activity(bad_path)
        assert result == -1


class TestActivityEventsConcurrency:
    """Tests for concurrent access to activity_events table."""

    def test_concurrent_writes_from_multiple_threads(self, temp_db):
        """Multiple threads can write activity events without lock errors."""
        errors = []
        results = []

        def write_events(session_id, count):
            for i in range(count):
                try:
                    r = record_activity_event(temp_db, "IV", "green", session_id)
                    results.append(r)
                except Exception as e:
                    errors.append(str(e))

        threads = []
        for i in range(5):
            t = threading.Thread(target=write_events, args=(f"session-{i}", 10))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No errors should occur
        assert errors == [], f"Got errors: {errors}"
        # All writes should succeed
        assert all(results), f"Some writes failed: {results}"

        # Verify all 50 rows are in the database
        conn = sqlite3.connect(temp_db)
        try:
            count = conn.execute("SELECT COUNT(*) FROM activity_events").fetchone()[0]
            assert count == 50
        finally:
            conn.close()

    def test_concurrent_read_write(self, temp_db):
        """Concurrent reads and writes do not cause lock errors."""
        errors = []

        # Pre-populate with some events
        for i in range(10):
            record_activity_event(temp_db, "IV", "green", f"session-{i}")

        def reader():
            try:
                get_recent_activity(temp_db, window_seconds=60)
            except Exception as e:
                errors.append(f"reader: {e}")

        def writer(idx):
            try:
                record_activity_event(temp_db, "TD", "red", f"writer-{idx}")
            except Exception as e:
                errors.append(f"writer: {e}")

        threads = []
        for i in range(10):
            threads.append(threading.Thread(target=reader))
            threads.append(threading.Thread(target=writer, args=(i,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Got concurrent errors: {errors}"
