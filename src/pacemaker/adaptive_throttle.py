#!/usr/bin/env python3
"""
Forward-looking adaptive throttling algorithm.

Implements intelligent delay calculation that:
- Projects into the future (not just reactive)
- Considers remaining time and budget
- Gradual, smooth correction (no knee-jerks)
- Pure function design (no external dependencies)
- Weekend-aware allowance calculation (budget accumulation only on weekdays)
"""

from datetime import datetime, timedelta
from typing import Optional


def is_weekend(dt: datetime) -> bool:
    """
    Check if datetime falls on weekend (Saturday=5, Sunday=6).

    Args:
        dt: Datetime to check

    Returns:
        True if Saturday or Sunday, False otherwise
    """
    return dt.weekday() in (5, 6)


def count_weekday_seconds(start_dt: datetime, end_dt: datetime) -> int:
    """
    Count only weekday (Mon-Fri) seconds between start and end.
    Exclude weekend (Sat-Sun) seconds entirely.

    Args:
        start_dt: Start datetime
        end_dt: End datetime

    Returns:
        Number of weekday seconds in range

    Examples:
        Monday 9am to Monday 5pm → 28,800 seconds (8 hours)
        Friday 11pm to Sunday 11pm → 3,600 seconds (only 1 hour from Fri 11pm-midnight)
        Saturday all day → 0 seconds (weekend)
    """
    if start_dt >= end_dt:
        return 0

    total_seconds = 0
    current = start_dt

    while current < end_dt:
        # Find the end of current day or end_dt, whichever comes first
        next_day = datetime(
            current.year, current.month, current.day, 23, 59, 59
        ) + timedelta(seconds=1)
        segment_end = min(next_day, end_dt)

        # Only count if it's a weekday
        if not is_weekend(current):
            segment_seconds = int((segment_end - current).total_seconds())
            total_seconds += segment_seconds

        current = segment_end

    return total_seconds


def calculate_continuous_allowance_pct(
    window_start: datetime,
    current_time: datetime,
    window_hours: float = 5.0,
    preload_hours: float = 0.0,
) -> float:
    """
    Calculate allowance percentage for continuous-time windows (5-hour).

    Unlike weekend-aware allowance, this accumulates linearly across ALL time
    (weekdays + weekends), with optional preload period.

    Args:
        window_start: When the window started
        current_time: Current point in time
        window_hours: Total window duration (default 5 hours)
        preload_hours: First N hours with preloaded allowance (default 0 = no preload)

    Returns:
        Allowance percentage (0-100) available at current_time

    Logic:
        total_seconds = window_hours × 3600
        seconds_elapsed = (current_time - window_start).total_seconds()

        If preload_hours > 0 and hours_elapsed <= preload_hours:
            allowance = (preload_hours / window_hours) × 100  # Flat preload
        Else:
            allowance = (seconds_elapsed / total_seconds) × 100  # Linear accrual

    Examples:
        5-hour window with 30-minute preload:
        T+0 min:   allowance = 10% (preload: 0.5h / 5h = 10%)
        T+15 min:  allowance = 10% (still in preload)
        T+30 min:  allowance = 10% (end of preload: 0.5h / 5h)
        T+60 min:  allowance = 20% (linear: 1h / 5h)
        T+150 min: allowance = 50% (linear: 2.5h / 5h)
        T+300 min: allowance = 100% (linear: 5h / 5h)
    """
    # Calculate total seconds in window
    total_seconds = window_hours * 3600.0

    # Calculate seconds elapsed from start to current time
    seconds_elapsed = (current_time - window_start).total_seconds()

    # Clamp to [0, total_seconds]
    seconds_elapsed = max(0.0, min(seconds_elapsed, total_seconds))

    # Calculate hours elapsed
    hours_elapsed = seconds_elapsed / 3600.0

    # Apply preload logic if configured
    if preload_hours > 0 and hours_elapsed <= preload_hours:
        # First N hours: use flat preload allowance
        preload_allowance = (preload_hours / window_hours) * 100.0
        return preload_allowance
    else:
        # After preload period (or no preload): normal linear accrual
        allowance_pct = (seconds_elapsed / total_seconds) * 100.0
        return allowance_pct


