#!/usr/bin/env python3
"""
Unit tests for Langfuse status command (AC3).

Tests:
- AC3: Connection Status and Validation
  - Displays enabled/disabled status
  - Displays base URL (without secret key)
  - Tests connection to Langfuse API
  - Completes within 5 seconds
"""

import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from pacemaker import user_commands


class TestLangfuseStatus(unittest.TestCase):
    """Test AC3: Connection Status and Validation"""

    def setUp(self):
        """Create temporary config file for testing."""
        self.test_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.test_dir, "config.json")

        # Pre-configure Langfuse credentials
        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://cloud.langfuse.com",
            "langfuse_public_key": "pk-lf-test-123",
            "langfuse_secret_key": "sk-lf-test-secret-456",
        }
        with open(self.config_path, "w") as f:
            json.dump(config, f)

    def tearDown(self):
        """Clean up temporary files."""
        import shutil

        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_langfuse_status_shows_enabled_state(self):
        """AC3: Status shows enabled/disabled state"""
        # Act - Test enabled state
        result = user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand="status",
        )

        # Assert - Shows enabled
        self.assertTrue(result["success"])
        self.assertIn("enabled", result["message"].lower())

        # Act - Test disabled state
        user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand="off",
        )

        result = user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand="status",
        )

        # Assert - Shows disabled
        self.assertTrue(result["success"])
        self.assertIn("disabled", result["message"].lower())

    def test_langfuse_status_shows_config(self):
        """AC3: Status displays base URL and public key (NOT secret key)"""
        # Act
        result = user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand="status",
        )

        # Assert - Shows base URL
        self.assertTrue(result["success"])
        self.assertIn("https://cloud.langfuse.com", result["message"])

        # Assert - Shows public key
        self.assertIn("pk-lf-test-123", result["message"])

        # Assert - Does NOT show secret key
        self.assertNotIn("sk-lf-test-secret-456", result["message"])

    def test_langfuse_status_tests_connection(self):
        """AC3: Status tests connection to Langfuse API"""
        # Mock the langfuse client connection test
        with patch("pacemaker.user_commands._langfuse_test_connection") as mock_test:
            mock_test.return_value = {
                "connected": True,
                "message": "Connection successful",
            }

            # Act
            result = user_commands.execute_command(
                command="langfuse",
                config_path=self.config_path,
                subcommand="status",
            )

            # Assert - Connection test was called
            mock_test.assert_called_once()

            # Assert - Connection status displayed
            self.assertTrue(result["success"])
            self.assertIn("connection", result["message"].lower())

    def test_langfuse_status_timeout_constraint(self):
        """AC3: Status completes within 5 seconds"""
        # Act - Measure execution time
        start_time = time.time()

        result = user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand="status",
        )

        elapsed_time = time.time() - start_time

        # Assert - Completes within 5 seconds
        self.assertTrue(result["success"])
        self.assertLess(
            elapsed_time, 5.0, f"Status command took {elapsed_time:.2f}s (>5s limit)"
        )


if __name__ == "__main__":
    unittest.main()
