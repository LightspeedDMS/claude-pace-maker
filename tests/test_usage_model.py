#!/usr/bin/env python3
"""
Tests for UsageModel — unified SQLite-backed usage state.

Story #42: Phases 1-4 (schema migration, core class, API storage, fallback mode).

TDD: These tests are written FIRST and define expected behavior.
"""

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_model(db_path: str):
    """Instantiate a fresh UsageModel pointing at the given db_path."""
    from pacemaker.usage_model import UsageModel

    return UsageModel(db_path=db_path)


def _table_names(db_path: str) -> set:
    """Return set of table names present in the database."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Phase 1: Schema tests
# ---------------------------------------------------------------------------


class TestSchemaCreation:
    """Phase 1: SQLite schema migration — new tables are created on init."""

    def test_new_tables_created_on_init(self, tmp_path):
        """All 6 new tables must exist after UsageModel is instantiated."""
        db_path = str(tmp_path / "usage.db")
        make_model(db_path)

        tables = _table_names(db_path)
        required_new_tables = {
            "api_cache",
            "fallback_state_v2",
            "accumulated_costs",
            "backoff_state",
            "profile_cache",
            "calibrated_coefficients",
        }
        assert required_new_tables.issubset(
            tables
        ), f"Missing tables: {required_new_tables - tables}"

    def test_existing_tables_preserved(self, tmp_path):
        """Original usage_snapshots and pacing_decisions tables must survive."""
        db_path = str(tmp_path / "usage.db")
        make_model(db_path)

        tables = _table_names(db_path)
        original_tables = {"usage_snapshots", "pacing_decisions", "blockage_events"}
        assert original_tables.issubset(
            tables
        ), f"Original tables removed: {original_tables - tables}"

    def test_singleton_constraint_api_cache(self, tmp_path):
        """api_cache must enforce id=1 singleton constraint."""
        db_path = str(tmp_path / "usage.db")
        make_model(db_path)

        with sqlite3.connect(db_path) as conn:
            # First insert succeeds
            conn.execute(
                "INSERT OR REPLACE INTO api_cache "
                "(id, timestamp, five_hour_util, seven_day_util) VALUES (1, ?, ?, ?)",
                (time.time(), 50.0, 30.0),
            )
            # Second insert with id=2 should fail
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO api_cache "
                    "(id, timestamp, five_hour_util, seven_day_util) VALUES (2, ?, ?, ?)",
                    (time.time(), 60.0, 40.0),
                )

    def test_accumulated_costs_has_autoincrement(self, tmp_path):
        """accumulated_costs uses AUTOINCREMENT so rows accumulate (no singleton)."""
        db_path = str(tmp_path / "usage.db")
        make_model(db_path)

        with sqlite3.connect(db_path) as conn:
            for i in range(5):
                conn.execute(
                    "INSERT INTO accumulated_costs "
                    "(timestamp, session_id, cost_dollars) VALUES (?, ?, ?)",
                    (time.time(), f"session-{i}", 0.01 * i),
                )
            count = conn.execute("SELECT COUNT(*) FROM accumulated_costs").fetchone()[0]

        assert count == 5


# ---------------------------------------------------------------------------
# Phase 2: UsageModel core — get_current_usage
# ---------------------------------------------------------------------------


class TestGetCurrentUsage:
    """Phase 2: Core UsageModel.get_current_usage() behaviour."""

    def test_get_current_usage_returns_none_when_no_data(self, tmp_path):
        """Fresh database with no cached data → returns None."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        result = model.get_current_usage()
        assert result is None

    def test_get_current_usage_returns_api_cache_in_normal_mode(self, tmp_path):
        """Normal mode with api_cache populated → returns real snapshot."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # Pre-populate api_cache (simulating a prior fetch_usage call)
        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 42.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 15.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )

        snapshot = model.get_current_usage()
        assert snapshot is not None
        assert abs(snapshot.five_hour_util - 42.0) < 0.001
        assert abs(snapshot.seven_day_util - 15.0) < 0.001
        assert snapshot.is_synthetic is False

    def test_get_current_usage_returns_synthetic_in_fallback(self, tmp_path):
        """Fallback mode → get_current_usage returns synthetic snapshot."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # Store an API response so enter_fallback has baselines
        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 30.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 10.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )

        model.enter_fallback()

        snapshot = model.get_current_usage()
        assert snapshot is not None
        assert snapshot.is_synthetic is True
        # Should be at least the baseline value
        assert snapshot.five_hour_util >= 30.0
        assert snapshot.seven_day_util >= 10.0

    def test_usage_model_stateless_across_instances(self, tmp_path):
        """Two separate UsageModel instances on the same db share state."""
        db_path = str(tmp_path / "usage.db")

        model_a = make_model(db_path)
        model_a.store_api_response(
            {
                "five_hour": {
                    "utilization": 55.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 22.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )

        # Completely new instance — should read same SQLite state
        model_b = make_model(db_path)
        snapshot = model_b.get_current_usage()

        assert snapshot is not None
        assert abs(snapshot.five_hour_util - 55.0) < 0.001


# ---------------------------------------------------------------------------
# Phase 3: API response storage
# ---------------------------------------------------------------------------


class TestStoreApiResponse:
    """Phase 3: store_api_response / get_api_cache."""

    def test_store_api_response_upserts_singleton(self, tmp_path):
        """Calling store_api_response twice replaces the single row."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        model.store_api_response(
            {
                "five_hour": {"utilization": 10.0, "resets_at": None},
                "seven_day": {"utilization": 5.0, "resets_at": None},
            }
        )
        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 90.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 70.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )

        with sqlite3.connect(db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0]
        assert count == 1  # Only ever 1 row

        cache = model.get_api_cache()
        assert cache is not None
        assert abs(cache["five_hour_util"] - 90.0) < 0.001

    def test_get_api_cache_returns_none_when_empty(self, tmp_path):
        """get_api_cache returns None on empty database."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        assert model.get_api_cache() is None

    def test_store_api_response_preserves_raw_response(self, tmp_path):
        """Raw API response JSON is stored and retrievable."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        raw = {
            "five_hour": {
                "utilization": 33.3,
                "resets_at": "2099-06-01T10:00:00+00:00",
            },
            "seven_day": {
                "utilization": 11.1,
                "resets_at": "2099-06-07T10:00:00+00:00",
            },
        }
        model.store_api_response(raw)

        cache = model.get_api_cache()
        assert cache is not None
        assert cache["raw_response"] is not None
        assert "five_hour" in cache["raw_response"]


# ---------------------------------------------------------------------------
# Phase 4: Fallback mode
# ---------------------------------------------------------------------------


class TestFallbackMode:
    """Phase 4: enter/exit fallback, accumulate_cost, synthetic calculation."""

    def test_enter_fallback_captures_baselines_from_api_cache(self, tmp_path):
        """enter_fallback() snapshots baselines from api_cache table."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 48.0,
                    "resets_at": "2099-01-01T15:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 22.0,
                    "resets_at": "2099-01-07T15:00:00+00:00",
                },
            }
        )

        model.enter_fallback()

        assert model.is_fallback_active()

        # Baselines must be stored in fallback_state_v2
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT baseline_5h, baseline_7d FROM fallback_state_v2 WHERE id=1"
            ).fetchone()
        assert row is not None
        assert abs(row[0] - 48.0) < 0.001
        assert abs(row[1] - 22.0) < 0.001

    def test_enter_fallback_idempotent(self, tmp_path):
        """Calling enter_fallback twice does NOT reset accumulated costs."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 20.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 5.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )

        model.enter_fallback()
        # Accumulate some cost
        model.accumulate_cost(
            input_tokens=1000,
            output_tokens=500,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="session-idempotent",
        )

        # Get cost before second enter_fallback
        with sqlite3.connect(db_path) as conn:
            cost_before = (
                conn.execute(
                    "SELECT SUM(cost_dollars) FROM accumulated_costs"
                ).fetchone()[0]
                or 0.0
            )

        # Second enter_fallback should be a no-op
        model.enter_fallback()

        with sqlite3.connect(db_path) as conn:
            cost_after = (
                conn.execute(
                    "SELECT SUM(cost_dollars) FROM accumulated_costs"
                ).fetchone()[0]
                or 0.0
            )

        assert abs(cost_before - cost_after) < 1e-10

    def test_exit_fallback_resets_state(self, tmp_path):
        """exit_fallback() transitions back to normal mode."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 60.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 40.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )

        model.enter_fallback()
        assert model.is_fallback_active()

        model.exit_fallback(real_5h=65.0, real_7d=42.0)
        assert not model.is_fallback_active()

    def test_accumulate_cost_inserts_row_in_fallback(self, tmp_path):
        """accumulate_cost() inserts a row in accumulated_costs when in fallback."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 10.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 5.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )
        model.enter_fallback()

        model.accumulate_cost(
            input_tokens=10_000,
            output_tokens=5_000,
            cache_read_tokens=1_000,
            cache_creation_tokens=500,
            model_family="sonnet",
            session_id="test-session",
        )

        with sqlite3.connect(db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM accumulated_costs").fetchone()[0]
        assert count == 1

    def test_accumulate_cost_noop_in_normal_mode(self, tmp_path):
        """accumulate_cost() is a no-op when NOT in fallback mode."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # Normal mode — no enter_fallback called
        model.accumulate_cost(
            input_tokens=10_000,
            output_tokens=5_000,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="test-session",
        )

        with sqlite3.connect(db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM accumulated_costs").fetchone()[0]
        assert count == 0

    def test_accumulate_cost_concurrent_sessions(self, tmp_path):
        """3 concurrent threads accumulate cost safely — SUM is correct."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 0.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 0.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )
        model.enter_fallback()

        # Each thread accumulates 1000 input tokens with sonnet pricing ($3/1M)
        # = 1000 * 3 / 1_000_000 = $0.003 per thread
        expected_per_thread = 1000 * 3.0 / 1_000_000  # input only
        n_threads = 3
        errors = []

        def worker(session_idx: int):
            try:
                m = make_model(db_path)  # Fresh instance per thread
                m.accumulate_cost(
                    input_tokens=1000,
                    output_tokens=0,
                    cache_read_tokens=0,
                    cache_creation_tokens=0,
                    model_family="sonnet",
                    session_id=f"concurrent-session-{session_idx}",
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

        expected_total = expected_per_thread * n_threads
        assert (
            abs(total_cost - expected_total) < 1e-9
        ), f"Expected {expected_total:.9f}, got {total_cost:.9f}"

    def test_synthetic_calculation_uses_sum_of_accumulated_costs(self, tmp_path):
        """Synthetic util = baseline + SUM(accumulated_costs) * coefficient * 100."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # Store baseline: 5h=20%, 7d=10%
        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 20.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 10.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )
        model.enter_fallback()

        # Accumulate $1.00 across 2 calls ($0.50 each via 1M output sonnet tokens each)
        # 333333 output tokens sonnet at $15/1M = $4.99...; let's use simpler math:
        # Use input_tokens only: 1_000_000 input sonnet tokens = $3.00/1M * 1 = $3.00
        # Two calls of 500K each = $3.00 total input cost
        for i in range(2):
            model.accumulate_cost(
                input_tokens=500_000,
                output_tokens=0,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                model_family="sonnet",
                session_id=f"calc-session-{i}",
            )

        snapshot = model.get_current_usage()
        assert snapshot is not None
        assert snapshot.is_synthetic is True

        # Expected: total_cost = 2 * (500_000 * 3.0 / 1_000_000) = 2 * 1.5 = 3.0
        # Tier depends on profile cache (5x or 20x), so use the actual coefficient
        from pacemaker.fallback import _DEFAULT_TOKEN_COSTS

        # Detect which tier the model used (matches _detect_tier logic)
        tier = model._detect_tier()
        tier_costs = _DEFAULT_TOKEN_COSTS.get(tier, _DEFAULT_TOKEN_COSTS["5x"])
        coeff_5h = tier_costs["coefficient_5h"]
        expected_5h = 20.0 + (3.0 * coeff_5h * 100.0)
        assert abs(snapshot.five_hour_util - expected_5h) < 0.01

    def test_synthetic_capped_at_100_percent(self, tmp_path):
        """Synthetic utilization must be capped at 100.0%."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # High baseline so cap triggers easily
        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 99.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 99.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )
        model.enter_fallback()

        # Add large cost to exceed 100%
        model.accumulate_cost(
            input_tokens=10_000_000,
            output_tokens=5_000_000,
            cache_read_tokens=1_000_000,
            cache_creation_tokens=500_000,
            model_family="opus",
            session_id="cap-test",
        )

        snapshot = model.get_current_usage()
        assert snapshot is not None
        assert snapshot.five_hour_util <= 100.0
        assert snapshot.seven_day_util <= 100.0

    def test_cost_calculation_matches_api_pricing(self, tmp_path):
        """Cost stored in accumulated_costs must match API_PRICING constants."""
        from pacemaker.fallback import API_PRICING

        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 0.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 0.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )
        model.enter_fallback()

        # Calculate expected cost manually
        input_tokens = 100_000
        output_tokens = 50_000
        cache_read_tokens = 10_000
        cache_creation_tokens = 5_000
        model_family = "opus"

        pricing = API_PRICING[model_family]
        expected_cost = (
            input_tokens * pricing["input"] / 1_000_000
            + output_tokens * pricing["output"] / 1_000_000
            + cache_read_tokens * pricing["cache_read"] / 1_000_000
            + cache_creation_tokens * pricing["cache_create"] / 1_000_000
        )

        model.accumulate_cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
            model_family=model_family,
            session_id="pricing-test",
        )

        with sqlite3.connect(db_path) as conn:
            actual_cost = conn.execute(
                "SELECT cost_dollars FROM accumulated_costs LIMIT 1"
            ).fetchone()[0]

        assert abs(actual_cost - expected_cost) < 1e-9


# ---------------------------------------------------------------------------
# Integration: is_fallback_active
# ---------------------------------------------------------------------------


class TestIsFallbackActive:
    """Test fallback state transitions."""

    def test_is_fallback_active_false_by_default(self, tmp_path):
        """Fresh model is in normal mode."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)
        assert not model.is_fallback_active()

    def test_is_fallback_active_true_after_enter(self, tmp_path):
        """is_fallback_active() returns True after enter_fallback."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)
        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 10.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 5.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )
        model.enter_fallback()
        assert model.is_fallback_active()

    def test_is_fallback_active_false_after_exit(self, tmp_path):
        """is_fallback_active() returns False after exit_fallback."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)
        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 10.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 5.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )
        model.enter_fallback()
        model.exit_fallback(real_5h=15.0, real_7d=7.0)
        assert not model.is_fallback_active()


