#!/usr/bin/env python3
"""
Tests for Langfuse metrics tracking module.

Tests cover:
- Bucket alignment to 15-minute boundaries
- Counter increment (new bucket creation, existing bucket update)
- Cleanup of stale buckets (older than 24 hours)
- 24-hour metrics query (correct sums, empty state)
- Langfuse enabled check (config flag + API keys)

Story #34: Langfuse Integration Status and Metrics Display
"""

import os
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.pacemaker.langfuse.metrics import (
    align_to_bucket,
    increment_metric,
    cleanup_stale_buckets,
    get_24h_metrics,
    is_langfuse_enabled,
)


class TestBucketAlignment:
    """Test align_to_bucket() function - 15-minute boundary alignment."""

    def test_align_to_bucket_exact_boundary(self):
        """Timestamp exactly on 15-min boundary returns unchanged."""
        # 2025-02-04 12:00:00 UTC = 1738670400 (divisible by 900)
        timestamp = 1738670400
        assert align_to_bucket(timestamp) == 1738670400

    def test_align_to_bucket_rounds_down(self):
        """Timestamp between boundaries rounds down to previous boundary."""
        # 2025-02-04 12:07:30 = 1738670850 (450 seconds after 12:00:00)
        timestamp = 1738670850
        expected = 1738670400  # Should round down to 12:00:00
        assert align_to_bucket(timestamp) == expected

    def test_align_to_bucket_just_before_boundary(self):
        """Timestamp 1 second before next boundary rounds down."""
        # 2025-02-04 12:14:59 = 1738671299 (899 seconds after 12:00:00)
        timestamp = 1738671299
        expected = 1738670400  # Should still round to 12:00:00
        assert align_to_bucket(timestamp) == expected

    def test_align_to_bucket_next_boundary(self):
        """Timestamp at next 15-min boundary aligns correctly."""
        # 2025-02-04 12:15:00 = 1738671300
        timestamp = 1738671300
        assert align_to_bucket(timestamp) == 1738671300


class TestIncrementMetric:
    """Test increment_metric() function - counter increment with upsert."""

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

    def test_increment_creates_new_bucket(self, temp_db):
        """First increment creates new bucket with count=1."""
        with patch("time.time", return_value=1738670850):  # 12:07:30
            increment_metric("sessions", temp_db)

        conn = sqlite3.connect(temp_db)
        result = conn.execute(
            "SELECT bucket_timestamp, sessions_count FROM langfuse_metrics"
        ).fetchone()
        conn.close()

        assert result[0] == 1738670400  # Aligned to 12:00:00
        assert result[1] == 1  # Count = 1

    def test_increment_updates_existing_bucket(self, temp_db):
        """Second increment to same bucket increases count."""
        bucket = 1738670400

        # First increment
        with patch("time.time", return_value=1738670850):  # 12:07:30
            increment_metric("traces", temp_db)

        # Second increment (same bucket)
        with patch("time.time", return_value=1738670900):  # 12:08:20
            increment_metric("traces", temp_db)

        conn = sqlite3.connect(temp_db)
        result = conn.execute(
            "SELECT traces_count FROM langfuse_metrics WHERE bucket_timestamp = ?",
            (bucket,),
        ).fetchone()
        conn.close()

        assert result[0] == 2

    def test_increment_different_metrics_same_bucket(self, temp_db):
        """Different metrics in same bucket update independently."""
        with patch("time.time", return_value=1738670850):  # 12:07:30
            increment_metric("sessions", temp_db)
            increment_metric("traces", temp_db)
            increment_metric("traces", temp_db)
            increment_metric("spans", temp_db)

        conn = sqlite3.connect(temp_db)
        result = conn.execute(
            """SELECT sessions_count, traces_count, spans_count
               FROM langfuse_metrics WHERE bucket_timestamp = ?""",
            (1738670400,),
        ).fetchone()
        conn.close()

        assert result == (1, 2, 1)  # sessions=1, traces=2, spans=1

    def test_increment_creates_multiple_buckets(self, temp_db):
        """Increments in different time buckets create separate rows."""
        # Bucket 1: 12:00:00
        with patch("time.time", return_value=1738670850):
            increment_metric("sessions", temp_db)

        # Bucket 2: 12:15:00
        with patch("time.time", return_value=1738671300):
            increment_metric("sessions", temp_db)

        conn = sqlite3.connect(temp_db)
        count = conn.execute("SELECT COUNT(*) FROM langfuse_metrics").fetchone()[0]
        conn.close()

        assert count == 2

    def test_increment_raises_on_invalid_metric_type(self, temp_db):
        """Invalid metric_type should raise ValueError."""
        with pytest.raises(ValueError) as excinfo:
            increment_metric("invalid_metric", temp_db)
        assert "Invalid metric_type" in str(excinfo.value)


