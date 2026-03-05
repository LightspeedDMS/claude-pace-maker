#!/usr/bin/env python3
"""
Tests for api_backoff module - exponential backoff for Anthropic API rate limits.

TDD: Tests written first to define behavior before implementation.
Part 1: Core unit tests for load_backoff_state, save_backoff_state, record_429.
"""

import json
import time
from pathlib import Path
import sys

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestLoadBackoffState:
    """Tests for load_backoff_state() function."""

    def test_returns_defaults_when_file_missing(self, tmp_path):
        """load_backoff_state returns default state when file does not exist."""
        from pacemaker.api_backoff import load_backoff_state

        missing_path = tmp_path / "api_backoff.json"
        state = load_backoff_state(str(missing_path))

        assert state["consecutive_429s"] == 0
        assert state["backoff_until"] is None
        assert state["last_success_time"] is None

    def test_returns_defaults_when_file_corrupt(self, tmp_path):
        """load_backoff_state returns default state when file contains invalid JSON."""
        from pacemaker.api_backoff import load_backoff_state

        corrupt_path = tmp_path / "api_backoff.json"
        corrupt_path.write_text("not valid json {{{")
        state = load_backoff_state(str(corrupt_path))

        assert state["consecutive_429s"] == 0
        assert state["backoff_until"] is None
        assert state["last_success_time"] is None

    def test_returns_defaults_when_file_empty(self, tmp_path):
        """load_backoff_state returns default state when file is empty."""
        from pacemaker.api_backoff import load_backoff_state

        empty_path = tmp_path / "api_backoff.json"
        empty_path.write_text("")
        state = load_backoff_state(str(empty_path))

        assert state["consecutive_429s"] == 0
        assert state["backoff_until"] is None
        assert state["last_success_time"] is None

    def test_returns_saved_state(self, tmp_path):
        """load_backoff_state returns previously saved state."""
        from pacemaker.api_backoff import load_backoff_state

        state_path = tmp_path / "api_backoff.json"
        saved = {
            "consecutive_429s": 3,
            "backoff_until": time.time() + 600,
            "last_success_time": time.time() - 3600,
        }
        state_path.write_text(json.dumps(saved))

        state = load_backoff_state(str(state_path))

        assert state["consecutive_429s"] == 3
        assert state["backoff_until"] == pytest.approx(saved["backoff_until"], abs=0.01)
        assert state["last_success_time"] == pytest.approx(
            saved["last_success_time"], abs=0.01
        )


class TestSaveBackoffState:
    """Tests for save_backoff_state() - atomic writes."""

    def test_saves_state_to_file(self, tmp_path):
        """save_backoff_state writes state to the given path."""
        from pacemaker.api_backoff import save_backoff_state, load_backoff_state

        state_path = tmp_path / "api_backoff.json"
        state = {
            "consecutive_429s": 2,
            "backoff_until": time.time() + 300,
            "last_success_time": None,
        }
        save_backoff_state(state, str(state_path))

        loaded = load_backoff_state(str(state_path))
        assert loaded["consecutive_429s"] == 2
        assert loaded["backoff_until"] == pytest.approx(
            state["backoff_until"], abs=0.01
        )

    def test_atomic_write_no_tmp_file_left(self, tmp_path):
        """save_backoff_state does not leave .tmp files behind."""
        from pacemaker.api_backoff import save_backoff_state

        state_path = tmp_path / "api_backoff.json"
        state = {
            "consecutive_429s": 1,
            "backoff_until": None,
            "last_success_time": None,
        }
        save_backoff_state(state, str(state_path))

        tmp_files = list(tmp_path.glob("*.tmp*"))
        assert len(tmp_files) == 0

    def test_creates_parent_directory(self, tmp_path):
        """save_backoff_state creates parent directories if missing."""
        from pacemaker.api_backoff import save_backoff_state

        nested_path = tmp_path / "subdir" / "api_backoff.json"
        state = {
            "consecutive_429s": 0,
            "backoff_until": None,
            "last_success_time": None,
        }
        save_backoff_state(state, str(nested_path))

        assert nested_path.exists()