# ---------------------------------------------------------------------------
# Phase 5: Enhanced Reset Window Tracking
# ---------------------------------------------------------------------------


class TestResetWindowProjection:
    """Phase 5: get_reset_windows() projects stale windows during fallback mode."""

    def _set_fallback_state_v2(
        self,
        db_path,
        resets_at_5h,
        resets_at_7d,
        rollover_cost_5h=None,
        rollover_cost_7d=None,
        last_rollover_resets_5h=None,
        last_rollover_resets_7d=None,
    ):
        """Helper: directly write fallback_state_v2 with given resets timestamps."""
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO fallback_state_v2
                (id, state, baseline_5h, baseline_7d, resets_at_5h, resets_at_7d,
                 tier, entered_at, rollover_cost_5h, rollover_cost_7d,
                 last_rollover_resets_5h, last_rollover_resets_7d)
                VALUES (1, 'fallback', 0.0, 0.0, ?, ?, '5x', ?, ?, ?, ?, ?)
                """,
                (
                    resets_at_5h,
                    resets_at_7d,
                    time.time(),
                    rollover_cost_5h,
                    rollover_cost_7d,
                    last_rollover_resets_5h,
                    last_rollover_resets_7d,
                ),
            )

    def test_get_reset_windows_not_stale_in_normal_mode_with_future_times(
        self, tmp_path
    ):
        """In normal mode with future resets_at, windows are not stale."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # Store API cache with future reset times
        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 30.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 10.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )

        windows = model.get_reset_windows()
        assert windows.five_hour_stale is False
        assert windows.seven_day_stale is False
        assert windows.five_hour_resets_at is not None
        assert windows.seven_day_resets_at is not None

    def test_get_reset_windows_stale_in_normal_mode_with_past_times(self, tmp_path):
        """In normal mode with past resets_at (stale API data), windows are stale."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # Store API cache with past reset times (simulating stale cached data)
        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 30.0,
                    "resets_at": "2020-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 10.0,
                    "resets_at": "2020-01-07T12:00:00+00:00",
                },
            }
        )

        windows = model.get_reset_windows()
        assert windows.five_hour_stale is True
        assert windows.seven_day_stale is True

    def test_get_reset_windows_projects_stale_5h_window_in_fallback(self, tmp_path):
        """In fallback mode, stale 5h window is projected forward so it's in the future."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # Put a far-past resets_at in fallback_state_v2 (simulates prolonged outage)
        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 10.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 5.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )
        model.enter_fallback()

        # Overwrite with stale timestamps to simulate prolonged outage
        past_5h = "2020-01-01T10:00:00+00:00"
        past_7d = "2020-01-01T10:00:00+00:00"
        self._set_fallback_state_v2(db_path, past_5h, past_7d)

        windows = model.get_reset_windows()

        # The projected window should be in the future
        assert windows.five_hour_resets_at is not None
        now = datetime.now(timezone.utc)
        assert (
            windows.five_hour_resets_at > now
        ), f"Projected 5h window {windows.five_hour_resets_at} is not in the future"
        # After projection it is NOT stale
        assert windows.five_hour_stale is False

    def test_get_reset_windows_projects_stale_7d_window_in_fallback(self, tmp_path):
        """In fallback mode, stale 7d window is projected forward so it's in the future."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 10.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 5.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )
        model.enter_fallback()

        past_ts = "2020-01-01T10:00:00+00:00"
        self._set_fallback_state_v2(db_path, past_ts, past_ts)

        windows = model.get_reset_windows()

        assert windows.seven_day_resets_at is not None
        now = datetime.now(timezone.utc)
        assert windows.seven_day_resets_at > now
        assert windows.seven_day_stale is False

    def test_get_reset_windows_persists_projected_window_back_to_db(self, tmp_path):
        """Projected windows are written back to fallback_state_v2 so they survive restarts."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 10.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 5.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )
        model.enter_fallback()

        past_ts = "2020-01-01T10:00:00+00:00"
        self._set_fallback_state_v2(db_path, past_ts, past_ts)

        # Call get_reset_windows() which should persist projected values
        model.get_reset_windows()

        # Now read raw DB to verify persisted values
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT resets_at_5h, resets_at_7d FROM fallback_state_v2 WHERE id=1"
            ).fetchone()

        assert row is not None
        assert row[0] != past_ts, "5h window was NOT updated in DB after projection"
        assert row[1] != past_ts, "7d window was NOT updated in DB after projection"

        # Parsed projected values should be in the future.
        # parse_api_datetime() returns naive UTC datetimes (strips timezone suffix),
        # so compare against a naive UTC reference.
        from pacemaker.fallback import parse_api_datetime

        now_naive = datetime.utcnow()
        assert parse_api_datetime(row[0]) > now_naive
        assert parse_api_datetime(row[1]) > now_naive

    def test_get_reset_windows_rollover_updates_rollover_cost(self, tmp_path):
        """When 5h rollover is detected, rollover_cost_5h is updated in DB."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 10.0,
                    "resets_at": "2020-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 5.0,
                    "resets_at": "2020-01-01T12:00:00+00:00",
                },
            }
        )
        model.enter_fallback()

        # Accumulate some cost before triggering rollover detection
        model.accumulate_cost(
            input_tokens=100_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="rollover-test",
        )

        # This call should detect rollover and persist rollover_cost_5h
        model.get_reset_windows()

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT rollover_cost_5h, last_rollover_resets_5h FROM fallback_state_v2 WHERE id=1"
            ).fetchone()

        assert row is not None
        assert row[0] is not None, "rollover_cost_5h was not set after rollover"
        assert row[1] is not None, "last_rollover_resets_5h was not set after rollover"

    def test_get_reset_windows_no_rollover_when_window_is_future(self, tmp_path):
        """No rollover_cost update when the window is still in the future."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 10.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 5.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )
        model.enter_fallback()

        model.get_reset_windows()

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT rollover_cost_5h FROM fallback_state_v2 WHERE id=1"
            ).fetchone()

        # rollover_cost_5h should remain NULL since no rollover occurred
        assert row is not None
        assert (
            row[0] is None
        ), "rollover_cost_5h was unexpectedly set (no rollover should occur)"

    def test_get_reset_windows_in_fallback_uses_fallback_state_not_api_cache(
        self, tmp_path
    ):
        """During fallback, get_reset_windows uses fallback_state_v2, not api_cache."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # API cache has far-future times
        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 10.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 5.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )
        model.enter_fallback()

        # Set fallback state to a different (stale) time — 3 hours from now (within window)
        future_3h = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"
        )
        future_7d = (datetime.now(timezone.utc) + timedelta(hours=100)).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"
        )
        self._set_fallback_state_v2(db_path, future_3h, future_7d)

        windows = model.get_reset_windows()

        # The 5h window should come from fallback state (3h in future), not api_cache (2099)
        # It should NOT be the year 2099
        assert windows.five_hour_resets_at is not None
        assert (
            windows.five_hour_resets_at.year < 2099
        ), "get_reset_windows used api_cache instead of fallback_state during fallback"


# ---------------------------------------------------------------------------
# Phase 6: Coefficient Calibration
# ---------------------------------------------------------------------------


class TestCoefficientCalibration:
    """Phase 6: calibrate_on_recovery() and _get_calibrated_coefficients()."""

    def _enter_fallback_with_tier(self, model, db_path, tier="5x"):
        """Helper: enter fallback mode and force a specific tier in DB."""
        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 50.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 20.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )
        model.enter_fallback()
        # Force tier in DB
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE fallback_state_v2 SET tier=? WHERE id=1", (tier,))

    def test_exit_fallback_calls_calibrate_stores_coefficients(self, tmp_path):
        """exit_fallback() triggers calibration and stores result in calibrated_coefficients."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)
        self._enter_fallback_with_tier(model, db_path, tier="5x")

        # Accumulate some cost so synthetic values are non-zero
        model.accumulate_cost(
            input_tokens=500_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="calib-test",
        )

        # Exit fallback with real values
        model.exit_fallback(real_5h=55.0, real_7d=22.0)

        # calibrated_coefficients should now have a row for tier "5x"
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT tier, coefficient_5h, coefficient_7d, sample_count "
                "FROM calibrated_coefficients WHERE tier='5x'"
            ).fetchone()

        assert (
            row is not None
        ), "calibrated_coefficients has no row for '5x' after exit_fallback"
        tier, coeff_5h, coeff_7d, sample_count = row
        assert coeff_5h > 0, "coefficient_5h must be positive"
        assert coeff_7d > 0, "coefficient_7d must be positive"
        assert sample_count >= 1, "sample_count must be at least 1"

    def test_calibrate_weighted_average_accumulates_samples(self, tmp_path):
        """Repeated exit_fallback() calls build sample_count via weighted average."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # First fallback cycle
        self._enter_fallback_with_tier(model, db_path, tier="5x")
        model.accumulate_cost(
            input_tokens=500_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="calib-1",
        )
        model.exit_fallback(real_5h=55.0, real_7d=22.0)

        # Second fallback cycle
        self._enter_fallback_with_tier(model, db_path, tier="5x")
        model.accumulate_cost(
            input_tokens=500_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="calib-2",
        )
        model.exit_fallback(real_5h=57.0, real_7d=23.0)

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT sample_count FROM calibrated_coefficients WHERE tier='5x'"
            ).fetchone()

        assert row is not None
        assert row[0] == 2, f"Expected sample_count=2 after 2 cycles, got {row[0]}"

    def test_get_calibrated_coefficients_returns_none_when_no_data(self, tmp_path):
        """_get_calibrated_coefficients returns None when no calibration data exists."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        result = model._get_calibrated_coefficients("5x")
        assert result is None

    def test_get_calibrated_coefficients_returns_tuple_after_calibration(
        self, tmp_path
    ):
        """_get_calibrated_coefficients returns (coeff_5h, coeff_7d) after calibration."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)
        self._enter_fallback_with_tier(model, db_path, tier="5x")

        model.accumulate_cost(
            input_tokens=500_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="coeff-test",
        )
        model.exit_fallback(real_5h=55.0, real_7d=22.0)

        result = model._get_calibrated_coefficients("5x")
        assert (
            result is not None
        ), "_get_calibrated_coefficients returned None after calibration"
        coeff_5h, coeff_7d = result
        assert isinstance(coeff_5h, float)
        assert isinstance(coeff_7d, float)
        assert coeff_5h > 0
        assert coeff_7d > 0

    def test_calibrate_clamps_extreme_error_ratio(self, tmp_path):
        """Calibration clamps error ratio to [0.1, 10.0] to avoid wild coefficient swings."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)
        self._enter_fallback_with_tier(model, db_path, tier="5x")

        # Accumulate tiny cost so synthetic prediction is near 0, but real is large
        # This creates a huge error ratio > 10.0 which should be clamped
        model.accumulate_cost(
            input_tokens=100,  # Very tiny cost
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="clamp-test",
        )
        model.exit_fallback(real_5h=90.0, real_7d=40.0)

        result = model._get_calibrated_coefficients("5x")
        assert result is not None

        from pacemaker.fallback import _DEFAULT_TOKEN_COSTS

        default_5h = _DEFAULT_TOKEN_COSTS["5x"]["coefficient_5h"]
        default_7d = _DEFAULT_TOKEN_COSTS["5x"]["coefficient_7d"]

        coeff_5h, coeff_7d = result
        # With clamping at 10.0, max coefficient = default * 10 (weighted avg with 1 sample)
        assert (
            coeff_5h <= default_5h * 10.0 * 1.1
        ), f"coefficient_5h={coeff_5h} exceeds clamped maximum"
        assert coeff_7d <= default_7d * 10.0 * 1.1

    def test_synthetic_snapshot_uses_calibrated_coefficients(self, tmp_path):
        """After calibration, get_current_usage() in fallback uses calibrated coefficients."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # First cycle: calibrate with a known multiplier
        self._enter_fallback_with_tier(model, db_path, tier="5x")
        model.accumulate_cost(
            input_tokens=500_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="calib-first",
        )
        model.exit_fallback(real_5h=55.0, real_7d=22.0)

        # Second cycle: in fallback, snapshot should use calibrated coefficients
        self._enter_fallback_with_tier(model, db_path, tier="5x")
        model.accumulate_cost(
            input_tokens=500_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="calib-second",
        )

        snapshot = model.get_current_usage()
        assert snapshot is not None
        assert snapshot.is_synthetic is True

        # Compute what the snapshot would be with DEFAULT coefficients
        from pacemaker.fallback import _DEFAULT_TOKEN_COSTS

        default_coeff_5h = _DEFAULT_TOKEN_COSTS["5x"]["coefficient_5h"]
        total_cost = 500_000 * 3.0 / 1_000_000  # sonnet input pricing
        default_5h = 50.0 + total_cost * default_coeff_5h * 100.0

        # Calibrated coefficients should produce a different result
        calibrated = model._get_calibrated_coefficients("5x")
        assert calibrated is not None
        cal_5h, _ = calibrated
        calibrated_5h = 50.0 + total_cost * cal_5h * 100.0

        # The snapshot should match calibrated, not default (if they differ)
        if abs(default_5h - calibrated_5h) > 0.01:
            assert abs(snapshot.five_hour_util - calibrated_5h) < abs(
                snapshot.five_hour_util - default_5h
            ), "Synthetic snapshot did not use calibrated coefficients"

    def test_calibrate_no_op_when_synthetic_prediction_is_zero(self, tmp_path):
        """Calibration is skipped when synthetic prediction is zero (division by zero risk)."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)
        self._enter_fallback_with_tier(model, db_path, tier="5x")

        # No cost accumulated — synthetic prediction will be at baseline (50%)
        # Exit with real values that match — calibration should run (baseline != 0)
        model.exit_fallback(real_5h=50.0, real_7d=20.0)

        # Should not raise and should handle gracefully
        result = model._get_calibrated_coefficients("5x")
        # Either None (skipped) or valid calibration — both are acceptable
        if result is not None:
            coeff_5h, coeff_7d = result
            assert coeff_5h > 0
            assert coeff_7d > 0


