#!/usr/bin/env python3
"""
Tests for Bug #68: Tier-mismatch calibration writes coefficients to wrong slot.

RED phase — all tests are written first and MUST FAIL until the fix is implemented.

Problem: _detect_tier() can return "20x" at fallback entry time (stale profile
cache), but the real subscription is "5x". This causes calibrate_on_recovery()
to write calibrated coefficients to the "20x" slot, while the "5x" slot stays
at defaults forever.

Fix contract:
1. calibrate_on_recovery() re-detects tier at recovery time (API is up).
2. If re-detected tier mismatches stored tier: skip calibration, log WARNING.
3. After successful calibration for tier T: purge calibrated_coefficients rows
   for all other tiers (purge_stale_calibrations).
4. _get_synthetic_snapshot() uses the tier stored in fallback_state_v2.

Tier detection is driven through the real SQLite profile_cache table so that
_detect_tier() is exercised without mocking its internals.
"""

import json
import sqlite3
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path):
    """Isolated SQLite database, fully initialized, cleaned up after each test."""
    from pacemaker import database

    path = str(tmp_path / "usage.db")
    database.initialize_database(path)
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def model(db_path):
    """UsageModel wired to the isolated db_path."""
    from pacemaker.usage_model import UsageModel

    return UsageModel(db_path=db_path)


# ---------------------------------------------------------------------------
# Low-level DB seeders (no UsageModel API — set state the tests need)
# ---------------------------------------------------------------------------


