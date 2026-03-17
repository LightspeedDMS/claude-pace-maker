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

    def test_collect_credentials_oauth_and_api_key(self, tmp_path):
        """Should collect OAuth token, API key, and email when all are available."""
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
            mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-key-456"}),
            mock.patch(
                "pacemaker.langfuse.provisioner.Path.home", return_value=tmp_path
            ),
        ):
            provisioner = LangfuseProvisioner()
            oauth_token, api_key, email = provisioner.collect_credentials()

        assert oauth_token == "oauth_token_123"
        assert api_key == "sk-ant-key-456"
        assert email == "user@example.com"

    def test_collect_credentials_oauth_only(self, tmp_path):
        """Should succeed with only OAuth token (subscription users)."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        credentials = {
            "claudeAiOauth": {
                "accessToken": "oauth_token_123",
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
            oauth_token, api_key, email = provisioner.collect_credentials()

        assert oauth_token == "oauth_token_123"
        assert api_key is None
        assert email is None

    def test_collect_credentials_api_key_only_no_creds_file(self, tmp_path):
        """Should succeed with only ANTHROPIC_API_KEY when no credentials file exists (console/token users)."""
        with (
            mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-key-456"}, clear=True),
            mock.patch(
                "pacemaker.langfuse.provisioner.Path.home", return_value=tmp_path
            ),
        ):
            provisioner = LangfuseProvisioner()
            oauth_token, api_key, email = provisioner.collect_credentials()

        assert oauth_token is None
        assert api_key == "sk-ant-key-456"
        assert email is None

    def test_collect_credentials_admin_api_key_fallback(self, tmp_path):
        """Should fall back to ANTHROPIC_ADMIN_API_KEY when ANTHROPIC_API_KEY is not set."""
        with (
            mock.patch.dict(os.environ, {"ANTHROPIC_ADMIN_API_KEY": "admin_key_789"}, clear=True),
            mock.patch(
                "pacemaker.langfuse.provisioner.Path.home", return_value=tmp_path
            ),
        ):
            provisioner = LangfuseProvisioner()
            oauth_token, api_key, email = provisioner.collect_credentials()

        assert oauth_token is None
        assert api_key == "admin_key_789"
        assert email is None

    def test_collect_credentials_api_key_with_corrupted_creds_file(self, tmp_path):
        """Should fall through corrupted credentials file and use API key."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        creds_file = claude_dir / ".credentials.json"
        creds_file.write_text("{ invalid json }")

        with (
            mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-key-456"}, clear=True),
            mock.patch(
                "pacemaker.langfuse.provisioner.Path.home", return_value=tmp_path
            ),
        ):
            provisioner = LangfuseProvisioner()
            oauth_token, api_key, email = provisioner.collect_credentials()

        assert oauth_token is None
        assert api_key == "sk-ant-key-456"
        assert email is None

    def test_collect_credentials_no_oauth_token_in_file_with_api_key(self, tmp_path):
        """Should succeed via API key when credentials file exists but has no token."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        credentials = {"claudeAiOauth": {"email": "user@example.com"}}
        creds_file = claude_dir / ".credentials.json"
        creds_file.write_text(json.dumps(credentials))

        with (
            mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-key-456"}, clear=True),
            mock.patch(
                "pacemaker.langfuse.provisioner.Path.home", return_value=tmp_path
            ),
        ):
            provisioner = LangfuseProvisioner()
            oauth_token, api_key, email = provisioner.collect_credentials()

        assert oauth_token is None
        assert api_key == "sk-ant-key-456"
        assert email == "user@example.com"

    def test_collect_credentials_nothing_available(self, tmp_path):
        """Should raise error when neither OAuth token nor API key is available."""
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch(
                "pacemaker.langfuse.provisioner.Path.home", return_value=tmp_path
            ),
        ):
            provisioner = LangfuseProvisioner()
            with pytest.raises(
                CredentialsNotFoundError,
                match="No authentication credentials found",
            ):
                provisioner.collect_credentials()

    def test_collect_credentials_nothing_available_with_empty_creds(self, tmp_path):
        """Should raise error when credentials file exists but has no token, and no API key."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        credentials = {"claudeAiOauth": {}}
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
                match="No authentication credentials found",
            ):
                provisioner.collect_credentials()