# ---------------------------------------------------------------------------
# Coverage gap tests: uncovered paths in usage_model.py (batch 1 of 2)
# ---------------------------------------------------------------------------


class TestCoverageGapsBatch1:
    """Tests targeting uncovered but reachable branches — batch 1."""

    def test_enter_fallback_no_api_cache_snapshot_synthesizes_timestamps(
        self, tmp_path
    ):
        """enter_fallback(api_cache_snapshot=False) synthesizes resets_at timestamps."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # No API cache stored — api_cache_snapshot=False skips reading it
        model.enter_fallback(api_cache_snapshot=False)

        assert model.is_fallback_active()

        # resets_at_5h and resets_at_7d must have been synthesized (not NULL)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT resets_at_5h, resets_at_7d, baseline_5h, baseline_7d "
                "FROM fallback_state_v2 WHERE id=1"
            ).fetchone()

        assert row is not None
        assert (
            row[0] is not None
        ), "resets_at_5h should be synthesized even without api_cache"
        assert (
            row[1] is not None
        ), "resets_at_7d should be synthesized even without api_cache"
        assert abs(row[2] - 0.0) < 0.001
        assert abs(row[3] - 0.0) < 0.001

    def test_exit_fallback_noop_when_already_normal(self, tmp_path):
        """exit_fallback() is a no-op when already in normal mode."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        assert not model.is_fallback_active()
        model.exit_fallback(real_5h=50.0, real_7d=20.0)
        assert not model.is_fallback_active()

        with sqlite3.connect(db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM calibrated_coefficients"
            ).fetchone()[0]
        assert count == 0

    def test_get_reset_windows_returns_stale_when_no_data_at_all(self, tmp_path):
        """get_reset_windows() returns all-stale when no api_cache and not in fallback."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        windows = model.get_reset_windows()
        assert windows.five_hour_resets_at is None
        assert windows.seven_day_resets_at is None
        assert windows.five_hour_stale is True
        assert windows.seven_day_stale is True

    def test_synthetic_snapshot_with_7d_rollover(self, tmp_path):
        """_get_synthetic_snapshot() uses rollover formula when 7d window has expired."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 10.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 5.0,
                    "resets_at": "2020-01-01T12:00:00+00:00",
                },
            }
        )
        model.enter_fallback()

        model.accumulate_cost(
            input_tokens=100_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="7d-rollover-test",
        )

        snapshot = model.get_current_usage()
        assert snapshot is not None
        assert snapshot.is_synthetic is True
        assert 0.0 <= snapshot.seven_day_util <= 100.0