class TestCleanupStaleBuckets:
    """Test cleanup_stale_buckets() function - delete buckets older than 24h."""

    @pytest.fixture
    def db_with_mixed_data(self):
        """Database with recent and stale buckets."""
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

        now = int(time.time())
        buckets = [
            (now - 90000, 10, 20, 30),  # 25 hours ago (STALE)
            (now - 87000, 5, 10, 15),  # ~24.2 hours ago (STALE)
            (now - 85000, 3, 6, 9),  # ~23.6 hours ago (RECENT)
            (now - 3600, 1, 2, 3),  # 1 hour ago (RECENT)
            (now - 900, 2, 4, 6),  # 15 min ago (RECENT)
        ]

        conn.executemany(
            """INSERT INTO langfuse_metrics
               (bucket_timestamp, sessions_count, traces_count, spans_count)
               VALUES (?, ?, ?, ?)""",
            buckets,
        )
        conn.commit()
        conn.close()

        yield path

        Path(path).unlink(missing_ok=True)

    def test_cleanup_removes_stale_buckets(self, db_with_mixed_data):
        """Buckets older than 24 hours are deleted."""
        cleanup_stale_buckets(db_with_mixed_data)

        conn = sqlite3.connect(db_with_mixed_data)
        remaining = conn.execute(
            "SELECT bucket_timestamp FROM langfuse_metrics ORDER BY bucket_timestamp"
        ).fetchall()
        conn.close()

        assert len(remaining) == 3  # Only 3 recent buckets remain

    def test_cleanup_keeps_recent_buckets(self, db_with_mixed_data):
        """Buckets within 24 hours are preserved."""
        now = int(time.time())
        cleanup_stale_buckets(db_with_mixed_data)

        conn = sqlite3.connect(db_with_mixed_data)
        remaining = conn.execute(
            "SELECT bucket_timestamp FROM langfuse_metrics ORDER BY bucket_timestamp"
        ).fetchall()
        conn.close()

        # All remaining buckets should be within 24 hours
        cutoff = now - 86400
        for (timestamp,) in remaining:
            assert timestamp >= cutoff

    def test_cleanup_empty_database(self):
        """Cleanup on empty database doesn't error."""
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

        # Should not raise exception
        cleanup_stale_buckets(path)

        Path(path).unlink(missing_ok=True)


