#!/usr/bin/env python3
"""
Integration tests demonstrating complete throttling workflow.

These tests verify the end-to-end throttling behavior with real scenarios.
"""

import unittest
import tempfile
import os
import json
import sys
from unittest.mock import patch
from datetime import datetime, timedelta


class TestThrottlingIntegration(unittest.TestCase):
    """Integration tests for complete throttling workflow."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.pacemaker_dir = os.path.join(self.temp_dir, ".claude-pace-maker")
        os.makedirs(self.pacemaker_dir)

        self.config_path = os.path.join(self.pacemaker_dir, "config.json")
        self.db_path = os.path.join(self.pacemaker_dir, "usage.db")
        self.state_path = os.path.join(self.pacemaker_dir, "state.json")

    def tearDown(self):
        """Clean up test environment."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_throttling_prompt_injection_when_credits_low(self):
        """
        Integration test: When credit utilization is high, hook must inject throttling prompt.

        Scenario:
        - User has 90% credit usage at 50% time elapsed (severe deviation)
        - Hook polls API and detects high utilization
        - Hook injects prompt asking Claude to wait 120 seconds
        - Prompt reaches stdout for Claude to see
        """
        from pacemaker import hook, database, pacing_engine

        # Create config with throttling enabled
        config = {
            "enabled": True,
            "base_delay": 5,
            "max_delay": 120,
            "threshold_percent": 0,
            "poll_interval": 0,  # Always poll for testing
        }
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Initialize database
        database.initialize_database(self.db_path)

        # Insert high utilization snapshot
        now = datetime.utcnow()
        five_hour_reset = now + timedelta(hours=2.5)  # 50% into window
        seven_day_reset = now + timedelta(days=3.5)

        database.insert_usage_snapshot(
            db_path=self.db_path,
            timestamp=now,
            five_hour_util=90.0,  # 90% used at 50% time = severe deviation
            five_hour_resets_at=five_hour_reset,
            seven_day_util=85.0,
            seven_day_resets_at=seven_day_reset,
            session_id="test-session",
        )

        # Mock pacing_engine to return throttling decision
        # (bypassing actual API call)
        def mock_pacing_check(*args, **kwargs):
            return {
                "polled": True,
                "decision": {
                    "should_throttle": True,
                    "delay_seconds": 120,
                    "strategy": {
                        "method": "prompt",
                        "prompt": "[PACING] Please wait 120 seconds to maintain credit budget...",
                    },
                },
                "poll_time": datetime.utcnow(),
            }

        # Capture stdout
        from io import StringIO

        captured_stdout = StringIO()

        with patch.object(hook, "DEFAULT_CONFIG_PATH", self.config_path), patch.object(
            hook, "DEFAULT_DB_PATH", self.db_path
        ), patch.object(hook, "DEFAULT_STATE_PATH", self.state_path), patch.object(
            pacing_engine, "run_pacing_check", side_effect=mock_pacing_check
        ), patch(
            "sys.stdout", captured_stdout
        ), patch.object(
            sys, "argv", ["hook.py", "post_tool_use"]
        ):

            # Execute hook
            hook.main()

        # Verify throttling prompt reached stdout
        output = captured_stdout.getvalue()
        self.assertIn(
            "[PACING]", output, "Throttling prompt must be injected to stdout"
        )
        self.assertIn("wait", output.lower(), "Prompt must mention waiting")
        self.assertIn("120 seconds", output, "Prompt must specify delay duration")

    def test_no_throttling_when_credits_healthy(self):
        """
        Integration test: When credit utilization is healthy, no throttling occurs.

        Scenario:
        - User has 30% credit usage at 50% time elapsed (on pace)
        - Hook polls and determines no throttling needed
        - Hook produces no output (no delay message)
        """
        from pacemaker import hook, pacing_engine

        # Create config
        config = {
            "enabled": True,
            "base_delay": 5,
            "threshold_percent": 0,
            "poll_interval": 0,
        }
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Mock pacing_engine to return no throttling
        def mock_pacing_check(*args, **kwargs):
            return {
                "polled": True,
                "decision": {"should_throttle": False, "delay_seconds": 0},
                "poll_time": datetime.utcnow(),
            }

        # Capture stdout
        from io import StringIO

        captured_stdout = StringIO()

        with patch.object(hook, "DEFAULT_CONFIG_PATH", self.config_path), patch.object(
            hook, "DEFAULT_DB_PATH", self.db_path
        ), patch.object(hook, "DEFAULT_STATE_PATH", self.state_path), patch.object(
            pacing_engine, "run_pacing_check", side_effect=mock_pacing_check
        ), patch(
            "sys.stdout", captured_stdout
        ), patch.object(
            sys, "argv", ["hook.py", "post_tool_use"]
        ):

            # Execute hook
            hook.main()

        # Verify NO throttling output
        output = captured_stdout.getvalue()
        self.assertEqual(output, "", "No throttling output when credits are healthy")

    def test_direct_delay_for_small_deviations(self):
        """
        Integration test: Small deviations use direct sleep instead of prompt.

        Scenario:
        - User has slight deviation requiring 15 second delay (< 30 seconds)
        - Hook uses direct execution (sleep) instead of prompt injection
        - No output to stdout (delay happens silently)
        """
        from pacemaker import hook, pacing_engine
        import time

        # Create config
        config = {
            "enabled": True,
            "base_delay": 5,
            "threshold_percent": 0,
            "poll_interval": 0,
        }
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Mock pacing_engine to return direct delay strategy
        def mock_pacing_check(*args, **kwargs):
            return {
                "polled": True,
                "decision": {
                    "should_throttle": True,
                    "delay_seconds": 2,  # Small delay for testing
                    "strategy": {
                        "method": "direct",
                        "delay_seconds": 2,  # Use 2 seconds for fast test
                    },
                },
                "poll_time": datetime.utcnow(),
            }

        # Capture stdout
        from io import StringIO

        captured_stdout = StringIO()

        with patch.object(hook, "DEFAULT_CONFIG_PATH", self.config_path), patch.object(
            hook, "DEFAULT_DB_PATH", self.db_path
        ), patch.object(hook, "DEFAULT_STATE_PATH", self.state_path), patch.object(
            pacing_engine, "run_pacing_check", side_effect=mock_pacing_check
        ), patch(
            "sys.stdout", captured_stdout
        ), patch.object(
            sys, "argv", ["hook.py", "post_tool_use"]
        ):

            # Execute hook and measure time
            start = time.time()
            hook.main()
            elapsed = time.time() - start

        # Verify delay happened (at least 2 seconds)
        self.assertGreaterEqual(elapsed, 2.0, "Direct delay must actually sleep")

        # Verify no output (silent delay)
        output = captured_stdout.getvalue()
        self.assertEqual(output, "", "Direct delay produces no output")

    def test_disabled_pacemaker_produces_no_throttling(self):
        """
        Integration test: When pace maker is disabled, no throttling occurs.

        Scenario:
        - User has disabled pace maker in config
        - Even with high credit usage, no throttling happens
        - Hook exits immediately with no output
        """
        from pacemaker import hook

        # Create DISABLED config
        config = {"enabled": False}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Capture stdout
        from io import StringIO

        captured_stdout = StringIO()

        with patch.object(hook, "DEFAULT_CONFIG_PATH", self.config_path), patch.object(
            hook, "DEFAULT_DB_PATH", self.db_path
        ), patch.object(hook, "DEFAULT_STATE_PATH", self.state_path), patch(
            "sys.stdout", captured_stdout
        ), patch.object(
            sys, "argv", ["hook.py", "post_tool_use"]
        ):

            # Execute hook
            hook.main()

        # Verify no output
        output = captured_stdout.getvalue()
        self.assertEqual(output, "", "Disabled pace maker produces no output")


if __name__ == "__main__":
    unittest.main()
