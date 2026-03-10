#!/usr/bin/env python3
"""
UsageModel — single source of truth for all usage metrics.

Story #42: Phases 1-4 (schema migration, core class, API storage, fallback).

Design principles:
- Stateless between calls: all state lives in SQLite (WAL mode).
- Can be instantiated fresh on every hook invocation.
- No JSON files: all reads/writes go through SQLite.
- Concurrency-safe accumulate_cost: uses INSERT (no read-modify-write).
- Raw API data in api_cache; synthetic state in fallback_state_v2.
"""

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any

from .database import execute_with_retry, initialize_database
from .fallback import (
    FallbackState,
    API_PRICING,
    _DEFAULT_TOKEN_COSTS,
    parse_api_datetime,
    _project_window,
)
from .logger import log_warning, log_info


def _ensure_utc_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Make a naive datetime UTC-aware; pass through aware datetimes and None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class UsageSnapshot:
    """Current best-known usage (real API or synthetic during fallback)."""

    five_hour_util: float
    five_hour_resets_at: Optional[datetime]
    seven_day_util: float
    seven_day_resets_at: Optional[datetime]
    is_synthetic: bool
    timestamp: datetime


@dataclass
class ResetWindows:
    """Properly tracked reset windows with staleness flags."""

    five_hour_resets_at: Optional[datetime]
    seven_day_resets_at: Optional[datetime]
    five_hour_stale: bool
    seven_day_stale: bool