class TestGet24hMetrics:
    """Test get_24h_metrics() function - query and sum metrics."""

    @pytest.fixture
    def db_with_metrics(self):
        """Database with various metrics buckets."""
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

        now = int(time.time())
        buckets = [
            (now - 90000, 100, 200, 300),  # 25 hours ago (SHOULD BE EXCLUDED)
            (now - 3600, 10, 20, 30),  # 1 hour ago
            (now - 7200, 5, 10, 15),  # 2 hours ago
            (now - 43200, 3, 6, 9),  # 12 hours ago
            (now - 900, 2, 4, 6),  # 15 min ago
        ]

        conn.executemany(
            """INSERT INTO langfuse_metrics
               (bucket_timestamp, sessions_count, traces_count, spans_count)
               VALUES (?, ?, ?, ?)""",
            buckets,
        )
        conn.commit()
        conn.close()

        yield path

        Path(path).unlink(missing_ok=True)

    def test_get_24h_metrics_correct_sums(self, db_with_metrics):
        """Returns correct sums for last 24 hours."""
        result = get_24h_metrics(db_with_metrics)

        # Should sum only the 4 recent buckets (exclude 25h old bucket)
        # sessions: 10 + 5 + 3 + 2 = 20
        # traces: 20 + 10 + 6 + 4 = 40
        # spans: 30 + 15 + 9 + 6 = 60
        # total: 20 + 40 + 60 = 120
        assert result["sessions"] == 20
        assert result["traces"] == 40
        assert result["spans"] == 60
        assert result["total"] == 120

    def test_get_24h_metrics_empty_database(self):
        """Empty database returns zeros."""
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

        result = get_24h_metrics(path)

        assert result["sessions"] == 0
        assert result["traces"] == 0
        assert result["spans"] == 0
        assert result["total"] == 0

        Path(path).unlink(missing_ok=True)

    def test_get_24h_metrics_all_stale_data(self):
        """Database with only stale data returns zeros."""
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

        now = int(time.time())
        # All buckets older than 24 hours
        stale_buckets = [
            (now - 90000, 10, 20, 30),
            (now - 100000, 5, 10, 15),
        ]

        conn.executemany(
            """INSERT INTO langfuse_metrics
               (bucket_timestamp, sessions_count, traces_count, spans_count)
               VALUES (?, ?, ?, ?)""",
            stale_buckets,
        )
        conn.commit()
        conn.close()

        result = get_24h_metrics(path)

        assert result["sessions"] == 0
        assert result["traces"] == 0
        assert result["spans"] == 0
        assert result["total"] == 0

        Path(path).unlink(missing_ok=True)


class TestIsLangfuseEnabled:
    """Test is_langfuse_enabled() function - check config and keys."""

    def test_enabled_with_keys(self):
        """Returns True when enabled flag is True and keys are present."""
        config = {
            "langfuse_enabled": True,
            "langfuse_public_key": "pk-lf-test",
            "langfuse_secret_key": "sk-lf-test",
        }

        result = is_langfuse_enabled(config)
        assert result is True

    def test_disabled_flag(self):
        """Returns False when enabled flag is False (even with keys)."""
        config = {
            "langfuse_enabled": False,
            "langfuse_public_key": "pk-lf-test",
            "langfuse_secret_key": "sk-lf-test",
        }

        result = is_langfuse_enabled(config)
        assert result is False

    def test_missing_public_key(self):
        """Returns False when public key is missing."""
        config = {
            "langfuse_enabled": True,
            "langfuse_secret_key": "sk-lf-test",
        }

        result = is_langfuse_enabled(config)
        assert result is False

    def test_missing_secret_key(self):
        """Returns False when secret key is missing."""
        config = {
            "langfuse_enabled": True,
            "langfuse_public_key": "pk-lf-test",
        }

        result = is_langfuse_enabled(config)
        assert result is False

    def test_empty_public_key(self):
        """Returns False when public key is empty string."""
        config = {
            "langfuse_enabled": True,
            "langfuse_public_key": "",
            "langfuse_secret_key": "sk-lf-test",
        }

        result = is_langfuse_enabled(config)
        assert result is False

    def test_empty_secret_key(self):
        """Returns False when secret key is empty string."""
        config = {
            "langfuse_enabled": True,
            "langfuse_public_key": "pk-lf-test",
            "langfuse_secret_key": "",
        }

        result = is_langfuse_enabled(config)
        assert result is False

    def test_missing_enabled_flag_defaults_false(self):
        """Returns False when langfuse_enabled flag is missing."""
        config = {
            "langfuse_public_key": "pk-lf-test",
            "langfuse_secret_key": "sk-lf-test",
        }

        result = is_langfuse_enabled(config)
        assert result is False

    def test_none_keys(self):
        """Returns False when keys are None."""
        config = {
            "langfuse_enabled": True,
            "langfuse_public_key": None,
            "langfuse_secret_key": None,
        }

        result = is_langfuse_enabled(config)
        assert result is False
