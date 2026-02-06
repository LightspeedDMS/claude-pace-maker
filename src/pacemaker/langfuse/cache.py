#!/usr/bin/env python3
"""
Simple in-memory cache for Langfuse stats API results.

Implements 60-second TTL caching to reduce API calls and enable graceful
fallback when Langfuse is unavailable (AC3, AC4 for Story #33).
"""

import time
from typing import Any, Optional, Dict, Union
from datetime import datetime, date


# In-memory cache storage
_cache: Dict[str, Dict[str, Any]] = {}


def get(key: str) -> Optional[Any]:
    """
    Retrieve cached data if not expired.

    Args:
        key: Cache key (e.g., "daily_stats_2026-02-04")

    Returns:
        Cached data if valid, None if expired or not found
    """
    if key not in _cache:
        return None

    entry = _cache[key]
    expires_at = entry.get("expires_at", 0)

    # Check if expired
    if time.time() >= expires_at:
        # Remove expired entry
        del _cache[key]
        return None

    return entry.get("data")


def set(key: str, data: Any, ttl: int = 60) -> None:
    """
    Store data in cache with TTL.

    Args:
        key: Cache key
        data: Data to cache
        ttl: Time-to-live in seconds (default: 60)
    """
    expires_at = time.time() + ttl
    cached_at = datetime.now()

    _cache[key] = {"data": data, "expires_at": expires_at, "cached_at": cached_at}


def get_with_metadata(key: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve cached data with metadata (cached_at timestamp).

    Used for graceful fallback to show age of cached data (AC4).

    Args:
        key: Cache key

    Returns:
        Dict with 'data' and 'cached_at' if found, None otherwise
    """
    if key not in _cache:
        return None

    entry = _cache[key]
    expires_at = entry.get("expires_at", 0)

    # Return even if expired (for fallback scenarios)
    # Caller can check cached_at to decide whether to use
    return {
        "data": entry.get("data"),
        "cached_at": entry.get("cached_at"),
        "expired": time.time() >= expires_at,
    }


def clear() -> None:
    """Clear all cached data (useful for testing)."""
    global _cache
    _cache = {}


def generate_key(
    prefix: str,
    from_date: Union[date, datetime],
    to_date: Optional[Union[date, datetime]] = None,
) -> str:
    """
    Generate cache key from date range.

    Args:
        prefix: Key prefix (e.g., "daily", "weekly")
        from_date: Start date (date or datetime)
        to_date: End date (optional, defaults to from_date)

    Returns:
        Cache key string
    """
    to_date = to_date or from_date
    from_str = (
        from_date.strftime("%Y-%m-%d")
        if hasattr(from_date, "strftime")
        else str(from_date)
    )
    to_str = (
        to_date.strftime("%Y-%m-%d") if hasattr(to_date, "strftime") else str(to_date)
    )

    if from_str == to_str:
        return f"{prefix}_{from_str}"
    else:
        return f"{prefix}_{from_str}_to_{to_str}"