class TestRecord429:
    """Tests for record_429() - exponential backoff calculation.

    Backoff formula: backoff_until = now + min(300 * 2^consecutive_429s, 3600)
    where consecutive_429s is incremented BEFORE applying the formula.
    So: 1st 429 -> consecutive=1, delay=min(600, 3600)=600s
        2nd 429 -> consecutive=2, delay=min(1200, 3600)=1200s
        3rd 429 -> consecutive=3, delay=min(2400, 3600)=2400s
        4th 429 -> consecutive=4, delay=min(4800, 3600)=3600s (capped)
    """

    def test_first_429_sets_backoff_600_seconds(self, tmp_path):
        """First 429: consecutive=1, backoff = 300 * 2^1 = 600 seconds."""
        from pacemaker.api_backoff import record_429, load_backoff_state

        state_path = str(tmp_path / "api_backoff.json")
        before = time.time()
        record_429(state_path)
        after = time.time()

        state = load_backoff_state(state_path)
        assert state["consecutive_429s"] == 1
        assert state["backoff_until"] is not None
        expected_min = before + 600 - 1
        expected_max = after + 600 + 1
        assert expected_min <= state["backoff_until"] <= expected_max

    def test_second_429_doubles_backoff(self, tmp_path):
        """Second consecutive 429: consecutive=2, backoff = 300 * 2^2 = 1200s."""
        from pacemaker.api_backoff import record_429, load_backoff_state

        state_path = str(tmp_path / "api_backoff.json")
        record_429(state_path)  # consecutive=1
        before = time.time()
        record_429(state_path)  # consecutive=2
        after = time.time()

        state = load_backoff_state(state_path)
        assert state["consecutive_429s"] == 2
        expected_min = before + 1200 - 1
        expected_max = after + 1200 + 1
        assert expected_min <= state["backoff_until"] <= expected_max

    def test_fourth_429_caps_at_3600_seconds(self, tmp_path):
        """Fourth 429: 300*2^4=4800 -> capped at 3600 seconds."""
        from pacemaker.api_backoff import record_429, load_backoff_state

        state_path = str(tmp_path / "api_backoff.json")
        for _ in range(4):
            record_429(state_path)

        state = load_backoff_state(state_path)
        assert state["consecutive_429s"] == 4
        before_estimated = time.time()
        assert state["backoff_until"] <= before_estimated + 3600 + 2

    def test_many_429s_never_exceed_cap(self, tmp_path):
        """After many 429s, backoff_until never exceeds now + 3600."""
        from pacemaker.api_backoff import record_429, load_backoff_state

        state_path = str(tmp_path / "api_backoff.json")
        for _ in range(10):
            record_429(state_path)

        state = load_backoff_state(state_path)
        before_estimated = time.time()
        assert state["backoff_until"] <= before_estimated + 3600 + 2

    def test_increments_from_existing_state(self, tmp_path):
        """record_429 increments from pre-existing consecutive_429s count."""
        from pacemaker.api_backoff import (
            record_429,
            load_backoff_state,
            save_backoff_state,
        )

        state_path = str(tmp_path / "api_backoff.json")
        save_backoff_state(
            {
                "consecutive_429s": 2,
                "backoff_until": time.time() + 100,
                "last_success_time": None,
            },
            state_path,
        )

        record_429(state_path)

        state = load_backoff_state(state_path)
        assert state["consecutive_429s"] == 3


class TestRecordSuccess:
    """Tests for record_success() - resets backoff state after successful API call."""

    def test_resets_consecutive_count_to_zero(self, tmp_path):
        """record_success resets consecutive_429s to 0."""
        from pacemaker.api_backoff import record_429, record_success, load_backoff_state

        state_path = str(tmp_path / "api_backoff.json")
        for _ in range(3):
            record_429(state_path)

        record_success(state_path)

        state = load_backoff_state(state_path)
        assert state["consecutive_429s"] == 0

    def test_clears_backoff_until(self, tmp_path):
        """record_success sets backoff_until to None."""
        from pacemaker.api_backoff import record_429, record_success, load_backoff_state

        state_path = str(tmp_path / "api_backoff.json")
        record_429(state_path)

        record_success(state_path)

        state = load_backoff_state(state_path)
        assert state["backoff_until"] is None

    def test_sets_last_success_time(self, tmp_path):
        """record_success sets last_success_time to current epoch timestamp."""
        from pacemaker.api_backoff import record_success, load_backoff_state

        state_path = str(tmp_path / "api_backoff.json")
        before = time.time()
        record_success(state_path)
        after = time.time()

        state = load_backoff_state(state_path)
        assert state["last_success_time"] is not None
        assert before - 1 <= state["last_success_time"] <= after + 1

    def test_works_on_fresh_state_no_error(self, tmp_path):
        """record_success works fine even if no 429s occurred before."""
        from pacemaker.api_backoff import record_success, load_backoff_state

        state_path = str(tmp_path / "api_backoff.json")
        # No prior 429s - should not raise
        record_success(state_path)

        state = load_backoff_state(state_path)
        assert state["consecutive_429s"] == 0
        assert state["backoff_until"] is None
        assert state["last_success_time"] is not None


