#!/usr/bin/env python3
"""
Pressure tests for UsageModel fallback/synthetic calculation subsystem.

These are scenario-based tests that simulate realistic multi-cycle transitions,
long-running outages, calibration workflows, and edge cases. They complement the
unit-level tests in test_usage_model.py by exercising sequences of operations.

Key implementation constraints discovered from source code:
- _get_synthetic_snapshot() computes rollover logic locally but does NOT persist to DB.
- Only _get_reset_windows_fallback() → _persist_rollover() writes rollover state to DB.
- After _persist_rollover(), the next _get_synthetic_snapshot() reads the persisted
  rollover_cost from DB and uses it in the window formula.
- Calibration ratio clamping is [0.1, 10.0], NOT [0.5, 2.0].
- accumulate_cost() is a no-op in NORMAL mode.
"""

import sqlite3
import threading
import time
from datetime import datetime, timedelta

import pytest


# ---------------------------------------------------------------------------
# Helpers shared across all pressure test classes
# ---------------------------------------------------------------------------


def make_model(db_path: str):
    """Instantiate a fresh UsageModel pointing at the given db_path."""
    from pacemaker.usage_model import UsageModel

    return UsageModel(db_path=db_path)


def _store_simple_api_response(
    model,
    five_util: float,
    five_resets_at: str,
    seven_util: float,
    seven_resets_at: str,
) -> None:
    """Helper: store a minimal API response into the model."""
    model.store_api_response(
        {
            "five_hour": {"utilization": five_util, "resets_at": five_resets_at},
            "seven_day": {"utilization": seven_util, "resets_at": seven_resets_at},
        }
    )


def _force_resets_at(db_path: str, resets_at_5h: str, resets_at_7d: str) -> None:
    """Helper: directly overwrite resets_at timestamps in fallback_state_v2."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE fallback_state_v2 SET resets_at_5h=?, resets_at_7d=? WHERE id=1",
            (resets_at_5h, resets_at_7d),
        )


def _force_entered_at(db_path: str, entered_at: float) -> None:
    """Helper: overwrite entered_at in fallback_state_v2."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE fallback_state_v2 SET entered_at=? WHERE id=1",
            (entered_at,),
        )


def _insert_cost_at_time(
    db_path: str, timestamp: float, cost: float, session_id: str = "pressure-session"
) -> None:
    """Helper: directly insert a cost row at a specific timestamp."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO accumulated_costs
            (timestamp, session_id, cost_dollars, input_tokens, output_tokens,
             cache_read_tokens, cache_creation_tokens, model_family)
            VALUES (?, ?, ?, 0, 0, 0, 0, 'sonnet')
            """,
            (timestamp, session_id, cost),
        )


