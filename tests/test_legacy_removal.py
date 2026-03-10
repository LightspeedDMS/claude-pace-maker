#!/usr/bin/env python3
"""
Tests for story #47: Remove legacy pacing algorithm dead code.

Acceptance criteria:
  Scenario 1: Adaptive algorithm runs unconditionally when a window is active
  Scenario 2: Both limiters disabled returns zero throttle without legacy fallthrough
  Scenario 3: No 'algorithm' key in pacing decision (monitor display omits Algorithm line)
  Scenario 4: calculate_pacing_decision signature rejects use_adaptive
  Scenario 5: Legacy calculator functions are removed
"""

import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pacemaker import pacing_engine, calculator


def _future_reset(hours: float = 2.5) -> datetime:
    """Return a future reset time ``hours`` from now (UTC)."""
    return datetime.now(timezone.utc) + timedelta(hours=hours)


class TestScenario1_AdaptiveRunsUnconditionally:
    """Scenario 1: Adaptive algorithm runs unconditionally when a window is active."""

    def test_return_dict_contains_strategy_not_algorithm_for_active_5h_window(self):
        """When 5-hour window is active, return dict has 'strategy' but NOT 'algorithm'."""
        result = pacing_engine.calculate_pacing_decision(
            five_hour_util=80.0,
            five_hour_resets_at=_future_reset(2.5),
            seven_day_util=30.0,
            seven_day_resets_at=_future_reset(80.0),
        )
        assert "strategy" in result, "Result must contain 'strategy' key"
        assert "algorithm" not in result, "Result must NOT contain 'algorithm' key"

    def test_return_dict_contains_strategy_not_algorithm_for_active_7d_window(self):
        """When 7-day window is active, return dict has 'strategy' but NOT 'algorithm'."""
        result = pacing_engine.calculate_pacing_decision(
            five_hour_util=10.0,
            five_hour_resets_at=_future_reset(4.0),
            seven_day_util=90.0,
            seven_day_resets_at=_future_reset(100.0),
        )
        assert "strategy" in result, "Result must contain 'strategy' key"
        assert "algorithm" not in result, "Result must NOT contain 'algorithm' key"

    def test_return_dict_contains_strategy_not_algorithm_for_both_windows_active(self):
        """When both windows active, return dict has 'strategy' but NOT 'algorithm'."""
        result = pacing_engine.calculate_pacing_decision(
            five_hour_util=85.0,
            five_hour_resets_at=_future_reset(1.0),
            seven_day_util=85.0,
            seven_day_resets_at=_future_reset(50.0),
        )
        assert "strategy" in result, "Result must contain 'strategy' key"
        assert "algorithm" not in result, "Result must NOT contain 'algorithm' key"

    def test_projection_key_is_preserved(self):
        """The 'projection' key must still be present in result (used by adaptive)."""
        result = pacing_engine.calculate_pacing_decision(
            five_hour_util=80.0,
            five_hour_resets_at=_future_reset(2.5),
            seven_day_util=30.0,
            seven_day_resets_at=_future_reset(80.0),
        )
        assert (
            "projection" in result
        ), "Result must contain 'projection' key (adaptive output)"


class TestScenario2_BothLimitersDisabled:
    """Scenario 2: Both limiters disabled returns zero throttle without legacy fallthrough."""

    def test_both_disabled_constrained_window_is_none(self):
        """Both limiters disabled: constrained_window is None."""
        result = pacing_engine.calculate_pacing_decision(
            five_hour_util=80.0,
            five_hour_resets_at=_future_reset(2.5),
            seven_day_util=80.0,
            seven_day_resets_at=_future_reset(80.0),
            five_hour_limit_enabled=False,
            weekly_limit_enabled=False,
        )
        assert result["constrained_window"] is None

    def test_both_disabled_delay_is_zero(self):
        """Both limiters disabled: delay_seconds is 0."""
        result = pacing_engine.calculate_pacing_decision(
            five_hour_util=99.0,
            five_hour_resets_at=_future_reset(2.5),
            seven_day_util=99.0,
            seven_day_resets_at=_future_reset(80.0),
            five_hour_limit_enabled=False,
            weekly_limit_enabled=False,
        )
        assert result["delay_seconds"] == 0

    def test_both_disabled_should_not_throttle(self):
        """Both limiters disabled: should_throttle is False."""
        result = pacing_engine.calculate_pacing_decision(
            five_hour_util=99.0,
            five_hour_resets_at=_future_reset(2.5),
            seven_day_util=99.0,
            seven_day_resets_at=_future_reset(80.0),
            five_hour_limit_enabled=False,
            weekly_limit_enabled=False,
        )
        assert result["should_throttle"] is False

    def test_both_disabled_no_algorithm_key(self):
        """Both limiters disabled: result dict does NOT contain 'algorithm' key."""
        result = pacing_engine.calculate_pacing_decision(
            five_hour_util=99.0,
            five_hour_resets_at=_future_reset(2.5),
            seven_day_util=99.0,
            seven_day_resets_at=_future_reset(80.0),
            five_hour_limit_enabled=False,
            weekly_limit_enabled=False,
        )
        assert "algorithm" not in result, "Result must NOT contain 'algorithm' key"