class TestIsInBackoff:
    """Tests for is_in_backoff() - check if currently rate-limited."""

    def test_returns_false_when_no_backoff_state(self, tmp_path):
        """is_in_backoff returns False when backoff_until is None (fresh state)."""
        from pacemaker.api_backoff import is_in_backoff

        state_path = str(tmp_path / "api_backoff.json")
        assert is_in_backoff(state_path) is False

    def test_returns_true_when_backoff_in_future(self, tmp_path):
        """is_in_backoff returns True when backoff_until is in the future."""
        from pacemaker.api_backoff import is_in_backoff, save_backoff_state

        state_path = str(tmp_path / "api_backoff.json")
        save_backoff_state(
            {
                "consecutive_429s": 1,
                "backoff_until": time.time() + 600,
                "last_success_time": None,
            },
            state_path,
        )

        assert is_in_backoff(state_path) is True

    def test_returns_false_when_backoff_expired(self, tmp_path):
        """is_in_backoff returns False when backoff_until is in the past."""
        from pacemaker.api_backoff import is_in_backoff, save_backoff_state

        state_path = str(tmp_path / "api_backoff.json")
        save_backoff_state(
            {
                "consecutive_429s": 1,
                "backoff_until": time.time() - 1,
                "last_success_time": None,
            },
            state_path,
        )

        assert is_in_backoff(state_path) is False

    def test_returns_false_when_file_missing(self, tmp_path):
        """is_in_backoff returns False when state file does not exist."""
        from pacemaker.api_backoff import is_in_backoff

        missing_path = str(tmp_path / "no_such_file.json")
        assert is_in_backoff(missing_path) is False


class TestGetBackoffRemainingSeconds:
    """Tests for get_backoff_remaining_seconds() - time left in backoff."""

    def test_returns_zero_when_not_in_backoff(self, tmp_path):
        """get_backoff_remaining_seconds returns 0.0 when backoff_until is None."""
        from pacemaker.api_backoff import get_backoff_remaining_seconds

        state_path = str(tmp_path / "api_backoff.json")
        assert get_backoff_remaining_seconds(state_path) == 0.0

    def test_returns_remaining_seconds_when_in_backoff(self, tmp_path):
        """get_backoff_remaining_seconds returns time until backoff expires."""
        from pacemaker.api_backoff import (
            get_backoff_remaining_seconds,
            save_backoff_state,
        )

        state_path = str(tmp_path / "api_backoff.json")
        future_time = time.time() + 300
        save_backoff_state(
            {
                "consecutive_429s": 1,
                "backoff_until": future_time,
                "last_success_time": None,
            },
            state_path,
        )

        remaining = get_backoff_remaining_seconds(state_path)
        assert 298 <= remaining <= 301

    def test_returns_zero_when_backoff_expired(self, tmp_path):
        """get_backoff_remaining_seconds returns 0.0 when backoff has expired."""
        from pacemaker.api_backoff import (
            get_backoff_remaining_seconds,
            save_backoff_state,
        )

        state_path = str(tmp_path / "api_backoff.json")
        save_backoff_state(
            {
                "consecutive_429s": 1,
                "backoff_until": time.time() - 100,
                "last_success_time": None,
            },
            state_path,
        )

        assert get_backoff_remaining_seconds(state_path) == 0.0

    def test_returns_zero_when_file_missing(self, tmp_path):
        """get_backoff_remaining_seconds returns 0.0 when file does not exist."""
        from pacemaker.api_backoff import get_backoff_remaining_seconds

        missing_path = str(tmp_path / "no_such_file.json")
        assert get_backoff_remaining_seconds(missing_path) == 0.0


