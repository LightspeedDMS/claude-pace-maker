#!/usr/bin/env python3
"""
Tests for calculate_synthetic_with_rollover() in fallback.py.

Priority 1: Extract inline rollover logic from pacing_engine.py into a
single parameterized function in fallback.py. Eliminates 80-100 lines of
copy-pasted 5h/7d code and fixes formula divergence (Bug 3 HIGH).

AC1 scenarios covered:
- rollover detection when window expired
- no rollover when window still active
- both windows rolled simultaneously
- rollover saves updated state to file
- resets_at=None skips rollover for that window
- result has correct structure (synthetic_5h, synthetic_7d, is_synthetic, etc.)
- 5x tier produces higher synthetic than 20x for same cost (coefficients differ)
"""

import time
from datetime import datetime, timedelta
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _make_fallback_state(
    baseline_5h: float = 50.0,
    baseline_7d: float = 35.0,
    accumulated_cost: float = 5.0,
    resets_at_5h: str = None,
    resets_at_7d: str = None,
    rollover_cost_5h=None,
    rollover_cost_7d=None,
    last_rollover_resets_5h=None,
    last_rollover_resets_7d=None,
    tier: str = "5x",
) -> dict:
    """Build a minimal fallback state dict for testing."""
    from pacemaker.fallback import FallbackState

    return {
        "state": FallbackState.FALLBACK.value,
        "baseline_5h": baseline_5h,
        "baseline_7d": baseline_7d,
        "resets_at_5h": resets_at_5h,
        "resets_at_7d": resets_at_7d,
        "accumulated_cost": accumulated_cost,
        "rollover_cost_5h": rollover_cost_5h,
        "rollover_cost_7d": rollover_cost_7d,
        "last_rollover_resets_5h": last_rollover_resets_5h,
        "last_rollover_resets_7d": last_rollover_resets_7d,
        "tier": tier,
        "entered_at": time.time() - 3600,
    }


