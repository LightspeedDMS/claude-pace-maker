#!/usr/bin/env python3
"""
Unit tests for API client.

Tests API integration for:
- Fetching usage data from OAuth API
- Parsing API responses
- Handling NULL reset times
- Graceful degradation on errors
"""

import unittest
from datetime import datetime
from unittest.mock import patch, Mock
import json


class TestAPIClient(unittest.TestCase):
    """Test API client operations."""

    def test_parse_usage_response_complete(self):
        """Should parse complete API response with all fields."""
        from pacemaker.api_client import parse_usage_response

        response_data = {
            "five_hour": {
                "utilization": 45.5,
                "resets_at": "2025-11-14T20:00:00.000000+00:00",
            },
            "seven_day": {
                "utilization": 62.3,
                "resets_at": "2025-11-18T16:00:00.000000+00:00",
            },
        }

        result = parse_usage_response(response_data)

        self.assertIsNotNone(result)
        self.assertEqual(result["five_hour_util"], 45.5)
        self.assertEqual(result["seven_day_util"], 62.3)
        self.assertIsInstance(result["five_hour_resets_at"], datetime)
        self.assertIsInstance(result["seven_day_resets_at"], datetime)

    def test_parse_usage_response_with_nulls(self):
        """Should handle NULL reset times (inactive windows)."""
        from pacemaker.api_client import parse_usage_response

        response_data = {
            "five_hour": {
                "utilization": 0.0,
                "resets_at": None,  # NULL - inactive window
            },
            "seven_day": {
                "utilization": 75.0,
                "resets_at": "2025-11-18T16:00:00.000000+00:00",
            },
        }

        result = parse_usage_response(response_data)

        self.assertIsNotNone(result)
        self.assertEqual(result["five_hour_util"], 0.0)
        self.assertIsNone(result["five_hour_resets_at"])  # Should be None
        self.assertEqual(result["seven_day_util"], 75.0)
        self.assertIsInstance(result["seven_day_resets_at"], datetime)

    def test_parse_usage_response_missing_windows(self):
        """Should handle missing window data gracefully."""
        from pacemaker.api_client import parse_usage_response

        response_data = {
            "five_hour": {
                "utilization": 50.0,
                "resets_at": "2025-11-14T20:00:00.000000+00:00",
            }
            # seven_day missing entirely
        }

        result = parse_usage_response(response_data)

        # Should still parse available data
        self.assertIsNotNone(result)
        self.assertEqual(result["five_hour_util"], 50.0)
        # seven_day should default to 0/None
        self.assertEqual(result.get("seven_day_util", 0.0), 0.0)

    def test_fetch_usage_success(self):
        """Should successfully fetch and parse usage data."""
        from pacemaker.api_client import fetch_usage

        # Mock successful API response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "five_hour": {
                "utilization": 30.0,
                "resets_at": "2025-11-14T20:00:00.000000+00:00",
            },
            "seven_day": {
                "utilization": 50.0,
                "resets_at": "2025-11-18T16:00:00.000000+00:00",
            },
        }

        with patch("requests.get", return_value=mock_response):
            result = fetch_usage("fake-token-123")

        self.assertIsNotNone(result)
        self.assertEqual(result["five_hour_util"], 30.0)
        self.assertEqual(result["seven_day_util"], 50.0)

    def test_fetch_usage_api_unavailable(self):
        """Should return None when API is unavailable (graceful degradation)."""
        from pacemaker.api_client import fetch_usage

        # Mock network error
        with patch("requests.get", side_effect=Exception("Network error")):
            result = fetch_usage("fake-token-123")

        self.assertIsNone(result)

    def test_fetch_usage_401_unauthorized(self):
        """Should return None for 401 (expired token)."""
        from pacemaker.api_client import fetch_usage

        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        with patch("requests.get", return_value=mock_response):
            result = fetch_usage("expired-token")

        self.assertIsNone(result)

    def test_fetch_usage_500_server_error(self):
        """Should return None for 500 (server error)."""
        from pacemaker.api_client import fetch_usage

        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        with patch("requests.get", return_value=mock_response):
            result = fetch_usage("valid-token")

        self.assertIsNone(result)

    def test_load_access_token_from_credentials(self):
        """Should load access token from Claude credentials file."""
        from pacemaker.api_client import load_access_token

        mock_creds = {"claudeAiOauth": {"accessToken": "test-access-token-xyz"}}

        with patch(
            "builtins.open", unittest.mock.mock_open(read_data=json.dumps(mock_creds))
        ):
            with patch("pathlib.Path.exists", return_value=True):
                token = load_access_token()

        self.assertEqual(token, "test-access-token-xyz")

    def test_load_access_token_missing_file(self):
        """Should return None when credentials file doesn't exist."""
        from pacemaker.api_client import load_access_token

        with patch("pathlib.Path.exists", return_value=False):
            token = load_access_token()

        self.assertIsNone(token)

    def test_load_access_token_invalid_json(self):
        """Should return None when credentials file has invalid JSON."""
        from pacemaker.api_client import load_access_token

        with patch(
            "builtins.open", unittest.mock.mock_open(read_data="invalid json {")
        ):
            with patch("pathlib.Path.exists", return_value=True):
                token = load_access_token()

        self.assertIsNone(token)


if __name__ == "__main__":
    unittest.main()
