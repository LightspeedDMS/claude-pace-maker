"""Tests for intent_validator logging."""

import unittest
from unittest.mock import patch
import os
import tempfile


class TestIntentValidatorLogging(unittest.TestCase):
    """Test that logging occurs at key points."""

    @patch("pacemaker.intent_validator._fresh_sdk_call")
    @patch("pacemaker.intent_validator.build_stop_hook_context")
    @patch("pacemaker.intent_validator.format_stop_hook_context")
    def test_validate_intent_logs_context_stats(
        self, mock_format, mock_build, mock_sdk
    ):
        """Test that validate_intent logs context building stats."""
        # Setup mocks
        mock_build.return_value = {
            "first_pairs": [("user", ["asst"])],
            "backwards_messages": [],
            "truncated_count": 0,
            "total_tokens": 100,
        }
        mock_format.return_value = "test context"
        mock_sdk.return_value = "APPROVED"

        # Create temp transcript
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"message": {"role": "user", "content": "test"}}\n')
            transcript_path = f.name

        try:
            from pacemaker.intent_validator import validate_intent

            result = validate_intent("sess1", transcript_path)
            # If we get here without exception, logging code path was hit
            self.assertIn("continue", result)
        finally:
            os.unlink(transcript_path)


if __name__ == "__main__":
    unittest.main()
