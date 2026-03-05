#!/usr/bin/env python3
"""
Unit tests for intent_validator module.

Tests prompt template loading from organized subfolders.
"""


def test_get_pre_tool_prompt_template_uses_new_path():
    """Test that pre-tool prompt is loaded from pre_tool_use/ subfolder."""
    from pacemaker.intent_validator import get_pre_tool_prompt_template

    # Execute: Load template
    template = get_pre_tool_prompt_template()

    # Assert: Template loaded successfully (would raise FileNotFoundError if path wrong)
    assert template is not None
    assert isinstance(template, str)
    assert len(template) > 0
    # Verify it contains expected content
    assert "OUTCOME" in template or "tool_name" in template


def test_get_stop_hook_prompt_template_uses_new_path():
    """Test that stop hook prompt is loaded from stop/ subfolder."""
    from pacemaker.intent_validator import get_prompt_template

    # Execute: Load template
    template = get_prompt_template()

    # Assert: Template loaded successfully
    assert template is not None
    assert isinstance(template, str)
    assert len(template) > 0
    # Verify it contains expected content
    assert "APPROVED" in template or "BLOCKED" in template


def test_build_intent_declaration_prompt_uses_external_template():
    """Test that intent declaration prompt uses external template with variables."""
    from pacemaker.intent_validator import _build_intent_declaration_prompt

    # Setup: Test data
    messages = ["Message 1: Test message", "Message 2: Another message"]
    file_path = "src/test.py"
    tool_name = "Write"

    # Execute: Build prompt
    prompt = _build_intent_declaration_prompt(messages, file_path, tool_name)

    # Assert: Prompt contains replaced variables
    assert "test.py" in prompt  # filename extracted
    assert "Write" in prompt  # tool_name
    assert "Message 1: Test message" in prompt  # messages included
    assert "create or modify" in prompt  # action for Write tool
    # Verify template structure present
    assert "intent" in prompt.lower()
    assert "declared" in prompt.lower()


# --- Tests for validate_intent_declared fail-open behavior ---


def test_validate_intent_declaration_fails_open_on_empty_response():
    """Infrastructure failure (empty response) must fail-open: intent_found=True."""
    from unittest.mock import patch
    from pacemaker.intent_validator import validate_intent_declared

    with (
        patch(
            "pacemaker.intent_validator._call_sdk_intent_validation", return_value=""
        ),
        patch(
            "pacemaker.intent_validator._build_intent_declaration_prompt",
            return_value="dummy prompt",
        ),
    ):
        result = validate_intent_declared(["some message"], "src/foo.py", "Write")

    assert result["intent_found"] is True


def test_validate_intent_declaration_fails_open_on_exception():
    """Exception from SDK must fail-open: intent_found=True."""
    from unittest.mock import patch
    from pacemaker.intent_validator import validate_intent_declared

    with (
        patch(
            "pacemaker.intent_validator._call_sdk_intent_validation",
            side_effect=Exception("Connection refused"),
        ),
        patch(
            "pacemaker.intent_validator._build_intent_declaration_prompt",
            return_value="dummy prompt",
        ),
    ):
        result = validate_intent_declared(["some message"], "src/foo.py", "Write")

    assert result["intent_found"] is True


def test_validate_intent_declaration_blocks_on_explicit_no():
    """Explicit NO from LLM must block: intent_found=False."""
    from unittest.mock import patch
    from pacemaker.intent_validator import validate_intent_declared

    with (
        patch(
            "pacemaker.intent_validator._call_sdk_intent_validation", return_value="NO"
        ),
        patch(
            "pacemaker.intent_validator._build_intent_declaration_prompt",
            return_value="dummy prompt",
        ),
    ):
        result = validate_intent_declared(["some message"], "src/foo.py", "Write")

    assert result["intent_found"] is False


def test_validate_intent_declaration_passes_on_yes():
    """YES from LLM must pass: intent_found=True."""
    from unittest.mock import patch
    from pacemaker.intent_validator import validate_intent_declared

    with (
        patch(
            "pacemaker.intent_validator._call_sdk_intent_validation", return_value="YES"
        ),
        patch(
            "pacemaker.intent_validator._build_intent_declaration_prompt",
            return_value="dummy prompt",
        ),
    ):
        result = validate_intent_declared(["some message"], "src/foo.py", "Write")

    assert result["intent_found"] is True


def test_validate_intent_declaration_blocks_on_unexpected_response():
    """Unexpected non-empty response must block: intent_found=False."""
    from unittest.mock import patch
    from pacemaker.intent_validator import validate_intent_declared

    with (
        patch(
            "pacemaker.intent_validator._call_sdk_intent_validation",
            return_value="MAYBE",
        ),
        patch(
            "pacemaker.intent_validator._build_intent_declaration_prompt",
            return_value="dummy prompt",
        ),
    ):
        result = validate_intent_declared(["some message"], "src/foo.py", "Write")

    assert result["intent_found"] is False