def calculate_allowance_pct(
    window_start: datetime,
    current_time: datetime,
    window_hours: float = 168.0,
    preload_hours: float = 0.0,
) -> float:
    """
    Calculate allowance percentage at current time.

    Allowance accumulates linearly during weekdays, freezes during weekends.
    Supports preload: first N weekday hours get a flat allowance percentage.

    Args:
        window_start: When the 7-day window started
        current_time: Current point in time
        window_hours: Total window duration (default 168 = 7 days)
        preload_hours: First N weekday hours with preloaded allowance (default 0 = no preload)

    Returns:
        Allowance percentage (0-100) available at current_time

    Logic:
        total_weekday_seconds = count weekday seconds in full window
        weekday_seconds_elapsed = count weekday seconds from start to now

        If preload_hours > 0 and weekday_hours_elapsed <= preload_hours:
            allowance = (preload_hours / total_weekday_hours) × 100  # Flat preload
        Else:
            allowance = (weekday_seconds_elapsed / total_weekday_seconds) × 100  # Normal accrual

    Examples:
        Window: Mon 00:00 to Sun 23:59, preload_hours=12
        Current: Monday 08:00 (8 weekday hours) → allowance = 10% (preload)
        Current: Monday 16:00 (16 weekday hours) → allowance ≈ 13.33% (normal accrual)
        Current: Friday 23:59:59 → allowance ≈ 100% (all weekday time elapsed)
        Current: Saturday 12:00 → allowance ≈ 100% (frozen at Friday end)
    """
    # Calculate total weekday seconds in the full window
    window_end = window_start + timedelta(hours=window_hours)
    total_weekday_seconds = count_weekday_seconds(window_start, window_end)

    if total_weekday_seconds == 0:
        return 100.0  # Edge case: no weekdays in window

    # Calculate weekday seconds elapsed from start to current time
    weekday_seconds_elapsed = count_weekday_seconds(window_start, current_time)

    # Calculate weekday hours elapsed
    weekday_hours_elapsed = weekday_seconds_elapsed / 3600.0

    # Apply preload logic if configured
    if preload_hours > 0 and weekday_hours_elapsed <= preload_hours:
        # First N weekday hours: use flat preload allowance
        total_weekday_hours = total_weekday_seconds / 3600.0
        preload_allowance = (preload_hours / total_weekday_hours) * 100.0
        return preload_allowance
    elif preload_hours == 0 and current_time <= window_start:
        # No preload and at/before window start: 0%
        return 0.0
    else:
        # After preload period (or no preload with time elapsed): normal linear accrual
        allowance_pct = (weekday_seconds_elapsed / total_weekday_seconds) * 100.0
        return allowance_pct


