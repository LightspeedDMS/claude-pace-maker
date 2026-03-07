#!/usr/bin/env python3
"""
Tests for state completeness in fallback.py: exit_fallback(), enter_fallback().

Priority 3: exit_fallback() must write ALL keys from _default_state() (not a 4-key dict).
Priority 4: enter_fallback() must start from _default_state() then override specific fields.
Priority 5: enter_fallback() must synthesize resets_at when null (now+5h / now+168h).

AC3: exit_fallback writes complete state
AC4: enter_fallback writes complete state
AC5: null resets_at values get synthetic timestamps on entry
"""

import json
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestExitFallbackWritesAllKeys:
    """Priority 3: exit_fallback() must write every key from _default_state()."""

    def _make_full_fallback_state(self, state_path: str) -> None:
        """Write a complete fallback state with rollover keys populated."""
        from pacemaker.fallback import FallbackState

        state = {
            "state": FallbackState.FALLBACK.value,
            "baseline_5h": 50.0,
            "baseline_7d": 35.0,
            "resets_at_5h": "2026-03-06T15:00:00+00:00",
            "resets_at_7d": "2026-03-07T15:00:00+00:00",
            "accumulated_cost": 12.5,
            "rollover_cost_5h": 1.5,
            "rollover_cost_7d": 3.2,
            "last_rollover_resets_5h": "2026-03-06T20:00:00",
            "last_rollover_resets_7d": "2026-03-07T15:00:00",
            "tier": "20x",
            "entered_at": time.time() - 3600,
        }
        Path(state_path).parent.mkdir(parents=True, exist_ok=True)
        Path(state_path).write_text(json.dumps(state))

    def test_exit_fallback_writes_all_default_keys(self, tmp_path):
        """
        exit_fallback() must write ALL keys present in _default_state().
        Previously it wrote only 4 keys, leaving rollover/tier orphaned.
        """
        from pacemaker.fallback import (
            exit_fallback,
            _default_state,
            load_fallback_state,
        )

        state_path = str(tmp_path / "fallback_state.json")
        self._make_full_fallback_state(state_path)

        exit_fallback(real_5h=25.0, real_7d=20.0, state_path=state_path)

        saved = load_fallback_state(state_path)
        default_keys = set(_default_state().keys())
        saved_keys = set(saved.keys())

        assert default_keys.issubset(
            saved_keys
        ), f"exit_fallback() missing keys: {default_keys - saved_keys}"

    def test_exit_fallback_state_is_normal(self, tmp_path):
        """exit_fallback() sets state to 'normal'."""
        from pacemaker.fallback import exit_fallback, load_fallback_state, FallbackState

        state_path = str(tmp_path / "fallback_state.json")
        self._make_full_fallback_state(state_path)

        exit_fallback(real_5h=25.0, real_7d=20.0, state_path=state_path)

        saved = load_fallback_state(state_path)
        assert saved["state"] == FallbackState.NORMAL.value

    def test_exit_fallback_clears_rollover_cost_5h(self, tmp_path):
        """exit_fallback() must clear rollover_cost_5h to None."""
        from pacemaker.fallback import exit_fallback, load_fallback_state

        state_path = str(tmp_path / "fallback_state.json")
        self._make_full_fallback_state(state_path)

        exit_fallback(real_5h=25.0, real_7d=20.0, state_path=state_path)

        saved = load_fallback_state(state_path)
        assert (
            saved["rollover_cost_5h"] is None
        ), f"rollover_cost_5h should be None after exit, got {saved['rollover_cost_5h']}"

    def test_exit_fallback_clears_rollover_cost_7d(self, tmp_path):
        """exit_fallback() must clear rollover_cost_7d to None."""
        from pacemaker.fallback import exit_fallback, load_fallback_state

        state_path = str(tmp_path / "fallback_state.json")
        self._make_full_fallback_state(state_path)

        exit_fallback(real_5h=25.0, real_7d=20.0, state_path=state_path)

        saved = load_fallback_state(state_path)
        assert (
            saved["rollover_cost_7d"] is None
        ), f"rollover_cost_7d should be None after exit, got {saved['rollover_cost_7d']}"

    def test_exit_fallback_clears_last_rollover_resets_5h(self, tmp_path):
        """exit_fallback() must clear last_rollover_resets_5h to None."""
        from pacemaker.fallback import exit_fallback, load_fallback_state

        state_path = str(tmp_path / "fallback_state.json")
        self._make_full_fallback_state(state_path)

        exit_fallback(real_5h=25.0, real_7d=20.0, state_path=state_path)

        saved = load_fallback_state(state_path)
        assert saved["last_rollover_resets_5h"] is None

    def test_exit_fallback_clears_last_rollover_resets_7d(self, tmp_path):
        """exit_fallback() must clear last_rollover_resets_7d to None."""
        from pacemaker.fallback import exit_fallback, load_fallback_state

        state_path = str(tmp_path / "fallback_state.json")
        self._make_full_fallback_state(state_path)

        exit_fallback(real_5h=25.0, real_7d=20.0, state_path=state_path)

        saved = load_fallback_state(state_path)
        assert saved["last_rollover_resets_7d"] is None

    def test_exit_fallback_clears_tier(self, tmp_path):
        """exit_fallback() must clear tier to None."""
        from pacemaker.fallback import exit_fallback, load_fallback_state

        state_path = str(tmp_path / "fallback_state.json")
        self._make_full_fallback_state(state_path)

        exit_fallback(real_5h=25.0, real_7d=20.0, state_path=state_path)

        saved = load_fallback_state(state_path)
        assert (
            saved["tier"] is None
        ), f"tier should be None after exit, got {saved['tier']}"

    def test_exit_fallback_clears_resets_at_5h(self, tmp_path):
        """exit_fallback() must clear resets_at_5h to None."""
        from pacemaker.fallback import exit_fallback, load_fallback_state

        state_path = str(tmp_path / "fallback_state.json")
        self._make_full_fallback_state(state_path)

        exit_fallback(real_5h=25.0, real_7d=20.0, state_path=state_path)

        saved = load_fallback_state(state_path)
        assert saved["resets_at_5h"] is None

    def test_exit_fallback_clears_resets_at_7d(self, tmp_path):
        """exit_fallback() must clear resets_at_7d to None."""
        from pacemaker.fallback import exit_fallback, load_fallback_state

        state_path = str(tmp_path / "fallback_state.json")
        self._make_full_fallback_state(state_path)

        exit_fallback(real_5h=25.0, real_7d=20.0, state_path=state_path)

        saved = load_fallback_state(state_path)
        assert saved["resets_at_7d"] is None

    def test_exit_fallback_resets_accumulated_cost_to_zero(self, tmp_path):
        """exit_fallback() must reset accumulated_cost to 0.0."""
        from pacemaker.fallback import exit_fallback, load_fallback_state

        state_path = str(tmp_path / "fallback_state.json")
        self._make_full_fallback_state(state_path)

        exit_fallback(real_5h=25.0, real_7d=20.0, state_path=state_path)

        saved = load_fallback_state(state_path)
        assert saved["accumulated_cost"] == 0.0

    def test_exit_fallback_noop_when_already_normal(self, tmp_path):
        """exit_fallback() is a no-op when already in NORMAL state."""
        from pacemaker.fallback import exit_fallback, load_fallback_state, FallbackState

        state_path = str(tmp_path / "fallback_state.json")
        # No state file => NORMAL defaults

        exit_fallback(real_5h=25.0, real_7d=20.0, state_path=state_path)

        # Should not have written a file (or if it did, state must still be normal)
        saved = load_fallback_state(state_path)
        assert saved["state"] == FallbackState.NORMAL.value