# ---------------------------------------------------------------------------
# Coverage gap tests: uncovered paths in usage_model.py (batch 2 of 2)
# ---------------------------------------------------------------------------


class TestCoverageGapsBatch2:
    """Tests targeting uncovered but reachable branches — batch 2."""

    def test_synthetic_snapshot_with_5h_rollover(self, tmp_path):
        """_get_synthetic_snapshot() uses rollover formula when 5h window has expired."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 80.0,
                    "resets_at": "2020-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 5.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )
        model.enter_fallback()

        model.accumulate_cost(
            input_tokens=50_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="5h-rollover-test",
        )

        snapshot = model.get_current_usage()
        assert snapshot is not None
        assert snapshot.is_synthetic is True
        # 5h rolled over — starts fresh, so utilization based only on post-rollover cost
        assert 0.0 <= snapshot.five_hour_util <= 100.0

    def test_accumulate_cost_unknown_model_falls_back_to_sonnet(self, tmp_path):
        """accumulate_cost() with unknown model_family falls back to sonnet pricing."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 0.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 0.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )
        model.enter_fallback()

        model.accumulate_cost(
            input_tokens=1_000_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="unknown-model-xyz",
            session_id="unknown-model-test",
        )

        from pacemaker.fallback import API_PRICING

        expected_cost = 1_000_000 * API_PRICING["sonnet"]["input"] / 1_000_000

        with sqlite3.connect(db_path) as conn:
            actual = conn.execute(
                "SELECT cost_dollars FROM accumulated_costs LIMIT 1"
            ).fetchone()[0]

        assert abs(actual - expected_cost) < 1e-9

    def test_get_reset_windows_fallback_with_null_timestamps(self, tmp_path):
        """get_reset_windows() in fallback with NULL timestamps returns stale windows."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # Manually insert fallback state with NULL timestamps
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO fallback_state_v2
                (id, state, baseline_5h, baseline_7d, resets_at_5h, resets_at_7d,
                 tier, entered_at, rollover_cost_5h, rollover_cost_7d,
                 last_rollover_resets_5h, last_rollover_resets_7d)
                VALUES (1, 'fallback', 0.0, 0.0, NULL, NULL, '5x', ?, NULL, NULL, NULL, NULL)
                """,
                (time.time(),),
            )

        windows = model.get_reset_windows()
        assert windows.five_hour_stale is True
        assert windows.seven_day_stale is True

    def test_calibrate_on_recovery_blends_with_existing_samples(self, tmp_path):
        """calibrate_on_recovery() uses existing calibrated coefficients as starting point."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # Pre-seed calibrated_coefficients with 3 samples
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO calibrated_coefficients
                (tier, coefficient_5h, coefficient_7d, sample_count, last_calibrated)
                VALUES ('5x', 0.0100, 0.0020, 3, ?)
                """,
                (time.time(),),
            )

        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 50.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 20.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )
        model.enter_fallback()
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE fallback_state_v2 SET tier='5x' WHERE id=1")

        model.accumulate_cost(
            input_tokens=500_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="blend-test",
        )
        model.exit_fallback(real_5h=55.0, real_7d=22.0)

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT coefficient_5h, sample_count FROM calibrated_coefficients WHERE tier='5x'"
            ).fetchone()

        assert row is not None
        assert row[1] == 4, f"Expected sample_count=4 (3+1), got {row[1]}"
        assert row[0] > 0


