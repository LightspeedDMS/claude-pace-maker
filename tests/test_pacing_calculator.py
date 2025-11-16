#!/usr/bin/env python3
"""
Unit tests for pacing calculation algorithms.

Tests the core algorithms for:
- Logarithmic curve calculation (5-hour window)
- Linear curve calculation (7-day window)
- Most constrained window determination
- Delay calculation based on deviation
"""

import unittest
from datetime import datetime, timedelta


class TestPacingCalculator(unittest.TestCase):
    """Test pacing calculation algorithms."""

    def test_logarithmic_target_at_start(self):
        """5-hour logarithmic target should be ~0% at start (0% elapsed)."""
        # FAILING TEST: Need to implement calculate_logarithmic_target
        from pacemaker.calculator import calculate_logarithmic_target

        time_percent = 0.0
        target = calculate_logarithmic_target(time_percent)

        # At start (0% time), target should be close to 0%
        self.assertLess(target, 1.0)

    def test_logarithmic_target_at_end(self):
        """5-hour logarithmic target should be 100% at end (100% elapsed)."""
        from pacemaker.calculator import calculate_logarithmic_target

        time_percent = 100.0
        target = calculate_logarithmic_target(time_percent)

        # At end (100% time), target should be 100%
        self.assertAlmostEqual(target, 100.0, places=1)

    def test_logarithmic_target_at_midpoint(self):
        """5-hour logarithmic target should be ~63% at 50% elapsed."""
        from pacemaker.calculator import calculate_logarithmic_target

        time_percent = 50.0
        target = calculate_logarithmic_target(time_percent)

        # At 50% time, logarithmic curve should be around 63%
        # Formula: 100 * ln(1 + time_pct/100 * (e - 1))
        # At 50%: 100 * ln(1 + 0.5 * 1.718) = 100 * ln(1.859) ≈ 62%
        self.assertGreater(target, 60.0)
        self.assertLess(target, 65.0)

    def test_linear_target_at_start(self):
        """7-day linear target should be 0% at start."""
        from pacemaker.calculator import calculate_linear_target

        time_percent = 0.0
        target = calculate_linear_target(time_percent)

        self.assertEqual(target, 0.0)

    def test_linear_target_at_end(self):
        """7-day linear target should be 100% at end."""
        from pacemaker.calculator import calculate_linear_target

        time_percent = 100.0
        target = calculate_linear_target(time_percent)

        self.assertEqual(target, 100.0)

    def test_linear_target_at_midpoint(self):
        """7-day linear target should be 50% at midpoint."""
        from pacemaker.calculator import calculate_linear_target

        time_percent = 50.0
        target = calculate_linear_target(time_percent)

        self.assertEqual(target, 50.0)

    def test_calculate_time_percent_with_null_reset(self):
        """Should return 0% when reset time is NULL (inactive window)."""
        from pacemaker.calculator import calculate_time_percent

        resets_at = None
        time_pct = calculate_time_percent(resets_at)

        self.assertEqual(time_pct, 0.0)

    def test_calculate_time_percent_at_start(self):
        """Should return ~100% when reset time is far in future."""
        from pacemaker.calculator import calculate_time_percent

        # Reset time 5 hours from now (just started)
        resets_at = datetime.utcnow() + timedelta(hours=5)
        time_pct = calculate_time_percent(resets_at)

        # Should be close to 0% elapsed (100% remaining)
        self.assertLess(time_pct, 5.0)

    def test_calculate_time_percent_near_end(self):
        """Should return ~100% when reset time is near."""
        from pacemaker.calculator import calculate_time_percent

        # Reset time 5 minutes from now (near end of 5-hour window)
        resets_at = datetime.utcnow() + timedelta(minutes=5)
        time_pct = calculate_time_percent(resets_at, window_hours=5)

        # Should be close to 100% elapsed
        self.assertGreater(time_pct, 95.0)

    def test_determine_most_constrained_window_five_hour(self):
        """Should return 5-hour when it's more constrained."""
        from pacemaker.calculator import determine_most_constrained_window

        # 5-hour: 70% used, target 50% => 20% over
        # 7-day: 60% used, target 50% => 10% over
        result = determine_most_constrained_window(
            five_hour_util=70.0,
            five_hour_target=50.0,
            seven_day_util=60.0,
            seven_day_target=50.0,
        )

        self.assertEqual(result["window"], "5-hour")
        self.assertAlmostEqual(result["deviation"], 20.0)

    def test_determine_most_constrained_window_seven_day(self):
        """Should return 7-day when it's more constrained."""
        from pacemaker.calculator import determine_most_constrained_window

        # 5-hour: 55% used, target 50% => 5% over
        # 7-day: 75% used, target 50% => 25% over
        result = determine_most_constrained_window(
            five_hour_util=55.0,
            five_hour_target=50.0,
            seven_day_util=75.0,
            seven_day_target=50.0,
        )

        self.assertEqual(result["window"], "7-day")
        self.assertAlmostEqual(result["deviation"], 25.0)

    def test_determine_most_constrained_with_null_windows(self):
        """Should handle NULL windows (return no constraint)."""
        from pacemaker.calculator import determine_most_constrained_window

        # Both windows NULL
        result = determine_most_constrained_window(
            five_hour_util=None,
            five_hour_target=0.0,
            seven_day_util=None,
            seven_day_target=0.0,
        )

        self.assertIsNone(result["window"])
        self.assertEqual(result["deviation"], 0.0)

    def test_calculate_delay_no_deviation(self):
        """Should return 0 delay when deviation is <= threshold."""
        from pacemaker.calculator import calculate_delay

        # Deviation at threshold (0%) - no delay
        delay = calculate_delay(deviation_percent=0.0, base_delay=5, threshold=0)

        self.assertEqual(delay, 0)

    def test_calculate_delay_small_deviation(self):
        """Should return proportional delay for small deviation."""
        from pacemaker.calculator import calculate_delay

        # Deviation 20% over threshold 0% => 20% over
        # delay = base * (1 + 2 * 20) = 5 * 41 = 205 (capped at 120)
        delay = calculate_delay(
            deviation_percent=20.0, base_delay=5, threshold=0, max_delay=120
        )

        self.assertEqual(delay, 120)

    def test_calculate_delay_large_deviation(self):
        """Should cap delay at max_delay."""
        from pacemaker.calculator import calculate_delay

        # Large deviation should hit max_delay cap
        delay = calculate_delay(deviation_percent=200.0, base_delay=5, max_delay=120)

        self.assertEqual(delay, 120)

    def test_calculate_delay_minimum_floor(self):
        """Should enforce minimum delay of 5 seconds when throttling."""
        from pacemaker.calculator import calculate_delay

        # Very small deviation (0.1% over), should be at least 5s (base_delay)
        delay = calculate_delay(deviation_percent=0.1, base_delay=5, threshold=0)

        self.assertGreaterEqual(delay, 5)

    # ====================================================================
    # ZERO-TOLERANCE THRESHOLD TESTS
    # These tests verify that throttling activates IMMEDIATELY when
    # actual usage exceeds target (threshold=0 by default)
    # ====================================================================

    def test_calculate_delay_zero_tolerance_tiny_positive_deviation(self):
        """Should throttle immediately at +0.01% deviation (zero tolerance)."""
        from pacemaker.calculator import calculate_delay

        # With default threshold=0, even tiny positive deviation should throttle
        delay = calculate_delay(deviation_percent=0.01, base_delay=5)

        # Should return base_delay (5 seconds)
        self.assertEqual(delay, 5)

    def test_calculate_delay_zero_tolerance_exactly_zero_deviation(self):
        """Should NOT throttle at exactly 0% deviation."""
        from pacemaker.calculator import calculate_delay

        # At exactly 0%, no throttling
        delay = calculate_delay(deviation_percent=0.0, base_delay=5)

        self.assertEqual(delay, 0)

    def test_calculate_delay_zero_tolerance_negative_deviation(self):
        """Should NOT throttle at negative deviation (under target)."""
        from pacemaker.calculator import calculate_delay

        # Under target - no throttling
        delay = calculate_delay(deviation_percent=-0.01, base_delay=5)

        self.assertEqual(delay, 0)

    def test_calculate_delay_zero_tolerance_one_percent_deviation(self):
        """Should throttle at +1% deviation with zero tolerance."""
        from pacemaker.calculator import calculate_delay

        # 1% over target
        # Formula: delay = base * (1 + 2 * excess)
        # excess = 1.0 - 0 = 1.0
        # delay = 5 * (1 + 2 * 1.0) = 5 * 3 = 15
        delay = calculate_delay(deviation_percent=1.0, base_delay=5)

        self.assertEqual(delay, 15)

    def test_calculate_delay_zero_tolerance_five_percent_deviation(self):
        """Should throttle at +5% deviation with zero tolerance."""
        from pacemaker.calculator import calculate_delay

        # 5% over target
        # Formula: delay = base * (1 + 2 * excess)
        # excess = 5.0 - 0 = 5.0
        # delay = 5 * (1 + 2 * 5.0) = 5 * 11 = 55
        delay = calculate_delay(deviation_percent=5.0, base_delay=5)

        self.assertEqual(delay, 55)

    def test_calculate_delay_zero_tolerance_ten_percent_deviation(self):
        """Should throttle at +10% deviation with zero tolerance."""
        from pacemaker.calculator import calculate_delay

        # 10% over target
        # Formula: delay = base * (1 + 2 * excess)
        # excess = 10.0 - 0 = 10.0
        # delay = 5 * (1 + 2 * 10.0) = 5 * 21 = 105
        delay = calculate_delay(deviation_percent=10.0, base_delay=5)

        self.assertEqual(delay, 105)

    def test_calculate_delay_zero_tolerance_twenty_percent_deviation(self):
        """Should cap at max_delay for large deviations."""
        from pacemaker.calculator import calculate_delay

        # 20% over target
        # Formula: delay = base * (1 + 2 * excess)
        # excess = 20.0 - 0 = 20.0
        # delay = 5 * (1 + 2 * 20.0) = 5 * 41 = 205
        # But should cap at max_delay=120
        delay = calculate_delay(deviation_percent=20.0, base_delay=5, max_delay=120)

        self.assertEqual(delay, 120)

    # ====================================================================
    # BUG #8 FIX TESTS - FUTURE RESET TIME HANDLING
    # Tests for calculate_time_percent when reset time is more than
    # window_hours in the future (window hasn't started yet)
    # ====================================================================

    def test_calculate_time_percent_far_future_reset(self):
        """Should return 0% when reset time is > window_hours in future (window not started)."""
        from pacemaker.calculator import calculate_time_percent

        # Reset time 5.76 hours from now (more than 5-hour window)
        resets_at = datetime.utcnow() + timedelta(hours=5.76)
        time_pct = calculate_time_percent(resets_at, window_hours=5.0)

        # Window hasn't started yet - should be 0% elapsed
        self.assertEqual(time_pct, 0.0)

    def test_calculate_time_percent_exactly_window_hours_future(self):
        """Should return 0% when reset time is exactly window_hours in future."""
        from pacemaker.calculator import calculate_time_percent

        # Reset time exactly 5.0 hours from now
        resets_at = datetime.utcnow() + timedelta(hours=5.0)
        time_pct = calculate_time_percent(resets_at, window_hours=5.0)

        # At exact window start - should be 0% elapsed (allowing for tiny float precision)
        self.assertLess(time_pct, 0.001)

    def test_calculate_time_percent_at_midpoint(self):
        """Should return ~50% when reset time is 2.5 hours in future (midpoint of 5-hour window)."""
        from pacemaker.calculator import calculate_time_percent

        # Reset time 2.5 hours from now (middle of 5-hour window)
        resets_at = datetime.utcnow() + timedelta(hours=2.5)
        time_pct = calculate_time_percent(resets_at, window_hours=5.0)

        # Should be approximately 50% elapsed
        self.assertGreater(time_pct, 49.0)
        self.assertLess(time_pct, 51.0)

    def test_calculate_time_percent_past_reset(self):
        """Should return 100% when reset time is in the past."""
        from pacemaker.calculator import calculate_time_percent

        # Reset time 1 hour ago
        resets_at = datetime.utcnow() - timedelta(hours=1.0)
        time_pct = calculate_time_percent(resets_at, window_hours=5.0)

        # Window is complete - should be 100%
        self.assertEqual(time_pct, 100.0)


if __name__ == "__main__":
    unittest.main()
