#!/usr/bin/env python3
"""
Tests for fallback mode integration in pacing_engine.py.

Story #38: When API fails and fallback is active, pacing engine must use
synthetic utilization values instead of returning no-throttle.

Integration points tested:
- run_pacing_check() uses synthetic values when usage_data=None + fallback active
- run_pacing_check() returns no-throttle when usage_data=None + fallback NOT active
- is_synthetic flag is propagated in result
- Normal (non-fallback) behavior is unchanged
"""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
import sys
import json
import time

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pacemaker import pacing_engine, database


def _setup_fallback_state(
    state_path: str,
    baseline_5h: float = 60.0,
    baseline_7d: float = 40.0,
    accumulated_cost: float = 5.0,
):
    """Helper: write a fallback_state.json in FALLBACK mode."""
    from pacemaker.fallback import FallbackState

    state = {
        "state": FallbackState.FALLBACK.value,
        "baseline_5h": baseline_5h,
        "baseline_7d": baseline_7d,
        "accumulated_cost": accumulated_cost,
        "entered_at": time.time() - 300,
    }
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))


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

    def test_uses_synthetic_when_api_fails_and_fallback_active(self, tmp_path):
        """
        When fetch_usage returns None (API failed) and fallback is active,
        run_pacing_check() must use synthetic utilization values, not return no-throttle.

        This is the core Story #38 behavior: fallback synthesizes pacing estimates
        so quota protection continues even when the API is unreachable.
        """
        state_path = str(tmp_path / "fallback_state.json")
        # Set up very high synthetic utilization to guarantee throttle decision
        _setup_fallback_state(
            state_path, baseline_5h=90.0, baseline_7d=85.0, accumulated_cost=10.0
        )

        with (
            patch("pacemaker.api_client.fetch_usage", return_value=None),
            patch("pacemaker.api_client.load_access_token", return_value="test-token"),
            patch("pacemaker.fallback.DEFAULT_FALLBACK_STATE_PATH", state_path),
        ):

            result = pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
                last_poll_time=None,  # Force a poll attempt
                fallback_state_path=state_path,
            )

        # Must have attempted a poll
        assert (
            result.get("polled") is True
            or result.get("error") is None
            or result.get("is_synthetic") is True
        ), "Expected synthetic path to be taken"

        # The result should indicate synthetic/fallback data was used
        assert (
            result.get("is_synthetic") is True
        ), f"Expected is_synthetic=True in result, got: {result}"

    def test_returns_no_throttle_when_api_fails_and_fallback_not_active(self, tmp_path):
        """
        Existing behavior: when fetch_usage returns None and fallback is NOT active,
        run_pacing_check() must return no-throttle (graceful degradation).
        This ensures we don't break the existing behavior.
        """
        state_path = str(tmp_path / "fallback_state.json")
        # State file absent = NORMAL state

        with (
            patch("pacemaker.api_client.fetch_usage", return_value=None),
            patch("pacemaker.api_client.load_access_token", return_value="test-token"),
            patch("pacemaker.fallback.DEFAULT_FALLBACK_STATE_PATH", state_path),
        ):

            result = pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
                last_poll_time=None,
                fallback_state_path=state_path,
            )

        assert result["decision"]["should_throttle"] is False
        assert result["decision"]["delay_seconds"] == 0

    def test_synthetic_result_has_stale_data_flag(self, tmp_path):
        """
        When synthetic data is used, result must have stale_data=True
        so callers know this is not fresh API data.
        """
        state_path = str(tmp_path / "fallback_state.json")
        _setup_fallback_state(
            state_path, baseline_5h=70.0, baseline_7d=60.0, accumulated_cost=5.0
        )

        with (
            patch("pacemaker.api_client.fetch_usage", return_value=None),
            patch("pacemaker.api_client.load_access_token", return_value="test-token"),
            patch("pacemaker.fallback.DEFAULT_FALLBACK_STATE_PATH", state_path),
        ):

            result = pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
                last_poll_time=None,
                fallback_state_path=state_path,
            )

        assert result.get("is_synthetic") is True
        assert result.get("stale_data") is True

    def test_synthetic_path_produces_valid_decision_structure(self, tmp_path):
        """
        Synthetic path must return a dict with the same structure as a normal pacing result.
        Callers (hook.py) must not need special handling for synthetic results.
        """
        state_path = str(tmp_path / "fallback_state.json")
        _setup_fallback_state(
            state_path, baseline_5h=50.0, baseline_7d=40.0, accumulated_cost=3.0
        )

        with (
            patch("pacemaker.api_client.fetch_usage", return_value=None),
            patch("pacemaker.api_client.load_access_token", return_value="test-token"),
            patch("pacemaker.fallback.DEFAULT_FALLBACK_STATE_PATH", state_path),
        ):

            result = pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
                last_poll_time=None,
                fallback_state_path=state_path,
            )

        # Must have decision sub-dict
        assert "decision" in result
        decision = result["decision"]
        assert "should_throttle" in decision
        assert "delay_seconds" in decision
        assert isinstance(decision["should_throttle"], bool)
        assert isinstance(decision["delay_seconds"], (int, float))

    def test_fallback_low_utilization_does_not_throttle(self, tmp_path):
        """
        Even in fallback mode, if synthetic utilization is low, no throttle applied.
        Fallback does not always throttle — it uses the normal pacing logic with synthetic values.
        """
        state_path = str(tmp_path / "fallback_state.json")
        # Very low baseline and cost = low synthetic utilization
        _setup_fallback_state(
            state_path, baseline_5h=5.0, baseline_7d=3.0, accumulated_cost=0.01
        )

        with (
            patch("pacemaker.api_client.fetch_usage", return_value=None),
            patch("pacemaker.api_client.load_access_token", return_value="test-token"),
            patch("pacemaker.fallback.DEFAULT_FALLBACK_STATE_PATH", state_path),
        ):

            result = pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
                last_poll_time=None,
                fallback_state_path=state_path,
            )

        # Low utilization = no throttle even in fallback
        assert result["decision"]["should_throttle"] is False
        assert result.get("is_synthetic") is True


