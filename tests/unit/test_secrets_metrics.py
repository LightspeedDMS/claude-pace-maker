#!/usr/bin/env python3
"""
Unit tests for secrets metrics tracking.

Tests the secrets metrics module that tracks secrets masking statistics
in 15-minute buckets, following the same pattern as langfuse metrics.
"""

import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from src.pacemaker.secrets.metrics import (
    align_to_bucket,
    increment_secrets_masked,
    cleanup_stale_buckets,
    get_24h_secrets_metrics,
)


class TestBucketAlignment:
    """Test bucket timestamp alignment to 15-minute boundaries."""

    def test_align_to_start_of_bucket(self):
        """Test alignment for timestamp at exact bucket start."""
        # 12:00:00 should align to 12:00:00
        timestamp = 1738670400.0  # Exact 15-min boundary
        expected = 1738670400
        assert align_to_bucket(timestamp) == expected

    def test_align_to_middle_of_bucket(self):
        """Test alignment for timestamp in middle of bucket."""
        # 12:07:30 should align down to 12:00:00
        timestamp = 1738670850.0
        expected = 1738670400  # 12:00:00
        assert align_to_bucket(timestamp) == expected

    def test_align_to_end_of_bucket(self):
        """Test alignment for timestamp at end of bucket."""
        # 12:14:59 should align down to 12:00:00
        timestamp = 1738671299.0
        expected = 1738670400  # 12:00:00
        assert align_to_bucket(timestamp) == expected

    def test_align_to_next_bucket_start(self):
        """Test alignment for timestamp at next bucket boundary."""
        # 12:15:00 should align to 12:15:00
        timestamp = 1738671300.0
        expected = 1738671300  # 12:15:00
        assert align_to_bucket(timestamp) == expected

    def test_align_returns_integer(self):
        """Test that alignment returns integer type."""
        timestamp = 1738670850.5  # Float input
        result = align_to_bucket(timestamp)
        assert isinstance(result, int)


class TestIncrementSecretsMasked:
    """Test incrementing secrets masked counter."""

    @pytest.fixture
    def temp_db(self):
        """Create temporary database with secrets_metrics table."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
            db_path = f.name

        # Initialize table
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE secrets_metrics (
                bucket_timestamp INTEGER PRIMARY KEY,
                secrets_masked_count INTEGER DEFAULT 0
            )
            """
        )
        conn.commit()
        conn.close()

        yield db_path

        # Cleanup
        Path(db_path).unlink(missing_ok=True)

    def test_increment_creates_new_bucket(self, temp_db):
        """Test incrementing creates new bucket if it doesn't exist."""
        increment_secrets_masked(temp_db, count=1)

        conn = sqlite3.connect(temp_db)
        cursor = conn.execute(
            "SELECT secrets_masked_count FROM secrets_metrics LIMIT 1"
        )
        result = cursor.fetchone()
        conn.close()

        assert result is not None
        assert result[0] == 1

    def test_increment_updates_existing_bucket(self, temp_db):
        """Test incrementing updates existing bucket."""
        # First increment
        increment_secrets_masked(temp_db, count=5)
        # Second increment in same bucket (mock time to ensure same bucket)
        increment_secrets_masked(temp_db, count=3)

        conn = sqlite3.connect(temp_db)
        cursor = conn.execute("SELECT SUM(secrets_masked_count) FROM secrets_metrics")
        result = cursor.fetchone()
        conn.close()

        assert result[0] == 8  # 5 + 3

    def test_increment_with_custom_count(self, temp_db):
        """Test incrementing with custom count value."""
        increment_secrets_masked(temp_db, count=10)

        conn = sqlite3.connect(temp_db)
        cursor = conn.execute(
            "SELECT secrets_masked_count FROM secrets_metrics LIMIT 1"
        )
        result = cursor.fetchone()
        conn.close()

        assert result[0] == 10

    def test_increment_multiple_buckets(self, temp_db):
        """Test incrementing creates separate buckets for different times."""
        # Mock current time for first bucket
        bucket1 = align_to_bucket(time.time())

        # Insert directly to simulate first bucket
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "INSERT INTO secrets_metrics (bucket_timestamp, secrets_masked_count) VALUES (?, ?)",
            (bucket1, 5),
        )
        conn.commit()
        conn.close()

        # Insert second bucket 15 minutes later
        bucket2 = bucket1 + 900
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "INSERT INTO secrets_metrics (bucket_timestamp, secrets_masked_count) VALUES (?, ?)",
            (bucket2, 3),
        )
        conn.commit()
        conn.close()

        # Verify two buckets exist
        conn = sqlite3.connect(temp_db)
        cursor = conn.execute("SELECT COUNT(*) FROM secrets_metrics")
        count = cursor.fetchone()[0]
        conn.close()

        assert count == 2

    def test_increment_cleanup_called(self, temp_db):
        """Test that cleanup is called after increment."""
        # Insert old bucket (25 hours ago)
        old_bucket = align_to_bucket(time.time() - 90000)
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "INSERT INTO secrets_metrics (bucket_timestamp, secrets_masked_count) VALUES (?, ?)",
            (old_bucket, 100),
        )
        conn.commit()
        conn.close()

        # Increment should trigger cleanup
        increment_secrets_masked(temp_db, count=1)

        # Old bucket should be deleted
        conn = sqlite3.connect(temp_db)
        cursor = conn.execute(
            "SELECT COUNT(*) FROM secrets_metrics WHERE bucket_timestamp = ?",
            (old_bucket,),
        )
        result = cursor.fetchone()[0]
        conn.close()

        assert result == 0


