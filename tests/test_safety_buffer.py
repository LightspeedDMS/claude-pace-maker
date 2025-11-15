#!/usr/bin/env python3
"""
Test suite for 95% safety buffer feature.

Tests that throttling starts when usage exceeds 95% of allowance,
leaving a 5% safety buffer to prevent hitting hard credit limits.
"""

import pytest
from datetime import datetime, timedelta
from src.pacemaker.adaptive_throttle import (
    calculate_adaptive_delay,
    calculate_allowance_pct
)


class TestSafetyBufferThreshold:
    """Test safety buffer threshold detection."""

    def test_under_safety_buffer_no_throttle(self):
        """Usage below 95% of allowance should not throttle."""
        # Wednesday noon: 50% allowance
        # Current usage: 45% (90% of allowance - under 95% threshold)
        # Expected: No throttle
        window_start = datetime(2025, 1, 6, 0, 0, 0)   # Monday midnight
        current_time = datetime(2025, 1, 8, 12, 0, 0)  # Wednesday noon

        result = calculate_adaptive_delay(
            current_util=45.0,
            window_start=window_start,
            current_time=current_time,
            time_remaining_hours=96.0,
            window_hours=168.0,
            safety_buffer_pct=95.0
        )

        assert result['delay_seconds'] == 0
        assert result['strategy'] == 'none'
        assert result['projection']['safe_allowance'] == pytest.approx(47.5, abs=0.5)  # 50% * 95%
        assert result['projection']['buffer_remaining'] == pytest.approx(2.5, abs=0.5)  # 47.5 - 45

    def test_exactly_at_safety_buffer_no_throttle(self):
        """Usage exactly at 95% of allowance should not throttle."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)   # Monday midnight
        current_time = datetime(2025, 1, 8, 12, 0, 0)  # Wednesday noon

        result = calculate_adaptive_delay(
            current_util=47.5,  # Exactly 95% of 50% allowance
            window_start=window_start,
            current_time=current_time,
            time_remaining_hours=96.0,
            window_hours=168.0,
            safety_buffer_pct=95.0
        )

        assert result['delay_seconds'] == 0
        assert result['strategy'] == 'none'

    def test_over_safety_buffer_throttle(self):
        """Usage over 95% of allowance should throttle."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)   # Monday midnight
        current_time = datetime(2025, 1, 8, 12, 0, 0)  # Wednesday noon

        result = calculate_adaptive_delay(
            current_util=48.0,  # 96% of 50% allowance - over safety buffer
            window_start=window_start,
            current_time=current_time,
            time_remaining_hours=96.0,
            window_hours=168.0,
            safety_buffer_pct=95.0
        )

        assert result['delay_seconds'] > 0
        assert result['strategy'] in ['minimal', 'gradual', 'aggressive', 'emergency']
        assert result['projection']['safe_allowance'] == pytest.approx(47.5, abs=0.5)

    def test_legacy_mode_safety_buffer(self):
        """Legacy mode (without datetime) should also support safety buffer."""
        result = calculate_adaptive_delay(
            current_util=47.5,  # 95% of 50% target
            target_util=50.0,
            time_elapsed_pct=50.0,
            time_remaining_hours=2.5,
            window_hours=5.0,
            safety_buffer_pct=95.0
        )

        assert result['delay_seconds'] == 0
        assert result['strategy'] == 'none'

        # Test over buffer with more significant overage
        result = calculate_adaptive_delay(
            current_util=52.0,  # Significantly over 95% of 50% target (47.5)
            target_util=50.0,
            time_elapsed_pct=50.0,
            time_remaining_hours=2.5,
            window_hours=5.0,
            safety_buffer_pct=95.0
        )

        # With 52% usage at 50% elapsed, we're overshooting the 95% target
        # This should trigger throttling
        assert result['delay_seconds'] > 0