def calculate_adaptive_delay(
    current_util: float,
    target_util: Optional[float] = None,
    time_elapsed_pct: Optional[float] = None,
    time_remaining_hours: float = 0.0,
    window_hours: float = 168.0,
    min_delay: int = 5,
    max_delay: int = 350,
    window_start: Optional[datetime] = None,
    current_time: Optional[datetime] = None,
    safety_buffer_pct: float = 95.0,
    preload_hours: float = 0.0,
    weekly_limit_enabled: bool = True,
) -> dict:
    """
    Calculate adaptive delay to smoothly return to target curve.

    Two calling conventions supported:
    1. Legacy (backward compatible): target_util, time_elapsed_pct
    2. Weekend-aware: window_start, current_time (calculates allowance from weekday seconds)

    Uses forward-looking projection to determine optimal delay:
    1. Calculate current burn rate (% per hour)
    2. Project end utilization if we continue at current pace
    3. Calculate slowdown needed to hit target
    4. Convert to inter-tool delay

    Args:
        current_util: Current utilization % (e.g., 56%)
        target_util: Target utilization % (legacy mode, e.g., 32%)
        time_elapsed_pct: % of window elapsed (legacy mode, e.g., 31%)
        time_remaining_hours: Hours left in window (e.g., 3.45)
        window_hours: Total window duration (5 or 168)
        min_delay: Minimum delay in seconds (default 5)
        max_delay: Maximum delay in seconds (default 350)
        window_start: When window started (weekend-aware mode)
        current_time: Current time (weekend-aware mode)
        safety_buffer_pct: Target percentage of allowance (default 95% for 5% safety buffer)
        preload_hours: First N weekday hours with preloaded allowance (default 0 = no preload)
        weekly_limit_enabled: Enable weekly limit calculations (default True)

    Returns:
        {
            'delay_seconds': int,          # Calculated delay
            'strategy': str,               # 'none'|'minimal'|'gradual'|'aggressive'|'emergency'
            'projection': {
                'util_if_no_throttle': float,    # Where we'd end up
                'util_if_throttled': float,      # Where throttling gets us
                'credits_remaining_pct': float,   # Budget left
                'allowance': float,               # Raw allowance percentage
                'safe_allowance': float,          # Allowance with safety buffer applied
                'buffer_remaining': float         # Safe allowance - current utilization
            }
        }
    """
    # Determine which mode we're in and calculate allowance/target
    if window_start is not None and current_time is not None:
        # Check if this is a 7-day window and weekly limit is disabled
        if window_hours >= 168.0 and not weekly_limit_enabled:
            # Weekly limit disabled for 7-day window - return no throttling
            budget_remaining_pct = 100.0 - current_util
            return {
                "delay_seconds": 0,
                "strategy": "none",
                "reason": "weekly limit disabled",
                "projection": {
                    "util_if_no_throttle": current_util,
                    "util_if_throttled": current_util,
                    "credits_remaining_pct": budget_remaining_pct,
                    "allowance": 100.0,  # Unlimited when disabled
                    "safe_allowance": 100.0,
                    "buffer_remaining": 100.0 - current_util,
                },
            }

        # Calculate allowance based on window type:
        # - 5-hour window: continuous-time linear (24/7 accrual)
        # - 7-day window: weekend-aware (weekday-only accrual)
        if window_hours < 168.0:
            # Short window (5-hour): use continuous-time linear allowance
            allowance_pct = calculate_continuous_allowance_pct(
                window_start, current_time, window_hours, preload_hours
            )
        else:
            # Long window (7-day): use weekend-aware allowance
            allowance_pct = calculate_allowance_pct(
                window_start, current_time, window_hours, preload_hours
            )
        target_util = allowance_pct

        # Apply safety buffer: throttle if over X% of allowance
        safe_allowance = allowance_pct * (safety_buffer_pct / 100.0)

        # Check if over safe budget on weekend
        is_over_safe_budget = current_util > safe_allowance
        is_on_weekend = is_weekend(current_time)

        if is_over_safe_budget and is_on_weekend:
            # Over safe budget on weekend → max throttle (allowance frozen)
            budget_remaining_pct = 100.0 - current_util
            return {
                "delay_seconds": max_delay,
                "strategy": "emergency",
                "reason": "over safe budget on weekend - allowance frozen",
                "projection": {
                    "util_if_no_throttle": current_util,
                    "util_if_throttled": current_util,
                    "credits_remaining_pct": budget_remaining_pct,
                    "allowance": allowance_pct,
                    "safe_allowance": safe_allowance,
                    "buffer_remaining": safe_allowance - current_util,
                },
            }

        # Calculate time_elapsed_pct from weekday seconds for burn rate calculation
        window_end = window_start + timedelta(hours=window_hours)
        total_weekday_seconds = count_weekday_seconds(window_start, window_end)
        weekday_seconds_elapsed = count_weekday_seconds(window_start, current_time)

        if total_weekday_seconds > 0:
            time_elapsed_pct = (weekday_seconds_elapsed / total_weekday_seconds) * 100.0
        else:
            time_elapsed_pct = 0.0
    else:
        # Legacy mode: use provided target_util and time_elapsed_pct
        if target_util is None or time_elapsed_pct is None:
            raise ValueError(
                "Must provide either (window_start, current_time) or (target_util, time_elapsed_pct)"
            )

        # Apply safety buffer in legacy mode too
        allowance_pct = target_util
        safe_allowance = allowance_pct * (safety_buffer_pct / 100.0)

    # Phase 1: Calculate situation
    budget_remaining_pct = 100.0 - current_util
    overage_pct = (
        current_util - safe_allowance
    )  # Use safe_allowance instead of target_util

    # Calculate time elapsed (in hours)
    time_elapsed_hours = (time_elapsed_pct / 100.0) * window_hours

    # Handle edge case: zero time remaining
    if time_remaining_hours <= 0.0:
        # No time left - apply maximum delay
        return {
            "delay_seconds": max_delay,
            "strategy": "emergency",
            "projection": {
                "util_if_no_throttle": current_util,
                "util_if_throttled": current_util,
                "credits_remaining_pct": budget_remaining_pct,
                "allowance": allowance_pct,
                "safe_allowance": safe_allowance,
                "buffer_remaining": safe_allowance - current_util,
            },
        }

    # Phase 2: Project future without throttling
    # Current burn rate (% per hour)
    if time_elapsed_hours > 0:
        burn_rate = current_util / time_elapsed_hours
    else:
        burn_rate = 0.0

    # Projected end utilization if we continue at current pace
    projected_util_no_throttle = current_util + (burn_rate * time_remaining_hours)

    # Phase 3: Calculate required slowdown
    # If we're under safe allowance, no throttling needed
    if overage_pct <= 0:
        return {
            "delay_seconds": 0,
            "strategy": "none",
            "projection": {
                "util_if_no_throttle": projected_util_no_throttle,
                "util_if_throttled": projected_util_no_throttle,
                "credits_remaining_pct": budget_remaining_pct,
                "allowance": allowance_pct,
                "safe_allowance": safe_allowance,
                "buffer_remaining": safe_allowance - current_util,
            },
        }

    # We're over safe allowance. We want to slow down to use exactly 95% by window end.
    # Strategy: aim for safety_buffer_pct endpoint (default 95%) - leave 5% safety margin.
    conservative_target = safety_buffer_pct
    target_remaining_budget = conservative_target - current_util

    # If we're already at or above conservative target, just slow way down
    if target_remaining_budget <= 0:
        target_burn_rate = (
            budget_remaining_pct / time_remaining_hours * 0.5
        )  # Half pace
    else:
        target_burn_rate = target_remaining_budget / time_remaining_hours

    # Calculate slowdown ratio needed
    if burn_rate > 0:
        slowdown_ratio = target_burn_rate / burn_rate
    else:
        slowdown_ratio = 1.0

    # Convert slowdown ratio to delay using a formula that scales reasonably
    # Instead of trying to achieve exact slowdown via inter-tool delay,
    # use a graduated formula based on how much slowdown is needed
    if slowdown_ratio >= 0.95:
        # Very close to target pace or faster - minimal/no delay
        delay_seconds = min_delay if slowdown_ratio < 1.0 else 0
    elif slowdown_ratio <= 0:
        # Budget exhausted - apply maximum delay
        delay_seconds = max_delay
    else:
        # Need to slow down. Use formula: delay = base * (1/slowdown_ratio - 1) * multiplier
        # This gives reasonable delays without hitting max too quickly
        #
        # slowdown_ratio 0.5 -> delay = base * (2 - 1) * multiplier = base * multiplier
        # slowdown_ratio 0.25 -> delay = base * (4 - 1) * multiplier = base * 3 * multiplier
        #
        # Use multiplier that considers time pressure
        if time_remaining_hours < 1.0:
            # Very little time - use aggressive multiplier
            multiplier = 60
        elif time_remaining_hours < 3.0:
            # Some time - use moderate multiplier
            multiplier = 40
        else:
            # Plenty of time - use gentle multiplier
            multiplier = 20

        delay_seconds = int(min_delay * (1.0 / slowdown_ratio - 1.0) * multiplier)

    # Phase 4: Apply bounds
    delay_seconds = max(min_delay, min(delay_seconds, max_delay))

    # Calculate projected utilization WITH throttling
    # With throttling, our effective burn rate becomes target_burn_rate
    projected_util_throttled = current_util + (target_burn_rate * time_remaining_hours)

    # Phase 5: Determine strategy based on overage and delay
    if delay_seconds <= min_delay:
        # No delay needed
        strategy = "none"
        delay_seconds = 0
    elif delay_seconds >= max_delay:
        # Emergency - hitting max delay
        strategy = "emergency"
    elif overage_pct < 5:
        strategy = "minimal"
    elif overage_pct < 20:
        strategy = "gradual"
    else:
        strategy = "aggressive"

    return {
        "delay_seconds": int(delay_seconds),
        "strategy": strategy,
        "projection": {
            "util_if_no_throttle": projected_util_no_throttle,
            "util_if_throttled": projected_util_throttled,
            "credits_remaining_pct": budget_remaining_pct,
            "allowance": allowance_pct,
            "safe_allowance": safe_allowance,
            "buffer_remaining": safe_allowance - current_util,
        },
    }
