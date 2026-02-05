#!/usr/bin/env python3
"""
Tests for blockage telemetry capture and storage.

Story #21: Blockage Telemetry Capture and Storage

Tests organized by acceptance criteria:
- AC6: BLOCKAGE_CATEGORIES constant
- AC1: Database schema extension (blockage_events table)
- AC2: record_blockage() function
- AC3: get_hourly_blockage_stats() function
"""

import json
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

# Add src to path for imports
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ==============================================================================
# AC6: BLOCKAGE_CATEGORIES Constant Tests
# ==============================================================================


class TestBlockageCategoriesConstant:
    """AC6: Blockage categories constant must be defined with all valid categories."""

    def test_blockage_categories_exists(self):
        """BLOCKAGE_CATEGORIES constant should be defined in constants module."""
        from pacemaker import constants

        assert hasattr(constants, "BLOCKAGE_CATEGORIES")
        assert constants.BLOCKAGE_CATEGORIES is not None

    def test_blockage_categories_is_tuple(self):
        """BLOCKAGE_CATEGORIES should be a tuple (immutable)."""
        from pacemaker.constants import BLOCKAGE_CATEGORIES

        assert isinstance(BLOCKAGE_CATEGORIES, tuple)

    def test_blockage_categories_contains_intent_validation(self):
        """BLOCKAGE_CATEGORIES should include 'intent_validation'."""
        from pacemaker.constants import BLOCKAGE_CATEGORIES

        assert "intent_validation" in BLOCKAGE_CATEGORIES

    def test_blockage_categories_contains_intent_validation_tdd(self):
        """BLOCKAGE_CATEGORIES should include 'intent_validation_tdd'."""
        from pacemaker.constants import BLOCKAGE_CATEGORIES

        assert "intent_validation_tdd" in BLOCKAGE_CATEGORIES

    def test_blockage_categories_contains_pacing_tempo(self):
        """BLOCKAGE_CATEGORIES should include 'pacing_tempo'."""
        from pacemaker.constants import BLOCKAGE_CATEGORIES

        assert "pacing_tempo" in BLOCKAGE_CATEGORIES

    def test_blockage_categories_contains_pacing_quota(self):
        """BLOCKAGE_CATEGORIES should include 'pacing_quota'."""
        from pacemaker.constants import BLOCKAGE_CATEGORIES

        assert "pacing_quota" in BLOCKAGE_CATEGORIES

    def test_blockage_categories_contains_other(self):
        """BLOCKAGE_CATEGORIES should include 'other' as catch-all."""
        from pacemaker.constants import BLOCKAGE_CATEGORIES

        assert "other" in BLOCKAGE_CATEGORIES

    def test_blockage_categories_has_exactly_six_categories(self):
        """BLOCKAGE_CATEGORIES should have exactly 6 categories (3 intent validation types + 2 pacing + other)."""
        from pacemaker.constants import BLOCKAGE_CATEGORIES

        # Updated to 6: intent_validation, intent_validation_tdd, intent_validation_cleancode,
        # pacing_tempo, pacing_quota, other
        assert len(BLOCKAGE_CATEGORIES) == 6


# ==============================================================================
# AC1: Database Schema Extension Tests
# ==============================================================================