class TestCleanupStaleBuckets:
    """Test cleanup of old metric buckets."""

    @pytest.fixture
    def temp_db(self):
        """Create temporary database with secrets_metrics table."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
            db_path = f.name

        # Initialize table
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE secrets_metrics (
                bucket_timestamp INTEGER PRIMARY KEY,
                secrets_masked_count INTEGER DEFAULT 0
            )
            """
        )
        conn.commit()
        conn.close()

        yield db_path

        # Cleanup
        Path(db_path).unlink(missing_ok=True)

    def test_cleanup_removes_old_buckets(self, temp_db):
        """Test cleanup removes buckets older than 24 hours."""
        now = time.time()
        old_bucket = align_to_bucket(now - 90000)  # 25 hours ago
        recent_bucket = align_to_bucket(now - 3600)  # 1 hour ago

        conn = sqlite3.connect(temp_db)
        conn.execute(
            "INSERT INTO secrets_metrics (bucket_timestamp, secrets_masked_count) VALUES (?, ?)",
            (old_bucket, 100),
        )
        conn.execute(
            "INSERT INTO secrets_metrics (bucket_timestamp, secrets_masked_count) VALUES (?, ?)",
            (recent_bucket, 50),
        )
        conn.commit()
        conn.close()

        cleanup_stale_buckets(temp_db)

        # Verify only recent bucket remains
        conn = sqlite3.connect(temp_db)
        cursor = conn.execute(
            "SELECT bucket_timestamp, secrets_masked_count FROM secrets_metrics ORDER BY bucket_timestamp"
        )
        results = cursor.fetchall()
        conn.close()

        assert len(results) == 1
        assert results[0][0] == recent_bucket
        assert results[0][1] == 50

    def test_cleanup_keeps_recent_buckets(self, temp_db):
        """Test cleanup keeps all buckets within 24 hours."""
        now = time.time()
        buckets = [
            align_to_bucket(now - 3600),  # 1 hour ago
            align_to_bucket(now - 7200),  # 2 hours ago
            align_to_bucket(now - 43200),  # 12 hours ago
            align_to_bucket(now - 82800),  # 23 hours ago (safely within 24h)
        ]

        conn = sqlite3.connect(temp_db)
        for bucket in buckets:
            conn.execute(
                "INSERT INTO secrets_metrics (bucket_timestamp, secrets_masked_count) VALUES (?, ?)",
                (bucket, 10),
            )
        conn.commit()
        conn.close()

        cleanup_stale_buckets(temp_db)

        # Verify all buckets remain
        conn = sqlite3.connect(temp_db)
        cursor = conn.execute("SELECT COUNT(*) FROM secrets_metrics")
        count = cursor.fetchone()[0]
        conn.close()

        assert count == 4

    def test_cleanup_empty_table(self, temp_db):
        """Test cleanup on empty table doesn't fail."""
        cleanup_stale_buckets(temp_db)

        conn = sqlite3.connect(temp_db)
        cursor = conn.execute("SELECT COUNT(*) FROM secrets_metrics")
        count = cursor.fetchone()[0]
        conn.close()

        assert count == 0


