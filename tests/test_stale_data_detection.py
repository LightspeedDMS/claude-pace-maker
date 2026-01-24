#!/usr/bin/env python3
"""
Tests for stale data detection in calculate_time_percent().

These tests verify that the calculator correctly detects and handles
stale data scenarios where resets_at timestamps are in the past.
"""

import pytest
from datetime import datetime, timedelta
from pathlib import Path
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pacemaker import calculator


class TestCalculateTimePercentStaleData:
    """Tests for stale data detection in calculate_time_percent()."""

    def test_normal_case_resets_at_in_future_returns_valid_percent(self):
        """When resets_at is in the future, should return normal percentage."""
        resets_at = datetime.utcnow() + timedelta(hours=2.5)
        result = calculator.calculate_time_percent(resets_at, window_hours=5.0)
        assert 49.0 <= result <= 51.0, f"Expected ~50%, got {result}%"

    def test_edge_case_resets_at_just_passed_within_5_min_returns_100(self):
        """When resets_at just passed (within 5 min), should return 100%."""
        resets_at = datetime.utcnow() - timedelta(minutes=3)
        result = calculator.calculate_time_percent(resets_at, window_hours=5.0)
        assert result == 100.0, f"Expected 100%, got {result}%"

    def test_stale_case_resets_at_more_than_5_min_past_returns_sentinel(self):
        """When resets_at is more than 5 min in past, return -1.0 sentinel."""
        resets_at = datetime.utcnow() - timedelta(minutes=6)
        result = calculator.calculate_time_percent(resets_at, window_hours=5.0)
        assert result == -1.0, f"Expected -1.0 (stale sentinel), got {result}"


class TestPacingDecisionStaleDataHandling:
    """Tests for stale data handling in calculate_pacing_decision()."""

    def test_stale_5hour_data_returns_stale_flag(self):
        """When 5-hour resets_at is stale, should return stale_data flag."""
        from pacemaker import pacing_engine

        five_hour_resets_at = datetime.utcnow() - timedelta(minutes=10)
        seven_day_resets_at = datetime.utcnow() + timedelta(days=3)

        result = pacing_engine.calculate_pacing_decision(
            five_hour_util=38.0,
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=45.0,
            seven_day_resets_at=seven_day_resets_at,
        )

        assert result.get("stale_data") is True
        assert result.get("should_throttle") is False

    def test_stale_7day_data_returns_stale_flag(self):
        """When 7-day resets_at is stale, should return stale_data flag."""
        from pacemaker import pacing_engine

        five_hour_resets_at = datetime.utcnow() + timedelta(hours=2)
        seven_day_resets_at = datetime.utcnow() - timedelta(minutes=10)

        result = pacing_engine.calculate_pacing_decision(
            five_hour_util=38.0,
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=45.0,
            seven_day_resets_at=seven_day_resets_at,
        )

        assert result.get("stale_data") is True
        assert result.get("should_throttle") is False

    def test_valid_data_no_stale_flag(self):
        """When data is valid, should not set stale_data flag."""
        from pacemaker import pacing_engine

        five_hour_resets_at = datetime.utcnow() + timedelta(hours=2)
        seven_day_resets_at = datetime.utcnow() + timedelta(days=3)

        result = pacing_engine.calculate_pacing_decision(
            five_hour_util=60.0,
            five_hour_resets_at=five_hour_resets_at,
            seven_day_util=45.0,
            seven_day_resets_at=seven_day_resets_at,
        )

        assert result.get("stale_data") is not True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
