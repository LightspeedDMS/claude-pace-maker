#!/usr/bin/env python3
"""
Pacing calculation algorithms for Credit-Aware Adaptive Throttling.

Implements:
- Logarithmic curve for 5-hour window target utilization
- Linear curve for 7-day window target utilization
- Most constrained window determination
- Adaptive delay calculation based on deviation
"""

import math
from datetime import datetime
from typing import Optional, Dict, Any


def calculate_logarithmic_target(time_percent: float) -> float:
    """
    Calculate target utilization for 5-hour window using logarithmic curve.

    Formula: target = 100 * ln(1 + (time_pct/100) * (e - 1))

    This creates a curve that starts slow and accelerates, allowing more
    aggressive use early in the window while conserving credits toward the end.

    Args:
        time_percent: Percentage of time elapsed in the window (0-100)

    Returns:
        Target utilization percentage (0-100)
    """
    if time_percent <= 0:
        return 0.0
    if time_percent >= 100:
        return 100.0

    # Convert to 0-1 range
    time_fraction = time_percent / 100.0

    # Apply logarithmic formula
    # target = 100 * ln(1 + time_fraction * (e - 1))
    target = 100.0 * math.log(1.0 + time_fraction * (math.e - 1.0))

    return target


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
        Percentage of time elapsed (0-100), or 0.0 if window is inactive (NULL)
    """
    if resets_at is None:
        # Inactive window (NULL reset time)
        return 0.0

    now = datetime.utcnow()

    # Calculate time remaining until reset
    time_remaining = (resets_at - now).total_seconds()

    # If reset time has passed, we're at 100% (or beyond)
    if time_remaining <= 0:
        return 100.0

    # Calculate total window duration in seconds
    window_seconds = window_hours * 3600

    # Calculate elapsed time
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


def calculate_delay(
    deviation_percent: float,
    base_delay: int = 5,
    threshold: int = 0,
    max_delay: int = 120,
) -> int:
    """
    Calculate adaptive delay based on deviation from target.

    Formula: delay = base_delay * (1 + 2 * excess_deviation)
    Where excess_deviation = max(0, deviation - threshold) as percentage points

    Zero-tolerance throttling: By default (threshold=0), throttling activates
    IMMEDIATELY when actual usage exceeds the target curve by any amount.

    Args:
        deviation_percent: How far actual is above target (%)
        base_delay: Base delay in seconds (default 5)
        threshold: Deviation threshold before throttling kicks in (default 0% - zero tolerance)
        max_delay: Maximum delay cap in seconds (default 120)

    Returns:
        Delay in seconds (0 if at or under threshold, capped at max_delay)
    """
    # No delay if under threshold
    if deviation_percent <= threshold:
        return 0

    # Calculate excess deviation (in percentage points, not fraction)
    excess = deviation_percent - threshold

    # Apply formula: delay = base * (1 + 2 * excess)
    # For small deviations this gives gradual increase
    # For large deviations it quickly hits the cap
    delay = base_delay * (1.0 + 2.0 * excess)

    # Enforce bounds: minimum 5s, maximum max_delay
    delay = int(max(5, min(delay, max_delay)))

    return delay