class UsageModel:
    """Single source of truth for all usage metrics.

    Stateless between calls — all state lives in SQLite.
    Can be instantiated fresh on every hook invocation.

    Usage::

        model = UsageModel()
        snapshot = model.get_current_usage()
        if snapshot:
            print(f"5h: {snapshot.five_hour_util:.1f}%")
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or str(Path.home() / ".claude-pace-maker" / "usage.db")
        # Initialize schema (additive — existing tables preserved)
        initialize_database(self.db_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_current_usage(self) -> Optional[UsageSnapshot]:
        """Return current best-known usage (real API or synthetic during fallback).

        This is THE single method both repos call.

        Returns:
            UsageSnapshot, or None if no data available.
        """
        try:
            if self.is_fallback_active():
                return self._get_synthetic_snapshot()
            return self._get_api_snapshot()
        except Exception as e:
            log_warning("usage_model", "Failed to get current usage", e)
            return None

    def is_fallback_active(self) -> bool:
        """Check if currently in fallback mode (SQLite only)."""
        try:

            def operation(conn):
                row = conn.execute(
                    "SELECT state FROM fallback_state_v2 WHERE id = 1"
                ).fetchone()
                if row is None:
                    return False
                return row[0] == FallbackState.FALLBACK.value

            return execute_with_retry(self.db_path, operation, readonly=True)

        except Exception as e:
            log_warning("usage_model", "Failed to check fallback state", e)
            return False

    # ------------------------------------------------------------------
    # Backoff state (replaces api_backoff.py JSON)
    # ------------------------------------------------------------------

    _BACKOFF_BASE_DELAY = 300  # 5 minutes base
    _BACKOFF_MAX_DELAY = 3600  # 60 minutes cap

    def is_in_backoff(self) -> bool:
        """Check if currently in API backoff period."""
        try:

            def operation(conn):
                row = conn.execute(
                    "SELECT backoff_until FROM backoff_state WHERE id = 1"
                ).fetchone()
                if row is None or row[0] is None:
                    return False
                return time.time() < row[0]

            return execute_with_retry(self.db_path, operation, readonly=True)

        except Exception as e:
            log_warning("usage_model", "Failed to check backoff state", e)
            return False

    def get_backoff_remaining(self) -> float:
        """Get seconds remaining in current backoff period, or 0.0."""
        try:

            def operation(conn):
                row = conn.execute(
                    "SELECT backoff_until FROM backoff_state WHERE id = 1"
                ).fetchone()
                if row is None or row[0] is None:
                    return 0.0
                return max(0.0, row[0] - time.time())

            return execute_with_retry(self.db_path, operation, readonly=True)

        except Exception as e:
            log_warning("usage_model", "Failed to get backoff remaining", e)
            return 0.0

    def record_429(self) -> None:
        """Record a 429 rate-limit response with exponential backoff."""
        try:

            def operation(conn):
                row = conn.execute(
                    "SELECT consecutive_429s FROM backoff_state WHERE id = 1"
                ).fetchone()
                old_count = row[0] if row else 0
                new_count = old_count + 1
                delay = min(
                    self._BACKOFF_BASE_DELAY * (2**new_count),
                    self._BACKOFF_MAX_DELAY,
                )
                backoff_until = time.time() + delay
                conn.execute(
                    """
                    INSERT OR REPLACE INTO backoff_state
                    (id, consecutive_429s, backoff_until, last_success_time)
                    VALUES (1, ?, ?, (SELECT last_success_time FROM backoff_state WHERE id = 1))
                    """,
                    (new_count, backoff_until),
                )
                return new_count, delay

            new_count, delay = execute_with_retry(self.db_path, operation)
            log_warning(
                "usage_model",
                f"Rate limited (429). Consecutive count: {new_count}. "
                f"Backing off for {delay:.0f}s.",
            )

        except Exception as e:
            log_warning("usage_model", "Failed to record 429", e)

    def record_success(self) -> None:
        """Record successful API call, reset backoff state."""
        try:

            def operation(conn):
                row = conn.execute(
                    "SELECT consecutive_429s FROM backoff_state WHERE id = 1"
                ).fetchone()
                had_backoff = row is not None and row[0] > 0
                conn.execute(
                    """
                    INSERT OR REPLACE INTO backoff_state
                    (id, consecutive_429s, backoff_until, last_success_time)
                    VALUES (1, 0, NULL, ?)
                    """,
                    (time.time(),),
                )
                return had_backoff

            had_backoff = execute_with_retry(self.db_path, operation)
            if had_backoff:
                log_info("usage_model", "API call succeeded, backoff state reset.")

        except Exception as e:
            log_warning("usage_model", "Failed to record success", e)

    def enter_fallback(self, api_cache_snapshot: bool = True) -> None:
        """Transition to fallback mode, snapshots baselines from api_cache table.

        Idempotent: if already in FALLBACK, does not reset accumulated_costs.

        Args:
            api_cache_snapshot: If True (default), read baselines from api_cache.
        """
        try:
            # Idempotent check
            if self.is_fallback_active():
                log_info("usage_model", "Already in fallback mode, not resetting costs")
                return

            # Read baselines from api_cache table
            baseline_5h = 0.0
            baseline_7d = 0.0
            resets_at_5h: Optional[str] = None
            resets_at_7d: Optional[str] = None

            if api_cache_snapshot:
                cache = self.get_api_cache()
                if cache is not None:
                    baseline_5h = float(cache.get("five_hour_util") or 0.0)
                    baseline_7d = float(cache.get("seven_day_util") or 0.0)
                    resets_at_5h = cache.get("five_hour_resets_at")
                    resets_at_7d = cache.get("seven_day_resets_at")

            # Synthesize resets_at when null so rollover detection has valid timestamps
            now_utc = datetime.now(timezone.utc)
            if not resets_at_5h:
                resets_at_5h = (now_utc + timedelta(hours=5)).strftime(
                    "%Y-%m-%dT%H:%M:%S+00:00"
                )
            if not resets_at_7d:
                resets_at_7d = (now_utc + timedelta(hours=168)).strftime(
                    "%Y-%m-%dT%H:%M:%S+00:00"
                )

            # Detect tier from profile cache
            tier = self._detect_tier()

            entered_at = time.time()

            def operation(conn):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO fallback_state_v2
                    (id, state, baseline_5h, baseline_7d, resets_at_5h, resets_at_7d,
                     tier, entered_at,
                     rollover_cost_5h, rollover_cost_7d,
                     last_rollover_resets_5h, last_rollover_resets_7d)
                    VALUES (1, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)
                    """,
                    (
                        FallbackState.FALLBACK.value,
                        baseline_5h,
                        baseline_7d,
                        resets_at_5h,
                        resets_at_7d,
                        tier,
                        entered_at,
                    ),
                )

            execute_with_retry(self.db_path, operation)
            log_info(
                "usage_model",
                f"Entered fallback. Baselines: 5h={baseline_5h:.1f}%, 7d={baseline_7d:.1f}%",
            )
        except Exception as e:
            log_warning("usage_model", "Failed to enter fallback mode", e)

    def exit_fallback(self, real_5h: float, real_7d: float) -> None:
        """Transition from FALLBACK back to NORMAL after API recovery.

        Calls calibrate_on_recovery() before resetting state so we can compare
        our synthetic predictions against the real API values.

        Args:
            real_5h: Real 5-hour utilization from recovered API.
            real_7d: Real 7-day utilization from recovered API.
        """
        try:
            if not self.is_fallback_active():
                return  # Already normal

            # Calibrate before resetting state (needs fallback state to compute synthetic)
            self.calibrate_on_recovery(real_5h, real_7d)

            def operation(conn):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO fallback_state_v2
                    (id, state, baseline_5h, baseline_7d, resets_at_5h, resets_at_7d,
                     tier, entered_at,
                     rollover_cost_5h, rollover_cost_7d,
                     last_rollover_resets_5h, last_rollover_resets_7d)
                    VALUES (1, ?, 0.0, 0.0, NULL, NULL, '5x', NULL, NULL, NULL, NULL, NULL)
                    """,
                    (FallbackState.NORMAL.value,),
                )

            execute_with_retry(self.db_path, operation)
            log_info(
                "usage_model",
                f"Exited fallback. Real: 5h={real_5h:.1f}%, 7d={real_7d:.1f}%",
            )
        except Exception as e:
            log_warning("usage_model", "Failed to exit fallback mode", e)

    def accumulate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_creation_tokens: int,
        model_family: str,
        session_id: str,
    ) -> None:
        """INSERT cost row — no read-modify-write.

        Only accumulates when in FALLBACK mode. No-op in NORMAL mode.

        Args:
            input_tokens: Number of input tokens.
            output_tokens: Number of output tokens.
            cache_read_tokens: Number of cache read tokens.
            cache_creation_tokens: Number of cache creation tokens.
            model_family: Model family ("opus", "sonnet", "haiku").
            session_id: Unique identifier for the accumulating session.
        """
        try:
            if not self.is_fallback_active():
                return  # No-op when not in fallback

            pricing = API_PRICING.get(model_family.lower()) or API_PRICING["sonnet"]
            cost = (
                input_tokens * pricing["input"] / 1_000_000
                + output_tokens * pricing["output"] / 1_000_000
                + cache_read_tokens * pricing["cache_read"] / 1_000_000
                + cache_creation_tokens * pricing["cache_create"] / 1_000_000
            )

            ts = time.time()

            def operation(conn):
                conn.execute(
                    """
                    INSERT INTO accumulated_costs
                    (timestamp, session_id, cost_dollars,
                     input_tokens, output_tokens, cache_read_tokens,
                     cache_creation_tokens, model_family)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts,
                        session_id,
                        cost,
                        input_tokens,
                        output_tokens,
                        cache_read_tokens,
                        cache_creation_tokens,
                        model_family.lower(),
                    ),
                )

            execute_with_retry(self.db_path, operation)

        except Exception as e:
            log_warning("usage_model", "Failed to accumulate cost", e)

    def store_api_response(self, response_data: Dict[str, Any]) -> None:
        """Store raw API response in api_cache table (singleton upsert).

        Args:
            response_data: Raw API response dict with five_hour and seven_day keys.
        """
        try:
            five_hour = response_data.get("five_hour") or {}
            seven_day = response_data.get("seven_day") or {}

            five_hour_util = float(five_hour.get("utilization") or 0.0)
            seven_day_util = float(seven_day.get("utilization") or 0.0)
            five_hour_resets_at = five_hour.get("resets_at")
            seven_day_resets_at = seven_day.get("resets_at")
            raw_json = json.dumps(response_data)
            ts = time.time()

            def operation(conn):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO api_cache
                    (id, timestamp, five_hour_util, five_hour_resets_at,
                     seven_day_util, seven_day_resets_at, raw_response)
                    VALUES (1, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts,
                        five_hour_util,
                        five_hour_resets_at,
                        seven_day_util,
                        seven_day_resets_at,
                        raw_json,
                    ),
                )

            execute_with_retry(self.db_path, operation)

        except Exception as e:
            log_warning("usage_model", "Failed to store API response", e)

    def get_api_cache(self) -> Optional[Dict[str, Any]]:
        """Get last cached API response from api_cache table.

        Returns:
            Dict with five_hour_util, seven_day_util, resets_at fields,
            and raw_response (parsed dict), or None if empty.
        """
        try:

            def operation(conn):
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT timestamp, five_hour_util, five_hour_resets_at,
                           seven_day_util, seven_day_resets_at, raw_response
                    FROM api_cache WHERE id = 1
                    """
                ).fetchone()
                if row is None:
                    return None
                result = dict(row)
                # Parse raw_response JSON if present
                if result.get("raw_response"):
                    try:
                        result["raw_response"] = json.loads(result["raw_response"])
                    except Exception:
                        pass
                return result

            return execute_with_retry(self.db_path, operation, readonly=True)

        except Exception as e:
            log_warning("usage_model", "Failed to get API cache", e)
            return None

    def get_reset_windows(self) -> ResetWindows:
        """Return properly tracked reset windows with staleness flags.

        During fallback mode, reads from fallback_state_v2 and projects stale
        windows forward using _project_window(). Persists projected windows back
        to DB so they survive across invocations.

        Returns:
            ResetWindows with five_hour_resets_at, seven_day_resets_at, and
            staleness flags.
        """
        now = datetime.now(timezone.utc)
        # parse_api_datetime returns naive datetimes; strip tzinfo for comparisons
        now_naive = now.replace(tzinfo=None)
        stale_cutoff = timedelta(minutes=5)

        if self.is_fallback_active():
            return self._get_reset_windows_fallback(now_naive, stale_cutoff)

        cache = self.get_api_cache()
        if cache is None:
            return ResetWindows(
                five_hour_resets_at=None,
                seven_day_resets_at=None,
                five_hour_stale=True,
                seven_day_stale=True,
            )

        five_resets_str = cache.get("five_hour_resets_at")
        seven_resets_str = cache.get("seven_day_resets_at")

        five_dt = parse_api_datetime(five_resets_str)
        seven_dt = parse_api_datetime(seven_resets_str)

        # Stale if reset time is more than 5 minutes in the past
        five_stale = five_dt is None or (now_naive - five_dt) > stale_cutoff
        seven_stale = seven_dt is None or (now_naive - seven_dt) > stale_cutoff

        return ResetWindows(
            five_hour_resets_at=_ensure_utc_aware(five_dt),
            seven_day_resets_at=_ensure_utc_aware(seven_dt),
            five_hour_stale=five_stale,
            seven_day_stale=seven_stale,
        )

    def _get_reset_windows_fallback(self, now: datetime, stale_cutoff) -> ResetWindows:
        """Compute reset windows during fallback by projecting stale timestamps forward."""
        try:

            def load_state(conn):
                conn.row_factory = sqlite3.Row
                return conn.execute(
                    "SELECT * FROM fallback_state_v2 WHERE id = 1"
                ).fetchone()

            row = execute_with_retry(self.db_path, load_state, readonly=True)
            if row is None:
                return ResetWindows(None, None, True, True)

            state = dict(row)
            five_resets, five_rolled = _project_window(
                state.get("resets_at_5h"), window_hours=5.0, now=now
            )
            seven_resets, seven_rolled = _project_window(
                state.get("resets_at_7d"), window_hours=168.0, now=now
            )

            # Persist projected windows and rollover costs if anything changed
            if five_rolled or seven_rolled:
                self._persist_rollover(
                    state, five_resets, five_rolled, seven_resets, seven_rolled
                )

            # Projected windows are computed (not stale); unprojectable ones are stale
            five_stale = five_resets is None
            seven_stale = seven_resets is None

            return ResetWindows(
                five_hour_resets_at=_ensure_utc_aware(five_resets),
                seven_day_resets_at=_ensure_utc_aware(seven_resets),
                five_hour_stale=five_stale,
                seven_day_stale=seven_stale,
            )
        except Exception as e:
            log_warning("usage_model", "Failed to get fallback reset windows", e)
            return ResetWindows(None, None, True, True)

    def _persist_rollover(
        self,
        state: dict,
        five_resets: Optional[datetime],
        five_rolled: bool,
        seven_resets: Optional[datetime],
        seven_rolled: bool,
        old_boundary_5h: Optional[datetime] = None,
        old_boundary_7d: Optional[datetime] = None,
    ) -> None:
        """Persist projected windows and rollover costs to fallback_state_v2.

        Called when _project_window() detects a rollover. Stores the accumulated
        cost BEFORE the last-expired boundary as the rollover offset so that
        Branch 1 (stored_rollover_* is not None) computes only post-rollover cost:

            cost_in_window = accumulated_total - rollover_cost_offset

        Args:
            state: Current fallback_state_v2 row as a dict.
            five_resets: Projected future 5h reset boundary (from _project_window).
            five_rolled: True if the 5h window has expired.
            seven_resets: Projected future 7d reset boundary.
            seven_rolled: True if the 7d window has expired.
            old_boundary_5h: The last-expired 5h boundary (= five_resets - 5h).
                             Costs strictly before this timestamp are pre-rollover.
            old_boundary_7d: The last-expired 7d boundary (= seven_resets - 168h).
        """
        try:
            entered_at = state.get("entered_at") or 0.0

            def sum_costs_before(boundary_ts: float) -> float:
                """Sum costs accumulated since entered_at but BEFORE the boundary."""

                def query(conn):
                    result = conn.execute(
                        "SELECT COALESCE(SUM(cost_dollars), 0.0) FROM accumulated_costs "
                        "WHERE timestamp >= ? AND timestamp < ?",
                        (entered_at, boundary_ts),
                    ).fetchone()
                    return float(result[0]) if result else 0.0

                return execute_with_retry(self.db_path, query, readonly=True)

            new_resets_at_5h = state.get("resets_at_5h")
            new_rollover_cost_5h = state.get("rollover_cost_5h")
            new_last_rollover_5h = state.get("last_rollover_resets_5h")

            new_resets_at_7d = state.get("resets_at_7d")
            new_rollover_cost_7d = state.get("rollover_cost_7d")
            new_last_rollover_7d = state.get("last_rollover_resets_7d")

            if five_rolled and five_resets is not None:
                projected_str = five_resets.isoformat()
                if state.get("last_rollover_resets_5h") != projected_str:
                    new_resets_at_5h = projected_str
                    new_last_rollover_5h = projected_str
                    if old_boundary_5h is not None:
                        boundary_ts = old_boundary_5h.replace(
                            tzinfo=timezone.utc
                        ).timestamp()
                        new_rollover_cost_5h = sum_costs_before(boundary_ts)
                    else:
                        # Fallback: use total accumulated cost (pre-existing behaviour)
                        def sum_all(conn):
                            result = conn.execute(
                                "SELECT COALESCE(SUM(cost_dollars), 0.0) "
                                "FROM accumulated_costs WHERE timestamp >= ?",
                                (entered_at,),
                            ).fetchone()
                            return float(result[0]) if result else 0.0

                        new_rollover_cost_5h = execute_with_retry(
                            self.db_path, sum_all, readonly=True
                        )

            if seven_rolled and seven_resets is not None:
                projected_str = seven_resets.isoformat()
                if state.get("last_rollover_resets_7d") != projected_str:
                    new_resets_at_7d = projected_str
                    new_last_rollover_7d = projected_str
                    if old_boundary_7d is not None:
                        boundary_ts_7d = old_boundary_7d.replace(
                            tzinfo=timezone.utc
                        ).timestamp()
                        new_rollover_cost_7d = sum_costs_before(boundary_ts_7d)
                    else:

                        def sum_all_7d(conn):
                            result = conn.execute(
                                "SELECT COALESCE(SUM(cost_dollars), 0.0) "
                                "FROM accumulated_costs WHERE timestamp >= ?",
                                (entered_at,),
                            ).fetchone()
                            return float(result[0]) if result else 0.0

                        new_rollover_cost_7d = execute_with_retry(
                            self.db_path, sum_all_7d, readonly=True
                        )

            def update_state(conn):
                conn.execute(
                    """
                    UPDATE fallback_state_v2 SET
                        resets_at_5h = ?,
                        rollover_cost_5h = ?,
                        last_rollover_resets_5h = ?,
                        resets_at_7d = ?,
                        rollover_cost_7d = ?,
                        last_rollover_resets_7d = ?
                    WHERE id = 1
                    """,
                    (
                        new_resets_at_5h,
                        new_rollover_cost_5h,
                        new_last_rollover_5h,
                        new_resets_at_7d,
                        new_rollover_cost_7d,
                        new_last_rollover_7d,
                    ),
                )

            execute_with_retry(self.db_path, update_state)

        except Exception as e:
            log_warning("usage_model", "Failed to persist rollover state", e)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_api_snapshot(self) -> Optional[UsageSnapshot]:
        """Build UsageSnapshot from api_cache in normal mode."""
        cache = self.get_api_cache()
        if cache is not None:
            five_dt = parse_api_datetime(cache.get("five_hour_resets_at"))
            seven_dt = parse_api_datetime(cache.get("seven_day_resets_at"))

            return UsageSnapshot(
                five_hour_util=float(cache["five_hour_util"]),
                five_hour_resets_at=five_dt,
                seven_day_util=float(cache["seven_day_util"]),
                seven_day_resets_at=seven_dt,
                is_synthetic=False,
                timestamp=datetime.utcfromtimestamp(float(cache["timestamp"])),
            )

        # APPROVED FALLBACK: usage_snapshots populated by existing pipeline during transition
        # api_cache table is empty; fall back to usage_snapshots written by run_pacing_check()
        def load_snapshot(conn):
            conn.row_factory = sqlite3.Row
            return conn.execute(
                "SELECT * FROM usage_snapshots ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()

        row = execute_with_retry(self.db_path, load_snapshot, readonly=True)
        if row is None:
            return None

        snap = dict(row)
        five_dt = parse_api_datetime(snap.get("five_hour_resets_at"))
        seven_dt = parse_api_datetime(snap.get("seven_day_resets_at"))

        return UsageSnapshot(
            five_hour_util=float(snap["five_hour_util"]),
            five_hour_resets_at=five_dt,
            seven_day_util=float(snap["seven_day_util"]),
            seven_day_resets_at=seven_dt,
            is_synthetic=False,
            timestamp=datetime.utcfromtimestamp(float(snap["timestamp"])),
        )

    def _get_synthetic_snapshot(self) -> Optional[UsageSnapshot]:
        """Compute synthetic UsageSnapshot from fallback_state_v2 + accumulated_costs."""
        try:
            # Load fallback state
            def load_state(conn):
                conn.row_factory = sqlite3.Row
                return conn.execute(
                    "SELECT * FROM fallback_state_v2 WHERE id = 1"
                ).fetchone()

            row = execute_with_retry(self.db_path, load_state, readonly=True)
            if row is None:
                return None

            state = dict(row)

            # Sum accumulated costs since entered_at
            entered_at = state.get("entered_at") or 0.0

            def sum_costs(conn):
                result = conn.execute(
                    "SELECT COALESCE(SUM(cost_dollars), 0.0) FROM accumulated_costs "
                    "WHERE timestamp >= ?",
                    (entered_at,),
                ).fetchone()
                return float(result[0]) if result else 0.0

            accumulated_cost = execute_with_retry(
                self.db_path, sum_costs, readonly=True
            )

            # Get tier and coefficients — prefer calibrated over defaults
            tier = state.get("tier") or "5x"
            calibrated = self._get_calibrated_coefficients(tier)
            if calibrated is not None:
                coeff_5h, coeff_7d = calibrated
            else:
                tier_costs = (
                    _DEFAULT_TOKEN_COSTS.get(tier) or _DEFAULT_TOKEN_COSTS["5x"]
                )
                coeff_5h = float(tier_costs.get("coefficient_5h", 0.0075))
                coeff_7d = float(tier_costs.get("coefficient_7d", 0.0011))

            baseline_5h = float(state.get("baseline_5h") or 0.0)
            baseline_7d = float(state.get("baseline_7d") or 0.0)

            # Apply rollover logic using existing _project_window
            # parse_api_datetime returns naive datetimes; _project_window expects naive now
            now = datetime.now(timezone.utc)
            now_naive = now.replace(tzinfo=None)
            five_resets, five_rolled = _project_window(
                state.get("resets_at_5h"), window_hours=5.0, now=now_naive
            )
            seven_resets, seven_rolled = _project_window(
                state.get("resets_at_7d"), window_hours=168.0, now=now_naive
            )

            # Check for previously persisted rollover first, then fresh rollover.
            # After get_reset_windows() persists the rollover (updates resets_at_5h
            # to a future time), _project_window() returns five_rolled=False because
            # the stored timestamp is now in the future. So we use stored_rollover_*
            # as the primary indicator that a rollover was previously recorded.
            stored_rollover_5h = state.get("rollover_cost_5h")
            if stored_rollover_5h is not None:
                # Rollover was previously persisted by get_reset_windows() or us
                cost_in_window_5h = max(
                    0.0, accumulated_cost - float(stored_rollover_5h)
                )
                synthetic_5h = min(cost_in_window_5h * coeff_5h * 100.0, 100.0)
            elif five_rolled and five_resets is not None:
                # Fresh rollover detected now (before get_reset_windows persists it).
                # Compute the last-expired boundary: five_resets is the NEXT future
                # boundary, so the last-expired boundary = five_resets - window_size.
                # This handles multiple consecutive rollovers correctly (e.g. if
                # _project_window advanced by 2× window_hours, the last-expired
                # boundary is still five_resets - 5h, not the original stored value).
                last_expired_5h = five_resets - timedelta(hours=5.0)
                last_expired_ts_5h = last_expired_5h.replace(
                    tzinfo=timezone.utc
                ).timestamp()

                def sum_post_rollover_5h(conn):
                    result = conn.execute(
                        "SELECT COALESCE(SUM(cost_dollars), 0.0) "
                        "FROM accumulated_costs WHERE timestamp >= ?",
                        (last_expired_ts_5h,),
                    ).fetchone()
                    return float(result[0]) if result else 0.0

                post_rollover_cost_5h = execute_with_retry(
                    self.db_path, sum_post_rollover_5h, readonly=True
                )

                # Persist the rollover state (with the correct pre-boundary offset)
                # so subsequent calls use Branch 1 (stored offset) instead of
                # re-entering this branch.
                self._persist_rollover(
                    state,
                    five_resets,
                    five_rolled,
                    seven_resets,
                    False,
                    old_boundary_5h=last_expired_5h,
                )

                cost_in_window_5h = post_rollover_cost_5h
                synthetic_5h = min(cost_in_window_5h * coeff_5h * 100.0, 100.0)
            else:
                synthetic_5h = min(
                    baseline_5h + accumulated_cost * coeff_5h * 100.0, 100.0
                )

            stored_rollover_7d = state.get("rollover_cost_7d")
            if stored_rollover_7d is not None:
                # Rollover was previously persisted by get_reset_windows() or us
                cost_in_window_7d = max(
                    0.0, accumulated_cost - float(stored_rollover_7d)
                )
                synthetic_7d = min(cost_in_window_7d * coeff_7d * 100.0, 100.0)
            elif seven_rolled and seven_resets is not None:
                # Fresh rollover detected now (before get_reset_windows persists it).
                # Compute the last-expired 7d boundary: seven_resets - window_size.
                last_expired_7d = seven_resets - timedelta(hours=168.0)
                last_expired_ts_7d = last_expired_7d.replace(
                    tzinfo=timezone.utc
                ).timestamp()

                def sum_post_rollover_7d(conn):
                    result = conn.execute(
                        "SELECT COALESCE(SUM(cost_dollars), 0.0) "
                        "FROM accumulated_costs WHERE timestamp >= ?",
                        (last_expired_ts_7d,),
                    ).fetchone()
                    return float(result[0]) if result else 0.0

                post_rollover_cost_7d = execute_with_retry(
                    self.db_path, sum_post_rollover_7d, readonly=True
                )

                # Persist the 7d rollover state (with the correct pre-boundary offset)
                # so subsequent calls use Branch 1 (stored offset).
                self._persist_rollover(
                    state,
                    five_resets,
                    False,
                    seven_resets,
                    seven_rolled,
                    old_boundary_7d=last_expired_7d,
                )

                cost_in_window_7d = post_rollover_cost_7d
                synthetic_7d = min(cost_in_window_7d * coeff_7d * 100.0, 100.0)
            else:
                synthetic_7d = min(
                    baseline_7d + accumulated_cost * coeff_7d * 100.0, 100.0
                )

            return UsageSnapshot(
                five_hour_util=synthetic_5h,
                five_hour_resets_at=five_resets,
                seven_day_util=synthetic_7d,
                seven_day_resets_at=seven_resets,
                is_synthetic=True,
                timestamp=datetime.now(timezone.utc),
            )

        except Exception as e:
            log_warning("usage_model", "Failed to compute synthetic snapshot", e)
            return None

    def calibrate_on_recovery(self, real_5h: float, real_7d: float) -> None:
        """Calibrate coefficients by comparing synthetic predictions vs real API values.

        Called just before resetting fallback state in exit_fallback(). Computes what
        synthetic values we predicted, compares against real API values, then updates
        calibrated_coefficients via weighted average.

        Args:
            real_5h: Real 5-hour utilization from recovered API (percentage).
            real_7d: Real 7-day utilization from recovered API (percentage).
        """
        try:
            snapshot = self._get_synthetic_snapshot()
            if snapshot is None:
                return

            # Get current tier from fallback state
            def load_tier(conn):
                row = conn.execute(
                    "SELECT tier FROM fallback_state_v2 WHERE id = 1"
                ).fetchone()
                return row[0] if row else "5x"

            tier = execute_with_retry(self.db_path, load_tier, readonly=True)

            synthetic_5h = snapshot.five_hour_util
            synthetic_7d = snapshot.seven_day_util

            # Skip calibration if synthetic prediction is at baseline only (no cost accumulated)
            # — but still calibrate if baseline itself is non-zero, just use it carefully
            # Skip only if both synthetic values are exactly 0 (no data at all)
            if synthetic_5h <= 0.0 and synthetic_7d <= 0.0:
                log_info(
                    "usage_model", "Skipping calibration: synthetic prediction is zero"
                )
                return

            # Load existing calibration data
            existing = self._get_calibrated_coefficients(tier)
            tier_defaults = _DEFAULT_TOKEN_COSTS.get(tier) or _DEFAULT_TOKEN_COSTS["5x"]
            default_5h = float(tier_defaults.get("coefficient_5h", 0.0075))
            default_7d = float(tier_defaults.get("coefficient_7d", 0.0011))

            if existing is not None:
                old_coeff_5h, old_coeff_7d = existing
            else:
                old_coeff_5h, old_coeff_7d = default_5h, default_7d

            def load_sample_count(conn):
                row = conn.execute(
                    "SELECT sample_count FROM calibrated_coefficients WHERE tier = ?",
                    (tier,),
                ).fetchone()
                return int(row[0]) if row else 0

            sample_count = execute_with_retry(
                self.db_path, load_sample_count, readonly=True
            )

            # Compute error ratio and clamp to [0.1, 10.0] to avoid wild swings
            _CLAMP_MIN = 0.1
            _CLAMP_MAX = 10.0

            if synthetic_5h > 0.0:
                ratio_5h = real_5h / synthetic_5h
                ratio_5h = max(_CLAMP_MIN, min(_CLAMP_MAX, ratio_5h))
                measured_5h = old_coeff_5h * ratio_5h
            else:
                measured_5h = old_coeff_5h

            if synthetic_7d > 0.0:
                ratio_7d = real_7d / synthetic_7d
                ratio_7d = max(_CLAMP_MIN, min(_CLAMP_MAX, ratio_7d))
                measured_7d = old_coeff_7d * ratio_7d
            else:
                measured_7d = old_coeff_7d

            # Weighted average: blend old coefficient with new measurement
            new_count = sample_count + 1
            new_coeff_5h = (old_coeff_5h * sample_count + measured_5h) / new_count
            new_coeff_7d = (old_coeff_7d * sample_count + measured_7d) / new_count
            now_ts = time.time()

            def upsert_calibration(conn):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO calibrated_coefficients
                    (tier, coefficient_5h, coefficient_7d, sample_count, last_calibrated)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (tier, new_coeff_5h, new_coeff_7d, new_count, now_ts),
                )

            execute_with_retry(self.db_path, upsert_calibration)
            log_info(
                "usage_model",
                f"Calibrated {tier}: coeff_5h={new_coeff_5h:.6f} "
                f"coeff_7d={new_coeff_7d:.6f} samples={new_count}",
            )

        except Exception as e:
            log_warning("usage_model", "Failed to calibrate coefficients", e)

    def _get_calibrated_coefficients(self, tier: str) -> Optional[tuple]:
        """Read calibrated coefficients from DB for the given tier.

        Args:
            tier: Subscription tier ("5x" or "20x").

        Returns:
            (coeff_5h, coeff_7d) tuple if calibration data exists with sample_count >= 1,
            or None if no calibration data is available.
        """
        try:

            def load(conn):
                row = conn.execute(
                    "SELECT coefficient_5h, coefficient_7d, sample_count "
                    "FROM calibrated_coefficients WHERE tier = ?",
                    (tier,),
                ).fetchone()
                return row

            row = execute_with_retry(self.db_path, load, readonly=True)
            if row is None:
                return None
            coeff_5h, coeff_7d, sample_count = row
            if sample_count < 1:
                return None
            return (float(coeff_5h), float(coeff_7d))

        except Exception as e:
            log_warning("usage_model", "Failed to get calibrated coefficients", e)
            return None

    def get_pacing_decision(self, config: Dict[str, Any]) -> Optional[Dict]:
        """Return a pacing decision dict based on current usage.

        Single-call API for monitor and other consumers that need both usage
        and the throttling decision in one shot.

        Args:
            config: Dict with pacing parameters:
                - threshold_percent (int, default 0)
                - base_delay (int, default 5)
                - max_delay (int, default 350)
                - safety_buffer_pct (float, default 95.0)
                - preload_hours (float, default 0.0)
                - weekly_limit_enabled (bool, default True)
                - five_hour_limit_enabled (bool, default True)

        Returns:
            Dict with pacing decision (same structure as
            pacing_engine.calculate_pacing_decision()), or None if no usage
            data is available.
        """
        snapshot = self.get_current_usage()
        if snapshot is None:
            return None

        from .pacing_engine import calculate_pacing_decision

        return calculate_pacing_decision(
            five_hour_util=snapshot.five_hour_util,
            five_hour_resets_at=snapshot.five_hour_resets_at,
            seven_day_util=snapshot.seven_day_util,
            seven_day_resets_at=snapshot.seven_day_resets_at,
            threshold_percent=config.get("threshold_percent", 0),
            base_delay=config.get("base_delay", 5),
            max_delay=config.get("max_delay", 350),
            safety_buffer_pct=config.get("safety_buffer_pct", 95.0),
            preload_hours=config.get("preload_hours", 0.0),
            weekly_limit_enabled=config.get("weekly_limit_enabled", True),
            five_hour_limit_enabled=config.get("five_hour_limit_enabled", True),
        )

    def _detect_tier(self) -> str:
        """Detect subscription tier from SQLite profile_cache or JSON file fallback."""
        try:
            # Try SQLite profile_cache first
            def load_profile(conn):
                row = conn.execute(
                    "SELECT profile_json FROM profile_cache WHERE id = 1"
                ).fetchone()
                if row:
                    return json.loads(row[0])
                return None

            profile = execute_with_retry(self.db_path, load_profile, readonly=True)

            if profile is None:
                # APPROVED FALLBACK: Story #42 transition — JSON profile cache used when
                # SQLite profile_cache table is not yet populated. Remove when profile
                # caching migrates fully to SQLite.
                from .profile_cache import load_cached_profile

                profile = load_cached_profile()

            from .fallback import detect_tier

            return detect_tier(profile)

        except Exception:
            return "5x"
