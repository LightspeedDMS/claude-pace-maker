#!/usr/bin/env python3
"""
Unit tests for refactored intent validator using transcript-based context.

Tests the new approach where context is extracted directly from transcript
instead of relying on stored prompts.
"""

import unittest
import tempfile
import os
import json
from unittest.mock import patch


class TestRefactoredIntentValidator(unittest.TestCase):
    """Test refactored intent validator with transcript-based context."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.transcript_path = os.path.join(self.temp_dir, "transcript.jsonl")

    def tearDown(self):
        """Clean up."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def create_transcript(self, messages):
        """Create a JSONL transcript file."""
        with open(self.transcript_path, "w") as f:
            for msg in messages:
                f.write(json.dumps(msg) + "\n")

    def test_build_validation_prompt_with_transcript_context(self):
        """Should build prompt with ALL user messages and last N assistant messages."""
        from src.pacemaker.intent_validator import build_validation_prompt

        all_user_messages = [
            "First user message",
            "Second user message",
            "Recent message 1",
            "Recent message 2",
        ]
        last_assistant_messages = ["Assistant response 1", "Assistant response 2"]
        last_assistant = "This is what Claude just said"

        # Function should only accept 3 parameters (no liveliness_detected)
        prompt = build_validation_prompt(
            all_user_messages, last_assistant_messages, last_assistant
        )

        # Should contain all user messages
        self.assertIn("First user message", prompt)
        self.assertIn("Second user message", prompt)
        self.assertIn("Recent message 1", prompt)
        self.assertIn("Recent message 2", prompt)

        # Should contain assistant messages
        self.assertIn("Assistant response 1", prompt)
        self.assertIn("Assistant response 2", prompt)

        # Should contain last assistant message
        self.assertIn("This is what Claude just said", prompt)

        # Should explain what the section represents
        self.assertIn("COMPLETE", prompt.upper())
        self.assertIn("REQUEST", prompt.upper())

    def test_build_validation_prompt_handles_empty_lists(self):
        """Should handle empty message lists gracefully."""
        from src.pacemaker.intent_validator import build_validation_prompt

        prompt = build_validation_prompt([], [], "")

        # Should still produce valid prompt
        self.assertIsInstance(prompt, str)
        self.assertGreater(len(prompt), 0)

    def test_build_validation_prompt_maintains_response_format(self):
        """Should maintain APPROVED/BLOCKED response format."""
        from src.pacemaker.intent_validator import build_validation_prompt

        prompt = build_validation_prompt(
            ["test message"], ["test assistant"], "test response"
        )

        # Should contain response format instructions
        self.assertIn("APPROVED", prompt)
        self.assertIn("BLOCKED:", prompt)

    @patch("src.pacemaker.intent_validator.call_sdk_validation")
    @patch("src.pacemaker.intent_validator.get_all_user_messages")
    @patch("src.pacemaker.intent_validator.get_last_n_assistant_messages")
    def test_validate_intent_extracts_context_from_transcript(
        self,
        mock_last_assistant_msgs,
        mock_all_user,
        mock_sdk,
    ):
        """Should extract context directly from transcript, not from stored prompts."""
        from src.pacemaker.intent_validator import validate_intent

        # Setup mocks
        mock_all_user.return_value = [
            "First user msg",
            "Second user msg",
            "Last user msg",
        ]
        mock_last_assistant_msgs.return_value = [
            "Assistant msg 1",
            "Last assistant msg",
        ]
        mock_sdk.return_value = "APPROVED"

        # Create transcript
        self.create_transcript([])

        result = validate_intent(
            session_id="test-session",
            transcript_path=self.transcript_path,
            conversation_context_size=5,
        )

        # Verify extraction functions were called
        mock_all_user.assert_called_once_with(self.transcript_path)
        mock_last_assistant_msgs.assert_called_once_with(self.transcript_path, n=5)

        # Verify SDK was called with extracted context
        self.assertTrue(mock_sdk.called)
        call_args = mock_sdk.call_args.kwargs
        self.assertEqual(
            call_args["all_user_messages"],
            ["First user msg", "Second user msg", "Last user msg"],
        )
        self.assertEqual(
            call_args["last_assistant_messages"],
            ["Assistant msg 1", "Last assistant msg"],
        )
        self.assertEqual(call_args["last_assistant"], "Last assistant msg")

        # Should return parsed result
        self.assertEqual(result, {"continue": True})

    @patch("src.pacemaker.intent_validator.call_sdk_validation")
    @patch("src.pacemaker.intent_validator.get_all_user_messages")
    @patch("src.pacemaker.intent_validator.get_last_n_assistant_messages")
    def test_validate_intent_uses_config_context_size(
        self,
        mock_last_assistant_msgs,
        mock_all_user,
        mock_sdk,
    ):
        """Should use conversation_context_size from config for assistant messages only."""
        from src.pacemaker.intent_validator import validate_intent

        # Setup mocks
        mock_all_user.return_value = ["msg1", "msg2"]
        mock_last_assistant_msgs.return_value = ["response"]
        mock_sdk.return_value = "APPROVED"

        # Create transcript
        self.create_transcript([])

        # Call with custom context size
        validate_intent(
            session_id="test",
            transcript_path=self.transcript_path,
            conversation_context_size=10,
        )

        # Verify extraction functions - all_user_messages doesn't take n parameter
        mock_all_user.assert_called_once_with(self.transcript_path)
        # Only assistant messages use context size
        mock_last_assistant_msgs.assert_called_once_with(self.transcript_path, n=10)

    def test_validate_intent_fails_open_on_missing_transcript(self):
        """Should fail open if transcript doesn't exist."""
        from src.pacemaker.intent_validator import validate_intent

        nonexistent_path = os.path.join(self.temp_dir, "nonexistent.jsonl")

        result = validate_intent(
            session_id="test",
            transcript_path=nonexistent_path,
            conversation_context_size=5,
        )

        # Should fail open
        self.assertEqual(result, {"continue": True})

    @patch("src.pacemaker.intent_validator.call_sdk_validation")
    @patch("src.pacemaker.intent_validator.get_all_user_messages")
    @patch("src.pacemaker.intent_validator.get_last_n_assistant_messages")
    def test_validate_intent_fails_open_on_empty_context(
        self,
        mock_last_assistant_msgs,
        mock_all_user,
        mock_sdk,
    ):
        """Should fail open if all context is empty."""
        from src.pacemaker.intent_validator import validate_intent

        # Setup mocks to return empty context
        mock_all_user.return_value = []
        mock_last_assistant_msgs.return_value = []

        # Create empty transcript
        self.create_transcript([])

        result = validate_intent(
            session_id="test",
            transcript_path=self.transcript_path,
            conversation_context_size=5,
        )

        # Should fail open without calling SDK
        self.assertFalse(mock_sdk.called)
        self.assertEqual(result, {"continue": True})

    @patch("src.pacemaker.intent_validator.call_sdk_validation")
    @patch("src.pacemaker.intent_validator.get_all_user_messages")
    @patch("src.pacemaker.intent_validator.get_last_n_assistant_messages")
    def test_validate_intent_handles_sdk_error(
        self,
        mock_last_assistant_msgs,
        mock_all_user,
        mock_sdk,
    ):
        """Should fail open on SDK error."""
        from src.pacemaker.intent_validator import validate_intent

        # Setup mocks
        mock_all_user.return_value = ["msg"]
        mock_last_assistant_msgs.return_value = ["response"]
        mock_sdk.side_effect = Exception("SDK error")

        # Create transcript
        self.create_transcript([])

        result = validate_intent(
            session_id="test",
            transcript_path=self.transcript_path,
            conversation_context_size=5,
        )

        # Should fail open
        self.assertEqual(result, {"continue": True})


