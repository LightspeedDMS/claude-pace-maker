#!/usr/bin/env python3
"""
Profile caching to shared disk file.

Story #38: Cache the /api/oauth/profile response to profile_cache.json so that
both pace-maker and claude-usage can read profile/tier information without
redundant API calls. Backoff-aware: skips API when in active 429 backoff.

Uses atomic write pattern (write to .tmp, rename) consistent with api_backoff.py.
"""

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


def cache_profile(
    profile_data: Dict[str, Any],
    cache_path: Optional[str] = None,
) -> None:
    """
    Write profile data to shared disk cache with timestamp.

    Uses atomic write pattern (write to .tmp, rename) to avoid partial writes.

    Args:
        profile_data: Profile dict from Claude OAuth API
        cache_path: Path to write cache file (default: ~/.claude-pace-maker/profile_cache.json)
    """
    if cache_path is None:
        cache_path = DEFAULT_PROFILE_CACHE_PATH

    try:
        path = Path(cache_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        content = {
            "profile": profile_data,
            "timestamp": time.time(),
        }

        tmp_path = path.with_suffix(f".json.tmp.{os.getpid()}")
        tmp_path.write_text(json.dumps(content))
        tmp_path.rename(path)

    except Exception as e:
        log_warning("profile_cache", "Failed to cache profile", e)


def load_cached_profile(
    cache_path: Optional[str] = None,
    max_age_seconds: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Load profile data from shared disk cache.

    Args:
        cache_path: Path to cache file (default: ~/.claude-pace-maker/profile_cache.json)
        max_age_seconds: If set, returns None if cache is older than this many seconds.
                         If None, returns cached data regardless of age.

    Returns:
        Profile dict (the inner profile, not the {profile, timestamp} wrapper),
        or None if cache is missing, corrupt, or expired.
    """
    if cache_path is None:
        cache_path = DEFAULT_PROFILE_CACHE_PATH

    try:
        path = Path(cache_path)
        if not path.exists():
            return None

        text = path.read_text().strip()
        if not text:
            return None

        content = json.loads(text)

        # Check TTL if max_age_seconds is specified
        if max_age_seconds is not None:
            cached_at = content.get("timestamp")
            if cached_at is None:
                return None
            age = time.time() - float(cached_at)
            if age > max_age_seconds:
                return None

        profile = content.get("profile")
        if profile is None:
            return None

        return profile

    except Exception as e:
        log_warning("profile_cache", "Failed to load cached profile", e)
        return None


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
            cache_profile(profile, cache_path)
            return profile

        # API returned None (error) - return cached profile as fallback
        return load_cached_profile(cache_path)

    except Exception as e:
        log_warning("profile_cache", "Failed to fetch and cache profile", e)
        return load_cached_profile(cache_path)
