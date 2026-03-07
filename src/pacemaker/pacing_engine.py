#!/usr/bin/env python3
"""
Pacing engine orchestration for Credit-Aware Adaptive Throttling.

Orchestrates:
- 60-second API polling throttle
- Usage data fetching and persistence
- Pacing calculations
- Hybrid delay strategy
"""

import os
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict
from . import calculator, database, api_client, adaptive_throttle
from .logger import log_warning, log_info

# Path to shared usage cache file read by claude-usage monitor
USAGE_CACHE_PATH = Path.home() / ".claude-pace-maker" / "usage_cache.json"

# Separate cache path for synthetic (fallback) values — prevents race condition
# where concurrent sessions corrupt each other's real API baselines.
SYNTHETIC_CACHE_PATH = Path.home() / ".claude-pace-maker" / "synthetic_cache.json"


def should_poll_api(last_poll_time: Optional[datetime], interval: int = 60) -> bool:
    """
    Determine if enough time has passed to poll the API again.

    Args:
        last_poll_time: When we last polled, or None for first poll
        interval: Polling interval in seconds (default 60)

    Returns:
        True if should poll now, False otherwise
    """
    if last_poll_time is None:
        return True  # First poll

    elapsed = (datetime.utcnow() - last_poll_time).total_seconds()
    return elapsed >= interval


