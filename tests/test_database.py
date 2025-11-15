#!/usr/bin/env python3
"""
Unit tests for database operations.

Tests the SQLite database layer for:
- Schema initialization
- Usage snapshot insertion
- Querying historical data
- Session tracking
"""

import unittest
import tempfile
import os
from datetime import datetime, timedelta


class TestDatabase(unittest.TestCase):
    """Test database operations."""

    def setUp(self):
        """Create temporary database for each test."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name

    def tearDown(self):
        """Clean up temporary database."""
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_initialize_database_creates_schema(self):
        """Database initialization should create required tables."""
        from pacemaker.database import initialize_database

        initialize_database(self.db_path)

        # Verify database file was created
        self.assertTrue(os.path.exists(self.db_path))

        # Verify table exists (by attempting to query it)
        import sqlite3

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='usage_snapshots';"
        )
        result = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(result)
        self.assertEqual(result[0], "usage_snapshots")

    def test_insert_usage_snapshot(self):
        """Should insert usage snapshot with all fields."""
        from pacemaker.database import initialize_database, insert_usage_snapshot

        initialize_database(self.db_path)

        timestamp = datetime.utcnow()
        result = insert_usage_snapshot(
            db_path=self.db_path,
            timestamp=timestamp,
            five_hour_util=45.5,
            five_hour_resets_at=timestamp + timedelta(hours=3),
            seven_day_util=62.3,
            seven_day_resets_at=timestamp + timedelta(days=5),
            session_id="test-session-123",
        )

        self.assertTrue(result)

        # Verify data was inserted
        import sqlite3

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM usage_snapshots WHERE session_id='test-session-123';"
        )
        count = cursor.fetchone()[0]
        conn.close()

        self.assertEqual(count, 1)

    def test_insert_usage_snapshot_with_nulls(self):
        """Should handle NULL reset times for inactive windows."""
        from pacemaker.database import initialize_database, insert_usage_snapshot

        initialize_database(self.db_path)

        timestamp = datetime.utcnow()
        result = insert_usage_snapshot(
            db_path=self.db_path,
            timestamp=timestamp,
            five_hour_util=0.0,
            five_hour_resets_at=None,  # NULL - inactive window
            seven_day_util=50.0,
            seven_day_resets_at=timestamp + timedelta(days=3),
            session_id="test-session-456",
        )

        self.assertTrue(result)

        # Verify NULL was stored correctly
        import sqlite3

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT five_hour_resets_at FROM usage_snapshots WHERE session_id='test-session-456';"
        )
        result = cursor.fetchone()[0]
        conn.close()

        self.assertIsNone(result)

    def test_query_recent_snapshots(self):
        """Should query snapshots within time window."""
        from pacemaker.database import (
            initialize_database,
            insert_usage_snapshot,
            query_recent_snapshots,
        )

        initialize_database(self.db_path)

        base_time = datetime.utcnow()

        # Insert snapshots at different times
        for i in range(5):
            timestamp = base_time - timedelta(minutes=i * 30)
            insert_usage_snapshot(
                db_path=self.db_path,
                timestamp=timestamp,
                five_hour_util=float(i * 10),
                five_hour_resets_at=timestamp + timedelta(hours=3),
                seven_day_util=float(i * 5),
                seven_day_resets_at=timestamp + timedelta(days=3),
                session_id=f"session-{i}",
            )

        # Query last 90 minutes (should get 3 snapshots: 0, 30, 60 mins ago)
        snapshots = query_recent_snapshots(self.db_path, minutes=90)

        self.assertEqual(len(snapshots), 3)
        self.assertEqual(snapshots[0]["session_id"], "session-0")  # Most recent

    def test_database_handles_concurrent_inserts(self):
        """Should handle multiple rapid inserts without corruption."""
        from pacemaker.database import initialize_database, insert_usage_snapshot

        initialize_database(self.db_path)

        timestamp = datetime.utcnow()

        # Rapidly insert 10 snapshots
        for i in range(10):
            result = insert_usage_snapshot(
                db_path=self.db_path,
                timestamp=timestamp - timedelta(seconds=i),
                five_hour_util=float(i),
                five_hour_resets_at=timestamp,
                seven_day_util=float(i),
                seven_day_resets_at=timestamp,
                session_id=f"rapid-{i}",
            )
            self.assertTrue(result)

        # Verify all were inserted
        import sqlite3

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM usage_snapshots WHERE session_id LIKE 'rapid-%';"
        )
        count = cursor.fetchone()[0]
        conn.close()

        self.assertEqual(count, 10)


if __name__ == "__main__":
    unittest.main()
