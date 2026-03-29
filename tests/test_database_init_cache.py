#!/usr/bin/env python3
"""
Tests for initialize_database() initialization cache (Bug 1 fix).

Bug: initialize_database() calls cursor.executescript(SCHEMA) every time it
is called. executescript() requires an exclusive lock. When UsageModel is
instantiated multiple times for the same db_path (common in tests via
conftest autouse fixture), WAL mode causes contention and tests hang.

Fix: Add an _initialized_dbs set to cache which db_paths have already been
initialized. Add reset_initialized_dbs() for test cleanup. The second call
to initialize_database() on the same path returns True immediately without
acquiring the exclusive lock.
"""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pacemaker.database import initialize_database, reset_initialized_dbs


class TestInitializeDatabaseCache:
    """Tests for the _initialized_dbs cache in initialize_database()."""

    def test_reset_initialized_dbs_is_importable(self):
        """reset_initialized_dbs must be importable from pacemaker.database."""
        # If this import fails, the function doesn't exist yet (red phase)
        from pacemaker.database import reset_initialized_dbs

        assert callable(reset_initialized_dbs)

    def test_initialized_dbs_set_is_importable(self):
        """_initialized_dbs module-level set must be importable."""
        from pacemaker import database

        assert hasattr(database, "_initialized_dbs")
        assert isinstance(database._initialized_dbs, set)

    def test_first_call_initializes_database(self, tmp_path):
        """First call to initialize_database must create the schema and return True."""
        reset_initialized_dbs()
        db_path = str(tmp_path / "test.db")

        result = initialize_database(db_path)

        assert result is True
        # Verify schema was created
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        assert "usage_snapshots" in tables
        assert "pacing_decisions" in tables

    def test_second_call_returns_true_without_executescript(self, tmp_path):
        """
        Second call to initialize_database on the same path must return True
        immediately (cache hit) without calling executescript again.
        """
        reset_initialized_dbs()
        db_path = str(tmp_path / "test.db")

        # First call — initializes
        result1 = initialize_database(db_path)
        assert result1 is True

        # Second call — should be a cache hit, no executescript
        with patch("pacemaker.database.get_db_connection") as mock_conn:
            result2 = initialize_database(db_path)

        assert result2 is True
        # get_db_connection must NOT have been called on the second call
        mock_conn.assert_not_called()

    def test_db_path_added_to_cache_after_init(self, tmp_path):
        """After initialize_database succeeds, db_path must be in _initialized_dbs."""
        from pacemaker import database

        reset_initialized_dbs()
        db_path = str(tmp_path / "cache_test.db")

        assert db_path not in database._initialized_dbs

        initialize_database(db_path)

        assert db_path in database._initialized_dbs

    def test_reset_clears_cache(self, tmp_path):
        """reset_initialized_dbs() must clear all entries from _initialized_dbs."""
        from pacemaker import database

        reset_initialized_dbs()
        db_path = str(tmp_path / "reset_test.db")

        # Populate cache
        initialize_database(db_path)
        assert db_path in database._initialized_dbs

        # Reset must clear it
        reset_initialized_dbs()
        assert len(database._initialized_dbs) == 0

    def test_after_reset_reinitializes_on_next_call(self, tmp_path):
        """After reset, the next call to initialize_database must re-run schema creation."""
        reset_initialized_dbs()
        db_path = str(tmp_path / "reinit_test.db")

        # First init
        initialize_database(db_path)

        # Reset cache
        reset_initialized_dbs()

        # Second init after reset — must succeed (schema already exists, CREATE IF NOT EXISTS is safe)
        result = initialize_database(db_path)
        assert result is True

        # Schema still intact
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        assert "usage_snapshots" in tables

    def test_different_paths_are_cached_independently(self, tmp_path):
        """Two different db_paths must each get their own cache entry."""
        from pacemaker import database

        reset_initialized_dbs()
        db_path_a = str(tmp_path / "a.db")
        db_path_b = str(tmp_path / "b.db")

        initialize_database(db_path_a)
        assert db_path_a in database._initialized_dbs
        assert db_path_b not in database._initialized_dbs

        initialize_database(db_path_b)
        assert db_path_a in database._initialized_dbs
        assert db_path_b in database._initialized_dbs

    def test_failed_init_does_not_add_to_cache(self, tmp_path):
        """
        If initialize_database fails (e.g., bad path), the db_path must NOT
        be added to _initialized_dbs so a retry can succeed.
        """
        from pacemaker import database

        reset_initialized_dbs()
        # Use a path in a directory that can't be created (file as parent)
        bad_path = str(tmp_path / "not_a_dir.txt" / "sub" / "test.db")
        # Create a file at the would-be directory path to force failure
        (tmp_path / "not_a_dir.txt").write_text("I am a file, not a dir")

        result = initialize_database(bad_path)

        assert result is False
        assert bad_path not in database._initialized_dbs

    def test_schema_correct_after_initialization(self, tmp_path):
        """Verify the schema has the expected tables and indexes after init."""
        reset_initialized_dbs()
        db_path = str(tmp_path / "schema_test.db")

        initialize_database(db_path)

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Check tables
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}

        # Check indexes
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
        )
        indexes = {row[0] for row in cursor.fetchall()}

        conn.close()

        assert "usage_snapshots" in tables
        assert "pacing_decisions" in tables
        assert "idx_timestamp" in indexes
        assert "idx_session" in indexes


class TestConfTestIntegration:
    """Verify conftest correctly resets the cache between tests."""

    def test_cache_reset_by_conftest(self):
        """
        The conftest _guard_production_db fixture calls reset_initialized_dbs()
        before each test, then calls initialize_database(fake_db_path) to guard
        the hook's DEFAULT_DB_PATH. This means the cache will contain exactly
        one entry (the conftest-initialized fake DB path).
        """
        from pacemaker import database

        # After conftest fixture runs, cache contains exactly the conftest fake DB
        assert len(database._initialized_dbs) == 1