class TestEnterFallbackWritesAllKeys:
    """Priority 4: enter_fallback() must start from _default_state() to avoid key drift."""

    def _write_usage_cache(
        self,
        tmp_path,
        five_util=30.0,
        seven_util=20.0,
        resets_5h="2026-03-06T15:00:00+00:00",
        resets_7d="2026-03-07T15:00:00+00:00",
    ) -> str:
        """Write a usage_cache.json with given values."""
        cache_path = tmp_path / "usage_cache.json"
        cache_path.write_text(
            json.dumps(
                {
                    "timestamp": time.time(),
                    "response": {
                        "five_hour": {"utilization": five_util, "resets_at": resets_5h},
                        "seven_day": {
                            "utilization": seven_util,
                            "resets_at": resets_7d,
                        },
                    },
                }
            )
        )
        return str(cache_path)

    def test_enter_fallback_writes_all_default_keys(self, tmp_path):
        """
        enter_fallback() must write ALL keys from _default_state().
        Previously it wrote only 8 keys, missing rollover keys.
        """
        from pacemaker.fallback import (
            enter_fallback,
            _default_state,
            load_fallback_state,
        )

        state_path = str(tmp_path / "fallback_state.json")
        cache_path = self._write_usage_cache(tmp_path)

        enter_fallback(usage_cache_path=cache_path, state_path=state_path)

        saved = load_fallback_state(state_path)
        default_keys = set(_default_state().keys())
        saved_keys = set(saved.keys())

        assert default_keys.issubset(
            saved_keys
        ), f"enter_fallback() missing keys: {default_keys - saved_keys}"

    def test_enter_fallback_rollover_cost_5h_is_none(self, tmp_path):
        """enter_fallback() sets rollover_cost_5h=None initially."""
        from pacemaker.fallback import enter_fallback, load_fallback_state

        state_path = str(tmp_path / "fallback_state.json")
        cache_path = self._write_usage_cache(tmp_path)

        enter_fallback(usage_cache_path=cache_path, state_path=state_path)

        saved = load_fallback_state(state_path)
        assert saved["rollover_cost_5h"] is None

    def test_enter_fallback_rollover_cost_7d_is_none(self, tmp_path):
        """enter_fallback() sets rollover_cost_7d=None initially."""
        from pacemaker.fallback import enter_fallback, load_fallback_state

        state_path = str(tmp_path / "fallback_state.json")
        cache_path = self._write_usage_cache(tmp_path)

        enter_fallback(usage_cache_path=cache_path, state_path=state_path)

        saved = load_fallback_state(state_path)
        assert saved["rollover_cost_7d"] is None

    def test_enter_fallback_last_rollover_resets_5h_is_none(self, tmp_path):
        """enter_fallback() sets last_rollover_resets_5h=None initially."""
        from pacemaker.fallback import enter_fallback, load_fallback_state

        state_path = str(tmp_path / "fallback_state.json")
        cache_path = self._write_usage_cache(tmp_path)

        enter_fallback(usage_cache_path=cache_path, state_path=state_path)

        saved = load_fallback_state(state_path)
        assert saved["last_rollover_resets_5h"] is None

    def test_enter_fallback_last_rollover_resets_7d_is_none(self, tmp_path):
        """enter_fallback() sets last_rollover_resets_7d=None initially."""
        from pacemaker.fallback import enter_fallback, load_fallback_state

        state_path = str(tmp_path / "fallback_state.json")
        cache_path = self._write_usage_cache(tmp_path)

        enter_fallback(usage_cache_path=cache_path, state_path=state_path)

        saved = load_fallback_state(state_path)
        assert saved["last_rollover_resets_7d"] is None

    def test_enter_fallback_sets_state_to_fallback(self, tmp_path):
        """enter_fallback() sets state='fallback'."""
        from pacemaker.fallback import (
            enter_fallback,
            load_fallback_state,
            FallbackState,
        )

        state_path = str(tmp_path / "fallback_state.json")
        cache_path = self._write_usage_cache(tmp_path)

        enter_fallback(usage_cache_path=cache_path, state_path=state_path)

        saved = load_fallback_state(state_path)
        assert saved["state"] == FallbackState.FALLBACK.value

    def test_enter_fallback_captures_baselines(self, tmp_path):
        """enter_fallback() captures baseline_5h and baseline_7d from usage_cache."""
        from pacemaker.fallback import enter_fallback, load_fallback_state

        state_path = str(tmp_path / "fallback_state.json")
        cache_path = self._write_usage_cache(tmp_path, five_util=45.0, seven_util=30.0)

        enter_fallback(usage_cache_path=cache_path, state_path=state_path)

        saved = load_fallback_state(state_path)
        assert saved["baseline_5h"] == 45.0
        assert saved["baseline_7d"] == 30.0


