#!/usr/bin/env python3
"""
Tests for fallback wiring in api_client.py.

Story #38: API returns 429 -> enter_fallback; success -> exit_fallback.

Integration points tested:
- fetch_usage() on 429 (after all retries) -> enter_fallback() called
- fetch_usage() on 200 success -> exit_fallback() called
- fetch_usage() on non-429 error -> neither enter nor exit called

NOTE: HTTP calls are mocked (unittest.mock) because this module tests the
integration between api_client and fallback state — not the HTTP layer.
Real HTTP calls would require a live API token.
"""

import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
import sys
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _make_usage_response(five_hour_util: float = 40.0, seven_day_util: float = 25.0):
    """Return a minimal valid usage API response dict."""
    return {
        "five_hour": {
            "utilization": five_hour_util,
            "resets_at": "2026-03-06T12:00:00+00:00",
        },
        "seven_day": {
            "utilization": seven_day_util,
            "resets_at": "2026-03-10T12:00:00+00:00",
        },
    }


def _write_usage_cache(
    tmp_path, five_hour_util: float = 40.0, seven_day_util: float = 25.0
) -> Path:
    """Write a usage_cache.json file for fallback baseline reading."""
    cache_path = tmp_path / "usage_cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "timestamp": time.time(),
                "response": {
                    "five_hour": {"utilization": five_hour_util, "resets_at": None},
                    "seven_day": {"utilization": seven_day_util, "resets_at": None},
                },
            }
        )
    )
    return cache_path


class TestFetchUsage429TriggersEnterFallback:
    """Tests that 429 response after all retries triggers enter_fallback()."""

    def test_429_after_all_retries_enters_fallback(self, tmp_path):
        """
        When fetch_usage exhausts all retries on 429, enter_fallback() must be called.
        This verifies the fallback wiring in api_client.py.
        """
        from pacemaker import api_client, fallback

        usage_cache_path = _write_usage_cache(tmp_path)
        state_path = tmp_path / "fallback_state.json"
        backoff_path = tmp_path / "api_backoff.json"

        mock_resp = MagicMock()
        mock_resp.status_code = 429

        with (
            patch("pacemaker.api_client.requests.get", return_value=mock_resp),
            patch("pacemaker.api_client.time.sleep"),
        ):  # skip real sleeps

            result = api_client.fetch_usage(
                access_token="test-token",
                timeout=5,
                backoff_state_path=str(backoff_path),
                fallback_state_path=str(state_path),
                usage_cache_path=str(usage_cache_path),
            )

        assert result is None  # 429 must return None

        # enter_fallback must have been triggered
        assert fallback.is_fallback_active(str(state_path)) is True

    def test_429_fallback_captures_baselines_from_cache(self, tmp_path):
        """
        When fallback is entered after 429, baseline_5h/7d are read from usage_cache.json.
        """
        from pacemaker import api_client, fallback

        usage_cache_path = _write_usage_cache(
            tmp_path, five_hour_util=55.0, seven_day_util=38.0
        )
        state_path = tmp_path / "fallback_state.json"
        backoff_path = tmp_path / "api_backoff.json"

        mock_resp = MagicMock()
        mock_resp.status_code = 429

        with (
            patch("pacemaker.api_client.requests.get", return_value=mock_resp),
            patch("pacemaker.api_client.time.sleep"),
        ):

            api_client.fetch_usage(
                access_token="test-token",
                timeout=5,
                backoff_state_path=str(backoff_path),
                fallback_state_path=str(state_path),
                usage_cache_path=str(usage_cache_path),
            )

        state = fallback.load_fallback_state(str(state_path))
        assert state["baseline_5h"] == pytest.approx(55.0, abs=0.1)
        assert state["baseline_7d"] == pytest.approx(38.0, abs=0.1)

    def test_429_does_not_enter_fallback_on_intermediate_retry(self, tmp_path):
        """
        On first 429 retry (not the last retry), enter_fallback must NOT be called yet.
        Only after all retries are exhausted should fallback be entered.
        """
        from pacemaker import api_client, fallback

        usage_cache_path = _write_usage_cache(tmp_path)
        state_path = tmp_path / "fallback_state.json"
        backoff_path = tmp_path / "api_backoff.json"

        call_count = {"n": 0}
        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.json.return_value = _make_usage_response()

        mock_429 = MagicMock()
        mock_429.status_code = 429

        # Return 429 once, then 200 — fallback should NOT be entered
        def side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return mock_429
            return mock_200

        with (
            patch("pacemaker.api_client.requests.get", side_effect=side_effect),
            patch("pacemaker.api_client.time.sleep"),
        ):

            result = api_client.fetch_usage(
                access_token="test-token",
                timeout=5,
                backoff_state_path=str(backoff_path),
                fallback_state_path=str(state_path),
                usage_cache_path=str(usage_cache_path),
            )

        # Should have succeeded on second attempt
        assert result is not None
        # Fallback should NOT be active since we recovered
        assert fallback.is_fallback_active(str(state_path)) is False


