#!/usr/bin/env python3
"""
Tests for database concurrency handling in Langfuse metrics module.

These tests verify that metrics.py properly handles database locking
and concurrent access scenarios using proper concurrency patterns.

Story #34: Langfuse Integration Status and Metrics Display
"""

import os
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from src.pacemaker.langfuse.metrics import (
    increment_metric,
    cleanup_stale_buckets,
    get_24h_metrics,
)


class TestDatabaseConcurrency:
    """Test proper database concurrency handling in metrics functions."""

    @pytest.fixture
    def temp_db(self):
        """Create temporary database with langfuse_metrics table."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        conn = sqlite3.connect(path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS langfuse_metrics (
                bucket_timestamp INTEGER PRIMARY KEY,
                sessions_count INTEGER DEFAULT 0,
                traces_count INTEGER DEFAULT 0,
                spans_count INTEGER DEFAULT 0
            )
        """
        )
        conn.commit()
        conn.close()

        yield path

        # Cleanup
        Path(path).unlink(missing_ok=True)

    def test_increment_metric_uses_timeout(self, temp_db):
        """
        increment_metric should use connection timeout to avoid indefinite blocking.

        EXPECTED BEHAVIOR: Function uses sqlite3.connect(path, timeout=5.0)
        CURRENT BEHAVIOR: Uses sqlite3.connect(path) without timeout
        """
        # This test verifies that the connection has timeout configured
        # We'll patch sqlite3.connect to verify timeout parameter is passed
        original_connect = sqlite3.connect
        connect_calls = []

        def mock_connect(*args, **kwargs):
            connect_calls.append({"args": args, "kwargs": kwargs})
            return original_connect(*args, **kwargs)

        with patch("sqlite3.connect", side_effect=mock_connect):
            with patch("time.time", return_value=1738670850):
                increment_metric("sessions", temp_db)

        # Verify timeout was used
        assert len(connect_calls) > 0
        call = connect_calls[0]
        assert "timeout" in call["kwargs"], "Connection should use timeout parameter"
        assert call["kwargs"]["timeout"] == 5.0, "Timeout should be 5.0 seconds"

    def test_increment_metric_uses_wal_mode(self, temp_db):
        """
        increment_metric should enable WAL mode for better concurrency.

        EXPECTED BEHAVIOR: Executes PRAGMA journal_mode=WAL after connection
        CURRENT BEHAVIOR: Does not set WAL mode
        """
        # Verify WAL mode is enabled by checking journal_mode after operation
        with patch("time.time", return_value=1738670850):
            increment_metric("sessions", temp_db)

        # Check that WAL mode was set (will persist on the database)
        conn = sqlite3.connect(temp_db)
        result = conn.execute("PRAGMA journal_mode").fetchone()
        conn.close()

        # The journal mode should be WAL (it persists once set)
        assert result[0].lower() == "wal", f"Expected WAL mode, got {result[0]}"

    def test_cleanup_stale_buckets_uses_timeout(self, temp_db):
        """
        cleanup_stale_buckets should use connection timeout.

        EXPECTED BEHAVIOR: Function uses sqlite3.connect(path, timeout=5.0)
        CURRENT BEHAVIOR: Uses sqlite3.connect(path) without timeout
        """
        original_connect = sqlite3.connect
        connect_calls = []

        def mock_connect(*args, **kwargs):
            connect_calls.append({"args": args, "kwargs": kwargs})
            return original_connect(*args, **kwargs)

        with patch("sqlite3.connect", side_effect=mock_connect):
            cleanup_stale_buckets(temp_db)

        # Verify timeout was used
        assert len(connect_calls) > 0
        call = connect_calls[0]
        assert "timeout" in call["kwargs"], "Connection should use timeout parameter"
        assert call["kwargs"]["timeout"] == 5.0, "Timeout should be 5.0 seconds"

    def test_cleanup_stale_buckets_uses_wal_mode(self, temp_db):
        """
        cleanup_stale_buckets should enable WAL mode.

        EXPECTED BEHAVIOR: Executes PRAGMA journal_mode=WAL after connection
        CURRENT BEHAVIOR: Does not set WAL mode
        """
        # Verify WAL mode is enabled by checking journal_mode after operation
        cleanup_stale_buckets(temp_db)

        # Check that WAL mode was set (will persist on the database)
        conn = sqlite3.connect(temp_db)
        result = conn.execute("PRAGMA journal_mode").fetchone()
        conn.close()

        # The journal mode should be WAL (it persists once set)
        assert result[0].lower() == "wal", f"Expected WAL mode, got {result[0]}"

    def test_get_24h_metrics_uses_timeout(self, temp_db):
        """
        get_24h_metrics should use connection timeout.

        EXPECTED BEHAVIOR: Function uses sqlite3.connect(path, timeout=5.0)
        CURRENT BEHAVIOR: Uses sqlite3.connect(path) without timeout
        """
        original_connect = sqlite3.connect
        connect_calls = []

        def mock_connect(*args, **kwargs):
            connect_calls.append({"args": args, "kwargs": kwargs})
            return original_connect(*args, **kwargs)

        with patch("sqlite3.connect", side_effect=mock_connect):
            get_24h_metrics(temp_db)

        # Verify timeout was used
        assert len(connect_calls) > 0
        call = connect_calls[0]
        assert "timeout" in call["kwargs"], "Connection should use timeout parameter"
        assert call["kwargs"]["timeout"] == 5.0, "Timeout should be 5.0 seconds"

    def test_get_24h_metrics_uses_wal_mode(self, temp_db):
        """
        get_24h_metrics should enable WAL mode.

        EXPECTED BEHAVIOR: Executes PRAGMA journal_mode=WAL after connection
        CURRENT BEHAVIOR: Does not set WAL mode
        """
        # Verify WAL mode is enabled by checking journal_mode after operation
        get_24h_metrics(temp_db)

        # Check that WAL mode was set (will persist on the database)
        conn = sqlite3.connect(temp_db)
        result = conn.execute("PRAGMA journal_mode").fetchone()
        conn.close()

        # The journal mode should be WAL (it persists once set)
        assert result[0].lower() == "wal", f"Expected WAL mode, got {result[0]}"

    def test_increment_metric_retries_on_database_locked(self, temp_db):
        """
        increment_metric should retry on database locked errors.

        EXPECTED BEHAVIOR: Retries with exponential backoff on sqlite3.OperationalError
        CURRENT BEHAVIOR: Fails immediately on database locked error
        """
        # Simulate "database is locked" error on first attempt, success on second
        from pacemaker import database

        original_get_db = database.get_db_connection
        attempt_count = [0]

        @contextmanager
        def mock_get_db(db_path, readonly=False):
            attempt_count[0] += 1
            if attempt_count[0] == 1:
                # First attempt: raise locked error during operation
                conn = sqlite3.connect(db_path, timeout=5.0)
                conn.execute("PRAGMA journal_mode=WAL")

                # Create a wrapper that fails on first INSERT
                class FailingConnection:
                    def __init__(self, real_conn):
                        self.real_conn = real_conn
                        self.failed = False

                    def execute(self, sql, *args):
                        if "INSERT INTO langfuse_metrics" in sql and not self.failed:
                            self.failed = True
                            raise sqlite3.OperationalError("database is locked")
                        return self.real_conn.execute(sql, *args)

                    def commit(self):
                        return self.real_conn.commit()

                    def close(self):
                        return self.real_conn.close()

                wrapped = FailingConnection(conn)
                try:
                    yield wrapped
                finally:
                    conn.close()
            else:
                # Second attempt: use real connection
                with original_get_db(db_path, readonly) as conn:
                    yield conn

        with patch("pacemaker.database.get_db_connection", side_effect=mock_get_db):
            with patch("time.time", return_value=1738670850):
                # Should succeed after retry
                increment_metric("sessions", temp_db)

        # Verify retry occurred
        assert attempt_count[0] > 1, "Should have retried after database locked error"

    def test_cleanup_retries_on_database_locked(self, temp_db):
        """
        cleanup_stale_buckets should retry on database locked errors.

        EXPECTED BEHAVIOR: Retries with exponential backoff on sqlite3.OperationalError
        CURRENT BEHAVIOR: Fails immediately on database locked error
        """
        # Simulate "database is locked" error on first attempt
        from pacemaker import database

        original_get_db = database.get_db_connection
        attempt_count = [0]

        @contextmanager
        def mock_get_db(db_path, readonly=False):
            attempt_count[0] += 1
            if attempt_count[0] == 1:
                # First attempt: raise locked error during operation
                conn = sqlite3.connect(db_path, timeout=5.0)
                conn.execute("PRAGMA journal_mode=WAL")

                class FailingConnection:
                    def __init__(self, real_conn):
                        self.real_conn = real_conn
                        self.failed = False

                    def execute(self, sql, *args):
                        if "DELETE FROM langfuse_metrics" in sql and not self.failed:
                            self.failed = True
                            raise sqlite3.OperationalError("database is locked")
                        return self.real_conn.execute(sql, *args)

                    def commit(self):
                        return self.real_conn.commit()

                    def close(self):
                        return self.real_conn.close()

                wrapped = FailingConnection(conn)
                try:
                    yield wrapped
                finally:
                    conn.close()
            else:
                # Second attempt: use real connection
                with original_get_db(db_path, readonly) as conn:
                    yield conn

        with patch("pacemaker.database.get_db_connection", side_effect=mock_get_db):
            cleanup_stale_buckets(temp_db)

        # Verify retry occurred
        assert attempt_count[0] > 1, "Should have retried after database locked error"

    def test_get_24h_metrics_retries_on_database_locked(self, temp_db):
        """
        get_24h_metrics should retry on database locked errors.

        EXPECTED BEHAVIOR: Retries with exponential backoff on sqlite3.OperationalError
        CURRENT BEHAVIOR: Fails immediately on database locked error
        """
        # Simulate "database is locked" error on first attempt
        from pacemaker import database

        original_get_db = database.get_db_connection
        attempt_count = [0]

        @contextmanager
        def mock_get_db(db_path, readonly=False):
            attempt_count[0] += 1
            if attempt_count[0] == 1:
                # First attempt: raise locked error during operation
                conn = sqlite3.connect(db_path, timeout=5.0)
                conn.execute("PRAGMA journal_mode=WAL")

                class FailingConnection:
                    def __init__(self, real_conn):
                        self.real_conn = real_conn
                        self.failed = False

                    def execute(self, sql, *args):
                        if "SELECT COALESCE" in sql and not self.failed:
                            self.failed = True
                            raise sqlite3.OperationalError("database is locked")
                        return self.real_conn.execute(sql, *args)

                    def commit(self):
                        return self.real_conn.commit()

                    def close(self):
                        return self.real_conn.close()

                wrapped = FailingConnection(conn)
                try:
                    yield wrapped
                finally:
                    conn.close()
            else:
                # Second attempt: use real connection
                with original_get_db(db_path, readonly) as conn:
                    yield conn

        with patch("pacemaker.database.get_db_connection", side_effect=mock_get_db):
            result = get_24h_metrics(temp_db)

        # Verify retry occurred
        assert attempt_count[0] > 1, "Should have retried after database locked error"
        # Result should still be valid
        assert "sessions" in result

    def test_concurrent_increments_succeed(self, temp_db):
        """
        Multiple concurrent increment_metric calls should all succeed.

        This integration test verifies that the concurrency handling works
        in a real concurrent scenario with multiple threads.
        """
        errors = []
        threads = []

        # Use a single time.time patch for all threads to avoid conflicts
        with patch("time.time", return_value=1738670850):
            # Also patch time.time in cleanup to avoid real time being used
            with patch(
                "src.pacemaker.langfuse.metrics.time.time", return_value=1738670850
            ):

                def increment_worker(metric_type, count):
                    try:
                        for _ in range(count):
                            increment_metric(metric_type, temp_db)
                            time.sleep(0.001)  # Small delay between increments
                    except Exception as e:
                        errors.append(e)

                # Launch 5 threads, each doing 10 increments
                for i in range(5):
                    t = threading.Thread(target=increment_worker, args=("sessions", 10))
                    threads.append(t)
                    t.start()

                # Wait for all threads to complete
                for t in threads:
                    t.join()

        # Verify no errors occurred
        assert len(errors) == 0, f"Concurrent increments failed with errors: {errors}"

        # Verify final count is correct (5 threads * 10 increments = 50)
        conn = sqlite3.connect(temp_db)
        result = conn.execute(
            "SELECT sessions_count FROM langfuse_metrics WHERE bucket_timestamp = ?",
            (1738670400,),
        ).fetchone()
        conn.close()

        assert result is not None, "Bucket should exist"
        assert result[0] == 50, f"Expected 50 increments, got {result[0]}"