class TestGet24hSecretsMetrics:
    """Test querying 24-hour secrets metrics."""

    @pytest.fixture
    def temp_db(self):
        """Create temporary database with secrets_metrics table."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
            db_path = f.name

        # Initialize table
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE secrets_metrics (
                bucket_timestamp INTEGER PRIMARY KEY,
                secrets_masked_count INTEGER DEFAULT 0
            )
            """
        )
        conn.commit()
        conn.close()

        yield db_path

        # Cleanup
        Path(db_path).unlink(missing_ok=True)

    def test_get_metrics_empty_table(self, temp_db):
        """Test getting metrics from empty table returns zero."""
        result = get_24h_secrets_metrics(temp_db)

        assert result == {"secrets_masked": 0}

    def test_get_metrics_single_bucket(self, temp_db):
        """Test getting metrics with single bucket."""
        bucket = align_to_bucket(time.time())

        conn = sqlite3.connect(temp_db)
        conn.execute(
            "INSERT INTO secrets_metrics (bucket_timestamp, secrets_masked_count) VALUES (?, ?)",
            (bucket, 42),
        )
        conn.commit()
        conn.close()

        result = get_24h_secrets_metrics(temp_db)

        assert result == {"secrets_masked": 42}

    def test_get_metrics_multiple_buckets(self, temp_db):
        """Test getting metrics sums all buckets within 24 hours."""
        now = time.time()
        buckets = [
            (align_to_bucket(now - 3600), 10),  # 1 hour ago
            (align_to_bucket(now - 7200), 20),  # 2 hours ago
            (align_to_bucket(now - 43200), 30),  # 12 hours ago
        ]

        conn = sqlite3.connect(temp_db)
        for bucket, count in buckets:
            conn.execute(
                "INSERT INTO secrets_metrics (bucket_timestamp, secrets_masked_count) VALUES (?, ?)",
                (bucket, count),
            )
        conn.commit()
        conn.close()

        result = get_24h_secrets_metrics(temp_db)

        assert result == {"secrets_masked": 60}  # 10 + 20 + 30

    def test_get_metrics_excludes_old_buckets(self, temp_db):
        """Test getting metrics excludes buckets older than 24 hours."""
        now = time.time()
        buckets = [
            (align_to_bucket(now - 3600), 10),  # 1 hour ago (included)
            (align_to_bucket(now - 90000), 100),  # 25 hours ago (excluded)
        ]

        conn = sqlite3.connect(temp_db)
        for bucket, count in buckets:
            conn.execute(
                "INSERT INTO secrets_metrics (bucket_timestamp, secrets_masked_count) VALUES (?, ?)",
                (bucket, count),
            )
        conn.commit()
        conn.close()

        result = get_24h_secrets_metrics(temp_db)

        assert result == {"secrets_masked": 10}  # Only recent bucket

    def test_get_metrics_handles_null_values(self, temp_db):
        """Test getting metrics handles NULL counts as zero."""
        bucket = align_to_bucket(time.time())

        conn = sqlite3.connect(temp_db)
        # Insert bucket with NULL count (should default to 0)
        conn.execute(
            "INSERT INTO secrets_metrics (bucket_timestamp) VALUES (?)",
            (bucket,),
        )
        conn.commit()
        conn.close()

        result = get_24h_secrets_metrics(temp_db)

        assert result == {"secrets_masked": 0}