class TestFetchUsageSuccessExitsFallback:
    """Tests that successful 200 response calls exit_fallback()."""

    def test_success_exits_fallback_when_active(self, tmp_path):
        """
        When fetch_usage succeeds (200) while fallback is active, exit_fallback() must be called.
        """
        from pacemaker import api_client, fallback

        usage_cache_path = _write_usage_cache(
            tmp_path, five_hour_util=45.0, seven_day_util=30.0
        )
        state_path = tmp_path / "fallback_state.json"
        backoff_path = tmp_path / "api_backoff.json"

        # Pre-populate fallback state as FALLBACK
        fallback.enter_fallback(str(usage_cache_path), str(state_path))
        assert fallback.is_fallback_active(str(state_path)) is True

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _make_usage_response(
            five_hour_util=50.0, seven_day_util=35.0
        )

        with patch("pacemaker.api_client.requests.get", return_value=mock_resp):
            result = api_client.fetch_usage(
                access_token="test-token",
                timeout=5,
                backoff_state_path=str(backoff_path),
                fallback_state_path=str(state_path),
                usage_cache_path=str(usage_cache_path),
            )

        assert result is not None
        # Fallback must have been exited
        assert fallback.is_fallback_active(str(state_path)) is False

    def test_success_while_already_normal_does_not_crash(self, tmp_path):
        """
        Calling exit_fallback when already in NORMAL mode is a no-op; must not crash.
        """
        from pacemaker import api_client, fallback

        usage_cache_path = _write_usage_cache(tmp_path)
        state_path = tmp_path / "fallback_state.json"
        backoff_path = tmp_path / "api_backoff.json"

        # State is NORMAL (default)
        assert fallback.is_fallback_active(str(state_path)) is False

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _make_usage_response()

        with patch("pacemaker.api_client.requests.get", return_value=mock_resp):
            result = api_client.fetch_usage(
                access_token="test-token",
                timeout=5,
                backoff_state_path=str(backoff_path),
                fallback_state_path=str(state_path),
                usage_cache_path=str(usage_cache_path),
            )

        assert result is not None
        # Still normal, no crash
        assert fallback.is_fallback_active(str(state_path)) is False

    def test_success_exits_fallback_with_correct_utilization_values(self, tmp_path):
        """
        exit_fallback() must be called with real 5h/7d utilization from the API response.
        """
        from pacemaker import api_client, fallback

        usage_cache_path = _write_usage_cache(
            tmp_path, five_hour_util=45.0, seven_day_util=30.0
        )
        state_path = tmp_path / "fallback_state.json"
        backoff_path = tmp_path / "api_backoff.json"

        fallback.enter_fallback(str(usage_cache_path), str(state_path))

        exit_calls = []
        original_exit = fallback.exit_fallback

        def capturing_exit(real_5h, real_7d, state_path=None):
            exit_calls.append({"real_5h": real_5h, "real_7d": real_7d})
            return original_exit(real_5h, real_7d, state_path=state_path)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _make_usage_response(
            five_hour_util=52.0, seven_day_util=33.0
        )

        with (
            patch("pacemaker.api_client.requests.get", return_value=mock_resp),
            patch("pacemaker.fallback.exit_fallback", side_effect=capturing_exit),
        ):

            api_client.fetch_usage(
                access_token="test-token",
                timeout=5,
                backoff_state_path=str(backoff_path),
                fallback_state_path=str(state_path),
                usage_cache_path=str(usage_cache_path),
            )

        assert len(exit_calls) == 1
        assert exit_calls[0]["real_5h"] == pytest.approx(52.0, abs=0.1)
        assert exit_calls[0]["real_7d"] == pytest.approx(33.0, abs=0.1)


