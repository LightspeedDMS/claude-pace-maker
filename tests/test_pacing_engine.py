#!/usr/bin/env python3
"""
Integration tests for pacing engine.

Tests the main orchestration logic:
- 60-second polling throttle
- Database persistence
- Pacing decision making
- Hybrid delay strategy
"""

import unittest
import tempfile
import os
from datetime import datetime, timedelta
from unittest.mock import patch


class TestPacingEngine(unittest.TestCase):
    """Test pacing engine integration."""

    def setUp(self):
        """Create temporary database for each test."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name

    def tearDown(self):
        """Clean up temporary database."""
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_should_poll_api_after_60_seconds(self):
        """Should return True when 60+ seconds since last poll."""
        from pacemaker.pacing_engine import should_poll_api

        # Last poll 61 seconds ago
        last_poll = datetime.utcnow() - timedelta(seconds=61)
        result = should_poll_api(last_poll, interval=60)

        self.assertTrue(result)

    def test_should_not_poll_api_before_60_seconds(self):
        """Should return False when less than 60 seconds since last poll."""
        from pacemaker.pacing_engine import should_poll_api

        # Last poll 30 seconds ago
        last_poll = datetime.utcnow() - timedelta(seconds=30)
        result = should_poll_api(last_poll, interval=60)

        self.assertFalse(result)

    def test_should_poll_api_first_time(self):
        """Should return True on first call (no last poll time)."""
        from pacemaker.pacing_engine import should_poll_api

        result = should_poll_api(None, interval=60)

        self.assertTrue(result)

    def test_calculate_pacing_decision_no_throttle_needed(self):
        """Should return no delay when within target."""
        from pacemaker.pacing_engine import calculate_pacing_decision

        # Both windows well within target
        decision = calculate_pacing_decision(
            five_hour_util=30.0,
            five_hour_resets_at=datetime.utcnow() + timedelta(hours=2),
            seven_day_util=40.0,
            seven_day_resets_at=datetime.utcnow() + timedelta(days=3),
        )

        self.assertEqual(decision["delay_seconds"], 0)
        self.assertFalse(decision["should_throttle"])

    def test_calculate_pacing_decision_throttle_needed(self):
        """Should return delay when over target + threshold."""
        from pacemaker.pacing_engine import calculate_pacing_decision

        # 5-hour window way over target
        # At 10% elapsed, target is ~9.5%, but we're at 50% = 40% over
        decision = calculate_pacing_decision(
            five_hour_util=50.0,
            five_hour_resets_at=datetime.utcnow()
            + timedelta(hours=4, minutes=30),  # 90% remaining
            seven_day_util=10.0,
            seven_day_resets_at=datetime.utcnow() + timedelta(days=6),
        )

        self.assertTrue(decision["should_throttle"])
        self.assertGreater(decision["delay_seconds"], 0)

    def test_calculate_pacing_decision_handles_null_windows(self):
        """Should handle NULL reset times (inactive windows)."""
        from pacemaker.pacing_engine import calculate_pacing_decision

        decision = calculate_pacing_decision(
            five_hour_util=0.0,
            five_hour_resets_at=None,  # Inactive
            seven_day_util=60.0,
            seven_day_resets_at=datetime.utcnow() + timedelta(days=3),
        )

        # Should still work with only one active window
        self.assertIsNotNone(decision)
        self.assertIn("delay_seconds", decision)

    def test_determine_delay_strategy_direct_execution(self):
        """Should use direct execution for delays < 30 seconds."""
        from pacemaker.pacing_engine import determine_delay_strategy

        strategy = determine_delay_strategy(delay_seconds=25)

        self.assertEqual(strategy["method"], "direct")
        self.assertEqual(strategy["delay_seconds"], 25)
        self.assertIsNone(strategy.get("prompt"))

    def test_determine_delay_strategy_inject_prompt(self):
        """Should inject prompt for delays >= 30 seconds."""
        from pacemaker.pacing_engine import determine_delay_strategy

        strategy = determine_delay_strategy(delay_seconds=45)

        self.assertEqual(strategy["method"], "prompt")
        self.assertIsNotNone(strategy["prompt"])
        self.assertIn("45", strategy["prompt"])

    def test_process_usage_update_stores_in_database(self):
        """Should store usage data in database."""
        from pacemaker.pacing_engine import process_usage_update
        from pacemaker.database import initialize_database, query_recent_snapshots

        initialize_database(self.db_path)

        usage_data = {
            "five_hour_util": 35.0,
            "five_hour_resets_at": datetime.utcnow() + timedelta(hours=3),
            "seven_day_util": 55.0,
            "seven_day_resets_at": datetime.utcnow() + timedelta(days=4),
        }

        process_usage_update(
            usage_data=usage_data, db_path=self.db_path, session_id="test-session"
        )

        # Verify stored
        snapshots = query_recent_snapshots(self.db_path, minutes=5)
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["session_id"], "test-session")

    def test_run_pacing_check_with_api_success(self):
        """Should complete full pacing check cycle with successful API call."""
        from pacemaker.pacing_engine import run_pacing_check
        from pacemaker.database import initialize_database

        initialize_database(self.db_path)

        # Mock successful API response
        mock_usage = {
            "five_hour_util": 40.0,
            "five_hour_resets_at": datetime.utcnow() + timedelta(hours=3),
            "seven_day_util": 50.0,
            "seven_day_resets_at": datetime.utcnow() + timedelta(days=4),
        }

        with patch("pacemaker.api_client.fetch_usage", return_value=mock_usage):
            with patch(
                "pacemaker.api_client.load_access_token", return_value="fake-token"
            ):
                result = run_pacing_check(
                    db_path=self.db_path,
                    session_id="test-session",
                    last_poll_time=None,  # First poll
                )

        self.assertIsNotNone(result)
        self.assertIn("decision", result)
        self.assertIn("polled", result)
        self.assertTrue(result["polled"])

    def test_run_pacing_check_with_api_failure(self):
        """Should gracefully degrade when API fails."""
        from pacemaker.pacing_engine import run_pacing_check
        from pacemaker.database import initialize_database

        initialize_database(self.db_path)

        # Mock API failure
        with patch("pacemaker.api_client.fetch_usage", return_value=None):
            with patch(
                "pacemaker.api_client.load_access_token", return_value="fake-token"
            ):
                result = run_pacing_check(
                    db_path=self.db_path, session_id="test-session", last_poll_time=None
                )

        # Should return graceful result (no throttling when API unavailable)
        self.assertIsNotNone(result)
        self.assertFalse(result["decision"]["should_throttle"])

    def test_run_pacing_check_skips_when_interval_not_met(self):
        """Should skip API call when less than 60 seconds since last poll."""
        from pacemaker.pacing_engine import run_pacing_check
        from pacemaker.database import initialize_database

        initialize_database(self.db_path)

        # Last poll 30 seconds ago
        last_poll = datetime.utcnow() - timedelta(seconds=30)

        result = run_pacing_check(
            db_path=self.db_path, session_id="test-session", last_poll_time=last_poll
        )

        self.assertIsNotNone(result)
        self.assertFalse(result["polled"])
        self.assertFalse(result["decision"]["should_throttle"])

    def test_status_shows_weekend_aware_target_on_saturday(self):
        """Status should show frozen 100% target on Saturday for 7-day window."""
        from pacemaker.pacing_engine import calculate_pacing_decision

        # Setup: Saturday noon in 7-day window
        saturday_noon = datetime(2025, 1, 11, 12, 0, 0)  # Saturday
        monday_start = datetime(2025, 1, 6, 0, 0, 0)  # Monday
        sunday_end = monday_start + timedelta(hours=168)

        # Mock datetime.utcnow in both modules
        with patch("pacemaker.pacing_engine.datetime") as mock_dt_engine:
            with patch("pacemaker.adaptive_throttle.datetime") as mock_dt_adaptive:
                mock_dt_engine.utcnow.return_value = saturday_noon
                mock_dt_adaptive.utcnow.return_value = saturday_noon
                # Allow datetime constructor to work normally
                mock_dt_adaptive.side_effect = lambda *args, **kwargs: (
                    datetime(*args, **kwargs) if args else saturday_noon
                )

                decision = calculate_pacing_decision(
                    five_hour_util=50.0,
                    five_hour_resets_at=saturday_noon + timedelta(hours=2),
                    seven_day_util=95.0,
                    seven_day_resets_at=sunday_end,
                    use_adaptive=True,
                )

        # Should show frozen 100% target (all weekday time elapsed)
        self.assertAlmostEqual(decision["seven_day"]["target"], 100.0, delta=0.1)

    def test_status_shows_weekend_aware_target_on_wednesday(self):
        """Status should show ~50% target on Wednesday for 7-day window."""
        from pacemaker.pacing_engine import calculate_pacing_decision

        # Wednesday noon (2.5 weekdays / 5 weekdays = 50%)
        wednesday_noon = datetime(2025, 1, 8, 12, 0, 0)  # Wednesday
        monday_start = datetime(2025, 1, 6, 0, 0, 0)  # Monday
        sunday_end = monday_start + timedelta(hours=168)

        # Mock datetime.utcnow in pacing_engine (adaptive_throttle doesn't need mocking for allowance calc)
        with patch("pacemaker.pacing_engine.datetime") as mock_dt:
            mock_dt.utcnow.return_value = wednesday_noon
            mock_dt.side_effect = lambda *args, **kwargs: (
                datetime(*args, **kwargs) if args else wednesday_noon
            )

            decision = calculate_pacing_decision(
                five_hour_util=50.0,
                five_hour_resets_at=wednesday_noon + timedelta(hours=2),
                seven_day_util=45.0,
                seven_day_resets_at=sunday_end,
                use_adaptive=True,
            )

        # Should show ~50% target (2.5 weekdays / 5 weekdays)
        self.assertAlmostEqual(decision["seven_day"]["target"], 50.0, delta=1.0)

    def test_status_shows_legacy_linear_target_when_not_adaptive(self):
        """Status should show linear target when use_adaptive=False."""
        from pacemaker.pacing_engine import calculate_pacing_decision

        # Wednesday noon is 60 hours into 168-hour window = 35.71% elapsed
        wednesday_noon = datetime(2025, 1, 8, 12, 0, 0)
        monday_start = datetime(2025, 1, 6, 0, 0, 0)
        sunday_end = monday_start + timedelta(hours=168)

        # Mock calculator.datetime.utcnow to control time_percent calculation
        with patch("pacemaker.calculator.datetime") as mock_calc_dt:
            mock_calc_dt.utcnow.return_value = wednesday_noon
            mock_calc_dt.side_effect = lambda *args, **kwargs: (
                datetime(*args, **kwargs) if args else wednesday_noon
            )

            decision = calculate_pacing_decision(
                five_hour_util=50.0,
                five_hour_resets_at=wednesday_noon + timedelta(hours=2),
                seven_day_util=45.0,
                seven_day_resets_at=sunday_end,
                use_adaptive=False,  # Legacy mode
            )

        # Should show linear target (not weekend-aware)
        # At 35.71% time elapsed, linear target = 35.71%
        self.assertIsNotNone(decision["seven_day"]["target"])
        self.assertAlmostEqual(decision["seven_day"]["target"], 35.71, delta=0.5)

    def test_status_five_hour_window_unchanged_logarithmic(self):
        """5-hour window should still use logarithmic target (no weekend awareness)."""
        from pacemaker.pacing_engine import calculate_pacing_decision

        # Saturday noon
        saturday_noon = datetime(2025, 1, 11, 12, 0, 0)
        five_hour_resets = saturday_noon + timedelta(
            hours=4
        )  # 20% elapsed, 80% remaining
        monday_start = datetime(2025, 1, 6, 0, 0, 0)
        sunday_end = monday_start + timedelta(hours=168)

        # Mock calculator.datetime.utcnow to control time_percent calculation
        with patch("pacemaker.calculator.datetime") as mock_calc_dt:
            with patch("pacemaker.pacing_engine.datetime") as mock_engine_dt:
                mock_calc_dt.utcnow.return_value = saturday_noon
                mock_engine_dt.utcnow.return_value = saturday_noon
                mock_calc_dt.side_effect = lambda *args, **kwargs: (
                    datetime(*args, **kwargs) if args else saturday_noon
                )
                mock_engine_dt.side_effect = lambda *args, **kwargs: (
                    datetime(*args, **kwargs) if args else saturday_noon
                )

                decision = calculate_pacing_decision(
                    five_hour_util=50.0,
                    five_hour_resets_at=five_hour_resets,
                    seven_day_util=95.0,
                    seven_day_resets_at=sunday_end,
                    use_adaptive=True,
                )

        # 5-hour window should use logarithmic target (not linear or weekend-aware)
        # At 20% elapsed (1 hour / 5 hours), logarithmic = 29.54% (higher than linear 20%)
        five_hour_target = decision["five_hour"]["target"]
        # Logarithmic at 20% elapsed ≈ 29.54%
        self.assertAlmostEqual(five_hour_target, 29.54, delta=0.5)
        # Verify it's using logarithmic (not linear which would be 20%)
        self.assertGreater(five_hour_target, 20.0)


if __name__ == "__main__":
    unittest.main()
