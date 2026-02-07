#!/usr/bin/env python3
"""
Unit tests for Langfuse provision-url command and auto-provisioning status display.

Tests:
- FEATURE 1: Auto-provisioning status display in 'langfuse status'
  - Shows provision URL from config
  - Shows provision URL from env var when config not set
  - Shows default URL when neither config nor env set
  - Shows CONFIGURED status when config set
  - Shows CONFIGURED status when env var set
  - Shows DEFAULT status when neither set

- FEATURE 2: 'langfuse provision-url' command
  - Shows current URL with no arguments
  - Sets URL in config with <url> argument
  - Removes from config with 'reset' argument
  - Updates provisioner to use config before env
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from pacemaker import user_commands


class TestLangfuseProvisionUrlStatus(unittest.TestCase):
    """Test FEATURE 1: Auto-provisioning status display"""

    def setUp(self):
        """Create temporary config file for testing."""
        self.test_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.test_dir, "config.json")

        # Pre-configure basic Langfuse credentials
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

    @patch("pacemaker.user_commands._langfuse_test_connection")
    def test_status_shows_provision_url_from_config(self, mock_test):
        """FEATURE 1: Status shows provision URL from config"""
        mock_test.return_value = {"connected": True, "message": "OK"}

        # Setup: Add provision URL to config
        config = {}
        with open(self.config_path) as f:
            config = json.load(f)
        config["langfuse_provision_endpoint"] = "https://custom.example.com/provision"
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Act
        result = user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand="status",
        )

        # Assert - Shows custom URL from config
        self.assertTrue(result["success"])
        self.assertIn("https://custom.example.com/provision", result["message"])
        self.assertIn("Auto-Provision URL:", result["message"])

    @patch("pacemaker.user_commands._langfuse_test_connection")
    def test_status_shows_provision_url_from_env(self, mock_test):
        """FEATURE 1: Status shows provision URL from env var when config not set"""
        mock_test.return_value = {"connected": True, "message": "OK"}

        # Setup: Set env var
        with patch.dict(
            os.environ,
            {"LANGFUSE_PROVISION_ENDPOINT": "https://env.example.com/provision"},
        ):
            # Act
            result = user_commands.execute_command(
                command="langfuse",
                config_path=self.config_path,
                subcommand="status",
            )

            # Assert - Shows URL from env var
            self.assertTrue(result["success"])
            self.assertIn("https://env.example.com/provision", result["message"])
            self.assertIn("Auto-Provision URL:", result["message"])

    @patch("pacemaker.user_commands._langfuse_test_connection")
    def test_status_shows_default_provision_url(self, mock_test):
        """FEATURE 1: Status shows default URL when neither config nor env set"""
        mock_test.return_value = {"connected": True, "message": "OK"}

        # Act - No config provision URL, no env var
        result = user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand="status",
        )

        # Assert - Shows default URL
        self.assertTrue(result["success"])
        self.assertIn("http://localhost:3000/provision", result["message"])
        self.assertIn("Auto-Provision URL:", result["message"])

    @patch("pacemaker.user_commands._langfuse_test_connection")
    def test_status_shows_configured_when_config_set(self, mock_test):
        """FEATURE 1: Status shows CONFIGURED when provision URL in config"""
        mock_test.return_value = {"connected": True, "message": "OK"}

        # Setup: Add provision URL to config
        config = {}
        with open(self.config_path) as f:
            config = json.load(f)
        config["langfuse_provision_endpoint"] = "https://custom.example.com/provision"
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Act
        result = user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand="status",
        )

        # Assert - Shows CONFIGURED status
        self.assertTrue(result["success"])
        self.assertIn("Auto-Provision: CONFIGURED", result["message"])

    @patch("pacemaker.user_commands._langfuse_test_connection")
    def test_status_shows_configured_when_env_set(self, mock_test):
        """FEATURE 1: Status shows CONFIGURED when env var set"""
        mock_test.return_value = {"connected": True, "message": "OK"}

        # Setup: Set env var, no config
        with patch.dict(
            os.environ,
            {"LANGFUSE_PROVISION_ENDPOINT": "https://env.example.com/provision"},
        ):
            # Act
            result = user_commands.execute_command(
                command="langfuse",
                config_path=self.config_path,
                subcommand="status",
            )

            # Assert - Shows CONFIGURED status
            self.assertTrue(result["success"])
            self.assertIn("Auto-Provision: CONFIGURED", result["message"])

    @patch("pacemaker.user_commands._langfuse_test_connection")
    def test_status_shows_default_when_neither_set(self, mock_test):
        """FEATURE 1: Status shows DEFAULT when neither config nor env set"""
        mock_test.return_value = {"connected": True, "message": "OK"}

        # Act - No config provision URL, no env var
        result = user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand="status",
        )

        # Assert - Shows DEFAULT status
        self.assertTrue(result["success"])
        self.assertIn("Auto-Provision: DEFAULT (not configured)", result["message"])


class TestLangfuseProvisionUrlCommand(unittest.TestCase):
    """Test FEATURE 2: 'langfuse provision-url' command"""

    def setUp(self):
        """Create temporary config file for testing."""
        self.test_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.test_dir, "config.json")

        # Pre-configure basic Langfuse credentials
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

    def test_provision_url_shows_current_url_no_args(self):
        """FEATURE 2: provision-url shows current URL with no arguments"""
        # Setup: Set custom URL in config
        config = {}
        with open(self.config_path) as f:
            config = json.load(f)
        config["langfuse_provision_endpoint"] = "https://custom.example.com/provision"
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Act
        result = user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand="provision-url",
        )

        # Assert - Shows current URL
        self.assertTrue(result["success"])
        self.assertIn("https://custom.example.com/provision", result["message"])

    def test_provision_url_shows_default_when_not_configured(self):
        """FEATURE 2: provision-url shows default when not configured"""
        # Act - No provision URL configured
        result = user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand="provision-url",
        )

        # Assert - Shows default URL
        self.assertTrue(result["success"])
        self.assertIn("http://localhost:3000/provision", result["message"])

    def test_provision_url_sets_url_in_config(self):
        """FEATURE 2: provision-url <url> saves URL to config"""
        # Act
        result = user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand="provision-url https://new.example.com/provision",
        )

        # Assert - Command successful
        self.assertTrue(result["success"])
        self.assertIn("configured", result["message"].lower())

        # Verify config was updated
        with open(self.config_path) as f:
            config = json.load(f)
        self.assertEqual(
            config["langfuse_provision_endpoint"], "https://new.example.com/provision"
        )

    def test_provision_url_reset_removes_from_config(self):
        """FEATURE 2: provision-url reset removes from config"""
        # Setup: Add provision URL to config
        config = {}
        with open(self.config_path) as f:
            config = json.load(f)
        config["langfuse_provision_endpoint"] = "https://custom.example.com/provision"
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Act
        result = user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand="provision-url reset",
        )

        # Assert - Command successful
        self.assertTrue(result["success"])
        self.assertIn("reset", result["message"].lower())

        # Verify config key was removed
        with open(self.config_path) as f:
            config = json.load(f)
        self.assertNotIn("langfuse_provision_endpoint", config)

    def test_provision_url_reset_when_not_configured(self):
        """FEATURE 2: provision-url reset succeeds even when not configured"""
        # Act - Reset when no provision URL configured
        result = user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand="provision-url reset",
        )

        # Assert - Command successful
        self.assertTrue(result["success"])

    def test_provision_url_validates_http_scheme(self):
        """FEATURE 2: provision-url validates URL has http:// or https:// scheme"""
        # Act - Try to set URL without scheme
        result = user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand="provision-url example.com/provision",
        )

        # Assert - Command fails with validation error
        self.assertFalse(result["success"])
        self.assertIn("Invalid URL", result["message"])
        self.assertIn("http://", result["message"])

        # Verify config was NOT updated
        with open(self.config_path) as f:
            config = json.load(f)
        self.assertNotIn("langfuse_provision_endpoint", config)

    def test_provision_url_accepts_https_scheme(self):
        """FEATURE 2: provision-url accepts valid https:// URL"""
        # Act
        result = user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand="provision-url https://valid.example.com/provision",
        )

        # Assert - Command successful
        self.assertTrue(result["success"])

        # Verify config was updated
        with open(self.config_path) as f:
            config = json.load(f)
        self.assertEqual(
            config["langfuse_provision_endpoint"], "https://valid.example.com/provision"
        )

    def test_provision_url_accepts_http_scheme(self):
        """FEATURE 2: provision-url accepts valid http:// URL (for localhost)"""
        # Act
        result = user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand="provision-url http://localhost:3000/provision",
        )

        # Assert - Command successful
        self.assertTrue(result["success"])

        # Verify config was updated
        with open(self.config_path) as f:
            config = json.load(f)
        self.assertEqual(
            config["langfuse_provision_endpoint"], "http://localhost:3000/provision"
        )


