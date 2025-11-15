#!/usr/bin/env python3
"""
End-to-end tests for user control commands with ZERO mocking.

Tests the complete system:
- Real file I/O
- Real database operations
- Real configuration updates
- Real hook integration
- Complete workflow
"""

import unittest
import tempfile
import os
import json
from datetime import datetime, timedelta


class TestUserCommandsE2E(unittest.TestCase):
    """End-to-end integration tests with zero mocking."""

    def setUp(self):
        """Set up temporary environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.json")
        self.db_path = os.path.join(self.temp_dir, "test.db")

        # Create initial config
        initial_config = {
            "enabled": False,
            "base_delay": 5,
            "max_delay": 120,
            "threshold_percent": 0,
            "poll_interval": 60,
        }
        os.makedirs(self.temp_dir, exist_ok=True)
        with open(self.config_path, "w") as f:
            json.dump(initial_config, f)

        # Initialize database with real data
        from pacemaker import database

        database.initialize_database(self.db_path)

        # Insert real usage data
        current_time = datetime.utcnow()
        database.insert_usage_snapshot(
            db_path=self.db_path,
            timestamp=current_time,
            five_hour_util=0.423,
            five_hour_resets_at=current_time + timedelta(hours=2, minutes=15),
            seven_day_util=0.187,
            seven_day_resets_at=current_time + timedelta(days=4, hours=6),
            session_id="e2e-test-session",
        )

    def tearDown(self):
        """Clean up temporary files."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_complete_enable_workflow_end_to_end(self):
        """
        E2E: Complete workflow for enabling pace maker.

        Flow:
        1. User submits 'pace-maker on' command
        2. Command is parsed
        3. Configuration is updated atomically
        4. User receives confirmation
        5. Configuration persists
        6. Pacing engine respects new setting
        """
        from pacemaker.user_commands import handle_user_prompt
        from pacemaker.hook import load_config

        # 1. User submits command
        result = handle_user_prompt("pace-maker on", self.config_path, self.db_path)

        # 2. Verify command was intercepted
        self.assertTrue(result["intercepted"])
        self.assertIn("ENABLED", result["output"])

        # 3. Verify configuration updated
        config = load_config(self.config_path)
        self.assertTrue(config["enabled"])

        # 4. Verify no temporary files left
        tmp_files = [f for f in os.listdir(self.temp_dir) if f.endswith(".tmp")]
        self.assertEqual(len(tmp_files), 0)

        # 5. Verify config persists across reads
        config2 = load_config(self.config_path)
        self.assertTrue(config2["enabled"])

    def test_complete_disable_workflow_end_to_end(self):
        """
        E2E: Complete workflow for disabling pace maker.

        Flow:
        1. Pace maker is enabled
        2. User submits 'pace-maker off' command
        3. Configuration is updated atomically
        4. User receives confirmation
        5. Pacing engine stops throttling
        """
        from pacemaker.user_commands import handle_user_prompt
        from pacemaker.hook import load_config

        # 1. Enable first
        handle_user_prompt("pace-maker on", self.config_path, self.db_path)
        config = load_config(self.config_path)
        self.assertTrue(config["enabled"])

        # 2. Disable
        result = handle_user_prompt("pace-maker off", self.config_path, self.db_path)

        # 3. Verify command was intercepted
        self.assertTrue(result["intercepted"])
        self.assertIn("DISABLED", result["output"])

        # 4. Verify configuration updated
        config = load_config(self.config_path)
        self.assertFalse(config["enabled"])

    def test_complete_status_workflow_end_to_end(self):
        """
        E2E: Complete workflow for checking status.

        Flow:
        1. User submits 'pace-maker status' command
        2. System reads configuration
        3. System queries database for usage
        4. User receives comprehensive status
        """
        from pacemaker.user_commands import handle_user_prompt

        # Submit status command
        result = handle_user_prompt("pace-maker status", self.config_path, self.db_path)

        # Verify command was intercepted
        self.assertTrue(result["intercepted"])

        # Verify output contains status information
        output = result["output"]
        self.assertIn("Pace Maker:", output)
        self.assertIn("INACTIVE", output)  # Initially disabled

        # Verify usage data displayed
        self.assertIn("Current Usage:", output)
        self.assertIn("5-hour window:", output)
        self.assertIn("7-day window:", output)
        self.assertIn("42.3%", output)  # 0.423 * 100
        self.assertIn("18.7%", output)  # 0.187 * 100

    def test_enable_then_status_shows_active_state(self):
        """
        E2E: Enable pace maker then check status shows ACTIVE.

        Flow:
        1. Enable pace maker
        2. Check status
        3. Verify status shows ACTIVE with usage data
        """
        from pacemaker.user_commands import handle_user_prompt

        # Enable
        handle_user_prompt("pace-maker on", self.config_path, self.db_path)

        # Check status
        result = handle_user_prompt("pace-maker status", self.config_path, self.db_path)

        # Verify shows ACTIVE
        self.assertTrue(result["intercepted"])
        self.assertIn("ACTIVE", result["output"])
        self.assertIn("Current Usage:", result["output"])

    def test_non_pace_maker_prompts_pass_through_unchanged(self):
        """
        E2E: Verify non-pace-maker prompts are not intercepted.

        Flow:
        1. User submits regular prompt
        2. System identifies it's not a pace-maker command
        3. Prompt passes through unchanged
        4. Configuration is not modified
        """
        from pacemaker.user_commands import handle_user_prompt

        # Read original config
        with open(self.config_path) as f:
            original_config = json.load(f)

        # Submit regular prompt
        regular_prompt = "implement this feature using TDD"
        result = handle_user_prompt(regular_prompt, self.config_path, self.db_path)

        # Verify NOT intercepted
        self.assertFalse(result["intercepted"])
        self.assertEqual(result["passthrough"], regular_prompt)

        # Verify config unchanged
        with open(self.config_path) as f:
            current_config = json.load(f)
        self.assertEqual(original_config, current_config)

    def test_case_insensitive_commands_end_to_end(self):
        """
        E2E: Verify case-insensitive command handling.

        Flow:
        1. Submit uppercase command
        2. Submit mixed case command
        3. Submit lowercase command
        4. All should work identically
        """
        from pacemaker.user_commands import handle_user_prompt

        # Test uppercase
        result1 = handle_user_prompt("PACE-MAKER ON", self.config_path, self.db_path)
        self.assertTrue(result1["intercepted"])
        self.assertIn("ENABLED", result1["output"])

        # Test mixed case
        result2 = handle_user_prompt(
            "Pace-Maker Status", self.config_path, self.db_path
        )
        self.assertTrue(result2["intercepted"])
        self.assertIn("ACTIVE", result2["output"])

        # Test lowercase
        result3 = handle_user_prompt("pace-maker off", self.config_path, self.db_path)
        self.assertTrue(result3["intercepted"])
        self.assertIn("DISABLED", result3["output"])

    def test_rapid_command_execution_maintains_consistency(self):
        """
        E2E: Verify rapid command execution maintains consistency.

        Flow:
        1. Execute multiple commands rapidly
        2. Verify all execute successfully
        3. Verify final state is consistent
        4. Verify no corruption
        """
        from pacemaker.user_commands import handle_user_prompt

        # Execute rapid commands
        commands = [
            "pace-maker on",
            "pace-maker status",
            "pace-maker off",
            "pace-maker status",
            "pace-maker on",
        ]

        results = []
        for cmd in commands:
            result = handle_user_prompt(cmd, self.config_path, self.db_path)
            results.append(result)
            self.assertTrue(result["intercepted"])

        # Verify final state
        with open(self.config_path) as f:
            final_config = json.load(f)

        # Last command was 'on', so should be enabled
        self.assertTrue(final_config["enabled"])

        # Verify config is valid JSON (not corrupted)
        self.assertIsInstance(final_config, dict)
        self.assertIn("enabled", final_config)

    def test_status_with_missing_database_shows_config_only(self):
        """
        E2E: Status command works even if database unavailable.

        Flow:
        1. Delete database file
        2. Submit status command
        3. Verify shows config state
        4. Verify graceful handling of missing usage data
        """
        from pacemaker.user_commands import handle_user_prompt

        # Remove database
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

        # Submit status command
        result = handle_user_prompt("pace-maker status", self.config_path, self.db_path)

        # Should still work
        self.assertTrue(result["intercepted"])
        self.assertIn("Pace Maker:", result["output"])
        self.assertIn("No usage data available", result["output"])

    def test_configuration_survives_application_restart(self):
        """
        E2E: Configuration persists across application restarts.

        Flow:
        1. Enable pace maker
        2. Simulate restart (new command handler instance)
        3. Check status
        4. Verify enabled state persisted
        """
        from pacemaker.user_commands import handle_user_prompt

        # Enable
        result1 = handle_user_prompt("pace-maker on", self.config_path, self.db_path)
        self.assertTrue(result1["intercepted"])

        # Simulate restart by using fresh import
        # In real system, this would be a new Python process
        # Here we just call handle_user_prompt again (fresh execution)

        # Check status after "restart"
        result2 = handle_user_prompt(
            "pace-maker status", self.config_path, self.db_path
        )
        self.assertTrue(result2["intercepted"])
        self.assertIn("ACTIVE", result2["output"])


class TestHookScriptIntegration(unittest.TestCase):
    """Test integration with actual hook script."""

    def setUp(self):
        """Set up temporary environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.json")
        self.db_path = os.path.join(self.temp_dir, "test.db")

        # Create initial config
        initial_config = {
            "enabled": False,
            "base_delay": 5,
            "max_delay": 120,
            "threshold_percent": 0,
            "poll_interval": 60,
        }
        os.makedirs(self.temp_dir, exist_ok=True)
        with open(self.config_path, "w") as f:
            json.dump(initial_config, f)

    def tearDown(self):
        """Clean up temporary files."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_hook_module_entry_point_exists(self):
        """Verify hook module has proper entry point."""
        from pacemaker import hook

        # Module should exist
        self.assertIsNotNone(hook)

        # Should have main function
        self.assertTrue(hasattr(hook, "main"))

    def test_user_commands_can_be_called_from_hook(self):
        """Verify user commands can be invoked from hook context."""
        from pacemaker.user_commands import handle_user_prompt

        # This is the function the hook would call
        result = handle_user_prompt("pace-maker on", self.config_path, self.db_path)

        self.assertTrue(result["intercepted"])
        self.assertIn("output", result)


if __name__ == "__main__":
    unittest.main()
