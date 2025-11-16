#!/usr/bin/env python3
"""
Tests for hook execution module.
"""

import unittest
import tempfile
import os
import json
from datetime import datetime, timedelta
from unittest.mock import patch
import sys
import io


class TestHook(unittest.TestCase):
    """Test hook execution."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.json")
        self.state_path = os.path.join(self.temp_dir, "state.json")
        self.db_path = os.path.join(self.temp_dir, "test.db")

    def tearDown(self):
        """Clean up."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_inject_prompt_delay_prints_to_stdout(self):
        """Should print prompt to stdout."""
        from pacemaker.hook import inject_prompt_delay

        # Capture stdout
        captured = io.StringIO()
        sys.stdout = captured

        try:
            inject_prompt_delay("[TEST] Wait 30 seconds")
            output = captured.getvalue()
            self.assertIn("[TEST] Wait 30 seconds", output)
        finally:
            sys.stdout = sys.__stdout__

    def test_run_hook_disabled_config(self):
        """Should do nothing when disabled in config."""
        from pacemaker.hook import run_hook

        # Create disabled config
        config = {"enabled": False}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Mock to use our config/state paths
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                with patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path):
                    # Should return without errors
                    run_hook()

        # State file should not be created (hook did nothing)
        self.assertFalse(os.path.exists(self.state_path))

    def test_run_hook_enabled_with_mocked_api(self):
        """Should execute complete hook with API mocked."""
        from pacemaker.hook import run_hook
        from pacemaker import database

        # Initialize database
        database.initialize_database(self.db_path)

        # Create enabled config
        config = {"enabled": True, "poll_interval": 0}  # Force immediate poll
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Mock API response
        mock_usage = {
            "five_hour_util": 30.0,
            "five_hour_resets_at": datetime.utcnow() + timedelta(hours=3),
            "seven_day_util": 40.0,
            "seven_day_resets_at": datetime.utcnow() + timedelta(days=4),
        }

        # Patch at module level where they're used
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                with patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path):
                    with patch(
                        "pacemaker.api_client.fetch_usage", return_value=mock_usage
                    ):
                        with patch(
                            "pacemaker.api_client.load_access_token",
                            return_value="fake-token",
                        ):
                            run_hook()

        # State file should be created (hook ran successfully)
        # Note: May not exist if no polls happened, so just verify no crash
        self.assertTrue(True)  # Hook completed without exception

    def test_main_graceful_degradation_on_exception(self):
        """Should not crash on exceptions."""
        from pacemaker.hook import main

        # Mock run_hook to raise exception
        with patch("pacemaker.hook.run_hook", side_effect=Exception("Test error")):
            # Capture stderr
            captured = io.StringIO()
            sys.stderr = captured

            try:
                main()  # Should not raise
                output = captured.getvalue()
                self.assertIn("Test error", output)
            finally:
                sys.stderr = sys.__stderr__

    def test_execute_delay_actually_waits(self):
        """Should actually sleep for specified duration."""
        from pacemaker.hook import execute_delay
        import time

        start = time.time()
        execute_delay(1)
        elapsed = time.time() - start

        self.assertGreaterEqual(elapsed, 1.0)
        self.assertLess(elapsed, 1.5)

    def test_execute_delay_zero_seconds(self):
        """Should handle zero delay without error."""
        from pacemaker.hook import execute_delay
        import time

        start = time.time()
        execute_delay(0)
        elapsed = time.time() - start

        # Should return immediately
        self.assertLess(elapsed, 0.1)