class TestEnterFallbackSynthesizesNullResetsAt:
    """Priority 5: null resets_at must get synthetic values (now+5h / now+168h)."""

    def _write_usage_cache_null_resets(self, tmp_path) -> str:
        """Write a usage_cache.json where resets_at values are null."""
        cache_path = tmp_path / "usage_cache.json"
        cache_path.write_text(
            json.dumps(
                {
                    "timestamp": time.time(),
                    "response": {
                        "five_hour": {"utilization": 30.0, "resets_at": None},
                        "seven_day": {"utilization": 20.0, "resets_at": None},
                    },
                }
            )
        )
        return str(cache_path)

    def _write_usage_cache_with_resets(
        self, tmp_path, resets_5h: str, resets_7d: str
    ) -> str:
        """Write a usage_cache.json with specific resets_at values."""
        cache_path = tmp_path / "usage_cache.json"
        cache_path.write_text(
            json.dumps(
                {
                    "timestamp": time.time(),
                    "response": {
                        "five_hour": {"utilization": 30.0, "resets_at": resets_5h},
                        "seven_day": {"utilization": 20.0, "resets_at": resets_7d},
                    },
                }
            )
        )
        return str(cache_path)

    def test_null_resets_5h_gets_synthetic_value(self, tmp_path):
        """
        When resets_at for five_hour is null, enter_fallback() sets resets_at_5h
        to now+5h (so window rollover detection has a valid timestamp).
        """
        from pacemaker.fallback import (
            enter_fallback,
            load_fallback_state,
            parse_api_datetime,
        )
        from datetime import datetime, timedelta

        state_path = str(tmp_path / "fallback_state.json")
        cache_path = self._write_usage_cache_null_resets(tmp_path)

        before = datetime.utcnow()
        enter_fallback(usage_cache_path=cache_path, state_path=state_path)
        after = datetime.utcnow()

        saved = load_fallback_state(state_path)
        resets_5h = saved.get("resets_at_5h")

        assert resets_5h is not None, "resets_at_5h must be synthesized when null"

        # Parse the stored value and verify it's approximately now+5h
        parsed = parse_api_datetime(resets_5h)
        assert parsed is not None, f"resets_at_5h '{resets_5h}' must be parseable"

        # strftime truncates to seconds, so allow 1-second tolerance below lower bound
        expected_low = before + timedelta(hours=5) - timedelta(seconds=1)
        expected_high = after + timedelta(hours=5)
        assert (
            expected_low <= parsed <= expected_high
        ), f"resets_at_5h {parsed} not in expected range [{expected_low}, {expected_high}]"

    def test_null_resets_7d_gets_synthetic_value(self, tmp_path):
        """
        When resets_at for seven_day is null, enter_fallback() sets resets_at_7d
        to now+168h (so window rollover detection has a valid timestamp).
        """
        from pacemaker.fallback import (
            enter_fallback,
            load_fallback_state,
            parse_api_datetime,
        )
        from datetime import datetime, timedelta

        state_path = str(tmp_path / "fallback_state.json")
        cache_path = self._write_usage_cache_null_resets(tmp_path)

        before = datetime.utcnow()
        enter_fallback(usage_cache_path=cache_path, state_path=state_path)
        after = datetime.utcnow()

        saved = load_fallback_state(state_path)
        resets_7d = saved.get("resets_at_7d")

        assert resets_7d is not None, "resets_at_7d must be synthesized when null"

        parsed = parse_api_datetime(resets_7d)
        assert parsed is not None, f"resets_at_7d '{resets_7d}' must be parseable"

        # strftime truncates to seconds, so allow 1-second tolerance below lower bound
        expected_low = before + timedelta(hours=168) - timedelta(seconds=1)
        expected_high = after + timedelta(hours=168)
        assert (
            expected_low <= parsed <= expected_high
        ), f"resets_at_7d {parsed} not in expected range [{expected_low}, {expected_high}]"

    def test_nonnull_resets_5h_preserved_unchanged(self, tmp_path):
        """
        When resets_at_5h is already set (non-null), enter_fallback() must
        preserve it unchanged.
        """
        from pacemaker.fallback import enter_fallback, load_fallback_state

        state_path = str(tmp_path / "fallback_state.json")
        resets_str = "2026-03-06T15:00:00+00:00"
        cache_path = self._write_usage_cache_with_resets(
            tmp_path, resets_5h=resets_str, resets_7d="2026-03-07T15:00:00+00:00"
        )

        enter_fallback(usage_cache_path=cache_path, state_path=state_path)

        saved = load_fallback_state(state_path)
        assert (
            saved["resets_at_5h"] == resets_str
        ), f"Non-null resets_at_5h should be preserved, got {saved['resets_at_5h']}"

    def test_nonnull_resets_7d_preserved_unchanged(self, tmp_path):
        """
        When resets_at_7d is already set (non-null), enter_fallback() must
        preserve it unchanged.
        """
        from pacemaker.fallback import enter_fallback, load_fallback_state

        state_path = str(tmp_path / "fallback_state.json")
        resets_str = "2026-03-07T15:00:00+00:00"
        cache_path = self._write_usage_cache_with_resets(
            tmp_path, resets_5h="2026-03-06T15:00:00+00:00", resets_7d=resets_str
        )

        enter_fallback(usage_cache_path=cache_path, state_path=state_path)

        saved = load_fallback_state(state_path)
        assert (
            saved["resets_at_7d"] == resets_str
        ), f"Non-null resets_at_7d should be preserved, got {saved['resets_at_7d']}"