def _seed_profile_cache(db_path: str, has_claude_max: bool) -> None:
    """Write a profile_cache row so _detect_tier() returns the desired tier.

    _detect_tier() reads profile_cache WHERE id=1 first; by writing a real
    profile there we drive tier detection without patching any internals.
    """
    profile = {"account": {"has_claude_max": has_claude_max}}
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            INSERT OR REPLACE INTO profile_cache (id, timestamp, profile_json)
            VALUES (1, ?, ?)
            """,
            (time.time(), json.dumps(profile)),
        )


def _seed_fallback_state(db_path: str, tier: str) -> None:
    """Write fallback_state_v2 with the given tier and a future reset timestamp.

    Bypasses enter_fallback() so the stored tier is fully controlled by the test.
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            INSERT OR REPLACE INTO fallback_state_v2
            (id, state, baseline_5h, baseline_7d, resets_at_5h, resets_at_7d,
             tier, entered_at, rollover_cost_5h, rollover_cost_7d,
             last_rollover_resets_5h, last_rollover_resets_7d)
            VALUES (1, 'fallback', 50.0, 30.0,
                    '2099-01-01T00:00:00+00:00', '2099-01-01T00:00:00+00:00',
                    ?, ?, NULL, NULL, NULL, NULL)
            """,
            (tier, time.time()),
        )


def _seed_calibrated_row(
    db_path: str,
    tier: str,
    coeff_5h: float,
    coeff_7d: float,
    sample_count: int = 2,
) -> None:
    """Insert a calibrated_coefficients row for the given tier."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            INSERT OR REPLACE INTO calibrated_coefficients
            (tier, coefficient_5h, coefficient_7d, sample_count, last_calibrated)
            VALUES (?, ?, ?, ?, ?)
            """,
            (tier, coeff_5h, coeff_7d, sample_count, time.time()),
        )


def _seed_accumulated_cost(db_path: str) -> None:
    """Seed a small cost row so _get_synthetic_snapshot() is non-zero."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            INSERT INTO accumulated_costs
            (timestamp, session_id, cost_dollars, input_tokens, output_tokens,
             cache_read_tokens, cache_creation_tokens, model_family)
            VALUES (?, 'test-session', 0.01, 1000, 200, 0, 0, 'sonnet')
            """,
            (time.time(),),
        )


def _read_calibrated_row(db_path: str, tier: str):
    """Return (coeff_5h, coeff_7d, sample_count) for a tier, or None."""
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT coefficient_5h, coefficient_7d, sample_count "
            "FROM calibrated_coefficients WHERE tier = ?",
            (tier,),
        ).fetchone()


# ---------------------------------------------------------------------------
# Test 1: calibration skipped when tier mismatches
# ---------------------------------------------------------------------------


def test_calibration_skipped_when_tier_mismatch(db_path, model):
    """Stored tier='20x' in fallback_state_v2, but profile_cache says 5x.

    calibrate_on_recovery() re-detects tier; mismatch means synthetic predictions
    used the wrong coefficients, so the error ratio is garbage.
    The '20x' calibrated_coefficients slot must NOT be written.
    """
    # Profile says 5x (correct tier at recovery time)
    _seed_profile_cache(db_path, has_claude_max=False)
    # But fallback was entered when stale cache said 20x
    _seed_fallback_state(db_path, tier="20x")
    _seed_accumulated_cost(db_path)

    model.calibrate_on_recovery(real_5h=60.0, real_7d=35.0)

    row_20x = _read_calibrated_row(db_path, "20x")
    assert row_20x is None, (
        f"Expected NO calibrated row for '20x' after tier mismatch, "
        f"but found: {row_20x}"
    )


# ---------------------------------------------------------------------------
# Test 2: tier mismatch must not update an existing wrong-slot row
# ---------------------------------------------------------------------------


def test_calibration_mismatch_does_not_update_existing_wrong_slot(db_path, model):
    """Pre-existing '20x' calibration row (from earlier pollution) must NOT be
    updated when re-detected tier is '5x'.
    """
    _seed_profile_cache(db_path, has_claude_max=False)  # 5x at recovery
    _seed_fallback_state(db_path, tier="20x")  # wrong tier stored
    _seed_calibrated_row(
        db_path, "20x", coeff_5h=0.002, coeff_7d=0.0003, sample_count=3
    )
    _seed_accumulated_cost(db_path)

    model.calibrate_on_recovery(real_5h=60.0, real_7d=35.0)

    row_20x = _read_calibrated_row(db_path, "20x")
    assert row_20x is not None, "Pre-existing '20x' row should still exist"
    assert row_20x[2] == 3, f"Expected sample_count=3 (unchanged), got {row_20x[2]}"


# ---------------------------------------------------------------------------
# Test 3: calibration proceeds when tier matches
# ---------------------------------------------------------------------------


def test_calibration_proceeds_when_tier_matches(db_path, model):
    """Stored tier='5x', profile_cache also says 5x — no mismatch.

    calibrate_on_recovery() must write a '5x' calibrated_coefficients row.
    """
    _seed_profile_cache(db_path, has_claude_max=False)  # 5x
    _seed_fallback_state(db_path, tier="5x")
    _seed_accumulated_cost(db_path)

    model.calibrate_on_recovery(real_5h=55.0, real_7d=32.0)

    row_5x = _read_calibrated_row(db_path, "5x")
    assert row_5x is not None, "Expected '5x' calibrated row after matching tiers"
    coeff_5h, coeff_7d, sample_count = row_5x
    assert sample_count >= 1
    assert coeff_5h > 0.0
    assert coeff_7d > 0.0


# ---------------------------------------------------------------------------
# Test 4: stale calibrations purged after successful recovery
# ---------------------------------------------------------------------------


def test_stale_calibrations_purged_after_successful_recovery(db_path, model):
    """Both '5x' and '20x' rows pre-exist (Bug #68 pollution scenario).

    After successful calibration for '5x', the '20x' row must be deleted.
    """
    _seed_profile_cache(db_path, has_claude_max=False)  # 5x at recovery
    _seed_fallback_state(db_path, tier="5x")
    _seed_calibrated_row(
        db_path, "5x", coeff_5h=0.0070, coeff_7d=0.0010, sample_count=1
    )
    _seed_calibrated_row(
        db_path, "20x", coeff_5h=0.0032, coeff_7d=0.0005, sample_count=2
    )
    _seed_accumulated_cost(db_path)

    model.calibrate_on_recovery(real_5h=55.0, real_7d=32.0)

    row_5x = _read_calibrated_row(db_path, "5x")
    assert row_5x is not None, "Expected '5x' row after successful calibration"

    row_20x = _read_calibrated_row(db_path, "20x")
    assert (
        row_20x is None
    ), f"Expected stale '20x' row to be purged, but found: {row_20x}"


# ---------------------------------------------------------------------------
# Test 5: _get_synthetic_snapshot uses stored-tier calibrated coefficients
# ---------------------------------------------------------------------------


def test_synthetic_snapshot_uses_calibrated_coefficients_for_stored_tier(
    db_path, model
):
    """When fallback tier='5x' and calibrated_coefficients has a '5x' row with
    a distinctive high coefficient, _get_synthetic_snapshot() must use it and
    NOT the '20x' calibrated row or the '20x' default.

    We plant coeff_5h=0.9999 for '5x' (produces near-100% five_hour_util for
    even a tiny cost) and coeff_5h=0.001875 for '20x' (the default, produces
    ~0.002% for the same cost). A result > 0.5% proves '5x' was used.
    """
    _seed_fallback_state(db_path, tier="5x")
    _seed_accumulated_cost(db_path)

    # Distinctive '5x' calibration — extremely high so we can detect it
    _seed_calibrated_row(
        db_path, "5x", coeff_5h=0.9999, coeff_7d=0.0011, sample_count=1
    )
    # Normal '20x' values so accidental cross-tier reads would give a tiny result
    _seed_calibrated_row(
        db_path, "20x", coeff_5h=0.001875, coeff_7d=0.000275, sample_count=1
    )

    snapshot = model._get_synthetic_snapshot()

    assert snapshot is not None, "_get_synthetic_snapshot() must return a snapshot"
    assert snapshot.five_hour_util > 0.5, (
        f"Expected five_hour_util > 0.5 (using '5x' coeff=0.9999), "
        f"got {snapshot.five_hour_util:.6f} — likely using wrong tier coefficients"
    )
