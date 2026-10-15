#!/usr/bin/env python3
"""
Profile caching to shared disk file.

Story #38: Cache the /api/oauth/profile response to profile_cache.json so that
both pace-maker and claude-usage can read profile/tier information without
redundant API calls. Backoff-aware: skips API when in active 429 backoff.

Uses atomic write pattern (write to .tmp, rename) consistent with api_backoff.py.
"""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional, Dict, Any

from .logger import log_warning, log_info


# Default path for profile cache file
DEFAULT_PROFILE_CACHE_PATH = str(
    Path.home() / ".claude-pace-maker" / "profile_cache.json"
)


# Length of the token fingerprint in hex chars (same name as in claude-usage).
TOKEN_FINGERPRINT_LENGTH = 16


def token_fingerprint(access_token: str) -> str:
    """First TOKEN_FINGERPRINT_LENGTH (16) hex chars of sha256(accessToken UTF-8).

    Credentials carry no account id, so this is the account-match key shared
    with the claude-usage monitor (issue #177). Never stores the token itself.
    """
    return hashlib.sha256(access_token.encode("utf-8")).hexdigest()[
        :TOKEN_FINGERPRINT_LENGTH
    ]


def cache_profile(
    profile_data: Dict[str, Any],
    cache_path: Optional[str] = None,
    access_token: Optional[str] = None,
) -> None:
    """
    Write profile data to shared disk cache with timestamp.

    Uses atomic write pattern (write to .tmp, rename) to avoid partial writes.

    Args:
        profile_data: Profile dict from Claude OAuth API
        cache_path: Path to write cache file (default: ~/.claude-pace-maker/profile_cache.json)
        access_token: Token the profile was fetched with; when given, its
            fingerprint is stored as top-level "token_fingerprint".
    """
    if cache_path is None:
        cache_path = DEFAULT_PROFILE_CACHE_PATH

    try:
        path = Path(cache_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        content: Dict[str, Any] = {
            "profile": profile_data,
            "timestamp": time.time(),
        }
        if access_token:
            content["token_fingerprint"] = token_fingerprint(access_token)

        tmp_path = path.with_suffix(f".json.tmp.{os.getpid()}")
        tmp_path.write_text(json.dumps(content))
        tmp_path.rename(path)

    except Exception as e:
        log_warning("profile_cache", "Failed to cache profile", e)


def load_cached_profile(
    cache_path: Optional[str] = None,
    max_age_seconds: Optional[float] = None,
    for_current_account: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Load profile data from shared disk cache.

    Args:
        cache_path: Path to cache file (default: ~/.claude-pace-maker/profile_cache.json)
        max_age_seconds: If set, returns None if cache is older than this many seconds.
                         If None, returns cached data regardless of age.
        for_current_account: If True, apply the reader rule shared with claude-usage
                         (#177): return None when the cache has a token_fingerprint
                         that differs from the current access token's. A missing key,
                         or no current token, accepts the cache. The current token
                         (a Keychain read on macOS) is loaded only when the cache has
                         a fingerprint.

    Returns:
        Profile dict (the inner profile, not the {profile, timestamp} wrapper),
        or None if cache is missing, corrupt, expired or for another account.
    """
    if cache_path is None:
        cache_path = DEFAULT_PROFILE_CACHE_PATH

    content = _read_cache_content(cache_path)
    if content is None:
        return None

    if max_age_seconds is not None and not _cache_is_fresh(content, max_age_seconds):
        return None

    cached_fingerprint = content.get("token_fingerprint")
    if for_current_account and cached_fingerprint is not None:
        from . import api_client

        current_token = api_client.load_access_token()
        if current_token and cached_fingerprint != token_fingerprint(current_token):
            log_info(
                "profile_cache",
                "Ignoring cached profile written for a different account",
            )
            return None

    return content.get("profile")


def fetch_and_cache_profile(
    access_token: str,
    cache_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Fetch user profile from API (if not in backoff) and cache to disk.

    Backoff-aware: if the API is in active 429 backoff, skips the API call
    and returns the cached profile instead (or None if no cache available).

    Args:
        access_token: OAuth access token for API authentication
        cache_path: Path to profile cache file (default: auto)

    Returns:
        Profile dict, or None if API unavailable and no cache
    """
    if cache_path is None:
        cache_path = DEFAULT_PROFILE_CACHE_PATH

    # Check if we are in backoff via UsageModel (SQLite)
    from .usage_model import UsageModel

    model = UsageModel()
    if model.is_in_backoff():
        remaining = model.get_backoff_remaining()
        log_info(
            "profile_cache",
            f"Skipping profile API call - in backoff ({remaining:.0f}s remaining)",
        )
        # Return cached profile if available
        return load_cached_profile(cache_path)

    # Not in backoff - fetch from API
    try:
        from . import api_client

        profile = api_client.fetch_user_profile(
            access_token=access_token,
        )

        if profile is not None:
            # Cache the fresh profile
            cache_profile(profile, cache_path, access_token=access_token)
            return profile

        # API returned None (error) - return cached profile as fallback
        return load_cached_profile(cache_path)

    except Exception as e:
        log_warning("profile_cache", "Failed to fetch and cache profile", e)
        return load_cached_profile(cache_path)


def _read_cache_content(cache_path: str) -> Optional[Dict[str, Any]]:
    """Parse the cache file once. None if missing, empty, corrupt or not an object."""
    try:
        path = Path(cache_path)
        if not path.exists():
            return None
        text = path.read_text().strip()
        if not text:
            return None
        content = json.loads(text)
        if not isinstance(content, dict):
            log_warning("profile_cache", "Profile cache is not a JSON object")
            return None
        return content
    except Exception as e:
        log_warning("profile_cache", "Failed to read profile cache", e)
        return None


# A cache timestamp this far in the future (or more) is not trusted as fresh.
MAX_CLOCK_SKEW_SECONDS = 300


def _cache_is_fresh(content: Dict[str, Any], max_age_seconds: float) -> bool:
    """True when the cache timestamp is valid, not older than max_age_seconds,
    and not in the future beyond MAX_CLOCK_SKEW_SECONDS."""
    try:
        age = time.time() - float(content.get("timestamp"))
    except (TypeError, ValueError) as e:
        log_warning("profile_cache", "Invalid timestamp in profile cache", e)
        return False
    if age < -MAX_CLOCK_SKEW_SECONDS:
        log_warning("profile_cache", "Profile cache timestamp is in the future")
        return False
    return age <= max_age_seconds


def refresh_profile_if_needed(
    access_token: str,
    max_age_seconds: float,
    cache_path: Optional[str] = None,
) -> bool:
    """
    Refetch the profile when the cache is missing, older than max_age_seconds,
    or was written for a different token (fingerprint differs or is missing).

    Returns True when a refetch was attempted, False when the cache was fresh.
    """
    if cache_path is None:
        cache_path = DEFAULT_PROFILE_CACHE_PATH

    content = _read_cache_content(cache_path)
    if (
        content is not None
        and content.get("profile") is not None
        and content.get("token_fingerprint") == token_fingerprint(access_token)
        and _cache_is_fresh(content, max_age_seconds)
    ):
        return False

    fetch_and_cache_profile(access_token, cache_path)
    return True
