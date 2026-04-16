#!/usr/bin/env python3
"""
Unit tests for META message filtering in stop hook context building.

These tests verify that isMeta=true entries (stop hook feedback injections)
are excluded from build_stop_hook_context(), preventing the death spiral where:
  1. Stop hook rejects E2E evidence
  2. Injects META feedback message
  3. Assistant responds with short "I'm waiting" message
  4. Stop hook now evaluates the SHORT message as "LAST MESSAGE"
  5. Repeat -> E2E evidence never evaluated correctly
"""

import json
import os
import shutil
import tempfile
import unittest

# Short assistant responses immediately following a META message are filtered
# when their length is below this threshold.  Real E2E tables are 500+ chars;
# reflexive "I'm waiting" messages are typically <150 chars.  200 gives
# comfortable margin — matches the implementation in transcript_reader.py.
MIN_SUBSTANTIVE_MESSAGE_CHARS = 200


def _make_user_msg(text):
    """Helper: build a normal user JSONL entry."""
    return {"message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def _make_assistant_msg(text):
    """Helper: build a normal assistant JSONL entry."""
    return {
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}
    }


def _make_meta_user_msg(text):
    """Helper: build a META user JSONL entry (stop hook feedback injection)."""
    return {
        "isMeta": True,
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


E2E_TABLE = """\
| # | Test | Command | Captured Output | Result |
|---|------|---------|----------------|--------|
| 1 | Health check | curl -s http://localhost:8000/health | {"status":"ok","version":"1.0"} | PASS |
| 2 | Create item | curl -s -X POST http://localhost:8000/items -d '{"name":"test"}' | {"id":42,"name":"test"} | PASS |
| 3 | List items | curl -s http://localhost:8000/items | [{"id":42,"name":"test"}] | PASS |
| 4 | Error handling | curl -s http://localhost:8000/items/999 | {"error":"not found","code":404} | PASS |
Server started on port 8000, all 4 acceptance criteria verified against live system."""

META_FEEDBACK = (
    "BLOCKED: Your recent messages do not contain E2E evidence. "
    "Please produce a proper E2E evidence table with real captured output."
)

SHORT_WAITING = "Understood. I will wait for your feedback before continuing."


class TestMetaMessageFiltering(unittest.TestCase):
    """Tests for META message filtering in build_stop_hook_context."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.transcript_path = os.path.join(self.temp_dir, "transcript.jsonl")

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def _write_transcript(self, entries):
        with open(self.transcript_path, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

    def _build_backwards(self, first_n_pairs=1):
        """Return backwards_messages list from current transcript.

        Uses first_n_pairs=1 by default so the backwards walk is exercised
        even for short transcripts.  The default of 10 in production absorbs
        all messages into first_pairs for small transcripts, leaving
        backwards_messages empty — which would make the tests vacuously pass.
        """
        from src.pacemaker.transcript_reader import build_stop_hook_context

        result = build_stop_hook_context(
            self.transcript_path, first_n_pairs=first_n_pairs
        )
        return result["backwards_messages"]

    def _backwards_texts(self, first_n_pairs=1):
        """Return list of text strings from backwards_messages."""
        return [
            text for (_role, text) in self._build_backwards(first_n_pairs=first_n_pairs)
        ]

    # ------------------------------------------------------------------ #
    # 1. META messages must not appear in backwards_messages              #
    # ------------------------------------------------------------------ #
    def test_meta_messages_excluded_from_context(self):
        """
        META entries (isMeta=true) must be excluded from backwards_messages.

        Build a transcript with:
          - Normal user request + substantive assistant E2E table
          - META user message (stop hook feedback injection)
          - Short assistant "waiting" response  (<200 chars)
          - Another META user message
          - Another short assistant "waiting" response  (<200 chars)

        Assert: backwards_messages contains NO META text and NO short waiting messages.
        """
        entries = [
            _make_user_msg("Implement the feature and run E2E tests."),
            _make_assistant_msg(E2E_TABLE),
            _make_meta_user_msg(META_FEEDBACK),
            _make_assistant_msg(SHORT_WAITING),
            _make_meta_user_msg(META_FEEDBACK),
            _make_assistant_msg(SHORT_WAITING),
        ]
        self._write_transcript(entries)

        all_texts = self._backwards_texts()

        # No META feedback text must appear
        for text in all_texts:
            self.assertNotIn(
                "BLOCKED:",
                text,
                "META feedback message must not appear in backwards_messages",
            )

        # No short waiting messages must appear
        for text in all_texts:
            self.assertNotEqual(
                text.strip(),
                SHORT_WAITING.strip(),
                "Short waiting message after META must not appear in backwards_messages",
            )

    # ------------------------------------------------------------------ #
    # 2. Substantive assistant messages AFTER META must be preserved       #
    # ------------------------------------------------------------------ #
    def test_substantive_assistant_after_meta_is_preserved(self):
        """
        An assistant response >= MIN_SUBSTANTIVE_MESSAGE_CHARS chars that follows
        a META message must NOT be filtered — only short reflexive responses are
        suppressed.
        """
        # A real substantive follow-up (not just "I'm waiting")
        substantive_followup = (
            "I have re-run the E2E tests and here are the complete results. "
            + E2E_TABLE
            + "\nAll tests passed against the live system running on port 8000. "
            "The implementation is complete and all acceptance criteria are verified."
        )
        self.assertGreaterEqual(
            len(substantive_followup.strip()),
            MIN_SUBSTANTIVE_MESSAGE_CHARS,
            "Test setup: followup must be >= MIN_SUBSTANTIVE_MESSAGE_CHARS chars",
        )

        entries = [
            _make_user_msg("Implement the feature and run E2E tests."),
            _make_assistant_msg(E2E_TABLE),
            _make_meta_user_msg(META_FEEDBACK),
            _make_assistant_msg(substantive_followup),  # substantive — must be kept
        ]
        self._write_transcript(entries)

        all_texts = self._backwards_texts()

        self.assertTrue(
            any(substantive_followup[:50] in text for text in all_texts),
            "Substantive assistant response after META must be preserved in backwards_messages",
        )

    # ------------------------------------------------------------------ #
    # 3. E2E table is the last substantive message after META+short cycle  #
    # ------------------------------------------------------------------ #
    def test_original_e2e_table_is_the_last_substantive_message(self):
        """
        After a chain of META + short-response cycles, the most recent entry in
        backwards_messages (index 0, most-recent-first) must be exactly the E2E
        table, NOT the short waiting message.

        Transcript order:
          user-request -> E2E-table -> META -> short -> META -> short

        Expected backwards_messages (most recent first):
          index 0: ("assistant", E2E_TABLE)
          index 1: ("user",      "Implement the feature and run E2E tests.")
        """
        entries = [
            _make_user_msg("Implement the feature and run E2E tests."),
            _make_assistant_msg(E2E_TABLE),
            _make_meta_user_msg(META_FEEDBACK),
            _make_assistant_msg(SHORT_WAITING),
            _make_meta_user_msg(META_FEEDBACK),
            _make_assistant_msg(SHORT_WAITING),
        ]
        self._write_transcript(entries)

        backwards = self._build_backwards()

        self.assertGreater(len(backwards), 0, "backwards_messages must not be empty")

        # Most-recent-first: index 0 must be exactly the E2E table
        last_role, last_text = backwards[0]
        self.assertEqual(
            last_role,
            "assistant",
            "Most recent entry must be assistant (E2E table), not a waiting message",
        )
        self.assertEqual(
            last_text,
            E2E_TABLE,
            "Most recent assistant entry must be the exact E2E table text",
        )

        # index 1 must be the original user request
        self.assertGreater(
            len(backwards), 1, "backwards_messages must have at least 2 entries"
        )
        second_role, second_text = backwards[1]
        self.assertEqual(second_role, "user")
        self.assertEqual(second_text, "Implement the feature and run E2E tests.")

    # ------------------------------------------------------------------ #
    # 4. Normal transcript (no META) behaves exactly as before             #
    # ------------------------------------------------------------------ #
    def test_no_meta_messages_behaves_normally(self):
        """
        Regression test: transcripts with no META messages must include all
        user and assistant messages across first_pairs + backwards_messages,
        unchanged from pre-fix behavior.

        With first_n_pairs=1: the first user+assistant pair lands in first_pairs,
        leaving the second user+assistant pair in backwards_messages (2 messages).
        No messages must be dropped.
        """
        from src.pacemaker.transcript_reader import build_stop_hook_context

        entries = [
            _make_user_msg("First user request"),
            _make_assistant_msg("First assistant response with some detail."),
            _make_user_msg("Second user request"),
            _make_assistant_msg(E2E_TABLE),
        ]
        self._write_transcript(entries)

        result = build_stop_hook_context(self.transcript_path, first_n_pairs=1)
        backwards = result["backwards_messages"]
        first_pairs = result["first_pairs"]

        # Collect all text from both sections
        all_texts = [text for (_role, text) in backwards]
        for user_msg, assistant_msgs in first_pairs:
            all_texts.append(user_msg)
            all_texts.extend(assistant_msgs)

        self.assertTrue(
            any("First user request" in t for t in all_texts),
            "First user message must appear (in first_pairs)",
        )
        self.assertTrue(
            any("First assistant response" in t for t in all_texts),
            "First assistant message must appear (in first_pairs)",
        )
        self.assertTrue(
            any("Second user request" in t for t in all_texts),
            "Second user message must appear (in backwards_messages)",
        )
        self.assertTrue(
            any(E2E_TABLE[:30] in t for t in all_texts),
            "E2E table must appear (in backwards_messages)",
        )
        # With first_n_pairs=1: exactly 2 messages in backwards_messages
        self.assertEqual(
            len(backwards), 2, "Second pair must appear in backwards_messages"
        )

    # ------------------------------------------------------------------ #
    # 5. META messages at beginning of transcript are skipped              #
    # ------------------------------------------------------------------ #
    def test_meta_at_beginning_is_skipped(self):
        """
        META messages that appear early (not just at the end) are also filtered.
        The real conversation messages must still be present.
        """
        entries = [
            _make_meta_user_msg("Early META injection from previous session"),
            _make_assistant_msg(SHORT_WAITING),
            _make_user_msg("Real user request: implement and test the feature."),
            _make_assistant_msg(E2E_TABLE),
        ]
        self._write_transcript(entries)

        all_texts = self._backwards_texts()

        # META text must not appear
        for text in all_texts:
            self.assertNotIn(
                "Early META injection",
                text,
                "META messages at the beginning must be filtered",
            )

        # Real conversation must appear
        self.assertTrue(
            any("Real user request" in t for t in all_texts),
            "Real user message must be present",
        )
        self.assertTrue(
            any(E2E_TABLE[:30] in t for t in all_texts),
            "E2E table must be present",
        )


if __name__ == "__main__":
    unittest.main()