class TestWeekendSafetyBuffer:
    """Test safety buffer on weekends."""

    def test_weekend_safety_buffer_throttle(self):
        """Weekend should freeze allowance but still apply 95% safety buffer."""
        # Saturday: 100% allowance (frozen at Friday end)
        # Current usage: 96%
        # Safe threshold: 95% of 100% = 95%
        # Expected: Throttle (96% > 95%)
        window_start = datetime(2025, 1, 6, 0, 0, 0)    # Monday midnight
        current_time = datetime(2025, 1, 11, 12, 0, 0)  # Saturday noon

        result = calculate_adaptive_delay(
            current_util=96.0,
            window_start=window_start,
            current_time=current_time,
            time_remaining_hours=36.0,
            window_hours=168.0,
            safety_buffer_pct=95.0
        )

        assert result['delay_seconds'] > 0  # Should throttle
        assert result['projection']['safe_allowance'] == pytest.approx(95.0, abs=1.0)  # 100% * 95%

    def test_weekend_under_safety_buffer_no_throttle(self):
        """Weekend with usage under 95% should not throttle."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)    # Monday midnight
        current_time = datetime(2025, 1, 11, 12, 0, 0)  # Saturday noon

        result = calculate_adaptive_delay(
            current_util=90.0,  # Under 95%
            window_start=window_start,
            current_time=current_time,
            time_remaining_hours=36.0,
            window_hours=168.0,
            safety_buffer_pct=95.0
        )

        assert result['delay_seconds'] == 0
        assert result['strategy'] == 'none'


class TestTargetEndpoint:
    """Test that algorithm targets 95% endpoint, not 100%."""

    def test_target_endpoint_95_percent(self):
        """Algorithm should project to 95% endpoint, not 100%."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)   # Monday midnight
        current_time = datetime(2025, 1, 8, 12, 0, 0)  # Wednesday noon

        result = calculate_adaptive_delay(
            current_util=48.0,
            window_start=window_start,
            current_time=current_time,
            time_remaining_hours=96.0,
            window_hours=168.0,
            safety_buffer_pct=95.0
        )

        # Conservative target should be 95%, not 100%
        # This is reflected in util_if_throttled being ~95%
        assert result['projection']['util_if_throttled'] <= 95.0

    def test_projected_utilization_respects_safety_buffer(self):
        """Throttled projection should not exceed 95%."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)   # Monday midnight
        current_time = datetime(2025, 1, 7, 12, 0, 0)  # Tuesday noon

        result = calculate_adaptive_delay(
            current_util=50.0,  # High early usage
            window_start=window_start,
            current_time=current_time,
            time_remaining_hours=132.0,
            window_hours=168.0,
            safety_buffer_pct=95.0
        )

        # Should project to 95% or less
        if result['delay_seconds'] > 0:
            assert result['projection']['util_if_throttled'] <= 96.0  # Allow small margin


class TestCustomSafetyBuffer:
    """Test custom safety buffer percentages."""

    def test_custom_safety_buffer_90_percent(self):
        """Should support custom safety buffer percentage (90%)."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)   # Monday midnight
        current_time = datetime(2025, 1, 8, 12, 0, 0)  # Wednesday noon

        result = calculate_adaptive_delay(
            current_util=46.0,
            window_start=window_start,
            current_time=current_time,
            time_remaining_hours=96.0,
            window_hours=168.0,
            safety_buffer_pct=90.0  # Custom buffer
        )

        assert result['projection']['safe_allowance'] == pytest.approx(45.0, abs=0.5)  # 50% * 90%
        assert result['delay_seconds'] > 0  # 46% > 45%, should throttle

    def test_custom_safety_buffer_98_percent(self):
        """Should support higher safety buffer (98%) for less conservative throttling."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)   # Monday midnight
        current_time = datetime(2025, 1, 8, 12, 0, 0)  # Wednesday noon

        result = calculate_adaptive_delay(
            current_util=48.0,
            window_start=window_start,
            current_time=current_time,
            time_remaining_hours=96.0,
            window_hours=168.0,
            safety_buffer_pct=98.0  # Less conservative
        )

        assert result['projection']['safe_allowance'] == pytest.approx(49.0, abs=0.5)  # 50% * 98%
        assert result['delay_seconds'] == 0  # 48% < 49%, no throttle

    def test_default_safety_buffer_95_percent(self):
        """Default safety buffer should be 95% when not specified."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)   # Monday midnight
        current_time = datetime(2025, 1, 8, 12, 0, 0)  # Wednesday noon

        # Call without safety_buffer_pct parameter
        result = calculate_adaptive_delay(
            current_util=48.0,
            window_start=window_start,
            current_time=current_time,
            time_remaining_hours=96.0,
            window_hours=168.0
            # No safety_buffer_pct - should default to 95.0
        )

        # Should use 95% default
        assert result['projection']['safe_allowance'] == pytest.approx(47.5, abs=0.5)  # 50% * 95%


