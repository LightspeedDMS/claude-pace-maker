#!/usr/bin/env python3
"""
Unit tests for governance_events database functions.

Tests record_governance_event, cleanup_old_governance_events using real SQLite
(no mocking). Follows TDD red-green-refactor cycle.
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
    record_governance_event,
    cleanup_old_governance_events,
)


@pytest.fixture
def temp_db():
    """Create temporary database path with initialized schema."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    initialize_database(path)
    yield path
    Path(path).unlink(missing_ok=True)


class TestGovernanceEventsTable:
    """Tests for governance_events table schema creation."""

    def test_create_governance_events_table(self, temp_db):
        """governance_events table exists after initialize_database."""
        conn = sqlite3.connect(temp_db)
        try:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='governance_events'"
            )
            row = cursor.fetchone()
            assert row is not None
            assert row[0] == "governance_events"
        finally:
            conn.close()

    def test_governance_events_has_expected_columns(self, temp_db):
        """governance_events table has all required columns."""
        conn = sqlite3.connect(temp_db)
        try:
            cursor = conn.execute("PRAGMA table_info(governance_events)")
            columns = {row[1] for row in cursor.fetchall()}
            expected = {
                "id",
                "timestamp",
                "event_type",
                "project_name",
                "session_id",
                "feedback_text",
                "created_at",
            }
            assert expected == columns
        finally:
            conn.close()

    def test_governance_events_wal_mode(self, temp_db):
        """Database uses WAL journal mode after initialization."""
        conn = sqlite3.connect(temp_db)
        try:
            cursor = conn.execute("PRAGMA journal_mode")
            mode = cursor.fetchone()[0]
            assert mode == "wal"
        finally:
            conn.close()


class TestRecordGovernanceEvent:
    """Tests for record_governance_event function."""

    def test_record_governance_event_iv(self, temp_db):
        """record_governance_event stores IV (intent validation) event."""
        record_governance_event(
            temp_db,
            "IV",
            "test-project",
            "session-1",
            "Missing INTENT: marker in message",
        )
        conn = sqlite3.connect(temp_db)
        try:
            row = conn.execute(
                "SELECT event_type, project_name, session_id, feedback_text "
                "FROM governance_events"
            ).fetchone()
            assert row is not None
            assert row[0] == "IV"
            assert row[1] == "test-project"
            assert row[2] == "session-1"
            assert row[3] == "Missing INTENT: marker in message"
        finally:
            conn.close()

    def test_record_governance_event_td(self, temp_db):
        """record_governance_event stores TD (TDD failure) event."""
        record_governance_event(
            temp_db,
            "TD",
            "my-project",
            "session-2",
            "TDD declaration missing for core code",
        )
        conn = sqlite3.connect(temp_db)
        try:
            row = conn.execute(
                "SELECT event_type, feedback_text FROM governance_events"
            ).fetchone()
            assert row is not None
            assert row[0] == "TD"
            assert row[1] == "TDD declaration missing for core code"
        finally:
            conn.close()

    def test_record_governance_event_cc(self, temp_db):
        """record_governance_event stores CC (clean code) event."""
        record_governance_event(
            temp_db,
            "CC",
            "another-proj",
            "session-3",
            "Clean code rule violation: method too long",
        )
        conn = sqlite3.connect(temp_db)
        try:
            row = conn.execute(
                "SELECT event_type, feedback_text FROM governance_events"
            ).fetchone()
            assert row is not None
            assert row[0] == "CC"
            assert row[1] == "Clean code rule violation: method too long"
        finally:
            conn.close()

    def test_record_governance_event_returns_true_on_success(self, temp_db):
        """record_governance_event returns True when insert succeeds."""
        result = record_governance_event(
            temp_db,
            "IV",
            "proj",
            "sess",
            "feedback",
        )
        assert result is True

    def test_record_governance_event_returns_false_on_error(self):
        """record_governance_event returns False when DB path is invalid."""
        result = record_governance_event(
            "/nonexistent/path/db.sqlite",
            "IV",
            "proj",
            "sess",
            "feedback",
        )
        assert result is False

    def test_record_governance_event_stores_timestamp(self, temp_db):
        """record_governance_event stores a reasonable timestamp."""
        before = time.time()
        record_governance_event(
            temp_db,
            "IV",
            "proj",
            "sess",
            "feedback",
        )
        after = time.time()

        conn = sqlite3.connect(temp_db)
        try:
            row = conn.execute("SELECT timestamp FROM governance_events").fetchone()
            assert row is not None
            assert before <= row[0] <= after
        finally:
            conn.close()

    def test_governance_events_concurrent_writes(self, temp_db):
        """Multiple threads can write governance events concurrently."""
        errors = []

        def writer(thread_id):
            try:
                for i in range(5):
                    result = record_governance_event(
                        temp_db,
                        "IV",
                        f"proj-{thread_id}",
                        f"session-{thread_id}",
                        f"feedback-{i}",
                    )
                    if not result:
                        errors.append(f"Thread {thread_id} write {i} failed")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"Concurrent write errors: {errors}"

        conn = sqlite3.connect(temp_db)
        try:
            count = conn.execute("SELECT COUNT(*) FROM governance_events").fetchone()[0]
            assert count == 20  # 4 threads * 5 writes
        finally:
            conn.close()


class TestCleanupOldGovernanceEvents:
    """Tests for cleanup_old_governance_events function."""

    def test_cleanup_old_governance_events_removes_expired(self, temp_db):
        """cleanup removes events older than max_age_seconds."""
        # Insert an event with old timestamp
        conn = sqlite3.connect(temp_db)
        try:
            old_ts = time.time() - 90000  # 25 hours ago
            conn.execute(
                "INSERT INTO governance_events "
                "(timestamp, event_type, project_name, session_id, feedback_text) "
                "VALUES (?, ?, ?, ?, ?)",
                (old_ts, "IV", "proj", "sess", "old feedback"),
            )
            conn.commit()
        finally:
            conn.close()

        deleted = cleanup_old_governance_events(temp_db, max_age_seconds=86400)
        assert deleted == 1

        conn = sqlite3.connect(temp_db)
        try:
            count = conn.execute("SELECT COUNT(*) FROM governance_events").fetchone()[0]
            assert count == 0
        finally:
            conn.close()

    def test_cleanup_old_governance_events_preserves_recent(self, temp_db):
        """cleanup preserves events within max_age_seconds."""
        record_governance_event(
            temp_db,
            "IV",
            "proj",
            "sess",
            "recent feedback",
        )

        deleted = cleanup_old_governance_events(temp_db, max_age_seconds=86400)
        assert deleted == 0

        conn = sqlite3.connect(temp_db)
        try:
            count = conn.execute("SELECT COUNT(*) FROM governance_events").fetchone()[0]
            assert count == 1
        finally:
            conn.close()

    def test_cleanup_returns_negative_one_on_error(self):
        """cleanup returns -1 when database is inaccessible."""
        result = cleanup_old_governance_events(
            "/nonexistent/path/db.sqlite",
            max_age_seconds=86400,
        )
        assert result == -1
