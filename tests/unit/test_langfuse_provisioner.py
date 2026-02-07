#!/usr/bin/env python3
"""
Unit tests for Langfuse provisioner module.

Tests the LangfuseProvisioner class including credential collection,
Lambda service invocation, and configuration storage.
"""

import json
import os
from unittest import mock
import pytest
import responses

from pacemaker.langfuse.provisioner import (
    LangfuseProvisioner,
    CredentialsNotFoundError,
    ProvisioningError,
)


class TestLangfuseProvisionerInit:
    """Test LangfuseProvisioner initialization."""

    def test_default_endpoint_from_env(self):
        """Should use endpoint from environment variable if set."""
        with mock.patch.dict(
            os.environ, {"LANGFUSE_PROVISION_ENDPOINT": "https://custom.example.com"}
        ):
            provisioner = LangfuseProvisioner()
            assert provisioner.endpoint == "https://custom.example.com"

    def test_default_endpoint_fallback(self):
        """Should use default endpoint if no env var set."""
        with mock.patch.dict(os.environ, {}, clear=True):
            provisioner = LangfuseProvisioner()
            assert provisioner.endpoint == "http://localhost:3000/provision"

    def test_custom_endpoint_override(self):
        """Should use custom endpoint when provided."""
        provisioner = LangfuseProvisioner(endpoint="https://override.example.com")
        assert provisioner.endpoint == "https://override.example.com"


class TestCollectCredentials:
    """Test credential collection from Claude credentials file."""

    def test_collect_credentials_success(self, tmp_path):
        """Should successfully collect OAuth token and admin API key."""
        # Create mock credentials file in the structure expected
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        credentials = {
            "claudeAiOauth": {
                "accessToken": "oauth_token_123",
                "email": "user@example.com",
            }
        }
        creds_file = claude_dir / ".credentials.json"
        creds_file.write_text(json.dumps(credentials))

        # Mock admin API key in environment and Path.home
        with (
            mock.patch.dict(os.environ, {"ANTHROPIC_ADMIN_API_KEY": "admin_key_456"}),
            mock.patch(
                "pacemaker.langfuse.provisioner.Path.home", return_value=tmp_path
            ),
        ):
            provisioner = LangfuseProvisioner()
            oauth_token, admin_key, email = provisioner.collect_credentials()

        assert oauth_token == "oauth_token_123"
        assert admin_key == "admin_key_456"
        assert email == "user@example.com"

    def test_collect_credentials_missing_file(self, tmp_path):
        """Should raise CredentialsNotFoundError if credentials file missing."""
        provisioner = LangfuseProvisioner()
        with mock.patch(
            "pacemaker.langfuse.provisioner.Path.home", return_value=tmp_path
        ):
            with pytest.raises(
                CredentialsNotFoundError, match="OAuth credentials not found"
            ):
                provisioner.collect_credentials()

    def test_collect_credentials_missing_oauth_token(self, tmp_path):
        """Should raise error if OAuth token missing from credentials."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        credentials = {"claudeAiOauth": {"email": "user@example.com"}}
        creds_file = claude_dir / ".credentials.json"
        creds_file.write_text(json.dumps(credentials))

        provisioner = LangfuseProvisioner()
        with mock.patch(
            "pacemaker.langfuse.provisioner.Path.home", return_value=tmp_path
        ):
            with pytest.raises(
                CredentialsNotFoundError, match="OAuth access token not found"
            ):
                provisioner.collect_credentials()

    def test_collect_credentials_missing_admin_key(self, tmp_path):
        """Should raise error if admin API key not in environment."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        credentials = {
            "claudeAiOauth": {
                "accessToken": "oauth_token_123",
                "email": "user@example.com",
            }
        }
        creds_file = claude_dir / ".credentials.json"
        creds_file.write_text(json.dumps(credentials))

        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch(
                "pacemaker.langfuse.provisioner.Path.home", return_value=tmp_path
            ),
        ):
            provisioner = LangfuseProvisioner()
            with pytest.raises(
                CredentialsNotFoundError,
                match="Anthropic admin API key not found",
            ):
                provisioner.collect_credentials()

    def test_collect_credentials_missing_email(self, tmp_path):
        """Should raise error if email missing from credentials."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        credentials = {"claudeAiOauth": {"accessToken": "oauth_token_123"}}
        creds_file = claude_dir / ".credentials.json"
        creds_file.write_text(json.dumps(credentials))

        with (
            mock.patch.dict(os.environ, {"ANTHROPIC_ADMIN_API_KEY": "admin_key_456"}),
            mock.patch(
                "pacemaker.langfuse.provisioner.Path.home", return_value=tmp_path
            ),
        ):
            provisioner = LangfuseProvisioner()
            with pytest.raises(CredentialsNotFoundError, match="User email not found"):
                provisioner.collect_credentials()

    def test_collect_credentials_corrupted_json(self, tmp_path):
        """Should raise error if credentials file is corrupted."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        creds_file = claude_dir / ".credentials.json"
        creds_file.write_text("{ invalid json }")

        provisioner = LangfuseProvisioner()
        with mock.patch(
            "pacemaker.langfuse.provisioner.Path.home", return_value=tmp_path
        ):
            with pytest.raises(CredentialsNotFoundError, match="corrupted"):
                provisioner.collect_credentials()


