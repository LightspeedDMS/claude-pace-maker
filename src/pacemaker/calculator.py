#!/usr/bin/env python3
"""
Pacing calculation algorithms for Credit-Aware Adaptive Throttling.

Implements:
- Linear curve for 7-day window target utilization
- Most constrained window determination
"""

from datetime import datetime, timezone
from typing import Optional, Dict, Any


def calculate_linear_target(time_percent: float) -> float:
    """
    Calculate target utilization for 7-day window using linear curve.

    Formula: target = time_pct

    This creates a straight line that maintains steady pacing throughout
    the window period.

    Args:
        time_percent: Percentage of time elapsed in the window (0-100)

    Returns:
        Target utilization percentage (0-100)
    """
    return max(0.0, min(100.0, time_percent))


def calculate_time_percent(
    resets_at: Optional[datetime], window_hours: float = 5.0
) -> float:
    """
    Calculate percentage of time elapsed in the current window.

    Args:
        resets_at: When the window resets (UTC datetime), or None if inactive
        window_hours: Length of the window in hours (default 5 for 5-hour window)

    Returns:
        Percentage of time elapsed (0-100), or 0.0 if window is inactive (NULL),
        or -1.0 if data is stale (resets_at more than 5 minutes in the past)
    """
    if resets_at is None:
        # Inactive window (NULL reset time)
        return 0.0

    now = datetime.now(timezone.utc)
    # If resets_at is naive (e.g. from parse_api_datetime), strip tzinfo from now
    if resets_at.tzinfo is None:
        now = now.replace(tzinfo=None)

    # Calculate time remaining until reset
    time_remaining = (resets_at - now).total_seconds()

    # If reset time has passed
    if time_remaining <= 0:
        # Check if data is stale (reset time more than 5 minutes in the past)
        # STALE_DATA_THRESHOLD_SECONDS = 300 (5 minutes)
        if time_remaining < -300:
            return -1.0  # Stale data sentinel
        # Within 5 minutes past - treat as 100% (window just ended)
        return 100.0

    # Calculate total window duration in seconds
    window_seconds = window_hours * 3600

    # BUGFIX #8: If reset time is more than window_hours in the future,
    # the window hasn't started yet - return 0%
    # This prevents negative elapsed_seconds calculations
    if time_remaining >= window_seconds:
        return 0.0

    # Normal case: calculate elapsed time within the window
    elapsed_seconds = window_seconds - time_remaining

    # Convert to percentage
    time_percent = (elapsed_seconds / window_seconds) * 100.0

    return max(0.0, min(100.0, time_percent))


def determine_most_constrained_window(
    five_hour_util: Optional[float],
    five_hour_target: float,
    seven_day_util: Optional[float],
    seven_day_target: float,
) -> Dict[str, Any]:
    """
    Determine which window is most constrained (highest deviation over target).

    Args:
        five_hour_util: Current 5-hour utilization (%), or None if inactive
        five_hour_target: Target 5-hour utilization (%)
        seven_day_util: Current 7-day utilization (%), or None if inactive
        seven_day_target: Target 7-day utilization (%)

    Returns:
        Dict with 'window' ('5-hour', '7-day', or None) and 'deviation' (%)
    """
    # Calculate deviations (only for active windows)
    five_hour_deviation = None
    if five_hour_util is not None:
        five_hour_deviation = five_hour_util - five_hour_target

    seven_day_deviation = None
    if seven_day_util is not None:
        seven_day_deviation = seven_day_util - seven_day_target

    # Determine most constrained
    if five_hour_deviation is None and seven_day_deviation is None:
        # Both windows inactive
        return {"window": None, "deviation": 0.0}
    elif five_hour_deviation is None:
        # Only 7-day active
        return {"window": "7-day", "deviation": seven_day_deviation}
    elif seven_day_deviation is None:
        # Only 5-hour active
        return {"window": "5-hour", "deviation": five_hour_deviation}
    else:
        # Both active - choose highest deviation
        if five_hour_deviation >= seven_day_deviation:
            return {"window": "5-hour", "deviation": five_hour_deviation}
        else:
            return {"window": "7-day", "deviation": seven_day_deviation}
