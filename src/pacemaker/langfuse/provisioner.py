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

    def collect_credentials(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Collect authentication credentials for provisioning.

        Tries two paths in order:
          1. OAuth token from ~/.claude/.credentials.json (subscription users)
          2. ANTHROPIC_API_KEY from environment (console/token-based users)

        At least one must be available. The provisioning server accepts either.

        Returns:
            Tuple of (oauth_token_or_none, api_key_or_none, user_email_or_none)

        Raises:
            CredentialsNotFoundError: If neither OAuth token nor API key is found
        """
        oauth_token = None
        user_email = None
        api_key = None

        # Try OAuth credentials first (subscription users)
        home = Path.home()
        creds_path = home / ".claude" / ".credentials.json"

        if creds_path.exists():
            try:
                with open(creds_path) as f:
                    credentials = json.load(f)
                oauth_data = credentials.get("claudeAiOauth", {})
                oauth_token = oauth_data.get("accessToken")
                user_email = oauth_data.get("email")
            except json.JSONDecodeError:
                pass  # Fall through to API key check

        # Check for API key in environment (console/token-based users, agents)
        api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get(
            "ANTHROPIC_ADMIN_API_KEY"
        )

        # Need at least one auth method
        if not oauth_token and not api_key:
            raise CredentialsNotFoundError(
                "No authentication credentials found.\n"
                "Either authenticate with Claude Code (creates ~/.claude/.credentials.json)\n"
                "or set ANTHROPIC_API_KEY environment variable."
            )

        return oauth_token, api_key, user_email

    def provision(
        self,
        oauth_token: Optional[str] = None,
        admin_api_key: Optional[str] = None,
        user_email: Optional[str] = None,
    ) -> Dict:
        """
        Invoke Lambda provisioning service.

        The server requires at least one of oauthToken or adminApiKey.
        userEmail is optional — the server extracts it from the auth token.

        Args:
            oauth_token: Claude OAuth access token (subscription users)
            admin_api_key: Anthropic API key (console/token-based users, agents)
            user_email: User's email address (optional, for cross-validation)

        Returns:
            Dictionary with keys: publicKey, secretKey, host

        Raises:
            ProvisioningError: On HTTP error or invalid response
        """
        payload: Dict = {}
        if oauth_token:
            payload["oauthToken"] = oauth_token
        if admin_api_key:
            payload["adminApiKey"] = admin_api_key
        if user_email:
            payload["userEmail"] = user_email

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
