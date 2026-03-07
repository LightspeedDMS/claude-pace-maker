#!/usr/bin/env python3
"""
Edge-case tests for enter_fallback and exit_fallback transitions.
"""

import json
import time
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pacemaker.fallback import (
    parse_api_datetime,
    _default_state,
    load_fallback_state,
    save_fallback_state,
    enter_fallback,
    exit_fallback,
    accumulate_cost,
)


class TestEnterFallbackEdgeCases:
    def _write_usage_cache(
        self, tmp_path, five_hour_util, seven_day_util, resets_5h=None, resets_7d=None
    ):
        cache = tmp_path / "usage_cache.json"
        data = {
            "timestamp": time.time(),
            "response": {
                "five_hour": {
                    "utilization": five_hour_util,
                    "resets_at": resets_5h,
                },
                "seven_day": {
                    "utilization": seven_day_util,
                    "resets_at": resets_7d,
                },
            },
        }
        cache.write_text(json.dumps(data))
        return str(cache)

    @patch("pacemaker.profile_cache.load_cached_profile", return_value=None)
    def test_normal_to_fallback_captures_baselines(self, _mock, tmp_path):
        cache = self._write_usage_cache(tmp_path, 45.0, 30.0)
        sp = str(tmp_path / "state.json")
        enter_fallback(cache, sp)
        state = load_fallback_state(sp)
        assert state["state"] == "fallback"
        assert state["baseline_5h"] == 45.0
        assert state["baseline_7d"] == 30.0

    @patch("pacemaker.profile_cache.load_cached_profile", return_value=None)
    def test_missing_cache_uses_zero_baselines(self, _mock, tmp_path):
        sp = str(tmp_path / "state.json")
        enter_fallback(str(tmp_path / "nonexistent.json"), sp)
        state = load_fallback_state(sp)
        assert state["baseline_5h"] == 0.0
        assert state["baseline_7d"] == 0.0

    @patch("pacemaker.profile_cache.load_cached_profile", return_value=None)
    def test_empty_cache_uses_zero_baselines(self, _mock, tmp_path):
        cache = tmp_path / "usage_cache.json"
        cache.write_text("")
        sp = str(tmp_path / "state.json")
        enter_fallback(str(cache), sp)
        state = load_fallback_state(sp)
        assert state["baseline_5h"] == 0.0

    @patch("pacemaker.profile_cache.load_cached_profile", return_value=None)
    def test_corrupt_cache_uses_zero_baselines(self, _mock, tmp_path):
        cache = tmp_path / "usage_cache.json"
        cache.write_text("{broken json")
        sp = str(tmp_path / "state.json")
        enter_fallback(str(cache), sp)
        state = load_fallback_state(sp)
        assert state["baseline_5h"] == 0.0

    @patch("pacemaker.profile_cache.load_cached_profile", return_value=None)
    def test_null_resets_at_synthesized(self, _mock, tmp_path):
        cache = self._write_usage_cache(tmp_path, 10.0, 5.0)
        sp = str(tmp_path / "state.json")
        enter_fallback(cache, sp)
        state = load_fallback_state(sp)
        assert state["resets_at_5h"] is not None
        assert state["resets_at_7d"] is not None
        r5h = parse_api_datetime(state["resets_at_5h"])
        r7d = parse_api_datetime(state["resets_at_7d"])
        now = datetime.utcnow()
        assert abs((r5h - now).total_seconds() - 5 * 3600) < 60
        assert abs((r7d - now).total_seconds() - 168 * 3600) < 60

    @patch("pacemaker.profile_cache.load_cached_profile", return_value=None)
    def test_valid_resets_at_preserved(self, _mock, tmp_path):
        future_5h = (datetime.utcnow() + timedelta(hours=2)).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"
        )
        future_7d = (datetime.utcnow() + timedelta(days=5)).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"
        )
        cache = self._write_usage_cache(tmp_path, 20.0, 10.0, future_5h, future_7d)
        sp = str(tmp_path / "state.json")
        enter_fallback(cache, sp)
        state = load_fallback_state(sp)
        assert state["resets_at_5h"] == future_5h
        assert state["resets_at_7d"] == future_7d

    @patch("pacemaker.profile_cache.load_cached_profile", return_value=None)
    def test_idempotent_does_not_reset_cost(self, _mock, tmp_path):
        cache = self._write_usage_cache(tmp_path, 40.0, 25.0)
        sp = str(tmp_path / "state.json")
        enter_fallback(cache, sp)
        accumulate_cost(10000, 5000, 0, 0, "opus", sp)
        state_before = load_fallback_state(sp)
        cost_before = state_before["accumulated_cost"]
        assert cost_before > 0
        enter_fallback(cache, sp)
        state_after = load_fallback_state(sp)
        assert state_after["accumulated_cost"] == pytest.approx(cost_before)

    @patch("pacemaker.profile_cache.load_cached_profile", return_value=None)
    def test_idempotent_does_not_change_baselines(self, _mock, tmp_path):
        cache1 = self._write_usage_cache(tmp_path, 40.0, 25.0)
        sp = str(tmp_path / "state.json")
        enter_fallback(cache1, sp)
        state1 = load_fallback_state(sp)
        cache2 = self._write_usage_cache(tmp_path, 90.0, 80.0)
        enter_fallback(cache2, sp)
        state2 = load_fallback_state(sp)
        assert state2["baseline_5h"] == state1["baseline_5h"]

    @patch("pacemaker.profile_cache.load_cached_profile", return_value=None)
    def test_state_has_all_keys(self, _mock, tmp_path):
        cache = self._write_usage_cache(tmp_path, 10.0, 5.0)
        sp = str(tmp_path / "state.json")
        enter_fallback(cache, sp)
        state = load_fallback_state(sp)
        for key in _default_state():
            assert key in state, f"Missing key: {key}"

    @patch("pacemaker.profile_cache.load_cached_profile", return_value=None)
    def test_synthetic_cache_rejected(self, _mock, tmp_path):
        cache = tmp_path / "usage_cache.json"
        cache.write_text(
            json.dumps(
                {
                    "timestamp": time.time(),
                    "is_synthetic": True,
                    "response": {
                        "five_hour": {"utilization": 80.0, "resets_at": None},
                        "seven_day": {"utilization": 60.0, "resets_at": None},
                    },
                }
            )
        )
        sp = str(tmp_path / "state.json")
        enter_fallback(str(cache), sp)
        state = load_fallback_state(sp)
        assert state["baseline_5h"] == 0.0
        assert state["baseline_7d"] == 0.0

    @patch("pacemaker.profile_cache.load_cached_profile", return_value=None)
    def test_null_five_hour_in_cache(self, _mock, tmp_path):
        cache = tmp_path / "usage_cache.json"
        cache.write_text(
            json.dumps(
                {
                    "timestamp": time.time(),
                    "response": {"five_hour": None, "seven_day": {"utilization": 20.0}},
                }
            )
        )
        sp = str(tmp_path / "state.json")
        enter_fallback(str(cache), sp)
        state = load_fallback_state(sp)
        assert state["baseline_5h"] == 0.0
        assert state["baseline_7d"] == 20.0