class TestLangfuseProvisionerConfigPriority(unittest.TestCase):
    """Test that LangfuseProvisioner checks config before env var"""

    def setUp(self):
        """Create temporary config file."""
        self.test_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.test_dir, "config.json")

    def tearDown(self):
        """Clean up temporary files."""
        import shutil

        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_provisioner_uses_explicit_endpoint(self):
        """Provisioner uses explicitly provided endpoint"""
        from pacemaker.langfuse.provisioner import LangfuseProvisioner

        provisioner = LangfuseProvisioner(
            endpoint="https://explicit.example.com/provision"
        )
        self.assertEqual(provisioner.endpoint, "https://explicit.example.com/provision")

    def test_provisioner_uses_env_when_no_explicit(self):
        """Provisioner uses env var when no explicit endpoint"""
        from pacemaker.langfuse.provisioner import LangfuseProvisioner

        with patch.dict(
            os.environ,
            {"LANGFUSE_PROVISION_ENDPOINT": "https://env.example.com/provision"},
        ):
            provisioner = LangfuseProvisioner()
            self.assertEqual(provisioner.endpoint, "https://env.example.com/provision")

    def test_provisioner_uses_default_when_neither(self):
        """Provisioner uses default when no explicit or env"""
        from pacemaker.langfuse.provisioner import LangfuseProvisioner

        # Ensure env var is not set
        with patch.dict(os.environ, {}, clear=True):
            provisioner = LangfuseProvisioner()
            self.assertEqual(provisioner.endpoint, "http://localhost:3000/provision")


