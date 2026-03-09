"""Tests for adaptive throttle behavior when window has expired (time_remaining=0)."""

from datetime import datetime, timezone, timedelta
from pacemaker.adaptive_throttle import calculate_adaptive_delay


def test_expired_window_under_budget_returns_no_delay():
    """Bug fix: expired window with under-budget usage should NOT throttle."""
    now = datetime(2025, 3, 9, 12, 0, 0, tzinfo=timezone.utc)
    window_start = now - timedelta(hours=5, minutes=1)  # window already ended

    result = calculate_adaptive_delay(
        current_util=50.0,  # 50% used — well under budget
        window_start=window_start,
        current_time=now,
        time_remaining_hours=0.0,  # window expired
        window_hours=5.0,
        min_delay=5,
        max_delay=350,
        safety_buffer_pct=95.0,
        preload_hours=0.5,
    )

    assert (
        result["delay_seconds"] == 0
    ), f"Expected 0 delay for under-budget expired window, got {result['delay_seconds']}"
    assert result["strategy"] == "none"


def test_expired_window_over_budget_still_returns_max_delay():
    """Expired window with over-budget usage should still max-throttle."""
    now = datetime(2025, 3, 9, 12, 0, 0, tzinfo=timezone.utc)
    window_start = now - timedelta(hours=5, minutes=1)

    result = calculate_adaptive_delay(
        current_util=98.0,  # 98% used — over the 95% safe allowance
        window_start=window_start,
        current_time=now,
        time_remaining_hours=0.0,
        window_hours=5.0,
        min_delay=5,
        max_delay=350,
        safety_buffer_pct=95.0,
        preload_hours=0.5,
    )

    assert (
        result["delay_seconds"] == 350
    ), f"Expected max delay for over-budget expired window, got {result['delay_seconds']}"
    assert result["strategy"] == "emergency"
