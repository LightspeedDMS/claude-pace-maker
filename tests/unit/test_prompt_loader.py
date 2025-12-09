#!/usr/bin/env python3
"""
Unit tests for PromptLoader.

Tests prompt loading from organized and legacy locations with template variable replacement.
"""

import pytest


def test_load_prompt_from_organized_location(tmp_path):
    """Test loading prompt from organized subfolder location."""
    # Import here to avoid issues if module doesn't exist yet
    from pacemaker.prompt_loader import PromptLoader

    # Setup: Create test prompt structure
    prompts_dir = tmp_path / "prompts"
    session_start_dir = prompts_dir / "session_start"
    session_start_dir.mkdir(parents=True)

    # Write test prompt file
    prompt_file = session_start_dir / "test_guidance.md"
    prompt_file.write_text("# Test Guidance\nThis is test content.")

    # Execute: Load prompt from organized location
    loader = PromptLoader(prompts_base_dir=str(prompts_dir))
    content = loader.load_prompt("test_guidance.md", subfolder="session_start")

    # Assert: Content matches file content
    assert content == "# Test Guidance\nThis is test content."


def test_load_prompt_with_template_variables(tmp_path):
    """Test template variable replacement in prompts."""
    from pacemaker.prompt_loader import PromptLoader

    # Setup: Create prompt with template variables
    prompts_dir = tmp_path / "prompts"
    common_dir = prompts_dir / "common"
    common_dir.mkdir(parents=True)

    prompt_file = common_dir / "test_template.md"
    prompt_file.write_text("File: {{file_path}}\nTool: {{tool_name}}")

    # Execute: Load with variables
    loader = PromptLoader(prompts_base_dir=str(prompts_dir))
    content = loader.load_prompt(
        "test_template.md",
        subfolder="common",
        variables={"file_path": "src/test.py", "tool_name": "Write"},
    )

    # Assert: Variables replaced
    assert content == "File: src/test.py\nTool: Write"


def test_load_prompt_fallback_to_legacy(tmp_path):
    """Test fallback to legacy root location."""
    from pacemaker.prompt_loader import PromptLoader

    # Setup: Create prompt only in legacy location
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    legacy_file = prompts_dir / "legacy_prompt.md"
    legacy_file.write_text("Legacy content")

    # Execute: Load with subfolder specified but file only in legacy location
    loader = PromptLoader(prompts_base_dir=str(prompts_dir))
    content = loader.load_prompt("legacy_prompt.md", subfolder="session_start")

    # Assert: Content loaded from legacy location
    assert content == "Legacy content"


def test_load_prompt_missing_file_error(tmp_path):
    """Test clear error when prompt file is missing."""
    from pacemaker.prompt_loader import PromptLoader

    # Setup: Empty prompts directory
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    # Execute & Assert: FileNotFoundError raised
    loader = PromptLoader(prompts_base_dir=str(prompts_dir))
    with pytest.raises(FileNotFoundError) as exc_info:
        loader.load_prompt("missing.md", subfolder="session_start")

    # Assert: Error message includes paths
    assert "missing.md" in str(exc_info.value)
    assert "session_start" in str(exc_info.value)


def test_load_prompt_unreplaced_placeholder_error(tmp_path):
    """Test error when template has unreplaced placeholders."""
    from pacemaker.prompt_loader import PromptLoader

    # Setup: Create prompt with placeholders
    prompts_dir = tmp_path / "prompts"
    common_dir = prompts_dir / "common"
    common_dir.mkdir(parents=True)

    prompt_file = common_dir / "test_template.md"
    prompt_file.write_text("File: {{file_path}}\nTool: {{tool_name}}")

    # Execute & Assert: ValueError raised for missing variable
    loader = PromptLoader(prompts_base_dir=str(prompts_dir))
    with pytest.raises(ValueError) as exc_info:
        loader.load_prompt(
            "test_template.md",
            subfolder="common",
            variables={"file_path": "src/test.py"},  # Missing tool_name
        )

    # Assert: Error message mentions unreplaced placeholder
    assert "tool_name" in str(exc_info.value)
    assert "unreplaced" in str(exc_info.value).lower()


def test_load_json_messages(tmp_path):
    """Test loading JSON messages file."""
    from pacemaker.prompt_loader import PromptLoader

    # Setup: Create JSON messages file
    prompts_dir = tmp_path / "prompts"
    user_commands_dir = prompts_dir / "user_commands"
    user_commands_dir.mkdir(parents=True)

    messages_file = user_commands_dir / "messages.json"
    messages_file.write_text(
        '{"success": "Operation succeeded", "error": "Operation failed"}'
    )

    # Execute: Load JSON messages
    loader = PromptLoader(prompts_base_dir=str(prompts_dir))
    messages = loader.load_json_messages("messages.json", subfolder="user_commands")

    # Assert: Messages loaded correctly
    assert messages == {"success": "Operation succeeded", "error": "Operation failed"}


def test_load_json_messages_missing_file_error(tmp_path):
    """Test error when JSON messages file is missing."""
    from pacemaker.prompt_loader import PromptLoader

    # Setup: Empty prompts directory
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    # Execute & Assert: FileNotFoundError raised
    loader = PromptLoader(prompts_base_dir=str(prompts_dir))
    with pytest.raises(FileNotFoundError) as exc_info:
        loader.load_json_messages("messages.json", subfolder="user_commands")

    # Assert: Error message includes path
    assert "messages.json" in str(exc_info.value)
