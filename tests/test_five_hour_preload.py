"""
Tests for 5-hour window with 30-minute preload and LINEAR pacing

The 5-hour window uses LINEAR pacing (not logarithmic) with a 30-minute preload.
This gives users 10% working room at the start, then linear accrual.

Requirements:
- T+0 minutes: 10% allowance upfront (preload)
- T+15 minutes: Still 10% allowance (within preload period)
- T+30 minutes: 10% (end of preload, transition to linear)
- T+60 minutes: 20% (linear accrual)
- T+150 minutes: 50% (midpoint - linear, not logarithmic ~58%)
- T+300 minutes: 100% allowance (end of window)
- No throttling when usage is under safe allowance (95% of target)

See also: test_five_hour_linear_target.py for comprehensive linear pacing tests
"""

import unittest
from datetime import datetime, timedelta
from src.pacemaker import pacing_engine


class TestFiveHourPreload(unittest.TestCase):
    """Test that 5-hour window has 30-minute preload"""

    def test_five_hour_preload_at_window_start(self):
        """At T+0 minutes, 5-hour window should have 10% allowance"""
        # Window just reset
        now = datetime.utcnow()
        five_hour_resets_at = now + timedelta(hours=5)
        seven_day_resets_at = now + timedelta(days=7)

        # User has used 0% so far
        decision = pacing_engine.calculate_pacing_decision(
            five_hour_util=0.0,
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=0.0,
            seven_day_resets_at=seven_day_resets_at,
            use_adaptive=True,
            safety_buffer_pct=95.0,
            weekly_limit_enabled=True,
        )

        # Verify: Should NOT throttle (we're at 0%, allowance is 10%)
        self.assertFalse(
            decision["should_throttle"],
            "Should not throttle at window start with 0% usage and 10% preload allowance",
        )

        # Check the 5-hour window target (should be 10% from preload)
        five_hour_target = decision["five_hour"]["target"]
        self.assertGreaterEqual(
            five_hour_target,
            10.0,
            f"5-hour target should be at least 10% from preload. Got: {five_hour_target}%",
        )

    def test_five_hour_preload_at_10_percent_usage(self):
        """When using exactly 10% at T+0, should throttle minimally (slightly over safe allowance)"""
        now = datetime.utcnow()
        five_hour_resets_at = now + timedelta(hours=5)
        seven_day_resets_at = now + timedelta(days=7)

        # User has used exactly 10%
        decision = pacing_engine.calculate_pacing_decision(
            five_hour_util=10.0,  # Exactly at preload allowance
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=0.0,
            seven_day_resets_at=seven_day_resets_at,
            use_adaptive=True,
            safety_buffer_pct=95.0,
            weekly_limit_enabled=True,
        )

        # At T+0, allowance should be 10%
        # Safe allowance = 10% × 0.95 = 9.5%
        # Util=10% > safe=9.5%, so should throttle
        five_hour_target = decision["five_hour"]["target"]

        self.assertAlmostEqual(
            five_hour_target,
            10.0,
            places=1,
            msg=f"Target should be 10% at window start. Got: {five_hour_target}%",
        )

        # With 10% usage and 9.5% safe allowance, should throttle
        # The delay may be significant due to forward-looking projection (no time elapsed yet)
        self.assertTrue(
            decision["should_throttle"],
            "Should throttle when utilization (10%) exceeds safe allowance (9.5%)",
        )

    def test_five_hour_preload_during_preload_period(self):
        """At T+15 minutes (within preload), allowance should still be 10%"""
        now = datetime.utcnow()
        # Simulate 15 minutes elapsed
        window_start = now - timedelta(minutes=15)
        five_hour_resets_at = window_start + timedelta(hours=5)
        seven_day_resets_at = now + timedelta(days=7)

        # User has used 5% so far
        decision = pacing_engine.calculate_pacing_decision(
            five_hour_util=5.0,
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=0.0,
            seven_day_resets_at=seven_day_resets_at,
            use_adaptive=True,
            safety_buffer_pct=95.0,
            weekly_limit_enabled=True,
        )

        # Verify: Should NOT throttle (5% < 10% preload allowance)
        self.assertFalse(
            decision["should_throttle"],
            "Should not throttle during preload period with 5% usage and 10% allowance",
        )

        # Target should still be around 10% (preload flat allowance)
        five_hour_target = decision["five_hour"]["target"]
        self.assertGreaterEqual(
            five_hour_target,
            10.0,
            f"Target should be at least 10% during preload period. Got: {five_hour_target}%",
        )

    def test_five_hour_after_preload_period(self):
        """At T+31 minutes (after preload), allowance should accrue linearly"""
        now = datetime.utcnow()
        # Simulate 31 minutes elapsed (just after preload)
        window_start = now - timedelta(minutes=31)
        five_hour_resets_at = window_start + timedelta(hours=5)
        seven_day_resets_at = now + timedelta(days=7)

        # User has used 8% so far
        decision = pacing_engine.calculate_pacing_decision(
            five_hour_util=8.0,
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=0.0,
            seven_day_resets_at=seven_day_resets_at,
            use_adaptive=True,
            safety_buffer_pct=95.0,
            weekly_limit_enabled=True,
        )

        # At T+31min, time_pct = 31/300 = 10.33%
        # With LINEAR target, allowance should be ~10.33% (normal accrual after preload)
        five_hour_target = decision["five_hour"]["target"]

        # Target should be > 10% (we've moved past preload into normal linear accrual)
        self.assertGreater(
            five_hour_target,
            10.0,
            f"Target should be > 10% after preload period. Got: {five_hour_target}%",
        )

        # Should NOT throttle (8% usage < allowance)
        self.assertFalse(
            decision["should_throttle"],
            f"Should not throttle with 8% usage and {five_hour_target}% allowance",
        )

    def test_five_hour_end_of_window(self):
        """At T+300 minutes (end of window), allowance should be 100%"""
        now = datetime.utcnow()
        # Simulate window about to reset (1 minute left)
        window_start = now - timedelta(hours=5) + timedelta(minutes=1)
        five_hour_resets_at = window_start + timedelta(hours=5)
        seven_day_resets_at = now + timedelta(days=7)

        # User has used 95% so far
        decision = pacing_engine.calculate_pacing_decision(
            five_hour_util=95.0,
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=0.0,
            seven_day_resets_at=seven_day_resets_at,
            use_adaptive=True,
            safety_buffer_pct=95.0,
            weekly_limit_enabled=True,
        )

        # Target should be close to 100%
        five_hour_target = decision["five_hour"]["target"]
        self.assertGreaterEqual(
            five_hour_target,
            99.0,
            f"Target should be nearly 100% at end of window. Got: {five_hour_target}%",
        )

        # Should NOT throttle (95% < 100%)
        self.assertFalse(
            decision["should_throttle"],
            f"Should not throttle with 95% usage and {five_hour_target}% allowance near window end",
        )

    def test_five_hour_no_throttling_when_using_preload_immediately(self):
        """User should be able to use 10% credits immediately without throttling"""
        now = datetime.utcnow()
        five_hour_resets_at = now + timedelta(hours=5)
        seven_day_resets_at = now + timedelta(days=7)

        # Scenario: User burns through 9% immediately at window start
        decision = pacing_engine.calculate_pacing_decision(
            five_hour_util=9.0,  # Just under safe_allowance (10% × 0.95 = 9.5%)
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=0.0,
            seven_day_resets_at=seven_day_resets_at,
            use_adaptive=True,
            safety_buffer_pct=95.0,
            weekly_limit_enabled=True,
        )

        # Should NOT throttle (9% < 9.5% safe allowance)
        self.assertFalse(
            decision["should_throttle"],
            "Should not throttle when using 9% immediately with 10% preload (9.5% safe allowance)",
        )

        self.assertEqual(
            decision["delay_seconds"],
            0,
            f"Delay should be 0 when under safe allowance. Got: {decision['delay_seconds']}s",
        )

    def test_five_hour_preload_integration_with_adaptive_throttle(self):
        """Verify preload is passed to adaptive_throttle for 5-hour window"""
        now = datetime.utcnow()
        five_hour_resets_at = now + timedelta(hours=5)
        seven_day_resets_at = now + timedelta(days=7)

        # Call with adaptive enabled
        decision = pacing_engine.calculate_pacing_decision(
            five_hour_util=12.0,  # Slightly over preload
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=0.0,
            seven_day_resets_at=seven_day_resets_at,
            use_adaptive=True,
            safety_buffer_pct=95.0,
            preload_hours=0.5,  # Explicitly pass preload
            weekly_limit_enabled=True,
        )

        # Should use adaptive algorithm
        self.assertEqual(
            decision.get("algorithm"),
            "adaptive",
            "Should use adaptive algorithm when use_adaptive=True",
        )

        # Projection should exist (adaptive algorithm provides it)
        self.assertIn(
            "projection", decision, "Adaptive decision should include projection data"
        )


if __name__ == "__main__":
    unittest.main()