# ---------------------------------------------------------------------------
# Coverage gap tests: reachable uncovered branches (batch 3 of 3)
# ---------------------------------------------------------------------------


class TestCoverageGapsBatch3:
    """Tests for the remaining reachable uncovered branches."""

    def test_calibrate_skips_when_both_synthetic_zero(self, tmp_path):
        """calibrate_on_recovery skips when both synthetic values are exactly 0."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # Enter fallback with baseline=0 and no cost accumulated → synthetic = 0.0
        model.enter_fallback(api_cache_snapshot=False)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE fallback_state_v2 SET baseline_5h=0.0, baseline_7d=0.0 WHERE id=1"
            )

        # No cost, no baseline → both synthetic=0 → calibration should be skipped
        model.calibrate_on_recovery(real_5h=50.0, real_7d=20.0)

        with sqlite3.connect(db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM calibrated_coefficients"
            ).fetchone()[0]
        assert (
            count == 0
        ), "calibrate_on_recovery should skip when both synthetic values are 0"

    def test_get_calibrated_coefficients_returns_none_when_sample_count_zero(
        self, tmp_path
    ):
        """_get_calibrated_coefficients returns None when sample_count=0."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # Insert a row with sample_count=0 (row exists but not yet calibrated)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO calibrated_coefficients
                (tier, coefficient_5h, coefficient_7d, sample_count, last_calibrated)
                VALUES ('5x', 0.0075, 0.0011, 0, ?)
                """,
                (time.time(),),
            )

        result = model._get_calibrated_coefficients("5x")
        assert result is None, "sample_count=0 should return None (not yet calibrated)"

    def test_calibrate_when_synthetic_7d_is_zero_uses_old_coefficient(self, tmp_path):
        """When synthetic_7d=0 but synthetic_5h>0, measured_7d uses old_coeff_7d (line 699)."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # Enter fallback with 5h baseline=50% (non-zero synthetic_5h) and 7d baseline=0
        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 50.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 0.0,
                    "resets_at": "2020-01-01T12:00:00+00:00",
                },
            }
        )
        model.enter_fallback()
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE fallback_state_v2 SET baseline_7d=0.0, tier='5x' WHERE id=1"
            )

        # Accumulate cost to make synthetic_5h > 0
        model.accumulate_cost(
            input_tokens=500_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id="7d-zero-test",
        )

        # Trigger rollover persistence so rollover_cost_7d = current accumulated cost
        # After rollover: cost_in_window_7d = accumulated - rollover_cost = 0 → synthetic_7d = 0
        model.get_reset_windows()

        # calibrate_on_recovery: synthetic_5h > 0, synthetic_7d = 0
        # → line 699: measured_7d = old_coeff_7d (uses fallback path)
        model.calibrate_on_recovery(real_5h=55.0, real_7d=22.0)

        # Calibration should have run (synthetic_5h > 0 means we don't skip)
        result = model._get_calibrated_coefficients("5x")
        if result is not None:
            coeff_5h, coeff_7d = result
            assert coeff_5h > 0
            assert coeff_7d > 0


