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
from unittest.mock import patch, Mock


class TestPacingEngine(unittest.TestCase):
    """Test pacing engine integration."""

    def setUp(self):
        """Create temporary database for each test."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
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
            seven_day_resets_at=datetime.utcnow() + timedelta(days=3)
        )

        self.assertEqual(decision['delay_seconds'], 0)
        self.assertFalse(decision['should_throttle'])

    def test_calculate_pacing_decision_throttle_needed(self):
        """Should return delay when over target + threshold."""
        from pacemaker.pacing_engine import calculate_pacing_decision

        # 5-hour window way over target
        # At 10% elapsed, target is ~9.5%, but we're at 50% = 40% over
        decision = calculate_pacing_decision(
            five_hour_util=50.0,
            five_hour_resets_at=datetime.utcnow() + timedelta(hours=4, minutes=30),  # 90% remaining
            seven_day_util=10.0,
            seven_day_resets_at=datetime.utcnow() + timedelta(days=6)
        )

        self.assertTrue(decision['should_throttle'])
        self.assertGreater(decision['delay_seconds'], 0)

    def test_calculate_pacing_decision_handles_null_windows(self):
        """Should handle NULL reset times (inactive windows)."""
        from pacemaker.pacing_engine import calculate_pacing_decision

        decision = calculate_pacing_decision(
            five_hour_util=0.0,
            five_hour_resets_at=None,  # Inactive
            seven_day_util=60.0,
            seven_day_resets_at=datetime.utcnow() + timedelta(days=3)
        )

        # Should still work with only one active window
        self.assertIsNotNone(decision)
        self.assertIn('delay_seconds', decision)

    def test_determine_delay_strategy_direct_execution(self):
        """Should use direct execution for delays < 30 seconds."""
        from pacemaker.pacing_engine import determine_delay_strategy

        strategy = determine_delay_strategy(delay_seconds=25)

        self.assertEqual(strategy['method'], 'direct')
        self.assertEqual(strategy['delay_seconds'], 25)
        self.assertIsNone(strategy.get('prompt'))

    def test_determine_delay_strategy_inject_prompt(self):
        """Should inject prompt for delays >= 30 seconds."""
        from pacemaker.pacing_engine import determine_delay_strategy

        strategy = determine_delay_strategy(delay_seconds=45)

        self.assertEqual(strategy['method'], 'prompt')
        self.assertIsNotNone(strategy['prompt'])
        self.assertIn('45', strategy['prompt'])

    def test_process_usage_update_stores_in_database(self):
        """Should store usage data in database."""
        from pacemaker.pacing_engine import process_usage_update
        from pacemaker.database import initialize_database, query_recent_snapshots

        initialize_database(self.db_path)

        usage_data = {
            'five_hour_util': 35.0,
            'five_hour_resets_at': datetime.utcnow() + timedelta(hours=3),
            'seven_day_util': 55.0,
            'seven_day_resets_at': datetime.utcnow() + timedelta(days=4)
        }

        process_usage_update(
            usage_data=usage_data,
            db_path=self.db_path,
            session_id='test-session'
        )

        # Verify stored
        snapshots = query_recent_snapshots(self.db_path, minutes=5)
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]['session_id'], 'test-session')

    def test_run_pacing_check_with_api_success(self):
        """Should complete full pacing check cycle with successful API call."""
        from pacemaker.pacing_engine import run_pacing_check
        from pacemaker.database import initialize_database

        initialize_database(self.db_path)

        # Mock successful API response
        mock_usage = {
            'five_hour_util': 40.0,
            'five_hour_resets_at': datetime.utcnow() + timedelta(hours=3),
            'seven_day_util': 50.0,
            'seven_day_resets_at': datetime.utcnow() + timedelta(days=4)
        }

        with patch('pacemaker.api_client.fetch_usage', return_value=mock_usage):
            with patch('pacemaker.api_client.load_access_token', return_value='fake-token'):
                result = run_pacing_check(
                    db_path=self.db_path,
                    session_id='test-session',
                    last_poll_time=None  # First poll
                )

        self.assertIsNotNone(result)
        self.assertIn('decision', result)
        self.assertIn('polled', result)
        self.assertTrue(result['polled'])

    def test_run_pacing_check_with_api_failure(self):
        """Should gracefully degrade when API fails."""
        from pacemaker.pacing_engine import run_pacing_check
        from pacemaker.database import initialize_database

        initialize_database(self.db_path)

        # Mock API failure
        with patch('pacemaker.api_client.fetch_usage', return_value=None):
            with patch('pacemaker.api_client.load_access_token', return_value='fake-token'):
                result = run_pacing_check(
                    db_path=self.db_path,
                    session_id='test-session',
                    last_poll_time=None
                )

        # Should return graceful result (no throttling when API unavailable)
        self.assertIsNotNone(result)
        self.assertFalse(result['decision']['should_throttle'])

    def test_run_pacing_check_skips_when_interval_not_met(self):
        """Should skip API call when less than 60 seconds since last poll."""
        from pacemaker.pacing_engine import run_pacing_check
        from pacemaker.database import initialize_database

        initialize_database(self.db_path)

        # Last poll 30 seconds ago
        last_poll = datetime.utcnow() - timedelta(seconds=30)

        result = run_pacing_check(
            db_path=self.db_path,
            session_id='test-session',
            last_poll_time=last_poll
        )

        self.assertIsNotNone(result)
        self.assertFalse(result['polled'])
        self.assertFalse(result['decision']['should_throttle'])


if __name__ == '__main__':
    unittest.main()
