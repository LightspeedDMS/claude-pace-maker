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
        """Should fail open (return True) when SDK call fails.

        Infrastructure failures (API down, auth error, rate limit) should not
        block writes. The function fails open to avoid blocking due to
        infrastructure issues outside the developer's control.
        """
        mock_sdk.side_effect = Exception("SDK error")

        result = validate_intent_declared(
            messages=["test"], file_path="/test.py", tool_name="Write"
        )

        # Should fail open (return True = allow write despite infrastructure failure)
        assert result["intent_found"] is True

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


class TestStage1ReviewerTag:
    """Tests that Stage 1 regex blocks include reviewer='RegEx'."""

    @patch("pacemaker.excluded_paths.load_exclusions", return_value=[])
    def test_stage1_no_returns_regex_reviewer(self, _mock_excl):
        """Stage 1 NO (missing INTENT: marker) must return reviewer='RegEx'."""
        from pacemaker.intent_validator import validate_intent_and_code

        result = validate_intent_and_code(
            messages=["Just some text without intent marker"],
            code="x = 1",
            file_path="/home/user/project/src/mymodule.py",
            tool_name="Write",
        )

        assert result["approved"] is False
        assert result.get("reviewer") == "RegEx"

    @patch("pacemaker.excluded_paths.load_exclusions", return_value=[])
    def test_stage1_no_tdd_returns_regex_reviewer(self, _mock_excl):
        """Stage 1 NO_TDD (core path, no TDD declaration) must return reviewer='RegEx'."""
        from pacemaker.intent_validator import validate_intent_and_code

        # Message has INTENT: marker and mentions file, but no test declaration
        result = validate_intent_and_code(
            messages=[
                "INTENT: Modify src/mymodule.py to add a helper function "
                "that does X, for performance."
            ],
            code="def helper(): pass",
            file_path="/home/user/project/src/mymodule.py",
            tool_name="Write",
        )

        assert result["approved"] is False
        assert result.get("tdd_failure") is True
        assert result.get("reviewer") == "RegEx"
