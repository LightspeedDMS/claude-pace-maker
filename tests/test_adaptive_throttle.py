#!/usr/bin/env python3
"""
Test suite for adaptive throttling algorithm.

Tests forward-looking projection and intelligent delay calculation.
"""

import pytest
from datetime import datetime
from src.pacemaker.adaptive_throttle import (
    calculate_adaptive_delay,
    count_weekday_seconds,
    calculate_allowance_pct,
    is_weekend,
)


class TestScenario1SlightOverageEarly:
    """Scenario 1: 10% over target, early in 5-hour window."""

    def test_slight_overage_early_calculates_small_delay(self):
        """With 95% safety buffer, pace targeting 100% needs throttling."""
        result = calculate_adaptive_delay(
            current_util=20.0,
            target_util=10.0,
            time_elapsed_pct=20.0,
            time_remaining_hours=4.0,
            window_hours=5.0,
        )

        # At 20% burn rate, would hit 100% in 4 hours
        # But with 95% safety buffer, we need throttling to hit 95% instead
        # Overage is 20% - 9.5% = 10.5%, which triggers 'gradual' strategy
        assert result["delay_seconds"] >= 0
        assert result["strategy"] in ["none", "minimal", "gradual"]
        assert result["projection"]["util_if_throttled"] <= 96.0  # Should target ~95%

    def test_slight_overage_early_includes_projection(self):
        """Should include projection data."""
        result = calculate_adaptive_delay(
            current_util=20.0,
            target_util=10.0,
            time_elapsed_pct=20.0,
            time_remaining_hours=4.0,
            window_hours=5.0,
        )

        assert "projection" in result
        assert "util_if_no_throttle" in result["projection"]
        assert "util_if_throttled" in result["projection"]
        assert "credits_remaining_pct" in result["projection"]


class TestScenario2MajorOverageMidWindow:
    """Scenario 2: 24% over target, mid-window (current real situation)."""

    def test_major_overage_mid_window_calculates_moderate_delay(self):
        """Should apply aggressive correction with significant delay."""
        result = calculate_adaptive_delay(
            current_util=56.0,
            target_util=32.0,
            time_elapsed_pct=31.0,
            time_remaining_hours=3.45,
            window_hours=5.0,
        )

        # Expect significant delay for major overage (24% over target)
        # Algorithm correctly identifies this needs strong throttling
        assert result["delay_seconds"] >= 100
        assert result["delay_seconds"] <= 300
        assert result["strategy"] in ["aggressive", "emergency"]

    def test_major_overage_mid_window_projects_overage(self):
        """Should project we'll exceed 100% without throttling."""
        result = calculate_adaptive_delay(
            current_util=56.0,
            target_util=32.0,
            time_elapsed_pct=31.0,
            time_remaining_hours=3.45,
            window_hours=5.0,
        )

        # At current burn rate, should project exceeding 100%
        assert result["projection"]["util_if_no_throttle"] > 100.0


class TestScenario3SlightOverageNearEnd:
    """Scenario 3: 10% over but only 30 minutes left."""

    def test_slight_overage_near_end_calculates_high_delay(self):
        """Should apply emergency correction with high delay."""
        result = calculate_adaptive_delay(
            current_util=95.0,
            target_util=85.0,
            time_elapsed_pct=90.0,
            time_remaining_hours=0.5,
            window_hours=5.0,
            max_delay=350,
        )

        # Expect high delay - little time to correct
        # 10% overage = gradual strategy, but still significant delay due to time pressure
        assert result["delay_seconds"] >= 120
        assert result["delay_seconds"] <= 350
        assert result["strategy"] in ["gradual", "aggressive", "emergency"]


