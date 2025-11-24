#!/usr/bin/env python3
"""
Unit tests for Stop hook - Intent-based validation (Story #9).

Tests AC2, AC6, AC7:
- Incomplete work blocked with feedback
- SDK error fails open
- Transcript parsing extracts last N messages
"""

import unittest
import tempfile
import os
import json
from unittest.mock import patch


class TestStopHookTranscriptParsing(unittest.TestCase):
    """Test AC7: Transcript parsing extracts last N messages."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.transcript_path = os.path.join(self.temp_dir, "transcript.jsonl")

    def tearDown(self):
        """Clean up."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def create_transcript(self, num_messages=50):
        """Create a mock transcript with N messages."""
        with open(self.transcript_path, "w") as f:
            for i in range(num_messages):
                role = "user" if i % 2 == 0 else "assistant"
                message = {
                    "message": {
                        "role": role,
                        "content": [
                            {"type": "text", "text": f"Message {i} from {role}"}
                        ],
                    }
                }
                f.write(json.dumps(message) + "\n")

    def test_extract_last_n_messages(self):
        """Should extract last N messages from long transcript."""
        from src.pacemaker.intent_validator import extract_last_n_messages

        self.create_transcript(num_messages=50)

        messages = extract_last_n_messages(self.transcript_path, n=10)

        # Should return exactly 10 messages
        self.assertEqual(len(messages), 10)

        # Should be the LAST 10 messages (40-49)
        self.assertIn("Message 49", messages[-1])
        self.assertIn("Message 40", messages[0])

    def test_extract_messages_formats_with_role_prefix(self):
        """Should format messages with [USER] and [ASSISTANT] prefix."""
        from src.pacemaker.intent_validator import extract_last_n_messages

        self.create_transcript(num_messages=10)

        messages = extract_last_n_messages(self.transcript_path, n=5)

        # Check formatting
        for msg in messages:
            self.assertTrue(
                msg.startswith("[USER]\n") or msg.startswith("[ASSISTANT]\n"),
                f"Message not properly formatted: {msg[:50]}",
            )

    def test_extract_messages_handles_short_transcript(self):
        """Should return all messages if transcript has fewer than N."""
        from src.pacemaker.intent_validator import extract_last_n_messages

        self.create_transcript(num_messages=5)

        messages = extract_last_n_messages(self.transcript_path, n=10)

        # Should return all 5 messages
        self.assertEqual(len(messages), 5)

    def test_extract_messages_handles_missing_file(self):
        """Should return empty list for missing transcript."""
        from src.pacemaker.intent_validator import extract_last_n_messages

        messages = extract_last_n_messages("/nonexistent/transcript.jsonl", n=10)

        self.assertEqual(messages, [])

    def test_extract_messages_handles_complex_content(self):
        """Should handle messages with multiple text blocks."""
        from src.pacemaker.intent_validator import extract_last_n_messages

        # Create transcript with complex content
        with open(self.transcript_path, "w") as f:
            message = {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Part 1"},
                        {"type": "text", "text": "Part 2"},
                        {"type": "image", "source": "ignored"},  # Non-text block
                        {"type": "text", "text": "Part 3"},
                    ],
                }
            }
            f.write(json.dumps(message) + "\n")

        messages = extract_last_n_messages(self.transcript_path, n=5)

        # Should extract all text parts
        self.assertEqual(len(messages), 1)
        self.assertIn("Part 1", messages[0])
        self.assertIn("Part 2", messages[0])
        self.assertIn("Part 3", messages[0])


class TestStopHookSDKResponseParsing(unittest.TestCase):
    """Test SDK response parsing logic."""

    def test_parse_approved_response(self):
        """Should parse APPROVED response correctly."""
        from src.pacemaker.intent_validator import parse_sdk_response

        response = "APPROVED"

        result = parse_sdk_response(response)

        self.assertEqual(result, {"continue": True})

    def test_parse_approved_with_whitespace(self):
        """Should handle APPROVED with extra whitespace."""
        from src.pacemaker.intent_validator import parse_sdk_response

        response = "  APPROVED  \n"

        result = parse_sdk_response(response)

        self.assertEqual(result, {"continue": True})

    def test_parse_blocked_response(self):
        """AC2: Should parse BLOCKED response with feedback."""
        from src.pacemaker.intent_validator import parse_sdk_response

        response = "BLOCKED: You only created placeholder functions. Implement the actual authentication logic."

        result = parse_sdk_response(response)

        self.assertEqual(result["decision"], "block")
        self.assertIn("placeholder functions", result["reason"])
        self.assertIn("authentication logic", result["reason"])

    def test_parse_blocked_multiline_feedback(self):
        """Should handle BLOCKED with multiline feedback."""
        from src.pacemaker.intent_validator import parse_sdk_response

        response = """BLOCKED: The following work is incomplete:
1. Authentication function is not implemented
2. Tests are missing
3. No error handling"""

        result = parse_sdk_response(response)

        self.assertEqual(result["decision"], "block")
        self.assertIn("incomplete", result["reason"])
        self.assertIn("error handling", result["reason"])

    def test_parse_unexpected_format_fails_open(self):
        """AC6: Unexpected format should fail open (allow exit)."""
        from src.pacemaker.intent_validator import parse_sdk_response

        response = "UNEXPECTED_FORMAT or malformed output"

        result = parse_sdk_response(response)

        # Should fail open
        self.assertEqual(result, {"continue": True})


