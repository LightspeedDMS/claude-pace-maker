#!/usr/bin/env python3
"""
E2E tests for Langfuse provisioner against a real auto-provisioning server.

These tests require:
1. The langfuse-apikey-auto-provisioner server running at localhost:3000
2. Real Claude credentials in ~/.claude/.credentials.json
3. ANTHROPIC_ADMIN_API_KEY environment variable set

Run with: pytest tests/test_langfuse_provisioner_e2e.py -v -s

To start the provisioning server first:
    cd /home/jsbattig/Dev/langfuse-apikey-auto-provisioner
    LANGFUSE_HOST=http://192.168.68.42:3000 \
    LANGFUSE_PROJECT_NAME="Claude Code" \
    LANGFUSE_ADMIN_PUBLIC_KEY=<org-public-key> \
    LANGFUSE_ADMIN_SECRET_KEY=<org-secret-key> \
    LANGFUSE_INSTANCE_ADMIN_KEY=<instance-admin-key> \
    ORG_ADMIN_API_KEY=<anthropic-org-admin-key> \
    node dist/local-server.js
"""

import json
import os
import pytest
import requests
from pathlib import Path

from pacemaker.langfuse.provisioner import (
    LangfuseProvisioner,
    ProvisioningError,
)


# Skip all tests if provisioning server is not running
def server_is_running():
    """Check if the auto-provisioning server is running."""
    try:
        response = requests.get("http://localhost:3000/health", timeout=2)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False


def admin_api_key_is_set():
    """Check if ANTHROPIC_ADMIN_API_KEY environment variable is set."""
    return bool(os.environ.get("ANTHROPIC_ADMIN_API_KEY"))


def claude_credentials_exist():
    """Check if Claude credentials file exists."""
    creds_path = Path.home() / ".claude" / ".credentials.json"
    return creds_path.exists()


# Markers for conditional skipping
requires_server = pytest.mark.skipif(
    not server_is_running(),
    reason="Auto-provisioning server not running at localhost:3000",
)

requires_admin_key = pytest.mark.skipif(
    not admin_api_key_is_set(),
    reason="ANTHROPIC_ADMIN_API_KEY environment variable not set",
)

requires_claude_creds = pytest.mark.skipif(
    not claude_credentials_exist(),
    reason="Claude credentials file not found at ~/.claude/.credentials.json",
)


class TestProvisioningServerHealth:
    """Test that the provisioning server is accessible."""

    @requires_server
    def test_health_endpoint(self):
        """Should return healthy status from provisioning server."""
        response = requests.get("http://localhost:3000/health")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "langfuse-apikey-auto-provisioner"
        print(f"\nServer mode: {data.get('mode', 'unknown')}")


class TestCredentialCollection:
    """Test credential collection from real Claude credentials."""

    @requires_admin_key
    def test_admin_key_available(self):
        """Should have ANTHROPIC_ADMIN_API_KEY set."""
        admin_key = os.environ.get("ANTHROPIC_ADMIN_API_KEY")

        assert admin_key and isinstance(admin_key, str)
        assert len(admin_key) > 20  # Basic validation

        print(f"\nAdmin key available: {admin_key[:20]}...{admin_key[-4:]}")
        print(f"Admin key length: {len(admin_key)} chars")