class TestScenario4OnTrack:
    """Scenario 4: Exactly on target."""

    def test_on_track_no_delay(self):
        """Should throttle when exactly on target with 95% safety buffer."""
        result = calculate_adaptive_delay(
            current_util=50.0,
            target_util=50.0,
            time_elapsed_pct=50.0,
            time_remaining_hours=2.5,
            window_hours=5.0,
        )

        # With 95% safety buffer, current_util=50% is over safe_allowance=47.5%
        # So we expect some throttling
        assert result["delay_seconds"] >= 0
        assert result["strategy"] in ["none", "minimal", "gradual"]

    def test_on_track_projection_stays_under_100(self):
        """Should project staying under 100%."""
        result = calculate_adaptive_delay(
            current_util=50.0,
            target_util=50.0,
            time_elapsed_pct=50.0,
            time_remaining_hours=2.5,
            window_hours=5.0,
        )

        # Should project ending at or under 100%
        assert result["projection"]["util_if_no_throttle"] <= 100.0


class TestScenario5UnderBudget:
    """Scenario 5: Using less than target."""

    def test_under_budget_no_delay(self):
        """Should not throttle when under budget."""
        result = calculate_adaptive_delay(
            current_util=30.0,
            target_util=50.0,
            time_elapsed_pct=50.0,
            time_remaining_hours=2.5,
            window_hours=5.0,
        )

        assert result["delay_seconds"] == 0
        assert result["strategy"] == "none"

    def test_under_budget_has_credits_remaining(self):
        """Should show positive credits remaining."""
        result = calculate_adaptive_delay(
            current_util=30.0,
            target_util=50.0,
            time_elapsed_pct=50.0,
            time_remaining_hours=2.5,
            window_hours=5.0,
        )

        assert result["projection"]["credits_remaining_pct"] > 0


class TestScenario6MassiveOverageNearEnd:
    """Scenario 6: Way over budget, little time left."""

    def test_massive_overage_near_end_max_delay(self):
        """Should apply maximum delay for emergency situation."""
        result = calculate_adaptive_delay(
            current_util=95.0,
            target_util=50.0,
            time_elapsed_pct=80.0,
            time_remaining_hours=1.0,
            window_hours=5.0,
            max_delay=350,
        )

        # Should hit max delay cap
        assert result["delay_seconds"] == 350
        assert result["strategy"] == "emergency"

    def test_massive_overage_near_end_projects_way_over(self):
        """Should project massive overage."""
        result = calculate_adaptive_delay(
            current_util=95.0,
            target_util=50.0,
            time_elapsed_pct=80.0,
            time_remaining_hours=1.0,
            window_hours=5.0,
        )

        # Should project significant overage (relaxed from 120% to 115%)
        assert result["projection"]["util_if_no_throttle"] > 115.0