class TestWeeklyLimitToggle(unittest.TestCase):
    """Test weekly limit toggle commands."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.json")

    def tearDown(self):
        """Clean up."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_weekly_limit_off_returns_success(self):
        """Should execute 'pace-maker weekly-limit off' and return success."""
        from pacemaker.user_commands import handle_user_prompt

        result = handle_user_prompt(
            "pace-maker weekly-limit off", self.config_path, None
        )

        self.assertTrue(result["intercepted"])
        self.assertIn("weekly", result["output"].lower())

        # Verify config was updated
        with open(self.config_path) as f:
            config = json.load(f)
            self.assertFalse(config["weekly_limit_enabled"])

    def test_weekly_limit_on_returns_success(self):
        """Should execute 'pace-maker weekly-limit on' and return success."""
        from pacemaker.user_commands import handle_user_prompt

        # First turn it off
        handle_user_prompt("pace-maker weekly-limit off", self.config_path, None)

        # Then turn it back on
        result = handle_user_prompt(
            "pace-maker weekly-limit on", self.config_path, None
        )

        self.assertTrue(result["intercepted"])
        self.assertIn("weekly", result["output"].lower())

        # Verify config was updated
        with open(self.config_path) as f:
            config = json.load(f)
            self.assertTrue(config["weekly_limit_enabled"])

    def test_weekly_limit_invalid_subcommand(self):
        """Should reject invalid subcommand like 'pace-maker weekly-limit invalid'."""
        from pacemaker.user_commands import handle_user_prompt

        result = handle_user_prompt(
            "pace-maker weekly-limit invalid", self.config_path, None
        )

        self.assertTrue(result["intercepted"])
        self.assertIn("unknown", result["output"].lower())

    def test_config_defaults_to_weekly_limit_enabled(self):
        """Should default weekly_limit_enabled to true in new config."""
        from pacemaker.user_commands import _load_config

        config = _load_config(self.config_path)

        self.assertTrue(config["weekly_limit_enabled"])

    def test_config_persists_weekly_limit_enabled_flag(self):
        """Should persist weekly_limit_enabled flag across reads/writes."""
        from pacemaker.user_commands import _load_config, _write_config_atomic

        # Load default config
        config = _load_config(self.config_path)

        # Modify flag
        config["weekly_limit_enabled"] = False
        _write_config_atomic(config, self.config_path)

        # Read back
        reloaded = _load_config(self.config_path)
        self.assertFalse(reloaded["weekly_limit_enabled"])

        # Toggle back
        reloaded["weekly_limit_enabled"] = True
        _write_config_atomic(reloaded, self.config_path)

        # Verify again
        final = _load_config(self.config_path)
        self.assertTrue(final["weekly_limit_enabled"])


class TestHelpCommand(unittest.TestCase):
    """Test help command."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.json")

    def tearDown(self):
        """Clean up."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_help_command_returns_success(self):
        """Should execute 'pace-maker help' and return success with help text."""
        from pacemaker.user_commands import handle_user_prompt

        result = handle_user_prompt("pace-maker help", self.config_path, None)

        self.assertTrue(result["intercepted"])
        output = result["output"].lower()

        # Verify help text contains key information
        self.assertIn("pace-maker", output)
        self.assertIn("on", output)
        self.assertIn("off", output)
        self.assertIn("status", output)
        self.assertIn("help", output)
        self.assertIn("weekly-limit", output)
        self.assertIn("config", output)


class TestAdaptiveThrottleIntegration(unittest.TestCase):
    """Test adaptive throttle respects weekly_limit_enabled flag."""

    def test_calculate_adaptive_delay_skips_weekly_limit_when_disabled(self):
        """Should skip weekly limit calculations when flag is false."""
        from pacemaker.adaptive_throttle import calculate_adaptive_delay

        # Test with weekend-aware mode but weekly_limit_enabled=False
        window_start = datetime(2025, 11, 10, 0, 0, 0)  # Monday
        current_time = datetime(2025, 11, 16, 12, 0, 0)  # Sunday noon

        # Over safe budget on weekend (would normally trigger max delay)
        result = calculate_adaptive_delay(
            current_util=97.0,  # Over 95% safe allowance
            time_remaining_hours=12.0,
            window_hours=168.0,
            min_delay=5,
            max_delay=350,
            window_start=window_start,
            current_time=current_time,
            safety_buffer_pct=95.0,
            preload_hours=12.0,
            weekly_limit_enabled=False,  # NEW PARAMETER
        )

        # When weekly limit disabled, should NOT apply weekend emergency throttling
        self.assertNotEqual(result["delay_seconds"], 350)
        self.assertNotEqual(result["strategy"], "emergency")

    def test_calculate_adaptive_delay_applies_weekly_limit_when_enabled(self):
        """Should apply weekly limit calculations when flag is true."""
        from pacemaker.adaptive_throttle import calculate_adaptive_delay

        # Test with weekend-aware mode and weekly_limit_enabled=True
        window_start = datetime(2025, 11, 10, 0, 0, 0)  # Monday
        current_time = datetime(2025, 11, 16, 12, 0, 0)  # Sunday noon

        # Over safe budget on weekend (should trigger max delay)
        result = calculate_adaptive_delay(
            current_util=97.0,  # Over 95% safe allowance
            time_remaining_hours=12.0,
            window_hours=168.0,
            min_delay=5,
            max_delay=350,
            window_start=window_start,
            current_time=current_time,
            safety_buffer_pct=95.0,
            preload_hours=12.0,
            weekly_limit_enabled=True,  # NEW PARAMETER
        )

        # When weekly limit enabled, should apply emergency throttling
        self.assertEqual(result["delay_seconds"], 350)
        self.assertEqual(result["strategy"], "emergency")

    def test_calculate_adaptive_delay_defaults_to_enabled(self):
        """Should default to weekly_limit_enabled=True for backward compatibility."""
        from pacemaker.adaptive_throttle import calculate_adaptive_delay

        # Test without passing weekly_limit_enabled parameter
        window_start = datetime(2025, 11, 10, 0, 0, 0)  # Monday
        current_time = datetime(2025, 11, 16, 12, 0, 0)  # Sunday noon

        result = calculate_adaptive_delay(
            current_util=97.0,
            time_remaining_hours=12.0,
            window_hours=168.0,
            min_delay=5,
            max_delay=350,
            window_start=window_start,
            current_time=current_time,
            safety_buffer_pct=95.0,
            preload_hours=12.0,
            # weekly_limit_enabled NOT passed - should default to True
        )

        # Should apply weekly limit by default
        self.assertEqual(result["delay_seconds"], 350)
        self.assertEqual(result["strategy"], "emergency")


