#!/usr/bin/env python3
"""
API client for Claude OAuth usage endpoint.

Handles:
- Loading access token from Claude credentials
- Fetching usage data from OAuth API
- Parsing API responses with NULL handling
- Graceful degradation on errors
"""

import json
import requests
from datetime import datetime
from typing import Optional, Dict
from pathlib import Path


API_URL = "https://api.anthropic.com/api/oauth/usage"
API_HEADERS = {
    "Content-Type": "application/json",
    "anthropic-beta": "oauth-2025-04-20",
    "User-Agent": "claude-pace-maker/1.0.0",
}


def load_access_token() -> Optional[str]:
    """
    Load OAuth access token from Claude Code credentials file.

    Returns:
        Access token string, or None if not available
    """
    try:
        creds_path = Path.home() / ".claude" / ".credentials.json"

        if not creds_path.exists():
            return None

        with open(creds_path) as f:
            data = json.load(f)

        oauth = data.get("claudeAiOauth", {})
        token = oauth.get("accessToken")

        return token

    except Exception:
        # Graceful degradation - don't crash on credential issues
        return None


def parse_usage_response(response_data: Dict) -> Optional[Dict]:
    """
    Parse usage API response into normalized format.

    Handles:
    - NULL reset times (inactive windows)
    - Missing window data
    - Date parsing

    Args:
        response_data: Raw API response JSON

    Returns:
        Parsed usage data dict, or None if parse fails
    """
    try:
        result = {}

        # Parse 5-hour window
        five_hour = response_data.get("five_hour", {})
        result["five_hour_util"] = five_hour.get("utilization", 0.0)

        resets_at_str = five_hour.get("resets_at")
        if resets_at_str:
            result["five_hour_resets_at"] = datetime.fromisoformat(
                resets_at_str.replace("+00:00", "")
            )
        else:
            result["five_hour_resets_at"] = None

        # Parse 7-day window (may be null)
        seven_day = response_data.get("seven_day")
        if seven_day is not None:
            result["seven_day_util"] = seven_day.get("utilization", 0.0)
            resets_at_str = seven_day.get("resets_at")
            if resets_at_str:
                result["seven_day_resets_at"] = datetime.fromisoformat(
                    resets_at_str.replace("+00:00", "")
                )
            else:
                result["seven_day_resets_at"] = None
        else:
            result["seven_day_util"] = 0.0
            result["seven_day_resets_at"] = None

        return result

    except Exception:
        # Graceful degradation - don't crash on parse errors
        return None


def fetch_usage(access_token: str, timeout: int = 10) -> Optional[Dict]:
    """
    Fetch usage data from Claude OAuth API.

    Implements graceful degradation:
    - Network errors -> None
    - API errors (401, 500, etc.) -> None
    - Parse errors -> None

    Args:
        access_token: OAuth access token
        timeout: Request timeout in seconds (default 10)

    Returns:
        Parsed usage data dict, or None on any error
    """
    try:
        headers = {**API_HEADERS, "Authorization": f"Bearer {access_token}"}

        response = requests.get(API_URL, headers=headers, timeout=timeout)

        # Only process successful responses
        if response.status_code != 200:
            return None

        # Parse JSON response
        data = response.json()

        # Parse into normalized format
        return parse_usage_response(data)

    except Exception:
        # Graceful degradation - all errors return None
        # This allows the system to continue without throttling
        return None
