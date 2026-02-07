#!/usr/bin/env python3
"""
Langfuse provisioning module.

Handles automatic provisioning of Langfuse API keys via Lambda service,
credential collection from Claude OAuth, and configuration storage.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple
import requests


class CredentialsNotFoundError(Exception):
    """Raised when required credentials are not found."""

    pass


class ProvisioningError(Exception):
    """Raised when provisioning service fails."""

    pass


class LangfuseProvisioner:
    """Client for invoking Lambda provisioning service and managing credentials."""

    def __init__(self, endpoint: Optional[str] = None):
        """
        Initialize provisioner with endpoint.

        Args:
            endpoint: Lambda provisioning service URL. If None, uses
                     LANGFUSE_PROVISION_ENDPOINT env var or default.

        Note:
            For production use, set LANGFUSE_PROVISION_ENDPOINT environment
            variable to point to your deployed provisioning service.
            Default (http://localhost:3000/provision) is for development only.
        """
        self.endpoint = endpoint or os.environ.get(
            "LANGFUSE_PROVISION_ENDPOINT", "http://localhost:3000/provision"
        )

    def collect_credentials(self) -> Tuple[str, str, str]:
        """
        Collect OAuth token and admin API key from Claude credentials.

        Returns:
            Tuple of (oauth_token, admin_api_key, user_email)

        Raises:
            CredentialsNotFoundError: If credentials are missing or invalid
        """
        # Read from ~/.claude/.credentials.json
        home = Path.home()
        creds_path = home / ".claude" / ".credentials.json"

        if not creds_path.exists():
            raise CredentialsNotFoundError(
                "OAuth credentials not found at ~/.claude/.credentials.json\n"
                "Please authenticate with Claude Code first."
            )

        try:
            with open(creds_path) as f:
                credentials = json.load(f)
        except json.JSONDecodeError as e:
            raise CredentialsNotFoundError(
                f"OAuth credentials file is corrupted: {e}\n"
                f"Please re-authenticate with Claude Code."
            )

        # Extract OAuth token
        oauth_data = credentials.get("claudeAiOauth", {})
        oauth_token = oauth_data.get("accessToken")
        if not oauth_token:
            raise CredentialsNotFoundError(
                "OAuth access token not found in credentials file.\n"
                "Please re-authenticate with Claude Code."
            )

        # Extract user email
        user_email = oauth_data.get("email")
        if not user_email:
            raise CredentialsNotFoundError(
                "User email not found in credentials file.\n"
                "Please re-authenticate with Claude Code."
            )

        # Get admin API key from environment
        admin_api_key = os.environ.get("ANTHROPIC_ADMIN_API_KEY")
        if not admin_api_key:
            raise CredentialsNotFoundError(
                "Anthropic admin API key not found in environment.\n"
                "Please set ANTHROPIC_ADMIN_API_KEY environment variable.\n"
                "Contact your Anthropic administrator to obtain an admin API key."
            )

        return oauth_token, admin_api_key, user_email

    def provision(self, oauth_token: str, admin_api_key: str, user_email: str) -> Dict:
        """
        Invoke Lambda provisioning service.

        Args:
            oauth_token: Claude OAuth access token
            admin_api_key: Anthropic admin API key
            user_email: User's email address

        Returns:
            Dictionary with keys: publicKey, secretKey, host

        Raises:
            ProvisioningError: On HTTP error or invalid response
        """
        payload = {
            "oauthToken": oauth_token,
            "adminApiKey": admin_api_key,
            "userEmail": user_email,
        }

        try:
            response = requests.post(
                self.endpoint,
                json=payload,
                timeout=30,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            raise ProvisioningError(
                f"HTTP error {response.status_code} from provisioning service: {e}\n"
                f"Response: {response.text}"
            )
        except requests.exceptions.Timeout as e:
            raise ProvisioningError(
                f"Timeout connecting to provisioning service: {e}\n"
                f"Endpoint: {self.endpoint}"
            )
        except requests.exceptions.ConnectionError as e:
            raise ProvisioningError(
                f"Connection error connecting to provisioning service: {e}\n"
                f"Endpoint: {self.endpoint}\n"
                f"Please verify the service is running and accessible."
            )
        except Exception as e:
            raise ProvisioningError(
                f"Connection error calling provisioning service: {e}"
            )

        # Parse response
        try:
            result = response.json()
        except json.JSONDecodeError as e:
            raise ProvisioningError(
                f"Invalid response format from provisioning service: {e}\n"
                f"Response body: {response.text}"
            )

        # Validate required fields
        required_fields = ["publicKey", "secretKey", "host"]
        missing = [f for f in required_fields if f not in result]
        if missing:
            raise ProvisioningError(
                f"Provisioning response missing required fields: {', '.join(missing)}\n"
                f"Response: {result}"
            )

        return result

    def save_to_config(self, keys: Dict, config_path: str) -> None:
        """
        Save provisioned keys to pace-maker config.

        Args:
            keys: Dictionary with publicKey, secretKey, host
            config_path: Path to config file

        Updates config with:
            - langfuse_public_key
            - langfuse_secret_key
            - langfuse_base_url
            - langfuse_enabled = True
        """
        # Ensure directory exists
        os.makedirs(os.path.dirname(config_path), exist_ok=True)

        # Load existing config or create empty
        if os.path.exists(config_path):
            with open(config_path) as f:
                config = json.load(f)
        else:
            config = {}

        # Update with new keys
        config["langfuse_public_key"] = keys["publicKey"]
        config["langfuse_secret_key"] = keys["secretKey"]
        config["langfuse_base_url"] = keys["host"]
        config["langfuse_enabled"] = True

        # Write atomically using temporary file
        dir_path = os.path.dirname(config_path)
        with tempfile.NamedTemporaryFile(
            mode="w", dir=dir_path, delete=False, suffix=".tmp"
        ) as tmp_file:
            json.dump(config, tmp_file, indent=2)
            tmp_path = tmp_file.name

        # Atomic move
        os.replace(tmp_path, config_path)