class TestSyntheticValuesWrittenToUsageCache:
    """Tests that synthetic utilization values are written to synthetic_cache.json."""

    def setup_method(self):
        """Create temporary database for each test."""
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.temp_db.name
        database.initialize_database(self.db_path)
        self.session_id = "test-session-cache"

    def teardown_method(self):
        """Clean up temporary database."""
        Path(self.db_path).unlink(missing_ok=True)

    def test_synthetic_values_written_to_usage_cache(self, tmp_path):
        """
        When run_pacing_check() produces synthetic data (API failed, fallback active),
        synthetic_cache.json must be updated with synthetic utilization values so
        the claude-usage monitor can display estimated values instead of stale data.
        """
        state_path = str(tmp_path / "fallback_state.json")
        cache_path = tmp_path / "usage_cache.json"
        _setup_fallback_state(
            state_path, baseline_5h=70.0, baseline_7d=55.0, accumulated_cost=6.0
        )

        with (
            patch("pacemaker.api_client.fetch_usage", return_value=None),
            patch("pacemaker.api_client.load_access_token", return_value="test-token"),
            patch("pacemaker.fallback.DEFAULT_FALLBACK_STATE_PATH", state_path),
            patch("pacemaker.pacing_engine.SYNTHETIC_CACHE_PATH", cache_path),
        ):

            result = pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
                last_poll_time=None,
                fallback_state_path=state_path,
            )

        assert result.get("is_synthetic") is True, "Expected synthetic path"
        assert (
            cache_path.exists()
        ), "synthetic_cache.json must be written when synthetic values computed"

        cache = json.loads(cache_path.read_text())
        assert cache.get("is_synthetic") is True, "Cache must be flagged as synthetic"
        assert "timestamp" in cache, "Cache must have a timestamp"
        assert "response" in cache, "Cache must have response structure"

    def test_usage_cache_has_five_hour_structure(self, tmp_path):
        """
        The written cache entry must have five_hour with utilization and resets_at
        so the monitor parses it identically to real API responses.
        """
        state_path = str(tmp_path / "fallback_state.json")
        cache_path = tmp_path / "usage_cache.json"
        _setup_fallback_state(
            state_path, baseline_5h=65.0, baseline_7d=45.0, accumulated_cost=4.0
        )

        with (
            patch("pacemaker.api_client.fetch_usage", return_value=None),
            patch("pacemaker.api_client.load_access_token", return_value="test-token"),
            patch("pacemaker.fallback.DEFAULT_FALLBACK_STATE_PATH", state_path),
            patch("pacemaker.pacing_engine.SYNTHETIC_CACHE_PATH", cache_path),
        ):

            pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
                last_poll_time=None,
                fallback_state_path=state_path,
            )

        cache = json.loads(cache_path.read_text())
        five_hour = cache["response"]["five_hour"]
        assert "utilization" in five_hour, "five_hour must have utilization"
        assert "resets_at" in five_hour, "five_hour must have resets_at"
        assert isinstance(
            five_hour["utilization"], float
        ), "utilization must be a float"
        assert five_hour["utilization"] >= 0.0, "utilization must be non-negative"

    def test_usage_cache_has_seven_day_structure(self, tmp_path):
        """
        The written cache entry must have seven_day with utilization and resets_at
        so the monitor parses it identically to real API responses.
        """
        state_path = str(tmp_path / "fallback_state.json")
        cache_path = tmp_path / "usage_cache.json"
        _setup_fallback_state(
            state_path, baseline_5h=60.0, baseline_7d=50.0, accumulated_cost=3.5
        )

        with (
            patch("pacemaker.api_client.fetch_usage", return_value=None),
            patch("pacemaker.api_client.load_access_token", return_value="test-token"),
            patch("pacemaker.fallback.DEFAULT_FALLBACK_STATE_PATH", state_path),
            patch("pacemaker.pacing_engine.SYNTHETIC_CACHE_PATH", cache_path),
        ):

            pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
                last_poll_time=None,
                fallback_state_path=state_path,
            )

        cache = json.loads(cache_path.read_text())
        seven_day = cache["response"]["seven_day"]
        assert "utilization" in seven_day, "seven_day must have utilization"
        assert "resets_at" in seven_day, "seven_day must have resets_at"
        assert isinstance(
            seven_day["utilization"], float
        ), "utilization must be a float"
        assert seven_day["utilization"] >= 0.0, "utilization must be non-negative"

    def test_normal_api_success_does_not_set_is_synthetic_in_cache(self, tmp_path):
        """
        When API succeeds, usage_cache.json must NOT have is_synthetic=True.
        The synthetic cache write only happens on the fallback path.
        """
        state_path = str(tmp_path / "fallback_state.json")
        cache_path = tmp_path / "usage_cache.json"

        mock_usage = {
            "five_hour_util": 25.0,
            "five_hour_resets_at": datetime.utcnow() + timedelta(hours=3),
            "seven_day_util": 20.0,
            "seven_day_resets_at": datetime.utcnow() + timedelta(days=5),
        }

        with (
            patch("pacemaker.api_client.fetch_usage", return_value=mock_usage),
            patch("pacemaker.api_client.load_access_token", return_value="test-token"),
            patch("pacemaker.fallback.DEFAULT_FALLBACK_STATE_PATH", state_path),
            patch("pacemaker.pacing_engine.SYNTHETIC_CACHE_PATH", cache_path),
        ):

            result = pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
                last_poll_time=None,
                fallback_state_path=state_path,
            )

        assert result.get("is_synthetic") is not True
        # Cache file should NOT exist (only synthetic path writes it via this mechanism)
        # OR if it exists (e.g. from prior runs), it must not have is_synthetic=True
        if cache_path.exists():
            cache = json.loads(cache_path.read_text())
            assert cache.get("is_synthetic") is not True


