#!/usr/bin/env python3
"""
Tests for langfuse_metrics table in database schema.

Verifies that the database initialization creates the langfuse_metrics
table with correct structure for metrics tracking.

Story #34: Langfuse Integration Status and Metrics Display
"""

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.pacemaker.database import initialize_database


class TestLangfuseMetricsSchema:
    """Test langfuse_metrics table schema."""

    @pytest.fixture
    def temp_db(self):
        """Create temporary database path."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        yield path
        Path(path).unlink(missing_ok=True)

    def test_initialize_database_creates_langfuse_metrics_table(self, temp_db):
        """Database initialization creates langfuse_metrics table."""
        success = initialize_database(temp_db)
        assert success is True

        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()

        # Check if table exists
        cursor.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='langfuse_metrics'
            """
        )
        result = cursor.fetchone()
        conn.close()

        assert result is not None
        assert result[0] == "langfuse_metrics"

    def test_langfuse_metrics_table_has_correct_columns(self, temp_db):
        """langfuse_metrics table has all required columns."""
        initialize_database(temp_db)

        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()

        # Get table schema
        cursor.execute("PRAGMA table_info(langfuse_metrics)")
        columns = cursor.fetchall()
        conn.close()

        # columns format: (cid, name, type, notnull, dflt_value, pk)
        column_names = [col[1] for col in columns]
        column_types = {col[1]: col[2] for col in columns}

        # Verify all required columns exist
        assert "bucket_timestamp" in column_names
        assert "sessions_count" in column_names
        assert "traces_count" in column_names
        assert "spans_count" in column_names

        # Verify column types
        assert column_types["bucket_timestamp"] == "INTEGER"
        assert column_types["sessions_count"] == "INTEGER"
        assert column_types["traces_count"] == "INTEGER"
        assert column_types["spans_count"] == "INTEGER"

    def test_langfuse_metrics_primary_key_constraint(self, temp_db):
        """bucket_timestamp is the primary key."""
        initialize_database(temp_db)

        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()

        # Get table schema
        cursor.execute("PRAGMA table_info(langfuse_metrics)")
        columns = cursor.fetchall()
        conn.close()

        # Find primary key column (pk field = 1)
        primary_keys = [col[1] for col in columns if col[5] == 1]

        assert len(primary_keys) == 1
        assert primary_keys[0] == "bucket_timestamp"