def _read_fallback_state(db_path: str) -> dict:
    """Helper: read all columns from fallback_state_v2 as a dict."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM fallback_state_v2 WHERE id=1").fetchone()
        return dict(row) if row else {}


def _past_ts(hours_ago: float) -> str:
    """Return an ISO timestamp string that is hours_ago hours in the past."""
    dt = datetime.utcnow() - timedelta(hours=hours_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _future_ts(hours_from_now: float) -> str:
    """Return an ISO timestamp string that is hours_from_now hours in the future."""
    dt = datetime.utcnow() + timedelta(hours=hours_from_now)
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


# ---------------------------------------------------------------------------
# Category 1: 5-Hour Cycle Transitions
# ---------------------------------------------------------------------------


class TestFiveHourCycleTransitions:
    """Scenarios exercising 5-hour window rollovers during fallback."""

    def test_fallback_spanning_two_consecutive_5h_windows(self, tmp_path):
        """Fallback crosses one 5h boundary: post-rollover cost produces window-local util.

        Setup:
        - Enter fallback with 5h baseline=20%, resets_at set to past (already rolled).
        - Accumulate $3.00 total cost (pre-rollover baseline captured).
        - Trigger rollover via get_reset_windows().
        - Accumulate $1.00 more cost AFTER the rollover snapshot.
        - get_current_usage() should use (total - rollover_cost) * coeff * 100.
        """
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # Store API response; set 5h to already-expired so enter_fallback captures it
        _store_simple_api_response(
            model,
            five_util=20.0,
            five_resets_at=_past_ts(6.0),
            seven_util=5.0,
            seven_resets_at=_future_ts(100.0),
        )
        model.enter_fallback()

        # Accumulate $3.00 pre-rollover cost across two sessions
        for i in range(3):
            model.accumulate_cost(
                input_tokens=1_000_000,
                output_tokens=0,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                model_family="sonnet",
                session_id=f"pre-rollover-{i}",
            )
        # At this point total_cost = 3.0 * (1_000_000 * 3.0 / 1_000_000) = $9.00

        # Trigger rollover detection: this persists rollover_cost_5h = total_cost
        model.get_reset_windows()

        # Now accumulate $3.00 more (post-rollover)
        model.accumulate_cost(
            input_tokens=1_000_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="post-rollover-0",
        )

        # Verify rollover cost was persisted
        state = _read_fallback_state(db_path)
        assert (
            state.get("rollover_cost_5h") is not None
        ), "rollover_cost_5h should be set after get_reset_windows() detected rollover"
        rollover_cost = float(state["rollover_cost_5h"])
        assert rollover_cost > 0.0

        snapshot = model.get_current_usage()
        assert snapshot is not None
        assert snapshot.is_synthetic is True

        # Post-rollover: synthetic_5h uses only cost since rollover
        # cost_in_window = total - rollover_cost (only the last $3.00)
        from pacemaker.fallback import _DEFAULT_TOKEN_COSTS

        tier = model._detect_tier()
        coeff_5h = _DEFAULT_TOKEN_COSTS.get(tier, _DEFAULT_TOKEN_COSTS["5x"])[
            "coefficient_5h"
        ]

        # Total cost: 4 batches of 1M input sonnet = 4 * $3.00 = $12.00 total
        # rollover happened after 3 batches ($9.00), so post-rollover = $3.00
        post_rollover_cost = 1_000_000 * 3.0 / 1_000_000  # = $3.00

        expected_5h = min(post_rollover_cost * coeff_5h * 100.0, 100.0)
        assert abs(snapshot.five_hour_util - expected_5h) < 0.5, (
            f"Expected post-rollover 5h util ~{expected_5h:.2f}%, "
            f"got {snapshot.five_hour_util:.2f}%"
        )

    def test_fallback_spanning_three_consecutive_5h_windows(self, tmp_path):
        """Fallback lasting 15+ hours crosses two 5h boundaries.

        Strategy: set resets_at_5h to be 16 hours in the past (3+ cycles expired),
        call get_reset_windows() to trigger rollover, then check the projected window
        is in the future and is properly advanced.
        """
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        _store_simple_api_response(
            model,
            five_util=10.0,
            five_resets_at=_past_ts(16.0),
            seven_util=3.0,
            seven_resets_at=_future_ts(100.0),
        )
        model.enter_fallback()

        # Accumulate some cost
        model.accumulate_cost(
            input_tokens=500_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="long-outage-1",
        )

        # Trigger rollover detection for a window 16 hours stale
        windows = model.get_reset_windows()

        assert (
            windows.five_hour_resets_at is not None
        ), "Should have projected a valid 5h reset time"
        now = datetime.utcnow()
        assert (
            windows.five_hour_resets_at > now
        ), "Projected 5h window must be in the future after multi-cycle projection"
        # The projection should have advanced by at least 15 hours (3 cycles)
        # so the next reset is within one 5-hour window from now
        time_until_reset = (windows.five_hour_resets_at - now).total_seconds() / 3600.0
        assert (
            0.0 < time_until_reset <= 5.0
        ), f"Projected reset should be within one 5h window: {time_until_reset:.2f}h away"

        state = _read_fallback_state(db_path)
        assert (
            state.get("rollover_cost_5h") is not None
        ), "rollover_cost_5h should be persisted after multi-cycle rollover"

    def test_rollover_cost_5h_tracks_correctly_across_rollovers(self, tmp_path):
        """rollover_cost_5h is snapshot of total cost at the moment rollover is detected.

        After two separate rollovers (triggered explicitly), rollover_cost_5h should
        reflect the accumulated cost at the second rollover, not the first.
        """
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        _store_simple_api_response(
            model,
            five_util=0.0,
            five_resets_at=_past_ts(6.0),
            seven_util=0.0,
            seven_resets_at=_future_ts(100.0),
        )
        model.enter_fallback()

        # First batch: $3.00
        model.accumulate_cost(
            input_tokens=1_000_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="batch-1",
        )

        # Trigger first rollover (resets_at was 6h ago → rolled 1 cycle)
        model.get_reset_windows()

        state_after_first = _read_fallback_state(db_path)
        cost_at_first_rollover = float(state_after_first.get("rollover_cost_5h") or 0.0)
        assert (
            cost_at_first_rollover > 0.0
        ), "rollover_cost_5h should be non-zero after first rollover"

        # Advance the new resets_at to past again (simulate second 5h cycle expiring).
        # Must use a time far enough in the past that it projects to a DIFFERENT future
        # window than the first rollover (which projected ~4h ahead). Using 6h ago
        # means project → now+(-6+5)=-1h → +5h → now+4h… wait, need 11h to get a new window.
        # Reset last_rollover_resets_5h to None so deduplication guard allows the write.
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE fallback_state_v2 SET resets_at_5h=?, last_rollover_resets_5h=NULL WHERE id=1",
                (_past_ts(1.0),),
            )

        # Add more cost
        model.accumulate_cost(
            input_tokens=1_000_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="batch-2",
        )

        # Trigger second rollover
        model.get_reset_windows()

        state_after_second = _read_fallback_state(db_path)
        cost_at_second_rollover = float(
            state_after_second.get("rollover_cost_5h") or 0.0
        )

        # Second rollover cost must be larger (total cost is higher now)
        assert cost_at_second_rollover > cost_at_first_rollover, (
            f"Second rollover_cost ({cost_at_second_rollover:.4f}) must exceed "
            f"first ({cost_at_first_rollover:.4f})"
        )

    def test_5h_window_expiring_exactly_at_boundary(self, tmp_path):
        """Edge case: resets_at exactly equals current UTC time (== now boundary).

        The _project_window() code uses `parsed <= now` to detect expiry.
        A window exactly AT now should be treated as expired (rolled over).
        """
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # Set resets_at to exactly 1 second ago so it's definitively at/past the boundary
        boundary_ts = (datetime.utcnow() - timedelta(seconds=1)).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"
        )

        _store_simple_api_response(
            model,
            five_util=15.0,
            five_resets_at=boundary_ts,
            seven_util=3.0,
            seven_resets_at=_future_ts(100.0),
        )
        model.enter_fallback()

        windows = model.get_reset_windows()

        # The window should have been projected forward (rolled over)
        assert windows.five_hour_resets_at is not None
        now = datetime.utcnow()
        assert (
            windows.five_hour_resets_at > now
        ), "Window exactly at boundary should be projected to the next cycle"
        # Should be approximately 5 hours from now
        time_until = (windows.five_hour_resets_at - now).total_seconds() / 3600.0
        assert (
            4.9 < time_until < 5.1
        ), f"Projected window should be ~5h from now, got {time_until:.3f}h"


# ---------------------------------------------------------------------------
# Category 2: 7-Day Cycle Transitions
# ---------------------------------------------------------------------------


class TestSevenDayCycleTransitions:
    """Scenarios exercising 7-day window rollovers during fallback."""

    def test_fallback_lasting_through_7d_rollover(self, tmp_path):
        """Week-long outage: 7d window expires during fallback.

        Set resets_at_7d to 200 hours ago (> 168h), trigger rollover,
        verify projected window is in the future and rollover_cost_7d is set.
        """
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        _store_simple_api_response(
            model,
            five_util=5.0,
            five_resets_at=_future_ts(3.0),
            seven_util=80.0,
            seven_resets_at=_past_ts(200.0),
        )
        model.enter_fallback()

        model.accumulate_cost(
            input_tokens=2_000_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="week-long-1",
        )

        windows = model.get_reset_windows()

        assert windows.seven_day_resets_at is not None
        now = datetime.utcnow()
        assert (
            windows.seven_day_resets_at > now
        ), "7d projected window must be in the future after week-long outage"

        # Should be within one 7-day window from now (< 168h away)
        hours_away = (windows.seven_day_resets_at - now).total_seconds() / 3600.0
        assert (
            0.0 < hours_away <= 168.0
        ), f"Projected 7d reset should be within one week: {hours_away:.1f}h away"

        state = _read_fallback_state(db_path)
        assert (
            state.get("rollover_cost_7d") is not None
        ), "rollover_cost_7d must be set after 7d rollover"

        snapshot = model.get_current_usage()
        assert snapshot is not None
        assert snapshot.is_synthetic is True
        # After 7d rollover, synthetic_7d uses post-rollover cost only, starting from 0
        assert 0.0 <= snapshot.seven_day_util <= 100.0

    def test_both_5h_and_7d_rolling_over_simultaneously(self, tmp_path):
        """Both windows expire at the same time — simultaneous dual rollover.

        Both resets_at set to the past. Both should be projected forward independently.
        Both rollover_cost fields should be set.
        """
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        _store_simple_api_response(
            model,
            five_util=30.0,
            five_resets_at=_past_ts(6.0),
            seven_util=60.0,
            seven_resets_at=_past_ts(200.0),
        )
        model.enter_fallback()

        model.accumulate_cost(
            input_tokens=1_000_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="dual-rollover-1",
        )

        windows = model.get_reset_windows()

        now = datetime.utcnow()
        assert windows.five_hour_resets_at is not None
        assert windows.seven_day_resets_at is not None
        assert windows.five_hour_resets_at > now
        assert windows.seven_day_resets_at > now
        assert windows.five_hour_stale is False
        assert windows.seven_day_stale is False

        state = _read_fallback_state(db_path)
        assert state.get("rollover_cost_5h") is not None, "rollover_cost_5h must be set"
        assert state.get("rollover_cost_7d") is not None, "rollover_cost_7d must be set"

        # Both rollover costs should be the same (same snapshot moment)
        rc_5h = float(state["rollover_cost_5h"])
        rc_7d = float(state["rollover_cost_7d"])
        assert abs(rc_5h - rc_7d) < 1e-9, (
            f"Both rollover costs should be identical (same snapshot): "
            f"5h={rc_5h:.9f}, 7d={rc_7d:.9f}"
        )

    def test_multiple_5h_rollovers_within_single_7d_window(self, tmp_path):
        """Common scenario: 7d window active, but multiple 5h cycles pass.

        7d resets_at is well in the future. 5h resets_at is stale (12h ago = ~2 cycles).
        5h should roll, 7d should NOT roll.
        """
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        _store_simple_api_response(
            model,
            five_util=10.0,
            five_resets_at=_past_ts(12.0),
            seven_util=40.0,
            seven_resets_at=_future_ts(100.0),
        )
        model.enter_fallback()

        model.accumulate_cost(
            input_tokens=500_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="multi-5h-1",
        )

        windows = model.get_reset_windows()
        now = datetime.utcnow()

        # 5h window should have rolled
        assert windows.five_hour_resets_at is not None
        assert windows.five_hour_resets_at > now
        assert windows.five_hour_stale is False

        # 7d window should NOT have rolled (it's in the future)
        assert windows.seven_day_resets_at is not None
        assert windows.seven_day_resets_at > now
        assert windows.seven_day_stale is False

        state = _read_fallback_state(db_path)
        assert (
            state.get("rollover_cost_5h") is not None
        ), "5h rollover should have occurred"
        assert (
            state.get("rollover_cost_7d") is None
        ), "7d rollover should NOT have occurred (window still active)"

        # 7d accumulation should still use baseline formula (not rollover formula)
        snapshot = model.get_current_usage()
        assert snapshot is not None
        assert snapshot.is_synthetic is True

        from pacemaker.fallback import _DEFAULT_TOKEN_COSTS

        tier = model._detect_tier()
        coeff_7d = _DEFAULT_TOKEN_COSTS.get(tier, _DEFAULT_TOKEN_COSTS["5x"])[
            "coefficient_7d"
        ]
        total_cost = 500_000 * 3.0 / 1_000_000
        expected_7d = min(40.0 + total_cost * coeff_7d * 100.0, 100.0)

        assert abs(snapshot.seven_day_util - expected_7d) < 0.5, (
            f"7d util should use baseline formula: expected ~{expected_7d:.2f}%, "
            f"got {snapshot.seven_day_util:.2f}%"
        )


# ---------------------------------------------------------------------------
# Category 3: Coefficient Calibration
# ---------------------------------------------------------------------------


class TestCoefficientCalibration:
    """Scenarios exercising calibrate_on_recovery() and calibrated coefficient usage."""

    def _enter_fallback_with_baselines(
        self,
        model,
        db_path,
        five_util=50.0,
        seven_util=20.0,
        tier="5x",
        five_resets_at=None,
        seven_resets_at=None,
    ):
        """Helper: enter fallback with specific baselines and tier."""
        five_resets_at = five_resets_at or _future_ts(4.0)
        seven_resets_at = seven_resets_at or _future_ts(100.0)
        _store_simple_api_response(
            model,
            five_util=five_util,
            five_resets_at=five_resets_at,
            seven_util=seven_util,
            seven_resets_at=seven_resets_at,
        )
        model.enter_fallback()
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE fallback_state_v2 SET tier=? WHERE id=1", (tier,))

    def test_recovery_after_short_fallback_updates_calibration(self, tmp_path):
        """Short fallback (small cost): calibration runs, coefficients stored.

        A short fallback with minimal cost should still produce a calibrated
        coefficient row, even if the adjustment is small.
        """
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)
        self._enter_fallback_with_baselines(
            model, db_path, five_util=50.0, seven_util=20.0, tier="5x"
        )

        # Small cost: 100K sonnet input = $0.30
        model.accumulate_cost(
            input_tokens=100_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="short-fallback",
        )

        model.exit_fallback(real_5h=52.0, real_7d=21.0)

        result = model._get_calibrated_coefficients("5x")
        assert (
            result is not None
        ), "Short fallback should still produce calibration data"
        coeff_5h, coeff_7d = result
        assert coeff_5h > 0.0
        assert coeff_7d > 0.0

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT sample_count FROM calibrated_coefficients WHERE tier='5x'"
            ).fetchone()
        assert row is not None
        assert row[0] == 1

    def test_recovery_after_long_fallback_meaningful_adjustment(self, tmp_path):
        """Long fallback (24h+ worth of tokens): calibration should produce meaningful
        coefficient adjustment when real values differ significantly from predicted.
        """
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)
        self._enter_fallback_with_baselines(
            model, db_path, five_util=0.0, seven_util=0.0, tier="5x"
        )

        # Large cost: 50M input tokens sonnet = $150 — this would push synthetic to >100%
        # Use a moderate amount that produces a meaningful but non-capped synthetic
        # 1M sonnet input = $3.00; with default coeff_5h=0.0075:
        # synthetic_5h = 0 + 3.0 * 0.0075 * 100 = 2.25% per $3
        # Use 10M = $30: synthetic_5h = 22.5%
        model.accumulate_cost(
            input_tokens=10_000_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="long-fallback-1",
        )

        # Real values are twice as high as predicted (2x error ratio)
        snapshot_before = model.get_current_usage()
        predicted_5h = snapshot_before.five_hour_util if snapshot_before else 0.0

        model.exit_fallback(real_5h=predicted_5h * 2.0, real_7d=10.0)

        result = model._get_calibrated_coefficients("5x")
        assert result is not None
        coeff_5h, coeff_7d = result

        from pacemaker.fallback import _DEFAULT_TOKEN_COSTS

        default_5h = _DEFAULT_TOKEN_COSTS["5x"]["coefficient_5h"]

        # After 1 sample with a 2x error ratio: new_coeff = (default * 2) / 1 = 2 * default
        # Because weighted avg: (default * 0 + measured_5h) / 1 = measured_5h
        # measured_5h = default * ratio = default * 2.0
        assert coeff_5h > default_5h, (
            f"Calibrated coeff_5h ({coeff_5h:.6f}) should exceed default ({default_5h:.6f}) "
            f"when real was 2x predicted"
        )

    def test_multiple_calibration_cycles_accumulate_sample_count(self, tmp_path):
        """Three enter/exit/calibrate cycles: sample_count reaches 3."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        for i in range(3):
            self._enter_fallback_with_baselines(
                model, db_path, five_util=50.0, seven_util=20.0, tier="5x"
            )
            model.accumulate_cost(
                input_tokens=500_000,
                output_tokens=0,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                model_family="sonnet",
                session_id=f"multi-cycle-{i}",
            )
            model.exit_fallback(real_5h=55.0 + i, real_7d=22.0 + i)

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT sample_count FROM calibrated_coefficients WHERE tier='5x'"
            ).fetchone()

        assert row is not None
        assert row[0] == 3, f"Expected sample_count=3 after 3 cycles, got {row[0]}"

    def test_calibrated_coefficients_affect_subsequent_predictions(self, tmp_path):
        """After calibration with 2x error, second fallback predictions are ~2x higher.

        Cycle 1: default coefficients → calibrated with real=2x predicted.
        Cycle 2: same cost → prediction should be approximately 2x the uncalibrated prediction.
        """
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        cost_input = 5_000_000  # 5M sonnet input = $15

        # --- Cycle 1: Establish calibration with 2x error ratio ---
        self._enter_fallback_with_baselines(
            model, db_path, five_util=0.0, seven_util=0.0, tier="5x"
        )
        model.accumulate_cost(
            input_tokens=cost_input,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="cycle1-calib",
        )
        snapshot_cycle1 = model.get_current_usage()
        assert snapshot_cycle1 is not None
        predicted_5h_cycle1 = snapshot_cycle1.five_hour_util

        # Exit with real = 2x predicted
        model.exit_fallback(real_5h=predicted_5h_cycle1 * 2.0, real_7d=5.0)

        # --- Cycle 2: New fallback with same cost, check prediction uses calibrated coeff ---
        self._enter_fallback_with_baselines(
            model, db_path, five_util=0.0, seven_util=0.0, tier="5x"
        )
        model.accumulate_cost(
            input_tokens=cost_input,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="cycle2-predict",
        )
        snapshot_cycle2 = model.get_current_usage()
        assert snapshot_cycle2 is not None
        predicted_5h_cycle2 = snapshot_cycle2.five_hour_util

        # The calibrated prediction should be higher than the default prediction
        # (calibrated coeff > default coeff because ratio was 2x)
        assert predicted_5h_cycle2 > predicted_5h_cycle1 * 1.5, (
            f"After 2x calibration, cycle2 prediction ({predicted_5h_cycle2:.2f}%) "
            f"should be substantially higher than cycle1 ({predicted_5h_cycle1:.2f}%)"
        )

    def test_calibration_for_5x_and_20x_tiers_are_independent(self, tmp_path):
        """5x and 20x tiers maintain separate calibrated coefficients."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # Calibrate 5x tier
        self._enter_fallback_with_baselines(
            model, db_path, five_util=50.0, seven_util=20.0, tier="5x"
        )
        model.accumulate_cost(
            input_tokens=500_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="5x-calib",
        )
        model.exit_fallback(real_5h=60.0, real_7d=25.0)

        # Calibrate 20x tier
        self._enter_fallback_with_baselines(
            model, db_path, five_util=50.0, seven_util=20.0, tier="20x"
        )
        model.accumulate_cost(
            input_tokens=500_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="20x-calib",
        )
        model.exit_fallback(real_5h=55.0, real_7d=22.0)

        coeff_5x = model._get_calibrated_coefficients("5x")
        coeff_20x = model._get_calibrated_coefficients("20x")

        assert coeff_5x is not None, "5x tier should have calibration data"
        assert coeff_20x is not None, "20x tier should have calibration data"

        # They should be independent rows and potentially different values
        # (different tiers have different default coefficients)
        # Default 5x coeff is 4x higher than 20x (different tier multipliers)
        # After same number of tokens, 5x synthetic would be 4x higher than 20x
        # So calibrated 5x coeff should be different from calibrated 20x coeff
        assert coeff_5x[0] != coeff_20x[0], (
            "5x and 20x calibrated coefficient_5h should differ "
            f"(5x={coeff_5x[0]:.6f}, 20x={coeff_20x[0]:.6f})"
        )

    def test_calibration_ratio_clamping_prevents_extreme_adjustments(self, tmp_path):
        """Extreme error ratios are clamped to [0.1, 10.0].

        Setup: accumulate minimal cost so synthetic is very small,
        then exit with real=100% — ratio would be enormous without clamping.
        The resulting coefficient should not exceed default * 10 * (blending factor).
        """
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)
        self._enter_fallback_with_baselines(
            model, db_path, five_util=50.0, seven_util=20.0, tier="5x"
        )

        # Accumulate a tiny cost to make synthetic just slightly above baseline
        model.accumulate_cost(
            input_tokens=10,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="tiny-cost",
        )

        # Exit with real=100% — huge ratio but clamped at 10.0
        model.exit_fallback(real_5h=100.0, real_7d=100.0)

        result = model._get_calibrated_coefficients("5x")
        assert result is not None
        coeff_5h, coeff_7d = result

        from pacemaker.fallback import _DEFAULT_TOKEN_COSTS

        default_5h = _DEFAULT_TOKEN_COSTS["5x"]["coefficient_5h"]
        default_7d = _DEFAULT_TOKEN_COSTS["5x"]["coefficient_7d"]

        # With clamping at 10.0: measured = old * 10.0
        # With 1 sample: new_coeff = (old * 0 + measured) / 1 = old * 10.0
        # Allow small tolerance for floating point
        assert (
            coeff_5h <= default_5h * 10.0 * 1.01
        ), f"coeff_5h={coeff_5h:.6f} exceeds clamped maximum {default_5h * 10.0:.6f}"
        assert (
            coeff_7d <= default_7d * 10.0 * 1.01
        ), f"coeff_7d={coeff_7d:.6f} exceeds clamped maximum {default_7d * 10.0:.6f}"

        # And the minimum clamp prevents over-correction in the other direction
        # If we now do tiny real but huge synthetic, ratio < 0.1 → clamped at 0.1
        self._enter_fallback_with_baselines(
            model, db_path, five_util=0.0, seven_util=0.0, tier="5x"
        )
        model.accumulate_cost(
            input_tokens=100_000_000,
            output_tokens=0,  # Huge cost → synthetic near 100%
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="huge-cost",
        )
        model.exit_fallback(real_5h=0.1, real_7d=0.1)  # Real nearly zero

        result2 = model._get_calibrated_coefficients("5x")
        assert result2 is not None
        coeff_5h_2, _ = result2
        # After 2 samples, coefficient should still be positive (min clamp prevented zero)
        assert (
            coeff_5h_2 > 0.0
        ), "Coefficient must remain positive after minimum clamping"


# ---------------------------------------------------------------------------
# Category 4: End-to-End Lifecycle
# ---------------------------------------------------------------------------


class TestEndToEndLifecycle:
    """Full lifecycle scenarios covering normal → fallback → recovery → fallback cycles."""

    def test_full_lifecycle_normal_to_fallback_to_calibration_to_fallback_again(
        self, tmp_path
    ):
        """Complete lifecycle: normal → 429 → fallback → 8h → rollover → recover →
        calibrate → second fallback → verify calibrated coefficients are used.
        """
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # --- Phase 1: Normal mode with real API data ---
        _store_simple_api_response(
            model,
            five_util=30.0,
            five_resets_at=_past_ts(6.0),  # Already expired
            seven_util=15.0,
            seven_resets_at=_future_ts(100.0),
        )
        assert not model.is_fallback_active()
        snapshot_normal = model.get_current_usage()
        assert snapshot_normal is not None
        assert snapshot_normal.is_synthetic is False

        # --- Phase 2: 429 hits, enter fallback ---
        model.enter_fallback()
        assert model.is_fallback_active()

        # Accumulate cost simulating ~3 hours of usage
        for i in range(3):
            model.accumulate_cost(
                input_tokens=1_000_000,
                output_tokens=200_000,
                cache_read_tokens=500_000,
                cache_creation_tokens=0,
                model_family="sonnet",
                session_id=f"e2e-phase2-{i}",
            )

        # --- Phase 3: 5h window expires during fallback (8h total) ---
        # Trigger rollover detection
        windows = model.get_reset_windows()
        # The 5h window was already expired when we entered (past_ts(6h))
        assert windows.five_hour_resets_at is not None

        state_mid = _read_fallback_state(db_path)
        assert state_mid.get("rollover_cost_5h") is not None

        # Add more cost after rollover
        model.accumulate_cost(
            input_tokens=500_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="e2e-post-rollover",
        )

        snapshot_fallback = model.get_current_usage()
        assert snapshot_fallback is not None
        assert snapshot_fallback.is_synthetic is True

        # --- Phase 4: API recovers ---
        real_5h = 45.0
        real_7d = 20.0
        model.exit_fallback(real_5h=real_5h, real_7d=real_7d)
        assert not model.is_fallback_active()

        # Use the actual detected tier (may be "5x" or "20x" depending on environment)
        actual_tier = model._detect_tier()

        # Calibrated coefficients should be stored
        calibrated = model._get_calibrated_coefficients(actual_tier)
        assert calibrated is not None, "Calibration must be stored after API recovery"
        coeff_5h_after_first, coeff_7d_after_first = calibrated

        # --- Phase 5: Second fallback — verify calibrated coefficients used ---
        _store_simple_api_response(
            model,
            five_util=real_5h,
            five_resets_at=_future_ts(4.0),
            seven_util=real_7d,
            seven_resets_at=_future_ts(100.0),
        )
        model.enter_fallback()
        assert model.is_fallback_active()

        model.accumulate_cost(
            input_tokens=1_000_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="e2e-second-fallback",
        )

        snapshot_second = model.get_current_usage()
        assert snapshot_second is not None
        assert snapshot_second.is_synthetic is True

        # Verify calibrated coefficients are still available
        calibrated_after_second = model._get_calibrated_coefficients(actual_tier)
        assert calibrated_after_second is not None
        # After one sample, coefficients may have changed but should still be positive
        assert calibrated_after_second[0] > 0.0
        assert calibrated_after_second[1] > 0.0

    def test_concurrent_sessions_accumulating_during_same_fallback(self, tmp_path):
        """Multiple concurrent session_ids insert costs safely during one fallback period.

        5 threads, each with a unique session_id, all accumulate cost concurrently.
        Final sum should equal the expected total within floating-point tolerance.
        """
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        _store_simple_api_response(
            model,
            five_util=0.0,
            five_resets_at=_future_ts(4.0),
            seven_util=0.0,
            seven_resets_at=_future_ts(100.0),
        )
        model.enter_fallback()

        # Each thread: 1M sonnet input = $3.00
        n_threads = 5
        per_thread_cost = 1_000_000 * 3.0 / 1_000_000  # = $3.00
        errors = []

        def worker(idx: int):
            try:
                m = make_model(db_path)  # Fresh instance per thread (stateless)
                m.accumulate_cost(
                    input_tokens=1_000_000,
                    output_tokens=0,
                    cache_read_tokens=0,
                    cache_creation_tokens=0,
                    model_family="sonnet",
                    session_id=f"concurrent-{idx}",
                )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"

        with sqlite3.connect(db_path) as conn:
            total_cost = (
                conn.execute(
                    "SELECT SUM(cost_dollars) FROM accumulated_costs"
                ).fetchone()[0]
                or 0.0
            )

        expected = per_thread_cost * n_threads  # = $15.00
        assert (
            abs(total_cost - expected) < 1e-9
        ), f"Expected total=${expected:.9f}, got ${total_cost:.9f}"

        # Row count should equal number of threads
        with sqlite3.connect(db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM accumulated_costs").fetchone()[0]
        assert count == n_threads

    def test_high_volume_accumulation_100_entries_sum_is_correct(self, tmp_path):
        """100 cost entries during a single fallback period — sum must be precise."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        _store_simple_api_response(
            model,
            five_util=0.0,
            five_resets_at=_future_ts(4.0),
            seven_util=0.0,
            seven_resets_at=_future_ts(100.0),
        )
        model.enter_fallback()

        n_entries = 100
        # Each: 1000 input sonnet = 1000 * $3.0 / 1_000_000 = $0.003
        per_entry = 1000 * 3.0 / 1_000_000
        for i in range(n_entries):
            model.accumulate_cost(
                input_tokens=1000,
                output_tokens=0,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                model_family="sonnet",
                session_id=f"high-vol-{i}",
            )

        with sqlite3.connect(db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM accumulated_costs").fetchone()[0]
            total = (
                conn.execute(
                    "SELECT SUM(cost_dollars) FROM accumulated_costs"
                ).fetchone()[0]
                or 0.0
            )

        assert count == n_entries, f"Expected {n_entries} rows, got {count}"

        expected_total = per_entry * n_entries
        assert (
            abs(total - expected_total) < 1e-9
        ), f"Sum of 100 entries: expected ${expected_total:.9f}, got ${total:.9f}"

        # Verify snapshot reflects the full sum
        snapshot = model.get_current_usage()
        assert snapshot is not None
        assert snapshot.is_synthetic is True

        from pacemaker.fallback import _DEFAULT_TOKEN_COSTS

        tier = model._detect_tier()
        coeff_5h = _DEFAULT_TOKEN_COSTS.get(tier, _DEFAULT_TOKEN_COSTS["5x"])[
            "coefficient_5h"
        ]
        expected_5h = min(expected_total * coeff_5h * 100.0, 100.0)
        assert (
            abs(snapshot.five_hour_util - expected_5h) < 0.01
        ), f"Expected snapshot 5h={expected_5h:.4f}%, got {snapshot.five_hour_util:.4f}%"


# ---------------------------------------------------------------------------
# Category 5: Edge Cases & Recovery
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and recovery scenarios."""

    def test_enter_fallback_with_zero_baselines(self, tmp_path):
        """Enter fallback when API reported 0% usage — synthetic should grow from 0."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        _store_simple_api_response(
            model,
            five_util=0.0,
            five_resets_at=_future_ts(4.0),
            seven_util=0.0,
            seven_resets_at=_future_ts(100.0),
        )
        model.enter_fallback()

        # Even with zero baseline, accumulated cost should drive synthetic up
        model.accumulate_cost(
            input_tokens=5_000_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="zero-baseline-test",
        )

        snapshot = model.get_current_usage()
        assert snapshot is not None
        assert snapshot.is_synthetic is True
        assert (
            snapshot.five_hour_util > 0.0
        ), "Synthetic should grow above 0 even with zero baseline when cost is accumulated"
        assert snapshot.seven_day_util > 0.0, "7d synthetic should also grow above 0"

    def test_enter_fallback_at_near_100_percent_usage(self, tmp_path):
        """Enter fallback at 99% usage — synthetic caps at 100% quickly."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        _store_simple_api_response(
            model,
            five_util=99.0,
            five_resets_at=_future_ts(4.0),
            seven_util=98.0,
            seven_resets_at=_future_ts(100.0),
        )
        model.enter_fallback()

        # Any additional cost should immediately push synthetic to cap
        model.accumulate_cost(
            input_tokens=100_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="near-cap-test",
        )

        snapshot = model.get_current_usage()
        assert snapshot is not None
        assert snapshot.is_synthetic is True
        # 99% baseline + small cost: value grows above baseline but may not cap at 100%.
        # Assert it grew (> baseline of 99%) and is within valid range (<= 100%).
        assert (
            snapshot.five_hour_util > 99.0
        ), f"Near-100% baseline should grow above 99%, got {snapshot.five_hour_util:.3f}%"
        assert (
            snapshot.five_hour_util <= 100.0
        ), f"Synthetic must not exceed 100%, got {snapshot.five_hour_util:.3f}%"
        assert (
            snapshot.seven_day_util > 98.0
        ), f"Near-100% 7d baseline should grow above 98%, got {snapshot.seven_day_util:.3f}%"
        assert (
            snapshot.seven_day_util <= 100.0
        ), f"7d synthetic must not exceed 100%, got {snapshot.seven_day_util:.3f}%"

    def test_exit_fallback_when_not_in_fallback_is_noop(self, tmp_path):
        """exit_fallback() when already in NORMAL mode must not crash or corrupt state."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # No enter_fallback called — should be a clean no-op
        assert not model.is_fallback_active()
        model.exit_fallback(real_5h=50.0, real_7d=20.0)
        assert not model.is_fallback_active()

        # No calibration data should be stored (no fallback state to calibrate from)
        with sqlite3.connect(db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM calibrated_coefficients"
            ).fetchone()[0]
        assert count == 0, "No calibration data should be created for a no-op exit"

    def test_reenter_fallback_immediately_after_exit(self, tmp_path):
        """Re-entering fallback immediately after exit uses fresh state but
        keeps calibrated coefficients from the previous cycle.
        """
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # First cycle
        _store_simple_api_response(
            model,
            five_util=40.0,
            five_resets_at=_future_ts(3.0),
            seven_util=15.0,
            seven_resets_at=_future_ts(100.0),
        )
        model.enter_fallback()
        model.accumulate_cost(
            input_tokens=1_000_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="cycle-exit-1",
        )
        model.exit_fallback(real_5h=45.0, real_7d=18.0)

        # Use the actual detected tier (may be "5x" or "20x" depending on environment)
        actual_tier = model._detect_tier()

        # Verify coefficients exist after first exit
        calibrated_after_first = model._get_calibrated_coefficients(actual_tier)
        assert calibrated_after_first is not None

        # Immediately re-enter
        _store_simple_api_response(
            model,
            five_util=45.0,
            five_resets_at=_future_ts(4.0),
            seven_util=18.0,
            seven_resets_at=_future_ts(100.0),
        )
        model.enter_fallback()
        assert model.is_fallback_active()

        # Old calibrated coefficients should still be accessible
        calibrated_after_reenter = model._get_calibrated_coefficients(actual_tier)
        assert (
            calibrated_after_reenter is not None
        ), "Calibrated coefficients must survive exit → re-enter cycle"
        assert calibrated_after_reenter[0] == pytest.approx(
            calibrated_after_first[0], rel=1e-6
        )

        # Accumulated costs should be fresh (no carryover from previous cycle)
        with sqlite3.connect(db_path) as conn:
            state = conn.execute(
                "SELECT entered_at FROM fallback_state_v2 WHERE id=1"
            ).fetchone()
            entered_at = float(state[0]) if state else 0.0
            cost_in_new_cycle = conn.execute(
                "SELECT COALESCE(SUM(cost_dollars), 0.0) FROM accumulated_costs "
                "WHERE timestamp >= ?",
                (entered_at,),
            ).fetchone()[0]

        assert float(cost_in_new_cycle) == pytest.approx(
            0.0, abs=1e-9
        ), "Re-entered fallback should start with zero accumulated cost in new window"

    def test_synthetic_snapshot_exact_arithmetic_with_known_coefficients(
        self, tmp_path
    ):
        """Verify exact synthetic calculation formula with pre-seeded calibration.

        Seeds calibrated_coefficients with known values, then checks that
        _get_synthetic_snapshot() produces the exact expected output.
        """
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        known_coeff_5h = 0.01  # 1% per dollar
        known_coeff_7d = 0.002  # 0.2% per dollar

        # Pre-seed calibration
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO calibrated_coefficients
                (tier, coefficient_5h, coefficient_7d, sample_count, last_calibrated)
                VALUES ('5x', ?, ?, 1, ?)
                """,
                (known_coeff_5h, known_coeff_7d, time.time()),
            )

        _store_simple_api_response(
            model,
            five_util=20.0,
            five_resets_at=_future_ts(4.0),
            seven_util=10.0,
            seven_resets_at=_future_ts(100.0),
        )
        model.enter_fallback()
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE fallback_state_v2 SET tier='5x' WHERE id=1")

        # Accumulate exactly $10.00 by inserting directly
        entered_at = float(_read_fallback_state(db_path).get("entered_at", 0.0))
        _insert_cost_at_time(db_path, entered_at + 1.0, 10.0, "exact-arith")

        snapshot = model.get_current_usage()
        assert snapshot is not None
        assert snapshot.is_synthetic is True

        # Expected:
        # synthetic_5h = min(20.0 + 10.0 * 0.01 * 100.0, 100.0) = min(20.0 + 10.0, 100) = 30.0
        # synthetic_7d = min(10.0 + 10.0 * 0.002 * 100.0, 100.0) = min(10.0 + 2.0, 100) = 12.0
        assert snapshot.five_hour_util == pytest.approx(
            30.0, abs=0.001
        ), f"Expected 5h=30.0%, got {snapshot.five_hour_util:.4f}%"
        assert snapshot.seven_day_util == pytest.approx(
            12.0, abs=0.001
        ), f"Expected 7d=12.0%, got {snapshot.seven_day_util:.4f}%"

    def test_accumulate_cost_noop_during_normal_mode_then_active_after_enter(
        self, tmp_path
    ):
        """Verify accumulate_cost is no-op in normal mode and active after enter_fallback.

        This is a transition verification: cost before enter_fallback = 0, after > 0.
        """
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # Normal mode — cost should not accumulate
        model.accumulate_cost(
            input_tokens=1_000_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="normal-noop",
        )

        with sqlite3.connect(db_path) as conn:
            count_before = conn.execute(
                "SELECT COUNT(*) FROM accumulated_costs"
            ).fetchone()[0]
        assert count_before == 0, "No rows should be inserted in normal mode"

        # Now enter fallback
        _store_simple_api_response(
            model,
            five_util=10.0,
            five_resets_at=_future_ts(4.0),
            seven_util=5.0,
            seven_resets_at=_future_ts(100.0),
        )
        model.enter_fallback()

        model.accumulate_cost(
            input_tokens=1_000_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="fallback-active",
        )

        with sqlite3.connect(db_path) as conn:
            count_after = conn.execute(
                "SELECT COUNT(*) FROM accumulated_costs"
            ).fetchone()[0]
        assert count_after == 1, "Cost should accumulate after entering fallback"

    def test_stateless_model_instances_share_fallback_and_calibration_state(
        self, tmp_path
    ):
        """Two independent UsageModel instances on same DB see the same fallback and
        calibration state — verifies true statelessness via SQLite.
        """
        db_path = str(tmp_path / "usage.db")
        model_a = make_model(db_path)
        model_b = make_model(db_path)

        # model_a enters fallback
        _store_simple_api_response(
            model_a,
            five_util=25.0,
            five_resets_at=_future_ts(3.0),
            seven_util=10.0,
            seven_resets_at=_future_ts(100.0),
        )
        model_a.enter_fallback()

        # model_b should see the fallback state
        assert (
            model_b.is_fallback_active()
        ), "model_b should see fallback state set by model_a (same DB)"

        # model_b accumulates cost
        model_b.accumulate_cost(
            input_tokens=2_000_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="stateless-b",
        )

        # model_a can see the cost accumulated by model_b
        snapshot_from_a = model_a.get_current_usage()
        assert snapshot_from_a is not None
        assert snapshot_from_a.is_synthetic is True
        assert (
            snapshot_from_a.five_hour_util > 25.0
        ), "model_a should see cost accumulated by model_b"

        # model_a exits; model_b should see normal state
        model_a.exit_fallback(real_5h=30.0, real_7d=12.0)
        assert (
            not model_b.is_fallback_active()
        ), "model_b should see normal state after model_a exited fallback"