class TestBlockageEventsSchema:
    """AC1: Database schema must include blockage_events table with proper structure."""

    def setup_method(self):
        """Create temporary database for each test."""
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

    def teardown_method(self):
        """Clean up temporary database."""
        Path(self.db_path).unlink(missing_ok=True)

    def test_blockage_events_table_created_on_init(self):
        """blockage_events table should be created when database is initialized."""
        from pacemaker import database

        database.initialize_database(self.db_path)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='blockage_events'"
        )
        result = cursor.fetchone()
        conn.close()

        assert result is not None
        assert result[0] == "blockage_events"

    def test_blockage_events_has_id_column(self):
        """blockage_events table should have id column as primary key."""
        from pacemaker import database

        database.initialize_database(self.db_path)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(blockage_events)")
        columns = {row[1]: row for row in cursor.fetchall()}
        conn.close()

        assert "id" in columns
        assert columns["id"][2].upper() == "INTEGER"  # type
        assert columns["id"][5] == 1  # pk flag

    def test_blockage_events_has_timestamp_column(self):
        """blockage_events table should have timestamp column (NOT NULL)."""
        from pacemaker import database

        database.initialize_database(self.db_path)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(blockage_events)")
        columns = {row[1]: row for row in cursor.fetchall()}
        conn.close()

        assert "timestamp" in columns
        assert columns["timestamp"][2].upper() == "INTEGER"
        assert columns["timestamp"][3] == 1  # NOT NULL

    def test_blockage_events_has_category_column(self):
        """blockage_events table should have category column (TEXT NOT NULL)."""
        from pacemaker import database

        database.initialize_database(self.db_path)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(blockage_events)")
        columns = {row[1]: row for row in cursor.fetchall()}
        conn.close()

        assert "category" in columns
        assert columns["category"][2].upper() == "TEXT"
        assert columns["category"][3] == 1  # NOT NULL

    def test_blockage_events_has_reason_column(self):
        """blockage_events table should have reason column (TEXT NOT NULL)."""
        from pacemaker import database

        database.initialize_database(self.db_path)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(blockage_events)")
        columns = {row[1]: row for row in cursor.fetchall()}
        conn.close()

        assert "reason" in columns
        assert columns["reason"][2].upper() == "TEXT"
        assert columns["reason"][3] == 1  # NOT NULL

    def test_blockage_events_has_hook_type_column(self):
        """blockage_events table should have hook_type column (TEXT NOT NULL)."""
        from pacemaker import database

        database.initialize_database(self.db_path)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(blockage_events)")
        columns = {row[1]: row for row in cursor.fetchall()}
        conn.close()

        assert "hook_type" in columns
        assert columns["hook_type"][2].upper() == "TEXT"
        assert columns["hook_type"][3] == 1  # NOT NULL

    def test_blockage_events_has_session_id_column(self):
        """blockage_events table should have session_id column (TEXT NOT NULL)."""
        from pacemaker import database

        database.initialize_database(self.db_path)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(blockage_events)")
        columns = {row[1]: row for row in cursor.fetchall()}
        conn.close()

        assert "session_id" in columns
        assert columns["session_id"][2].upper() == "TEXT"
        assert columns["session_id"][3] == 1  # NOT NULL

    def test_blockage_events_has_details_column(self):
        """blockage_events table should have details column (TEXT, nullable)."""
        from pacemaker import database

        database.initialize_database(self.db_path)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(blockage_events)")
        columns = {row[1]: row for row in cursor.fetchall()}
        conn.close()

        assert "details" in columns
        assert columns["details"][2].upper() == "TEXT"
        assert columns["details"][3] == 0  # nullable (NOT NULL = 0)

    def test_blockage_events_has_created_at_column(self):
        """blockage_events table should have created_at column (INTEGER NOT NULL)."""
        from pacemaker import database

        database.initialize_database(self.db_path)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(blockage_events)")
        columns = {row[1]: row for row in cursor.fetchall()}
        conn.close()

        assert "created_at" in columns
        assert columns["created_at"][2].upper() == "INTEGER"
        assert columns["created_at"][3] == 1  # NOT NULL

    def test_blockage_events_has_timestamp_index(self):
        """blockage_events should have idx_blockage_timestamp index (DESC)."""
        from pacemaker import database

        database.initialize_database(self.db_path)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_blockage_timestamp'"
        )
        result = cursor.fetchone()
        conn.close()

        assert result is not None
        assert result[0] == "idx_blockage_timestamp"

    def test_blockage_events_has_category_index(self):
        """blockage_events should have idx_blockage_category index."""
        from pacemaker import database

        database.initialize_database(self.db_path)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_blockage_category'"
        )
        result = cursor.fetchone()
        conn.close()

        assert result is not None
        assert result[0] == "idx_blockage_category"

    def test_schema_creation_is_idempotent(self):
        """Schema creation should be idempotent (safe to run multiple times)."""
        from pacemaker import database

        # Initialize twice
        database.initialize_database(self.db_path)
        database.initialize_database(self.db_path)

        # Should not raise exception and table should still exist
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='blockage_events'"
        )
        result = cursor.fetchone()
        conn.close()

        assert result is not None


# ==============================================================================
# AC2: record_blockage() Function Tests
# ==============================================================================