class TestLangfuseProvisionCommandUsesConfig(unittest.TestCase):
    """Test that provision and on commands pass config endpoint to LangfuseProvisioner"""

    def setUp(self):
        """Create temporary config file."""
        self.test_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.test_dir, "config.json")

        # Pre-configure with provision endpoint
        config = {
            "langfuse_provision_endpoint": "https://custom-config.example.com/provision"
        }
        with open(self.config_path, "w") as f:
            json.dump(config, f)

    def tearDown(self):
        """Clean up temporary files."""
        import shutil

        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("pacemaker.langfuse.provisioner.LangfuseProvisioner")
    def test_provision_command_uses_config_endpoint(self, MockProvisioner):
        """CRITICAL: provision command should pass endpoint from config to LangfuseProvisioner"""
        # Setup mock
        mock_instance = MockProvisioner.return_value
        mock_instance.collect_credentials.return_value = (
            "token",
            "key",
            "test@example.com",
        )
        mock_instance.provision.return_value = {
            "host": "https://cloud.langfuse.com",
            "publicKey": "pk-123",
            "secretKey": "sk-456",
        }

        # Act - with verbose to skip confirmation
        with patch("builtins.input", return_value="y"):
            result = user_commands.execute_command(
                command="langfuse",
                config_path=self.config_path,
                subcommand="provision --verbose",
            )

        # Assert - LangfuseProvisioner was called with config endpoint
        MockProvisioner.assert_called_once_with(
            endpoint="https://custom-config.example.com/provision"
        )
        self.assertTrue(result["success"])

    @patch("pacemaker.langfuse.provisioner.LangfuseProvisioner")
    def test_provision_command_uses_default_when_no_config(self, MockProvisioner):
        """provision command should use None (triggers default) when no config endpoint"""
        # Setup empty config
        with open(self.config_path, "w") as f:
            json.dump({}, f)

        # Setup mock
        mock_instance = MockProvisioner.return_value
        mock_instance.collect_credentials.return_value = (
            "token",
            "key",
            "test@example.com",
        )
        mock_instance.provision.return_value = {
            "host": "https://cloud.langfuse.com",
            "publicKey": "pk-123",
            "secretKey": "sk-456",
        }

        # Act
        with patch("builtins.input", return_value="y"):
            result = user_commands.execute_command(
                command="langfuse",
                config_path=self.config_path,
                subcommand="provision --verbose",
            )

        # Assert - LangfuseProvisioner was called with None (will use env or default)
        MockProvisioner.assert_called_once_with(endpoint=None)
        self.assertTrue(result["success"])

    @patch("pacemaker.langfuse.provisioner.LangfuseProvisioner")
    def test_on_command_uses_config_endpoint(self, MockProvisioner):
        """CRITICAL: on command should pass endpoint from config to LangfuseProvisioner"""
        # Setup mock
        mock_instance = MockProvisioner.return_value
        mock_instance.collect_credentials.return_value = (
            "token",
            "key",
            "test@example.com",
        )
        mock_instance.provision.return_value = {
            "host": "https://cloud.langfuse.com",
            "publicKey": "pk-123",
            "secretKey": "sk-456",
        }

        # Act - on command with no existing keys (triggers auto-provision)
        result = user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand="on",
        )

        # Assert - LangfuseProvisioner was called with config endpoint
        MockProvisioner.assert_called_once_with(
            endpoint="https://custom-config.example.com/provision"
        )
        self.assertTrue(result["success"])

    @patch("pacemaker.langfuse.provisioner.LangfuseProvisioner")
    def test_on_command_uses_default_when_no_config(self, MockProvisioner):
        """on command should use None (triggers default) when no config endpoint"""
        # Setup empty config
        with open(self.config_path, "w") as f:
            json.dump({}, f)

        # Setup mock
        mock_instance = MockProvisioner.return_value
        mock_instance.collect_credentials.return_value = (
            "token",
            "key",
            "test@example.com",
        )
        mock_instance.provision.return_value = {
            "host": "https://cloud.langfuse.com",
            "publicKey": "pk-123",
            "secretKey": "sk-456",
        }

        # Act
        result = user_commands.execute_command(
            command="langfuse",
            config_path=self.config_path,
            subcommand="on",
        )

        # Assert - LangfuseProvisioner was called with None
        MockProvisioner.assert_called_once_with(endpoint=None)
        self.assertTrue(result["success"])


if __name__ == "__main__":
    unittest.main()