class TestFetchUsageBackoffIntegration:
    """Integration tests for fetch_usage() with backoff logic."""

    def test_skips_api_call_when_in_backoff(self, tmp_path):
        """fetch_usage returns None immediately without HTTP request when in backoff."""
        from pacemaker import api_client
        from pacemaker.api_backoff import save_backoff_state
        from unittest.mock import patch

        state_path = str(tmp_path / "api_backoff.json")
        save_backoff_state(
            {
                "consecutive_429s": 2,
                "backoff_until": time.time() + 600,
                "last_success_time": None,
            },
            state_path,
        )

        with patch("pacemaker.api_client.requests.get") as mock_get:
            result = api_client.fetch_usage(
                "fake-token", timeout=5, backoff_state_path=state_path
            )

        assert result is None
        mock_get.assert_not_called()

    def test_calls_record_429_on_429_response(self, tmp_path):
        """fetch_usage calls record_429 when API returns 429 after all retries."""
        from pacemaker import api_client
        from pacemaker.api_backoff import load_backoff_state
        from unittest.mock import patch, MagicMock

        state_path = str(tmp_path / "api_backoff.json")

        mock_429 = MagicMock()
        mock_429.status_code = 429

        with patch("pacemaker.api_client.requests.get", return_value=mock_429):
            with patch("pacemaker.api_client.time.sleep"):
                result = api_client.fetch_usage(
                    "fake-token", timeout=5, backoff_state_path=state_path
                )

        assert result is None
        state = load_backoff_state(state_path)
        assert state["consecutive_429s"] > 0
        assert state["backoff_until"] is not None

    def test_calls_record_success_on_200_response(self, tmp_path):
        """fetch_usage calls record_success when API returns 200."""
        from pacemaker import api_client
        from pacemaker.api_backoff import load_backoff_state, save_backoff_state
        from unittest.mock import patch, MagicMock

        state_path = str(tmp_path / "api_backoff.json")
        save_backoff_state(
            {
                "consecutive_429s": 3,
                "backoff_until": time.time() - 1,  # expired backoff
                "last_success_time": None,
            },
            state_path,
        )

        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.json.return_value = {
            "five_hour": {"utilization": 0.5, "resets_at": None},
            "seven_day": {"utilization": 0.3, "resets_at": None},
        }

        with patch("pacemaker.api_client.requests.get", return_value=mock_200):
            with patch("pacemaker.api_client._cache_usage_response"):
                api_client.fetch_usage(
                    "fake-token", timeout=5, backoff_state_path=state_path
                )

        state = load_backoff_state(state_path)
        assert state["consecutive_429s"] == 0
        assert state["backoff_until"] is None
        assert state["last_success_time"] is not None

    def test_does_not_change_backoff_on_non_429_error(self, tmp_path):
        """fetch_usage leaves backoff state unchanged on non-429 errors (e.g., 500)."""
        from pacemaker import api_client
        from pacemaker.api_backoff import load_backoff_state
        from unittest.mock import patch, MagicMock

        state_path = str(tmp_path / "api_backoff.json")

        mock_500 = MagicMock()
        mock_500.status_code = 500

        with patch("pacemaker.api_client.requests.get", return_value=mock_500):
            result = api_client.fetch_usage(
                "fake-token", timeout=5, backoff_state_path=state_path
            )

        assert result is None
        state = load_backoff_state(state_path)
        assert state["consecutive_429s"] == 0

    def test_retries_on_429_before_recording_backoff(self, tmp_path):
        """fetch_usage retries up to 2 times on 429 before recording persistent backoff."""
        from pacemaker import api_client
        from pacemaker.api_backoff import load_backoff_state
        from unittest.mock import patch, MagicMock

        state_path = str(tmp_path / "api_backoff.json")

        mock_429 = MagicMock()
        mock_429.status_code = 429
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return mock_429

        with patch("pacemaker.api_client.requests.get", side_effect=side_effect):
            with patch("pacemaker.api_client.time.sleep"):
                result = api_client.fetch_usage(
                    "fake-token", timeout=5, backoff_state_path=state_path
                )

        assert result is None
        # 1 initial + 2 retries = 3 total calls
        assert call_count == 3
        state = load_backoff_state(state_path)
        # Only ONE persistent backoff recorded (not one per retry)
        assert state["consecutive_429s"] == 1

    def test_succeeds_on_retry_after_429(self, tmp_path):
        """fetch_usage succeeds when a retry after 429 returns 200."""
        from pacemaker import api_client
        from pacemaker.api_backoff import load_backoff_state
        from unittest.mock import patch, MagicMock

        state_path = str(tmp_path / "api_backoff.json")

        mock_429 = MagicMock()
        mock_429.status_code = 429

        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.json.return_value = {
            "five_hour": {"utilization": 0.4, "resets_at": None},
            "seven_day": None,
        }

        responses = [mock_429, mock_200]

        with patch("pacemaker.api_client.requests.get", side_effect=responses):
            with patch("pacemaker.api_client.time.sleep"):
                with patch("pacemaker.api_client._cache_usage_response"):
                    result = api_client.fetch_usage(
                        "fake-token", timeout=5, backoff_state_path=state_path
                    )

        assert result is not None
        state = load_backoff_state(state_path)
        assert state["consecutive_429s"] == 0

    def test_backward_compatible_without_backoff_path(self):
        """fetch_usage works without explicit backoff_state_path (uses default)."""
        from pacemaker import api_client
        from unittest.mock import patch, MagicMock

        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.json.return_value = {
            "five_hour": {"utilization": 0.0, "resets_at": None},
            "seven_day": None,
        }

        with patch("pacemaker.api_client.requests.get", return_value=mock_200):
            with patch("pacemaker.api_client._cache_usage_response"):
                # Must not raise even without explicit backoff_state_path
                api_client.fetch_usage("fake-token", timeout=5)
        # No assertion on result value - just must not raise


