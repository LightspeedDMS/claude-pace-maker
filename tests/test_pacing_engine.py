#!/usr/bin/env python3
"""
Tests for pacing engine orchestration.

Critical test: Pacing engine must use cached decisions between API polls,
not just return "no throttle" when polling is skipped.
"""

import pytest
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pacemaker import pacing_engine, database


class TestCachedPacingDecisions:
    """Test that pacing engine uses cached decisions between polls."""

    def setup_method(self):
        """Create temporary database for each test."""
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.temp_db.name
        database.initialize_database(self.db_path)
        self.session_id = "test-session-123"

    def teardown_method(self):
        """Clean up temporary database."""
        Path(self.db_path).unlink(missing_ok=True)

    def _set_recent_poll(self):
        """Set global_poll_state to indicate a recent poll (skip next poll)."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT OR REPLACE INTO global_poll_state "
            "(id, last_poll_time, last_poll_session) VALUES (1, ?, ?)",
            (time.time(), self.session_id),
        )
        conn.commit()
        conn.close()

    def test_should_return_cached_decision_when_not_polling(self):
        """
        CRITICAL BUG FIX TEST:
        When API polling is skipped (too soon since last poll), the engine
        should return the LAST pacing decision from database, not "no throttle".

        This ensures throttling continues between polls.
        """
        # Setup: Simulate a recent poll and a stored throttle decision
        self._set_recent_poll()
        now = datetime.utcnow()
        database.insert_pacing_decision(
            db_path=self.db_path,
            timestamp=now,
            should_throttle=True,
            delay_seconds=30,
            session_id=self.session_id,
        )

        # Act: Try to check pacing immediately (should skip poll)
        result = pacing_engine.run_pacing_check(
            db_path=self.db_path,
            session_id=self.session_id,
            poll_interval=300,
        )

        # Assert: Should use cached decision (throttle=True, delay=30)
        assert result["polled"] is False, "Should not have polled API"
        assert (
            result["decision"]["should_throttle"] is True
        ), "Should use cached throttle decision"
        assert (
            result["decision"]["delay_seconds"] == 30
        ), "Should use cached delay value"

    def test_should_return_no_throttle_when_no_cached_decision_exists(self):
        """
        When no cached decision exists (first run, or after cache expires),
        and polling is skipped, should return "no throttle" gracefully.
        """
        # Setup: Recent poll but no cached decision
        self._set_recent_poll()

        # Act: Try to check pacing immediately
        result = pacing_engine.run_pacing_check(
            db_path=self.db_path,
            session_id=self.session_id,
            poll_interval=300,
        )

        # Assert: Should gracefully return no throttle
        assert result["polled"] is False
        assert result["decision"]["should_throttle"] is False
        assert result["decision"]["delay_seconds"] == 0


class TestGlobalPollCoordination:
    """Test that run_pacing_check uses global poll coordination."""

    def setup_method(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.temp_db.name
        database.initialize_database(self.db_path)

    def teardown_method(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_run_pacing_check_has_no_last_poll_time_param(self):
        """run_pacing_check should not accept last_poll_time parameter."""
        import inspect

        sig = inspect.signature(pacing_engine.run_pacing_check)
        assert "last_poll_time" not in sig.parameters

    def test_should_poll_api_removed(self):
        """should_poll_api function should no longer exist."""
        assert not hasattr(pacing_engine, "should_poll_api")

    def test_poll_interval_default_is_300(self):
        """Default poll_interval should be 300 seconds."""
        import inspect

        sig = inspect.signature(pacing_engine.run_pacing_check)
        assert sig.parameters["poll_interval"].default == 300


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