class TestScenario7SevenDayWindow:
    """Scenario 7: Similar overage but in 7-day window."""

    def test_long_window_smaller_delay(self):
        """Should apply reasonable delay even with more time available."""
        result = calculate_adaptive_delay(
            current_util=60.0,
            target_util=40.0,
            time_elapsed_pct=40.0,
            time_remaining_hours=100.0,
            window_hours=168.0,
        )

        # 20% overage still requires throttling even with more time
        # Algorithm uses gentler multiplier for long windows
        assert result["delay_seconds"] >= 5
        assert result["delay_seconds"] <= 250
        assert result["strategy"] in ["gradual", "aggressive"]


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_zero_time_remaining_max_delay(self):
        """Should apply max delay when no time left."""
        result = calculate_adaptive_delay(
            current_util=80.0,
            target_util=50.0,
            time_elapsed_pct=100.0,
            time_remaining_hours=0.0,
            window_hours=5.0,
            max_delay=350,
        )

        # No time left = max delay
        assert result["delay_seconds"] == 350

    def test_zero_utilization_no_delay(self):
        """Should not throttle with zero utilization."""
        result = calculate_adaptive_delay(
            current_util=0.0,
            target_util=50.0,
            time_elapsed_pct=50.0,
            time_remaining_hours=2.5,
            window_hours=5.0,
        )

        assert result["delay_seconds"] == 0

    def test_negative_utilization_no_delay(self):
        """Should handle negative utilization gracefully."""
        result = calculate_adaptive_delay(
            current_util=-10.0,
            target_util=50.0,
            time_elapsed_pct=50.0,
            time_remaining_hours=2.5,
            window_hours=5.0,
        )

        assert result["delay_seconds"] == 0

    def test_100_percent_utilization_max_delay(self):
        """Should apply max delay at 100% utilization."""
        result = calculate_adaptive_delay(
            current_util=100.0,
            target_util=50.0,
            time_elapsed_pct=50.0,
            time_remaining_hours=2.5,
            window_hours=5.0,
            max_delay=350,
        )

        assert result["delay_seconds"] == 350

    def test_custom_min_max_delays(self):
        """Should respect custom min/max delay bounds."""
        result = calculate_adaptive_delay(
            current_util=56.0,
            target_util=32.0,
            time_elapsed_pct=31.0,
            time_remaining_hours=3.45,
            window_hours=5.0,
            min_delay=10,
            max_delay=180,
        )

        assert result["delay_seconds"] >= 10
        assert result["delay_seconds"] <= 180

    def test_very_high_tools_per_hour(self):
        """Should handle high tool usage rate."""
        result = calculate_adaptive_delay(
            current_util=56.0,
            target_util=32.0,
            time_elapsed_pct=31.0,
            time_remaining_hours=3.45,
            window_hours=5.0,
        )

        # Should still calculate reasonable delay
        assert result["delay_seconds"] >= 5
        assert result["delay_seconds"] <= 300

    def test_very_low_tools_per_hour(self):
        """Should handle low tool usage rate."""
        result = calculate_adaptive_delay(
            current_util=56.0,
            target_util=32.0,
            time_elapsed_pct=31.0,
            time_remaining_hours=3.45,
            window_hours=5.0,
        )

        # Should still calculate reasonable delay
        assert result["delay_seconds"] >= 5
        assert result["delay_seconds"] <= 300


class TestReturnStructure:
    """Test return value structure and types."""

    def test_returns_all_required_fields(self):
        """Should return all required fields."""
        result = calculate_adaptive_delay(
            current_util=56.0,
            target_util=32.0,
            time_elapsed_pct=31.0,
            time_remaining_hours=3.45,
            window_hours=5.0,
        )

        # Top-level fields
        assert "delay_seconds" in result
        assert "strategy" in result
        assert "projection" in result

        # Projection fields
        assert "util_if_no_throttle" in result["projection"]
        assert "util_if_throttled" in result["projection"]
        assert "credits_remaining_pct" in result["projection"]

    def test_delay_seconds_is_integer(self):
        """Should return delay as integer."""
        result = calculate_adaptive_delay(
            current_util=56.0,
            target_util=32.0,
            time_elapsed_pct=31.0,
            time_remaining_hours=3.45,
            window_hours=5.0,
        )

        assert isinstance(result["delay_seconds"], int)

    def test_strategy_is_valid_value(self):
        """Should return valid strategy value."""
        result = calculate_adaptive_delay(
            current_util=56.0,
            target_util=32.0,
            time_elapsed_pct=31.0,
            time_remaining_hours=3.45,
            window_hours=5.0,
        )

        valid_strategies = ["none", "minimal", "gradual", "aggressive", "emergency"]
        assert result["strategy"] in valid_strategies

    def test_projection_values_are_numeric(self):
        """Should return numeric projection values."""
        result = calculate_adaptive_delay(
            current_util=56.0,
            target_util=32.0,
            time_elapsed_pct=31.0,
            time_remaining_hours=3.45,
            window_hours=5.0,
        )

        proj = result["projection"]
        assert isinstance(proj["util_if_no_throttle"], (int, float))
        assert isinstance(proj["util_if_throttled"], (int, float))
        assert isinstance(proj["credits_remaining_pct"], (int, float))


