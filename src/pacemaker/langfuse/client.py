#!/usr/bin/env python3
"""
Langfuse client for API connectivity and telemetry push.

Handles connection testing and data submission to Langfuse API.
"""

import requests
from typing import Dict, Any


def test_connection(
    base_url: str, public_key: str, secret_key: str, timeout: int = 5
) -> Dict[str, Any]:
    """
    Test connection to Langfuse API.

    Makes a lightweight health check request to validate API connectivity
    using HTTP Basic Auth with public_key and secret_key.

    Args:
        base_url: Langfuse API base URL (e.g., "https://cloud.langfuse.com")
        public_key: Langfuse public key
        secret_key: Langfuse secret key
        timeout: Request timeout in seconds (default: 5)

    Returns:
        Dict with:
        - connected: bool (True if connection successful)
        - message: str (status message)
    """
    try:
        # Use /api/public/health endpoint for quick connectivity test
        health_url = f"{base_url.rstrip('/')}/api/public/health"

        response = requests.get(
            health_url,
            timeout=timeout,
            auth=(public_key, secret_key),  # HTTP Basic Auth
        )

        if response.status_code == 200:
            return {"connected": True, "message": "Connection successful"}
        elif response.status_code == 401:
            return {
                "connected": False,
                "message": "Authentication failed - check credentials",
            }
        else:
            return {
                "connected": False,
                "message": f"API returned status {response.status_code}",
            }

    except requests.exceptions.Timeout:
        return {"connected": False, "message": f"Connection timed out after {timeout}s"}
    except requests.exceptions.ConnectionError:
        return {"connected": False, "message": "Unable to reach Langfuse API"}
    except Exception as e:
        return {"connected": False, "message": f"Connection failed: {str(e)}"}
