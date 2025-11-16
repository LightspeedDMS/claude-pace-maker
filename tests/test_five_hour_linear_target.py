#!/usr/bin/env python3
"""
Tests for 5-hour window LINEAR target calculation (Issue #3 - Additional Requirement)

Requirements:
- 5-hour window uses LINEAR target (NOT logarithmic)
- Linear pacing: target = time_pct (with 30-minute preload overlay)
- Consistent pacing strategy between 5-hour and 7-day windows
- Preload preserved: 10% allowance during first 30 minutes
- After preload: Linear accrual (50% at midpoint, 100% at end)

Test Coverage:
1. Target = 10% at T+0 (preload)
2. Target = 10% at T+15 min (still in preload)
3. Target = 10% at T+30 min (end of preload)
4. Target = 20% at T+60 min (linear after preload)
5. Target = 50% at T+150 min (midpoint linear)
6. Target = 100% at T+300 min (end of window)
7. Target != logarithmic curve (50% vs ~58% at midpoint)
8. Deviation calculation works with linear target
9. Throttling works with linear pacing
"""

import unittest
from datetime import datetime, timedelta
from src.pacemaker import pacing_engine, calculator


class TestFiveHourLinearTarget(unittest.TestCase):
    """Test that 5-hour window uses LINEAR target (not logarithmic)"""

    def test_target_is_10_percent_at_window_start(self):
        """At T+0 minutes, target should be 10% (preload)"""
        now = datetime.utcnow()
        five_hour_resets_at = now + timedelta(hours=5)  # Window just started
        seven_day_resets_at = now + timedelta(days=7)

        decision = pacing_engine.calculate_pacing_decision(
            five_hour_util=0.0,
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=0.0,
            seven_day_resets_at=seven_day_resets_at,
            use_adaptive=True,
            safety_buffer_pct=95.0,
            weekly_limit_enabled=True,
        )

        five_hour_target = decision["five_hour"]["target"]

        # MUST be 10% from preload
        self.assertAlmostEqual(
            five_hour_target,
            10.0,
            places=1,
            msg=f"Target at T+0 should be 10% (preload). Got: {five_hour_target}%",
        )

    def test_target_is_10_percent_at_15_minutes(self):
        """At T+15 minutes (within preload), target should still be 10%"""
        now = datetime.utcnow()
        window_start = now - timedelta(minutes=15)
        five_hour_resets_at = window_start + timedelta(hours=5)
        seven_day_resets_at = now + timedelta(days=7)

        decision = pacing_engine.calculate_pacing_decision(
            five_hour_util=0.0,
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=0.0,
            seven_day_resets_at=seven_day_resets_at,
            use_adaptive=True,
            safety_buffer_pct=95.0,
            weekly_limit_enabled=True,
        )

        five_hour_target = decision["five_hour"]["target"]

        # Still in preload period - should be 10%
        self.assertAlmostEqual(
            five_hour_target,
            10.0,
            places=1,
            msg=f"Target at T+15min should be 10% (still in preload). Got: {five_hour_target}%",
        )

    def test_target_is_10_percent_at_30_minutes(self):
        """At T+30 minutes (end of preload), target should be 10%"""
        now = datetime.utcnow()
        window_start = now - timedelta(minutes=30)
        five_hour_resets_at = window_start + timedelta(hours=5)
        seven_day_resets_at = now + timedelta(days=7)

        decision = pacing_engine.calculate_pacing_decision(
            five_hour_util=0.0,
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=0.0,
            seven_day_resets_at=seven_day_resets_at,
            use_adaptive=True,
            safety_buffer_pct=95.0,
            weekly_limit_enabled=True,
        )

        five_hour_target = decision["five_hour"]["target"]

        # At end of preload - transitions to linear but should still be ~10%
        # Time: 30/300 = 10% elapsed
        # Linear: 10% target
        self.assertAlmostEqual(
            five_hour_target,
            10.0,
            places=1,
            msg=f"Target at T+30min should be ~10% (transition point). Got: {five_hour_target}%",
        )

    def test_target_is_20_percent_at_60_minutes(self):
        """At T+60 minutes (after preload), target should be ~20% (linear)"""
        now = datetime.utcnow()
        window_start = now - timedelta(minutes=60)
        five_hour_resets_at = window_start + timedelta(hours=5)
        seven_day_resets_at = now + timedelta(days=7)

        decision = pacing_engine.calculate_pacing_decision(
            five_hour_util=0.0,
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=0.0,
            seven_day_resets_at=seven_day_resets_at,
            use_adaptive=True,
            safety_buffer_pct=95.0,
            weekly_limit_enabled=True,
        )

        five_hour_target = decision["five_hour"]["target"]

        # Time: 60/300 = 20% elapsed
        # Linear target: 20%
        self.assertAlmostEqual(
            five_hour_target,
            20.0,
            places=1,
            msg=f"Target at T+60min should be ~20% (linear). Got: {five_hour_target}%",
        )

    def test_target_is_50_percent_at_midpoint(self):
        """At T+150 minutes (midpoint), target should be 50% (linear)"""
        now = datetime.utcnow()
        window_start = now - timedelta(minutes=150)
        five_hour_resets_at = window_start + timedelta(hours=5)
        seven_day_resets_at = now + timedelta(days=7)

        decision = pacing_engine.calculate_pacing_decision(
            five_hour_util=0.0,
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=0.0,
            seven_day_resets_at=seven_day_resets_at,
            use_adaptive=True,
            safety_buffer_pct=95.0,
            weekly_limit_enabled=True,
        )

        five_hour_target = decision["five_hour"]["target"]

        # Time: 150/300 = 50% elapsed
        # Linear target: 50%
        self.assertAlmostEqual(
            five_hour_target,
            50.0,
            places=1,
            msg=f"Target at T+150min (midpoint) should be 50% (linear). Got: {five_hour_target}%",
        )

    def test_target_is_100_percent_at_end(self):
        """At T+300 minutes (end of window), target should be 100%"""
        now = datetime.utcnow()
        # 1 minute before reset to avoid edge cases
        window_start = now - timedelta(hours=5) + timedelta(minutes=1)
        five_hour_resets_at = window_start + timedelta(hours=5)
        seven_day_resets_at = now + timedelta(days=7)

        decision = pacing_engine.calculate_pacing_decision(
            five_hour_util=0.0,
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=0.0,
            seven_day_resets_at=seven_day_resets_at,
            use_adaptive=True,
            safety_buffer_pct=95.0,
            weekly_limit_enabled=True,
        )

        five_hour_target = decision["five_hour"]["target"]

        # At end of window - should be ~100%
        self.assertGreaterEqual(
            five_hour_target,
            99.0,
            msg=f"Target at T+~300min should be ~100%. Got: {five_hour_target}%",
        )

    def test_target_is_NOT_logarithmic(self):
        """At midpoint, linear target (50%) should differ from logarithmic (~58%)"""
        now = datetime.utcnow()
        window_start = now - timedelta(minutes=150)  # Midpoint
        five_hour_resets_at = window_start + timedelta(hours=5)
        seven_day_resets_at = now + timedelta(days=7)

        decision = pacing_engine.calculate_pacing_decision(
            five_hour_util=0.0,
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=0.0,
            seven_day_resets_at=seven_day_resets_at,
            use_adaptive=True,
            safety_buffer_pct=95.0,
            weekly_limit_enabled=True,
        )

        five_hour_target = decision["five_hour"]["target"]

        # Calculate what logarithmic would give at 50% time elapsed
        time_pct = 50.0
        logarithmic_target = calculator.calculate_logarithmic_target(time_pct)

        # Logarithmic at 50% ≈ 58% (100 * ln(1 + 0.5 * (e-1)))
        # Linear at 50% = 50%
        # These should be DIFFERENT

        self.assertNotAlmostEqual(
            five_hour_target,
            logarithmic_target,
            places=0,
            msg=f"Linear target ({five_hour_target}%) should differ from logarithmic ({logarithmic_target}%) at midpoint",
        )

        # Verify linear is LESS than logarithmic at midpoint
        self.assertLess(
            five_hour_target,
            logarithmic_target,
            msg=f"Linear target ({five_hour_target}%) should be LESS than logarithmic ({logarithmic_target}%) at midpoint",
        )

    def test_deviation_calculation_with_linear_target(self):
        """Deviation calculation should work correctly with linear target"""
        now = datetime.utcnow()
        window_start = now - timedelta(minutes=150)  # Midpoint
        five_hour_resets_at = window_start + timedelta(hours=5)
        seven_day_resets_at = now + timedelta(days=7)

        # User at 60% utilization, linear target should be 50%
        decision = pacing_engine.calculate_pacing_decision(
            five_hour_util=60.0,
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=0.0,
            seven_day_resets_at=seven_day_resets_at,
            use_adaptive=True,
            safety_buffer_pct=95.0,
            weekly_limit_enabled=True,
        )

        # Target should be ~50% (linear at midpoint)
        five_hour_target = decision["five_hour"]["target"]
        self.assertAlmostEqual(
            five_hour_target,
            50.0,
            places=1,
            msg=f"Target at midpoint should be ~50%. Got: {five_hour_target}%",
        )

        # Should throttle (60% util > 50% target × 95% safety = 47.5%)
        self.assertTrue(
            decision["should_throttle"],
            "Should throttle when utilization (60%) exceeds safe allowance (~47.5%)",
        )

        # Constrained window should be 5-hour
        self.assertEqual(
            decision["constrained_window"],
            "5-hour",
            "5-hour window should be most constrained",
        )

    def test_throttling_works_with_linear_pacing(self):
        """Throttling delay should scale correctly with linear pacing"""
        now = datetime.utcnow()
        window_start = now - timedelta(minutes=60)  # 20% elapsed
        five_hour_resets_at = window_start + timedelta(hours=5)
        seven_day_resets_at = now + timedelta(days=7)

        # User at 30% utilization, linear target should be 20%
        decision = pacing_engine.calculate_pacing_decision(
            five_hour_util=30.0,
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=0.0,
            seven_day_resets_at=seven_day_resets_at,
            use_adaptive=True,
            safety_buffer_pct=95.0,
            weekly_limit_enabled=True,
        )

        # Target should be ~20% (linear)
        five_hour_target = decision["five_hour"]["target"]
        self.assertAlmostEqual(
            five_hour_target,
            20.0,
            places=1,
            msg=f"Target at 20% time elapsed should be ~20%. Got: {five_hour_target}%",
        )

        # Safe allowance = 20% × 0.95 = 19%
        # Util = 30% > 19%, so should throttle
        self.assertTrue(
            decision["should_throttle"],
            "Should throttle when utilization (30%) exceeds safe allowance (19%)",
        )

        # Delay should be > 0 (throttling is active)
        # Note: 30% util vs 20% target is 50% overage, which is significant
        # and may result in max_delay (350s) being applied - this is correct behavior
        self.assertGreater(
            decision["delay_seconds"], 0, "Delay should be > 0 when over safe allowance"
        )
        self.assertLessEqual(
            decision["delay_seconds"],
            350,  # max_delay
            "Delay should not exceed max_delay",
        )

    def test_legacy_mode_still_uses_logarithmic(self):
        """Legacy mode (use_adaptive=False) should still use logarithmic target"""
        now = datetime.utcnow()
        window_start = now - timedelta(minutes=150)  # Midpoint
        five_hour_resets_at = window_start + timedelta(hours=5)
        seven_day_resets_at = now + timedelta(days=7)

        decision = pacing_engine.calculate_pacing_decision(
            five_hour_util=0.0,
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=0.0,
            seven_day_resets_at=seven_day_resets_at,
            use_adaptive=False,  # Legacy mode
            safety_buffer_pct=95.0,
            weekly_limit_enabled=True,
        )

        five_hour_target = decision["five_hour"]["target"]

        # In legacy mode, should use logarithmic
        time_pct = 50.0  # Midpoint
        logarithmic_target = calculator.calculate_logarithmic_target(time_pct)

        self.assertAlmostEqual(
            five_hour_target,
            logarithmic_target,
            places=1,
            msg=f"Legacy mode should use logarithmic target. Expected: {logarithmic_target}%, Got: {five_hour_target}%",
        )

    def test_preload_preserved_with_linear_pacing(self):
        """Preload should work correctly with linear pacing"""
        now = datetime.utcnow()
        five_hour_resets_at = now + timedelta(hours=5)  # Window start
        seven_day_resets_at = now + timedelta(days=7)

        # User burns 9% immediately
        decision = pacing_engine.calculate_pacing_decision(
            five_hour_util=9.0,
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=0.0,
            seven_day_resets_at=seven_day_resets_at,
            use_adaptive=True,
            safety_buffer_pct=95.0,
            weekly_limit_enabled=True,
        )

        # Should NOT throttle (9% < 10% × 0.95 = 9.5% safe allowance)
        self.assertFalse(
            decision["should_throttle"],
            "Should not throttle when using preload allowance",
        )

        self.assertEqual(
            decision["delay_seconds"],
            0,
            "Delay should be 0 when within preload allowance",
        )


