#!/usr/bin/env python3
"""
Unit tests for langfuse provision CLI command.

Tests pace-maker langfuse provision command flow including
success, error handling, confirmation prompts, and force flag.
"""

import json
import os
from unittest import mock
import responses

from pacemaker import user_commands


class TestLangfuseProvisionCommand:
    """Test langfuse provision command execution."""

    @responses.activate
    def test_provision_success_no_existing_keys(self, tmp_path):
        """Should provision successfully when no keys exist."""
        config_path = tmp_path / "config.json"

        # Create initial config without Langfuse keys
        config_path.write_text(json.dumps({"enabled": True}))

        # Mock Lambda response
        responses.add(
            responses.POST,
            "http://localhost:3000/provision",
            json={
                "publicKey": "pk_test_123",
                "secretKey": "sk_test_456",
                "host": "https://langfuse.example.com",
            },
            status=200,
        )

        # Mock credential collection and Path.home
        with (
            mock.patch(
                "pacemaker.langfuse.provisioner.Path.home", return_value=tmp_path
            ),
            mock.patch.dict(os.environ, {"ANTHROPIC_ADMIN_API_KEY": "admin_key_456"}),
        ):
            # Create mock credentials
            claude_dir = tmp_path / ".claude"
            claude_dir.mkdir()
            creds = {
                "claudeAiOauth": {
                    "accessToken": "oauth_123",
                    "email": "user@example.com",
                }
            }
            (claude_dir / ".credentials.json").write_text(json.dumps(creds))

            # Execute command
            result = user_commands._langfuse_provision(str(config_path), force=False)

        assert result["success"] is True
        assert "provisioned" in result["message"].lower()
        assert "langfuse.example.com" in result["message"]

        # Verify config was updated
        with open(config_path) as f:
            config = json.load(f)
        assert config["langfuse_public_key"] == "pk_test_123"
        assert config["langfuse_secret_key"] == "sk_test_456"
        assert config["langfuse_enabled"] is True

    @responses.activate
    def test_provision_with_existing_keys_confirmed(self, tmp_path):
        """Should provision when user confirms overwrite."""
        config_path = tmp_path / "config.json"

        # Create config with existing keys
        existing_config = {
            "enabled": True,
            "langfuse_public_key": "pk_old",
            "langfuse_secret_key": "sk_old",
            "langfuse_base_url": "https://old.example.com",
        }
        config_path.write_text(json.dumps(existing_config))

        # Mock Lambda response
        responses.add(
            responses.POST,
            "http://localhost:3000/provision",
            json={
                "publicKey": "pk_new",
                "secretKey": "sk_new",
                "host": "https://new.example.com",
            },
            status=200,
        )

        # Mock credentials and user confirmation
        with (
            mock.patch(
                "pacemaker.langfuse.provisioner.Path.home", return_value=tmp_path
            ),
            mock.patch.dict(os.environ, {"ANTHROPIC_ADMIN_API_KEY": "admin_key"}),
            mock.patch("builtins.input", return_value="y"),
        ):
            # Create mock credentials
            claude_dir = tmp_path / ".claude"
            claude_dir.mkdir()
            creds = {
                "claudeAiOauth": {
                    "accessToken": "oauth_123",
                    "email": "user@example.com",
                }
            }
            (claude_dir / ".credentials.json").write_text(json.dumps(creds))

            # Execute command
            result = user_commands._langfuse_provision(str(config_path), force=False)

        assert result["success"] is True

        # Verify keys were updated
        with open(config_path) as f:
            config = json.load(f)
        assert config["langfuse_public_key"] == "pk_new"

    def test_provision_with_existing_keys_declined(self, tmp_path):
        """Should not provision when user declines overwrite."""
        config_path = tmp_path / "config.json"

        # Create config with existing keys
        existing_config = {
            "langfuse_public_key": "pk_old",
            "langfuse_secret_key": "sk_old",
            "langfuse_base_url": "https://old.example.com",
        }
        config_path.write_text(json.dumps(existing_config))

        # Mock user declining
        with mock.patch("builtins.input", return_value="n"):
            result = user_commands._langfuse_provision(str(config_path), force=False)

        assert result["success"] is True
        assert (
            "cancelled" in result["message"].lower()
            or "aborted" in result["message"].lower()
        )

        # Verify keys unchanged
        with open(config_path) as f:
            config = json.load(f)
        assert config["langfuse_public_key"] == "pk_old"

    @responses.activate
    def test_provision_with_force_flag(self, tmp_path):
        """Should provision without prompting when force=True."""
        config_path = tmp_path / "config.json"

        # Create config with existing keys
        existing_config = {
            "langfuse_public_key": "pk_old",
            "langfuse_secret_key": "sk_old",
            "langfuse_base_url": "https://old.example.com",
        }
        config_path.write_text(json.dumps(existing_config))

        # Mock Lambda response
        responses.add(
            responses.POST,
            "http://localhost:3000/provision",
            json={
                "publicKey": "pk_new",
                "secretKey": "sk_new",
                "host": "https://new.example.com",
            },
            status=200,
        )

        # Mock credentials (no input mock - should not prompt)
        with (
            mock.patch(
                "pacemaker.langfuse.provisioner.Path.home", return_value=tmp_path
            ),
            mock.patch.dict(os.environ, {"ANTHROPIC_ADMIN_API_KEY": "admin_key"}),
        ):
            # Create mock credentials
            claude_dir = tmp_path / ".claude"
            claude_dir.mkdir()
            creds = {
                "claudeAiOauth": {
                    "accessToken": "oauth_123",
                    "email": "user@example.com",
                }
            }
            (claude_dir / ".credentials.json").write_text(json.dumps(creds))

            # Execute with force=True
            result = user_commands._langfuse_provision(str(config_path), force=True)

        assert result["success"] is True

        # Verify keys were updated
        with open(config_path) as f:
            config = json.load(f)
        assert config["langfuse_public_key"] == "pk_new"

    def test_provision_missing_oauth_credentials(self, tmp_path):
        """Should return error when OAuth credentials missing."""
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({}))

        with mock.patch(
            "pacemaker.langfuse.provisioner.Path.home", return_value=tmp_path
        ):
            result = user_commands._langfuse_provision(str(config_path), force=False)

        assert result["success"] is False
        assert "OAuth credentials not found" in result["message"]
        assert "authenticate" in result["message"].lower()

    def test_provision_missing_admin_key(self, tmp_path):
        """Should return error when admin API key missing."""
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({}))

        # Create credentials but no admin key
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        creds = {
            "claudeAiOauth": {
                "accessToken": "oauth_123",
                "email": "user@example.com",
            }
        }
        (claude_dir / ".credentials.json").write_text(json.dumps(creds))

        with (
            mock.patch(
                "pacemaker.langfuse.provisioner.Path.home", return_value=tmp_path
            ),
            mock.patch.dict(os.environ, {}, clear=True),
        ):
            result = user_commands._langfuse_provision(str(config_path), force=False)

        assert result["success"] is False
        assert "admin API key" in result["message"]

    @responses.activate
    def test_provision_service_error(self, tmp_path):
        """Should return error when provisioning service fails."""
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({}))

        # Mock Lambda error response
        responses.add(
            responses.POST,
            "http://localhost:3000/provision",
            json={"error": "Invalid credentials"},
            status=401,
        )

        with (
            mock.patch(
                "pacemaker.langfuse.provisioner.Path.home", return_value=tmp_path
            ),
            mock.patch.dict(os.environ, {"ANTHROPIC_ADMIN_API_KEY": "admin_key"}),
        ):
            # Create mock credentials
            claude_dir = tmp_path / ".claude"
            claude_dir.mkdir()
            creds = {
                "claudeAiOauth": {
                    "accessToken": "oauth_123",
                    "email": "user@example.com",
                }
            }
            (claude_dir / ".credentials.json").write_text(json.dumps(creds))

            result = user_commands._langfuse_provision(str(config_path), force=False)

        assert result["success"] is False
        assert "401" in result["message"] or "error" in result["message"].lower()


