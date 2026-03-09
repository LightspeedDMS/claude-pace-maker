"""
Tests for calculate_adaptive_delay() behaviour when the billing window has expired
(time_remaining_hours=0).

Bug: when time_remaining_hours <= 0, the function unconditionally returned max_delay
regardless of whether the user was actually over budget.  A user at 50% utilisation
with a 100% allowance should receive delay=0 even after the window closes.

Reference: src/pacemaker/adaptive_throttle.py lines 359-373
"""

from pacemaker.adaptive_throttle import calculate_adaptive_delay


class TestWindowExpiredUnderBudget:
    """When the window has expired AND the user is under budget, no delay is warranted."""

    def test_window_expired_under_budget_returns_no_delay(self):
        """
        Bug reproduction: time_remaining=0, current_util=50%, allowance=100%
        → overage_pct = 50 - 95 = -45  (well under safe allowance of 95%)
        Expected: delay_seconds=0, strategy='none'
        Actual (buggy): delay_seconds=350, strategy='emergency'
        """
        result = calculate_adaptive_delay(
            current_util=50.0,
            target_util=100.0,  # full allowance
            time_elapsed_pct=100.0,  # window fully elapsed
            time_remaining_hours=0.0,
            window_hours=5.0,
        )
        assert result["delay_seconds"] == 0, (
            f"Expected delay=0 when under budget with expired window, "
            f"got {result['delay_seconds']}. Strategy: {result['strategy']}"
        )
        assert (
            result["strategy"] == "none"
        ), f"Expected strategy='none', got '{result['strategy']}'"

    def test_window_expired_significantly_under_budget_returns_no_delay(self):
        """
        Variant: very low utilisation (10%) with generous allowance (80%).
        Window has just expired (time_remaining=0).
        """
        result = calculate_adaptive_delay(
            current_util=10.0,
            target_util=80.0,
            time_elapsed_pct=100.0,
            time_remaining_hours=0.0,
            window_hours=5.0,
        )
        assert (
            result["delay_seconds"] == 0
        ), f"Expected delay=0 for low-utilisation expired window, got {result['delay_seconds']}"
        assert result["strategy"] == "none"

    def test_window_expired_exactly_at_safe_allowance_returns_no_delay(self):
        """
        Edge: current_util equals safe_allowance exactly (overage_pct == 0).
        Should return delay=0 (not over budget).
        safe_allowance = target_util * (safety_buffer_pct / 100) = 80 * 0.95 = 76
        """
        result = calculate_adaptive_delay(
            current_util=76.0,
            target_util=80.0,
            time_elapsed_pct=100.0,
            time_remaining_hours=0.0,
            window_hours=5.0,
            safety_buffer_pct=95.0,
        )
        assert (
            result["delay_seconds"] == 0
        ), f"Expected delay=0 when exactly at safe allowance, got {result['delay_seconds']}"
        assert result["strategy"] == "none"


class TestWindowExpiredOverBudget:
    """When the window has expired AND the user is over budget, max delay is correct."""

    def test_window_expired_over_budget_returns_max_delay(self):
        """
        Correct behaviour: time_remaining=0, current_util=90%, target=50%
        → safe_allowance = 47.5, overage_pct = 42.5  (significantly over budget)
        Expected: delay_seconds=max_delay, strategy='emergency'
        """
        max_delay = 350
        result = calculate_adaptive_delay(
            current_util=90.0,
            target_util=50.0,
            time_elapsed_pct=100.0,
            time_remaining_hours=0.0,
            window_hours=5.0,
            max_delay=max_delay,
        )
        assert result["delay_seconds"] == max_delay, (
            f"Expected max_delay={max_delay} when over budget with expired window, "
            f"got {result['delay_seconds']}"
        )
        assert (
            result["strategy"] == "emergency"
        ), f"Expected strategy='emergency', got '{result['strategy']}'"

    def test_window_expired_slightly_over_safe_allowance_returns_max_delay(self):
        """
        Minimal overage: current_util just above safe_allowance.
        safe_allowance = 100 * 0.95 = 95.  current_util = 95.1 → overage = 0.1.
        Should still trigger emergency delay since window has expired.
        """
        result = calculate_adaptive_delay(
            current_util=95.1,
            target_util=100.0,
            time_elapsed_pct=100.0,
            time_remaining_hours=0.0,
            window_hours=5.0,
            safety_buffer_pct=95.0,
            max_delay=350,
        )
        assert result["delay_seconds"] == 350
        assert result["strategy"] == "emergency"


class TestWindowNotExpiredBehaviourUnchanged:
    """Ensure the fix does not regress normal (non-expired window) behaviour."""

    def test_normal_under_budget_no_delay(self):
        """With time remaining and under budget, delay should be 0."""
        result = calculate_adaptive_delay(
            current_util=30.0,
            target_util=100.0,
            time_elapsed_pct=50.0,
            time_remaining_hours=2.5,
            window_hours=5.0,
        )
        assert result["delay_seconds"] == 0
        assert result["strategy"] == "none"

    def test_normal_over_budget_with_time_remaining_applies_delay(self):
        """With time remaining and over budget, a positive delay should be returned."""
        result = calculate_adaptive_delay(
            current_util=90.0,
            target_util=50.0,
            time_elapsed_pct=50.0,
            time_remaining_hours=2.5,
            window_hours=5.0,
        )
        assert result["delay_seconds"] > 0
        assert result["strategy"] != "none"