class TestScenario3_NoPacingDecisionAlgorithmKey:
    """Scenario 3: Pacing decision return dict never contains 'algorithm' key.

    The monitor display omits the Algorithm line because pacemaker_integration.py
    stops populating the 'algorithm' key in the status dict. The upstream source
    of that value is calculate_pacing_decision() — this class tests that the key
    is absent from every code path in the pacing engine, so the integration layer
    has nothing to forward to the display.
    """

    def test_no_algorithm_key_when_throttling(self):
        """When throttling is active (high util), no 'algorithm' key in result."""
        result = pacing_engine.calculate_pacing_decision(
            five_hour_util=95.0,
            five_hour_resets_at=_future_reset(1.0),
            seven_day_util=30.0,
            seven_day_resets_at=_future_reset(80.0),
        )
        assert "algorithm" not in result

    def test_no_algorithm_key_when_not_throttling(self):
        """When well within limits (low util), no 'algorithm' key in result."""
        result = pacing_engine.calculate_pacing_decision(
            five_hour_util=5.0,
            five_hour_resets_at=_future_reset(4.0),
            seven_day_util=5.0,
            seven_day_resets_at=_future_reset(100.0),
        )
        assert "algorithm" not in result

    def test_no_algorithm_key_with_only_5h_limiter_active(self):
        """Only 5-hour limiter active: no 'algorithm' key."""
        result = pacing_engine.calculate_pacing_decision(
            five_hour_util=80.0,
            five_hour_resets_at=_future_reset(2.0),
            seven_day_util=50.0,
            seven_day_resets_at=_future_reset(80.0),
            weekly_limit_enabled=False,
        )
        assert "algorithm" not in result

    def test_no_algorithm_key_with_only_7d_limiter_active(self):
        """Only 7-day limiter active: no 'algorithm' key."""
        result = pacing_engine.calculate_pacing_decision(
            five_hour_util=80.0,
            five_hour_resets_at=_future_reset(2.0),
            seven_day_util=50.0,
            seven_day_resets_at=_future_reset(80.0),
            five_hour_limit_enabled=False,
        )
        assert "algorithm" not in result


class TestScenario4_SignatureRejectsUseAdaptive:
    """Scenario 4: calculate_pacing_decision signature rejects use_adaptive."""

    def test_passing_use_adaptive_raises_type_error(self):
        """Passing use_adaptive=True as kwarg must raise TypeError."""
        with pytest.raises(TypeError):
            pacing_engine.calculate_pacing_decision(
                five_hour_util=50.0,
                five_hour_resets_at=_future_reset(2.5),
                seven_day_util=30.0,
                seven_day_resets_at=_future_reset(80.0),
                use_adaptive=True,
            )

    def test_use_adaptive_not_in_signature(self):
        """use_adaptive must not appear in the function signature."""
        import inspect

        sig = inspect.signature(pacing_engine.calculate_pacing_decision)
        assert "use_adaptive" not in sig.parameters


class TestScenario5_LegacyCalculatorFunctionsRemoved:
    """Scenario 5: Legacy calculator functions are removed."""

    def test_calculate_logarithmic_target_does_not_exist(self):
        """calculate_logarithmic_target must not be an attribute of calculator module."""
        assert not hasattr(
            calculator, "calculate_logarithmic_target"
        ), "calculate_logarithmic_target is legacy dead code and must be removed"

    def test_calculate_delay_does_not_exist(self):
        """calculate_delay must not be an attribute of calculator module."""
        assert not hasattr(
            calculator, "calculate_delay"
        ), "calculate_delay is legacy dead code and must be removed"

    def test_calculate_linear_target_still_exists(self):
        """calculate_linear_target must still exist (used by manual_e2e_test.py)."""
        assert hasattr(calculator, "calculate_linear_target")
        assert calculator.calculate_linear_target(50.0) == 50.0
        assert calculator.calculate_linear_target(0.0) == 0.0
        assert calculator.calculate_linear_target(100.0) == 100.0

    def test_calculate_time_percent_still_exists(self):
        """calculate_time_percent must still exist and work correctly."""
        assert hasattr(calculator, "calculate_time_percent")
        # Window not yet started (resets_at in far future beyond window size)
        far_future = _future_reset(hours=10.0)
        result = calculator.calculate_time_percent(far_future, window_hours=5.0)
        assert result == 0.0  # Window hasn't started

    def test_determine_most_constrained_window_still_exists(self):
        """determine_most_constrained_window must still exist and work correctly."""
        assert hasattr(calculator, "determine_most_constrained_window")
        result = calculator.determine_most_constrained_window(
            five_hour_util=70.0,
            five_hour_target=50.0,
            seven_day_util=60.0,
            seven_day_target=50.0,
        )
        assert result["window"] == "5-hour"


class TestStaleDataPath:
    """Stale data path must not contain 'algorithm' key."""

    def test_stale_data_return_does_not_contain_algorithm(self):
        """When both windows are stale, return dict must not have 'algorithm' key."""
        # Both reset times are well in the past (stale: > 5 min past)
        past_time = datetime.now(timezone.utc) - timedelta(hours=1)
        result = pacing_engine.calculate_pacing_decision(
            five_hour_util=50.0,
            five_hour_resets_at=past_time,
            seven_day_util=50.0,
            seven_day_resets_at=past_time,
        )
        assert (
            result.get("stale_data") is True
        ), "Both stale should produce stale_data=True"
        assert (
            "algorithm" not in result
        ), "Stale data path must NOT contain 'algorithm' key"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