class TestExitFallbackEdgeCases:
    def test_from_fallback_resets_all(self, tmp_path):
        sp = str(tmp_path / "state.json")
        state = _default_state()
        state["state"] = "fallback"
        state["accumulated_cost"] = 50.0
        state["baseline_5h"] = 45.0
        state["baseline_7d"] = 30.0
        state["tier"] = "20x"
        state["resets_at_5h"] = "2026-01-01T00:00:00+00:00"
        state["rollover_cost_5h"] = 10.0
        save_fallback_state(state, sp)
        exit_fallback(50.0, 35.0, sp)
        result = load_fallback_state(sp)
        assert result["state"] == "normal"
        assert result["accumulated_cost"] == 0.0
        assert result["baseline_5h"] is None
        assert result["baseline_7d"] is None
        assert result["resets_at_5h"] is None
        assert result["resets_at_7d"] is None
        assert result["rollover_cost_5h"] is None
        assert result["rollover_cost_7d"] is None
        assert result["tier"] is None
        assert result["entered_at"] is None

    def test_from_normal_is_noop(self, tmp_path):
        sp = str(tmp_path / "state.json")
        state = _default_state()
        state["accumulated_cost"] = 99.0
        save_fallback_state(state, sp)
        exit_fallback(10.0, 5.0, sp)
        result = load_fallback_state(sp)
        assert result["accumulated_cost"] == 99.0

    def test_result_has_all_keys(self, tmp_path):
        sp = str(tmp_path / "state.json")
        state = _default_state()
        state["state"] = "fallback"
        save_fallback_state(state, sp)
        exit_fallback(10.0, 5.0, sp)
        result = load_fallback_state(sp)
        for key in _default_state():
            assert key in result, f"Missing key after exit: {key}"
