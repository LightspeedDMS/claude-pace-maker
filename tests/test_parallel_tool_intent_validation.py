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

    def test_parallel_writes_intent_found(self, tmp_path):
        """X|T|T pattern: text with INTENT followed by two Write tool_use entries."""
        transcript = tmp_path / "agent.jsonl"
        _write_transcript(
            [
                {"message": {"role": "user", "content": "implement both DTOs"}},
                _assistant_text(
                    "Now the DTOs. Creating both in parallel.\n\n"
                    "INTENT: Create src/dto/quota-response.dto.ts — new DTO file.\n"
                    "User permission to skip TDD: User said 'skip tests'.\n\n"
                    "INTENT: Create src/dto/rate-limit-info.dto.ts — new DTO file.\n"
                    "User permission to skip TDD: User said 'skip tests'."
                ),
                _assistant_tool_use(
                    "Write",
                    {
                        "file_path": "src/dto/quota-response.dto.ts",
                        "content": "export class QuotaResponse {}",
                    },
                ),
                _assistant_tool_use(
                    "Write",
                    {
                        "file_path": "src/dto/rate-limit-info.dto.ts",
                        "content": "export class RateLimitInfo {}",
                    },
                ),
            ],
            transcript,
        )

        messages = get_last_n_messages_for_validation(str(transcript), n=2)
        assert len(messages) >= 2
        combined = "\n".join(messages)
        assert (
            "intent:" in combined.lower()
        ), f"INTENT not found in window. Messages: {messages}"

    def test_parallel_writes_regex_stage1_passes(self, tmp_path):
        """Full pipeline: X|T|T → extract → regex stage1 should find INTENT."""
        transcript = tmp_path / "agent.jsonl"
        _write_transcript(
            [
                {"message": {"role": "user", "content": "create files"}},
                _assistant_text(
                    "Creating files now.\n\n"
                    "INTENT: Create src/services/quota-cache.ts — new cache service.\n"
                    "User permission to skip TDD: User said 'skip tests'."
                ),
                _assistant_tool_use(
                    "Write",
                    {
                        "file_path": "src/services/quota-cache.ts",
                        "content": "class QuotaCache {}",
                    },
                ),
                _assistant_tool_use(
                    "Write",
                    {
                        "file_path": "src/services/rate-limiter.ts",
                        "content": "class RateLimiter {}",
                    },
                ),
            ],
            transcript,
        )

        messages = get_last_n_messages_for_validation(str(transcript), n=2)
        current_message = extract_current_assistant_message(messages)
        assert _has_intent_marker(current_message) is not None
        result = _regex_stage1_check(current_message, "src/services/quota-cache.ts", [])
        assert (
            result != "NO"
        ), f"Stage1 returned NO for current_message: {current_message[:200]}"

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

    def test_three_parallel_tool_uses(self, tmp_path):
        """X|T|T|T pattern: three parallel tool_use entries."""
        transcript = tmp_path / "agent.jsonl"
        _write_transcript(
            [
                {"message": {"role": "user", "content": "create all three"}},
                _assistant_text(
                    "INTENT: Create src/a.ts — file A.\n"
                    "INTENT: Create src/b.ts — file B.\n"
                    "INTENT: Create src/c.ts — file C.\n"
                    "User permission to skip TDD: User said 'skip tests'."
                ),
                _assistant_tool_use("Write", {"file_path": "src/a.ts", "content": "a"}),
                _assistant_tool_use("Write", {"file_path": "src/b.ts", "content": "b"}),
                _assistant_tool_use("Write", {"file_path": "src/c.ts", "content": "c"}),
            ],
            transcript,
        )

        messages = get_last_n_messages_for_validation(str(transcript), n=2)
        combined = "\n".join(messages)
        assert "intent:" in combined.lower()

    def test_text_before_intent_with_parallel_writes(self, tmp_path):
        """User's exact failing pattern: preamble text before INTENT + parallel writes."""
        transcript = tmp_path / "agent.jsonl"
        _write_transcript(
            [
                {"message": {"role": "user", "content": "implement the service"}},
                _assistant_text("I'll pledge to TDD."),
                _assistant_tool_use("Read", {"file_path": "src/existing.ts"}),
                _user_tool_result(),
                _assistant_text(
                    "All existing changes look correct. Now creating the remaining files. "
                    "Let me start with the core implementations.\n\n"
                    "INTENT: Create src/services/quota-cache.ts — new file implementing "
                    "quota caching with TTL-based invalidation.\n"
                    "User permission to skip TDD: User said 'Write code in main context'."
                ),
                _assistant_tool_use(
                    "Write",
                    {
                        "file_path": "src/services/quota-cache.ts",
                        "content": "export class QuotaCache {}",
                    },
                ),
                _assistant_tool_use(
                    "Write",
                    {
                        "file_path": "src/services/rate-limiter.ts",
                        "content": "export class RateLimiter {}",
                    },
                ),
            ],
            transcript,
        )

        messages = get_last_n_messages_for_validation(str(transcript), n=2)
        current_message = extract_current_assistant_message(messages)
        assert _has_intent_marker(current_message) is not None
        result = _regex_stage1_check(current_message, "src/services/quota-cache.ts", [])
        assert (
            result != "NO"
        ), f"Stage1 should pass. current_message: {current_message[:300]}"


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

    def test_thinking_between_text_and_tool_use(self, tmp_path):
        """X|G|T pattern: thinking between INTENT text and tool_use."""
        transcript = tmp_path / "agent.jsonl"
        _write_transcript(
            [
                {"message": {"role": "user", "content": "update the code"}},
                _assistant_text(
                    "INTENT: Modify src/db.py to add connection pooling.\n"
                    "Test coverage: tests/test_db.py - test_pool()"
                ),
                _assistant_thinking("I should use a pool size of 10..."),
                _assistant_tool_use(
                    "Edit",
                    {
                        "file_path": "src/db.py",
                        "old_string": "old",
                        "new_string": "new",
                    },
                ),
            ],
            transcript,
        )

        messages = get_last_n_messages_for_validation(str(transcript), n=2)
        combined = "\n".join(messages)
        assert "intent:" in combined.lower()

    def test_multiple_thinking_blocks_filtered(self, tmp_path):
        """Multiple thinking blocks should all be filtered."""
        transcript = tmp_path / "agent.jsonl"
        _write_transcript(
            [
                {"message": {"role": "user", "content": "refactor"}},
                _assistant_thinking("thinking 1"),
                _assistant_thinking("thinking 2"),
                _assistant_text(
                    "INTENT: Modify src/utils.py to extract helper.\n"
                    "Test coverage: tests/test_utils.py - test_helper()"
                ),
                _assistant_thinking("thinking 3"),
                _assistant_tool_use(
                    "Edit",
                    {
                        "file_path": "src/utils.py",
                        "old_string": "old",
                        "new_string": "new",
                    },
                ),
            ],
            transcript,
        )

        messages = get_last_n_messages_for_validation(str(transcript), n=2)
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


