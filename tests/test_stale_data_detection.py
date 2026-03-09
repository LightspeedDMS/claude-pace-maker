#!/usr/bin/env python3
"""
Tests for stale data detection in calculate_time_percent().

These tests verify that the calculator correctly detects and handles
stale data scenarios where resets_at timestamps are in the past.
"""

import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pacemaker import calculator


class TestCalculateTimePercentStaleData:
    """Tests for stale data detection in calculate_time_percent()."""

    def test_normal_case_resets_at_in_future_returns_valid_percent(self):
        """When resets_at is in the future, should return normal percentage."""
        resets_at = datetime.now(timezone.utc) + timedelta(hours=2.5)
        result = calculator.calculate_time_percent(resets_at, window_hours=5.0)
        assert 49.0 <= result <= 51.0, f"Expected ~50%, got {result}%"

    def test_edge_case_resets_at_just_passed_within_5_min_returns_100(self):
        """When resets_at just passed (within 5 min), should return 100%."""
        resets_at = datetime.now(timezone.utc) - timedelta(minutes=3)
        result = calculator.calculate_time_percent(resets_at, window_hours=5.0)
        assert result == 100.0, f"Expected 100%, got {result}%"

    def test_stale_case_resets_at_more_than_5_min_past_returns_sentinel(self):
        """When resets_at is more than 5 min in past, return -1.0 sentinel."""
        resets_at = datetime.now(timezone.utc) - timedelta(minutes=6)
        result = calculator.calculate_time_percent(resets_at, window_hours=5.0)
        assert result == -1.0, f"Expected -1.0 (stale sentinel), got {result}"


class TestPacingDecisionStaleDataHandling:
    """Tests for stale data handling in calculate_pacing_decision()."""

    def test_stale_5hour_valid_7day_uses_7day_window(self):
        """When 5-hour is stale but 7-day valid, 7-day window takes over."""
        from pacemaker import pacing_engine

        five_hour_resets_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        seven_day_resets_at = datetime.now(timezone.utc) + timedelta(days=3)

        result = pacing_engine.calculate_pacing_decision(
            five_hour_util=38.0,
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=45.0,
            seven_day_resets_at=seven_day_resets_at,
        )

        # Stale 5h doesn't kill the whole result — 7d takes over
        assert result.get("stale_data") is not True
        assert result.get("constrained_window") == "7-day"
        assert result["seven_day"]["target"] > 0

    def test_stale_7day_valid_5hour_uses_5hour_window(self):
        """When 7-day is stale but 5-hour valid, 5-hour window takes over."""
        from pacemaker import pacing_engine

        five_hour_resets_at = datetime.now(timezone.utc) + timedelta(hours=2)
        seven_day_resets_at = datetime.now(timezone.utc) - timedelta(minutes=10)

        result = pacing_engine.calculate_pacing_decision(
            five_hour_util=38.0,
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=45.0,
            seven_day_resets_at=seven_day_resets_at,
        )

        # Stale 7d doesn't kill the whole result — 5h takes over
        assert result.get("stale_data") is not True
        assert result.get("constrained_window") == "5-hour"
        assert result["five_hour"]["target"] > 0

    def test_both_windows_stale_returns_stale_flag(self):
        """When both 5-hour and 7-day are stale, should return stale_data flag."""
        from pacemaker import pacing_engine

        five_hour_resets_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        seven_day_resets_at = datetime.now(timezone.utc) - timedelta(minutes=10)

        result = pacing_engine.calculate_pacing_decision(
            five_hour_util=38.0,
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=45.0,
            seven_day_resets_at=seven_day_resets_at,
        )

        assert result.get("stale_data") is True
        assert result.get("should_throttle") is False
        assert result.get("constrained_window") is None

    def test_valid_data_no_stale_flag(self):
        """When data is valid, should not set stale_data flag."""
        from pacemaker import pacing_engine

        five_hour_resets_at = datetime.now(timezone.utc) + timedelta(hours=2)
        seven_day_resets_at = datetime.now(timezone.utc) + timedelta(days=3)

        result = pacing_engine.calculate_pacing_decision(
            five_hour_util=60.0,
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=45.0,
            seven_day_resets_at=seven_day_resets_at,
        )

        assert result.get("stale_data") is not True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
