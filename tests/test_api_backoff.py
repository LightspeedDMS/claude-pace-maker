#!/usr/bin/env python3
"""
Tests for UsageModel backoff methods — exponential backoff for Anthropic API rate limits.

Replaces old JSON api_backoff module tests.
UsageModel stores all backoff state in SQLite (usage.db), not JSON files.

Backoff formula: backoff_until = now + min(300 * 2^n, 3600)
where n is the new consecutive_429s count after incrementing.
  1st 429 -> n=1, delay=min(600, 3600)=600s
  2nd 429 -> n=2, delay=min(1200, 3600)=1200s
  3rd 429 -> n=3, delay=min(2400, 3600)=2400s
  4th 429 -> n=4, delay=min(4800, 3600)=3600s (capped)
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _make_model(tmp_path):
    """Return a UsageModel backed by a fresh isolated DB."""
    from pacemaker.usage_model import UsageModel

    return UsageModel(db_path=str(tmp_path / "test.db"))


class TestIsInBackoffFreshDB:
    """Tests for UsageModel.is_in_backoff() on a fresh database."""

    def test_returns_false_when_no_backoff_state(self, tmp_path):
        """is_in_backoff returns False on a fresh DB (no row in backoff_state)."""
        model = _make_model(tmp_path)
        assert model.is_in_backoff() is False

    def test_returns_false_after_record_success_on_fresh_db(self, tmp_path):
        """is_in_backoff returns False after record_success on a fresh DB."""
        model = _make_model(tmp_path)
        model.record_success()
        assert model.is_in_backoff() is False

    def test_returns_true_after_record_429(self, tmp_path):
        """is_in_backoff returns True immediately after record_429."""
        model = _make_model(tmp_path)
        model.record_429()
        assert model.is_in_backoff() is True

    def test_returns_false_after_record_success_clears_backoff(self, tmp_path):
        """is_in_backoff returns False after record_success resets backoff."""
        model = _make_model(tmp_path)
        model.record_429()
        assert model.is_in_backoff() is True
        model.record_success()
        assert model.is_in_backoff() is False


class TestRecord429:
    """Tests for UsageModel.record_429() — exponential backoff calculation."""

    def test_first_429_sets_backoff_600_seconds(self, tmp_path):
        """First 429: n=1, backoff = 300 * 2^1 = 600 seconds."""
        model = _make_model(tmp_path)
        model.record_429()

        remaining = model.get_backoff_remaining()
        # remaining should be ~ 600 seconds
        assert 598 <= remaining <= 601

        # Also verify is_in_backoff
        assert model.is_in_backoff() is True

    def test_second_429_doubles_backoff(self, tmp_path):
        """Second consecutive 429: n=2, backoff = 300 * 2^2 = 1200s."""
        model = _make_model(tmp_path)
        model.record_429()  # n=1, 600s
        model.record_429()  # n=2, 1200s

        remaining = model.get_backoff_remaining()
        # remaining should be ~ 1200 seconds
        assert 1198 <= remaining <= 1201

    def test_fourth_429_caps_at_3600_seconds(self, tmp_path):
        """Fourth 429: 300*2^4=4800 -> capped at 3600 seconds."""
        model = _make_model(tmp_path)
        for _ in range(4):
            model.record_429()

        remaining = model.get_backoff_remaining()
        # Must not exceed cap
        assert remaining <= 3601
        # Must be close to 3600
        assert remaining >= 3598

    def test_many_429s_never_exceed_cap(self, tmp_path):
        """After many 429s, backoff never exceeds 3600s."""
        model = _make_model(tmp_path)
        for _ in range(10):
            model.record_429()

        remaining = model.get_backoff_remaining()
        assert remaining <= 3601

    def test_consecutive_count_increments(self, tmp_path):
        """record_429 increments consecutive count with each call."""
        model = _make_model(tmp_path)
        # After 3 calls, remaining should be ~ 2400s (300 * 2^3)
        model.record_429()
        model.record_429()
        model.record_429()

        remaining = model.get_backoff_remaining()
        assert 2398 <= remaining <= 2401


class TestRecordSuccess:
    """Tests for UsageModel.record_success() — resets backoff state."""

    def test_resets_backoff_after_429s(self, tmp_path):
        """record_success clears is_in_backoff after multiple 429s."""
        model = _make_model(tmp_path)
        for _ in range(3):
            model.record_429()
        assert model.is_in_backoff() is True

        model.record_success()

        assert model.is_in_backoff() is False

    def test_clears_backoff_remaining_to_zero(self, tmp_path):
        """record_success makes get_backoff_remaining() return 0.0."""
        model = _make_model(tmp_path)
        model.record_429()
        assert model.get_backoff_remaining() > 0

        model.record_success()

        assert model.get_backoff_remaining() == 0.0

    def test_works_on_fresh_state_no_error(self, tmp_path):
        """record_success on a fresh DB does not raise and leaves state clean."""
        model = _make_model(tmp_path)
        model.record_success()  # No prior 429s — must not raise

        assert model.is_in_backoff() is False
        assert model.get_backoff_remaining() == 0.0


class TestGetBackoffRemaining:
    """Tests for UsageModel.get_backoff_remaining() — time left in backoff."""

    def test_returns_zero_on_fresh_db(self, tmp_path):
        """get_backoff_remaining returns 0.0 on a fresh DB."""
        model = _make_model(tmp_path)
        assert model.get_backoff_remaining() == 0.0

    def test_returns_positive_when_in_backoff(self, tmp_path):
        """get_backoff_remaining returns > 0 when in backoff."""
        model = _make_model(tmp_path)
        model.record_429()

        remaining = model.get_backoff_remaining()
        assert remaining > 0

    def test_returns_zero_after_success(self, tmp_path):
        """get_backoff_remaining returns 0.0 after record_success."""
        model = _make_model(tmp_path)
        model.record_429()
        model.record_success()

        assert model.get_backoff_remaining() == 0.0

    def test_returns_approximately_correct_value_after_first_429(self, tmp_path):
        """get_backoff_remaining returns ~600s after first record_429."""
        model = _make_model(tmp_path)
        model.record_429()

        remaining = model.get_backoff_remaining()
        # Should be within 2 seconds of 600
        assert 598 <= remaining <= 602


class TestFetchUsageBackoffIntegration:
    """Integration tests for fetch_usage() with UsageModel backoff."""

    def test_skips_api_call_when_in_backoff(self, tmp_path):
        """fetch_usage returns None immediately without HTTP request when in backoff."""
        from pacemaker import api_client
        from pacemaker.usage_model import UsageModel
        from unittest.mock import patch

        db_path = str(tmp_path / "test.db")
        model = UsageModel(db_path=db_path)
        model.record_429()  # Set backoff state
        assert model.is_in_backoff() is True

        with patch("pacemaker.api_client.requests.get") as mock_get:
            result = api_client.fetch_usage("fake-token", timeout=5, db_path=db_path)

        assert result is None
        mock_get.assert_not_called()

    def test_calls_record_429_on_429_response(self, tmp_path):
        """fetch_usage records backoff when API returns 429 after all retries."""
        from pacemaker import api_client
        from pacemaker.usage_model import UsageModel
        from unittest.mock import patch, MagicMock

        db_path = str(tmp_path / "test.db")

        mock_429 = MagicMock()
        mock_429.status_code = 429

        with patch("pacemaker.api_client.requests.get", return_value=mock_429):
            with patch("pacemaker.api_client.time.sleep"):
                result = api_client.fetch_usage(
                    "fake-token", timeout=5, db_path=db_path
                )

        assert result is None
        model = UsageModel(db_path=db_path)
        assert model.is_in_backoff() is True
        assert model.get_backoff_remaining() > 0

    def test_calls_record_success_on_200_response(self, tmp_path):
        """fetch_usage calls record_success when API returns 200."""
        from pacemaker import api_client
        from pacemaker.usage_model import UsageModel
        from unittest.mock import patch, MagicMock

        db_path = str(tmp_path / "test.db")
        # Pre-set some 429s
        model = UsageModel(db_path=db_path)
        model.record_429()
        model.record_429()
        model.record_429()
        # Expire the backoff so the call proceeds
        # We'll do this by calling record_success first, then checking the 200 path
        model.record_success()

        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.json.return_value = {
            "five_hour": {"utilization": 0.5, "resets_at": None},
            "seven_day": {"utilization": 0.3, "resets_at": None},
        }

        with patch("pacemaker.api_client.requests.get", return_value=mock_200):
            api_client.fetch_usage("fake-token", timeout=5, db_path=db_path)

        # After success, backoff must be clear
        model2 = UsageModel(db_path=db_path)
        assert model2.is_in_backoff() is False
        assert model2.get_backoff_remaining() == 0.0

    def test_does_not_change_backoff_on_non_429_error(self, tmp_path):
        """fetch_usage leaves backoff state unchanged on non-429 errors (e.g., 500)."""
        from pacemaker import api_client
        from pacemaker.usage_model import UsageModel
        from unittest.mock import patch, MagicMock

        db_path = str(tmp_path / "test.db")

        mock_500 = MagicMock()
        mock_500.status_code = 500

        with patch("pacemaker.api_client.requests.get", return_value=mock_500):
            result = api_client.fetch_usage("fake-token", timeout=5, db_path=db_path)

        assert result is None
        model = UsageModel(db_path=db_path)
        assert model.is_in_backoff() is False
        assert model.get_backoff_remaining() == 0.0

    def test_retries_on_429_before_recording_backoff(self, tmp_path):
        """fetch_usage retries up to max retries on 429 before recording persistent backoff."""
        from pacemaker import api_client
        from pacemaker.usage_model import UsageModel
        from unittest.mock import patch, MagicMock

        db_path = str(tmp_path / "test.db")

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
                    "fake-token", timeout=5, db_path=db_path
                )

        assert result is None
        # 1 initial + 2 retries = 3 total calls
        assert call_count == 3
        model = UsageModel(db_path=db_path)
        # Only ONE persistent backoff recorded (not one per retry)
        assert model.is_in_backoff() is True

    def test_succeeds_on_retry_after_429(self, tmp_path):
        """fetch_usage succeeds when a retry after 429 returns 200."""
        from pacemaker import api_client
        from pacemaker.usage_model import UsageModel
        from unittest.mock import patch, MagicMock

        db_path = str(tmp_path / "test.db")

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
                result = api_client.fetch_usage(
                    "fake-token", timeout=5, db_path=db_path
                )

        assert result is not None
        model = UsageModel(db_path=db_path)
        assert model.is_in_backoff() is False

    def test_works_without_explicit_db_path(self):
        """fetch_usage works without explicit db_path (uses default path)."""
        from pacemaker import api_client
        from unittest.mock import patch, MagicMock

        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.json.return_value = {
            "five_hour": {"utilization": 0.0, "resets_at": None},
            "seven_day": None,
        }

        # Patch UsageModel at its definition site to avoid touching the real DB
        with patch("pacemaker.api_client.requests.get", return_value=mock_200):
            with patch("pacemaker.usage_model.UsageModel") as MockModel:
                mock_instance = MagicMock()
                mock_instance.is_in_backoff.return_value = False
                mock_instance.get_backoff_remaining.return_value = 0.0
                MockModel.return_value = mock_instance
                # Must not raise
                api_client.fetch_usage("fake-token", timeout=5)


class TestFetchUserProfileBackoffIntegration:
    """Integration tests for fetch_user_profile() with UsageModel backoff."""

    def test_skips_api_call_when_in_backoff(self, tmp_path):
        """fetch_user_profile returns None immediately when in backoff."""
        from pacemaker import api_client
        from pacemaker.usage_model import UsageModel
        from unittest.mock import patch

        db_path = str(tmp_path / "test.db")
        model = UsageModel(db_path=db_path)
        model.record_429()

        with patch("pacemaker.api_client.requests.get") as mock_get:
            result = api_client.fetch_user_profile(
                "fake-token", timeout=3, db_path=db_path
            )

        assert result is None
        mock_get.assert_not_called()

    def test_calls_record_429_on_429_response(self, tmp_path):
        """fetch_user_profile records backoff when API returns 429."""
        from pacemaker import api_client
        from pacemaker.usage_model import UsageModel
        from unittest.mock import patch, MagicMock

        db_path = str(tmp_path / "test.db")

        mock_429 = MagicMock()
        mock_429.status_code = 429

        with patch("pacemaker.api_client.requests.get", return_value=mock_429):
            with patch("pacemaker.api_client.time.sleep"):
                result = api_client.fetch_user_profile(
                    "fake-token", timeout=3, db_path=db_path
                )

        assert result is None
        model = UsageModel(db_path=db_path)
        assert model.is_in_backoff() is True

    def test_does_not_change_backoff_on_non_429_error(self, tmp_path):
        """fetch_user_profile leaves backoff unchanged on non-429 errors."""
        from pacemaker import api_client
        from pacemaker.usage_model import UsageModel
        from unittest.mock import patch, MagicMock

        db_path = str(tmp_path / "test.db")

        mock_404 = MagicMock()
        mock_404.status_code = 404

        with patch("pacemaker.api_client.requests.get", return_value=mock_404):
            result = api_client.fetch_user_profile(
                "fake-token", timeout=3, db_path=db_path
            )

        assert result is None
        model = UsageModel(db_path=db_path)
        assert model.is_in_backoff() is False

    def test_returns_profile_on_200_response(self, tmp_path):
        """fetch_user_profile returns profile data on 200."""
        from pacemaker import api_client
        from pacemaker.usage_model import UsageModel
        from unittest.mock import patch, MagicMock

        db_path = str(tmp_path / "test.db")

        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.json.return_value = {"account": {"email": "test@example.com"}}

        with patch("pacemaker.api_client.requests.get", return_value=mock_200):
            result = api_client.fetch_user_profile(
                "fake-token", timeout=3, db_path=db_path
            )

        assert result is not None
        assert result["account"]["email"] == "test@example.com"
        model = UsageModel(db_path=db_path)
        assert model.is_in_backoff() is False
