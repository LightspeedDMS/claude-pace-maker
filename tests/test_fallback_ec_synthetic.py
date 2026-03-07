#!/usr/bin/env python3
"""
Edge-case tests for synthetic calculation and window projection:
calculate_synthetic, _project_window, calculate_synthetic_with_rollover.
"""

import time
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pacemaker.fallback import (
    _default_state,
    calculate_synthetic,
    _project_window,
    calculate_synthetic_with_rollover,
    _DEFAULT_TOKEN_COSTS,
)


# ---------------------------------------------------------------------------
# calculate_synthetic
# ---------------------------------------------------------------------------
class TestCalculateSyntheticEdgeCases:
    def _state(self, baseline_5h=0.0, baseline_7d=0.0, cost=0.0):
        s = _default_state()
        s["state"] = "fallback"
        s["baseline_5h"] = baseline_5h
        s["baseline_7d"] = baseline_7d
        s["accumulated_cost"] = cost
        return s

    def test_zero_cost_returns_baselines(self):
        result = calculate_synthetic(
            self._state(45.0, 30.0, 0.0), "5x", _DEFAULT_TOKEN_COSTS
        )
        assert result["synthetic_5h"] == pytest.approx(45.0)
        assert result["synthetic_7d"] == pytest.approx(30.0)

    def test_small_cost_increases_proportionally(self):
        result = calculate_synthetic(
            self._state(45.0, 30.0, 1.0), "5x", _DEFAULT_TOKEN_COSTS
        )
        assert result["synthetic_5h"] == pytest.approx(45.75)
        assert result["synthetic_7d"] == pytest.approx(30.11)

    def test_large_cost_caps_at_100(self):
        result = calculate_synthetic(
            self._state(50.0, 50.0, 10000.0), "5x", _DEFAULT_TOKEN_COSTS
        )
        assert result["synthetic_5h"] == 100.0
        assert result["synthetic_7d"] == 100.0

    def test_huge_cost_still_exactly_100(self):
        result = calculate_synthetic(
            self._state(0.0, 0.0, 1_000_000.0), "5x", _DEFAULT_TOKEN_COSTS
        )
        assert result["synthetic_5h"] == 100.0
        assert result["synthetic_7d"] == 100.0

    def test_negative_cost_subtracts(self):
        result = calculate_synthetic(
            self._state(50.0, 50.0, -10.0), "5x", _DEFAULT_TOKEN_COSTS
        )
        assert result["synthetic_5h"] < 50.0

    def test_none_baselines_treated_as_zero(self):
        state = _default_state()
        state["state"] = "fallback"
        state["accumulated_cost"] = 1.0
        state["baseline_5h"] = None
        state["baseline_7d"] = None
        result = calculate_synthetic(state, "5x", _DEFAULT_TOKEN_COSTS)
        assert result["synthetic_5h"] == pytest.approx(0.75)

    def test_unknown_tier_falls_back_to_5x(self):
        r_unk = calculate_synthetic(
            self._state(10.0, 10.0, 5.0), "99x", _DEFAULT_TOKEN_COSTS
        )
        r_5x = calculate_synthetic(
            self._state(10.0, 10.0, 5.0), "5x", _DEFAULT_TOKEN_COSTS
        )
        assert r_unk["synthetic_5h"] == pytest.approx(r_5x["synthetic_5h"])

    def test_5x_higher_than_20x_same_cost(self):
        r5 = calculate_synthetic(
            self._state(10.0, 10.0, 10.0), "5x", _DEFAULT_TOKEN_COSTS
        )
        r20 = calculate_synthetic(
            self._state(10.0, 10.0, 10.0), "20x", _DEFAULT_TOKEN_COSTS
        )
        assert r5["synthetic_5h"] > r20["synthetic_5h"]
        assert r5["synthetic_7d"] > r20["synthetic_7d"]

    def test_baseline_at_100_stays_100(self):
        result = calculate_synthetic(
            self._state(100.0, 100.0, 5.0), "5x", _DEFAULT_TOKEN_COSTS
        )
        assert result["synthetic_5h"] == 100.0
        assert result["synthetic_7d"] == 100.0

    def test_baseline_99_9_small_cost_caps(self):
        result = calculate_synthetic(
            self._state(99.9, 99.9, 1.0), "5x", _DEFAULT_TOKEN_COSTS
        )
        assert result["synthetic_5h"] == 100.0

    def test_result_has_expected_keys(self):
        result = calculate_synthetic(
            self._state(10.0, 5.0, 1.0), "5x", _DEFAULT_TOKEN_COSTS
        )
        assert result["is_synthetic"] is True
        assert result["fallback_mode"] is True
        assert "accumulated_cost" in result
        assert "tier" in result

    def test_exact_math_5x(self):
        # 20 + (5 * 0.0075 * 100) = 20 + 3.75 = 23.75
        result = calculate_synthetic(
            self._state(20.0, 10.0, 5.0), "5x", _DEFAULT_TOKEN_COSTS
        )
        assert result["synthetic_5h"] == pytest.approx(23.75)
        # 10 + (5 * 0.0011 * 100) = 10 + 0.55 = 10.55
        assert result["synthetic_7d"] == pytest.approx(10.55)

    def test_exact_math_20x(self):
        # 20 + (5 * 0.001875 * 100) = 20 + 0.9375 = 20.9375
        result = calculate_synthetic(
            self._state(20.0, 10.0, 5.0), "20x", _DEFAULT_TOKEN_COSTS
        )
        assert result["synthetic_5h"] == pytest.approx(20.9375)
        # 10 + (5 * 0.000275 * 100) = 10 + 0.1375 = 10.1375
        assert result["synthetic_7d"] == pytest.approx(10.1375)


