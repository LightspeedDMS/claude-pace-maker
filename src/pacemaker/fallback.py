#!/usr/bin/env python3
"""
Utility functions and constants for fallback mode resilient pacing.

This module provides shared primitives used by UsageModel (SQLite-based state
machine) and api_client.  The JSON-based state machine has been superseded by
UsageModel (Story #42); this file now contains only:

  - parse_api_datetime   — ISO 8601 datetime parser for API responses
  - FallbackState        — Enum of NORMAL / FALLBACK states
  - API_PRICING          — Per-1M-token pricing by model family
  - _DEFAULT_TOKEN_COSTS — Fallback coefficients used when config unavailable
  - _project_window      — Projects an expired resets_at timestamp forward
  - detect_tier          — Infers subscription tier from profile dict
"""

from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, Any


# API-equivalent pricing per 1M tokens (mirrors claude-usage-reporting constants)
# These are used to compute accumulated_cost from raw token counts.
API_PRICING: Dict[str, Dict[str, float]] = {
    "opus": {
        "input": 15.0,
        "output": 75.0,
        "cache_read": 1.50,
        "cache_create": 18.75,
    },
    "sonnet": {
        "input": 3.0,
        "output": 15.0,
        "cache_read": 0.30,
        "cache_create": 3.75,
    },
    "haiku": {
        "input": 0.80,
        "output": 4.0,
        "cache_read": 0.08,
        "cache_create": 1.00,
    },
}

# Default coefficients if token_costs.json is unavailable
_DEFAULT_TOKEN_COSTS: Dict[str, Dict[str, float]] = {
    "5x": {"coefficient_5h": 0.0075, "coefficient_7d": 0.0011},
    "20x": {"coefficient_5h": 0.001875, "coefficient_7d": 0.000275},
}


def parse_api_datetime(s: Optional[str]) -> Optional[datetime]:
    """
    Parse an ISO 8601 datetime string from the Claude API into a naive UTC datetime.

    Handles the common variants returned by the API:
    - "2026-03-06T15:00:00+00:00"  -> strips +00:00 suffix
    - "2026-03-06T15:00:00Z"       -> strips Z suffix
    - "2026-03-06T15:00:00"        -> plain ISO, used as-is

    Args:
        s: ISO datetime string, or None/empty

    Returns:
        Timezone-naive datetime object, or None if input is None/empty/invalid.
    """
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        # Normalise: strip +00:00 and Z suffixes to get a plain naive datetime
        normalised = s.strip().replace("+00:00", "").rstrip("Z")
        return datetime.fromisoformat(normalised)
    except (ValueError, TypeError, AttributeError):
        return None


class FallbackState(Enum):
    """State machine states for fallback mode."""

    NORMAL = "normal"
    FALLBACK = "fallback"


def _project_window(
    raw_resets_at: Optional[str],
    window_hours: float,
    now: datetime,
) -> tuple:
    """
    Parse a resets_at string and project it forward past *now* if the window
    has already expired.

    Args:
        raw_resets_at: ISO 8601 resets_at string (or None/empty)
        window_hours: Window length in hours (5 or 168)
        now: Current UTC datetime (naive)

    Returns:
        (projected_datetime_or_None, rolled: bool)
        - projected_datetime_or_None: The next future reset boundary, or None if unparseable
        - rolled: True if at least one window increment was applied
    """
    if not raw_resets_at:
        return None, False

    parsed = parse_api_datetime(raw_resets_at)
    if parsed is None:
        return None, False

    if parsed <= now:
        while parsed <= now:
            parsed += timedelta(hours=window_hours)
        return parsed, True

    return parsed, False


def detect_tier(profile: Optional[Dict[str, Any]]) -> str:
    """
    Detect subscription tier from profile data.

    Args:
        profile: Profile dict from Claude OAuth API (or None)

    Returns:
        "20x" if Claude Max, "5x" otherwise (default)
    """
    if not profile:
        return "5x"

    try:
        account = profile.get("account", {}) or {}
        has_max = account.get("has_claude_max", False)
        if has_max:
            return "20x"
    except Exception:
        pass

    return "5x"
