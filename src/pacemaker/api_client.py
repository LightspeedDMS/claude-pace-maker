#!/usr/bin/env python3
"""
API client for Claude OAuth usage endpoint.

Handles:
- Loading access token from Claude credentials
- Fetching usage data from OAuth API
- Parsing API responses with NULL handling
- Graceful degradation on errors
- Exponential backoff on 429 rate limits (persistent across invocations)
"""

import json
import time
import requests
from typing import Optional, Dict
from pathlib import Path

from .logger import log_warning, log_info
from .fallback import parse_api_datetime


API_URL = "https://api.anthropic.com/api/oauth/usage"
PROFILE_API_URL = "https://api.anthropic.com/api/oauth/profile"
API_HEADERS = {
    "Content-Type": "application/json",
    "anthropic-beta": "oauth-2025-04-20",
    "User-Agent": "claude-pace-maker/1.0.0",
}

# Retry constants for 429 within a single call
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2  # seconds

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
        result["five_hour_resets_at"] = parse_api_datetime(resets_at_str)

        # Parse 7-day window (may be null)
        seven_day = response_data.get("seven_day")
        if seven_day is not None:
            result["seven_day_util"] = seven_day.get("utilization", 0.0)
            resets_at_str = seven_day.get("resets_at")
            result["seven_day_resets_at"] = parse_api_datetime(resets_at_str)
        else:
            result["seven_day_util"] = 0.0
            result["seven_day_resets_at"] = None

        return result

    except Exception as e:
        log_warning("api_client", "Failed to parse usage response", e)
        return None


def fetch_usage(
    access_token: str,
    timeout: int = 10,
    db_path: Optional[str] = None,
) -> Optional[Dict]:
    """
    Fetch usage data from Claude OAuth API.

    All state (backoff, cache, fallback) managed via UsageModel (SQLite).

    Args:
        access_token: OAuth access token
        timeout: Request timeout in seconds (default 10)
        db_path: Path to usage.db (default: auto)

    Returns:
        Parsed usage data dict, or None on any error
    """
    from .usage_model import UsageModel

    model = UsageModel(db_path=db_path)

    # Check persistent backoff state
    if model.is_in_backoff():
        remaining = model.get_backoff_remaining()
        log_info(
            "api_client",
            f"Skipping usage API call - in backoff ({remaining:.0f}s remaining)",
        )
        return None

    headers = {**API_HEADERS, "Authorization": f"Bearer {access_token}"}

    for attempt in range(_MAX_RETRIES):
        try:
            response = requests.get(API_URL, headers=headers, timeout=timeout)

            if response.status_code == 200:
                # Success - reset backoff and cache response in SQLite
                model.record_success()
                data = response.json()
                model.store_api_response(data)
                parsed = parse_usage_response(data)
                # Exit fallback mode if it was active (API recovered)
                if parsed is not None:
                    try:
                        model.exit_fallback(
                            real_5h=parsed.get("five_hour_util", 0.0),
                            real_7d=parsed.get("seven_day_util", 0.0),
                        )
                    except Exception as e:
                        log_warning("api_client", "Failed to exit fallback mode", e)
                return parsed

            elif response.status_code == 429:
                if attempt < _MAX_RETRIES - 1:
                    # Retry with short delay before giving up
                    delay = _RETRY_BASE_DELAY * (2**attempt)
                    time.sleep(delay)
                    continue
                # All retries exhausted - record persistent backoff and enter fallback
                model.record_429()
                try:
                    model.enter_fallback()
                except Exception as e:
                    log_warning("api_client", "Failed to enter fallback mode", e)
                return None

            else:
                # Non-429 error - don't touch backoff state or fallback state
                log_warning(
                    "api_client",
                    f"Usage API returned status {response.status_code}",
                )
                return None

        except Exception as e:
            log_warning("api_client", "Failed to fetch usage from API", e)
            return None

    return None


def fetch_user_profile(
    access_token: str,
    timeout: int = 3,
    db_path: Optional[str] = None,
) -> Optional[Dict]:
    """
    Fetch user profile from Claude OAuth API.

    Shares backoff state with fetch_usage via UsageModel (SQLite).

    Args:
        access_token: OAuth access token
        timeout: Request timeout in seconds (default 3)
        db_path: Path to usage.db (default: auto)

    Returns:
        Profile data dict containing account info, or None on any error
    """
    from .usage_model import UsageModel

    model = UsageModel(db_path=db_path)

    # Check persistent backoff state
    if model.is_in_backoff():
        remaining = model.get_backoff_remaining()
        log_info(
            "api_client",
            f"Skipping profile API call - in backoff ({remaining:.0f}s remaining)",
        )
        return None

    headers = {
        "Authorization": f"Bearer {access_token}",
        "anthropic-beta": "oauth-2025-04-20",
    }

    for attempt in range(_MAX_RETRIES):
        try:
            response = requests.get(PROFILE_API_URL, headers=headers, timeout=timeout)

            if response.status_code == 200:
                model.record_success()
                return response.json()

            elif response.status_code == 429:
                if attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_BASE_DELAY * (2**attempt)
                    time.sleep(delay)
                    continue
                model.record_429()
                return None

            else:
                return None

        except Exception as e:
            log_warning("api_client", "Failed to fetch user profile from API", e)
            return None

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
