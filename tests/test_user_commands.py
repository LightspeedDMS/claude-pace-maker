#!/usr/bin/env python3
"""
Tests for user control commands (pace-maker on/off/status).

Following TDD methodology:
1. Write failing tests first
2. Implement minimal code to pass
3. Refactor for quality
"""

import unittest
import tempfile
import os
import json
import sys
import io
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch


class TestUserCommandParsing(unittest.TestCase):
    """Test command parsing and pattern matching (AC1, AC5)."""

    def test_parse_pace_maker_on_command(self):
        """Should parse 'pace-maker on' command."""
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker on")
        self.assertEqual(result['command'], 'on')
        self.assertTrue(result['is_pace_maker_command'])

    def test_parse_pace_maker_off_command(self):
        """Should parse 'pace-maker off' command."""
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker off")
        self.assertEqual(result['command'], 'off')
        self.assertTrue(result['is_pace_maker_command'])

    def test_parse_pace_maker_status_command(self):
        """Should parse 'pace-maker status' command."""
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker status")
        self.assertEqual(result['command'], 'status')
        self.assertTrue(result['is_pace_maker_command'])

    def test_parse_case_insensitive_uppercase(self):
        """Should parse commands case-insensitively - uppercase (AC5)."""
        from pacemaker.user_commands import parse_command

        result = parse_command("PACE-MAKER ON")
        self.assertEqual(result['command'], 'on')
        self.assertTrue(result['is_pace_maker_command'])

    def test_parse_case_insensitive_mixed(self):
        """Should parse commands case-insensitively - mixed case (AC5)."""
        from pacemaker.user_commands import parse_command

        result = parse_command("Pace-Maker Status")
        self.assertEqual(result['command'], 'status')
        self.assertTrue(result['is_pace_maker_command'])

    def test_parse_non_pace_maker_command(self):
        """Should identify non-pace-maker commands (AC9)."""
        from pacemaker.user_commands import parse_command

        result = parse_command("implement this feature")
        self.assertFalse(result['is_pace_maker_command'])
        self.assertIsNone(result.get('command'))

    def test_parse_with_extra_whitespace(self):
        """Should handle extra whitespace in commands."""
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker   on  ")
        self.assertEqual(result['command'], 'on')
        self.assertTrue(result['is_pace_maker_command'])

    def test_parse_invalid_pace_maker_command(self):
        """Should reject invalid pace-maker commands."""
        from pacemaker.user_commands import parse_command

        result = parse_command("pace-maker invalid")
        self.assertFalse(result['is_pace_maker_command'])


class TestCommandExecution(unittest.TestCase):
    """Test command execution logic (AC2, AC3, AC4, AC6)."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, 'config.json')
        self.db_path = os.path.join(self.temp_dir, 'test.db')

        # Create initial config
        self.initial_config = {
            "enabled": False,
            "base_delay": 5,
            "max_delay": 120,
            "threshold_percent": 10,
            "poll_interval": 60
        }
        with open(self.config_path, 'w') as f:
            json.dump(self.initial_config, f)

    def tearDown(self):
        """Clean up."""
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_execute_on_command_enables_pacing(self):
        """Should enable pacing when 'on' command executed (AC2)."""
        from pacemaker.user_commands import execute_command

        result = execute_command('on', self.config_path)

        self.assertTrue(result['success'])

        # Verify config file updated
        with open(self.config_path) as f:
            config = json.load(f)
        self.assertTrue(config['enabled'])

    def test_execute_off_command_disables_pacing(self):
        """Should disable pacing when 'off' command executed (AC3)."""
        from pacemaker.user_commands import execute_command

        # First enable it
        with open(self.config_path, 'w') as f:
            json.dump({**self.initial_config, 'enabled': True}, f)

        result = execute_command('off', self.config_path)

        self.assertTrue(result['success'])

        # Verify config file updated
        with open(self.config_path) as f:
            config = json.load(f)
        self.assertFalse(config['enabled'])

    def test_execute_status_command_returns_state(self):
        """Should return current state when 'status' command executed (AC4)."""
        from pacemaker.user_commands import execute_command

        result = execute_command('status', self.config_path, self.db_path)

        self.assertTrue(result['success'])
        self.assertIn('enabled', result)
        self.assertFalse(result['enabled'])  # Initial state is disabled

    def test_configuration_update_is_atomic(self):
        """Should update configuration atomically (AC6)."""
        from pacemaker.user_commands import execute_command

        # Execute command
        execute_command('on', self.config_path)

        # Verify no .tmp files left behind
        tmp_files = [f for f in os.listdir(self.temp_dir) if f.endswith('.tmp')]
        self.assertEqual(len(tmp_files), 0)

        # Verify config is valid JSON and readable
        with open(self.config_path) as f:
            config = json.load(f)
        self.assertTrue(config['enabled'])

    def test_execute_on_provides_confirmation_message(self):
        """Should provide clear confirmation for 'on' command (AC7)."""
        from pacemaker.user_commands import execute_command

        result = execute_command('on', self.config_path)

        self.assertTrue(result['success'])
        self.assertIn('message', result)
        self.assertIn('ENABLED', result['message'])

    def test_execute_off_provides_confirmation_message(self):
        """Should provide clear confirmation for 'off' command (AC7)."""
        from pacemaker.user_commands import execute_command

        result = execute_command('off', self.config_path)

        self.assertTrue(result['success'])
        self.assertIn('message', result)
        self.assertIn('DISABLED', result['message'])

    def test_execute_status_with_no_usage_data(self):
        """Should handle status command with no usage data gracefully (AC4)."""
        from pacemaker.user_commands import execute_command

        result = execute_command('status', self.config_path, self.db_path)

        self.assertTrue(result['success'])
        self.assertIn('message', result)
        # Should indicate no usage data available


class TestStatusDisplay(unittest.TestCase):
    """Test status command output formatting (AC4)."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, 'config.json')
        self.db_path = os.path.join(self.temp_dir, 'test.db')

        # Create config
        config = {
            "enabled": True,
            "base_delay": 5,
            "max_delay": 120,
            "threshold_percent": 10,
            "poll_interval": 60
        }
        with open(self.config_path, 'w') as f:
            json.dump(config, f)

        # Initialize database and add usage data
        from pacemaker import database
        database.initialize_database(self.db_path)

        current_time = datetime.utcnow()
        database.insert_usage_snapshot(
            db_path=self.db_path,
            timestamp=current_time,
            five_hour_util=0.423,
            five_hour_resets_at=current_time + timedelta(hours=2),
            seven_day_util=0.187,
            seven_day_resets_at=current_time + timedelta(days=4),
            session_id='test-session'
        )

    def tearDown(self):
        """Clean up."""
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_status_displays_enabled_state(self):
        """Should display enabled state in status (AC4)."""
        from pacemaker.user_commands import execute_command

        result = execute_command('status', self.config_path, self.db_path)

        self.assertTrue(result['success'])
        self.assertTrue(result['enabled'])
        self.assertIn('ACTIVE', result['message'])

    def test_status_displays_disabled_state(self):
        """Should display disabled state in status (AC4)."""
        from pacemaker.user_commands import execute_command

        # Update config to disabled
        with open(self.config_path, 'w') as f:
            json.dump({'enabled': False}, f)

        result = execute_command('status', self.config_path, self.db_path)

        self.assertTrue(result['success'])
        self.assertFalse(result['enabled'])
        self.assertIn('INACTIVE', result['message'])

    def test_status_displays_usage_metrics(self):
        """Should display usage metrics in status (AC4)."""
        from pacemaker.user_commands import execute_command

        result = execute_command('status', self.config_path, self.db_path)

        self.assertTrue(result['success'])
        self.assertIn('usage_data', result)
        self.assertIsNotNone(result['usage_data'])

        usage = result['usage_data']
        self.assertIn('five_hour_util', usage)
        self.assertIn('seven_day_util', usage)


