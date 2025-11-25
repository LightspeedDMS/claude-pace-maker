#!/usr/bin/env python3
"""
Tests for pacing engine orchestration.

Critical test: Pacing engine must use cached decisions between API polls,
not just return "no throttle" when polling is skipped.
"""

import pytest
import tempfile
from datetime import datetime, timedelta
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

    def test_should_return_cached_decision_when_not_polling(self):
        """
        CRITICAL BUG FIX TEST:
        When API polling is skipped (too soon since last poll), the engine
        should return the LAST pacing decision from database, not "no throttle".

        This ensures throttling continues between polls.
        """
        # Setup: First poll happens and stores a "throttle" decision
        now = datetime.utcnow()
        last_poll_time = now  # Just polled

        # Simulate a throttle decision being stored in database
        # We'll need to add a database function to store pacing decisions
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
            last_poll_time=last_poll_time,
            poll_interval=60,
        )

        # Assert: Should use cached decision (throttle=True, delay=30)
        # NOT the broken behavior of returning no throttle
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
        # Setup: No cached decision exists
        now = datetime.utcnow()
        last_poll_time = now  # Just polled (but no decision stored)

        # Act: Try to check pacing immediately
        result = pacing_engine.run_pacing_check(
            db_path=self.db_path,
            session_id=self.session_id,
            last_poll_time=last_poll_time,
            poll_interval=60,
        )

        # Assert: Should gracefully return no throttle
        assert result["polled"] is False
        assert result["decision"]["should_throttle"] is False
        assert result["decision"]["delay_seconds"] == 0

    def test_should_update_cached_decision_after_polling(self):
        """
        After polling API and calculating a new decision, the engine
        should store that decision in the database for future use.
        """
        # This test will verify that pacing decisions are persisted
        # We'll need to mock the API fetch to test this
        # For now, this is a placeholder showing the required behavior

        # Setup: Time to poll (no last poll time)
        # Act: Run pacing check (will poll)
        # Assert: Decision should be stored in database
        pass  # Will implement after adding store function

    def test_cached_decision_lifecycle(self):
        """
        Integration test: Verify complete lifecycle of cached decisions.

        1. First poll: Calculate and store decision (throttle=True)
        2. Between polls: Use cached decision
        3. Second poll: Calculate new decision (throttle=False)
        4. Between polls: Use new cached decision
        """
        # This will be an integration test once basic functionality works
        pass


class TestPacingEnginePolling:
    """Test API polling logic."""

    def test_should_poll_on_first_run(self):
        """Should poll API when last_poll_time is None."""
        assert pacing_engine.should_poll_api(None, interval=60) is True

    def test_should_not_poll_too_soon(self):
        """Should not poll API if interval hasn't elapsed."""
        now = datetime.utcnow()
        last_poll = now - timedelta(seconds=30)  # Only 30 seconds ago
        assert pacing_engine.should_poll_api(last_poll, interval=60) is False

    def test_should_poll_after_interval(self):
        """Should poll API after interval has elapsed."""
        now = datetime.utcnow()
        last_poll = now - timedelta(seconds=61)  # 61 seconds ago
        assert pacing_engine.should_poll_api(last_poll, interval=60) is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