def calculate_pacing_decision(
    five_hour_util: float,
    five_hour_resets_at: Optional[datetime],
    seven_day_util: float,
    seven_day_resets_at: Optional[datetime],
    threshold_percent: int = 0,
    base_delay: int = 5,
    max_delay: int = 350,
    use_adaptive: bool = True,
    safety_buffer_pct: float = 95.0,
    preload_hours: float = 0.0,
    weekly_limit_enabled: bool = True,
    five_hour_limit_enabled: bool = True,
) -> Dict:
    """
    Calculate pacing decision based on current usage.

    Args:
        five_hour_util: Current 5-hour utilization (%)
        five_hour_resets_at: When 5-hour window resets (or None)
        seven_day_util: Current 7-day utilization (%)
        seven_day_resets_at: When 7-day window resets (or None)
        threshold_percent: Deviation threshold (default 0% - zero tolerance)
        base_delay: Base delay in seconds (default 5)
        max_delay: Maximum delay in seconds (default 350 = 360s timeout - 10s safety)
        use_adaptive: Use adaptive throttling algorithm (default True)
        safety_buffer_pct: Safety buffer percentage (default 95.0)
        preload_hours: First N weekday hours with preloaded allowance (default 0 = no preload)
        weekly_limit_enabled: Enable weekly limit calculations (default True)

    Returns:
        Dict with pacing decision details
    """
    # Calculate time percentages for each window
    five_hour_time_pct = calculator.calculate_time_percent(
        five_hour_resets_at, window_hours=5
    )
    seven_day_time_pct = calculator.calculate_time_percent(
        seven_day_resets_at, window_hours=168
    )  # 7 days

    # Mark stale windows (sentinel value -1.0 means > 5 min past reset).
    # Stale windows are excluded from constraint calculation but the other
    # window can still be valid and drive throttling/display.
    five_hour_stale = five_hour_time_pct == -1.0
    seven_day_stale = seven_day_time_pct == -1.0

    # Only return early if BOTH windows are stale — nothing to calculate
    if five_hour_stale and seven_day_stale:
        return {
            "should_throttle": False,
            "delay_seconds": 0,
            "stale_data": True,
            "constrained_window": None,
            "deviation_percent": 0.0,
            "five_hour": {
                "utilization": five_hour_util,
                "target": 0,
                "time_elapsed_pct": -1.0,
            },
            "seven_day": {
                "utilization": seven_day_util,
                "target": 0,
                "time_elapsed_pct": -1.0,
            },
            "algorithm": "stale_data_detected",
            "error": "Both window data is stale (resets_at > 5min in past)",
        }

    # Nullify stale window's resets_at so it's excluded from constraint calc
    if five_hour_stale:
        five_hour_resets_at = None
    if seven_day_stale:
        seven_day_resets_at = None

    # Get current time once for all calculations
    now = datetime.utcnow()

    # FIX 3 (Issue #3): Calculate 5-hour target with LINEAR pacing (not logarithmic)
    # Use continuous-time linear allowance with 30-minute preload
    if use_adaptive and five_hour_resets_at:
        # Calculate 5-hour window with continuous-time linear pacing
        five_hour_window_hours = 5.0
        five_hour_preload_hours = 0.5  # 30 minutes = 10% of 5 hours
        five_hour_window_start = five_hour_resets_at - timedelta(
            hours=five_hour_window_hours
        )

        # Use CONTINUOUS-TIME linear allowance (not weekend-aware)
        # This gives linear pacing: 10% at start (preload), 50% at midpoint, 100% at end
        five_hour_target = adaptive_throttle.calculate_continuous_allowance_pct(
            window_start=five_hour_window_start,
            current_time=now,
            window_hours=five_hour_window_hours,
            preload_hours=five_hour_preload_hours,
        )
    else:
        # Legacy logarithmic target
        five_hour_target = calculator.calculate_logarithmic_target(five_hour_time_pct)

    # Calculate 7-day target: weekend-aware if adaptive, linear if legacy
    if use_adaptive and seven_day_resets_at:
        # Weekend-aware target for status display
        window_hours = 168.0
        window_start = seven_day_resets_at - timedelta(hours=window_hours)
        seven_day_target = adaptive_throttle.calculate_allowance_pct(
            window_start=window_start,
            current_time=now,
            window_hours=window_hours,
            preload_hours=preload_hours,
        )
    else:
        # Legacy linear target
        seven_day_target = calculator.calculate_linear_target(seven_day_time_pct)

    # Determine most constrained window
    # Only include 5-hour window if five_hour_limit_enabled is True
    # Only include 7-day window if weekly_limit_enabled is True
    constrained = calculator.determine_most_constrained_window(
        five_hour_util=(
            five_hour_util
            if (five_hour_resets_at and five_hour_limit_enabled)
            else None
        ),
        five_hour_target=five_hour_target,
        seven_day_util=(
            seven_day_util if (seven_day_resets_at and weekly_limit_enabled) else None
        ),
        seven_day_target=seven_day_target,
    )

    # Choose algorithm: adaptive (new) or legacy (old)
    if use_adaptive and constrained["window"] is not None:
        # Use new adaptive throttling algorithm with forward-looking projection
        # (now already calculated above)

        if constrained["window"] == "5-hour":
            window_hours = 5.0
            current_util = five_hour_util
            # Calculate time remaining
            if five_hour_resets_at:
                time_remaining = (five_hour_resets_at - now).total_seconds() / 3600.0
                time_remaining = max(0.0, time_remaining)
                # Calculate window start from reset time
                window_start = five_hour_resets_at - timedelta(hours=window_hours)
            else:
                time_remaining = 0.0
                window_start = now - timedelta(hours=window_hours)

            # FIX 2: 5-hour window now uses preload mode (30 minutes = 0.5 hours = 10% of 5 hours)
            # This gives users 10% working room at window start
            five_hour_preload = 0.5  # 30 minutes preload

            # Use adaptive mode with preload (NOT legacy mode)
            adaptive_result = adaptive_throttle.calculate_adaptive_delay(
                current_util=current_util,
                window_start=window_start,
                current_time=now,
                time_remaining_hours=time_remaining,
                window_hours=window_hours,
                min_delay=base_delay,
                max_delay=max_delay,
                safety_buffer_pct=safety_buffer_pct,
                preload_hours=five_hour_preload,  # 30-minute preload
                weekly_limit_enabled=True,  # Not actually a week, but enables adaptive mode
            )
        else:  # 7-day window
            window_hours = 168.0
            current_util = seven_day_util
            # Calculate time remaining
            if seven_day_resets_at:
                time_remaining = (seven_day_resets_at - now).total_seconds() / 3600.0
                time_remaining = max(0.0, time_remaining)
                # Calculate window start from reset time
                window_start = seven_day_resets_at - timedelta(hours=window_hours)
            else:
                time_remaining = 0.0
                window_start = now - timedelta(hours=window_hours)

            # 7-day window: use weekend-aware mode
            adaptive_result = adaptive_throttle.calculate_adaptive_delay(
                current_util=current_util,
                window_start=window_start,
                current_time=now,
                time_remaining_hours=time_remaining,
                window_hours=window_hours,
                min_delay=base_delay,
                max_delay=max_delay,
                safety_buffer_pct=safety_buffer_pct,
                preload_hours=preload_hours,
                weekly_limit_enabled=weekly_limit_enabled,
            )

        delay_seconds = adaptive_result["delay_seconds"]
        strategy_info = {
            "algorithm": "adaptive",
            "strategy": adaptive_result["strategy"],
            "projection": adaptive_result["projection"],
        }
    else:
        # Use legacy algorithm (simple deviation-based)
        delay_seconds = calculator.calculate_delay(
            deviation_percent=constrained["deviation"],
            base_delay=base_delay,
            threshold=threshold_percent,
            max_delay=max_delay,
        )
        strategy_info = {"algorithm": "legacy", "strategy": "legacy"}

    return {
        "should_throttle": delay_seconds > 0,
        "delay_seconds": delay_seconds,
        "constrained_window": constrained["window"],
        "deviation_percent": constrained["deviation"],
        "five_hour": {
            "utilization": five_hour_util,
            "target": five_hour_target,
            "time_elapsed_pct": five_hour_time_pct,
        },
        "seven_day": {
            "utilization": seven_day_util,
            "target": seven_day_target,
            "time_elapsed_pct": seven_day_time_pct,
        },
        **strategy_info,
    }


