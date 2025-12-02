#!/usr/bin/env python3
"""
Unit tests for code_reviewer module.
"""

import tempfile
import os
from unittest.mock import patch, MagicMock
from pacemaker import code_reviewer


class TestBuildReviewPrompt:
    """Test build_review_prompt() function."""

    def test_includes_intent_in_prompt(self):
        """Should include declared intent in review prompt."""
        intent = "Add JWT validation to auth module"
        code = "def validate_jwt(): pass"

        prompt = code_reviewer.build_review_prompt(intent, code)

        assert intent in prompt

    def test_includes_code_in_prompt(self):
        """Should include code content in review prompt."""
        intent = "Add logging"
        code = "import logging\nlogger = logging.getLogger(__name__)"

        prompt = code_reviewer.build_review_prompt(intent, code)

        assert code in prompt

    def test_instructs_reviewer_to_validate_match(self):
        """Should instruct SDK to validate code matches intent."""
        intent = "Fix bug"
        code = "fixed = True"

        prompt = code_reviewer.build_review_prompt(intent, code)

        # Check for key instructions
        assert "reviewer" in prompt.lower() or "validate" in prompt.lower()
        assert "intent" in prompt.lower()


class TestCallSdkReview:
    """Test call_sdk_review() function."""

    @patch("pacemaker.code_reviewer._call_sdk_review_async")
    def test_calls_sdk_with_prompt(self, mock_async):
        """Should call SDK with provided prompt."""
        mock_async.return_value = "Looks good"
        prompt = "Review this code"

        code_reviewer.call_sdk_review(prompt)

        # Verify async function was called with prompt
        assert mock_async.called

    @patch("pacemaker.code_reviewer._call_sdk_review_async")
    def test_returns_sdk_response(self, mock_async):
        """Should return SDK response text."""
        expected = "Code matches intent"
        mock_async.return_value = expected

        result = code_reviewer.call_sdk_review("test prompt")

        assert result == expected


class TestValidateCodeAgainstIntent:
    """Test validate_code_against_intent() function."""

    def test_reads_file_from_disk(self):
        """Should read modified file content from disk."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".py") as f:
            f.write("print('test')")
            temp_path = f.name

        try:
            messages = ["I will add a print statement to test.py"]

            # Mock SDK to avoid real call
            with patch("pacemaker.code_reviewer.call_sdk_review") as mock_sdk:
                mock_sdk.return_value = ""
                code_reviewer.validate_code_against_intent(temp_path, messages)

                # Verify SDK was called with file content
                call_args = mock_sdk.call_args[0][0]
                assert "print('test')" in call_args
        finally:
            os.unlink(temp_path)

    def test_extracts_intent_from_messages(self):
        """Should extract intent from assistant messages."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".py") as f:
            f.write("code = True")
            temp_path = f.name

        try:
            messages = [
                "I will modify test.py to add a flag",
                "Setting code = True",
            ]

            with patch("pacemaker.code_reviewer.call_sdk_review") as mock_sdk:
                mock_sdk.return_value = ""
                code_reviewer.validate_code_against_intent(temp_path, messages)

                # Check that messages were included in prompt
                call_args = mock_sdk.call_args[0][0]
                assert "test.py" in call_args or "flag" in call_args
        finally:
            os.unlink(temp_path)

    def test_returns_empty_string_when_code_matches_intent(self):
        """Should return empty string when SDK approves code."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".py") as f:
            f.write("valid_code = True")
            temp_path = f.name

        try:
            messages = ["I will add valid_code flag"]

            with patch("pacemaker.code_reviewer.call_sdk_review") as mock_sdk:
                mock_sdk.return_value = ""
                result = code_reviewer.validate_code_against_intent(temp_path, messages)

                assert result == ""
        finally:
            os.unlink(temp_path)

    def test_returns_feedback_when_code_has_issues(self):
        """Should return SDK feedback when code doesn't match intent."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".py") as f:
            f.write("wrong_code = True")
            temp_path = f.name

        try:
            messages = ["I will add correct_code flag"]

            with patch("pacemaker.code_reviewer.call_sdk_review") as mock_sdk:
                feedback = "Code doesn't match intent: variable name mismatch"
                mock_sdk.return_value = feedback

                result = code_reviewer.validate_code_against_intent(temp_path, messages)

                assert result == feedback
        finally:
            os.unlink(temp_path)

    def test_handles_file_not_found_gracefully(self):
        """Should return empty string if file doesn't exist."""
        messages = ["I will modify nonexistent.py"]

        result = code_reviewer.validate_code_against_intent(
            "/nonexistent/path/file.py", messages
        )

        # Fail open: no feedback if we can't read file
        assert result == ""

    def test_handles_sdk_exception_gracefully(self):
        """Should return empty string if SDK call fails."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".py") as f:
            f.write("code = True")
            temp_path = f.name

        try:
            messages = ["I will modify file"]

            with patch("pacemaker.code_reviewer.call_sdk_review") as mock_sdk:
                mock_sdk.side_effect = Exception("SDK error")

                result = code_reviewer.validate_code_against_intent(temp_path, messages)

                # Fail open: no feedback on error
                assert result == ""
        finally:
            os.unlink(temp_path)


class TestSdkIntegration:
    """Test SDK integration patterns."""

    @patch("pacemaker.code_reviewer.asyncio")
    def test_uses_event_loop_for_async_call(self, mock_asyncio):
        """Should use asyncio event loop for SDK calls."""
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = False
        mock_loop.run_until_complete.return_value = "response"
        mock_asyncio.get_event_loop.return_value = mock_loop

        code_reviewer.call_sdk_review("test")

        # Verify event loop was used
        assert mock_loop.run_until_complete.called