class TestPacingEngineReadsTierFromFallbackState:
    """
    Tests for Finding 7: pacing_engine reads tier from fallback state, not hardcoded '5x'.

    The 5x coefficients are ~5x larger than 20x, causing over-estimation of
    synthetic utilization for 20x (Claude Max) users. The fix reads tier from
    fb_state.get("tier", "5x") instead of hardcoding tier = "5x".
    """

    def setup_method(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.temp_db.name
        database.initialize_database(self.db_path)
        self.session_id = "test-session-tier"

    def teardown_method(self):
        Path(self.db_path).unlink(missing_ok=True)

    def _setup_fallback_state_with_tier(
        self,
        state_path: str,
        tier: str,
        baseline_5h: float = 60.0,
        baseline_7d: float = 40.0,
        accumulated_cost: float = 10.0,
    ):
        """Helper: write a fallback_state.json in FALLBACK mode with explicit tier."""
        from pacemaker.fallback import FallbackState

        state = {
            "state": FallbackState.FALLBACK.value,
            "baseline_5h": baseline_5h,
            "baseline_7d": baseline_7d,
            "accumulated_cost": accumulated_cost,
            "entered_at": time.time() - 300,
            "tier": tier,
        }
        path = Path(state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state))

    def test_20x_tier_produces_lower_synthetic_utilization_than_5x(self, tmp_path):
        """
        When fallback state has tier='20x', synthetic utilization must be lower
        than when tier='5x' for the same accumulated_cost and baselines.

        This is the core Finding 7 fix: 20x coefficients are ~5x smaller than
        5x coefficients, so 20x users should not see over-estimated utilization.

        Tests via calculate_synthetic directly since run_pacing_check does not
        expose utilization values in its return dict.
        """
        from pacemaker.fallback import calculate_synthetic, load_token_costs

        token_costs = load_token_costs()
        state = {
            "baseline_5h": 20.0,
            "baseline_7d": 15.0,
            "accumulated_cost": 20.0,
        }

        synth_5x = calculate_synthetic(state, "5x", token_costs)
        synth_20x = calculate_synthetic(state, "20x", token_costs)

        assert synth_20x["synthetic_5h"] < synth_5x["synthetic_5h"], (
            f"20x tier should produce lower 5h synthetic than 5x. "
            f"Got 5x={synth_5x['synthetic_5h']}, 20x={synth_20x['synthetic_5h']}"
        )
        assert synth_20x["synthetic_7d"] < synth_5x["synthetic_7d"], (
            f"20x tier should produce lower 7d synthetic than 5x. "
            f"Got 5x={synth_5x['synthetic_7d']}, 20x={synth_20x['synthetic_7d']}"
        )

    def test_pacing_engine_reads_tier_from_fallback_state(self, tmp_path):
        """
        Verify run_pacing_check reads tier from fb_state.get('tier') and
        produces synthetic results (not an error) when tier is stored.
        """
        state_path = str(tmp_path / "fallback_state.json")
        self._setup_fallback_state_with_tier(
            state_path,
            tier="20x",
            baseline_5h=20.0,
            baseline_7d=15.0,
            accumulated_cost=10.0,
        )

        with (
            patch("pacemaker.api_client.fetch_usage", return_value=None),
            patch("pacemaker.api_client.load_access_token", return_value="test-token"),
            patch("pacemaker.fallback.DEFAULT_FALLBACK_STATE_PATH", state_path),
        ):
            result = pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
                last_poll_time=None,
                fallback_state_path=state_path,
            )

        assert result.get("is_synthetic") is True, "Must be synthetic"
        assert "error" not in result, f"Should not error: {result.get('error')}"

    def test_fallback_state_without_tier_defaults_to_5x(self, tmp_path):
        """
        When fallback state has no 'tier' key (legacy state), pacing_engine
        must default to '5x' (safe, conservative default).
        """
        state_path = str(tmp_path / "fallback_state.json")
        # Write state without tier key (simulates old state format)
        _setup_fallback_state(
            state_path, baseline_5h=50.0, baseline_7d=35.0, accumulated_cost=5.0
        )

        with (
            patch("pacemaker.api_client.fetch_usage", return_value=None),
            patch("pacemaker.api_client.load_access_token", return_value="test-token"),
            patch("pacemaker.fallback.DEFAULT_FALLBACK_STATE_PATH", state_path),
        ):
            result = pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
                last_poll_time=None,
                fallback_state_path=state_path,
            )

        # Must not crash — default to 5x and produce synthetic result
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

    def test_normal_api_success_path_unchanged(self, tmp_path):
        """
        When API succeeds (returns real usage_data), normal pacing logic runs.
        Adding fallback parameter must not affect normal path.
        """
        state_path = str(tmp_path / "fallback_state.json")

        mock_usage = {
            "five_hour_util": 20.0,
            "five_hour_resets_at": datetime.utcnow() + timedelta(hours=3),
            "seven_day_util": 15.0,
            "seven_day_resets_at": datetime.utcnow() + timedelta(days=5),
        }

        with (
            patch("pacemaker.api_client.fetch_usage", return_value=mock_usage),
            patch("pacemaker.api_client.load_access_token", return_value="test-token"),
            patch("pacemaker.fallback.DEFAULT_FALLBACK_STATE_PATH", state_path),
        ):

            result = pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
                last_poll_time=None,
                fallback_state_path=state_path,
            )

        assert result["polled"] is True
        assert result.get("is_synthetic") is not True  # Not synthetic
        assert "decision" in result
        # Low utilization should not throttle
        assert result["decision"]["should_throttle"] is False

    def test_no_access_token_returns_no_throttle(self, tmp_path):
        """
        When no access token is available, graceful degradation returns no-throttle.
        Adding fallback parameter must not affect this path.
        """
        state_path = str(tmp_path / "fallback_state.json")

        with (
            patch("pacemaker.api_client.load_access_token", return_value=None),
            patch("pacemaker.fallback.DEFAULT_FALLBACK_STATE_PATH", state_path),
        ):

            result = pacing_engine.run_pacing_check(
                db_path=self.db_path,
                session_id=self.session_id,
                last_poll_time=None,
                fallback_state_path=state_path,
            )

        assert result["decision"]["should_throttle"] is False
        assert "error" in result