def determine_delay_strategy(delay_seconds: int) -> Dict:
    """
    Determine how to apply the delay (always uses direct sleep).

    Args:
        delay_seconds: Calculated delay in seconds

    Returns:
        Dict with strategy details
    """
    # Always use direct delays - prompt injection doesn't work in Claude Code
    return {"method": "direct", "delay_seconds": delay_seconds, "prompt": None}


def process_usage_update(usage_data: Dict, db_path: str, session_id: str) -> bool:
    """
    Process and store usage update in database.

    Args:
        usage_data: Usage data from API
        db_path: Path to database
        session_id: Current session identifier

    Returns:
        True if successful, False otherwise
    """
    return database.insert_usage_snapshot(
        db_path=db_path,
        timestamp=datetime.utcnow(),
        five_hour_util=usage_data["five_hour_util"],
        five_hour_resets_at=usage_data["five_hour_resets_at"],
        seven_day_util=usage_data["seven_day_util"],
        seven_day_resets_at=usage_data["seven_day_resets_at"],
        session_id=session_id,
    )


def run_pacing_check(
    db_path: str,
    session_id: str,
    last_poll_time: Optional[datetime] = None,
    poll_interval: int = 60,
    last_cleanup_time: Optional[datetime] = None,
    safety_buffer_pct: float = 95.0,
    preload_hours: float = 0.0,
    api_timeout_seconds: int = 10,
    cleanup_interval_hours: int = 24,
    retention_days: int = 60,
    weekly_limit_enabled: bool = True,
    five_hour_limit_enabled: bool = True,
    fallback_state_path: Optional[str] = None,
) -> Dict:
    """
    Run complete pacing check cycle.

    Orchestrates:
    1. Check if should poll API (60-second throttle)
    2. Fetch usage data if time to poll
    3. Store in database
    4. Calculate pacing decision
    5. Return decision with strategy
    6. Periodic cleanup of old database records

    Args:
        db_path: Path to database
        session_id: Current session identifier
        last_poll_time: When we last polled (or None)
        poll_interval: Polling interval in seconds (default 60)
        last_cleanup_time: When we last cleaned up old records (or None)
        safety_buffer_pct: Safety buffer percentage (default 95.0)
        preload_hours: First N weekday hours with preloaded allowance (default 0 = no preload)
        api_timeout_seconds: API request timeout in seconds (default 10)
        cleanup_interval_hours: Hours between cleanup runs (default 24)
        retention_days: Days to keep old snapshots (default 60)
        weekly_limit_enabled: Enable weekly limit calculations (default True)
        fallback_state_path: Path to fallback_state.json (default: auto from fallback module)

    Returns:
        Dict with pacing check results. Includes is_synthetic=True and stale_data=True
        when synthetic fallback values are used.
    """
    # Periodic cleanup: use configurable interval
    cleanup_interval_seconds = cleanup_interval_hours * 3600
    should_cleanup = (
        last_cleanup_time is None
        or (datetime.utcnow() - last_cleanup_time).total_seconds()
        >= cleanup_interval_seconds
    )

    if should_cleanup:
        deleted_count = database.cleanup_old_snapshots(
            db_path, retention_days=retention_days
        )
        if deleted_count > 0:
            log_info(
                "pacing",
                f"Cleaned up {deleted_count} old database records (>{retention_days} days)",
            )

    # Check if should poll
    should_poll = should_poll_api(last_poll_time, interval=poll_interval)

    if not should_poll:
        # Too soon to poll - retrieve cached decision
        cached_decision = database.get_last_pacing_decision(db_path, session_id)

        if cached_decision:
            # Return cached decision to maintain throttling between polls
            return {
                "polled": False,
                "decision": {
                    "should_throttle": cached_decision["should_throttle"],
                    "delay_seconds": cached_decision["delay_seconds"],
                },
                "cached": True,
            }
        else:
            # No cached decision - graceful degradation (no throttling)
            return {
                "polled": False,
                "decision": {"should_throttle": False, "delay_seconds": 0},
                "cached": False,
            }

    # Poll API
    access_token = api_client.load_access_token()
    if not access_token:
        # No token - graceful degradation (no throttling)
        return {
            "polled": False,
            "decision": {"should_throttle": False, "delay_seconds": 0},
            "error": "No access token available",
        }

    usage_data = api_client.fetch_usage(access_token, timeout=api_timeout_seconds)
    is_synthetic = False

    # Populate profile cache (for tier detection during fallback) if missing
    if usage_data:
        try:
            from .profile_cache import load_cached_profile, fetch_and_cache_profile

            if load_cached_profile() is None:
                fetch_and_cache_profile(access_token)
        except Exception:
            pass  # Non-critical — tier defaults to 5x if cache unavailable

    if not usage_data:
        # API failed - check if fallback mode is active for synthetic pacing
        try:
            from . import fallback as _fallback

            if _fallback.is_fallback_active(fallback_state_path):
                fb_state = _fallback.load_fallback_state(fallback_state_path)
                token_costs = _fallback.load_token_costs()
                # Use tier from fallback state (captured at enter_fallback from profile cache)
                tier = fb_state.get("tier", "5x")

                # Delegate all rollover logic to fallback module (P1).
                # calculate_synthetic_with_rollover() handles window expiration,
                # forward projection, rollover cost snapshots, and recalculation.
                # It mutates fb_state in-place when a rollover occurs.
                synthetic = _fallback.calculate_synthetic_with_rollover(
                    fb_state, tier, token_costs
                )
                if synthetic["state_changed"]:
                    _fallback.save_fallback_state(fb_state, fallback_state_path)

                five_resets = synthetic["five_resets"]
                seven_resets = synthetic["seven_resets"]

                usage_data = {
                    "five_hour_util": synthetic["synthetic_5h"],
                    "five_hour_resets_at": five_resets,
                    "seven_day_util": synthetic["synthetic_7d"],
                    "seven_day_resets_at": seven_resets,
                }
                is_synthetic = True
                # Write synthetic values to SYNTHETIC_CACHE_PATH (P2) so
                # claude-usage monitor can display estimated utilization.
                # Using a separate file avoids race conditions with the real
                # API cache (usage_cache.json) written by concurrent sessions.
                try:
                    import time as _time

                    _synthetic_cache = {
                        "timestamp": _time.time(),
                        "is_synthetic": True,
                        "response": {
                            "five_hour": {
                                "utilization": float(synthetic["synthetic_5h"]),
                                "resets_at": (
                                    (five_resets.isoformat() + "+00:00")
                                    if five_resets
                                    else ""
                                ),
                            },
                            "seven_day": {
                                "utilization": float(synthetic["synthetic_7d"]),
                                "resets_at": (
                                    (seven_resets.isoformat() + "+00:00")
                                    if seven_resets
                                    else ""
                                ),
                            },
                        },
                    }
                    _cache_path = SYNTHETIC_CACHE_PATH
                    _cache_path.parent.mkdir(parents=True, exist_ok=True)
                    _tmp = _cache_path.with_suffix(f".json.tmp.{os.getpid()}")
                    _tmp.write_text(json.dumps(_synthetic_cache))
                    _tmp.rename(_cache_path)
                except Exception as _e:
                    log_warning("pacing", "Failed to cache synthetic values", _e)
            else:
                # Not in fallback - graceful degradation (no throttling)
                return {
                    "polled": False,
                    "decision": {"should_throttle": False, "delay_seconds": 0},
                    "error": "API fetch failed",
                }
        except Exception as e:
            # Fallback check failed - log and graceful degradation
            log_warning("pacing", "Fallback check failed", e)
            return {
                "polled": False,
                "decision": {"should_throttle": False, "delay_seconds": 0},
                "error": f"API fetch failed, fallback unavailable: {e}",
            }

    # Store in database (skip for synthetic data — values are estimated, not real API data)
    if not is_synthetic:
        process_usage_update(usage_data, db_path, session_id)

    # Calculate pacing decision
    decision = calculate_pacing_decision(
        five_hour_util=usage_data["five_hour_util"],
        five_hour_resets_at=usage_data["five_hour_resets_at"],
        seven_day_util=usage_data["seven_day_util"],
        seven_day_resets_at=usage_data["seven_day_resets_at"],
        safety_buffer_pct=safety_buffer_pct,
        preload_hours=preload_hours,
        weekly_limit_enabled=weekly_limit_enabled,
        five_hour_limit_enabled=five_hour_limit_enabled,
    )

    # Determine strategy
    if decision["should_throttle"]:
        strategy = determine_delay_strategy(decision["delay_seconds"])
        decision["strategy"] = strategy

    # Store pacing decision in database for caching between polls
    database.insert_pacing_decision(
        db_path=db_path,
        timestamp=datetime.utcnow(),
        should_throttle=decision["should_throttle"],
        delay_seconds=decision["delay_seconds"],
        session_id=session_id,
    )

    result = {"polled": True, "decision": decision, "poll_time": datetime.utcnow()}

    # Mark synthetic results so callers know this is estimated data
    if is_synthetic:
        result["is_synthetic"] = True
        result["stale_data"] = True

    # Include cleanup timestamp if cleanup was performed
    if should_cleanup:
        result["cleanup_time"] = datetime.utcnow()

    return result
