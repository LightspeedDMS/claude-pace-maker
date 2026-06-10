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


class TestCurrentTurnMessageForValidation(unittest.TestCase):
    """Fix 3: requestId-anchored extraction of the current tool_use's turn."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.transcript_path = os.path.join(self.temp_dir, "transcript.jsonl")

    def tearDown(self):
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def create_transcript(self, entries):
        with open(self.transcript_path, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

    @staticmethod
    def _asst(request_id, blocks):
        return {
            "requestId": request_id,
            "message": {"role": "assistant", "content": blocks},
        }

    @staticmethod
    def _text(t):
        return {"type": "text", "text": t}

    @staticmethod
    def _edit(file_path, new_string):
        return {
            "type": "tool_use",
            "name": "Edit",
            "input": {"file_path": file_path, "new_string": new_string},
        }

    def test_intent_same_requestid_as_tooluse_is_captured(self):
        """INTENT + tool_use share a requestId; a prior prose block has a
        DIFFERENT requestId. The INTENT must be captured."""
        from src.pacemaker.transcript_reader import (
            get_current_turn_message_for_validation,
        )

        self.create_transcript(
            [
                self._asst("req-A", [self._text("Let me look into this first.")]),
                self._asst(
                    "req-B",
                    [
                        self._text(
                            "INTENT: Modify src/auth.py to add bar().\n"
                            "TEST FILE: tests/test_auth.py"
                        ),
                        self._edit("src/auth.py", "def bar(): pass"),
                    ],
                ),
            ]
        )

        msg = get_current_turn_message_for_validation(self.transcript_path)
        self.assertIn("INTENT:", msg)
        self.assertIn("TEST FILE: tests/test_auth.py", msg)
        self.assertIn("src/auth.py", msg)

    def test_intent_split_entries_same_requestid_after_interrupt(self):
        """An interrupt-style turn intervenes, but the INTENT text and the
        tool_use are separate JSONL entries that SHARE the tool_use's
        requestId. The INTENT must still be captured."""
        from src.pacemaker.transcript_reader import (
            get_current_turn_message_for_validation,
        )

        self.create_transcript(
            [
                self._asst("req-prior", [self._text("Earlier unrelated turn.")]),
                # interrupt-style turn with its own requestId
                self._asst("req-interrupt", [self._text("Acknowledged, continuing.")]),
                # current turn fragmented across two entries sharing req-cur
                self._asst(
                    "req-cur",
                    [
                        self._text(
                            "INTENT: Modify src/auth.py to add bar().\n"
                            "TEST FILE: tests/test_auth.py"
                        )
                    ],
                ),
                self._asst("req-cur", [self._edit("src/auth.py", "def bar(): pass")]),
            ]
        )

        msg = get_current_turn_message_for_validation(self.transcript_path)
        self.assertIn("INTENT:", msg)
        self.assertIn("TEST FILE:", msg)
        self.assertIn("src/auth.py", msg)

    def test_stale_prior_turn_intent_not_captured(self):
        """A stale INTENT from a PRIOR turn (different requestId) with an
        intervening different turn must NOT be captured for the current edit."""
        from src.pacemaker.transcript_reader import (
            get_current_turn_message_for_validation,
        )

        self.create_transcript(
            [
                # stale INTENT for a DIFFERENT file, prior turn
                self._asst(
                    "req-stale",
                    [
                        self._text(
                            "INTENT: Modify src/old.py to add old().\n"
                            "TEST FILE: tests/test_old.py"
                        ),
                        self._edit("src/old.py", "def old(): pass"),
                    ],
                ),
                # intervening different turn
                self._asst("req-mid", [self._text("Now doing something else.")]),
                # current turn: tool_use with NO intent declaration
                self._asst("req-cur", [self._edit("src/auth.py", "def bar(): pass")]),
            ]
        )

        msg = get_current_turn_message_for_validation(self.transcript_path)
        # The current turn (req-cur) carries the Write/Edit but NO intent
        # marker, so the requestId-anchored override defers to the n-back
        # rescue by returning "". Critically, the STALE prior-turn INTENT for
        # the DIFFERENT file (src/old.py) is never pulled into the current
        # edit's validation context.
        self.assertEqual(msg, "")
        self.assertNotIn("src/old.py", msg)
        self.assertNotIn("INTENT: Modify src/old.py", msg)

    def test_wellbehaved_single_turn_unchanged(self):
        """The already-passing single-turn case (INTENT + tool_use in one
        requestId, no fragmentation) is captured unchanged."""
        from src.pacemaker.transcript_reader import (
            get_current_turn_message_for_validation,
        )

        self.create_transcript(
            [
                self._asst(
                    "req-only",
                    [
                        self._text(
                            "INTENT: Modify src/auth.py to add bar().\n"
                            "TEST FILE: tests/test_auth.py"
                        ),
                        self._edit("src/auth.py", "def bar(): pass"),
                    ],
                ),
            ]
        )

        msg = get_current_turn_message_for_validation(self.transcript_path)
        self.assertIn("INTENT:", msg)
        self.assertIn("src/auth.py", msg)

    def test_no_tooluse_returns_empty(self):
        """No Write/Edit tool_use anywhere → empty string (safe fallback)."""
        from src.pacemaker.transcript_reader import (
            get_current_turn_message_for_validation,
        )

        self.create_transcript(
            [self._asst("req-A", [self._text("Just talking, no edits.")])]
        )

        msg = get_current_turn_message_for_validation(self.transcript_path)
        self.assertEqual(msg, "")

    def test_missing_requestid_falls_back_safely(self):
        """Entries without requestId: the last entry's tool_use turn is still
        returned (graceful fallback, no crash)."""
        from src.pacemaker.transcript_reader import (
            get_current_turn_message_for_validation,
        )

        self.create_transcript(
            [
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            self._text(
                                "INTENT: Modify src/auth.py to add bar().\n"
                                "TEST FILE: tests/test_auth.py"
                            ),
                            self._edit("src/auth.py", "def bar(): pass"),
                        ],
                    }
                }
            ]
        )

        msg = get_current_turn_message_for_validation(self.transcript_path)
        self.assertIn("INTENT:", msg)
        self.assertIn("src/auth.py", msg)


if __name__ == "__main__":
    unittest.main()