class TestRecordBlockageFunction:
    """AC2: record_blockage() function must insert rows with proper validation."""

    def setup_method(self):
        """Create temporary database for each test."""
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

        # Initialize database
        from pacemaker import database

        database.initialize_database(self.db_path)

    def teardown_method(self):
        """Clean up temporary database."""
        Path(self.db_path).unlink(missing_ok=True)

    def test_record_blockage_function_exists(self):
        """record_blockage function should exist in database module."""
        from pacemaker import database

        assert hasattr(database, "record_blockage")
        assert callable(database.record_blockage)

    def test_record_blockage_inserts_row(self):
        """record_blockage should insert a row with all required fields."""
        from pacemaker import database

        result = database.record_blockage(
            db_path=self.db_path,
            category="intent_validation",
            reason="Missing INTENT: marker in message",
            hook_type="pre_tool_use",
            session_id="test-session-123",
        )

        assert result is True

        # Verify row was inserted
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM blockage_events")
        rows = cursor.fetchall()
        conn.close()

        assert len(rows) == 1

    def test_record_blockage_sets_timestamp(self):
        """record_blockage should set timestamp to current Unix epoch."""
        from pacemaker import database

        before_time = int(time.time())

        database.record_blockage(
            db_path=self.db_path,
            category="intent_validation",
            reason="Test reason",
            hook_type="pre_tool_use",
            session_id="test-session-123",
        )

        after_time = int(time.time())

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT timestamp FROM blockage_events")
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        timestamp = row[0]
        assert before_time <= timestamp <= after_time

    def test_record_blockage_stores_category(self):
        """record_blockage should store the category field."""
        from pacemaker import database

        database.record_blockage(
            db_path=self.db_path,
            category="pacing_tempo",
            reason="Test reason",
            hook_type="stop",
            session_id="test-session-123",
        )

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT category FROM blockage_events")
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "pacing_tempo"

    def test_record_blockage_stores_reason(self):
        """record_blockage should store the reason field."""
        from pacemaker import database

        database.record_blockage(
            db_path=self.db_path,
            category="intent_validation",
            reason="Missing INTENT: marker",
            hook_type="pre_tool_use",
            session_id="test-session-123",
        )

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT reason FROM blockage_events")
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "Missing INTENT: marker"

    def test_record_blockage_stores_hook_type(self):
        """record_blockage should store the hook_type field."""
        from pacemaker import database

        database.record_blockage(
            db_path=self.db_path,
            category="intent_validation",
            reason="Test reason",
            hook_type="pre_tool_use",
            session_id="test-session-123",
        )

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT hook_type FROM blockage_events")
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "pre_tool_use"

    def test_record_blockage_stores_session_id(self):
        """record_blockage should store the session_id field."""
        from pacemaker import database

        database.record_blockage(
            db_path=self.db_path,
            category="intent_validation",
            reason="Test reason",
            hook_type="pre_tool_use",
            session_id="abc123-def456",
        )

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT session_id FROM blockage_events")
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "abc123-def456"

    def test_record_blockage_stores_details_as_json(self):
        """record_blockage should convert details dict to JSON string."""
        from pacemaker import database

        details = {"tool": "Write", "file": "src/example.py"}

        database.record_blockage(
            db_path=self.db_path,
            category="intent_validation",
            reason="Test reason",
            hook_type="pre_tool_use",
            session_id="test-session-123",
            details=details,
        )

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT details FROM blockage_events")
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        stored_details = json.loads(row[0])
        assert stored_details == details

    def test_record_blockage_allows_null_details(self):
        """record_blockage should allow details to be None/NULL."""
        from pacemaker import database

        database.record_blockage(
            db_path=self.db_path,
            category="intent_validation",
            reason="Test reason",
            hook_type="pre_tool_use",
            session_id="test-session-123",
            details=None,
        )

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT details FROM blockage_events")
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row[0] is None

    def test_record_blockage_validates_category(self):
        """record_blockage should validate category against BLOCKAGE_CATEGORIES."""
        from pacemaker import database

        # Invalid category should be converted to 'other'
        result = database.record_blockage(
            db_path=self.db_path,
            category="invalid_category",
            reason="Test reason",
            hook_type="pre_tool_use",
            session_id="test-session-123",
        )

        # Should still succeed but use 'other' category
        assert result is True

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT category FROM blockage_events")
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "other"

    def test_record_blockage_handles_db_error_gracefully(self):
        """record_blockage should handle database errors without raising."""
        from pacemaker import database

        # Use invalid path to trigger error
        result = database.record_blockage(
            db_path="/nonexistent/path/database.db",
            category="intent_validation",
            reason="Test reason",
            hook_type="pre_tool_use",
            session_id="test-session-123",
        )

        # Should return False but not raise
        assert result is False

    def test_record_blockage_uses_parameterized_queries(self):
        """record_blockage should use parameterized queries (SQL injection safe)."""
        from pacemaker import database

        # Attempt SQL injection via reason field
        malicious_reason = "Test'; DROP TABLE blockage_events; --"

        database.record_blockage(
            db_path=self.db_path,
            category="intent_validation",
            reason=malicious_reason,
            hook_type="pre_tool_use",
            session_id="test-session-123",
        )

        # Table should still exist and have the row
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT reason FROM blockage_events")
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row[0] == malicious_reason  # Stored as literal string


# ==============================================================================
# AC3: get_hourly_blockage_stats() Function Tests
# ==============================================================================


