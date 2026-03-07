#!/usr/bin/env python3
"""
Tests for fallback wiring in api_client.py.

Story #38: API returns 429 -> enter_fallback; success -> exit_fallback.

Integration points tested:
- fetch_usage() on 429 (after all retries) -> model.enter_fallback() called
- fetch_usage() on 200 success -> model.exit_fallback() called
- fetch_usage() on non-429 error -> neither enter nor exit called

NOTE: HTTP calls are mocked (unittest.mock) because this module tests the
integration between api_client and UsageModel fallback state — not the HTTP
layer. Real HTTP calls would require a live API token.

All state management is via UsageModel (SQLite) — no JSON files.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
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


class TestFetchUsage429TriggersEnterFallback:
    """Tests that 429 response after all retries triggers enter_fallback()."""

    def test_429_after_all_retries_enters_fallback(self, tmp_path):
        """
        When fetch_usage exhausts all retries on 429, model.enter_fallback() must be called.
        This verifies the fallback wiring in api_client.py via UsageModel.
        """
        from pacemaker import api_client
        from pacemaker.usage_model import UsageModel

        db_path = str(tmp_path / "test.db")
        model = UsageModel(db_path=db_path)

        # Store baseline so enter_fallback() can read it from api_cache
        model.store_api_response(
            {
                "five_hour": {"utilization": 55.0, "resets_at": None},
                "seven_day": {"utilization": 38.0, "resets_at": None},
            }
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 429

        with (
            patch("pacemaker.api_client.requests.get", return_value=mock_resp),
            patch("pacemaker.api_client.time.sleep"),
        ):
            result = api_client.fetch_usage(
                access_token="test-token",
                timeout=5,
                db_path=db_path,
            )

        assert result is None  # 429 must return None

        # enter_fallback must have been triggered — verify via a fresh model instance
        model2 = UsageModel(db_path=db_path)
        assert model2.is_fallback_active() is True

    def test_429_fallback_captures_baselines_from_cache(self, tmp_path):
        """
        When fallback is entered after 429, baselines are read from api_cache table.
        Verify via _get_synthetic_snapshot() that baseline values are preserved.
        """
        from pacemaker import api_client
        from pacemaker.usage_model import UsageModel

        db_path = str(tmp_path / "test.db")
        model = UsageModel(db_path=db_path)

        # Store baseline BEFORE the 429 so enter_fallback() can read it
        model.store_api_response(
            {
                "five_hour": {"utilization": 55.0, "resets_at": None},
                "seven_day": {"utilization": 38.0, "resets_at": None},
            }
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 429

        with (
            patch("pacemaker.api_client.requests.get", return_value=mock_resp),
            patch("pacemaker.api_client.time.sleep"),
        ):
            api_client.fetch_usage(
                access_token="test-token",
                timeout=5,
                db_path=db_path,
            )

        # Verify via synthetic snapshot that baselines were captured
        model2 = UsageModel(db_path=db_path)
        assert model2.is_fallback_active() is True
        snapshot = model2._get_synthetic_snapshot()
        assert snapshot is not None
        # Synthetic starts at baseline (no accumulated costs yet)
        assert snapshot.five_hour_util == pytest.approx(55.0, abs=0.5)
        assert snapshot.seven_day_util == pytest.approx(38.0, abs=0.5)

    def test_429_does_not_enter_fallback_on_intermediate_retry(self, tmp_path):
        """
        On first 429 retry (not the last retry), enter_fallback must NOT be called yet.
        Only after all retries are exhausted should fallback be entered.
        """
        from pacemaker import api_client
        from pacemaker.usage_model import UsageModel

        db_path = str(tmp_path / "test.db")

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
                db_path=db_path,
            )

        # Should have succeeded on second attempt
        assert result is not None

        # Fallback should NOT be active since we recovered before exhausting retries
        model = UsageModel(db_path=db_path)
        assert model.is_fallback_active() is False


class TestFetchUsageSuccessExitsFallback:
    """Tests that successful 200 response calls exit_fallback()."""

    def test_success_exits_fallback_when_active(self, tmp_path):
        """
        When fetch_usage succeeds (200) while fallback is active, exit_fallback() must be called.
        """
        from pacemaker import api_client
        from pacemaker.usage_model import UsageModel

        db_path = str(tmp_path / "test.db")
        model = UsageModel(db_path=db_path)

        # Pre-populate api_cache and enter fallback
        model.store_api_response(
            {
                "five_hour": {"utilization": 45.0, "resets_at": None},
                "seven_day": {"utilization": 30.0, "resets_at": None},
            }
        )
        model.enter_fallback()
        assert model.is_fallback_active() is True

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _make_usage_response(
            five_hour_util=50.0, seven_day_util=35.0
        )

        with patch("pacemaker.api_client.requests.get", return_value=mock_resp):
            result = api_client.fetch_usage(
                access_token="test-token",
                timeout=5,
                db_path=db_path,
            )

        assert result is not None

        # Fallback must have been exited
        model2 = UsageModel(db_path=db_path)
        assert model2.is_fallback_active() is False

    def test_success_while_already_normal_does_not_crash(self, tmp_path):
        """
        Calling exit_fallback when already in NORMAL mode is a no-op; must not crash.
        """
        from pacemaker import api_client
        from pacemaker.usage_model import UsageModel

        db_path = str(tmp_path / "test.db")
        model = UsageModel(db_path=db_path)

        # State is NORMAL (default)
        assert model.is_fallback_active() is False

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _make_usage_response()

        with patch("pacemaker.api_client.requests.get", return_value=mock_resp):
            result = api_client.fetch_usage(
                access_token="test-token",
                timeout=5,
                db_path=db_path,
            )

        assert result is not None

        # Still normal, no crash
        model2 = UsageModel(db_path=db_path)
        assert model2.is_fallback_active() is False

    def test_success_exits_fallback_with_correct_utilization_values(self, tmp_path):
        """
        exit_fallback() must be called with real 5h/7d utilization from the API response.
        Verify by checking that fallback is exited (not still active after a 200).
        """
        from pacemaker import api_client
        from pacemaker.usage_model import UsageModel

        db_path = str(tmp_path / "test.db")
        model = UsageModel(db_path=db_path)

        # Pre-populate cache and enter fallback
        model.store_api_response(
            {
                "five_hour": {"utilization": 45.0, "resets_at": None},
                "seven_day": {"utilization": 30.0, "resets_at": None},
            }
        )
        model.enter_fallback()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _make_usage_response(
            five_hour_util=52.0, seven_day_util=33.0
        )

        with patch("pacemaker.api_client.requests.get", return_value=mock_resp):
            result = api_client.fetch_usage(
                access_token="test-token",
                timeout=5,
                db_path=db_path,
            )

        assert result is not None
        assert result["five_hour_util"] == pytest.approx(52.0, abs=0.1)
        assert result["seven_day_util"] == pytest.approx(33.0, abs=0.1)

        # Fallback must be exited after successful 200
        model2 = UsageModel(db_path=db_path)
        assert model2.is_fallback_active() is False


class TestFetchUsageNon429ErrorNoFallbackChange:
    """Tests that non-429 errors do not trigger fallback transitions."""

    def test_non_429_error_does_not_enter_fallback(self, tmp_path):
        """
        Non-429 HTTP errors (500, 401, etc.) must NOT trigger enter_fallback().
        Only rate limit (429) triggers fallback mode.
        """
        from pacemaker import api_client
        from pacemaker.usage_model import UsageModel

        for status_code in [500, 401, 403, 503]:
            # Use a fresh db per status code to isolate tests
            iter_db_path = str(tmp_path / f"test_{status_code}.db")

            mock_resp = MagicMock()
            mock_resp.status_code = status_code

            with patch("pacemaker.api_client.requests.get", return_value=mock_resp):
                result = api_client.fetch_usage(
                    access_token="test-token",
                    timeout=5,
                    db_path=iter_db_path,
                )

            assert result is None

            # Non-429 must NOT trigger fallback
            model = UsageModel(db_path=iter_db_path)
            assert (
                model.is_fallback_active() is False
            ), f"Fallback should not be active after HTTP {status_code}"

    def test_connection_error_does_not_enter_fallback(self, tmp_path):
        """
        Network errors (connection refused, timeout) must NOT enter fallback.
        Fallback is only for rate limiting (429), not transient network failures.
        """
        from pacemaker import api_client
        from pacemaker.usage_model import UsageModel
        import requests as req

        db_path = str(tmp_path / "test.db")

        with patch(
            "pacemaker.api_client.requests.get",
            side_effect=req.exceptions.ConnectionError("refused"),
        ):
            result = api_client.fetch_usage(
                access_token="test-token",
                timeout=5,
                db_path=db_path,
            )

        assert result is None

        model = UsageModel(db_path=db_path)
        assert model.is_fallback_active() is False


class TestFetchUsageBackoffSkipDoesNotChangeFallback:
    """Tests that backoff skip does not affect fallback state."""

    def test_backoff_skip_does_not_exit_fallback(self, tmp_path):
        """
        When API call is skipped due to backoff, fallback state must remain unchanged.
        If we entered fallback due to 429 and backoff is now active, we should stay
        in fallback until the API call actually succeeds.
        """
        from pacemaker import api_client
        from pacemaker.usage_model import UsageModel

        db_path = str(tmp_path / "test.db")
        model = UsageModel(db_path=db_path)

        # Set up: store baseline, enter fallback, then trigger backoff
        model.store_api_response(
            {
                "five_hour": {"utilization": 50.0, "resets_at": None},
                "seven_day": {"utilization": 35.0, "resets_at": None},
            }
        )
        model.enter_fallback()
        assert model.is_fallback_active() is True

        # Trigger backoff by recording a 429 directly
        model.record_429()
        assert model.is_in_backoff() is True

        # Attempt a fetch — should be skipped due to active backoff
        # (No HTTP mock needed since the call short-circuits before making a request)
        result = api_client.fetch_usage(
            access_token="test-token",
            timeout=5,
            db_path=db_path,
        )

        assert result is None  # Skipped due to backoff

        # Fallback must STILL be active — backoff skip does not exit fallback
        model2 = UsageModel(db_path=db_path)
        assert model2.is_fallback_active() is True