class TestProjectionData:
    """Test that projection data includes safety buffer info."""

    def test_projection_includes_safe_allowance(self):
        """Projection should include safe_allowance field."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)   # Monday midnight
        current_time = datetime(2025, 1, 8, 12, 0, 0)  # Wednesday noon

        result = calculate_adaptive_delay(
            current_util=45.0,
            window_start=window_start,
            current_time=current_time,
            time_remaining_hours=96.0,
            window_hours=168.0,
            safety_buffer_pct=95.0
        )

        assert 'safe_allowance' in result['projection']
        assert isinstance(result['projection']['safe_allowance'], float)

    def test_projection_includes_buffer_remaining(self):
        """Projection should include buffer_remaining field."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)   # Monday midnight
        current_time = datetime(2025, 1, 8, 12, 0, 0)  # Wednesday noon

        result = calculate_adaptive_delay(
            current_util=45.0,
            window_start=window_start,
            current_time=current_time,
            time_remaining_hours=96.0,
            window_hours=168.0,
            safety_buffer_pct=95.0
        )

        assert 'buffer_remaining' in result['projection']
        assert isinstance(result['projection']['buffer_remaining'], float)
        # Buffer remaining = safe_allowance - current_util
        expected = result['projection']['safe_allowance'] - 45.0
        assert result['projection']['buffer_remaining'] == pytest.approx(expected, abs=0.1)

    def test_projection_includes_allowance(self):
        """Projection should include raw allowance (before safety buffer)."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)   # Monday midnight
        current_time = datetime(2025, 1, 8, 12, 0, 0)  # Wednesday noon

        result = calculate_adaptive_delay(
            current_util=45.0,
            window_start=window_start,
            current_time=current_time,
            time_remaining_hours=96.0,
            window_hours=168.0,
            safety_buffer_pct=95.0
        )

        assert 'allowance' in result['projection']
        assert result['projection']['allowance'] == pytest.approx(50.0, abs=0.5)


class TestSafetyBufferEdgeCases:
    """Test edge cases for safety buffer logic."""

    def test_100_percent_safety_buffer(self):
        """100% safety buffer should be same as no buffer (original behavior)."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)   # Monday midnight
        current_time = datetime(2025, 1, 8, 12, 0, 0)  # Wednesday noon

        result = calculate_adaptive_delay(
            current_util=50.0,
            window_start=window_start,
            current_time=current_time,
            time_remaining_hours=96.0,
            window_hours=168.0,
            safety_buffer_pct=100.0
        )

        assert result['delay_seconds'] == 0  # At 100% of allowance = no throttle
        assert result['projection']['safe_allowance'] == pytest.approx(50.0, abs=0.5)

    def test_very_low_safety_buffer(self):
        """Very low safety buffer (50%) should throttle aggressively."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)   # Monday midnight
        current_time = datetime(2025, 1, 8, 12, 0, 0)  # Wednesday noon

        result = calculate_adaptive_delay(
            current_util=26.0,
            window_start=window_start,
            current_time=current_time,
            time_remaining_hours=96.0,
            window_hours=168.0,
            safety_buffer_pct=50.0
        )

        assert result['projection']['safe_allowance'] == pytest.approx(25.0, abs=0.5)  # 50% * 50%
        assert result['delay_seconds'] > 0  # 26% > 25%, should throttle

    def test_zero_utilization_with_safety_buffer(self):
        """Zero utilization should not throttle regardless of safety buffer."""
        window_start = datetime(2025, 1, 6, 0, 0, 0)   # Monday midnight
        current_time = datetime(2025, 1, 8, 12, 0, 0)  # Wednesday noon

        result = calculate_adaptive_delay(
            current_util=0.0,
            window_start=window_start,
            current_time=current_time,
            time_remaining_hours=96.0,
            window_hours=168.0,
            safety_buffer_pct=95.0
        )

        assert result['delay_seconds'] == 0
        assert result['projection']['buffer_remaining'] > 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