class TestGetHourlyBlockageStats:
    """AC3: get_hourly_blockage_stats() must aggregate counts per category."""

    def setup_method(self):
        """Create temporary database for each test."""
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

        # Initialize database
        from pacemaker import database

        database.initialize_database(self.db_path)

    def teardown_method(self):
        """Clean up temporary database."""
        Path(self.db_path).unlink(missing_ok=True)

    def test_get_hourly_blockage_stats_function_exists(self):
        """get_hourly_blockage_stats function should exist in database module."""
        from pacemaker import database

        assert hasattr(database, "get_hourly_blockage_stats")
        assert callable(database.get_hourly_blockage_stats)

    def test_get_hourly_blockage_stats_returns_dict(self):
        """get_hourly_blockage_stats should return a dictionary."""
        from pacemaker import database

        result = database.get_hourly_blockage_stats(db_path=self.db_path)

        assert isinstance(result, dict)

    def test_get_hourly_blockage_stats_includes_all_categories(self):
        """get_hourly_blockage_stats should include all defined categories."""
        from pacemaker import database
        from pacemaker.constants import BLOCKAGE_CATEGORIES

        result = database.get_hourly_blockage_stats(db_path=self.db_path)

        for category in BLOCKAGE_CATEGORIES:
            assert category in result

    def test_get_hourly_blockage_stats_returns_zeros_for_empty_db(self):
        """get_hourly_blockage_stats should return all zeros for empty database."""
        from pacemaker import database
        from pacemaker.constants import BLOCKAGE_CATEGORIES

        result = database.get_hourly_blockage_stats(db_path=self.db_path)

        for category in BLOCKAGE_CATEGORIES:
            assert result[category] == 0

    def test_get_hourly_blockage_stats_counts_recent_blockages(self):
        """get_hourly_blockage_stats should count blockages from last 60 minutes."""
        from pacemaker import database

        # Insert some blockages (within last hour)
        database.record_blockage(
            db_path=self.db_path,
            category="intent_validation",
            reason="Test 1",
            hook_type="pre_tool_use",
            session_id="test-1",
        )
        database.record_blockage(
            db_path=self.db_path,
            category="intent_validation",
            reason="Test 2",
            hook_type="pre_tool_use",
            session_id="test-2",
        )
        database.record_blockage(
            db_path=self.db_path,
            category="pacing_quota",
            reason="Throttle delay",
            hook_type="post_tool_use",
            session_id="test-3",
        )

        result = database.get_hourly_blockage_stats(db_path=self.db_path)

        assert result["intent_validation"] == 2
        assert result["pacing_quota"] == 1
        assert result["intent_validation_tdd"] == 0
        assert result["pacing_tempo"] == 0
        assert result["other"] == 0

    def test_get_hourly_blockage_stats_excludes_old_blockages(self):
        """get_hourly_blockage_stats should exclude blockages older than 60 minutes."""
        from pacemaker import database

        # Insert a blockage manually with old timestamp (90 minutes ago)
        old_timestamp = int(time.time()) - 5400  # 90 minutes ago

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO blockage_events (timestamp, category, reason, hook_type, session_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                old_timestamp,
                "pacing_tempo",
                "Old blockage",
                "stop",
                "old-session",
                old_timestamp,
            ),
        )
        conn.commit()
        conn.close()

        # Insert a recent blockage
        database.record_blockage(
            db_path=self.db_path,
            category="intent_validation",
            reason="Recent blockage",
            hook_type="pre_tool_use",
            session_id="recent-session",
        )

        result = database.get_hourly_blockage_stats(db_path=self.db_path)

        # Old pacing_tempo blockage should NOT be counted
        assert result["pacing_tempo"] == 0
        # Recent intent_validation should be counted
        assert result["intent_validation"] == 1

    def test_get_hourly_blockage_stats_zero_fills_missing_categories(self):
        """get_hourly_blockage_stats should zero-fill categories with no blockages."""
        from pacemaker import database
        from pacemaker.constants import BLOCKAGE_CATEGORIES

        # Insert blockages for only some categories
        database.record_blockage(
            db_path=self.db_path,
            category="intent_validation",
            reason="Test",
            hook_type="pre_tool_use",
            session_id="test-1",
        )

        result = database.get_hourly_blockage_stats(db_path=self.db_path)

        # All categories should be present
        assert len(result) == len(BLOCKAGE_CATEGORIES)

        # Categories with no blockages should be 0
        assert result["intent_validation_tdd"] == 0
        assert result["pacing_tempo"] == 0
        assert result["pacing_quota"] == 0
        assert result["other"] == 0

    def test_get_hourly_blockage_stats_handles_db_error_gracefully(self):
        """get_hourly_blockage_stats should handle database errors gracefully."""
        from pacemaker import database
        from pacemaker.constants import BLOCKAGE_CATEGORIES

        # Use invalid path to trigger error
        result = database.get_hourly_blockage_stats(db_path="/nonexistent/path/db.db")

        # Should return all zeros instead of raising
        assert isinstance(result, dict)
        for category in BLOCKAGE_CATEGORIES:
            assert result[category] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
