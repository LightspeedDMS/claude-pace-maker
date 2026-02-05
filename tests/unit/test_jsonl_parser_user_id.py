"""
Unit tests for extract_user_id() with API fallback.

Tests the updated extract_user_id() function that:
1. First tries to extract email from transcript (backwards compatibility)
2. Falls back to API if transcript doesn't have email
"""

import json
from unittest.mock import patch, mock_open

from pacemaker.telemetry.jsonl_parser import extract_user_id


class TestExtractUserIdWithApiFallback:
    """Tests for extract_user_id() with API fallback"""

    def test_extract_user_id_from_transcript_first(self):
        """Should extract email from transcript if available (backwards compat)"""
        transcript_data = [
            {
                "type": "auth_profile",
                "profile": {"email": "from-transcript@example.com"},
            },
        ]
        transcript_content = "\n".join(json.dumps(entry) for entry in transcript_data)

        with patch("builtins.open", mock_open(read_data=transcript_content)):
            # Should NOT call API if transcript has email
            with patch("pacemaker.telemetry.jsonl_parser.get_user_email") as mock_api:
                email = extract_user_id("/fake/path.jsonl")

                assert email == "from-transcript@example.com"
                mock_api.assert_not_called()

    def test_extract_user_id_falls_back_to_api(self):
        """Should call API when transcript doesn't contain email"""
        transcript_data = [
            {"type": "session_start", "session_id": "abc123"},
            {"type": "message", "content": "Hello"},
        ]
        transcript_content = "\n".join(json.dumps(entry) for entry in transcript_data)

        with (
            patch("builtins.open", mock_open(read_data=transcript_content)),
            patch(
                "pacemaker.telemetry.jsonl_parser.get_user_email",
                return_value="from-api@example.com",
            ) as mock_api,
        ):

            email = extract_user_id("/fake/path.jsonl")

            assert email == "from-api@example.com"
            mock_api.assert_called_once()

    def test_extract_user_id_returns_none_when_both_fail(self):
        """Should return None when transcript has no email AND API fails"""
        transcript_data = [
            {"type": "session_start", "session_id": "abc123"},
        ]
        transcript_content = "\n".join(json.dumps(entry) for entry in transcript_data)

        with (
            patch("builtins.open", mock_open(read_data=transcript_content)),
            patch("pacemaker.telemetry.jsonl_parser.get_user_email", return_value=None),
        ):

            email = extract_user_id("/fake/path.jsonl")

            assert email is None

    def test_extract_user_id_handles_empty_transcript(self):
        """Should fall back to API when transcript is empty"""
        with (
            patch("builtins.open", mock_open(read_data="")),
            patch(
                "pacemaker.telemetry.jsonl_parser.get_user_email",
                return_value="fallback@example.com",
            ),
        ):

            email = extract_user_id("/fake/path.jsonl")

            assert email == "fallback@example.com"

    def test_extract_user_id_handles_file_not_found(self):
        """Should try API when transcript file doesn't exist"""
        with (
            patch("builtins.open", side_effect=FileNotFoundError()),
            patch(
                "pacemaker.telemetry.jsonl_parser.get_user_email",
                return_value="api-email@example.com",
            ),
        ):

            email = extract_user_id("/nonexistent/path.jsonl")

            assert email == "api-email@example.com"

    def test_extract_user_id_prefers_transcript_over_api(self):
        """Should prefer transcript email even when API is available"""
        transcript_data = [
            {"type": "auth_profile", "profile": {"email": "transcript@example.com"}},
        ]
        transcript_content = "\n".join(json.dumps(entry) for entry in transcript_data)

        with (
            patch("builtins.open", mock_open(read_data=transcript_content)),
            patch(
                "pacemaker.telemetry.jsonl_parser.get_user_email",
                return_value="api@example.com",
            ) as mock_api,
        ):

            email = extract_user_id("/fake/path.jsonl")

            # Should get transcript email without calling API
            assert email == "transcript@example.com"
            mock_api.assert_not_called()