class TestE2EProvisioning:
    """End-to-end provisioning tests against real server."""

    @requires_server
    @requires_admin_key
    def test_provision_api_keys_e2e(self, tmp_path):
        """
        E2E test: Provision Langfuse API keys from real server using adminApiKey.

        This test:
        1. Uses ANTHROPIC_ADMIN_API_KEY to authenticate
        2. Calls the real provisioning server directly
        3. Receives real Langfuse API keys
        4. Saves them to a test config file using LangfuseProvisioner
        """
        config_path = tmp_path / "config.json"
        admin_key = os.environ.get("ANTHROPIC_ADMIN_API_KEY")

        # Step 1: Call provisioning server directly with adminApiKey
        print("\n[Step 1] Calling provisioning server with adminApiKey...")
        response = requests.post(
            "http://localhost:3000/provision",
            json={"adminApiKey": admin_key},
            timeout=30,
        )

        print(f"  Response status: {response.status_code}")

        # Check for success
        assert response.status_code == 200, f"Provisioning failed: {response.text}"

        keys = response.json()

        # Verify response structure
        assert "publicKey" in keys, "Response missing publicKey"
        assert "secretKey" in keys, "Response missing secretKey"
        assert "host" in keys, "Response missing host"

        # Verify key formats
        assert keys["publicKey"].startswith(
            "pk-lf-"
        ), f"Invalid public key format: {keys['publicKey']}"
        assert keys["secretKey"].startswith(
            "sk-lf-"
        ), f"Invalid secret key format: {keys['secretKey'][:10]}..."
        assert keys["host"].startswith("http"), f"Invalid host format: {keys['host']}"

        print(f"  Public Key: {keys['publicKey']}")
        print(f"  Secret Key: {keys['secretKey'][:15]}...{keys['secretKey'][-4:]}")
        print(f"  Host: {keys['host']}")

        # Step 2: Save to config using LangfuseProvisioner
        print("[Step 2] Saving to config...")
        provisioner = LangfuseProvisioner()
        provisioner.save_to_config(keys, str(config_path))

        # Verify config file
        assert config_path.exists(), "Config file was not created"

        with open(config_path) as f:
            config = json.load(f)

        assert config["langfuse_public_key"] == keys["publicKey"]
        assert config["langfuse_secret_key"] == keys["secretKey"]
        assert config["langfuse_base_url"] == keys["host"]
        assert config["langfuse_enabled"] is True

        print("  Config saved successfully!")
        print("\n[SUCCESS] E2E provisioning test passed!")

    @requires_server
    def test_provision_with_invalid_credentials(self):
        """Should fail gracefully with invalid credentials."""
        provisioner = LangfuseProvisioner(endpoint="http://localhost:3000/provision")

        # Use obviously invalid credentials
        with pytest.raises(ProvisioningError) as exc_info:
            provisioner.provision(
                oauth_token="invalid_oauth_token",
                admin_api_key="invalid_admin_key",
                user_email="invalid@test.com",
            )

        # Should get an error response
        print(f"\nExpected error: {exc_info.value}")


class TestProvisioningIdempotency:
    """Test that provisioning can be called multiple times."""

    @requires_server
    @requires_admin_key
    def test_multiple_provisions_same_user(self, tmp_path):
        """
        Test that provisioning the same user multiple times works.

        Each call should create a new API key (keys are user-labeled, not deduplicated).
        """
        admin_key = os.environ.get("ANTHROPIC_ADMIN_API_KEY")

        # Provision twice
        print("\n[Provision 1]")
        response1 = requests.post(
            "http://localhost:3000/provision",
            json={"adminApiKey": admin_key},
            timeout=30,
        )
        assert response1.status_code == 200, f"Provision 1 failed: {response1.text}"
        keys1 = response1.json()
        print(f"  Got: {keys1['publicKey']}")

        print("[Provision 2]")
        response2 = requests.post(
            "http://localhost:3000/provision",
            json={"adminApiKey": admin_key},
            timeout=30,
        )
        assert response2.status_code == 200, f"Provision 2 failed: {response2.text}"
        keys2 = response2.json()
        print(f"  Got: {keys2['publicKey']}")

        # Keys should be different (new key each time)
        # Note: This tests current behavior - each provision creates a new key
        assert (
            keys1["publicKey"] != keys2["publicKey"]
        ), "Expected different keys for each provision call"

        print("\n[SUCCESS] Multiple provisions work correctly!")


class TestConfigIntegration:
    """Test integration with pace-maker config system."""

    @requires_server
    @requires_admin_key
    def test_provision_updates_existing_config(self, tmp_path):
        """
        Test that provisioning correctly updates an existing config file.
        """
        config_path = tmp_path / "config.json"
        admin_key = os.environ.get("ANTHROPIC_ADMIN_API_KEY")

        # Create existing config with some settings
        existing_config = {
            "enabled": True,
            "weekly_limit_enabled": False,
            "tempo_mode": "normal",
            "langfuse_enabled": False,  # Will be overwritten
            "langfuse_base_url": "https://old.example.com",  # Will be overwritten
        }
        with open(config_path, "w") as f:
            json.dump(existing_config, f)

        # Provision via direct HTTP call
        response = requests.post(
            "http://localhost:3000/provision",
            json={"adminApiKey": admin_key},
            timeout=30,
        )
        assert response.status_code == 200, f"Provisioning failed: {response.text}"
        keys = response.json()

        # Save using provisioner
        provisioner = LangfuseProvisioner()
        provisioner.save_to_config(keys, str(config_path))

        # Verify config
        with open(config_path) as f:
            config = json.load(f)

        # New Langfuse settings should be set
        assert config["langfuse_public_key"] == keys["publicKey"]
        assert config["langfuse_secret_key"] == keys["secretKey"]
        assert config["langfuse_base_url"] == keys["host"]
        assert config["langfuse_enabled"] is True

        # Other settings should be preserved
        assert config["enabled"] is True
        assert config["weekly_limit_enabled"] is False
        assert config["tempo_mode"] == "normal"

        print("\nExisting config settings preserved after provisioning!")


if __name__ == "__main__":
    # Run tests with verbose output
    pytest.main([__file__, "-v", "-s"])
