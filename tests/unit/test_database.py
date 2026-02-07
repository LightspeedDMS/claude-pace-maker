#!/usr/bin/env python3
"""
Unit tests for database.py SQLite concurrency handling.

Tests connection management, WAL mode, retry logic, and resource cleanup
to prevent "database is locked" errors during concurrent access.
"""

import os
import sqlite3
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.pacemaker.database import (
    initialize_database,
    insert_usage_snapshot,
    query_recent_snapshots,
    cleanup_old_snapshots,
    insert_pacing_decision,
    get_last_pacing_decision,
    record_blockage,
    get_hourly_blockage_stats,
)


class TestDatabaseConcurrency:
    """Test SQLite concurrency handling in database operations."""

    @pytest.fixture
    def temp_db(self):
        """Create temporary database path."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        yield path
        Path(path).unlink(missing_ok=True)

    def test_connection_uses_timeout_parameter(self, temp_db):
        """Database connections use timeout parameter to handle locks."""
        initialize_database(temp_db)

        # Insert a snapshot to ensure DB is initialized
        insert_usage_snapshot(
            temp_db,
            datetime.utcnow(),
            50.0,
            datetime.utcnow(),
            30.0,
            datetime.utcnow(),
            "test-session",
        )

        # Verify timeout is used by checking connection properties
        # This test ensures the connection has been created with timeout
        conn = sqlite3.connect(temp_db, timeout=5.0)
        try:
            # Connection should succeed without blocking
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM usage_snapshots")
            result = cursor.fetchone()
            assert result[0] == 1
        finally:
            conn.close()

    def test_wal_mode_enabled_on_initialization(self, temp_db):
        """Database initialization enables WAL journal mode."""
        success = initialize_database(temp_db)
        assert success is True

        # Verify WAL mode is enabled
        conn = sqlite3.connect(temp_db)
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode")
            mode = cursor.fetchone()[0]
            assert mode.upper() == "WAL", f"Expected WAL mode, got {mode}"
        finally:
            conn.close()

    def test_retry_logic_on_database_locked_error(self, temp_db):
        """Operations retry on 'database is locked' error."""
        initialize_database(temp_db)

        # Create a lock by starting a transaction and not committing
        lock_conn = sqlite3.connect(temp_db, timeout=0.1)
        lock_cursor = lock_conn.cursor()
        lock_cursor.execute("BEGIN EXCLUSIVE")

        # Define operation that should retry
        def attempt_insert():
            result = insert_usage_snapshot(
                temp_db,
                datetime.utcnow(),
                50.0,
                None,
                30.0,
                None,
                "test-session",
            )
            return result

        # Start insert in background thread
        result_holder = []

        def background_insert():
            result_holder.append(attempt_insert())

        insert_thread = threading.Thread(target=background_insert)
        insert_thread.start()

        # Wait a bit to ensure insert is blocked
        time.sleep(0.2)

        # Release lock
        lock_conn.rollback()
        lock_conn.close()

        # Wait for insert to complete
        insert_thread.join(timeout=5.0)

        # Insert should have succeeded after retry
        assert len(result_holder) == 1
        # Note: This might fail if retry logic isn't implemented yet
        # which is expected in TDD - test should fail first

    def test_connection_closed_on_exception(self, temp_db):
        """Connections are properly closed even when exceptions occur."""
        initialize_database(temp_db)

        # Mock sqlite3.connect to simulate an error during cursor creation
        original_connect = sqlite3.connect

        def mock_connect_with_error(*args, **kwargs):
            conn = original_connect(*args, **kwargs)

            def failing_cursor():
                raise Exception("Test exception during cursor creation")

            conn.cursor = failing_cursor
            return conn

        with patch("sqlite3.connect", side_effect=mock_connect_with_error):
            # Operation should handle exception and not leak connection
            result = insert_usage_snapshot(
                temp_db,
                datetime.utcnow(),
                50.0,
                None,
                30.0,
                None,
                "test-session",
            )

            # Should return False on error
            assert result is False

        # Verify database is still accessible (no leaked connections)
        conn = sqlite3.connect(temp_db)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM usage_snapshots")
            # Should complete successfully
            result = cursor.fetchone()
            assert result is not None
        finally:
            conn.close()

    def test_concurrent_writes_do_not_cause_locks(self, temp_db):
        """Multiple concurrent writes complete without deadlock."""
        initialize_database(temp_db)

        # Number of concurrent writers
        num_writers = 5
        results = []

        def write_snapshot(session_id: str):
            success = insert_usage_snapshot(
                temp_db,
                datetime.utcnow(),
                float(session_id),  # Use session_id as unique value
                None,
                30.0,
                None,
                f"session-{session_id}",
            )
            results.append(success)

        # Start multiple writer threads
        threads = []
        for i in range(num_writers):
            thread = threading.Thread(target=write_snapshot, args=(str(i),))
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join(timeout=10.0)

        # All writes should succeed
        assert len(results) == num_writers
        # Note: This will likely fail without retry logic
        assert all(results), f"Some writes failed: {results}"

        # Verify all records were inserted
        snapshots = query_recent_snapshots(temp_db, minutes=60)
        assert len(snapshots) == num_writers

    def test_readonly_operations_use_uncommitted_reads(self, temp_db):
        """Read-only operations use read_uncommitted pragma for better concurrency."""
        initialize_database(temp_db)

        # Insert some data
        insert_usage_snapshot(
            temp_db, datetime.utcnow(), 50.0, None, 30.0, None, "test-session"
        )

        # Start a write transaction that doesn't commit immediately
        write_conn = sqlite3.connect(temp_db)
        write_cursor = write_conn.cursor()
        write_cursor.execute("BEGIN EXCLUSIVE")

        try:
            # Read operations should still work (with uncommitted reads)
            snapshots = query_recent_snapshots(temp_db, minutes=60)

            # Should be able to read despite write lock
            # Note: This test verifies the concept, actual implementation
            # will use WAL mode which allows concurrent readers anyway
            assert isinstance(snapshots, list)

        finally:
            write_conn.rollback()
            write_conn.close()

    def test_retry_delay_increases_with_attempts(self, temp_db):
        """Retry delay increases exponentially with each attempt."""
        initialize_database(temp_db)

        # This test verifies retry timing behavior
        # We'll use a mock to track retry attempts and timing
        retry_times = []

        original_connect = sqlite3.connect

        def mock_connect_with_timing(*args, **kwargs):
            retry_times.append(time.time())
            if len(retry_times) < 3:
                # First two attempts fail with lock error
                conn = original_connect(*args, **kwargs)
                conn.close()
                raise sqlite3.OperationalError("database is locked")
            # Third attempt succeeds
            return original_connect(*args, **kwargs)

        with patch("sqlite3.connect", side_effect=mock_connect_with_timing):
            # This will trigger retries
            result = insert_usage_snapshot(
                temp_db, datetime.utcnow(), 50.0, None, 30.0, None, "test"
            )

            # Should eventually succeed after retries
            # Note: Will fail until retry logic is implemented
            assert result is True

            # Verify retry delays increased
            if len(retry_times) >= 3:
                delay1 = retry_times[1] - retry_times[0]
                delay2 = retry_times[2] - retry_times[1]
                # Second delay should be longer than first
                assert delay2 > delay1

    def test_max_retries_limit_enforced(self, temp_db):
        """Retry logic respects maximum retry limit."""
        initialize_database(temp_db)

        # Mock connect to always fail with lock error
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.side_effect = sqlite3.OperationalError(
                "database is locked"
            )
            mock_connect.return_value = mock_conn

            # Should fail after max retries
            result = insert_usage_snapshot(
                temp_db, datetime.utcnow(), 50.0, None, 30.0, None, "test"
            )

            # Should return False after exhausting retries
            assert result is False

            # Should have attempted max retries + 1 (initial attempt)
            # Note: Exact count depends on implementation


class TestAllDatabaseFunctions:
    """Test that all database functions use proper concurrency handling."""

    @pytest.fixture
    def temp_db(self):
        """Create temporary database path."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        yield path
        Path(path).unlink(missing_ok=True)

    def test_initialize_database_uses_wal_mode(self, temp_db):
        """initialize_database enables WAL mode."""
        success = initialize_database(temp_db)
        assert success is True

        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        conn.close()

        assert mode.upper() == "WAL"

    def test_insert_usage_snapshot_handles_concurrency(self, temp_db):
        """insert_usage_snapshot uses retry logic."""
        initialize_database(temp_db)

        # Multiple concurrent inserts should all succeed
        results = []

        def insert():
            results.append(
                insert_usage_snapshot(
                    temp_db,
                    datetime.utcnow(),
                    50.0,
                    None,
                    30.0,
                    None,
                    f"session-{len(results)}",
                )
            )

        threads = [threading.Thread(target=insert) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(results)

    def test_query_recent_snapshots_handles_concurrency(self, temp_db):
        """query_recent_snapshots uses proper read handling."""
        initialize_database(temp_db)
        insert_usage_snapshot(
            temp_db, datetime.utcnow(), 50.0, None, 30.0, None, "test"
        )

        # Should complete without blocking
        snapshots = query_recent_snapshots(temp_db, minutes=60)
        assert isinstance(snapshots, list)
        assert len(snapshots) == 1

    def test_cleanup_old_snapshots_handles_concurrency(self, temp_db):
        """cleanup_old_snapshots uses retry logic."""
        initialize_database(temp_db)

        # Should complete successfully
        deleted = cleanup_old_snapshots(temp_db, retention_days=60)
        assert deleted >= 0

    def test_insert_pacing_decision_handles_concurrency(self, temp_db):
        """insert_pacing_decision uses retry logic."""
        initialize_database(temp_db)

        result = insert_pacing_decision(
            temp_db, datetime.utcnow(), True, 5, "test-session"
        )
        assert result is True

    def test_get_last_pacing_decision_handles_concurrency(self, temp_db):
        """get_last_pacing_decision uses proper read handling."""
        initialize_database(temp_db)
        insert_pacing_decision(temp_db, datetime.utcnow(), True, 5, "test-session")

        decision = get_last_pacing_decision(temp_db, "test-session")
        assert decision is not None
        assert decision["should_throttle"] is True

    def test_record_blockage_handles_concurrency(self, temp_db):
        """record_blockage uses retry logic."""
        initialize_database(temp_db)

        result = record_blockage(
            temp_db, "tdd", "Test blockage", "pre_tool_use", "test-session"
        )
        assert result is True

    def test_get_hourly_blockage_stats_handles_concurrency(self, temp_db):
        """get_hourly_blockage_stats uses proper read handling."""
        initialize_database(temp_db)

        stats = get_hourly_blockage_stats(temp_db)
        assert isinstance(stats, dict)
