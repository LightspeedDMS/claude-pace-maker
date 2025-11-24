#!/usr/bin/env python3
"""
Unit tests for transcript reader functions.

Tests the new context extraction functions that read user messages
from JSONL transcripts for intent validation.
"""

import unittest
import tempfile
import os
import json


class TestGetAllUserMessages(unittest.TestCase):
    """Test get_all_user_messages function."""

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
        """Create a JSONL transcript file from messages."""
        with open(self.transcript_path, "w") as f:
            for msg in messages:
                f.write(json.dumps(msg) + "\n")

    def test_extracts_all_user_messages(self):
        """Should extract ALL user messages from transcript."""
        from src.pacemaker.transcript_reader import get_all_user_messages

        # Create transcript with mixed user/assistant messages
        messages = [
            {
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "First user message"}],
                }
            },
            {
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Assistant response 1"}],
                }
            },
            {
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Second user message"}],
                }
            },
            {
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Assistant response 2"}],
                }
            },
            {
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Third user message"}],
                }
            },
            {
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Fourth user message"}],
                }
            },
            {
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Fifth user message"}],
                }
            },
            {
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Sixth user message"}],
                }
            },
        ]
        self.create_transcript(messages)

        result = get_all_user_messages(self.transcript_path)

        # Should return ALL 6 user messages (not assistant messages)
        self.assertEqual(len(result), 6)
        self.assertEqual(result[0], "First user message")
        self.assertEqual(result[1], "Second user message")
        self.assertEqual(result[2], "Third user message")
        self.assertEqual(result[3], "Fourth user message")
        self.assertEqual(result[4], "Fifth user message")
        self.assertEqual(result[5], "Sixth user message")

    def test_returns_all_user_messages_regardless_of_count(self):
        """Should return all user messages without limiting."""
        from src.pacemaker.transcript_reader import get_all_user_messages

        messages = [
            {
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "User message 1"}],
                }
            },
            {
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Assistant response"}],
                }
            },
            {
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "User message 2"}],
                }
            },
        ]
        self.create_transcript(messages)

        result = get_all_user_messages(self.transcript_path)

        # All 2 user messages available
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], "User message 1")
        self.assertEqual(result[1], "User message 2")

    def test_handles_empty_transcript(self):
        """Should return empty list for empty transcript."""
        from src.pacemaker.transcript_reader import get_all_user_messages

        self.create_transcript([])

        result = get_all_user_messages(self.transcript_path)

        self.assertEqual(result, [])

    def test_handles_missing_file(self):
        """Should return empty list for missing transcript file."""
        from src.pacemaker.transcript_reader import get_all_user_messages

        nonexistent_path = os.path.join(self.temp_dir, "nonexistent.jsonl")

        result = get_all_user_messages(nonexistent_path)

        self.assertEqual(result, [])

    def test_handles_multiline_content(self):
        """Should handle multiline user message content."""
        from src.pacemaker.transcript_reader import get_all_user_messages

        messages = [
            {
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "First line\nSecond line\nThird line",
                        }
                    ],
                }
            }
        ]
        self.create_transcript(messages)

        result = get_all_user_messages(self.transcript_path)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], "First line\nSecond line\nThird line")

    def test_handles_multiple_content_blocks(self):
        """Should concatenate multiple content blocks in user message."""
        from src.pacemaker.transcript_reader import get_all_user_messages

        messages = [
            {
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Part 1"},
                        {"type": "text", "text": "Part 2"},
                        {"type": "text", "text": "Part 3"},
                    ],
                }
            }
        ]
        self.create_transcript(messages)

        result = get_all_user_messages(self.transcript_path)

        self.assertEqual(len(result), 1)
        self.assertIn("Part 1", result[0])
        self.assertIn("Part 2", result[0])
        self.assertIn("Part 3", result[0])


if __name__ == "__main__":
    unittest.main()
