#!/usr/bin/env python3
"""
Integration tests for TDD toggle CLI commands (Story #14).

Tests end-to-end behavior of pace-maker tdd on/off commands.
"""

import unittest
import tempfile
import os
import json
import shutil


class TestTDDCLIIntegration(unittest.TestCase):
    """Test TDD CLI commands end-to-end."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.json")

    def tearDown(self):
        """Clean up."""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_tdd_on_command_sets_config(self):
        """Test that 'pace-maker tdd on' sets tdd_enabled=true."""
        from src.pacemaker.user_commands import execute_command

        # Disable TDD first
        execute_command("tdd", self.config_path, subcommand="off")

        # Enable TDD
        result = execute_command("tdd", self.config_path, subcommand="on")

        # Verify success
        self.assertTrue(result["success"])
        self.assertIn("ENABLED", result["message"])

        # Verify config
        with open(self.config_path) as f:
            config = json.load(f)
        self.assertTrue(config["tdd_enabled"])

    def test_tdd_off_command_sets_config(self):
        """Test that 'pace-maker tdd off' sets tdd_enabled=false."""
        from src.pacemaker.user_commands import execute_command

        # Enable TDD first
        execute_command("tdd", self.config_path, subcommand="on")

        # Disable TDD
        result = execute_command("tdd", self.config_path, subcommand="off")

        # Verify success
        self.assertTrue(result["success"])
        self.assertIn("DISABLED", result["message"])

        # Verify config
        with open(self.config_path) as f:
            config = json.load(f)
        self.assertFalse(config["tdd_enabled"])

    def test_status_shows_tdd_state_enabled(self):
        """Test that status shows TDD Enforcement: ENABLED."""
        from src.pacemaker.user_commands import execute_command

        # Enable TDD
        execute_command("tdd", self.config_path, subcommand="on")

        # Get status
        result = execute_command("status", self.config_path, db_path=None)

        # Verify TDD state shown
        self.assertTrue(result["success"])
        self.assertIn("TDD Enforcement: ENABLED", result["message"])

    def test_status_shows_tdd_state_disabled(self):
        """Test that status shows TDD Enforcement: DISABLED."""
        from src.pacemaker.user_commands import execute_command

        # Disable TDD
        execute_command("tdd", self.config_path, subcommand="off")

        # Get status
        result = execute_command("status", self.config_path, db_path=None)

        # Verify TDD state shown
        self.assertTrue(result["success"])
        self.assertIn("TDD Enforcement: DISABLED", result["message"])

    def test_tdd_command_parsing(self):
        """Test that 'pace-maker tdd on/off' is parsed correctly."""
        from src.pacemaker.user_commands import parse_command

        # Test 'pace-maker tdd on'
        result = parse_command("pace-maker tdd on")
        self.assertTrue(result["is_pace_maker_command"])
        self.assertEqual(result["command"], "tdd")
        self.assertEqual(result["subcommand"], "on")

        # Test 'pace-maker tdd off'
        result = parse_command("pace-maker tdd off")
        self.assertTrue(result["is_pace_maker_command"])
        self.assertEqual(result["command"], "tdd")
        self.assertEqual(result["subcommand"], "off")

    def test_tdd_invalid_subcommand_returns_error(self):
        """Test that invalid subcommand returns error."""
        from src.pacemaker.user_commands import execute_command

        # Execute with invalid subcommand
        result = execute_command("tdd", self.config_path, subcommand="invalid")

        # Should fail
        self.assertFalse(result["success"])
        self.assertIn("Unknown subcommand", result["message"])

    def test_tdd_missing_subcommand_returns_error(self):
        """Test that missing subcommand returns error."""
        from src.pacemaker.user_commands import execute_command

        # Execute without subcommand
        result = execute_command("tdd", self.config_path, subcommand=None)

        # Should fail
        self.assertFalse(result["success"])
        self.assertIn("unknown subcommand", result["message"].lower())


if __name__ == "__main__":
    unittest.main()