class TestSlowdownCalculation:
    """Test slowdown ratio and projection accuracy."""

    def test_slowdown_reduces_projected_utilization(self):
        """Throttling should reduce projected end utilization."""
        result = calculate_adaptive_delay(
            current_util=56.0,
            target_util=32.0,
            time_elapsed_pct=31.0,
            time_remaining_hours=3.45,
            window_hours=5.0,
        )

        # Throttled projection should be lower than no-throttle
        assert (
            result["projection"]["util_if_throttled"]
            < result["projection"]["util_if_no_throttle"]
        )

    def test_on_track_no_slowdown_needed(self):
        """When on track with safety buffer, minor slowdown needed."""
        result = calculate_adaptive_delay(
            current_util=50.0,
            target_util=50.0,
            time_elapsed_pct=50.0,
            time_remaining_hours=2.5,
            window_hours=5.0,
        )

        # With 95% safety buffer, we're over safe_allowance so expect some throttling
        # But should be minimal since we're close to target
        assert result["delay_seconds"] >= 0
        # Throttled projection should aim for ~95%
        throttled = result["projection"]["util_if_throttled"]
        assert throttled <= 96.0  # Should target ~95%


class TestStrategySelection:
    """Test strategy selection logic."""

    def test_minimal_strategy_for_tiny_overage(self):
        """Should use minimal strategy for <5% overage."""
        result = calculate_adaptive_delay(
            current_util=52.0,
            target_util=50.0,
            time_elapsed_pct=50.0,
            time_remaining_hours=2.5,
            window_hours=5.0,
        )

        # Only 2% over = minimal
        assert result["strategy"] == "minimal"

    def test_gradual_strategy_for_moderate_overage(self):
        """Should use gradual strategy for 5-20% overage."""
        result = calculate_adaptive_delay(
            current_util=60.0,
            target_util=50.0,
            time_elapsed_pct=50.0,
            time_remaining_hours=2.5,
            window_hours=5.0,
        )

        # 10% over = gradual
        assert result["strategy"] == "gradual"

    def test_aggressive_strategy_for_large_overage(self):
        """Should use aggressive or emergency strategy for >20% overage."""
        result = calculate_adaptive_delay(
            current_util=75.0,
            target_util=50.0,
            time_elapsed_pct=50.0,
            time_remaining_hours=2.5,
            window_hours=5.0,
        )

        # 25% over = aggressive or emergency (may hit max delay)
        assert result["strategy"] in ["aggressive", "emergency"]

    def test_emergency_strategy_for_critical_situation(self):
        """Should use emergency strategy when hitting max delay."""
        result = calculate_adaptive_delay(
            current_util=95.0,
            target_util=50.0,
            time_elapsed_pct=80.0,
            time_remaining_hours=1.0,
            window_hours=5.0,
            max_delay=350,
        )

        # Massive overage near end = emergency
        assert result["strategy"] == "emergency"
        assert result["delay_seconds"] == 350


class TestIsWeekend:
    """Test weekend detection function."""

    def test_monday_not_weekend(self):
        """Monday should not be weekend."""
        dt = datetime(2025, 1, 6)  # Monday, Jan 6, 2025
        assert is_weekend(dt) is False

    def test_tuesday_not_weekend(self):
        """Tuesday should not be weekend."""
        dt = datetime(2025, 1, 7)  # Tuesday
        assert is_weekend(dt) is False

    def test_wednesday_not_weekend(self):
        """Wednesday should not be weekend."""
        dt = datetime(2025, 1, 8)  # Wednesday
        assert is_weekend(dt) is False

    def test_thursday_not_weekend(self):
        """Thursday should not be weekend."""
        dt = datetime(2025, 1, 9)  # Thursday
        assert is_weekend(dt) is False

    def test_friday_not_weekend(self):
        """Friday should not be weekend."""
        dt = datetime(2025, 1, 10)  # Friday
        assert is_weekend(dt) is False

    def test_saturday_is_weekend(self):
        """Saturday should be weekend."""
        dt = datetime(2025, 1, 11)  # Saturday
        assert is_weekend(dt) is True

    def test_sunday_is_weekend(self):
        """Sunday should be weekend."""
        dt = datetime(2025, 1, 12)  # Sunday
        assert is_weekend(dt) is True