class TestValidationPromptTemplate(unittest.TestCase):
    """Test updated validation prompt template."""

    def test_template_explains_context_sections(self):
        """Template should clearly explain what each section represents."""
        from src.pacemaker.intent_validator import get_prompt_template

        template = get_prompt_template()

        # Should explain complete user requests
        self.assertIn("COMPLETE", template.upper())
        self.assertIn("REQUEST", template.upper())

        # Should NOT have separate original/recent sections anymore
        self.assertNotIn("YOUR ORIGINAL REQUEST", template)
        self.assertNotIn("YOUR RECENT CONTEXT", template)

        # Should mention last assistant message
        self.assertIn("CLAUDE", template.upper())

    def test_template_contains_tempo_liveliness_instructions(self):
        """Template should contain tempo liveliness check instructions for SDK."""
        from src.pacemaker.intent_validator import get_prompt_template

        template = get_prompt_template()

        # Should contain tempo liveliness detection section
        self.assertIn("TEMPO", template.upper())
        self.assertIn("LIVELINESS", template.upper())

        # Should contain example phrases
        self.assertIn("tempo, are you alive", template.lower())
        self.assertIn("tempo status", template.lower())

        # Should instruct SDK to detect and respond to liveliness checks
        self.assertIn("SYSTEM CHECK", template.upper())
        self.assertIn("BLOCKED:", template)

        # Should NOT have a {liveliness_check_detected} placeholder
        self.assertNotIn("{liveliness_check_detected}", template)

    def test_template_maintains_response_format(self):
        """Template should maintain APPROVED/BLOCKED format."""
        from src.pacemaker.intent_validator import get_prompt_template

        template = get_prompt_template()

        self.assertIn("APPROVED", template)
        self.assertIn("BLOCKED:", template)


class TestParseSDKResponse(unittest.TestCase):
    """Test SDK response parsing (should remain unchanged)."""

    def test_parse_approved_response(self):
        """Should parse APPROVED correctly."""
        from src.pacemaker.intent_validator import parse_sdk_response

        result = parse_sdk_response("APPROVED")
        self.assertEqual(result, {"continue": True})

    def test_parse_blocked_response(self):
        """Should parse BLOCKED with feedback."""
        from src.pacemaker.intent_validator import parse_sdk_response

        result = parse_sdk_response("BLOCKED: Task incomplete")
        self.assertEqual(result, {"decision": "block", "reason": "Task incomplete"})

    def test_parse_unexpected_response_fails_open(self):
        """Should fail open on unexpected response."""
        from src.pacemaker.intent_validator import parse_sdk_response

        result = parse_sdk_response("UNEXPECTED FORMAT")
        self.assertEqual(result, {"continue": True})


if __name__ == "__main__":
    unittest.main()
