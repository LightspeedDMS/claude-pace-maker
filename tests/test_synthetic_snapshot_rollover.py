#!/usr/bin/env python3
"""
Tests for Bug #46: Fallback Mode 5-Hour Window Rollover Fails to Reset Utilization.

Two bugs in _get_synthetic_snapshot():

Bug A: Fresh rollover uses ALL accumulated cost instead of only post-rollover costs.
Bug B: Rollover state is never persisted from _get_synthetic_snapshot() itself
       (only from get_reset_windows()).

Acceptance Criteria:
  Scenario 1: 5h rollover resets utilization to post-rollover costs only
  Scenario 2: _get_synthetic_snapshot() itself persists rollover state to DB
  Scenario 3: 7-day window rollover follows the same correct pattern
  Scenario 4: Multiple consecutive rollovers during extended fallback
  Scenario 5: No regression for normal (non-rollover) fallback operation
"""

import calendar
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pacemaker.usage_model import UsageModel
from pacemaker.database import execute_with_retry


def _utc_naive_to_epoch(dt: datetime) -> float:
    """Convert a naive datetime that represents UTC to a Unix epoch timestamp.

    Python's naive_datetime.timestamp() treats the naive value as *local time*,
    which is wrong when the datetime was constructed from UTC (e.g. via
    datetime.now(timezone.utc).replace(tzinfo=None)).  calendar.timegm()
    always interprets its input as UTC, so this is timezone-safe.
    """
    return float(calendar.timegm(dt.timetuple())) + dt.microsecond / 1e6


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model(tmp_path) -> tuple:
    """Create a fresh UsageModel backed by an isolated temp DB. Returns (model, db_path)."""
    db_path = str(tmp_path / "usage.db")
    model = UsageModel(db_path=db_path)
    return model, db_path


def _enter_fallback_with_past_resets(
    model: UsageModel,
    *,
    resets_at_5h_past: datetime,
    resets_at_7d_future: datetime,
    baseline_5h: float = 0.0,
    baseline_7d: float = 0.0,
) -> None:
    """
    Enter fallback mode with specific resets_at timestamps injected directly into
    the DB. This lets us simulate a 5h window that has already expired (rollover scenario).

    Args:
        resets_at_5h_past: A datetime in the PAST (UTC naive) to simulate 5h expiry.
        resets_at_7d_future: A datetime in the FUTURE (UTC naive) for 7d window.
        baseline_5h: Synthetic baseline for 5h window.
        baseline_7d: Synthetic baseline for 7d window.
    """
    # Use store_api_response + enter_fallback then overwrite the timestamps directly.
    model.store_api_response(
        {
            "five_hour": {
                "utilization": baseline_5h,
                "resets_at": resets_at_5h_past.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            },
            "seven_day": {
                "utilization": baseline_7d,
                "resets_at": resets_at_7d_future.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            },
        }
    )
    model.enter_fallback()


def _enter_fallback_with_both_past_resets(
    model: UsageModel,
    *,
    resets_at_5h_past: datetime,
    resets_at_7d_past: datetime,
    baseline_5h: float = 0.0,
    baseline_7d: float = 0.0,
) -> None:
    """Enter fallback with BOTH windows already expired (both roll over)."""
    model.store_api_response(
        {
            "five_hour": {
                "utilization": baseline_5h,
                "resets_at": resets_at_5h_past.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            },
            "seven_day": {
                "utilization": baseline_7d,
                "resets_at": resets_at_7d_past.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            },
        }
    )
    model.enter_fallback()


def _insert_cost_at_timestamp(db_path: str, cost: float, ts: float) -> None:
    """Insert a cost row at a specific Unix timestamp (bypasses is_fallback check)."""

    def op(conn):
        conn.execute(
            """
            INSERT INTO accumulated_costs
            (timestamp, session_id, cost_dollars,
             input_tokens, output_tokens, cache_read_tokens,
             cache_creation_tokens, model_family)
            VALUES (?, 'test', ?, 0, 0, 0, 0, 'sonnet')
            """,
            (ts, cost),
        )

    execute_with_retry(db_path, op)


