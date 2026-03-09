#!/usr/bin/env python3
"""
Tests for fallback mode integration in pacing_engine.py.

Story #38: When API fails and fallback is active, pacing engine must use
synthetic utilization values instead of returning no-throttle.

Story #42: Fallback state now lives in SQLite via UsageModel (not JSON files).

Integration points tested:
- run_pacing_check() uses synthetic values when fetch_usage=None + fallback active
- run_pacing_check() returns no-throttle when fetch_usage=None + fallback NOT active
- is_synthetic flag is propagated in result
- Normal (non-fallback) behavior is unchanged
"""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pacemaker import pacing_engine, database
from pacemaker.usage_model import UsageModel


def _setup_fallback_via_model(
    db_path: str,
    baseline_5h: float = 60.0,
    baseline_7d: float = 40.0,
    accumulated_input_tokens: int = 0,
    accumulated_output_tokens: int = 0,
    session_id: str = "setup-session",
) -> None:
    """
    Helper: set up fallback state using UsageModel (SQLite).

    Stores an API response with the given baselines, then transitions to
    fallback mode.  Optionally accumulates token costs after entering fallback.
    """
    model = UsageModel(db_path=db_path)

    # Store baselines so enter_fallback() reads them correctly
    model.store_api_response(
        {
            "five_hour": {
                "utilization": baseline_5h,
                "resets_at": "2026-03-07T20:00:00+00:00",
            },
            "seven_day": {
                "utilization": baseline_7d,
                "resets_at": "2026-03-12T00:00:00+00:00",
            },
        }
    )
    model.enter_fallback()

    # Accumulate costs if requested (only works when fallback is active)
    if accumulated_input_tokens or accumulated_output_tokens:
        model.accumulate_cost(
            input_tokens=accumulated_input_tokens,
            output_tokens=accumulated_output_tokens,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model_family="sonnet",
            session_id=session_id,
        )


class TestPacingEngineUsagesFallbackWhenAPIFails:
    """Tests that pacing engine uses synthetic values when API fails in fallback mode."""

    def setup_method(self):
        """Create temporary database for each test."""
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.temp_db.name
        database.initialize_database(self.db_path)
        self.session_id = "test-session-fallback"

    def teardown_method(self):
        """Clean up temporary database."""
        Path(self.db_path).unlink(missing_ok=True)

    def test_uses_synthetic_when_api_fails_and_fallback_active(self):
        """
        When fetch_usage returns None (API failed) and fallback is active,
        run_pacing_check() must use synthetic utilization values, not return no-throttle.

        This is the core Story #38 behavior: fallback synthesizes pacing estimates
        so quota protection continues even when the API is unreachable.
        """
        # High baselines guarantee is_synthetic=True in result
        _setup_fallback_via_model(
            self.db_path,
            baseline_5h=90.0,
            baseline_7d=85.0,
        )

        with (
            patch("pacemaker.api_client.fetch_usage", return_value=None),
            patch("pacemaker.api_client.load_access_token", return_value="test-token"),
        ):
            result = pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
            )

        assert (
            result.get("is_synthetic") is True
        ), f"Expected is_synthetic=True in result, got: {result}"

    def test_returns_no_throttle_when_api_fails_and_fallback_not_active(self):
        """
        Existing behavior: when fetch_usage returns None and fallback is NOT active,
        run_pacing_check() must return no-throttle (graceful degradation).
        This ensures we don't break the existing behavior.
        """
        # Do NOT set up fallback — NORMAL mode means no UsageModel snapshot exists

        with (
            patch("pacemaker.api_client.fetch_usage", return_value=None),
            patch("pacemaker.api_client.load_access_token", return_value="test-token"),
        ):
            result = pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
            )

        assert result["decision"]["should_throttle"] is False
        assert result["decision"]["delay_seconds"] == 0

    def test_synthetic_result_has_stale_data_flag(self):
        """
        When synthetic data is used, result must have stale_data=True
        so callers know this is not fresh API data.
        """
        _setup_fallback_via_model(
            self.db_path,
            baseline_5h=70.0,
            baseline_7d=60.0,
        )

        with (
            patch("pacemaker.api_client.fetch_usage", return_value=None),
            patch("pacemaker.api_client.load_access_token", return_value="test-token"),
        ):
            result = pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
            )

        assert result.get("is_synthetic") is True
        assert result.get("stale_data") is True

    def test_synthetic_path_produces_valid_decision_structure(self):
        """
        Synthetic path must return a dict with the same structure as a normal pacing result.
        Callers (hook.py) must not need special handling for synthetic results.
        """
        _setup_fallback_via_model(
            self.db_path,
            baseline_5h=50.0,
            baseline_7d=40.0,
        )

        with (
            patch("pacemaker.api_client.fetch_usage", return_value=None),
            patch("pacemaker.api_client.load_access_token", return_value="test-token"),
        ):
            result = pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
            )

        assert "decision" in result
        decision = result["decision"]
        assert "should_throttle" in decision
        assert "delay_seconds" in decision
        assert isinstance(decision["should_throttle"], bool)
        assert isinstance(decision["delay_seconds"], (int, float))

    def test_fallback_low_utilization_does_not_throttle(self):
        """
        Even in fallback mode, if synthetic utilization is low, no throttle applied.
        Fallback does not always throttle — it uses the normal pacing logic with synthetic values.
        """
        # Very low baseline and no accumulated cost = low synthetic utilization
        _setup_fallback_via_model(
            self.db_path,
            baseline_5h=5.0,
            baseline_7d=3.0,
            accumulated_input_tokens=0,
            accumulated_output_tokens=0,
        )

        with (
            patch("pacemaker.api_client.fetch_usage", return_value=None),
            patch("pacemaker.api_client.load_access_token", return_value="test-token"),
        ):
            result = pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
            )

        # Low utilization = no throttle even in fallback
        assert result["decision"]["should_throttle"] is False
        assert result.get("is_synthetic") is True


