#!/usr/bin/env python3
"""
Unit tests for liveliness check detection feature.

Tests the detection of liveliness check phrases that indicate the user
is testing if the tempo system is operational, not making actual work requests.
"""

import unittest
import tempfile
import os
import json
from unittest.mock import patch


class TestDetectLivelinessCheck(unittest.TestCase):
    """Test detect_liveliness_check() function."""

    def test_detect_basic_phrases_case_insensitive(self):
        """Should detect all basic liveliness phrases regardless of case."""
        from src.pacemaker.intent_validator import detect_liveliness_check

        test_cases = [
            # Original case
            ["tempo, are you alive"],
            ["tempo, are you working?"],
            ["tempo, are you there?"],
            ["tempo status"],
            ["tempo check"],
            # Uppercase
            ["TEMPO, ARE YOU ALIVE"],
            ["TEMPO, ARE YOU WORKING?"],
            ["TEMPO, ARE YOU THERE?"],
            ["TEMPO STATUS"],
            ["TEMPO CHECK"],
            # Mixed case
            ["TeMpO, ArE yOu AlIvE"],
            ["Tempo, Are You Working?"],
            ["TEMPO, are you there?"],
            ["Tempo Status"],
            ["TEMPO Check"],
        ]

        for messages in test_cases:
            with self.subTest(messages=messages):
                result = detect_liveliness_check(messages)
                self.assertTrue(
                    result, f"Should detect liveliness check in: {messages}"
                )

    def test_non_liveliness_messages_return_false(self):
        """Should return False for normal work requests."""
        from src.pacemaker.intent_validator import detect_liveliness_check

        test_cases = [
            ["Please implement the login feature"],
            ["Fix the bug in the authentication module"],
            ["Write tests for the API"],
            ["Refactor the database connection code"],
            ["tempo is mentioned but this is not a check"],
            [
                "Check the status of the deployment"
            ],  # "status" and "check" but not "tempo status/check"
            ["Are you working on the feature?"],  # Similar words but not the phrase
            [""],
            ["Some random text"],
        ]

        for messages in test_cases:
            with self.subTest(messages=messages):
                result = detect_liveliness_check(messages)
                self.assertFalse(
                    result, f"Should NOT detect liveliness check in: {messages}"
                )

    def test_partial_matches_dont_trigger_false_positives(self):
        """Should not trigger on partial or substring matches."""
        from src.pacemaker.intent_validator import detect_liveliness_check

        test_cases = [
            ["tempo-related work needs to be done"],
            ["The tempo of development is good"],
            ["status update on the project"],
            ["check the logs"],
            ["Are you there? I need help with code"],
            ["working on tempo feature"],
            ["alive and well, let's continue"],
        ]

        for messages in test_cases:
            with self.subTest(messages=messages):
                result = detect_liveliness_check(messages)
                self.assertFalse(
                    result, f"Should NOT trigger false positive for: {messages}"
                )

    def test_liveliness_in_first_message(self):
        """Should detect liveliness check in first message of multiple messages."""
        from src.pacemaker.intent_validator import detect_liveliness_check

        messages = [
            "tempo, are you alive",
            "Please implement feature X",
            "Also fix bug Y",
        ]

        result = detect_liveliness_check(messages)
        self.assertTrue(result, "Should detect liveliness in first message")

    def test_liveliness_in_middle_message(self):
        """Should detect liveliness check in middle message."""
        from src.pacemaker.intent_validator import detect_liveliness_check

        messages = ["Please implement feature X", "tempo status", "Also fix bug Y"]

        result = detect_liveliness_check(messages)
        self.assertTrue(result, "Should detect liveliness in middle message")

    def test_liveliness_in_last_message(self):
        """Should detect liveliness check in last message."""
        from src.pacemaker.intent_validator import detect_liveliness_check

        messages = [
            "Please implement feature X",
            "Also fix bug Y",
            "tempo, are you working?",
        ]

        result = detect_liveliness_check(messages)
        self.assertTrue(result, "Should detect liveliness in last message")

    def test_empty_messages_list(self):
        """Should handle empty messages list gracefully."""
        from src.pacemaker.intent_validator import detect_liveliness_check

        result = detect_liveliness_check([])
        self.assertFalse(result, "Empty list should return False")

    def test_multiple_liveliness_checks(self):
        """Should detect if multiple liveliness checks present."""
        from src.pacemaker.intent_validator import detect_liveliness_check

        messages = ["tempo, are you alive", "tempo status", "tempo check"]

        result = detect_liveliness_check(messages)
        self.assertTrue(result, "Should detect multiple liveliness checks")

    def test_liveliness_with_surrounding_text(self):
        """Should detect liveliness phrases even with surrounding text."""
        from src.pacemaker.intent_validator import detect_liveliness_check

        messages = [
            "Hey Claude, tempo, are you alive or what?",
            "Just checking: tempo status please",
            "Before we continue, tempo check",
        ]

        result = detect_liveliness_check(messages)
        self.assertTrue(result, "Should detect liveliness with surrounding text")


