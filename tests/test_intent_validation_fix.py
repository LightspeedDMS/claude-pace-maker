"""
Tests for extract_current_assistant_message — grouped vs ungrouped JSONL formats.

Grouped (requestId): text + tool_use are combined into one message by the
transcript reader. messages[-1] already contains the INTENT declaration.

Ungrouped (legacy / no requestId): text and tool_use are separate JSONL entries.
The INTENT declaration is in messages[-2], tool_use in messages[-1].

The function must support BOTH: return messages[-1] when it already has intent,
and combine messages[-2]+messages[-1] when the intent is one message back.
"""

from pacemaker.intent_validator import extract_current_assistant_message


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_list_returns_empty_string(self):
        assert extract_current_assistant_message([]) == ""

    def test_single_message_returned_as_is(self):
        msg = "INTENT: Modify foo.py\n[TOOL: Write]"
        assert extract_current_assistant_message([msg]) == msg

    def test_single_message_without_intent(self):
        msg = "[TOOL: Write]\nfile_path: foo.py"
        assert extract_current_assistant_message([msg]) == msg


# ---------------------------------------------------------------------------
# Grouped scenario — requestId combines text + tool_use into messages[-1]
# ---------------------------------------------------------------------------


class TestGroupedFormat:
    def test_intent_already_in_last_message(self):
        """When requestId grouping puts intent + tool in same message, return as-is."""
        messages = [
            "Previous unrelated message",
            (
                "INTENT: Modify src/auth.py to add validation.\n"
                "Test coverage: tests/test_auth.py - test_validate()\n\n"
                "[TOOL: Edit]\nfile_path: src/auth.py\nold_string: old\nnew_string: new"
            ),
        ]
        result = extract_current_assistant_message(messages)
        assert "INTENT:" in result
        assert "[TOOL: Edit]" in result
        assert "Previous unrelated" not in result

    def test_grouped_with_section_noise_in_last(self):
        """Grouped message with § intel lines should still return the full message."""
        messages = [
            "Earlier context",
            (
                "§ △0.1 ◎surg ■bug ◇0.9 ↻1\n"
                "INTENT: Modify src/foo.py to fix bug.\n"
                "Test coverage: tests/test_foo.py - test_fix()\n\n"
                "[TOOL: Write]\nfile_path: src/foo.py\ncontent: fixed"
            ),
        ]
        result = extract_current_assistant_message(messages)
        assert "INTENT:" in result
        assert "[TOOL: Write]" in result

    def test_grouped_no_backward_merge_when_intent_in_last(self):
        """When messages[-1] has intent, messages[-2] should NOT be merged in."""
        messages = [
            "INTENT: Modify old_file.py (stale from previous turn)",
            (
                "INTENT: Modify new_file.py to add feature.\n"
                "Test coverage: tests/test_new.py - test_feature()\n\n"
                "[TOOL: Edit]\nfile_path: new_file.py"
            ),
        ]
        result = extract_current_assistant_message(messages)
        assert "new_file.py" in result
        assert "old_file.py" not in result


# ---------------------------------------------------------------------------
# Ungrouped scenario — legacy format without requestId
# ---------------------------------------------------------------------------


class TestUngroupedFormat:
    def test_intent_in_previous_tool_in_last(self):
        """Legacy format: intent text in messages[-2], tool_use in messages[-1]."""
        messages = [
            "Earlier user message",
            "INTENT: Modify src/auth.py to fix token validation.\nTest coverage: tests/test_auth.py - test_validate_token()",
            "[TOOL: Edit]\nfile_path: src/auth.py\nold_string: old\nnew_string: new",
        ]
        result = extract_current_assistant_message(messages)
        assert "INTENT:" in result
        assert "src/auth.py" in result
        assert "[TOOL: Edit]" in result
        assert "Earlier user message" not in result

    def test_intent_lowercase_in_previous(self):
        """Case-insensitive intent: marker detection."""
        messages = [
            "context",
            "intent: modify src/foo.py to add bar.\nTest coverage: tests/t.py - test_bar()",
            "[TOOL: Write]\nfile_path: src/foo.py\ncontent: bar",
        ]
        result = extract_current_assistant_message(messages)
        assert "intent:" in result
        assert "[TOOL: Write]" in result

    def test_intent_mixed_case_in_previous(self):
        """Mixed case 'Intent:' should be detected."""
        messages = [
            "context",
            "Intent: Modify src/x.py to refactor.\nTest coverage: tests/t.py - test_x()",
            "[TOOL: Edit]\nfile_path: src/x.py",
        ]
        result = extract_current_assistant_message(messages)
        assert "Intent:" in result
        assert "[TOOL: Edit]" in result

    def test_intent_with_prefix_text_in_previous(self):
        """Intent marker preceded by other text (e.g. § line or analysis)."""
        messages = [
            "context",
            (
                "§ △0.3 ◎surg ■bug ◇0.7 ↻2\n"
                "I'll fix the validation logic.\n\n"
                "INTENT: Modify src/validator.py to fix regex.\n"
                "Test coverage: tests/test_validator.py - test_regex()"
            ),
            "[TOOL: Edit]\nfile_path: src/validator.py",
        ]
        result = extract_current_assistant_message(messages)
        assert "INTENT:" in result
        assert "[TOOL: Edit]" in result

    def test_combined_output_has_both_parts_separated(self):
        """Combined message should have double-newline separator."""
        intent_msg = (
            "INTENT: Modify a.py to add func.\nTest coverage: tests/t.py - test_func()"
        )
        tool_msg = "[TOOL: Write]\nfile_path: a.py"
        messages = ["context", intent_msg, tool_msg]
        result = extract_current_assistant_message(messages)
        assert f"{intent_msg}\n\n{tool_msg}" == result


