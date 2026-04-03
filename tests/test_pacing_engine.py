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
from datetime import datetime, timezone
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
        now = datetime.now(timezone.utc)
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

    # Constant: poll_interval that guarantees the cached code path is taken
    CACHED_PATH_POLL_INTERVAL = 300

    def _insert_cached_throttle(self, delay_seconds: int):
        """Set up: simulate recent poll and store a throttle decision in the DB."""
        self._set_recent_poll()
        database.insert_pacing_decision(
            db_path=self.db_path,
            timestamp=datetime.now(timezone.utc),
            should_throttle=True,
            delay_seconds=delay_seconds,
            session_id=self.session_id,
        )

    def _run_cached_check(
        self, weekly_limit_enabled: bool, five_hour_limit_enabled: bool
    ):
        """Run pacing check on the cached code path with the given limit flags."""
        return pacing_engine.run_pacing_check(
            db_path=self.db_path,
            session_id=self.session_id,
            poll_interval=self.CACHED_PATH_POLL_INTERVAL,
            weekly_limit_enabled=weekly_limit_enabled,
            five_hour_limit_enabled=five_hour_limit_enabled,
        )

    def test_both_limits_disabled_ignores_cached_throttle(self):
        """
        BUG FIX TEST:
        When both weekly_limit_enabled and five_hour_limit_enabled are False,
        a cached throttle decision must be overridden to should_throttle=False.

        Disabling limits must take effect immediately without waiting for next poll.
        result["cached"]=True means the cached code path was executed (not that the
        cached throttle value was applied unchanged).
        """
        self._insert_cached_throttle(delay_seconds=60)

        result = self._run_cached_check(
            weekly_limit_enabled=False, five_hour_limit_enabled=False
        )

        assert result["polled"] is False
        assert result["cached"] is True
        assert (
            result["decision"]["should_throttle"] is False
        ), "Both limits disabled: cached throttle must be overridden to False"
        assert (
            result["decision"]["delay_seconds"] == 0
        ), "Both limits disabled: delay must be overridden to 0"

    def test_only_weekly_limit_disabled_cached_decision_still_applies(self):
        """
        When only weekly_limit_enabled is False (five_hour still enabled),
        cached throttle must still apply because five_hour limit is active.
        """
        self._insert_cached_throttle(delay_seconds=45)

        result = self._run_cached_check(
            weekly_limit_enabled=False, five_hour_limit_enabled=True
        )

        assert result["polled"] is False
        assert result["cached"] is True
        assert (
            result["decision"]["should_throttle"] is True
        ), "five_hour limit still enabled: cached throttle must apply"
        assert result["decision"]["delay_seconds"] == 45

    def test_only_five_hour_limit_disabled_cached_decision_still_applies(self):
        """
        When only five_hour_limit_enabled is False (weekly still enabled),
        cached throttle must still apply because weekly limit is active.
        """
        self._insert_cached_throttle(delay_seconds=20)

        result = self._run_cached_check(
            weekly_limit_enabled=True, five_hour_limit_enabled=False
        )

        assert result["polled"] is False
        assert result["cached"] is True
        assert (
            result["decision"]["should_throttle"] is True
        ), "weekly limit still enabled: cached throttle must apply"
        assert result["decision"]["delay_seconds"] == 20

    def test_both_limits_enabled_cached_decision_applies_normally(self):
        """
        When both limits are enabled (default), existing cached decision
        behavior is preserved: cached throttle applies unchanged.
        """
        self._insert_cached_throttle(delay_seconds=30)

        result = self._run_cached_check(
            weekly_limit_enabled=True, five_hour_limit_enabled=True
        )

        assert result["polled"] is False
        assert result["cached"] is True
        assert result["decision"]["should_throttle"] is True
        assert result["decision"]["delay_seconds"] == 30


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
