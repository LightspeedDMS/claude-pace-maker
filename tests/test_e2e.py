#!/usr/bin/env python3
"""
End-to-end integration tests with ZERO mocking.

Tests the complete system:
- Real database operations
- Real file I/O
- Real timing
- Complete workflow
"""

import unittest
import tempfile
import os
import time
import json
from datetime import datetime, timedelta


class TestE2EIntegration(unittest.TestCase):
    """End-to-end integration tests with zero mocking."""

    def setUp(self):
        """Set up temporary environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.config_path = os.path.join(self.temp_dir, "config.json")
        self.state_path = os.path.join(self.temp_dir, "state.json")

    def tearDown(self):
        """Clean up temporary files."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_complete_pacing_workflow_with_real_database(self):
        """
        E2E: Complete pacing workflow using real database and calculations.

        Flow:
        1. Initialize database
        2. Simulate API response
        3. Run pacing check
        4. Verify database storage
        5. Verify pacing decision
        """
        from pacemaker import database, pacing_engine

        # 1. Initialize database
        result = database.initialize_database(self.db_path)
        self.assertTrue(result)
        self.assertTrue(os.path.exists(self.db_path))

        # 2. Create simulated usage data (realistic scenario: over target)
        current_time = datetime.utcnow()
        usage_data = {
            "five_hour_util": 60.0,  # High usage
            "five_hour_resets_at": current_time
            + timedelta(hours=2, minutes=30),  # 50% elapsed
            "seven_day_util": 70.0,  # High usage
            "seven_day_resets_at": current_time
            + timedelta(days=3, hours=12),  # 50% elapsed
        }

        # 3. Process usage update
        session_id = "e2e-test-session"
        stored = pacing_engine.process_usage_update(
            usage_data=usage_data, db_path=self.db_path, session_id=session_id
        )
        self.assertTrue(stored)

        # 4. Verify database storage
        snapshots = database.query_recent_snapshots(self.db_path, minutes=5)
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["session_id"], session_id)
        self.assertAlmostEqual(snapshots[0]["five_hour_util"], 60.0, places=1)

        # 5. Calculate pacing decision
        decision = pacing_engine.calculate_pacing_decision(
            five_hour_util=usage_data["five_hour_util"],
            five_hour_resets_at=usage_data["five_hour_resets_at"],
            seven_day_util=usage_data["seven_day_util"],
            seven_day_resets_at=usage_data["seven_day_resets_at"],
        )

        # At 50% time with 60% and 70% usage, we should be throttling
        self.assertTrue(decision["should_throttle"])
        self.assertGreater(decision["delay_seconds"], 0)

    def test_hook_state_persistence_across_invocations(self):
        """
        E2E: Verify hook state persists across multiple invocations.

        Flow:
        1. Run hook first time (no state)
        2. Verify state file created
        3. Run hook second time (with state)
        4. Verify state updated
        """
        from pacemaker.hook import load_state, save_state

        # 1. First invocation - no state
        state1 = load_state(self.state_path)
        self.assertIsNotNone(state1["session_id"])
        self.assertIsNone(state1["last_poll_time"])

        # 2. Save state with poll time
        state1["last_poll_time"] = datetime.utcnow()
        save_state(state1, self.state_path)
        self.assertTrue(os.path.exists(self.state_path))

        # 3. Second invocation - load existing state
        time.sleep(0.1)  # Small delay to ensure different timestamp
        state2 = load_state(self.state_path)
        self.assertEqual(state2["session_id"], state1["session_id"])
        self.assertIsNotNone(state2["last_poll_time"])

        # 4. Verify times are close (within 1 second)
        time_diff = abs(
            (state2["last_poll_time"] - state1["last_poll_time"]).total_seconds()
        )
        self.assertLess(time_diff, 1.0)

    def test_60_second_polling_throttle_with_real_timing(self):
        """
        E2E: Verify 60-second polling throttle with actual time delays.

        Flow:
        1. First check - should poll
        2. Immediate second check - should NOT poll (< 60s)
        3. Wait and third check - should poll (> 60s)
        """
        from pacemaker.pacing_engine import should_poll_api

        # 1. First poll
        first_poll = should_poll_api(None, interval=2)  # Use 2s for test speed
        self.assertTrue(first_poll)

        # 2. Immediate second poll - too soon
        poll_time = datetime.utcnow()
        second_poll = should_poll_api(poll_time, interval=2)
        self.assertFalse(second_poll)

        # 3. Wait 2 seconds
        time.sleep(2.1)
        third_poll = should_poll_api(poll_time, interval=2)
        self.assertTrue(third_poll)

    def test_hybrid_delay_strategy_execution(self):
        """
        E2E: Verify hybrid delay strategy with real execution.

        Flow:
        1. Small delay < 30s - direct execution
        2. Large delay >= 30s - prompt injection
        """
        from pacemaker.pacing_engine import determine_delay_strategy
        from pacemaker.hook import execute_delay

        # 1. Small delay - direct execution
        strategy = determine_delay_strategy(delay_seconds=2)
        self.assertEqual(strategy["method"], "direct")

        # Execute delay and verify it actually waits
        start = time.time()
        execute_delay(2)
        elapsed = time.time() - start
        self.assertGreaterEqual(elapsed, 2.0)
        self.assertLess(elapsed, 2.5)

        # 2. Large delay - prompt injection
        strategy = determine_delay_strategy(delay_seconds=45)
        self.assertEqual(strategy["method"], "prompt")
        self.assertIn("45", strategy["prompt"])

    def test_configuration_loading_and_defaults(self):
        """
        E2E: Verify configuration loading with real file I/O.

        Flow:
        1. Load config when file doesn't exist - get defaults
        2. Write custom config to file
        3. Load config - get custom values
        """
        from pacemaker.hook import load_config

        # 1. No config file - defaults
        config1 = load_config(self.config_path)
        self.assertEqual(config1["poll_interval"], 60)
        self.assertEqual(config1["base_delay"], 5)
        self.assertTrue(config1["enabled"])

        # 2. Write custom config
        custom_config = {"enabled": False, "poll_interval": 120, "base_delay": 10}
        with open(self.config_path, "w") as f:
            json.dump(custom_config, f)

        # 3. Load custom config
        config2 = load_config(self.config_path)
        self.assertFalse(config2["enabled"])
        self.assertEqual(config2["poll_interval"], 120)
        self.assertEqual(config2["base_delay"], 10)

    def test_graceful_degradation_with_corrupted_database(self):
        """
        E2E: Verify graceful degradation when database is corrupted.

        Flow:
        1. Initialize valid database
        2. Corrupt database file
        3. Attempt operations - should not crash
        """
        from pacemaker import database

        # 1. Initialize valid database
        database.initialize_database(self.db_path)

        # 2. Corrupt database
        with open(self.db_path, "wb") as f:
            f.write(b"CORRUPTED DATA")

        # 3. Attempt insert - should return False but not crash
        from datetime import datetime

        result = database.insert_usage_snapshot(
            db_path=self.db_path,
            timestamp=datetime.utcnow(),
            five_hour_util=50.0,
            five_hour_resets_at=datetime.utcnow(),
            seven_day_util=50.0,
            seven_day_resets_at=datetime.utcnow(),
            session_id="test",
        )
        self.assertFalse(result)  # Should fail gracefully

    def test_null_reset_times_end_to_end(self):
        """
        E2E: Verify NULL reset times (inactive windows) work end-to-end.

        Flow:
        1. Store snapshot with NULL 5-hour window
        2. Calculate pacing with NULL window
        3. Verify graceful handling
        """
        from pacemaker import database, pacing_engine

        # 1. Initialize and store with NULL
        database.initialize_database(self.db_path)

        usage_data = {
            "five_hour_util": 0.0,
            "five_hour_resets_at": None,  # NULL - inactive
            "seven_day_util": 50.0,
            "seven_day_resets_at": datetime.utcnow() + timedelta(days=3),
        }

        stored = pacing_engine.process_usage_update(
            usage_data=usage_data, db_path=self.db_path, session_id="null-test"
        )
        self.assertTrue(stored)

        # 2. Calculate pacing with NULL window
        decision = pacing_engine.calculate_pacing_decision(
            five_hour_util=0.0,
            five_hour_resets_at=None,
            seven_day_util=50.0,
            seven_day_resets_at=usage_data["seven_day_resets_at"],
        )

        # Should work - paces only on 7-day window
        self.assertIsNotNone(decision)
        self.assertEqual(decision["constrained_window"], "7-day")


if __name__ == "__main__":
    unittest.main()