class TestLinearVsLogarithmicComparison(unittest.TestCase):
    """Compare linear and logarithmic targets to verify difference"""

    def test_linear_vs_logarithmic_at_various_timepoints(self):
        """Verify linear != logarithmic at key timepoints"""
        test_cases = [
            (10.0, "10% elapsed"),
            (25.0, "25% elapsed"),
            (50.0, "50% elapsed (midpoint)"),
            (75.0, "75% elapsed"),
            (90.0, "90% elapsed"),
        ]

        for time_pct, description in test_cases:
            linear_target = calculator.calculate_linear_target(time_pct)
            logarithmic_target = calculator.calculate_logarithmic_target(time_pct)

            # Linear should equal time_pct
            self.assertAlmostEqual(
                linear_target,
                time_pct,
                places=1,
                msg=f"Linear target at {description} should equal {time_pct}%",
            )

            # Logarithmic should be different (except at 0% and 100%)
            if 0 < time_pct < 100:
                self.assertNotAlmostEqual(
                    linear_target,
                    logarithmic_target,
                    places=0,
                    msg=f"Linear and logarithmic should differ at {description}",
                )

    def test_logarithmic_greater_than_linear_before_midpoint(self):
        """Logarithmic curve should be GREATER than linear in early window (accelerates faster)"""
        # Logarithmic formula: 100 * ln(1 + (time_pct/100) * (e - 1))
        # This creates an accelerating curve that starts FASTER than linear
        time_pct = 30.0
        linear_target = calculator.calculate_linear_target(time_pct)
        logarithmic_target = calculator.calculate_logarithmic_target(time_pct)

        # Logarithmic accelerates faster early on
        self.assertGreater(
            logarithmic_target,
            linear_target,
            f"Logarithmic ({logarithmic_target}%) should be GREATER than linear ({linear_target}%) early in window",
        )

    def test_logarithmic_greater_than_linear_after_inflection(self):
        """Logarithmic curve should be GREATER than linear after ~63% time elapsed"""
        # At later timepoints, logarithmic accelerates past linear
        time_pct = 75.0
        linear_target = calculator.calculate_linear_target(time_pct)
        logarithmic_target = calculator.calculate_logarithmic_target(time_pct)

        # Logarithmic accelerates later
        self.assertGreater(
            logarithmic_target,
            linear_target,
            f"Logarithmic ({logarithmic_target}%) should be GREATER than linear ({linear_target}%) late in window",
        )


if __name__ == "__main__":
    unittest.main()
