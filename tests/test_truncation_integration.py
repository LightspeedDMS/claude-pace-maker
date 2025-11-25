#!/usr/bin/env python3
"""
Integration test for user message truncation feature.

Demonstrates the truncation feature working with the build_validation_prompt function.
"""

import unittest


class TestTruncationIntegration(unittest.TestCase):
    """Integration tests for truncation feature."""

    def test_long_user_message_truncated_in_full_prompt(self):
        """
        Integration test: Long user message gets truncated when building validation prompt.

        This demonstrates the feature working end-to-end with realistic message sizes.
        """
        from src.pacemaker.intent_validator import build_validation_prompt

        # Create a realistic long user message (5000 chars)
        long_message = "Please help me implement " + ("X" * 4975)

        all_user_messages = [long_message]
        last_assistant_messages = ["I'll help you with that."]
        last_assistant = "Here's my response."

        # Build the prompt
        prompt = build_validation_prompt(
            all_user_messages, last_assistant_messages, last_assistant
        )

        # Verify truncation occurred
        self.assertNotIn(long_message, prompt)
        self.assertIn("[TRUNCATED>4096 CHARS]", prompt)

        # Verify the truncated portion is present (first 4096 chars)
        truncated_content = long_message[:4096]
        self.assertIn(truncated_content, prompt)

    def test_multiple_long_messages_all_truncated(self):
        """
        Integration test: Multiple long messages are all independently truncated.
        """
        from src.pacemaker.intent_validator import build_validation_prompt

        # Create multiple long messages
        message1 = "First request: " + ("A" * 5000)
        message2 = "Second request: " + ("B" * 6000)
        message3 = "Third request: " + ("C" * 4500)

        all_user_messages = [message1, message2, message3]
        last_assistant_messages = ["Working on it..."]
        last_assistant = "Almost done."

        # Build the prompt
        prompt = build_validation_prompt(
            all_user_messages, last_assistant_messages, last_assistant
        )

        # Verify all three got truncated
        truncation_count = prompt.count("[TRUNCATED>4096 CHARS]")
        self.assertEqual(truncation_count, 3)

        # Verify we have the beginning of each message
        self.assertIn("First request: ", prompt)
        self.assertIn("Second request: ", prompt)
        self.assertIn("Third request: ", prompt)

    def test_normal_conversation_not_affected(self):
        """
        Integration test: Normal-sized messages are not modified.
        """
        from src.pacemaker.intent_validator import build_validation_prompt

        # Normal conversation
        all_user_messages = [
            "Can you help me with authentication?",
            "I need to add JWT tokens.",
            "Thanks for the help!",
        ]
        last_assistant_messages = [
            "Sure, I'll help you implement JWT authentication.",
            "Here's the code...",
        ]
        last_assistant = "You're welcome! Let me know if you need anything else."

        # Build the prompt
        prompt = build_validation_prompt(
            all_user_messages, last_assistant_messages, last_assistant
        )

        # Verify no truncation occurred
        self.assertNotIn("[TRUNCATED>", prompt)

        # Verify all messages are present in full
        for msg in all_user_messages:
            self.assertIn(msg, prompt)


if __name__ == "__main__":
    unittest.main()