# ---------------------------------------------------------------------------
# Coverage gap tests: reachable uncovered branches (batch 4 of 4)
# ---------------------------------------------------------------------------


class TestCoverageGapsBatch4:
    """Tests targeting the last reachable uncovered branches."""

    def test_detect_tier_reads_from_profile_cache_table(self, tmp_path):
        """_detect_tier() uses profile_cache SQLite table when populated (line 767)."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # Insert a profile_cache row indicating claude_max = False → tier "5x"
        profile_json = '{"account": {"has_claude_max": false}}'
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO profile_cache (id, timestamp, profile_json) "
                "VALUES (1, ?, ?)",
                (time.time(), profile_json),
            )

        # enter_fallback calls _detect_tier() internally
        model.enter_fallback(api_cache_snapshot=False)

        # Tier stored in fallback_state_v2 should reflect profile_cache result
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT tier FROM fallback_state_v2 WHERE id=1"
            ).fetchone()

        assert row is not None
        assert row[0] == "5x"

    def test_calibrate_when_synthetic_5h_zero_synthetic_7d_nonzero(self, tmp_path):
        """calibrate_on_recovery uses old_coeff_5h when synthetic_5h=0 (line 692).

        Setup: baseline_5h=0, no accumulated cost → synthetic_5h = 0+0=0.
        baseline_7d=20, future resets_at → synthetic_7d = 20+0=20 (non-zero).
        The skip guard (synthetic_5h<=0 AND synthetic_7d<=0) is False, so calibration runs.
        With synthetic_5h=0, the else branch at line 692 executes: measured_5h=old_coeff_5h.
        """
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # baseline_5h=0, baseline_7d=20, no cost accumulated → synthetic_5h=0, synthetic_7d=20
        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 0.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 20.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )
        model.enter_fallback()
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE fallback_state_v2 SET baseline_5h=0.0, tier='5x' WHERE id=1"
            )

        # No accumulated cost → synthetic_5h = 0+0=0, synthetic_7d = 20+0=20
        model.calibrate_on_recovery(real_5h=0.0, real_7d=22.0)

        # Calibration ran (synthetic_7d=20 > 0, so skip guard did not fire)
        result = model._get_calibrated_coefficients("5x")
        assert (
            result is not None
        ), "calibration should run when synthetic_7d > 0 even if synthetic_5h = 0"
        coeff_5h, coeff_7d = result
        assert coeff_5h > 0
        assert coeff_7d > 0

    def test_calibrate_when_synthetic_7d_zero_synthetic_5h_nonzero(self, tmp_path):
        """calibrate_on_recovery uses old_coeff_7d when synthetic_7d=0 (line 699).

        Setup: baseline_5h=50, baseline_7d=0, no cost → synthetic_5h=50, synthetic_7d=0.
        Skip guard (both<=0) is False. With synthetic_7d=0, line 699 executes:
        measured_7d = old_coeff_7d.
        """
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 50.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 0.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )
        model.enter_fallback()
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE fallback_state_v2 SET baseline_7d=0.0, tier='5x' WHERE id=1"
            )

        # No cost accumulated → synthetic_5h=50 (from baseline), synthetic_7d=0
        model.calibrate_on_recovery(real_5h=55.0, real_7d=0.0)

        result = model._get_calibrated_coefficients("5x")
        assert (
            result is not None
        ), "calibration should run when synthetic_5h > 0 even if synthetic_7d = 0"
        coeff_5h, coeff_7d = result
        assert coeff_5h > 0
        assert coeff_7d > 0

    def test_get_api_cache_handles_corrupt_raw_response_json(self, tmp_path):
        """get_api_cache() silently handles corrupt JSON in raw_response (lines 353-354)."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # Insert a row with deliberately corrupt JSON in raw_response
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO api_cache "
                "(id, timestamp, five_hour_util, seven_day_util, raw_response) "
                "VALUES (1, ?, 50.0, 20.0, ?)",
                (time.time(), "NOT VALID JSON {{{"),
            )

        cache = model.get_api_cache()
        assert cache is not None
        # raw_response should remain as the original string (exception was silenced)
        assert cache["raw_response"] == "NOT VALID JSON {{{"
        # Other fields are still accessible
        assert abs(cache["five_hour_util"] - 50.0) < 0.001