class TestWindowExpansionBounds:
    """Ensure expansion is bounded and correct."""

    def test_expansion_includes_only_from_last_text(self, tmp_path):
        """Expansion should find the NEAREST text entry, not go further back."""
        transcript = tmp_path / "agent.jsonl"
        _write_transcript(
            [
                {"message": {"role": "user", "content": "step 1"}},
                _assistant_text("OLD INTENT: Modify src/old.py — old change."),
                _assistant_tool_use(
                    "Edit",
                    {"file_path": "src/old.py", "old_string": "a", "new_string": "b"},
                ),
                _user_tool_result(),
                _assistant_text(
                    "INTENT: Modify src/new.py to add feature.\n"
                    "Test coverage: tests/test_new.py - test_feature()"
                ),
                _assistant_tool_use(
                    "Write", {"file_path": "src/new.py", "content": "new"}
                ),
                _assistant_tool_use("Bash", {"command": "python -m pytest"}),
            ],
            transcript,
        )

        messages = get_last_n_messages_for_validation(str(transcript), n=2)
        combined = "\n".join(messages)
        assert "src/new.py" in combined
        assert "intent:" in combined.lower()

    def test_many_parallel_reads_before_write(self, tmp_path):
        """Pattern: text → Read × 5 → Write. Text should still be found."""
        entries = [
            {"message": {"role": "user", "content": "analyze and fix"}},
            _assistant_text(
                "INTENT: Create src/fix.py — new fix module.\n"
                "User permission to skip TDD: User said 'skip tests'."
            ),
        ]
        for i in range(5):
            entries.append(
                _assistant_tool_use("Read", {"file_path": f"src/file{i}.py"})
            )
        entries.append(
            _assistant_tool_use("Write", {"file_path": "src/fix.py", "content": "fix"})
        )
        _write_transcript(entries, tmp_path / "agent.jsonl")

        messages = get_last_n_messages_for_validation(
            str(tmp_path / "agent.jsonl"), n=2
        )
        combined = "\n".join(messages)
        assert "intent:" in combined.lower()