class TestStopHookSDKIntegration(unittest.TestCase):
    """Test Stop hook SDK integration and validation."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.prompts_dir = os.path.join(self.temp_dir, "prompts")
        self.transcript_path = os.path.join(self.temp_dir, "transcript.jsonl")
        os.makedirs(self.prompts_dir, exist_ok=True)

    def tearDown(self):
        """Clean up."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def create_prompt_file(self, session_id, expanded_prompt):
        """Create a stored prompt file."""
        prompt_file = os.path.join(self.prompts_dir, f"{session_id}.json")
        data = {
            "session_id": session_id,
            "raw_prompt": "test prompt",
            "expanded_prompt": expanded_prompt,
            "timestamp": "2025-11-23T12:00:00",
        }
        with open(prompt_file, "w") as f:
            json.dump(data, f)

    def create_transcript(self):
        """Create a basic transcript."""
        with open(self.transcript_path, "w") as f:
            messages = [
                {
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "implement a calculator"}],
                    }
                },
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "I will implement the calculator"}
                        ],
                    }
                },
                {
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "great"}],
                    }
                },
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "Implementation complete"}
                        ],
                    }
                },
            ]
            for msg in messages:
                f.write(json.dumps(msg) + "\n")

    def test_read_stored_prompt(self):
        """Stop hook should read stored prompt from JSON file."""
        from src.pacemaker.intent_validator import read_stored_prompt

        session_id = "test-session-123"
        self.create_prompt_file(session_id, "implement authentication system")

        prompt_data = read_stored_prompt(session_id, self.prompts_dir)

        self.assertEqual(prompt_data["session_id"], session_id)
        self.assertEqual(
            prompt_data["expanded_prompt"], "implement authentication system"
        )

    def test_read_stored_prompt_missing_file(self):
        """Should return None for missing prompt file."""
        from src.pacemaker.intent_validator import read_stored_prompt

        prompt_data = read_stored_prompt("nonexistent", self.prompts_dir)

        self.assertIsNone(prompt_data)

    @patch("src.pacemaker.intent_validator.call_sdk_validation")
    def test_validate_intent_calls_sdk_with_correct_data(self, mock_sdk):
        """Should call SDK with user prompt and last 10 messages."""
        from src.pacemaker.intent_validator import validate_intent

        session_id = "test-session-456"
        self.create_prompt_file(session_id, "add authentication system")
        self.create_transcript()

        mock_sdk.return_value = "APPROVED"

        result = validate_intent(
            session_id=session_id,
            transcript_path=self.transcript_path,
            prompts_dir=self.prompts_dir,
        )

        # Verify SDK was called
        self.assertTrue(mock_sdk.called)

        # Get positional arguments
        call_args = mock_sdk.call_args[0]

        # Should include expanded prompt (first arg)
        self.assertIn("add authentication system", call_args[0])

        # Should include last messages (second arg)
        self.assertIsInstance(call_args[1], list)
        self.assertGreater(len(call_args[1]), 0)

        # Should return parsed result
        self.assertEqual(result, {"continue": True})

    @patch("src.pacemaker.intent_validator.call_sdk_validation")
    def test_validate_intent_sdk_error_fails_open(self, mock_sdk):
        """AC6: SDK error should fail open and allow exit."""
        from src.pacemaker.intent_validator import validate_intent

        session_id = "test-session-789"
        self.create_prompt_file(session_id, "test prompt")
        self.create_transcript()

        # Simulate SDK error
        mock_sdk.side_effect = Exception("SDK connection failed")

        result = validate_intent(
            session_id=session_id,
            transcript_path=self.transcript_path,
            prompts_dir=self.prompts_dir,
        )

        # Should fail open
        self.assertEqual(result, {"continue": True})

    @patch("src.pacemaker.intent_validator.call_sdk_validation")
    def test_validate_intent_incomplete_work_blocked(self, mock_sdk):
        """AC2: Incomplete work should be blocked with feedback."""
        from src.pacemaker.intent_validator import validate_intent

        session_id = "test-incomplete"
        self.create_prompt_file(session_id, "add authentication system")
        self.create_transcript()

        # SDK detects incomplete work
        mock_sdk.return_value = "BLOCKED: Only placeholder functions created, actual authentication logic missing."

        result = validate_intent(
            session_id=session_id,
            transcript_path=self.transcript_path,
            prompts_dir=self.prompts_dir,
        )

        # Should block with feedback
        self.assertEqual(result["decision"], "block")
        self.assertIn("placeholder", result["reason"])
        self.assertIn("authentication logic", result["reason"])


class TestSDKValidationPromptTemplate(unittest.TestCase):
    """Test SDK validation prompt template construction."""

    def test_build_validation_prompt(self):
        """Should build correct validation prompt for SDK."""
        from src.pacemaker.intent_validator import build_validation_prompt

        user_prompt = "implement a calculator"
        messages = [
            "[USER]\nimplement a calculator",
            "[ASSISTANT]\nI will create a calculator",
            "[ASSISTANT]\nImplementation complete",
        ]

        prompt = build_validation_prompt(user_prompt, messages)

        # Should contain user's original request
        self.assertIn("implement a calculator", prompt)
        self.assertIn("YOUR ORIGINAL REQUEST:", prompt)

        # Should contain last messages
        self.assertIn("CLAUDE'S WORK", prompt)
        self.assertIn("Last 10 messages", prompt)

        # Should have response format instructions
        self.assertIn("APPROVED", prompt)
        self.assertIn("BLOCKED:", prompt)

        # Should act as user proxy
        self.assertIn("USER who originally requested", prompt)


if __name__ == "__main__":
    unittest.main()