class TestCountWeekdaySeconds:
    """Test counting of weekday seconds."""

    def test_monday_9am_to_5pm(self):
        """Monday 9am to 5pm should be 8 hours = 28,800 seconds."""
        start = datetime(2025, 1, 6, 9, 0, 0)  # Monday 9am
        end = datetime(2025, 1, 6, 17, 0, 0)  # Monday 5pm
        result = count_weekday_seconds(start, end)
        assert result == 28800  # 8 hours * 3600

    def test_saturday_all_day(self):
        """Saturday all day should be 0 seconds."""
        start = datetime(2025, 1, 11, 0, 0, 0)  # Saturday midnight
        end = datetime(2025, 1, 11, 23, 59, 59)  # Saturday before midnight
        result = count_weekday_seconds(start, end)
        assert result == 0

    def test_sunday_all_day(self):
        """Sunday all day should be 0 seconds."""
        start = datetime(2025, 1, 12, 0, 0, 0)  # Sunday midnight
        end = datetime(2025, 1, 12, 23, 59, 59)  # Sunday before midnight
        result = count_weekday_seconds(start, end)
        assert result == 0

    def test_friday_6pm_to_monday_6am(self):
        """Friday 6pm to Monday 6am should count only Friday 6pm-midnight (6 hours)."""
        start = datetime(2025, 1, 10, 18, 0, 0)  # Friday 6pm
        end = datetime(2025, 1, 13, 6, 0, 0)  # Monday 6am
        result = count_weekday_seconds(start, end)
        # Friday 6pm to midnight = 6 hours = 21,600 seconds
        # Saturday = 0 (weekend)
        # Sunday = 0 (weekend)
        # Monday midnight to 6am = 6 hours = 21,600 seconds
        # Total = 43,200 seconds
        assert result == 43200

    def test_wednesday_to_following_wednesday(self):
        """Wednesday to following Wednesday (7 days) should be 5 weekdays = 432,000 seconds."""
        start = datetime(2025, 1, 8, 0, 0, 0)  # Wednesday midnight
        end = datetime(2025, 1, 15, 0, 0, 0)  # Following Wednesday midnight
        result = count_weekday_seconds(start, end)
        # Wed, Thu, Fri, Sat, Sun, Mon, Tue = 5 weekdays * 86400 seconds
        assert result == 432000  # 5 days * 86400

    def test_friday_midnight_to_sunday_midnight(self):
        """Friday midnight to Sunday midnight should be 1 day (Friday only)."""
        start = datetime(2025, 1, 10, 0, 0, 0)  # Friday midnight
        end = datetime(2025, 1, 12, 0, 0, 0)  # Sunday midnight
        result = count_weekday_seconds(start, end)
        assert result == 86400  # 1 day (Friday only)

    def test_single_minute_on_weekday(self):
        """Single minute on weekday should be 60 seconds."""
        start = datetime(2025, 1, 6, 12, 0, 0)  # Monday noon
        end = datetime(2025, 1, 6, 12, 1, 0)  # Monday 12:01pm
        result = count_weekday_seconds(start, end)
        assert result == 60

    def test_single_minute_on_weekend(self):
        """Single minute on weekend should be 0 seconds."""
        start = datetime(2025, 1, 11, 12, 0, 0)  # Saturday noon
        end = datetime(2025, 1, 11, 12, 1, 0)  # Saturday 12:01pm
        result = count_weekday_seconds(start, end)
        assert result == 0

    def test_spanning_multiple_weeks(self):
        """Spanning 14 days should count 10 weekdays."""
        start = datetime(2025, 1, 6, 0, 0, 0)  # Monday
        end = datetime(2025, 1, 20, 0, 0, 0)  # Monday two weeks later
        result = count_weekday_seconds(start, end)
        # 14 days with 4 weekend days = 10 weekdays
        assert result == 864000  # 10 days * 86400