class TestHookIntegration(unittest.TestCase):
    """Test integration with hook system (AC1, AC8, AC9)."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, 'config.json')
        self.db_path = os.path.join(self.temp_dir, 'test.db')

        # Create initial config
        config = {
            "enabled": False,
            "base_delay": 5,
            "max_delay": 120,
            "threshold_percent": 10,
            "poll_interval": 60
        }
        with open(self.config_path, 'w') as f:
            json.dump(config, f)

    def tearDown(self):
        """Clean up."""
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_handle_user_prompt_intercepts_pace_maker_commands(self):
        """Should intercept pace-maker commands in hook (AC1, AC8)."""
        from pacemaker.user_commands import handle_user_prompt

        result = handle_user_prompt("pace-maker on", self.config_path, self.db_path)

        self.assertTrue(result['intercepted'])
        self.assertIn('output', result)
        # Should NOT return original prompt (command intercepted)

    def test_handle_user_prompt_passes_through_non_pace_maker(self):
        """Should pass through non-pace-maker prompts (AC9)."""
        from pacemaker.user_commands import handle_user_prompt

        original_prompt = "implement this feature"
        result = handle_user_prompt(original_prompt, self.config_path, self.db_path)

        self.assertFalse(result['intercepted'])
        self.assertEqual(result['passthrough'], original_prompt)

    def test_hook_command_does_not_reach_claude(self):
        """Should suppress pace-maker commands from reaching Claude (AC8)."""
        from pacemaker.user_commands import handle_user_prompt

        result = handle_user_prompt("pace-maker status", self.config_path, self.db_path)

        self.assertTrue(result['intercepted'])
        # When intercepted, should not have passthrough
        self.assertNotIn('passthrough', result)


class TestErrorHandling(unittest.TestCase):
    """Test error handling scenarios (AC7)."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, 'config.json')

    def tearDown(self):
        """Clean up."""
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_execute_command_with_missing_config_file(self):
        """Should handle missing config file gracefully (AC7)."""
        from pacemaker.user_commands import execute_command

        # Don't create config file
        missing_path = os.path.join(self.temp_dir, 'nonexistent.json')

        result = execute_command('status', missing_path)

        # Should create config with defaults or report error clearly
        self.assertIn('success', result)
        self.assertIn('message', result)

    def test_execute_command_with_corrupted_config(self):
        """Should handle corrupted config file gracefully (AC7)."""
        from pacemaker.user_commands import execute_command

        # Write invalid JSON
        with open(self.config_path, 'w') as f:
            f.write("INVALID JSON {{{")

        result = execute_command('on', self.config_path)

        self.assertFalse(result['success'])
        self.assertIn('message', result)
        self.assertIn('error', result['message'].lower())

    def test_error_messages_are_clear_and_helpful(self):
        """Should provide clear error messages (AC7)."""
        from pacemaker.user_commands import execute_command

        # Corrupted config
        with open(self.config_path, 'w') as f:
            f.write("INVALID")

        result = execute_command('on', self.config_path)

        self.assertFalse(result['success'])
        message = result['message']
        # Message should be clear about what went wrong
        self.assertTrue(len(message) > 10)  # Not just a code
        self.assertTrue(any(word in message.lower() for word in ['error', 'fail', 'invalid', 'corrupt']))


if __name__ == '__main__':
    unittest.main()
