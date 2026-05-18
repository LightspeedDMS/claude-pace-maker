"""
Tests for the parallel tool_use window bug fix.

Bug: When Claude makes parallel tool calls, each tool_use is a separate JSONL
entry. With n=2, the last 2 entries are both tool_use (empty text), and the
text entry containing the INTENT declaration is pushed out of the window.

Fix: get_last_n_messages_for_validation expands backward to include the
nearest text-containing entry when the n-window has no text.
"""

import json


from pacemaker.transcript_reader import get_last_n_messages_for_validation
from pacemaker.intent_validator import (
    extract_current_assistant_message,
    _has_intent_marker,
    _regex_stage1_check,
)


def _write_transcript(entries, path):
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _assistant_text(text):
    return {
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}
    }


def _assistant_tool_use(name, input_data=None):
    return {
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_test",
                    "name": name,
                    "input": input_data or {},
                }
            ],
        }
    }


def _user_tool_result(tool_use_id="toolu_test"):
    return {
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_use_id}],
        }
    }


def _assistant_thinking(thinking_text):
    return {
        "message": {
            "role": "assistant",
            "content": [{"type": "thinking", "thinking": thinking_text}],
        }
    }


class TestParallelToolUseWindow:
    """Tests for the X|T|T pattern where n=2 missed the INTENT text."""

    def test_single_tool_use_still_works(self, tmp_path):
        """X|T pattern (normal case): n=2 naturally includes the text entry."""
        transcript = tmp_path / "agent.jsonl"
        _write_transcript(
            [
                {"message": {"role": "user", "content": "fix the bug"}},
                _assistant_text(
                    "INTENT: Modify src/auth.py to fix token validation.\n"
                    "Test coverage: tests/test_auth.py - test_validate_token()"
                ),
                _assistant_tool_use(
                    "Edit",
                    {
                        "file_path": "src/auth.py",
                        "old_string": "old",
                        "new_string": "new",
                    },
                ),
            ],
            transcript,
        )

        messages = get_last_n_messages_for_validation(str(transcript), n=2)
        current_message = extract_current_assistant_message(messages)
        assert _has_intent_marker(current_message) is not None
        result = _regex_stage1_check(current_message, "src/auth.py", [])
        assert result != "NO"


class TestGhostEntryFiltering:
    """Tests for filtering out thinking blocks and other empty entries."""

    def test_thinking_block_filtered(self, tmp_path):
        """Thinking blocks (no text, no tools) should not inflate the window."""
        transcript = tmp_path / "agent.jsonl"
        _write_transcript(
            [
                {"message": {"role": "user", "content": "fix it"}},
                _assistant_thinking("Let me think about this..."),
                _assistant_text(
                    "INTENT: Modify src/auth.py to fix validation.\n"
                    "Test coverage: tests/test_auth.py - test_fix()"
                ),
                _assistant_tool_use(
                    "Edit",
                    {
                        "file_path": "src/auth.py",
                        "old_string": "old",
                        "new_string": "new",
                    },
                ),
            ],
            transcript,
        )

        messages = get_last_n_messages_for_validation(str(transcript), n=2)
        assert len(messages) == 2
        combined = "\n".join(messages)
        assert "intent:" in combined.lower()


class TestNoIntentStillBlocked:
    """Ensure that missing INTENT is still correctly rejected."""

    def test_no_intent_still_returns_no(self, tmp_path):
        """Tool_use without any INTENT declaration should still fail."""
        transcript = tmp_path / "agent.jsonl"
        _write_transcript(
            [
                {"message": {"role": "user", "content": "write some code"}},
                _assistant_text("I'll create the file now."),
                _assistant_tool_use(
                    "Write",
                    {"file_path": "src/new.py", "content": "print('hello')"},
                ),
            ],
            transcript,
        )

        messages = get_last_n_messages_for_validation(str(transcript), n=2)
        current_message = extract_current_assistant_message(messages)
        result = _regex_stage1_check(current_message, "src/new.py", [])
        assert result == "NO"

    def test_empty_transcript(self, tmp_path):
        """Empty transcript returns empty list."""
        transcript = tmp_path / "agent.jsonl"
        _write_transcript([], transcript)
        messages = get_last_n_messages_for_validation(str(transcript), n=2)
        assert messages == []

    def test_only_tool_uses_no_text_anywhere(self, tmp_path):
        """If there's no text entry at all, expansion finds nothing and validation fails."""
        transcript = tmp_path / "agent.jsonl"
        _write_transcript(
            [
                {"message": {"role": "user", "content": "do it"}},
                _assistant_tool_use("Write", {"file_path": "src/x.py", "content": "x"}),
                _assistant_tool_use("Write", {"file_path": "src/y.py", "content": "y"}),
            ],
            transcript,
        )

        messages = get_last_n_messages_for_validation(str(transcript), n=2)
        current_message = extract_current_assistant_message(messages)
        assert _has_intent_marker(current_message) is None