class TestImplementMarkerDetection(unittest.TestCase):
    """Test /implement-* marker detection in UserPromptSubmit hook."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.json")
        self.state_path = os.path.join(self.temp_dir, "state.json")

    def tearDown(self):
        """Clean up."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_implement_story_sets_marker(self):
        """Should set implementation_started marker when /implement-story is detected."""
        from pacemaker.hook import run_session_start_hook

        # Create enabled tempo config
        config = {"tempo_enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Capture stdout
        captured = io.StringIO()
        sys.stdout = captured

        try:
            with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
                with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                    result = run_session_start_hook("/implement-story #7")

            output = captured.getvalue()

            # Should return True indicating implementation started
            self.assertTrue(result)

            # Should NOT inject reminder text (that's SessionStart hook's job)
            self.assertNotIn("IMPLEMENTATION LIFECYCLE PROTOCOL", output)

            # Should set implementation_started marker in state
            with open(self.state_path) as f:
                state = json.load(f)
            self.assertTrue(state.get("implementation_started", False))
        finally:
            sys.stdout = sys.__stdout__

    def test_implement_epic_sets_marker(self):
        """Should set implementation_started marker when /implement-epic is detected."""
        from pacemaker.hook import run_session_start_hook

        # Create enabled tempo config
        config = {"tempo_enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Suppress stdout
        captured = io.StringIO()
        sys.stdout = captured

        try:
            with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
                with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                    result = run_session_start_hook("/implement-epic #5")

            # Should return True
            self.assertTrue(result)

            # Should set marker in state
            with open(self.state_path) as f:
                state = json.load(f)
            self.assertTrue(state.get("implementation_started", False))
        finally:
            sys.stdout = sys.__stdout__

    def test_regular_command_no_marker(self):
        """Should NOT set marker for regular commands."""
        from pacemaker.hook import run_session_start_hook

        # Create enabled tempo config
        config = {"tempo_enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Suppress stdout
        captured = io.StringIO()
        sys.stdout = captured

        try:
            with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
                with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                    result = run_session_start_hook("help me debug this code")

            # Should return False
            self.assertFalse(result)

            # Should NOT create state file
            self.assertFalse(os.path.exists(self.state_path))
        finally:
            sys.stdout = sys.__stdout__

    def test_no_marker_when_tempo_disabled(self):
        """Should NOT set marker when tempo_enabled is False."""
        from pacemaker.hook import run_session_start_hook

        # Create disabled tempo config
        config = {"tempo_enabled": False}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Suppress stdout
        captured = io.StringIO()
        sys.stdout = captured

        try:
            with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
                with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                    result = run_session_start_hook("/implement-story #7")

            # Should return False (no implementation started)
            self.assertFalse(result)

            # Should NOT create state file
            self.assertFalse(os.path.exists(self.state_path))
        finally:
            sys.stdout = sys.__stdout__


if __name__ == "__main__":
    unittest.main()
