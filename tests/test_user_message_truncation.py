#!/usr/bin/env python3
"""
Unit tests for user message truncation feature in intent_validator.

Tests that user messages exceeding configured length limit are truncated
with appropriate suffix markers.
"""

import unittest
from unittest.mock import patch


class TestUserMessageTruncation(unittest.TestCase):
    """Test user message truncation functionality."""

    def test_message_under_limit_not_truncated(self):
        """Messages shorter than limit should NOT be truncated."""
        from src.pacemaker.intent_validator import build_validation_prompt

        # Short message (100 chars) well under default 4096 limit
        short_message = "A" * 100
        all_user_messages = [short_message]
        last_assistant_messages = ["response"]
        last_assistant = "final response"

        prompt = build_validation_prompt(
            all_user_messages, last_assistant_messages, last_assistant
        )

        # Should contain full message without truncation marker
        self.assertIn(short_message, prompt)
        self.assertNotIn("[TRUNCATED>", prompt)

    def test_message_exactly_at_limit_not_truncated(self):
        """Message exactly at limit (4096 chars) should NOT be truncated."""
        from src.pacemaker.intent_validator import build_validation_prompt

        # Message exactly 4096 chars
        exact_limit_message = "B" * 4096
        all_user_messages = [exact_limit_message]
        last_assistant_messages = ["response"]
        last_assistant = "final response"

        prompt = build_validation_prompt(
            all_user_messages, last_assistant_messages, last_assistant
        )

        # Should contain full message without truncation marker
        self.assertIn(exact_limit_message, prompt)
        self.assertNotIn("[TRUNCATED>", prompt)

    def test_message_over_limit_truncated_with_suffix(self):
        """Message over limit (5000 chars) should be truncated to 4096 with suffix."""
        from src.pacemaker.intent_validator import build_validation_prompt

        # Message over limit (5000 chars)
        over_limit_message = "C" * 5000
        all_user_messages = [over_limit_message]
        last_assistant_messages = ["response"]
        last_assistant = "final response"

        prompt = build_validation_prompt(
            all_user_messages, last_assistant_messages, last_assistant
        )

        # Should NOT contain full original message
        self.assertNotIn(over_limit_message, prompt)

        # Should contain truncated message with suffix
        self.assertIn("C" * 4096, prompt)
        self.assertIn("[TRUNCATED>4096 CHARS]", prompt)

        # Verify truncated content appears in prompt
        # Check that we have the first 4096 chars followed by the marker
        self.assertIn("[TRUNCATED>4096 CHARS]", prompt)

    def test_very_long_message_truncated_correctly(self):
        """Very long message (10000 chars) should be truncated to 4096 with suffix."""
        from src.pacemaker.intent_validator import build_validation_prompt

        # Very long message (10000 chars)
        very_long_message = "D" * 10000
        all_user_messages = [very_long_message]
        last_assistant_messages = ["response"]
        last_assistant = "final response"

        prompt = build_validation_prompt(
            all_user_messages, last_assistant_messages, last_assistant
        )

        # Should NOT contain full original message
        self.assertNotIn(very_long_message, prompt)

        # Should contain truncated message with suffix
        self.assertIn("D" * 4096, prompt)
        self.assertIn("[TRUNCATED>4096 CHARS]", prompt)

    def test_empty_message_not_modified(self):
        """Empty messages should NOT be modified."""
        from src.pacemaker.intent_validator import build_validation_prompt

        all_user_messages = [""]
        last_assistant_messages = ["response"]
        last_assistant = "final response"

        prompt = build_validation_prompt(
            all_user_messages, last_assistant_messages, last_assistant
        )

        # Should not have truncation marker
        self.assertNotIn("[TRUNCATED>", prompt)

    @patch("src.pacemaker.intent_validator.get_config")
    def test_custom_limit_from_config_respected(self, mock_get_config):
        """Should respect custom user_message_max_length from config."""
        from src.pacemaker.intent_validator import build_validation_prompt

        # Mock config to return custom limit of 2000
        mock_get_config.return_value = 2000

        # Message over custom limit (3000 chars)
        long_message = "E" * 3000
        all_user_messages = [long_message]
        last_assistant_messages = ["response"]
        last_assistant = "final response"

        prompt = build_validation_prompt(
            all_user_messages, last_assistant_messages, last_assistant
        )

        # Should be truncated at custom limit (2000)
        self.assertIn("E" * 2000, prompt)
        self.assertIn("[TRUNCATED>2000 CHARS]", prompt)

        # Should NOT contain original message
        self.assertNotIn(long_message, prompt)

    def test_multiple_messages_truncated_independently(self):
        """Multiple long messages should each be truncated independently."""
        from src.pacemaker.intent_validator import build_validation_prompt

        # Two messages, both over limit
        message1 = "F" * 5000
        message2 = "G" * 6000
        all_user_messages = [message1, message2]
        last_assistant_messages = ["response"]
        last_assistant = "final response"

        prompt = build_validation_prompt(
            all_user_messages, last_assistant_messages, last_assistant
        )

        # Both should be truncated
        self.assertIn("F" * 4096, prompt)
        self.assertIn("G" * 4096, prompt)

        # Should have two truncation markers
        truncation_count = prompt.count("[TRUNCATED>4096 CHARS]")
        self.assertEqual(truncation_count, 2)

    def test_mixed_messages_only_long_ones_truncated(self):
        """Mix of short and long messages - only long ones should be truncated."""
        from src.pacemaker.intent_validator import build_validation_prompt

        short_message = "Short message"
        long_message = "H" * 5000
        all_user_messages = [short_message, long_message]
        last_assistant_messages = ["response"]
        last_assistant = "final response"

        prompt = build_validation_prompt(
            all_user_messages, last_assistant_messages, last_assistant
        )

        # Short message should appear in full
        self.assertIn("Short message", prompt)

        # Long message should be truncated
        self.assertIn("H" * 4096, prompt)
        self.assertIn("[TRUNCATED>4096 CHARS]", prompt)

        # Should have exactly one truncation marker
        truncation_count = prompt.count("[TRUNCATED>4096 CHARS]")
        self.assertEqual(truncation_count, 1)


if __name__ == "__main__":
    unittest.main()