class TestProvision:
    """Test Lambda provisioning service invocation."""

    @responses.activate
    def test_provision_success(self):
        """Should successfully invoke Lambda and return keys."""
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

        provisioner = LangfuseProvisioner()
        result = provisioner.provision(
            oauth_token="oauth_123",
            admin_api_key="admin_456",
            user_email="user@example.com",
        )

        assert result["publicKey"] == "pk_test_123"
        assert result["secretKey"] == "sk_test_456"
        assert result["host"] == "https://langfuse.example.com"

        # Verify request was made with correct payload
        assert len(responses.calls) == 1
        request_body = json.loads(responses.calls[0].request.body)
        assert request_body["oauthToken"] == "oauth_123"
        assert request_body["adminApiKey"] == "admin_456"
        assert request_body["userEmail"] == "user@example.com"

    @responses.activate
    def test_provision_custom_endpoint(self):
        """Should use custom endpoint when provided."""
        responses.add(
            responses.POST,
            "https://custom.example.com/api/provision",
            json={
                "publicKey": "pk_test_123",
                "secretKey": "sk_test_456",
                "host": "https://langfuse.example.com",
            },
            status=200,
        )

        provisioner = LangfuseProvisioner(
            endpoint="https://custom.example.com/api/provision"
        )
        result = provisioner.provision(
            oauth_token="oauth_123",
            admin_api_key="admin_456",
            user_email="user@example.com",
        )

        assert result["publicKey"] == "pk_test_123"
        assert len(responses.calls) == 1
        assert (
            responses.calls[0].request.url == "https://custom.example.com/api/provision"
        )

    @responses.activate
    def test_provision_http_error(self):
        """Should raise ProvisioningError on HTTP error."""
        responses.add(
            responses.POST,
            "http://localhost:3000/provision",
            json={"error": "Invalid credentials"},
            status=401,
        )

        provisioner = LangfuseProvisioner()
        with pytest.raises(ProvisioningError, match="401"):
            provisioner.provision(
                oauth_token="oauth_123",
                admin_api_key="admin_456",
                user_email="user@example.com",
            )

    @responses.activate
    def test_provision_timeout_error(self):
        """Should raise ProvisioningError on timeout."""
        import requests

        responses.add(
            responses.POST,
            "http://localhost:3000/provision",
            body=requests.exceptions.Timeout("Connection timed out"),
        )

        provisioner = LangfuseProvisioner()
        with pytest.raises(ProvisioningError, match="[Tt]imeout"):
            provisioner.provision(
                oauth_token="oauth_123",
                admin_api_key="admin_456",
                user_email="user@example.com",
            )

    @responses.activate
    def test_provision_connection_error(self):
        """Should raise ProvisioningError on connection error."""
        import requests

        responses.add(
            responses.POST,
            "http://localhost:3000/provision",
            body=requests.exceptions.ConnectionError("Connection refused"),
        )

        provisioner = LangfuseProvisioner()
        with pytest.raises(ProvisioningError, match="Connection error"):
            provisioner.provision(
                oauth_token="oauth_123",
                admin_api_key="admin_456",
                user_email="user@example.com",
            )

    @responses.activate
    def test_provision_missing_keys_in_response(self):
        """Should raise ProvisioningError if keys missing from response."""
        responses.add(
            responses.POST,
            "http://localhost:3000/provision",
            json={"publicKey": "pk_test_123"},  # Missing secretKey and host
            status=200,
        )

        provisioner = LangfuseProvisioner()
        with pytest.raises(ProvisioningError, match="missing required fields"):
            provisioner.provision(
                oauth_token="oauth_123",
                admin_api_key="admin_456",
                user_email="user@example.com",
            )

    @responses.activate
    def test_provision_invalid_json_response(self):
        """Should raise ProvisioningError if response is not valid JSON."""
        responses.add(
            responses.POST,
            "http://localhost:3000/provision",
            body="Invalid JSON response",
            status=200,
        )

        provisioner = LangfuseProvisioner()
        with pytest.raises(ProvisioningError, match="Invalid response format"):
            provisioner.provision(
                oauth_token="oauth_123",
                admin_api_key="admin_456",
                user_email="user@example.com",
            )


