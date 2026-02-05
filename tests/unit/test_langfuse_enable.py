#!/usr/bin/env python3
"""
Unit tests for Langfuse enable/disable commands (AC2).

Tests:
- AC2: Enable/Disable Langfuse Collection
  - langfuse_enabled set to true when "on" command run
  - langfuse_enabled set to false when "off" command run
  - Success messages confirm enabled/disabled status
"""

import json
import os
import tempfile
import unittest

from src.pacemaker import user_commands


class TestLangfuseEnable(unittest.TestCase):
    """Test AC2: Enable/Disable Langfuse Collection"""

    def setUp(self):
        """Create temporary config file for testing."""
        self.test_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.test_dir, "config.json")

    def tearDown(self):
        """Clean up temporary files."""
        import shutil

        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_langfuse_on_enables_flag(self):
        """AC2: langfuse_enabled set to true in config when 'on' command run"""
        # Act
        result = user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand="on",
        )

        # Assert - Command succeeds
        self.assertTrue(result["success"], f"Command failed: {result.get('message')}")

        # Assert - Flag set to true in config
        with open(self.config_path) as f:
            config = json.load(f)

        self.assertTrue(config.get("langfuse_enabled"))

    def test_langfuse_off_disables_flag(self):
        """AC2: langfuse_enabled set to false in config when 'off' command run"""
        # Arrange - First enable it
        user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand="on",
        )

        # Act - Then disable it
        result = user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand="off",
        )

        # Assert - Command succeeds
        self.assertTrue(result["success"], f"Command failed: {result.get('message')}")

        # Assert - Flag set to false in config
        with open(self.config_path) as f:
            config = json.load(f)

        self.assertFalse(config.get("langfuse_enabled"))

    def test_langfuse_on_success_message(self):
        """AC2: Success message confirms enabled status"""
        # Act
        result = user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand="on",
        )

        # Assert - Success message present
        self.assertTrue(result["success"])
        self.assertIn("enabled", result["message"].lower())
        self.assertIn("langfuse", result["message"].lower())

    def test_langfuse_off_success_message(self):
        """AC2: Success message confirms disabled status"""
        # Act
        result = user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand="off",
        )

        # Assert - Success message present
        self.assertTrue(result["success"])
        self.assertIn("disabled", result["message"].lower())
        self.assertIn("langfuse", result["message"].lower())


if __name__ == "__main__":
    unittest.main()
