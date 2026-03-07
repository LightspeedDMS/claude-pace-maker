#!/usr/bin/env python3
"""
Edge-case tests for is_fallback_active and full lifecycle integration.
Tests complete cycles: enter -> accumulate -> synthetic -> rollover -> exit.
"""

import json
import time
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pacemaker.fallback import (
    _default_state,
    load_fallback_state,
    save_fallback_state,
    enter_fallback,
    exit_fallback,
    calculate_synthetic,
    calculate_synthetic_with_rollover,
    accumulate_cost,
    is_fallback_active,
    _DEFAULT_TOKEN_COSTS,
)


# ---------------------------------------------------------------------------
# is_fallback_active
# ---------------------------------------------------------------------------
class TestIsFallbackActiveEdgeCases:
    def test_false_when_normal(self, tmp_path):
        sp = str(tmp_path / "state.json")
        save_fallback_state(_default_state(), sp)
        assert is_fallback_active(sp) is False

    def test_true_when_fallback(self, tmp_path):
        sp = str(tmp_path / "state.json")
        state = _default_state()
        state["state"] = "fallback"
        save_fallback_state(state, sp)
        assert is_fallback_active(sp) is True

    def test_false_when_file_missing(self, tmp_path):
        assert is_fallback_active(str(tmp_path / "nope.json")) is False

    def test_false_when_file_corrupt(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{broken")
        assert is_fallback_active(str(p)) is False


# ---------------------------------------------------------------------------
# Full Lifecycle Integration
# ---------------------------------------------------------------------------
class TestFullLifecycleIntegration:
    def _write_cache(self, tmp_path, u5h, u7d, r5h=None, r7d=None):
        cache = tmp_path / "usage_cache.json"
        cache.write_text(
            json.dumps(
                {
                    "timestamp": time.time(),
                    "response": {
                        "five_hour": {"utilization": u5h, "resets_at": r5h},
                        "seven_day": {"utilization": u7d, "resets_at": r7d},
                    },
                }
            )
        )
        return str(cache)

    @patch("pacemaker.profile_cache.load_cached_profile", return_value=None)
    def test_full_cycle_enter_accumulate_synthetic_exit(self, _mock, tmp_path):
        cache = self._write_cache(tmp_path, 45.0, 30.0)
        sp = str(tmp_path / "state.json")

        assert is_fallback_active(sp) is False
        enter_fallback(cache, sp)
        assert is_fallback_active(sp) is True
        state = load_fallback_state(sp)
        assert state["baseline_5h"] == 45.0

        accumulate_cost(1_000_000, 0, 0, 0, "opus", sp)
        state = load_fallback_state(sp)
        assert state["accumulated_cost"] == pytest.approx(15.0)

        result = calculate_synthetic(state, "5x", _DEFAULT_TOKEN_COSTS)
        assert result["synthetic_5h"] > 45.0
        assert result["is_synthetic"] is True

        exit_fallback(50.0, 35.0, sp)
        assert is_fallback_active(sp) is False
        state = load_fallback_state(sp)
        assert state["accumulated_cost"] == 0.0
        assert state["baseline_5h"] is None

    @patch("pacemaker.profile_cache.load_cached_profile", return_value=None)
    def test_enter_accumulate_rollover_more_accumulate_exit(self, _mock, tmp_path):
        now = datetime(2026, 3, 6, 12, 0, 0)
        resets_5h = "2026-03-06T14:00:00+00:00"
        resets_7d = "2026-03-13T12:00:00+00:00"
        cache = self._write_cache(tmp_path, 40.0, 25.0, resets_5h, resets_7d)
        sp = str(tmp_path / "state.json")

        enter_fallback(cache, sp)
        accumulate_cost(1_000_000, 0, 0, 0, "opus", sp)  # $15

        state = load_fallback_state(sp)
        r1 = calculate_synthetic_with_rollover(state, "5x", _DEFAULT_TOKEN_COSTS, now)
        assert r1["synthetic_5h"] > 40.0
        assert r1["state_changed"] is False

        # Time passes, 5h window expires
        later = datetime(2026, 3, 6, 15, 0, 0)
        state = load_fallback_state(sp)
        r2 = calculate_synthetic_with_rollover(state, "5x", _DEFAULT_TOKEN_COSTS, later)
        assert r2["state_changed"] is True
        assert r2["synthetic_5h"] == pytest.approx(0.0)

        # Accumulate more after rollover
        save_fallback_state(state, sp)
        accumulate_cost(500_000, 100_000, 0, 0, "sonnet", sp)
        state = load_fallback_state(sp)

        r3 = calculate_synthetic_with_rollover(state, "5x", _DEFAULT_TOKEN_COSTS, later)
        cost_in_window = state["accumulated_cost"] - 15.0
        expected_5h = cost_in_window * 0.0075 * 100
        assert r3["synthetic_5h"] == pytest.approx(expected_5h, abs=0.01)

        exit_fallback(55.0, 40.0, sp)
        assert is_fallback_active(sp) is False

    @patch("pacemaker.profile_cache.load_cached_profile", return_value=None)
    def test_10_dollars_opus_reasonable_values(self, _mock, tmp_path):
        """$10 of opus usage should produce non-zero, non-100% synthetic values."""
        cache = self._write_cache(tmp_path, 30.0, 15.0)
        sp = str(tmp_path / "state.json")
        enter_fallback(cache, sp)

        accumulate_cost(666_667, 0, 0, 0, "opus", sp)
        state = load_fallback_state(sp)
        cost = state["accumulated_cost"]
        assert cost == pytest.approx(10.0, abs=0.01)

        result = calculate_synthetic(state, "5x", _DEFAULT_TOKEN_COSTS)
        assert result["synthetic_5h"] == pytest.approx(37.5, abs=0.1)
        assert result["synthetic_7d"] == pytest.approx(16.1, abs=0.1)
        assert 0 < result["synthetic_5h"] < 100
        assert 0 < result["synthetic_7d"] < 100

    @patch("pacemaker.profile_cache.load_cached_profile", return_value=None)
    def test_zero_baselines_still_grows(self, _mock, tmp_path):
        """Even with 0% baselines, synthetic values grow from accumulated cost."""
        cache = self._write_cache(tmp_path, 0.0, 0.0)
        sp = str(tmp_path / "state.json")
        enter_fallback(cache, sp)

        accumulate_cost(1_000_000, 500_000, 0, 0, "sonnet", sp)
        state = load_fallback_state(sp)
        result = calculate_synthetic(state, "5x", _DEFAULT_TOKEN_COSTS)
        assert result["synthetic_5h"] > 0.0
        assert result["synthetic_7d"] > 0.0

    @patch("pacemaker.profile_cache.load_cached_profile", return_value=None)
    def test_multiple_rollovers_successive(self, _mock, tmp_path):
        """Multiple 5h window rollovers with cost accumulation between each."""
        cache = self._write_cache(
            tmp_path,
            20.0,
            10.0,
            "2026-03-06T10:00:00+00:00",
            "2026-03-13T00:00:00+00:00",
        )
        sp = str(tmp_path / "state.json")
        enter_fallback(cache, sp)

        # First rollover at t=11:00
        t1 = datetime(2026, 3, 6, 11, 0, 0)
        accumulate_cost(1_000_000, 0, 0, 0, "sonnet", sp)
        state = load_fallback_state(sp)
        r1 = calculate_synthetic_with_rollover(state, "5x", _DEFAULT_TOKEN_COSTS, t1)
        assert r1["state_changed"] is True
        save_fallback_state(state, sp)

        # More cost, then second rollover at t=16:00
        accumulate_cost(1_000_000, 0, 0, 0, "sonnet", sp)
        t2 = datetime(2026, 3, 6, 16, 0, 0)
        state = load_fallback_state(sp)
        r2 = calculate_synthetic_with_rollover(state, "5x", _DEFAULT_TOKEN_COSTS, t2)
        assert r2["state_changed"] is True
        save_fallback_state(state, sp)

        state = load_fallback_state(sp)
        assert state["rollover_cost_5h"] == pytest.approx(6.0)

    @patch("pacemaker.profile_cache.load_cached_profile", return_value=None)
    def test_accumulate_noop_after_exit(self, _mock, tmp_path):
        """Accumulate does nothing after exit_fallback."""
        cache = self._write_cache(tmp_path, 30.0, 15.0)
        sp = str(tmp_path / "state.json")
        enter_fallback(cache, sp)
        accumulate_cost(1_000_000, 0, 0, 0, "opus", sp)
        exit_fallback(50.0, 35.0, sp)

        # This should be a no-op
        accumulate_cost(1_000_000, 0, 0, 0, "opus", sp)
        state = load_fallback_state(sp)
        assert state["accumulated_cost"] == 0.0

    @patch("pacemaker.profile_cache.load_cached_profile", return_value=None)
    def test_reenter_after_exit_fresh_state(self, _mock, tmp_path):
        """Re-entering fallback after exit gives fresh baselines."""
        cache1 = self._write_cache(tmp_path, 30.0, 15.0)
        sp = str(tmp_path / "state.json")
        enter_fallback(cache1, sp)
        accumulate_cost(1_000_000, 0, 0, 0, "opus", sp)
        exit_fallback(50.0, 35.0, sp)

        # Re-enter with different baselines
        cache2 = self._write_cache(tmp_path, 60.0, 40.0)
        enter_fallback(cache2, sp)
        state = load_fallback_state(sp)
        assert state["baseline_5h"] == 60.0
        assert state["baseline_7d"] == 40.0
        assert state["accumulated_cost"] == 0.0