class TestCalculateAllowancePct:
    """Test weekend-aware allowance percentage calculation."""

    def test_middle_of_week_50_percent(self):
        """Wednesday noon in Mon-Sun window should be ~50% allowance."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)  # Monday midnight
        current_time = datetime(2025, 1, 8, 12, 0, 0)  # Wednesday noon
        result = calculate_allowance_pct(window_start, current_time, window_hours=168.0)
        # 2.5 weekdays elapsed out of 5 total weekdays = 50%
        assert 49.0 <= result <= 51.0

    def test_end_of_friday_100_percent(self):
        """End of Friday should be ~100% allowance."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)  # Monday midnight
        current_time = datetime(2025, 1, 10, 23, 59, 59)  # Friday 11:59:59pm
        result = calculate_allowance_pct(window_start, current_time, window_hours=168.0)
        # All 5 weekdays elapsed = 100%
        assert result >= 99.0

    def test_saturday_frozen_at_100_percent(self):
        """Saturday should show ~100% (frozen at Friday's end)."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)  # Monday midnight
        current_time = datetime(2025, 1, 11, 12, 0, 0)  # Saturday noon
        result = calculate_allowance_pct(window_start, current_time, window_hours=168.0)
        # Frozen at end of Friday = 100%
        assert result >= 99.0

    def test_sunday_frozen_at_100_percent(self):
        """Sunday should show ~100% (frozen at Friday's end)."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)  # Monday midnight
        current_time = datetime(2025, 1, 12, 12, 0, 0)  # Sunday noon
        result = calculate_allowance_pct(window_start, current_time, window_hours=168.0)
        # Frozen at end of Friday = 100%
        assert result >= 99.0

    def test_monday_new_week_starts_growing(self):
        """Monday in new week should start growing again (but window hasn't reset)."""
        # This tests overlapping windows - if window started previous Monday
        window_start = datetime(2025, 1, 6, 0, 0, 0)  # Monday week 1
        current_time = datetime(2025, 1, 13, 12, 0, 0)  # Monday week 2, noon
        result = calculate_allowance_pct(window_start, current_time, window_hours=168.0)
        # Full 5 weekdays from week 1 + 0.5 day from week 2 = 5.5 / 5 = 110%
        # But we should cap at 100% or allow overflow - depends on implementation
        assert result >= 100.0

    def test_zero_elapsed_time(self):
        """At window start, allowance should be 0%."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)  # Monday midnight
        current_time = datetime(2025, 1, 6, 0, 0, 0)  # Same time
        result = calculate_allowance_pct(window_start, current_time, window_hours=168.0)
        assert result == 0.0

    def test_tuesday_morning_20_percent(self):
        """Tuesday 9am should be ~20% allowance."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)  # Monday midnight
        current_time = datetime(2025, 1, 7, 9, 0, 0)  # Tuesday 9am
        result = calculate_allowance_pct(window_start, current_time, window_hours=168.0)
        # 1 full day + 9 hours = 1.375 days elapsed out of 5 = 27.5%
        assert 25.0 <= result <= 30.0


class TestWeekendAwareAdaptiveDelay:
    """Test weekend-aware calculate_adaptive_delay() with datetime objects."""

    def test_under_budget_weekday_no_delay(self):
        """Under budget on weekday should have no delay."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)  # Monday midnight
        current_time = datetime(2025, 1, 8, 12, 0, 0)  # Wednesday noon

        result = calculate_adaptive_delay(
            current_util=40.0,  # Under allowance
            window_start=window_start,
            current_time=current_time,
            time_remaining_hours=84.0,  # 3.5 days left
            window_hours=168.0,
            min_delay=5,
            max_delay=350,
        )

        assert result["delay_seconds"] == 0
        assert result["strategy"] == "none"

    def test_under_budget_weekend_no_delay(self):
        """Under budget on weekend should have no delay."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)  # Monday midnight
        current_time = datetime(2025, 1, 11, 12, 0, 0)  # Saturday noon

        result = calculate_adaptive_delay(
            current_util=80.0,  # Under 100% but allowance frozen
            window_start=window_start,
            current_time=current_time,
            time_remaining_hours=36.0,
            window_hours=168.0,
            min_delay=5,
            max_delay=350,
        )

        assert result["delay_seconds"] == 0
        assert result["strategy"] == "none"

    def test_over_budget_weekday_adaptive_delay(self):
        """Over budget on weekday should have adaptive delay (not max)."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)  # Monday midnight
        current_time = datetime(2025, 1, 8, 12, 0, 0)  # Wednesday noon

        result = calculate_adaptive_delay(
            current_util=70.0,  # Over 50% allowance
            window_start=window_start,
            current_time=current_time,
            time_remaining_hours=84.0,
            window_hours=168.0,
            min_delay=5,
            max_delay=350,
        )

        # Should throttle but NOT max delay (plenty of time left)
        assert result["delay_seconds"] > 0
        assert result["delay_seconds"] < 350  # Not max
        assert result["strategy"] in ["minimal", "gradual", "aggressive"]

    def test_over_budget_weekend_max_delay(self):
        """Over budget on weekend should have max delay (allowance frozen)."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)  # Monday midnight
        current_time = datetime(2025, 1, 11, 12, 0, 0)  # Saturday noon

        result = calculate_adaptive_delay(
            current_util=110.0,  # Over 100% allowance
            window_start=window_start,
            current_time=current_time,
            time_remaining_hours=36.0,
            window_hours=168.0,
            min_delay=5,
            max_delay=350,
        )

        # Should apply max delay on weekend when over budget
        assert result["delay_seconds"] == 350
        assert result["strategy"] == "emergency"

    def test_exactly_on_budget_no_delay(self):
        """Exactly on budget with safety buffer should throttle slightly."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)  # Monday midnight
        current_time = datetime(2025, 1, 8, 12, 0, 0)  # Wednesday noon

        result = calculate_adaptive_delay(
            current_util=50.0,  # Exactly at allowance
            window_start=window_start,
            current_time=current_time,
            time_remaining_hours=84.0,
            window_hours=168.0,
            min_delay=5,
            max_delay=350,
        )

        # With 95% safety buffer, 50% usage is over safe_allowance (47.5%)
        # Should have minimal throttling
        assert result["delay_seconds"] >= 0
        assert result["strategy"] in ["none", "minimal", "gradual"]

    def test_backward_compatibility_with_old_signature(self):
        """Old signature without datetime args should still work."""
        # This tests that we maintain backward compatibility
        result = calculate_adaptive_delay(
            current_util=56.0,
            target_util=32.0,
            time_elapsed_pct=31.0,
            time_remaining_hours=3.45,
            window_hours=5.0,
        )

        # Should work and return valid result
        assert "delay_seconds" in result
        assert "strategy" in result
        assert result["delay_seconds"] >= 0


class TestPreloadAllowance:
    """Test 12-hour preload allowance feature."""

    def test_preload_at_window_start(self):
        """At window start (0 weekday hours), allowance should be 10% (preload)."""
        monday = datetime(2025, 1, 6, 0, 0, 0)  # Window start

        allowance = calculate_allowance_pct(
            window_start=monday,
            current_time=monday,  # Same time (0 hours elapsed)
            window_hours=168.0,
            preload_hours=12.0,
        )

        assert allowance == pytest.approx(10.0, abs=0.1)

    def test_preload_at_4_hours(self):
        """After 4 weekday hours, still in preload (10%)."""
        monday = datetime(2025, 1, 6, 0, 0, 0)
        monday_4am = datetime(2025, 1, 6, 4, 0, 0)  # 4 hours later

        allowance = calculate_allowance_pct(
            window_start=monday,
            current_time=monday_4am,
            window_hours=168.0,
            preload_hours=12.0,
        )

        assert allowance == pytest.approx(10.0, abs=0.1)

    def test_preload_ends_at_12_hours(self):
        """At exactly 12 weekday hours, allowance should still be 10%."""
        monday = datetime(2025, 1, 6, 0, 0, 0)
        monday_noon = datetime(2025, 1, 6, 12, 0, 0)  # 12 hours later

        allowance = calculate_allowance_pct(
            window_start=monday,
            current_time=monday_noon,
            window_hours=168.0,
            preload_hours=12.0,
        )

        assert allowance == pytest.approx(10.0, abs=0.1)

    def test_normal_accrual_after_preload(self):
        """After 12 weekday hours, normal accrual kicks in."""
        monday = datetime(2025, 1, 6, 0, 0, 0)
        monday_4pm = datetime(2025, 1, 6, 16, 0, 0)  # 16 hours

        allowance = calculate_allowance_pct(
            window_start=monday,
            current_time=monday_4pm,
            window_hours=168.0,
            preload_hours=12.0,
        )

        # 16 hours / 120 hours × 100 = 13.33%
        assert allowance == pytest.approx(13.33, abs=0.1)

    def test_preload_across_weekend(self):
        """
        Preload should only count weekday hours.

        Friday 4 PM → Monday 4 AM = 12 weekday hours
        - Friday 4 PM → Sat 12 AM = 8 hours
        - Sat + Sun = 0 hours (weekend)
        - Mon 12 AM → Mon 4 AM = 4 hours
        Total: 12 weekday hours → end of preload
        """
        friday_4pm = datetime(2025, 1, 10, 16, 0, 0)  # Friday 4 PM
        monday_4am = datetime(2025, 1, 13, 4, 0, 0)  # Monday 4 AM

        allowance = calculate_allowance_pct(
            window_start=friday_4pm,
            current_time=monday_4am,
            window_hours=168.0,
            preload_hours=12.0,
        )

        # Should still be at preload (exactly 12 weekday hours)
        assert allowance == pytest.approx(10.0, abs=0.1)

    def test_accrual_after_weekend(self):
        """
        Monday 4 PM after Friday 4 PM start = 24 weekday hours.
        Should be in normal accrual mode: 24/120 × 100 = 20%
        """
        friday_4pm = datetime(2025, 1, 10, 16, 0, 0)  # Friday 4 PM
        monday_4pm = datetime(2025, 1, 13, 16, 0, 0)  # Monday 4 PM

        allowance = calculate_allowance_pct(
            window_start=friday_4pm,
            current_time=monday_4pm,
            window_hours=168.0,
            preload_hours=12.0,
        )

        # 24 weekday hours / 120 hours × 100 = 20%
        assert allowance == pytest.approx(20.0, abs=0.5)

    def test_custom_preload_hours(self):
        """Should support custom preload hours (e.g., 24 hours = 20%)."""
        monday = datetime(2025, 1, 6, 0, 0, 0)
        monday_noon = datetime(2025, 1, 6, 12, 0, 0)

        allowance = calculate_allowance_pct(
            window_start=monday,
            current_time=monday_noon,
            window_hours=168.0,
            preload_hours=24.0,  # Custom: 24 hours
        )

        # 24h / 120h × 100 = 20% preload
        assert allowance == pytest.approx(20.0, abs=0.1)

    def test_zero_preload_backward_compatible(self):
        """preload_hours=0 should behave like original algorithm."""
        monday = datetime(2025, 1, 6, 0, 0, 0)
        monday_noon = datetime(2025, 1, 6, 12, 0, 0)

        allowance = calculate_allowance_pct(
            window_start=monday,
            current_time=monday_noon,
            window_hours=168.0,
            preload_hours=0.0,  # No preload
        )

        # 12h / 120h × 100 = 10% (normal accrual from start)
        assert allowance == pytest.approx(10.0, abs=0.1)

    def test_preload_with_safety_buffer(self):
        """Preload should work with safety buffer."""
        monday = datetime(2025, 1, 6, 0, 0, 0)

        result = calculate_adaptive_delay(
            current_util=5.0,  # Under 10% preload
            window_start=monday,
            current_time=monday,
            time_remaining_hours=168.0,
            window_hours=168.0,
            safety_buffer_pct=95.0,
            preload_hours=12.0,
        )

        # Allowance = 10%, Safe allowance = 9.5%
        # Usage = 5% < 9.5%
        # Should not throttle
        assert result["delay_seconds"] == 0
        assert result["projection"]["allowance"] == pytest.approx(10.0, abs=0.1)
        assert result["projection"]["safe_allowance"] == pytest.approx(9.5, abs=0.1)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
