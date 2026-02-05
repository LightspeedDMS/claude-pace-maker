"""
Unit tests for OAuth profile fetching in api_client.py

Tests the fetch_user_profile() and get_user_email() functions that retrieve
user email from the Claude OAuth profile API for Langfuse userId tracking.
"""

import json
from unittest.mock import patch, mock_open, MagicMock

# Import functions to test (will fail until implemented)
from pacemaker.api_client import (
    fetch_user_profile,
    get_user_email,
    clear_email_cache,
    PROFILE_API_URL,
)


class TestFetchUserProfile:
    """Tests for fetch_user_profile() function"""

    def test_fetch_user_profile_returns_email_on_success(self):
        """Should return profile dict with email when API call succeeds"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "account": {"email": "test@example.com", "id": "user_123"}
        }

        with patch(
            "pacemaker.api_client.requests.get", return_value=mock_response
        ) as mock_get:
            result = fetch_user_profile("fake_token")

            # Verify API call
            mock_get.assert_called_once_with(
                PROFILE_API_URL,
                headers={
                    "Authorization": "Bearer fake_token",
                    "anthropic-beta": "oauth-2025-04-20",
                },
                timeout=3,
            )

            # Verify result
            assert result is not None
            assert result["account"]["email"] == "test@example.com"

    def test_fetch_user_profile_returns_none_on_404(self):
        """Should return None when API returns 404"""
        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("pacemaker.api_client.requests.get", return_value=mock_response):
            result = fetch_user_profile("fake_token")
            assert result is None

    def test_fetch_user_profile_returns_none_on_401(self):
        """Should return None when access token is invalid (401)"""
        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch("pacemaker.api_client.requests.get", return_value=mock_response):
            result = fetch_user_profile("invalid_token")
            assert result is None

    def test_fetch_user_profile_handles_network_error(self):
        """Should return None when network request fails"""
        with patch(
            "pacemaker.api_client.requests.get", side_effect=Exception("Network error")
        ):
            result = fetch_user_profile("fake_token")
            assert result is None

    def test_fetch_user_profile_handles_timeout(self):
        """Should return None when request times out"""
        with patch(
            "pacemaker.api_client.requests.get", side_effect=TimeoutError("Timeout")
        ):
            result = fetch_user_profile("fake_token")
            assert result is None

    def test_fetch_user_profile_respects_custom_timeout(self):
        """Should use custom timeout when provided"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"account": {"email": "test@example.com"}}

        with patch(
            "pacemaker.api_client.requests.get", return_value=mock_response
        ) as mock_get:
            fetch_user_profile("fake_token", timeout=10)

            # Verify timeout parameter
            call_kwargs = mock_get.call_args[1]
            assert call_kwargs["timeout"] == 10


class TestGetUserEmail:
    """Tests for get_user_email() convenience function"""

    def setup_method(self):
        """Clear cache before each test"""
        clear_email_cache()

    def test_get_user_email_returns_email_on_success(self):
        """Should return email string when API fetch succeeds"""
        mock_creds = {"claudeAiOauth": {"accessToken": "valid_token"}}

        mock_profile = {"account": {"email": "user@example.com"}}

        with (
            patch("pacemaker.api_client.Path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=json.dumps(mock_creds))),
            patch("pacemaker.api_client.fetch_user_profile", return_value=mock_profile),
        ):

            email = get_user_email()
            assert email == "user@example.com"

    def test_get_user_email_caches_result(self):
        """Should cache email and not call API twice"""
        mock_creds = {"claudeAiOauth": {"accessToken": "valid_token"}}

        mock_profile = {"account": {"email": "cached@example.com"}}

        with (
            patch("pacemaker.api_client.Path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=json.dumps(mock_creds))),
            patch(
                "pacemaker.api_client.fetch_user_profile", return_value=mock_profile
            ) as mock_fetch,
        ):

            # First call
            email1 = get_user_email()
            assert email1 == "cached@example.com"
            assert mock_fetch.call_count == 1

            # Second call - should use cache
            email2 = get_user_email()
            assert email2 == "cached@example.com"
            assert mock_fetch.call_count == 1  # Still 1, not called again

    def test_get_user_email_returns_none_when_credentials_missing(self):
        """Should return None when credentials file doesn't exist"""
        with patch("pacemaker.api_client.Path.exists", return_value=False):
            email = get_user_email()
            assert email is None

    def test_get_user_email_returns_none_when_token_missing(self):
        """Should return None when accessToken is not in credentials"""
        mock_creds = {"claudeAiOauth": {}}  # No accessToken

        with (
            patch("pacemaker.api_client.Path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=json.dumps(mock_creds))),
        ):

            email = get_user_email()
            assert email is None

    def test_get_user_email_returns_none_when_profile_fetch_fails(self):
        """Should return None when API fetch fails"""
        mock_creds = {"claudeAiOauth": {"accessToken": "valid_token"}}

        with (
            patch("pacemaker.api_client.Path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=json.dumps(mock_creds))),
            patch("pacemaker.api_client.fetch_user_profile", return_value=None),
        ):

            email = get_user_email()
            assert email is None

    def test_get_user_email_handles_malformed_json(self):
        """Should return None when credentials file has invalid JSON"""
        with (
            patch("pacemaker.api_client.Path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data="not valid json")),
        ):

            email = get_user_email()
            assert email is None

    def test_get_user_email_handles_missing_email_in_profile(self):
        """Should return None when profile doesn't contain email"""
        mock_creds = {"claudeAiOauth": {"accessToken": "valid_token"}}

        mock_profile = {"account": {}}  # No email

        with (
            patch("pacemaker.api_client.Path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=json.dumps(mock_creds))),
            patch("pacemaker.api_client.fetch_user_profile", return_value=mock_profile),
        ):

            email = get_user_email()
            assert email is None

    def test_get_user_email_handles_exception_in_email_extraction(self):
        """Should return None when profile.get() raises exception"""
        mock_creds = {"claudeAiOauth": {"accessToken": "valid_token"}}

        # Mock profile that raises exception when accessed
        mock_profile = MagicMock()
        mock_profile.get.side_effect = Exception("Unexpected error")

        with (
            patch("pacemaker.api_client.Path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=json.dumps(mock_creds))),
            patch("pacemaker.api_client.fetch_user_profile", return_value=mock_profile),
        ):

            email = get_user_email()
            assert email is None


class TestClearEmailCache:
    """Tests for cache management"""

    def test_clear_email_cache_resets_cache(self):
        """Should clear cached email so next call fetches fresh"""
        mock_creds = {"claudeAiOauth": {"accessToken": "valid_token"}}

        mock_profile = {"account": {"email": "fresh@example.com"}}

        with (
            patch("pacemaker.api_client.Path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=json.dumps(mock_creds))),
            patch(
                "pacemaker.api_client.fetch_user_profile", return_value=mock_profile
            ) as mock_fetch,
        ):

            # First call - caches
            get_user_email()
            assert mock_fetch.call_count == 1

            # Clear cache
            clear_email_cache()

            # Next call should fetch again
            get_user_email()
            assert mock_fetch.call_count == 2