# ---------------------------------------------------------------------------
# _project_window
# ---------------------------------------------------------------------------
class TestProjectWindowEdgeCases:
    def test_none_input(self):
        result, rolled = _project_window(None, 5.0, datetime(2026, 1, 1))
        assert result is None
        assert rolled is False

    def test_empty_string(self):
        result, rolled = _project_window("", 5.0, datetime(2026, 1, 1))
        assert result is None
        assert rolled is False

    def test_invalid_string(self):
        result, rolled = _project_window("not-a-date", 5.0, datetime(2026, 1, 1))
        assert result is None
        assert rolled is False

    def test_future_no_roll(self):
        now = datetime(2026, 3, 6, 12, 0, 0)
        future = "2026-03-06T15:00:00+00:00"
        result, rolled = _project_window(future, 5.0, now)
        assert result == datetime(2026, 3, 6, 15, 0, 0)
        assert rolled is False

    def test_just_expired_rolls_once(self):
        now = datetime(2026, 3, 6, 15, 0, 1)
        resets = "2026-03-06T15:00:00+00:00"
        result, rolled = _project_window(resets, 5.0, now)
        assert result == datetime(2026, 3, 6, 20, 0, 0)
        assert rolled is True

    def test_expired_exactly_one_window(self):
        now = datetime(2026, 3, 6, 20, 0, 0)
        resets = "2026-03-06T15:00:00+00:00"
        result, rolled = _project_window(resets, 5.0, now)
        # 15:00+5h=20:00, but 20:00 <= 20:00, so rolls again to 01:00
        assert result == datetime(2026, 3, 7, 1, 0, 0)
        assert rolled is True

    def test_expired_three_windows(self):
        now = datetime(2026, 3, 7, 6, 0, 1)
        resets = "2026-03-06T15:00:00+00:00"
        result, rolled = _project_window(resets, 5.0, now)
        assert result == datetime(2026, 3, 7, 11, 0, 0)
        assert rolled is True

    def test_7d_window_rolls(self):
        now = datetime(2026, 3, 20, 0, 0, 0)
        resets = "2026-03-06T00:00:00+00:00"
        result, rolled = _project_window(resets, 168.0, now)
        assert rolled is True
        assert result > now

    def test_partial_window_expired(self):
        now = datetime(2026, 3, 6, 17, 0, 0)
        resets = "2026-03-06T15:00:00+00:00"
        result, rolled = _project_window(resets, 5.0, now)
        assert result == datetime(2026, 3, 6, 20, 0, 0)
        assert rolled is True

    def test_exactly_at_boundary_rolls(self):
        now = datetime(2026, 3, 6, 15, 0, 0)
        resets = "2026-03-06T15:00:00+00:00"
        result, rolled = _project_window(resets, 5.0, now)
        assert result == datetime(2026, 3, 6, 20, 0, 0)
        assert rolled is True

    def test_far_future_no_roll(self):
        now = datetime(2026, 3, 6, 12, 0, 0)
        future = "2027-01-01T00:00:00+00:00"
        result, rolled = _project_window(future, 5.0, now)
        assert result == datetime(2027, 1, 1, 0, 0, 0)
        assert rolled is False