# ---------------------------------------------------------------------------
# Phase 7: get_pacing_decision() on UsageModel
# ---------------------------------------------------------------------------


class TestGetPacingDecision:
    """Phase 7: UsageModel.get_pacing_decision() delegates to pacing_engine."""

    _DEFAULT_CONFIG = {
        "threshold_percent": 0,
        "base_delay": 5,
        "max_delay": 350,
        "safety_buffer_pct": 95.0,
        "preload_hours": 0.0,
        "weekly_limit_enabled": True,
        "five_hour_limit_enabled": True,
    }

    def test_get_pacing_decision_returns_none_when_no_data(self, tmp_path):
        """No data in DB → get_pacing_decision returns None."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        result = model.get_pacing_decision(self._DEFAULT_CONFIG)
        assert result is None

    def test_get_pacing_decision_returns_dict_with_required_keys(self, tmp_path):
        """Normal mode with API data → returns dict with all required keys."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # Store an API response so get_current_usage() returns real data
        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 40.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 15.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )

        result = model.get_pacing_decision(self._DEFAULT_CONFIG)

        assert result is not None
        # Required top-level keys from calculate_pacing_decision
        required_keys = {
            "should_throttle",
            "delay_seconds",
            "constrained_window",
            "five_hour",
            "seven_day",
        }
        assert required_keys.issubset(
            result.keys()
        ), f"Missing keys: {required_keys - result.keys()}"

    def test_get_pacing_decision_values_are_correct_types(self, tmp_path):
        """Returned dict values have correct types."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 50.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 20.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )

        result = model.get_pacing_decision(self._DEFAULT_CONFIG)

        assert result is not None
        assert isinstance(result["should_throttle"], bool)
        assert isinstance(result["delay_seconds"], (int, float))
        assert isinstance(result["five_hour"], dict)
        assert isinstance(result["seven_day"], dict)

    def test_get_pacing_decision_in_fallback_mode(self, tmp_path):
        """Fallback mode → get_pacing_decision uses synthetic values, still returns dict."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        # Store API response, then enter fallback
        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 30.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 10.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )
        model.enter_fallback()

        result = model.get_pacing_decision(self._DEFAULT_CONFIG)

        assert result is not None
        # Should still have the required structure
        assert "should_throttle" in result
        assert "delay_seconds" in result
        assert "five_hour" in result
        assert "seven_day" in result
        # five_hour utilization in result should reflect synthetic (>= baseline)
        assert result["five_hour"]["utilization"] >= 30.0

    def test_get_pacing_decision_respects_config_limits(self, tmp_path):
        """Config weekly_limit_enabled=False → seven_day window excluded from constraint."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 10.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 99.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )

        config_no_weekly = dict(self._DEFAULT_CONFIG)
        config_no_weekly["weekly_limit_enabled"] = False

        result = model.get_pacing_decision(config_no_weekly)

        assert result is not None
        # With weekly disabled, constrained window should NOT be '7-day'
        # (it would be '5-hour' or None, but definitely not '7-day' as constraint)
        assert result.get("constrained_window") != "7-day"

    def test_get_pacing_decision_is_not_throttled_when_limits_disabled(self, tmp_path):
        """Both limits disabled → should_throttle is False regardless of utilization."""
        db_path = str(tmp_path / "usage.db")
        model = make_model(db_path)

        model.store_api_response(
            {
                "five_hour": {
                    "utilization": 50.0,
                    "resets_at": "2099-01-01T12:00:00+00:00",
                },
                "seven_day": {
                    "utilization": 50.0,
                    "resets_at": "2099-01-07T12:00:00+00:00",
                },
            }
        )

        config_no_limits = dict(self._DEFAULT_CONFIG)
        config_no_limits["weekly_limit_enabled"] = False
        config_no_limits["five_hour_limit_enabled"] = False

        result = model.get_pacing_decision(config_no_limits)

        assert result is not None
        assert result["should_throttle"] is False
        assert result["delay_seconds"] == 0