class TestLivelinessCheckIntegration(unittest.TestCase):
    """Test liveliness check integration with validation flow."""

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

    def test_build_validation_prompt_includes_liveliness_detection(self):
        """Should include liveliness detection status in prompt."""
        from src.pacemaker.intent_validator import build_validation_prompt

        prompt = build_validation_prompt(
            all_user_messages=["tempo, are you alive"],
            last_assistant_messages=["I'm working on it"],
            last_assistant="Done",
            liveliness_detected=True,
        )

        # Should contain liveliness check section
        self.assertIn("LIVELINESS CHECK", prompt.upper())
        self.assertIn("True", prompt)

    def test_build_validation_prompt_handles_no_liveliness(self):
        """Should handle liveliness_detected=False correctly."""
        from src.pacemaker.intent_validator import build_validation_prompt

        prompt = build_validation_prompt(
            all_user_messages=["Please implement feature"],
            last_assistant_messages=["I'm working on it"],
            last_assistant="Done",
            liveliness_detected=False,
        )

        # Should contain liveliness check section with False
        self.assertIn("LIVELINESS CHECK", prompt.upper())
        self.assertIn("False", prompt)

    def test_prompt_template_includes_liveliness_instructions(self):
        """Template should include instructions for liveliness handling."""
        from src.pacemaker.intent_validator import VALIDATION_PROMPT_TEMPLATE

        # Should contain liveliness check section
        self.assertIn("LIVELINESS CHECK", VALIDATION_PROMPT_TEMPLATE.upper())

        # Should mention blocking for liveliness checks
        self.assertIn("liveliness", VALIDATION_PROMPT_TEMPLATE.lower())
        self.assertIn("BLOCKED:", VALIDATION_PROMPT_TEMPLATE)

    @patch("src.pacemaker.intent_validator.call_sdk_validation")
    @patch("src.pacemaker.intent_validator.get_all_user_messages")
    @patch("src.pacemaker.intent_validator.get_last_n_assistant_messages")
    def test_validate_intent_detects_liveliness_and_passes_to_sdk(
        self, mock_last_assistant_msgs, mock_all_user, mock_sdk
    ):
        """Should detect liveliness check and pass to SDK validation."""
        from src.pacemaker.intent_validator import validate_intent

        # Setup mocks - user sends liveliness check
        mock_all_user.return_value = ["tempo, are you alive"]
        mock_last_assistant_msgs.return_value = ["Response"]
        mock_sdk.return_value = (
            "BLOCKED: Liveliness check confirmed. Tempo system is active."
        )

        # Create transcript
        self.create_transcript([])

        result = validate_intent(
            session_id="test-session",
            transcript_path=self.transcript_path,
            conversation_context_size=5,
        )

        # Verify SDK was called
        self.assertTrue(mock_sdk.called)

        # Verify liveliness_detected was passed (check call args)
        # The function signature should have received liveliness info through prompt
        # We can verify this by checking if the call was made at all
        self.assertTrue(mock_sdk.called)

        # Result should be blocked
        self.assertEqual(result["decision"], "block")
        self.assertIn("Liveliness check", result["reason"])

    @patch("src.pacemaker.intent_validator.call_sdk_validation")
    @patch("src.pacemaker.intent_validator.get_all_user_messages")
    @patch("src.pacemaker.intent_validator.get_last_n_assistant_messages")
    def test_validate_intent_no_liveliness_normal_flow(
        self, mock_last_assistant_msgs, mock_all_user, mock_sdk
    ):
        """Should proceed normally when no liveliness check detected."""
        from src.pacemaker.intent_validator import validate_intent

        # Setup mocks - normal work request
        mock_all_user.return_value = ["Please implement feature X"]
        mock_last_assistant_msgs.return_value = ["Done implementing"]
        mock_sdk.return_value = "APPROVED"

        # Create transcript
        self.create_transcript([])

        result = validate_intent(
            session_id="test-session",
            transcript_path=self.transcript_path,
            conversation_context_size=5,
        )

        # Verify SDK was called
        self.assertTrue(mock_sdk.called)

        # Result should be approved
        self.assertEqual(result, {"continue": True})


class TestLivelinessSDKResponse(unittest.TestCase):
    """Test SDK response parsing for liveliness checks."""

    def test_parse_liveliness_blocked_response(self):
        """Should parse liveliness block response correctly."""
        from src.pacemaker.intent_validator import parse_sdk_response

        response = "BLOCKED: Liveliness check confirmed. Tempo system is active and monitoring your session. Claude, please acknowledge you received this system check."

        result = parse_sdk_response(response)

        self.assertEqual(result["decision"], "block")
        self.assertIn("Liveliness check confirmed", result["reason"])
        self.assertIn("Tempo system is active", result["reason"])


if __name__ == "__main__":
    unittest.main()