class TestFetchUserProfileBackoffIntegration:
    """Integration tests for fetch_user_profile() with backoff logic."""

    def test_skips_api_call_when_in_backoff(self, tmp_path):
        """fetch_user_profile returns None immediately when in backoff."""
        from pacemaker import api_client
        from pacemaker.api_backoff import save_backoff_state
        from unittest.mock import patch

        state_path = str(tmp_path / "api_backoff.json")
        save_backoff_state(
            {
                "consecutive_429s": 1,
                "backoff_until": time.time() + 300,
                "last_success_time": None,
            },
            state_path,
        )

        with patch("pacemaker.api_client.requests.get") as mock_get:
            result = api_client.fetch_user_profile(
                "fake-token", timeout=3, backoff_state_path=state_path
            )

        assert result is None
        mock_get.assert_not_called()

    def test_calls_record_429_on_429_response(self, tmp_path):
        """fetch_user_profile records 429 when API returns 429."""
        from pacemaker import api_client
        from pacemaker.api_backoff import load_backoff_state
        from unittest.mock import patch, MagicMock

        state_path = str(tmp_path / "api_backoff.json")

        mock_429 = MagicMock()
        mock_429.status_code = 429

        with patch("pacemaker.api_client.requests.get", return_value=mock_429):
            with patch("pacemaker.api_client.time.sleep"):
                result = api_client.fetch_user_profile(
                    "fake-token", timeout=3, backoff_state_path=state_path
                )

        assert result is None
        state = load_backoff_state(state_path)
        assert state["consecutive_429s"] > 0

    def test_does_not_change_backoff_on_non_429_error(self, tmp_path):
        """fetch_user_profile leaves backoff unchanged on non-429 errors."""
        from pacemaker import api_client
        from pacemaker.api_backoff import load_backoff_state
        from unittest.mock import patch, MagicMock

        state_path = str(tmp_path / "api_backoff.json")

        mock_404 = MagicMock()
        mock_404.status_code = 404

        with patch("pacemaker.api_client.requests.get", return_value=mock_404):
            result = api_client.fetch_user_profile(
                "fake-token", timeout=3, backoff_state_path=state_path
            )

        assert result is None
        state = load_backoff_state(state_path)
        assert state["consecutive_429s"] == 0

    def test_backward_compatible_without_backoff_path(self):
        """fetch_user_profile works without explicit backoff_state_path."""
        from pacemaker import api_client
        from unittest.mock import patch, MagicMock

        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.json.return_value = {"account": {"email": "test@example.com"}}

        # Mock backoff check to avoid reading real backoff state file
        with patch("pacemaker.api_backoff.is_in_backoff", return_value=False):
            with patch("pacemaker.api_backoff.record_success"):
                with patch("pacemaker.api_client.requests.get", return_value=mock_200):
                    result = api_client.fetch_user_profile("fake-token", timeout=3)

        assert result is not None