def _override_entered_at(db_path: str, entered_at: float = 0.0) -> None:
    """Override entered_at in fallback_state_v2 so all inserted cost rows
    pass the 'timestamp >= entered_at' filter regardless of system timezone.

    enter_fallback() sets entered_at = time.time() (real UTC epoch), but test
    cost timestamps are computed via _utc_naive_to_epoch() from naive datetimes
    that are 5h behind time.time() on a UTC-5 system.  Setting entered_at to 0
    ensures every cost row is included in accumulated-cost queries.
    """

    def op(conn):
        conn.execute(
            "UPDATE fallback_state_v2 SET entered_at = ? WHERE id = 1",
            (entered_at,),
        )

    execute_with_retry(db_path, op)


def _get_fallback_state(db_path: str) -> dict:
    """Read the raw fallback_state_v2 row as a dict."""

    def op(conn):
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM fallback_state_v2 WHERE id = 1").fetchone()

    row = execute_with_retry(db_path, op, readonly=True)
    return dict(row) if row else {}


# ---------------------------------------------------------------------------
# Scenario 1: 5h rollover resets utilization to post-rollover costs only
# ---------------------------------------------------------------------------


class TestFiveHourRolloverResetsUtilization:
    """
    Scenario 1: Synthetic utilization resets after 5-hour rollover.

    Given pace-maker is in fallback mode with $80 accumulated before rollover
    And $5 accumulated after the 5-hour window rolls over
    When _get_synthetic_snapshot() is called
    Then the 5-hour synthetic utilization reflects only the $5 post-rollover cost
    And the pre-rollover $80 is excluded from the calculation
    """

    def test_five_hour_rollover_uses_only_post_rollover_cost(self, tmp_path):
        """
        BUG A: Fresh 5h rollover must use only costs accumulated AFTER the rollover
        boundary, not the total accumulated_cost since entered_at.

        With coefficient_5h=0.0075, post_rollover_cost=$5:
          synthetic_5h = 5.0 * 0.0075 * 100 = 3.75%
        NOT 85.0 * 0.0075 * 100 = 63.75% (which is the buggy result).
        """
        model, db_path = _make_model(tmp_path)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # 5h window expired 10 minutes ago
        rollover_boundary = now - timedelta(minutes=10)
        # 7d window still 6 days in the future
        future_7d = now + timedelta(days=6)

        _enter_fallback_with_past_resets(
            model,
            resets_at_5h_past=rollover_boundary,
            resets_at_7d_future=future_7d,
        )
        _override_entered_at(db_path)

        # Insert $80 of cost BEFORE the rollover boundary
        pre_rollover_ts = _utc_naive_to_epoch(rollover_boundary - timedelta(minutes=5))
        _insert_cost_at_timestamp(db_path, 80.0, pre_rollover_ts)

        # Insert $5 of cost AFTER the rollover boundary
        post_rollover_ts = _utc_naive_to_epoch(rollover_boundary + timedelta(minutes=2))
        _insert_cost_at_timestamp(db_path, 5.0, post_rollover_ts)

        snapshot = model._get_synthetic_snapshot()

        assert snapshot is not None, "Expected a synthetic snapshot"

        # With coeff_5h=0.0075, post_rollover=$5:
        # synthetic_5h = 5.0 * 0.0075 * 100 = 3.75%
        # The bug produces: 85.0 * 0.0075 * 100 = 63.75%
        expected_5h = 5.0 * 0.0075 * 100.0
        assert snapshot.five_hour_util == pytest.approx(expected_5h, abs=0.5), (
            f"Expected synthetic_5h ~{expected_5h:.2f}% (post-rollover cost only), "
            f"got {snapshot.five_hour_util:.2f}% (bug: all accumulated cost used)"
        )

    def test_pre_rollover_cost_is_excluded_from_five_hour_utilization(self, tmp_path):
        """
        Pre-rollover costs must NOT contribute to 5h synthetic utilization.
        Only costs after the boundary matter.
        """
        model, db_path = _make_model(tmp_path)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rollover_boundary = now - timedelta(minutes=5)
        future_7d = now + timedelta(days=6)

        _enter_fallback_with_past_resets(
            model,
            resets_at_5h_past=rollover_boundary,
            resets_at_7d_future=future_7d,
        )
        _override_entered_at(db_path)

        # Insert heavy pre-rollover cost
        pre_ts = _utc_naive_to_epoch(rollover_boundary - timedelta(hours=3))
        _insert_cost_at_timestamp(db_path, 200.0, pre_ts)

        # Insert zero post-rollover cost (nothing after boundary)
        # With no post-rollover cost, synthetic_5h must be near 0%
        snapshot = model._get_synthetic_snapshot()

        assert snapshot is not None
        # No post-rollover cost → synthetic_5h should be ~0%
        assert snapshot.five_hour_util == pytest.approx(0.0, abs=0.5), (
            f"Expected synthetic_5h ~0% (no post-rollover cost), "
            f"got {snapshot.five_hour_util:.2f}%"
        )