# ---------------------------------------------------------------------------
# calculate_synthetic_with_rollover
# ---------------------------------------------------------------------------
class TestCalcSyntheticWithRolloverEdgeCases:
    def _fb(self, b5h=30.0, b7d=20.0, cost=10.0, r5h=None, r7d=None):
        s = _default_state()
        s["state"] = "fallback"
        s["baseline_5h"] = b5h
        s["baseline_7d"] = b7d
        s["accumulated_cost"] = cost
        s["resets_at_5h"] = r5h
        s["resets_at_7d"] = r7d
        s["entered_at"] = time.time()
        s["tier"] = "5x"
        return s

    def test_no_rollover_uses_baseline(self):
        now = datetime(2026, 3, 6, 12, 0, 0)
        state = self._fb(
            30.0, 20.0, 5.0, "2026-03-06T17:00:00+00:00", "2026-03-13T12:00:00+00:00"
        )
        r = calculate_synthetic_with_rollover(state, "5x", _DEFAULT_TOKEN_COSTS, now)
        assert r["synthetic_5h"] == pytest.approx(30.0 + 5.0 * 0.0075 * 100)
        assert r["synthetic_7d"] == pytest.approx(20.0 + 5.0 * 0.0011 * 100)
        assert r["state_changed"] is False

    def test_5h_expired_7d_active(self):
        now = datetime(2026, 3, 6, 16, 0, 0)
        state = self._fb(
            30.0, 20.0, 10.0, "2026-03-06T15:00:00+00:00", "2026-03-13T12:00:00+00:00"
        )
        r = calculate_synthetic_with_rollover(state, "5x", _DEFAULT_TOKEN_COSTS, now)
        assert r["synthetic_5h"] == pytest.approx(0.0)
        assert r["synthetic_7d"] == pytest.approx(20.0 + 10.0 * 0.0011 * 100)
        assert r["state_changed"] is True

    def test_7d_expired_5h_active(self):
        now = datetime(2026, 3, 14, 0, 0, 0)
        state = self._fb(
            30.0, 20.0, 10.0, "2026-03-14T05:00:00+00:00", "2026-03-13T00:00:00+00:00"
        )
        r = calculate_synthetic_with_rollover(state, "5x", _DEFAULT_TOKEN_COSTS, now)
        assert r["synthetic_5h"] == pytest.approx(30.0 + 10.0 * 0.0075 * 100)
        assert r["synthetic_7d"] == pytest.approx(0.0)
        assert r["state_changed"] is True

    def test_both_expired(self):
        now = datetime(2026, 3, 20, 0, 0, 0)
        state = self._fb(
            30.0, 20.0, 10.0, "2026-03-06T15:00:00+00:00", "2026-03-06T00:00:00+00:00"
        )
        r = calculate_synthetic_with_rollover(state, "5x", _DEFAULT_TOKEN_COSTS, now)
        assert r["synthetic_5h"] == pytest.approx(0.0)
        assert r["synthetic_7d"] == pytest.approx(0.0)
        assert r["state_changed"] is True

    def test_rollover_with_zero_cost(self):
        now = datetime(2026, 3, 6, 16, 0, 0)
        state = self._fb(
            30.0, 20.0, 0.0, "2026-03-06T15:00:00+00:00", "2026-03-13T12:00:00+00:00"
        )
        r = calculate_synthetic_with_rollover(state, "5x", _DEFAULT_TOKEN_COSTS, now)
        assert r["synthetic_5h"] == pytest.approx(0.0)

    def test_cost_split_across_rollover(self):
        now = datetime(2026, 3, 6, 16, 0, 0)
        state = self._fb(
            30.0, 20.0, 20.0, "2026-03-06T15:00:00+00:00", "2026-03-13T12:00:00+00:00"
        )
        state["rollover_cost_5h"] = 15.0
        state["last_rollover_resets_5h"] = datetime(2026, 3, 6, 20, 0, 0).isoformat()
        r = calculate_synthetic_with_rollover(state, "5x", _DEFAULT_TOKEN_COSTS, now)
        assert r["synthetic_5h"] == pytest.approx(5.0 * 0.0075 * 100)

    def test_idempotent_same_boundary(self):
        now = datetime(2026, 3, 6, 16, 0, 0)
        state = self._fb(
            30.0, 20.0, 10.0, "2026-03-06T15:00:00+00:00", "2026-03-13T12:00:00+00:00"
        )
        r1 = calculate_synthetic_with_rollover(state, "5x", _DEFAULT_TOKEN_COSTS, now)
        assert r1["state_changed"] is True
        assert state["rollover_cost_5h"] == 10.0
        state["accumulated_cost"] = 15.0
        r2 = calculate_synthetic_with_rollover(state, "5x", _DEFAULT_TOKEN_COSTS, now)
        assert r2["state_changed"] is False
        assert r2["synthetic_5h"] == pytest.approx(5.0 * 0.0075 * 100)

    def test_none_resets_at_skips_rollover(self):
        now = datetime(2026, 3, 6, 16, 0, 0)
        state = self._fb(30.0, 20.0, 5.0, None, None)
        r = calculate_synthetic_with_rollover(state, "5x", _DEFAULT_TOKEN_COSTS, now)
        assert r["synthetic_5h"] == pytest.approx(30.0 + 5.0 * 0.0075 * 100)
        assert r["five_resets"] is None
        assert r["seven_resets"] is None
        assert r["state_changed"] is False

    def test_one_none_other_rolls(self):
        now = datetime(2026, 3, 6, 16, 0, 0)
        state = self._fb(30.0, 20.0, 10.0, "2026-03-06T15:00:00+00:00", None)
        r = calculate_synthetic_with_rollover(state, "5x", _DEFAULT_TOKEN_COSTS, now)
        assert r["synthetic_5h"] == pytest.approx(0.0)
        assert r["synthetic_7d"] == pytest.approx(20.0 + 10.0 * 0.0011 * 100)
        assert r["five_resets"] is not None
        assert r["seven_resets"] is None

    def test_tier_difference(self):
        now = datetime(2026, 3, 6, 12, 0, 0)
        s5 = self._fb(
            10.0, 10.0, 10.0, "2026-03-06T17:00:00+00:00", "2026-03-13T12:00:00+00:00"
        )
        s20 = self._fb(
            10.0, 10.0, 10.0, "2026-03-06T17:00:00+00:00", "2026-03-13T12:00:00+00:00"
        )
        r5 = calculate_synthetic_with_rollover(s5, "5x", _DEFAULT_TOKEN_COSTS, now)
        r20 = calculate_synthetic_with_rollover(s20, "20x", _DEFAULT_TOKEN_COSTS, now)
        assert r5["synthetic_5h"] > r20["synthetic_5h"]
        assert r5["synthetic_7d"] > r20["synthetic_7d"]

    def test_result_keys(self):
        now = datetime(2026, 3, 6, 12, 0, 0)
        state = self._fb(
            10.0, 5.0, 1.0, "2026-03-06T17:00:00+00:00", "2026-03-13T12:00:00+00:00"
        )
        r = calculate_synthetic_with_rollover(state, "5x", _DEFAULT_TOKEN_COSTS, now)
        assert "synthetic_5h" in r
        assert "synthetic_7d" in r
        assert "five_resets" in r
        assert "seven_resets" in r
        assert r["is_synthetic"] is True
        assert "state_changed" in r

    def test_caps_at_100_with_preset_rollover(self):
        now = datetime(2026, 3, 6, 16, 0, 0)
        state = self._fb(
            0.0, 0.0, 100000.0, "2026-03-06T15:00:00+00:00", "2026-03-06T00:00:00+00:00"
        )
        state["rollover_cost_5h"] = 0.0
        state["last_rollover_resets_5h"] = datetime(2026, 3, 6, 20, 0, 0).isoformat()
        r = calculate_synthetic_with_rollover(state, "5x", _DEFAULT_TOKEN_COSTS, now)
        assert r["synthetic_5h"] == 100.0

    def test_default_now_uses_utcnow(self):
        state = self._fb(
            10.0, 5.0, 1.0, "2099-01-01T00:00:00+00:00", "2099-01-01T00:00:00+00:00"
        )
        r = calculate_synthetic_with_rollover(state, "5x", _DEFAULT_TOKEN_COSTS)
        assert r["state_changed"] is False
        assert r["synthetic_5h"] == pytest.approx(10.0 + 1.0 * 0.0075 * 100)