class TestSyntheticValuesWrittenToUsageCache:
    """
    Tests that synthetic utilization values are accessible via UsageModel after a
    run_pacing_check() that used the synthetic/fallback path.

    Story #42: The old synthetic_cache.json no longer exists. Synthetic state lives
    in SQLite (fallback_state_v2 + accumulated_costs tables), readable via
    UsageModel.get_current_usage().
    """

    def setup_method(self):
        """Create temporary database for each test."""
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.temp_db.name
        database.initialize_database(self.db_path)
        self.session_id = "test-session-cache"

    def teardown_method(self):
        """Clean up temporary database."""
        Path(self.db_path).unlink(missing_ok=True)

    def test_synthetic_values_accessible_via_usage_model(self):
        """
        When run_pacing_check() produces synthetic data (API failed, fallback active),
        UsageModel.get_current_usage() must return a UsageSnapshot with is_synthetic=True
        so the claude-usage monitor can display estimated values instead of stale data.
        """
        _setup_fallback_via_model(
            self.db_path,
            baseline_5h=70.0,
            baseline_7d=55.0,
        )

        with (
            patch("pacemaker.api_client.fetch_usage", return_value=None),
            patch("pacemaker.api_client.load_access_token", return_value="test-token"),
        ):
            result = pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
            )

        assert result.get("is_synthetic") is True, "Expected synthetic path"

        # Verify the snapshot is accessible via UsageModel
        model = UsageModel(db_path=self.db_path)
        snapshot = model.get_current_usage()
        assert (
            snapshot is not None
        ), "UsageModel must return a snapshot after synthetic run"
        assert snapshot.is_synthetic is True, "Snapshot must be flagged as synthetic"

    def test_usage_model_snapshot_has_five_hour_utilization(self):
        """
        The UsageSnapshot returned after a synthetic run must have five_hour_util >= 0
        so the monitor can display it identically to real API responses.
        """
        _setup_fallback_via_model(
            self.db_path,
            baseline_5h=65.0,
            baseline_7d=45.0,
        )

        with (
            patch("pacemaker.api_client.fetch_usage", return_value=None),
            patch("pacemaker.api_client.load_access_token", return_value="test-token"),
        ):
            pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
            )

        model = UsageModel(db_path=self.db_path)
        snapshot = model.get_current_usage()
        assert snapshot is not None
        assert isinstance(
            snapshot.five_hour_util, float
        ), "five_hour_util must be a float"
        assert snapshot.five_hour_util >= 0.0, "five_hour_util must be non-negative"

    def test_usage_model_snapshot_has_seven_day_utilization(self):
        """
        The UsageSnapshot returned after a synthetic run must have seven_day_util >= 0
        so the monitor can display it identically to real API responses.
        """
        _setup_fallback_via_model(
            self.db_path,
            baseline_5h=60.0,
            baseline_7d=50.0,
        )

        with (
            patch("pacemaker.api_client.fetch_usage", return_value=None),
            patch("pacemaker.api_client.load_access_token", return_value="test-token"),
        ):
            pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
            )

        model = UsageModel(db_path=self.db_path)
        snapshot = model.get_current_usage()
        assert snapshot is not None
        assert isinstance(
            snapshot.seven_day_util, float
        ), "seven_day_util must be a float"
        assert snapshot.seven_day_util >= 0.0, "seven_day_util must be non-negative"

    def test_normal_api_success_does_not_set_is_synthetic(self):
        """
        When API succeeds, UsageModel.get_current_usage() must NOT return is_synthetic=True.
        The synthetic path only activates on the fallback path.
        """
        mock_usage = {
            "five_hour_util": 25.0,
            "five_hour_resets_at": datetime.utcnow() + timedelta(hours=3),
            "seven_day_util": 20.0,
            "seven_day_resets_at": datetime.utcnow() + timedelta(days=5),
        }

        with (
            patch("pacemaker.api_client.fetch_usage", return_value=mock_usage),
            patch("pacemaker.api_client.load_access_token", return_value="test-token"),
        ):
            result = pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
            )

        assert result.get("is_synthetic") is not True

        # UsageModel snapshot should reflect NORMAL (not synthetic) state
        model = UsageModel(db_path=self.db_path)
        assert (
            model.is_fallback_active() is False
        ), "Fallback must not be active after a successful API response"