# ---------------------------------------------------------------------------
# Scenario 2: Rollover state persisted by _get_synthetic_snapshot() itself
# ---------------------------------------------------------------------------


class TestRolloverStatePersistedBySyntheticSnapshot:
    """
    Scenario 2: Rollover state is persisted by _get_synthetic_snapshot() itself.

    Given pace-maker is in fallback mode
    And get_reset_windows() has NOT been called
    When _get_synthetic_snapshot() detects a 5-hour rollover
    Then it persists rollover_cost_5h to the database
    And subsequent calls use the persisted rollover offset correctly
    """

    def test_synthetic_snapshot_persists_rollover_cost_5h(self, tmp_path):
        """
        BUG B: _get_synthetic_snapshot() must persist rollover_cost_5h to the DB
        when it detects a fresh rollover, so subsequent calls use Branch 1 (stored offset).
        """
        model, db_path = _make_model(tmp_path)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rollover_boundary = now - timedelta(minutes=10)
        future_7d = now + timedelta(days=6)

        _enter_fallback_with_past_resets(
            model,
            resets_at_5h_past=rollover_boundary,
            resets_at_7d_future=future_7d,
        )
        _override_entered_at(db_path)

        # Insert $30 total ($25 pre-rollover, $5 post-rollover)
        pre_ts = _utc_naive_to_epoch(rollover_boundary - timedelta(minutes=30))
        _insert_cost_at_timestamp(db_path, 25.0, pre_ts)
        post_ts = _utc_naive_to_epoch(rollover_boundary + timedelta(minutes=2))
        _insert_cost_at_timestamp(db_path, 5.0, post_ts)

        # Verify rollover_cost_5h is NULL before calling _get_synthetic_snapshot
        state_before = _get_fallback_state(db_path)
        assert (
            state_before.get("rollover_cost_5h") is None
        ), "rollover_cost_5h should be NULL before snapshot"

        # Call _get_synthetic_snapshot() — this should detect the rollover AND persist it
        snapshot = model._get_synthetic_snapshot()
        assert snapshot is not None

        # Verify rollover_cost_5h was persisted to the DB
        state_after = _get_fallback_state(db_path)
        assert state_after.get("rollover_cost_5h") is not None, (
            "BUG B: _get_synthetic_snapshot() must persist rollover_cost_5h "
            "when it detects a fresh rollover"
        )

    def test_subsequent_call_uses_persisted_rollover_offset(self, tmp_path):
        """
        After _get_synthetic_snapshot() persists the rollover offset, a second call
        must produce the same result (using Branch 1: stored_rollover_5h).
        """
        model, db_path = _make_model(tmp_path)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rollover_boundary = now - timedelta(minutes=10)
        future_7d = now + timedelta(days=6)

        _enter_fallback_with_past_resets(
            model,
            resets_at_5h_past=rollover_boundary,
            resets_at_7d_future=future_7d,
        )
        _override_entered_at(db_path)

        pre_ts = _utc_naive_to_epoch(rollover_boundary - timedelta(minutes=30))
        _insert_cost_at_timestamp(db_path, 25.0, pre_ts)
        post_ts = _utc_naive_to_epoch(rollover_boundary + timedelta(minutes=2))
        _insert_cost_at_timestamp(db_path, 5.0, post_ts)

        # First call — should detect rollover, persist it, return correct value
        snapshot1 = model._get_synthetic_snapshot()
        assert snapshot1 is not None

        # Second call — should use persisted rollover_cost_5h (Branch 1)
        snapshot2 = model._get_synthetic_snapshot()
        assert snapshot2 is not None

        # Both calls must produce the same 5h utilization
        assert snapshot1.five_hour_util == pytest.approx(
            snapshot2.five_hour_util, abs=0.1
        ), (
            f"First call: {snapshot1.five_hour_util:.2f}%, "
            f"Second call: {snapshot2.five_hour_util:.2f}% — must be consistent"
        )

        # And the value must reflect only post-rollover cost ($5 * 0.0075 * 100 = 3.75%)
        expected_5h = 5.0 * 0.0075 * 100.0
        assert snapshot2.five_hour_util == pytest.approx(
            expected_5h, abs=0.5
        ), f"Second call must reflect post-rollover cost only, got {snapshot2.five_hour_util:.2f}%"


