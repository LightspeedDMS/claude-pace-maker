#!/usr/bin/env python3
"""
Test to verify the intent validation fix works correctly.

This test validates that extract_current_assistant_message() correctly
combines last 2 messages since Claude Code splits text and tool calls.
"""

import pytest
from pacemaker.intent_validator import extract_current_assistant_message


def test_extract_current_message_combines_last_two():
    """Verify extract_current_assistant_message combines last 2 messages."""
    messages = [
        "Message 1: Previous operation",
        "Message 2: I will modify test.py to add test_function()",
        "[TOOL: Write]\nfile_path: test.py\ncontent: ...",
    ]

    result = extract_current_assistant_message(messages)

    # Should contain BOTH the intent text AND tool info
    assert "I will modify test.py" in result
    assert "test_function" in result
    assert "[TOOL: Write]" in result
    assert "test.py" in result

    # Should NOT contain message 1
    assert "Message 1" not in result


def test_extract_current_message_with_single_message():
    """Verify function handles single message correctly."""
    messages = ["I will modify file.py to add function()"]

    result = extract_current_assistant_message(messages)

    assert result == messages[0]


def test_extract_current_message_empty_list():
    """Verify function handles empty list gracefully."""
    result = extract_current_assistant_message([])

    assert result == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
