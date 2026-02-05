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

from .logger import log_warning


API_URL = "https://api.anthropic.com/api/oauth/usage"
PROFILE_API_URL = "https://api.anthropic.com/api/oauth/profile"
API_HEADERS = {
    "Content-Type": "application/json",
    "anthropic-beta": "oauth-2025-04-20",
    "User-Agent": "claude-pace-maker/1.0.0",
}

# Cache for user email to avoid repeated API calls
_cached_email: Optional[str] = None


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

    except Exception as e:
        log_warning("api_client", "Failed to load access token", e)
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

    except Exception as e:
        log_warning("api_client", "Failed to parse usage response", e)
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

    except Exception as e:
        log_warning("api_client", "Failed to fetch usage from API", e)
        return None


def fetch_user_profile(access_token: str, timeout: int = 3) -> Optional[Dict]:
    """
    Fetch user profile from Claude OAuth API.

    Implements graceful degradation:
    - Network errors -> None
    - API errors (401, 404, 500, etc.) -> None
    - Timeout errors -> None

    Args:
        access_token: OAuth access token
        timeout: Request timeout in seconds (default 3)

    Returns:
        Profile data dict containing account info, or None on any error
    """
    try:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "anthropic-beta": "oauth-2025-04-20",
        }

        response = requests.get(PROFILE_API_URL, headers=headers, timeout=timeout)

        # Only process successful responses
        if response.status_code != 200:
            return None

        return response.json()

    except Exception as e:
        log_warning("api_client", "Failed to fetch user profile from API", e)
        return None


def get_user_email() -> Optional[str]:
    """
    Get user email with caching.

    Fetches email from Claude OAuth profile API and caches the result
    to avoid repeated API calls. Uses existing credentials loading.

    Returns:
        User email string, or None if unavailable
    """
    global _cached_email

    # Return cached value if available
    if _cached_email:
        return _cached_email

    # Load access token
    token = load_access_token()
    if not token:
        return None

    # Fetch profile
    profile = fetch_user_profile(token)
    if not profile:
        return None

    # Extract email from profile
    try:
        email = profile.get("account", {}).get("email")
        if email:
            _cached_email = email
            return email
    except Exception as e:
        log_warning("api_client", "Failed to extract email from profile", e)

    return None


def clear_email_cache() -> None:
    """
    Clear the cached user email.

    Forces the next get_user_email() call to fetch fresh data from the API.
    Useful for testing or when credentials change.
    """
    global _cached_email
    _cached_email = None