# ---------------------------------------------------------------------------
# Scenario 3: 7-day window rollover follows same correct pattern
# ---------------------------------------------------------------------------


class TestSevenDayRolloverResetsUtilization:
    """
    Scenario 3: 7-day window rollover follows same correct pattern.

    Given pace-maker is in fallback mode spanning a 7-day window rollover
    When _get_synthetic_snapshot() is called after the 7-day rollover
    Then the 7-day synthetic utilization reflects only post-rollover costs
    """

    def test_seven_day_rollover_uses_only_post_rollover_cost(self, tmp_path):
        """
        Bug A (7d): Fresh 7d rollover must use only costs after the boundary,
        not all accumulated cost since entered_at.

        With coefficient_7d=0.0011, post_rollover_cost=$10:
          synthetic_7d = 10.0 * 0.0011 * 100 = 1.1%
        NOT (90+10) * 0.0011 * 100 = 11.0% (buggy result).
        """
        model, db_path = _make_model(tmp_path)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # 5h still in future
        future_5h = now + timedelta(hours=3)
        # 7d window expired 1 hour ago
        rollover_boundary_7d = now - timedelta(hours=1)

        _enter_fallback_with_both_past_resets(
            model,
            resets_at_5h_past=future_5h,  # won't roll over (in future, so no rollover)
            resets_at_7d_past=rollover_boundary_7d,
        )

        # We need 5h window to NOT roll over for this test to isolate 7d behavior.
        # Overwrite resets_at_5h directly to be in the future.
        def fix_5h_resets(conn):
            conn.execute(
                "UPDATE fallback_state_v2 SET resets_at_5h = ? WHERE id = 1",
                ((now + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S"),),
            )

        execute_with_retry(db_path, fix_5h_resets)
        _override_entered_at(db_path)

        # Insert $90 before 7d boundary
        pre_ts = _utc_naive_to_epoch(rollover_boundary_7d - timedelta(days=3))
        _insert_cost_at_timestamp(db_path, 90.0, pre_ts)

        # Insert $10 after 7d boundary
        post_ts = _utc_naive_to_epoch(rollover_boundary_7d + timedelta(minutes=30))
        _insert_cost_at_timestamp(db_path, 10.0, post_ts)

        snapshot = model._get_synthetic_snapshot()

        assert snapshot is not None

        # With coeff_7d=0.0011, post_rollover=$10:
        # synthetic_7d = 10.0 * 0.0011 * 100 = 1.1%
        expected_7d = 10.0 * 0.0011 * 100.0
        assert snapshot.seven_day_util == pytest.approx(expected_7d, abs=0.3), (
            f"Expected synthetic_7d ~{expected_7d:.2f}% (post-rollover only), "
            f"got {snapshot.seven_day_util:.2f}%"
        )

    def test_seven_day_rollover_state_persisted_by_synthetic_snapshot(self, tmp_path):
        """
        BUG B (7d): _get_synthetic_snapshot() must also persist rollover_cost_7d
        when it detects a fresh 7d rollover.
        """
        model, db_path = _make_model(tmp_path)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        future_5h = now + timedelta(hours=3)
        rollover_boundary_7d = now - timedelta(hours=1)

        _enter_fallback_with_both_past_resets(
            model,
            resets_at_5h_past=future_5h,
            resets_at_7d_past=rollover_boundary_7d,
        )

        # Fix 5h to be in future so only 7d rolls
        def fix_5h_resets(conn):
            conn.execute(
                "UPDATE fallback_state_v2 SET resets_at_5h = ? WHERE id = 1",
                ((now + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S"),),
            )

        execute_with_retry(db_path, fix_5h_resets)
        _override_entered_at(db_path)

        pre_ts = _utc_naive_to_epoch(rollover_boundary_7d - timedelta(hours=2))
        _insert_cost_at_timestamp(db_path, 50.0, pre_ts)
        post_ts = _utc_naive_to_epoch(rollover_boundary_7d + timedelta(minutes=30))
        _insert_cost_at_timestamp(db_path, 10.0, post_ts)

        state_before = _get_fallback_state(db_path)
        assert state_before.get("rollover_cost_7d") is None

        snapshot = model._get_synthetic_snapshot()
        assert snapshot is not None

        state_after = _get_fallback_state(db_path)
        assert state_after.get("rollover_cost_7d") is not None, (
            "BUG B (7d): _get_synthetic_snapshot() must persist rollover_cost_7d "
            "when it detects a fresh 7d rollover"
        )


# ---------------------------------------------------------------------------
# Scenario 4: Multiple consecutive rollovers
# ---------------------------------------------------------------------------


class TestMultipleConsecutiveRollovers:
    """
    Scenario 4: Multiple consecutive rollovers during extended fallback.

    Given pace-maker has been in fallback mode through multiple 5-hour windows
    When a second rollover occurs
    Then the rollover offset is updated to reflect the new boundary
    And utilization reflects only costs since the latest rollover
    """

    def test_second_rollover_updates_offset_correctly(self, tmp_path):
        """
        After two consecutive 5h rollovers, the rollover offset must reflect
        the second boundary, and utilization must use only costs after that.
        """
        model, db_path = _make_model(tmp_path)

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # First rollover was 6 hours ago (well past one 5h window)
        # Second rollover was 1 hour ago (one more 5h window after first)
        first_boundary = now - timedelta(hours=6)
        future_7d = now + timedelta(days=6)

        _enter_fallback_with_past_resets(
            model,
            resets_at_5h_past=first_boundary,
            resets_at_7d_future=future_7d,
        )
        _override_entered_at(db_path)

        # Cost before first boundary: $100
        pre_first_ts = _utc_naive_to_epoch(first_boundary - timedelta(hours=1))
        _insert_cost_at_timestamp(db_path, 100.0, pre_first_ts)

        # Cost between first and second boundary: $20
        # Second boundary = first_boundary + 5h = now - 1h
        between_ts = _utc_naive_to_epoch(first_boundary + timedelta(hours=2))
        _insert_cost_at_timestamp(db_path, 20.0, between_ts)

        # Cost after second boundary: $3
        post_second_ts = _utc_naive_to_epoch(now - timedelta(minutes=30))
        _insert_cost_at_timestamp(db_path, 3.0, post_second_ts)

        snapshot = model._get_synthetic_snapshot()
        assert snapshot is not None

        # _project_window advances first_boundary by 5h twice to get past 'now'.
        # The latest rollover boundary is second_boundary = first_boundary + 10h (2 increments).
        # Wait: now - first_boundary = 6h. We need to advance past now:
        #   first + 5h = now - 1h (still past now? no — now - 1h < now, so still past)
        #   first + 10h = now + 4h (future) → rolled=True, boundary = now + 4h
        #   But 'old' boundary before projection = first + 5h = now - 1h
        # Post-rollover costs = costs after (now - 1h) = $3
        # Expected: 3.0 * 0.0075 * 100 = 2.25%
        expected_5h = 3.0 * 0.0075 * 100.0
        assert snapshot.five_hour_util == pytest.approx(expected_5h, abs=0.5), (
            f"Expected ~{expected_5h:.2f}% (only costs after latest rollover boundary), "
            f"got {snapshot.five_hour_util:.2f}%"
        )


# ---------------------------------------------------------------------------
# Scenario 5: No regression for normal non-rollover fallback operation
# ---------------------------------------------------------------------------


class TestNoRegressionNormalFallback:
    """
    Scenario 5: No regression for normal (non-rollover) fallback operation.

    Given pace-maker is in fallback mode within the same 5-hour window
    When _get_synthetic_snapshot() is called
    Then synthetic utilization = baseline + (accumulated_cost * coefficient * 100)
    And existing behavior is unchanged
    """

    def test_no_rollover_uses_baseline_plus_accumulated_cost(self, tmp_path):
        """
        When no rollover has occurred, synthetic_5h = baseline_5h + accumulated * coeff * 100.
        """
        model, db_path = _make_model(tmp_path)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # Both windows still in the future (no rollover)
        future_5h = now + timedelta(hours=3)
        future_7d = now + timedelta(days=6)

        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 30.0,
                    "resets_at": future_5h.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                },
                "seven_day": {
                    "utilization": 20.0,
                    "resets_at": future_7d.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                },
            }
        )
        model.enter_fallback()

        # Accumulate $10 in cost during fallback
        model.accumulate_cost(
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="test",
        )
        # Insert $10 directly
        ts = time.time()
        _insert_cost_at_timestamp(db_path, 10.0, ts)

        snapshot = model._get_synthetic_snapshot()

        assert snapshot is not None
        assert snapshot.is_synthetic is True

        # synthetic_5h = baseline_5h + accumulated * coeff_5h * 100
        # = 30.0 + 10.0 * 0.0075 * 100 = 30.0 + 7.5 = 37.5%
        expected_5h = 30.0 + 10.0 * 0.0075 * 100.0
        assert snapshot.five_hour_util == pytest.approx(expected_5h, abs=0.5), (
            f"Expected ~{expected_5h:.2f}% (baseline + accumulated), "
            f"got {snapshot.five_hour_util:.2f}%"
        )

    def test_no_rollover_seven_day_uses_baseline_plus_accumulated(self, tmp_path):
        """
        When no 7d rollover, synthetic_7d = baseline_7d + accumulated * coeff_7d * 100.
        """
        model, db_path = _make_model(tmp_path)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        future_5h = now + timedelta(hours=3)
        future_7d = now + timedelta(days=6)

        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 25.0,
                    "resets_at": future_5h.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                },
                "seven_day": {
                    "utilization": 15.0,
                    "resets_at": future_7d.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                },
            }
        )
        model.enter_fallback()

        _insert_cost_at_timestamp(db_path, 20.0, time.time())

        snapshot = model._get_synthetic_snapshot()

        assert snapshot is not None

        # synthetic_7d = baseline_7d + accumulated * coeff_7d * 100
        # = 15.0 + 20.0 * 0.0011 * 100 = 15.0 + 2.2 = 17.2%
        expected_7d = 15.0 + 20.0 * 0.0011 * 100.0
        assert snapshot.seven_day_util == pytest.approx(
            expected_7d, abs=0.3
        ), f"Expected ~{expected_7d:.2f}%, got {snapshot.seven_day_util:.2f}%"

    def test_stored_rollover_branch_unchanged(self, tmp_path):
        """
        When rollover_cost_5h is already persisted (Branch 1), the calculation
        cost_in_window = accumulated - stored_rollover must remain unchanged.
        """
        model, db_path = _make_model(tmp_path)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        future_5h = now + timedelta(hours=4)
        future_7d = now + timedelta(days=6)

        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 0.0,
                    "resets_at": future_5h.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                },
                "seven_day": {
                    "utilization": 0.0,
                    "resets_at": future_7d.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                },
            }
        )
        model.enter_fallback()

        # Simulate a previously persisted rollover: rollover_cost_5h = $50
        # (as if get_reset_windows() already persisted this)
        def set_rollover(conn):
            conn.execute(
                "UPDATE fallback_state_v2 SET rollover_cost_5h = 50.0 WHERE id = 1"
            )

        execute_with_retry(db_path, set_rollover)

        # Accumulated total since entered_at = $55 ($50 pre-rollover + $5 post)
        _insert_cost_at_timestamp(db_path, 55.0, time.time())

        snapshot = model._get_synthetic_snapshot()

        assert snapshot is not None

        # Branch 1: cost_in_window = accumulated - stored_rollover = 55 - 50 = 5
        # synthetic_5h = 5 * 0.0075 * 100 = 3.75%
        expected_5h = 5.0 * 0.0075 * 100.0
        assert snapshot.five_hour_util == pytest.approx(expected_5h, abs=0.5), (
            f"Expected ~{expected_5h:.2f}% (Branch 1: stored rollover), "
            f"got {snapshot.five_hour_util:.2f}%"
        )