class TestProvision:
    """Test Lambda provisioning service invocation."""

    @responses.activate
    def test_provision_success_all_fields(self):
        """Should successfully invoke Lambda and return keys with all fields."""
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
    def test_provision_api_key_only(self):
        """Should succeed with only API key — no OAuth token (console/token users)."""
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
        result = provisioner.provision(admin_api_key="sk-ant-key-456")

        assert result["publicKey"] == "pk_test_123"

        request_body = json.loads(responses.calls[0].request.body)
        assert request_body["adminApiKey"] == "sk-ant-key-456"
        assert "oauthToken" not in request_body
        assert "userEmail" not in request_body

    @responses.activate
    def test_provision_oauth_only(self):
        """Should succeed with only OAuth token — no admin key or email."""
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
        result = provisioner.provision(oauth_token="oauth_123")

        assert result["publicKey"] == "pk_test_123"

        # Verify only oauthToken sent in payload
        request_body = json.loads(responses.calls[0].request.body)
        assert request_body["oauthToken"] == "oauth_123"
        assert "adminApiKey" not in request_body
        assert "userEmail" not in request_body

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
    def test_full_provisioning_workflow_oauth_only(self, tmp_path):
        """Should complete full workflow with only OAuth token (typical user flow)."""
        # Setup mock credentials — no email, no admin key
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        credentials = {
            "claudeAiOauth": {
                "accessToken": "oauth_token_123",
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
            mock.patch.dict(os.environ, {}, clear=True),
        ):
            oauth_token, admin_key, email = provisioner.collect_credentials()
            assert admin_key is None
            assert email is None

            keys = provisioner.provision(oauth_token, admin_key, email)
            provisioner.save_to_config(keys, str(config_path))

        # Verify only oauthToken was sent
        request_body = json.loads(responses.calls[0].request.body)
        assert "oauthToken" in request_body
        assert "adminApiKey" not in request_body
        assert "userEmail" not in request_body

        # Verify final config
        with open(config_path) as f:
            config = json.load(f)

        assert config["langfuse_public_key"] == "pk_test_123"
        assert config["langfuse_secret_key"] == "sk_test_456"
        assert config["langfuse_base_url"] == "https://langfuse.example.com"
        assert config["langfuse_enabled"] is True

    @responses.activate
    def test_full_provisioning_workflow_api_key_only(self, tmp_path):
        """Should complete full workflow with only API key (console/token-based users)."""
        # No credentials file — user authenticates via ANTHROPIC_API_KEY
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

        config_path = tmp_path / "config.json"
        provisioner = LangfuseProvisioner()

        with (
            mock.patch(
                "pacemaker.langfuse.provisioner.Path.home", return_value=tmp_path
            ),
            mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-key-456"}, clear=True),
        ):
            oauth_token, api_key, email = provisioner.collect_credentials()
            assert oauth_token is None
            assert api_key == "sk-ant-key-456"

            keys = provisioner.provision(oauth_token, api_key, email)
            provisioner.save_to_config(keys, str(config_path))

        # Verify only adminApiKey was sent
        request_body = json.loads(responses.calls[0].request.body)
        assert "oauthToken" not in request_body
        assert request_body["adminApiKey"] == "sk-ant-key-456"
        assert "userEmail" not in request_body

        with open(config_path) as f:
            config = json.load(f)

        assert config["langfuse_public_key"] == "pk_test_123"
        assert config["langfuse_enabled"] is True

    @responses.activate
    def test_full_provisioning_workflow_with_admin_key(self, tmp_path):
        """Should complete full workflow with all credentials (agent flow)."""
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

        config_path = tmp_path / "config.json"
        provisioner = LangfuseProvisioner()

        with (
            mock.patch(
                "pacemaker.langfuse.provisioner.Path.home", return_value=tmp_path
            ),
            mock.patch.dict(os.environ, {"ANTHROPIC_ADMIN_API_KEY": "admin_key_456"}),
        ):
            oauth_token, admin_key, email = provisioner.collect_credentials()
            keys = provisioner.provision(oauth_token, admin_key, email)
            provisioner.save_to_config(keys, str(config_path))

        # Verify all fields sent
        request_body = json.loads(responses.calls[0].request.body)
        assert request_body["oauthToken"] == "oauth_token_123"
        assert request_body["adminApiKey"] == "admin_key_456"
        assert request_body["userEmail"] == "user@example.com"

        with open(config_path) as f:
            config = json.load(f)

        assert config["langfuse_public_key"] == "pk_test_123"
        assert config["langfuse_enabled"] is True