class TestFetchUsageNon429ErrorNoFallbackChange:
    """Tests that non-429 errors do not trigger fallback transitions."""

    def test_non_429_error_does_not_enter_fallback(self, tmp_path):
        """
        Non-429 HTTP errors (500, 401, etc.) must NOT trigger enter_fallback().
        Only rate limit (429) triggers fallback mode.
        """
        from pacemaker import api_client, fallback

        usage_cache_path = _write_usage_cache(tmp_path)
        state_path = tmp_path / "fallback_state.json"
        backoff_path = tmp_path / "api_backoff.json"

        for status_code in [500, 401, 403, 503]:
            # Reset state for each test
            if state_path.exists():
                state_path.unlink()

            mock_resp = MagicMock()
            mock_resp.status_code = status_code

            with patch("pacemaker.api_client.requests.get", return_value=mock_resp):
                result = api_client.fetch_usage(
                    access_token="test-token",
                    timeout=5,
                    backoff_state_path=str(backoff_path),
                    fallback_state_path=str(state_path),
                    usage_cache_path=str(usage_cache_path),
                )

            assert result is None
            # Non-429 must NOT trigger fallback
            assert (
                fallback.is_fallback_active(str(state_path)) is False
            ), f"Fallback should not be active after HTTP {status_code}"

    def test_connection_error_does_not_enter_fallback(self, tmp_path):
        """
        Network errors (connection refused, timeout) must NOT enter fallback.
        Fallback is only for rate limiting (429), not transient network failures.
        """
        from pacemaker import api_client, fallback
        import requests as req

        usage_cache_path = _write_usage_cache(tmp_path)
        state_path = tmp_path / "fallback_state.json"
        backoff_path = tmp_path / "api_backoff.json"

        with patch(
            "pacemaker.api_client.requests.get",
            side_effect=req.exceptions.ConnectionError("refused"),
        ):
            result = api_client.fetch_usage(
                access_token="test-token",
                timeout=5,
                backoff_state_path=str(backoff_path),
                fallback_state_path=str(state_path),
                usage_cache_path=str(usage_cache_path),
            )

        assert result is None
        assert fallback.is_fallback_active(str(state_path)) is False


class TestFetchUsageBackoffSkipDoesNotChangeFallback:
    """Tests that backoff skip does not affect fallback state."""

    def test_backoff_skip_does_not_exit_fallback(self, tmp_path):
        """
        When API call is skipped due to backoff, fallback state must remain unchanged.
        If we entered fallback due to 429 and backoff is now active, we should stay
        in fallback until the API call actually succeeds.
        """
        from pacemaker import api_client, fallback

        usage_cache_path = _write_usage_cache(tmp_path)
        state_path = tmp_path / "fallback_state.json"
        backoff_path = tmp_path / "api_backoff.json"

        # Set up: fallback is active
        fallback.enter_fallback(str(usage_cache_path), str(state_path))
        assert fallback.is_fallback_active(str(state_path)) is True

        # Simulate active backoff (is_in_backoff returns True -> call is skipped)
        with (
            patch("pacemaker.api_client.api_backoff.is_in_backoff", return_value=True),
            patch(
                "pacemaker.api_client.api_backoff.get_backoff_remaining_seconds",
                return_value=30.0,
            ),
        ):

            result = api_client.fetch_usage(
                access_token="test-token",
                timeout=5,
                backoff_state_path=str(backoff_path),
                fallback_state_path=str(state_path),
                usage_cache_path=str(usage_cache_path),
            )

        assert result is None  # Skipped due to backoff
        # Fallback must STILL be active — backoff skip does not exit fallback
        assert fallback.is_fallback_active(str(state_path)) is True