class TestSaveToConfig:
    """Test saving provisioned keys to pace-maker config."""

    def test_save_to_config_new_file(self, tmp_path):
        """Should create new config file with provisioned keys."""
        config_path = tmp_path / "config.json"
        keys = {
            "publicKey": "pk_test_123",
            "secretKey": "sk_test_456",
            "host": "https://langfuse.example.com",
        }

        provisioner = LangfuseProvisioner()
        provisioner.save_to_config(keys, str(config_path))

        # Verify file was created
        assert config_path.exists()

        # Verify content
        with open(config_path) as f:
            config = json.load(f)

        assert config["langfuse_public_key"] == "pk_test_123"
        assert config["langfuse_secret_key"] == "sk_test_456"
        assert config["langfuse_base_url"] == "https://langfuse.example.com"
        assert config["langfuse_enabled"] is True

    def test_save_to_config_existing_file(self, tmp_path):
        """Should update existing config file preserving other settings."""
        config_path = tmp_path / "config.json"

        # Create existing config
        existing_config = {
            "enabled": True,
            "weekly_limit_enabled": False,
            "langfuse_base_url": "https://old.example.com",
        }
        with open(config_path, "w") as f:
            json.dump(existing_config, f)

        keys = {
            "publicKey": "pk_test_123",
            "secretKey": "sk_test_456",
            "host": "https://langfuse.example.com",
        }

        provisioner = LangfuseProvisioner()
        provisioner.save_to_config(keys, str(config_path))

        # Verify file was updated
        with open(config_path) as f:
            config = json.load(f)

        # New keys should be set
        assert config["langfuse_public_key"] == "pk_test_123"
        assert config["langfuse_secret_key"] == "sk_test_456"
        assert config["langfuse_base_url"] == "https://langfuse.example.com"
        assert config["langfuse_enabled"] is True

        # Old settings should be preserved
        assert config["enabled"] is True
        assert config["weekly_limit_enabled"] is False

    def test_save_to_config_creates_directory(self, tmp_path):
        """Should create parent directory if it doesn't exist."""
        config_path = tmp_path / "subdir" / "config.json"
        keys = {
            "publicKey": "pk_test_123",
            "secretKey": "sk_test_456",
            "host": "https://langfuse.example.com",
        }

        provisioner = LangfuseProvisioner()
        provisioner.save_to_config(keys, str(config_path))

        # Verify directory was created
        assert config_path.parent.exists()
        assert config_path.exists()

    def test_save_to_config_atomic_write(self, tmp_path):
        """Should use atomic write to prevent partial updates."""
        config_path = tmp_path / "config.json"

        # Create existing config
        with open(config_path, "w") as f:
            json.dump({"enabled": True}, f)

        keys = {
            "publicKey": "pk_test_123",
            "secretKey": "sk_test_456",
            "host": "https://langfuse.example.com",
        }

        provisioner = LangfuseProvisioner()

        # Mock atomic write failure to verify it's being used
        with mock.patch("os.replace", side_effect=OSError("Simulated failure")):
            with pytest.raises(OSError):
                provisioner.save_to_config(keys, str(config_path))

        # Original file should still exist and be intact
        with open(config_path) as f:
            config = json.load(f)
        assert config == {"enabled": True}


class TestEndToEndProvisioning:
    """End-to-end tests for complete provisioning flow."""

    @responses.activate
    def test_full_provisioning_workflow(self, tmp_path):
        """Should complete full provisioning workflow successfully."""
        # Setup mock credentials
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        credentials = {
            "claudeAiOauth": {
                "accessToken": "oauth_token_123",
                "email": "user@example.com",
            }
        }
        creds_file = claude_dir / ".credentials.json"
        creds_file.write_text(json.dumps(credentials))

        # Setup mock Lambda response
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

        # Execute full workflow
        config_path = tmp_path / "config.json"
        provisioner = LangfuseProvisioner()

        with (
            mock.patch(
                "pacemaker.langfuse.provisioner.Path.home", return_value=tmp_path
            ),
            mock.patch.dict(os.environ, {"ANTHROPIC_ADMIN_API_KEY": "admin_key_456"}),
        ):
            # Collect credentials
            oauth_token, admin_key, email = provisioner.collect_credentials()

            # Provision keys
            keys = provisioner.provision(oauth_token, admin_key, email)

            # Save to config
            provisioner.save_to_config(keys, str(config_path))

        # Verify final config
        with open(config_path) as f:
            config = json.load(f)

        assert config["langfuse_public_key"] == "pk_test_123"
        assert config["langfuse_secret_key"] == "sk_test_456"
        assert config["langfuse_base_url"] == "https://langfuse.example.com"
        assert config["langfuse_enabled"] is True