class TestLangfuseOnAutoProvisioning:
    """Test auto-provisioning when langfuse on is called without keys."""

    @responses.activate
    def test_langfuse_on_with_existing_keys(self, tmp_path):
        """Should enable Langfuse without provisioning if keys exist."""
        config_path = tmp_path / "config.json"

        # Create config with existing keys
        existing_config = {
            "langfuse_enabled": False,
            "langfuse_public_key": "pk_existing",
            "langfuse_secret_key": "sk_existing",
            "langfuse_base_url": "https://existing.example.com",
        }
        config_path.write_text(json.dumps(existing_config))

        # Execute langfuse on
        result = user_commands._langfuse_on(str(config_path))

        assert result["success"] is True
        assert "enabled" in result["message"].lower()

        # Verify no provisioning occurred (keys unchanged)
        with open(config_path) as f:
            config = json.load(f)
        assert config["langfuse_public_key"] == "pk_existing"
        assert config["langfuse_enabled"] is True

    @responses.activate
    def test_langfuse_on_auto_provision_success(self, tmp_path):
        """Should auto-provision when no keys configured."""
        config_path = tmp_path / "config.json"

        # Create config WITHOUT Langfuse keys
        config_path.write_text(json.dumps({"enabled": True}))

        # Mock Lambda response
        responses.add(
            responses.POST,
            "http://localhost:3000/provision",
            json={
                "publicKey": "pk_auto",
                "secretKey": "sk_auto",
                "host": "https://auto.example.com",
            },
            status=200,
        )

        with (
            mock.patch(
                "pacemaker.langfuse.provisioner.Path.home", return_value=tmp_path
            ),
            mock.patch.dict(os.environ, {"ANTHROPIC_ADMIN_API_KEY": "admin_key"}),
        ):
            # Create mock credentials
            claude_dir = tmp_path / ".claude"
            claude_dir.mkdir()
            creds = {
                "claudeAiOauth": {
                    "accessToken": "oauth_123",
                    "email": "user@example.com",
                }
            }
            (claude_dir / ".credentials.json").write_text(json.dumps(creds))

            result = user_commands._langfuse_on(str(config_path))

        assert result["success"] is True
        assert (
            "auto-provisioned" in result["message"].lower()
            or "provisioned" in result["message"].lower()
        )

        # Verify keys were provisioned
        with open(config_path) as f:
            config = json.load(f)
        assert config["langfuse_public_key"] == "pk_auto"
        assert config["langfuse_enabled"] is True

    def test_langfuse_on_auto_provision_missing_credentials(self, tmp_path):
        """Should show helpful error when credentials missing for auto-provision."""
        config_path = tmp_path / "config.json"

        # Create config without keys
        config_path.write_text(json.dumps({}))

        with mock.patch(
            "pacemaker.langfuse.provisioner.Path.home", return_value=tmp_path
        ):
            result = user_commands._langfuse_on(str(config_path))

        assert result["success"] is False
        assert (
            "credentials" in result["message"].lower()
            or "configure" in result["message"].lower()
        )
        assert (
            "langfuse config" in result["message"]
            or "langfuse provision" in result["message"]
        )