# ---------------------------------------------------------------------------
# No intent — should NOT merge backward
# ---------------------------------------------------------------------------


class TestNoIntentNoCombine:
    def test_no_intent_in_previous_returns_only_last(self):
        """When messages[-2] has no intent marker, return only messages[-1]."""
        messages = [
            "I'll create the file now.",
            "[TOOL: Write]\nfile_path: src/new.py\ncontent: print('hello')",
        ]
        result = extract_current_assistant_message(messages)
        assert result == messages[-1]
        assert "I'll create" not in result

    def test_analysis_without_intent_not_merged(self):
        """Analysis text without INTENT: should not be pulled in."""
        messages = [
            "user question",
            "Let me analyze the code structure. The function uses...",
            "[TOOL: Read]\nfile_path: src/main.py",
        ]
        result = extract_current_assistant_message(messages)
        assert result == messages[-1]
        assert "analyze" not in result


# ---------------------------------------------------------------------------
# Stale intent — must NOT reach back further than 1
# ---------------------------------------------------------------------------


class TestStaleIntentNotPickedUp:
    def test_stale_intent_two_messages_back_ignored(self):
        """Intent in messages[-3] must NOT be picked up — only 1-back."""
        messages = [
            "INTENT: Modify stale_file.py (from 2 turns ago)",
            "Some intermediate message without intent",
            "[TOOL: Edit]\nfile_path: current_file.py",
        ]
        result = extract_current_assistant_message(messages)
        assert "stale_file.py" not in result
        assert result == messages[-1]

    def test_stale_intent_three_messages_back_ignored(self):
        """Intent in messages[-4] must NOT be picked up."""
        messages = [
            "INTENT: Modify ancient.py (very old)",
            "intermediate 1",
            "intermediate 2 without intent",
            "[TOOL: Edit]\nfile_path: current.py",
        ]
        result = extract_current_assistant_message(messages)
        assert "ancient.py" not in result
        assert result == messages[-1]

    def test_only_immediate_previous_checked(self):
        """Even with intent in messages[-3] and no intent in messages[-2], don't reach back."""
        messages = [
            "INTENT: Modify old.py to do something old.",
            "I need to read a file first.",
            "[TOOL: Write]\nfile_path: new.py\ncontent: new stuff",
        ]
        result = extract_current_assistant_message(messages)
        assert "old.py" not in result
        assert result == messages[-1]


# ---------------------------------------------------------------------------
# Two-message list (minimum for backward check)
# ---------------------------------------------------------------------------


class TestTwoMessageList:
    def test_two_messages_with_intent(self):
        """Exactly 2 messages: intent in [-2], tool in [-1] — should combine."""
        messages = [
            "INTENT: Modify src/x.py to fix bug.\nTest coverage: tests/t.py - test()",
            "[TOOL: Edit]\nfile_path: src/x.py",
        ]
        result = extract_current_assistant_message(messages)
        assert "INTENT:" in result
        assert "[TOOL: Edit]" in result

    def test_two_messages_without_intent(self):
        """Exactly 2 messages: no intent in [-2] — return only [-1]."""
        messages = [
            "I will now write the file.",
            "[TOOL: Write]\nfile_path: test.py\ncontent: pass",
        ]
        result = extract_current_assistant_message(messages)
        assert result == messages[-1]
        assert "I will now" not in result