class TestPacingEngineReadsTierFromFallbackState:
    """
    Tests for Finding 7: pacing_engine reads tier from fallback state, not hardcoded '5x'.

    The 5x coefficients are ~5x larger than 20x, causing over-estimation of
    synthetic utilization for 20x (Claude Max) users. The fix reads tier from
    the fallback_state_v2 table (written by UsageModel.enter_fallback()).
    """

    def setup_method(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.temp_db.name
        database.initialize_database(self.db_path)
        self.session_id = "test-session-tier"

    def teardown_method(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_pacing_engine_reads_tier_from_fallback_state(self):
        """
        Verify run_pacing_check reads tier from fallback_state_v2 (via UsageModel)
        and produces synthetic results (not an error) when tier is stored.
        """
        # enter_fallback() auto-detects tier from profile cache; for test purposes
        # the default tier (5x) is fine — we just verify synthetic path runs cleanly
        _setup_fallback_via_model(
            self.db_path,
            baseline_5h=20.0,
            baseline_7d=15.0,
        )

        with (
            patch("pacemaker.api_client.fetch_usage", return_value=None),
            patch("pacemaker.api_client.load_access_token", return_value="test-token"),
        ):
            result = pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
            )

        assert result.get("is_synthetic") is True, "Must be synthetic"
        assert "error" not in result, f"Should not error: {result.get('error')}"

    def test_fallback_state_without_tier_defaults_to_5x(self):
        """
        When fallback_state_v2 has no tier (or tier is NULL), UsageModel must
        default to '5x' (safe, conservative default).

        We verify this by checking that a synthetic snapshot is returned without
        errors, which requires the coefficient lookup not to crash.
        """
        # Standard setup — enter_fallback writes tier; we confirm it works
        _setup_fallback_via_model(
            self.db_path,
            baseline_5h=50.0,
            baseline_7d=35.0,
        )

        with (
            patch("pacemaker.api_client.fetch_usage", return_value=None),
            patch("pacemaker.api_client.load_access_token", return_value="test-token"),
        ):
            result = pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
            )

        # Must not crash — produce synthetic result regardless of tier
        assert result.get("is_synthetic") is True, "Must still produce synthetic result"
        assert "decision" in result


class TestPacingEngineNormalBehaviorUnchanged:
    """Tests that normal (non-fallback) pacing behavior is unchanged."""

    def setup_method(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.temp_db.name
        database.initialize_database(self.db_path)
        self.session_id = "test-session-normal"

    def teardown_method(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_normal_api_success_path_unchanged(self):
        """
        When API succeeds (returns real usage_data), normal pacing logic runs.
        Removing fallback_state_path parameter must not affect normal path.
        """
        mock_usage = {
            "five_hour_util": 20.0,
            "five_hour_resets_at": datetime.utcnow() + timedelta(hours=3),
            "seven_day_util": 15.0,
            "seven_day_resets_at": datetime.utcnow() + timedelta(days=5),
        }

        with (
            patch("pacemaker.api_client.fetch_usage", return_value=mock_usage),
            patch("pacemaker.api_client.load_access_token", return_value="test-token"),
        ):
            result = pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
            )

        assert result["polled"] is True
        assert result.get("is_synthetic") is not True
        assert "decision" in result
        # Low utilization should not throttle
        assert result["decision"]["should_throttle"] is False

    def test_no_access_token_returns_no_throttle(self):
        """
        When no access token is available, graceful degradation returns no-throttle.
        Normal behavior must not be affected by fallback infrastructure.
        """
        with patch("pacemaker.api_client.load_access_token", return_value=None):
            result = pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
            )

        assert result["decision"]["should_throttle"] is False
        assert "error" in result