def _future_resets_at(hours: float, offset: float = 2.0) -> str:
    """Return an ISO string for a reset time `hours + offset` hours from now."""
    dt = datetime.utcnow() + timedelta(hours=hours + offset)
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _expired_resets_at(hours_ago: float = 1.0) -> str:
    """Return an ISO string for a reset time `hours_ago` in the past."""
    dt = datetime.utcnow() - timedelta(hours=hours_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


class TestCalculateSyntheticWithRolloverExists:
    """Verify the function exists and is importable."""

    def test_function_importable(self):
        """calculate_synthetic_with_rollover must be importable from fallback."""
        from pacemaker.fallback import calculate_synthetic_with_rollover

        assert callable(calculate_synthetic_with_rollover)


class TestNoRolloverWhenWindowNotExpired:
    """When resets_at is in the future, no rollover should occur."""

    def test_no_rollover_5h_not_expired(self):
        """5h window not expired: synthetic_5h equals calculate_synthetic result."""
        from pacemaker.fallback import (
            calculate_synthetic_with_rollover,
            load_token_costs,
        )

        token_costs = load_token_costs()
        state = _make_fallback_state(
            baseline_5h=40.0,
            baseline_7d=25.0,
            accumulated_cost=3.0,
            resets_at_5h=_future_resets_at(5),  # 7 hours from now
            resets_at_7d=_future_resets_at(168),
        )

        now = datetime.utcnow()
        result = calculate_synthetic_with_rollover(state, "5x", token_costs, now)

        assert result["is_synthetic"] is True
        # No rollover means result matches base synthetic
        from pacemaker.fallback import calculate_synthetic

        base = calculate_synthetic(state, "5x", token_costs)
        assert result["synthetic_5h"] == pytest.approx(base["synthetic_5h"], rel=0.001)

    def test_no_rollover_7d_not_expired(self):
        """7d window not expired: synthetic_7d equals calculate_synthetic result."""
        from pacemaker.fallback import (
            calculate_synthetic_with_rollover,
            load_token_costs,
        )

        token_costs = load_token_costs()
        state = _make_fallback_state(
            baseline_5h=40.0,
            baseline_7d=25.0,
            accumulated_cost=3.0,
            resets_at_5h=_future_resets_at(5),
            resets_at_7d=_future_resets_at(168),
        )

        now = datetime.utcnow()
        result = calculate_synthetic_with_rollover(state, "5x", token_costs, now)

        from pacemaker.fallback import calculate_synthetic

        base = calculate_synthetic(state, "5x", token_costs)
        assert result["synthetic_7d"] == pytest.approx(base["synthetic_7d"], rel=0.001)


class TestRolloverWhenWindowExpired:
    """When resets_at is in the past, rollover resets the baseline."""

    def test_rollover_5h_resets_synthetic_to_cost_in_new_window(self):
        """
        When 5h window expired, synthetic_5h should be based only on cost
        accumulated AFTER rollover (cost_in_window = accumulated - rollover_cost),
        not the full accumulated cost from baseline.
        """
        from pacemaker.fallback import (
            calculate_synthetic_with_rollover,
            load_token_costs,
        )

        token_costs = load_token_costs()
        accumulated = 10.0
        state = _make_fallback_state(
            baseline_5h=60.0,
            baseline_7d=40.0,
            accumulated_cost=accumulated,
            resets_at_5h=_expired_resets_at(1.0),  # expired 1 hour ago
            resets_at_7d=_future_resets_at(168),
        )

        now = datetime.utcnow()
        result = calculate_synthetic_with_rollover(state, "5x", token_costs, now)

        assert result["is_synthetic"] is True
        assert (
            result["synthetic_5h"] < 60.0
        ), f"After 5h rollover, synthetic_5h {result['synthetic_5h']} should be < 60.0"

    def test_rollover_7d_resets_synthetic_to_cost_in_new_window(self):
        """When 7d window expired, synthetic_7d based only on cost in new window."""
        from pacemaker.fallback import (
            calculate_synthetic_with_rollover,
            load_token_costs,
        )

        token_costs = load_token_costs()
        state = _make_fallback_state(
            baseline_5h=40.0,
            baseline_7d=70.0,
            accumulated_cost=8.0,
            resets_at_5h=_future_resets_at(5),
            resets_at_7d=_expired_resets_at(2.0),  # expired 2 hours ago
        )

        now = datetime.utcnow()
        result = calculate_synthetic_with_rollover(state, "5x", token_costs, now)

        assert (
            result["synthetic_7d"] < 70.0
        ), f"After 7d rollover, synthetic_7d {result['synthetic_7d']} should be < 70.0"

    def test_rollover_projects_resets_at_forward(self):
        """After 5h rollover, five_resets in result is projected into the future."""
        from pacemaker.fallback import (
            calculate_synthetic_with_rollover,
            load_token_costs,
        )

        token_costs = load_token_costs()
        state = _make_fallback_state(
            resets_at_5h=_expired_resets_at(1.0),
            resets_at_7d=_future_resets_at(168),
        )

        now = datetime.utcnow()
        result = calculate_synthetic_with_rollover(state, "5x", token_costs, now)

        five_resets = result.get("five_resets")
        assert five_resets is not None, "five_resets should be projected forward"
        assert (
            five_resets > now
        ), f"Projected five_resets {five_resets} should be in the future (now={now})"

    def test_both_windows_rolled(self):
        """Both 5h and 7d windows expired: both get rollover treatment."""
        from pacemaker.fallback import (
            calculate_synthetic_with_rollover,
            load_token_costs,
        )

        token_costs = load_token_costs()
        state = _make_fallback_state(
            baseline_5h=80.0,
            baseline_7d=75.0,
            accumulated_cost=5.0,
            resets_at_5h=_expired_resets_at(1.0),
            resets_at_7d=_expired_resets_at(24.0),
        )

        now = datetime.utcnow()
        result = calculate_synthetic_with_rollover(state, "5x", token_costs, now)

        assert result["synthetic_5h"] < 80.0, "5h should be rolled over"
        assert result["synthetic_7d"] < 75.0, "7d should be rolled over"
        assert result["five_resets"] is not None
        assert result["seven_resets"] is not None
        assert result["five_resets"] > now
        assert result["seven_resets"] > now


class TestRolloverSavesState:
    """Rollover must persist rollover_cost and last_rollover_resets to state file."""

    def test_rollover_saves_rollover_cost_5h(self, tmp_path):
        """After 5h rollover, fallback_state.json has rollover_cost_5h set."""
        from pacemaker.fallback import (
            calculate_synthetic_with_rollover,
            load_token_costs,
            load_fallback_state,
            save_fallback_state,
        )

        token_costs = load_token_costs()
        state_path = str(tmp_path / "fallback_state.json")
        accumulated = 7.5
        state = _make_fallback_state(
            accumulated_cost=accumulated,
            resets_at_5h=_expired_resets_at(1.0),
            resets_at_7d=_future_resets_at(168),
        )
        # Write state to file first
        save_fallback_state(state, state_path)

        now = datetime.utcnow()
        result = calculate_synthetic_with_rollover(state, "5x", token_costs, now)

        # Function mutates state in-place but does not save to disk.
        # Caller must save when state_changed is True.
        if result["state_changed"]:
            save_fallback_state(state, state_path)

        saved = load_fallback_state(state_path)
        assert saved["rollover_cost_5h"] == pytest.approx(
            accumulated, rel=0.01
        ), f"rollover_cost_5h should be {accumulated}, got {saved['rollover_cost_5h']}"

    def test_rollover_saves_last_rollover_resets_5h(self, tmp_path):
        """After 5h rollover, last_rollover_resets_5h is set in saved state."""
        from pacemaker.fallback import (
            calculate_synthetic_with_rollover,
            load_token_costs,
            load_fallback_state,
            save_fallback_state,
        )

        token_costs = load_token_costs()
        state_path = str(tmp_path / "fallback_state.json")
        state = _make_fallback_state(
            resets_at_5h=_expired_resets_at(1.0),
            resets_at_7d=_future_resets_at(168),
        )
        save_fallback_state(state, state_path)

        now = datetime.utcnow()
        result = calculate_synthetic_with_rollover(state, "5x", token_costs, now)

        if result["state_changed"]:
            save_fallback_state(state, state_path)

        saved = load_fallback_state(state_path)
        assert saved["last_rollover_resets_5h"] is not None

    def test_no_rollover_does_not_modify_state_file(self, tmp_path):
        """When no window rolls over, state file is not written."""
        from pacemaker.fallback import (
            calculate_synthetic_with_rollover,
            load_token_costs,
            save_fallback_state,
        )

        token_costs = load_token_costs()
        state_path = str(tmp_path / "fallback_state.json")
        state = _make_fallback_state(
            resets_at_5h=_future_resets_at(5),
            resets_at_7d=_future_resets_at(168),
        )
        save_fallback_state(state, state_path)

        # Record mtime before
        mtime_before = Path(state_path).stat().st_mtime

        now = datetime.utcnow()
        result = calculate_synthetic_with_rollover(state, "5x", token_costs, now)

        # Only save if state_changed (which should be False for non-expired windows)
        if result["state_changed"]:
            save_fallback_state(state, state_path)

        mtime_after = Path(state_path).stat().st_mtime
        assert (
            mtime_before == mtime_after
        ), "State file should not be written when no rollover"


class TestRolloverWithNoneResetsAt:
    """Windows with resets_at=None are skipped cleanly."""

    def test_none_resets_5h_skips_rollover(self):
        """resets_at_5h=None: no rollover for 5h window, result still valid."""
        from pacemaker.fallback import (
            calculate_synthetic_with_rollover,
            load_token_costs,
        )

        token_costs = load_token_costs()
        state = _make_fallback_state(
            baseline_5h=30.0,
            resets_at_5h=None,
            resets_at_7d=_future_resets_at(168),
        )

        now = datetime.utcnow()
        result = calculate_synthetic_with_rollover(state, "5x", token_costs, now)

        assert result["is_synthetic"] is True
        assert result.get("five_resets") is None

    def test_none_resets_7d_skips_rollover(self):
        """resets_at_7d=None: no rollover for 7d window, result still valid."""
        from pacemaker.fallback import (
            calculate_synthetic_with_rollover,
            load_token_costs,
        )

        token_costs = load_token_costs()
        state = _make_fallback_state(
            resets_at_5h=_future_resets_at(5),
            resets_at_7d=None,
        )

        now = datetime.utcnow()
        result = calculate_synthetic_with_rollover(state, "5x", token_costs, now)

        assert result["is_synthetic"] is True
        assert result.get("seven_resets") is None


class TestResultStructure:
    """Result dict must have required keys from calculate_synthetic_with_rollover()."""

    def test_result_has_synthetic_5h(self):
        """Result has synthetic_5h key."""
        from pacemaker.fallback import (
            calculate_synthetic_with_rollover,
            load_token_costs,
        )

        token_costs = load_token_costs()
        state = _make_fallback_state(
            resets_at_5h=_future_resets_at(5), resets_at_7d=_future_resets_at(168)
        )
        result = calculate_synthetic_with_rollover(
            state, "5x", token_costs, datetime.utcnow()
        )
        assert "synthetic_5h" in result

    def test_result_has_synthetic_7d(self):
        """Result has synthetic_7d key."""
        from pacemaker.fallback import (
            calculate_synthetic_with_rollover,
            load_token_costs,
        )

        token_costs = load_token_costs()
        state = _make_fallback_state(
            resets_at_5h=_future_resets_at(5), resets_at_7d=_future_resets_at(168)
        )
        result = calculate_synthetic_with_rollover(
            state, "5x", token_costs, datetime.utcnow()
        )
        assert "synthetic_7d" in result

    def test_result_has_is_synthetic_true(self):
        """Result has is_synthetic=True."""
        from pacemaker.fallback import (
            calculate_synthetic_with_rollover,
            load_token_costs,
        )

        token_costs = load_token_costs()
        state = _make_fallback_state(
            resets_at_5h=_future_resets_at(5), resets_at_7d=_future_resets_at(168)
        )
        result = calculate_synthetic_with_rollover(
            state, "5x", token_costs, datetime.utcnow()
        )
        assert result["is_synthetic"] is True

    def test_result_has_five_resets(self):
        """Result has five_resets key (datetime or None)."""
        from pacemaker.fallback import (
            calculate_synthetic_with_rollover,
            load_token_costs,
        )

        token_costs = load_token_costs()
        state = _make_fallback_state(
            resets_at_5h=_future_resets_at(5), resets_at_7d=_future_resets_at(168)
        )
        result = calculate_synthetic_with_rollover(
            state, "5x", token_costs, datetime.utcnow()
        )
        assert "five_resets" in result

    def test_result_has_seven_resets(self):
        """Result has seven_resets key (datetime or None)."""
        from pacemaker.fallback import (
            calculate_synthetic_with_rollover,
            load_token_costs,
        )

        token_costs = load_token_costs()
        state = _make_fallback_state(
            resets_at_5h=_future_resets_at(5), resets_at_7d=_future_resets_at(168)
        )
        result = calculate_synthetic_with_rollover(
            state, "5x", token_costs, datetime.utcnow()
        )
        assert "seven_resets" in result

    def test_result_has_state_changed(self):
        """Result has state_changed key."""
        from pacemaker.fallback import (
            calculate_synthetic_with_rollover,
            load_token_costs,
        )

        token_costs = load_token_costs()
        state = _make_fallback_state(
            resets_at_5h=_future_resets_at(5), resets_at_7d=_future_resets_at(168)
        )
        result = calculate_synthetic_with_rollover(
            state, "5x", token_costs, datetime.utcnow()
        )
        assert "state_changed" in result

    def test_no_rollover_state_changed_is_false(self):
        """When windows have not expired, state_changed is False."""
        from pacemaker.fallback import (
            calculate_synthetic_with_rollover,
            load_token_costs,
        )

        token_costs = load_token_costs()
        state = _make_fallback_state(
            accumulated_cost=4.5,
            resets_at_5h=_future_resets_at(5),
            resets_at_7d=_future_resets_at(168),
        )
        result = calculate_synthetic_with_rollover(
            state, "5x", token_costs, datetime.utcnow()
        )
        assert result["state_changed"] is False


class TestTierDifferenceInRollover:
    """20x tier produces lower synthetic values than 5x for the same state."""

    def test_20x_produces_lower_synthetic_5h_after_rollover(self):
        """
        After 5h rollover, 20x tier synthetic_5h is lower than 5x tier.
        This verifies the tier parameter flows through rollover calculation correctly.
        """
        from pacemaker.fallback import (
            calculate_synthetic_with_rollover,
            load_token_costs,
        )

        token_costs = load_token_costs()
        now = datetime.utcnow()
        # Set rollover_cost_5h=10.0 to simulate $10 accumulated before rollover.
        # With accumulated_cost=20.0, cost_in_window = 20.0 - 10.0 = 10.0
        # This gives non-zero synthetic values where tier coefficients differ.
        expired_5h = _expired_resets_at(1.0)
        # Pre-compute the projected resets_at so last_rollover_resets matches
        parsed = datetime.fromisoformat(expired_5h.replace("+00:00", ""))
        while parsed <= now:
            parsed += timedelta(hours=5)
        projected_str = parsed.isoformat()
        base_state = {
            "baseline_5h": 0.0,
            "baseline_7d": 0.0,
            "accumulated_cost": 20.0,
            "resets_at_5h": expired_5h,
            "resets_at_7d": _future_resets_at(168),
            "rollover_cost_5h": 10.0,
            "rollover_cost_7d": None,
            "last_rollover_resets_5h": projected_str,
            "last_rollover_resets_7d": None,
            "entered_at": time.time() - 3600,
        }
        import copy

        state_5x = copy.deepcopy(base_state)
        state_20x = copy.deepcopy(base_state)

        result_5x = calculate_synthetic_with_rollover(state_5x, "5x", token_costs, now)
        result_20x = calculate_synthetic_with_rollover(
            state_20x, "20x", token_costs, now
        )

        assert result_20x["synthetic_5h"] < result_5x["synthetic_5h"], (
            f"20x synthetic_5h {result_20x['synthetic_5h']} should be < "
            f"5x synthetic_5h {result_5x['synthetic_5h']}"
        )
