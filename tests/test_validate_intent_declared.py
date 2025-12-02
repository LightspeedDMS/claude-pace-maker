#!/usr/bin/env python3
"""
Unit tests for validate_intent_declared function in intent_validator module.
"""

from unittest.mock import patch
from pacemaker.intent_validator import validate_intent_declared


class TestValidateIntentDeclared:
    """Test validate_intent_declared() function."""

    @patch("pacemaker.intent_validator._call_sdk_intent_validation")
    def test_returns_dict_with_intent_found_key(self, mock_sdk):
        """Should return dict with 'intent_found' boolean key."""
        mock_sdk.return_value = "YES"

        result = validate_intent_declared(
            messages=["I will modify test.py"],
            file_path="/path/to/test.py",
            tool_name="Write",
        )

        assert isinstance(result, dict)
        assert "intent_found" in result
        assert isinstance(result["intent_found"], bool)

    @patch("pacemaker.intent_validator._call_sdk_intent_validation")
    def test_returns_true_when_sdk_says_yes(self, mock_sdk):
        """Should return intent_found=True when SDK confirms intent."""
        mock_sdk.return_value = "YES"

        result = validate_intent_declared(
            messages=["I will modify src/test.py to add logging"],
            file_path="/path/to/test.py",
            tool_name="Write",
        )

        assert result["intent_found"] is True

    @patch("pacemaker.intent_validator._call_sdk_intent_validation")
    def test_returns_false_when_sdk_says_no(self, mock_sdk):
        """Should return intent_found=False when SDK says no intent."""
        mock_sdk.return_value = "NO"

        result = validate_intent_declared(
            messages=["Some other text"],
            file_path="/path/to/test.py",
            tool_name="Write",
        )

        assert result["intent_found"] is False

    @patch("pacemaker.intent_validator._call_sdk_intent_validation")
    def test_includes_filename_in_sdk_prompt(self, mock_sdk):
        """Should include target filename in SDK validation prompt."""
        mock_sdk.return_value = "YES"

        validate_intent_declared(
            messages=["I will modify config.py"],
            file_path="/home/user/project/config.py",
            tool_name="Write",
        )

        # Check SDK was called with prompt mentioning the file
        call_args = mock_sdk.call_args[0][0]
        assert "config.py" in call_args

    @patch("pacemaker.intent_validator._call_sdk_intent_validation")
    def test_handles_sdk_exception_gracefully(self, mock_sdk):
        """Should return False when SDK call fails."""
        mock_sdk.side_effect = Exception("SDK error")

        result = validate_intent_declared(
            messages=["test"], file_path="/test.py", tool_name="Write"
        )

        # Should fail open (return False = no intent found)
        assert result["intent_found"] is False

    @patch("pacemaker.intent_validator._call_sdk_intent_validation")
    def test_case_insensitive_yes_no_parsing(self, mock_sdk):
        """Should handle YES/yes/Yes and NO/no/No."""
        test_cases = [
            ("YES", True),
            ("yes", True),
            ("Yes", True),
            ("NO", False),
            ("no", False),
            ("No", False),
        ]

        for sdk_response, expected_result in test_cases:
            mock_sdk.return_value = sdk_response

            result = validate_intent_declared(
                messages=["test"], file_path="/test.py", tool_name="Write"
            )

            assert result["intent_found"] is expected_result

    @patch("pacemaker.intent_validator._call_sdk_intent_validation")
    def test_handles_ambiguous_sdk_response(self, mock_sdk):
        """Should return False for ambiguous/unexpected SDK responses."""
        mock_sdk.return_value = "MAYBE"

        result = validate_intent_declared(
            messages=["test"], file_path="/test.py", tool_name="Write"
        )

        assert result["intent_found"] is False
