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
import math
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
            seven_day_target=50.0
        )

        self.assertEqual(result['window'], '5-hour')
        self.assertAlmostEqual(result['deviation'], 20.0)

    def test_determine_most_constrained_window_seven_day(self):
        """Should return 7-day when it's more constrained."""
        from pacemaker.calculator import determine_most_constrained_window

        # 5-hour: 55% used, target 50% => 5% over
        # 7-day: 75% used, target 50% => 25% over
        result = determine_most_constrained_window(
            five_hour_util=55.0,
            five_hour_target=50.0,
            seven_day_util=75.0,
            seven_day_target=50.0
        )

        self.assertEqual(result['window'], '7-day')
        self.assertAlmostEqual(result['deviation'], 25.0)

    def test_determine_most_constrained_with_null_windows(self):
        """Should handle NULL windows (return no constraint)."""
        from pacemaker.calculator import determine_most_constrained_window

        # Both windows NULL
        result = determine_most_constrained_window(
            five_hour_util=None,
            five_hour_target=0.0,
            seven_day_util=None,
            seven_day_target=0.0
        )

        self.assertIsNone(result['window'])
        self.assertEqual(result['deviation'], 0.0)

    def test_calculate_delay_no_deviation(self):
        """Should return 0 delay when deviation is <= threshold."""
        from pacemaker.calculator import calculate_delay

        # Deviation at threshold (10%) - no delay
        delay = calculate_delay(deviation_percent=10.0, base_delay=5, threshold=10)

        self.assertEqual(delay, 0)

    def test_calculate_delay_small_deviation(self):
        """Should return proportional delay for small deviation."""
        from pacemaker.calculator import calculate_delay

        # Deviation 20% over threshold 10% => 10% over
        # delay = base * (1 + 2 * 10) = 5 * 21 = 105
        delay = calculate_delay(deviation_percent=20.0, base_delay=5, threshold=10)

        self.assertAlmostEqual(delay, 105.0, places=1)

    def test_calculate_delay_large_deviation(self):
        """Should cap delay at max_delay."""
        from pacemaker.calculator import calculate_delay

        # Large deviation should hit max_delay cap
        delay = calculate_delay(deviation_percent=200.0, base_delay=5, max_delay=120)

        self.assertEqual(delay, 120)

    def test_calculate_delay_minimum_floor(self):
        """Should enforce minimum delay of 5 seconds when throttling."""
        from pacemaker.calculator import calculate_delay

        # Very small deviation, but should still be at least 5s
        delay = calculate_delay(deviation_percent=11.0, base_delay=5, threshold=10)

        self.assertGreaterEqual(delay, 5)


if __name__ == '__main__':
    unittest.main()
