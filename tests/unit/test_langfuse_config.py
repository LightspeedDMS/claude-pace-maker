#!/usr/bin/env python3
"""
Unit tests for Langfuse configuration command (AC1).

Tests:
- AC1: Langfuse Configuration Command
  - Credentials stored securely in config
  - Credentials NOT logged to any log files
  - Success message confirms configuration saved
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from pacemaker import user_commands


class TestLangfuseConfig(unittest.TestCase):
    """Test AC1: Langfuse Configuration Command"""

    def setUp(self):
        """Create temporary config file for testing."""
        self.test_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.test_dir, "config.json")

    def tearDown(self):
        """Clean up temporary files."""
        import shutil

        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_langfuse_config_stores_credentials(self):
        """AC1: Credentials stored securely in config.json"""
        # Arrange
        base_url = "https://cloud.langfuse.com"
        public_key = "pk-lf-test-123"
        secret_key = "sk-lf-test-secret-456"

        # Act
        result = user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand=f"config {base_url} {public_key} {secret_key}",
        )

        # Assert - Command succeeds
        self.assertTrue(result["success"], f"Command failed: {result.get('message')}")

        # Assert - Credentials stored in config
        with open(self.config_path) as f:
            config = json.load(f)

        self.assertEqual(config.get("langfuse_base_url"), base_url)
        self.assertEqual(config.get("langfuse_public_key"), public_key)
        self.assertEqual(config.get("langfuse_secret_key"), secret_key)

    def test_langfuse_config_no_credential_logging(self):
        """AC1: Credentials NOT logged to any log files"""
        # Arrange
        base_url = "https://cloud.langfuse.com"
        public_key = "pk-lf-test-123"
        secret_key = "sk-lf-test-secret-456"

        # Act - Mock logger to capture all log calls
        with patch("pacemaker.user_commands.log_warning") as mock_warning:
            user_commands.execute_command(
                command="langfuse",
                config_path=self.config_path,
                subcommand=f"config {base_url} {public_key} {secret_key}",
            )

            # Assert - No log calls contain secret_key
            for call in mock_warning.call_args_list:
                for arg in call[0]:
                    self.assertNotIn(secret_key, str(arg))

    def test_langfuse_config_success_message(self):
        """AC1: Success message confirms configuration saved"""
        # Arrange
        base_url = "https://cloud.langfuse.com"
        public_key = "pk-lf-test-123"
        secret_key = "sk-lf-test-secret-456"

        # Act
        result = user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand=f"config {base_url} {public_key} {secret_key}",
        )

        # Assert - Success message present
        self.assertTrue(result["success"])
        self.assertIn("success", result["message"].lower())
        self.assertIn("langfuse", result["message"].lower())
        self.assertIn("config", result["message"].lower())


if __name__ == "__main__":
    unittest.main()
