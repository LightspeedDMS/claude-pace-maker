#!/usr/bin/env python3
"""
Tests for hook.py integration with trace-per-turn Langfuse handlers.

Verifies that hook.py correctly calls the new orchestrator functions:
- handle_user_prompt_submit() on UserPromptSubmit
- handle_post_tool_use() on PostToolUse

Tests the integration points without requiring full hook execution.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from pacemaker import hook


# Shared fixtures for both test classes
@pytest.fixture
def config_file():
    """Create temporary config file with Langfuse enabled."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        config = {
            "enabled": True,
            "langfuse_enabled": True,
            "langfuse_base_url": "https://cloud.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }
        json.dump(config, f)
        config_path = f.name

    yield config_path

    # Cleanup
    Path(config_path).unlink()


@pytest.fixture
def state_file():
    """Create temporary state file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        state = {
            "session_id": "test-session",
            "subagent_counter": 0,
            "in_subagent": False,
            "tool_execution_count": 0,
        }
        json.dump(state, f)
        state_path = f.name

    yield state_path

    # Cleanup
    Path(state_path).unlink()


@pytest.fixture
def transcript_file():
    """Create temporary transcript file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps({"type": "session_start", "session_id": "test-123"}) + "\n")
        f.write(json.dumps({"message": {"role": "user", "content": "Hello"}}) + "\n")
        transcript_path = f.name

    yield transcript_path

    # Cleanup
    Path(transcript_path).unlink()


@pytest.fixture
def db_file():
    """Create temporary database file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    yield db_path

    # Cleanup
    Path(db_path).unlink(missing_ok=True)


class TestUserPromptSubmitTracePerTurnIntegration:
    """Test UserPromptSubmit hook integration with trace-per-turn handler."""

    def test_user_prompt_submit_calls_handle_user_prompt_submit(
        self, config_file, state_file, transcript_file, monkeypatch
    ):
        """
        Test that UserPromptSubmit hook calls handle_user_prompt_submit().

        Verifies:
        - Correct function called (handle_user_prompt_submit, not run_incremental_push)
        - Parameters extracted correctly from hook data
        - User message text passed to handler
        """
        # Patch constants
        monkeypatch.setattr("pacemaker.hook.DEFAULT_CONFIG_PATH", config_file)
        monkeypatch.setattr("pacemaker.hook.DEFAULT_STATE_PATH", state_file)

        # Mock stdin with JSON hook data
        hook_data = {
            "session_id": "test-session-123",
            "transcript_path": transcript_file,
            "prompt": "Write a test function",
        }
        mock_stdin = MagicMock()
        mock_stdin.read.return_value = json.dumps(hook_data)

        # Mock handle_user_prompt_submit
        # Note: Must mock get_transcript_path because hook derives path from session_id
        with (
            patch("sys.stdin", mock_stdin),
            patch(
                "pacemaker.hook.user_commands.handle_user_prompt"
            ) as mock_user_commands,
            patch(
                "pacemaker.langfuse.orchestrator.handle_user_prompt_submit"
            ) as mock_handler,
            patch("pacemaker.hook.get_transcript_path", return_value=transcript_file),
            patch("sys.exit"),
        ):

            # Setup mocks
            mock_user_commands.return_value = {"intercepted": False, "output": ""}
            mock_handler.return_value = True

            # Run hook
            hook.run_user_prompt_submit()

            # Verify handle_user_prompt_submit was called (NOT run_incremental_push)
            assert mock_handler.called
            call_kwargs = mock_handler.call_args[1]

            # Verify parameters
            assert call_kwargs["session_id"] == "test-session-123"
            assert call_kwargs["transcript_path"] == transcript_file
            assert call_kwargs["user_message"] == "Write a test function"
            assert "config" in call_kwargs
            assert "state_dir" in call_kwargs

    def test_user_prompt_submit_handles_plain_text_input(
        self, config_file, state_file, monkeypatch
    ):
        """
        Test UserPromptSubmit handles plain text (non-JSON) input.

        Verifies that handler is NOT called when transcript_path unavailable
        (plain text input doesn't include transcript_path).
        """
        # Patch constants
        monkeypatch.setattr("pacemaker.hook.DEFAULT_CONFIG_PATH", config_file)
        monkeypatch.setattr("pacemaker.hook.DEFAULT_STATE_PATH", state_file)

        # Mock stdin with plain text (not JSON)
        mock_stdin = MagicMock()
        mock_stdin.read.return_value = "Write a simple test"

        # Mock handle_user_prompt_submit
        with (
            patch("sys.stdin", mock_stdin),
            patch(
                "pacemaker.hook.user_commands.handle_user_prompt"
            ) as mock_user_commands,
            patch(
                "pacemaker.langfuse.orchestrator.handle_user_prompt_submit"
            ) as mock_handler,
            patch("sys.exit"),
        ):

            # Setup mocks
            mock_user_commands.return_value = {"intercepted": False, "output": ""}
            mock_handler.return_value = True

            # Run hook
            hook.run_user_prompt_submit()

            # Verify handle_user_prompt_submit was NOT called (no transcript_path in plain text)
            assert not mock_handler.called

    def test_user_prompt_submit_skips_handler_for_pace_maker_commands(
        self, config_file, state_file, transcript_file, monkeypatch
    ):
        """
        Test UserPromptSubmit skips handler for intercepted pace-maker commands.

        Verifies that pace-maker commands don't trigger Langfuse push.
        """
        # Patch constants
        monkeypatch.setattr("pacemaker.hook.DEFAULT_CONFIG_PATH", config_file)
        monkeypatch.setattr("pacemaker.hook.DEFAULT_STATE_PATH", state_file)

        # Mock stdin with pace-maker command
        hook_data = {
            "session_id": "test-session-123",
            "transcript_path": transcript_file,
            "prompt": "pace-maker status",
        }
        mock_stdin = MagicMock()
        mock_stdin.read.return_value = json.dumps(hook_data)

        # Mock handle_user_prompt_submit
        with (
            patch("sys.stdin", mock_stdin),
            patch(
                "pacemaker.hook.user_commands.handle_user_prompt"
            ) as mock_user_commands,
            patch(
                "pacemaker.langfuse.orchestrator.handle_user_prompt_submit"
            ) as mock_handler,
            patch("sys.exit"),
        ):

            # Setup mocks - command intercepted
            mock_user_commands.return_value = {
                "intercepted": True,
                "output": "Status: enabled",
            }
            mock_handler.return_value = True

            # Run hook
            hook.run_user_prompt_submit()

            # Verify handle_user_prompt_submit was NOT called
            assert not mock_handler.called

    def test_user_prompt_submit_graceful_failure_on_handler_error(
        self, config_file, state_file, transcript_file, monkeypatch
    ):
        """
        Test UserPromptSubmit continues on handler error (graceful failure).

        Verifies AC5: Non-blocking behavior on Langfuse errors.
        """
        # Patch constants
        monkeypatch.setattr("pacemaker.hook.DEFAULT_CONFIG_PATH", config_file)
        monkeypatch.setattr("pacemaker.hook.DEFAULT_STATE_PATH", state_file)

        # Mock stdin
        hook_data = {
            "session_id": "test-session-123",
            "transcript_path": transcript_file,
            "prompt": "Test prompt",
        }
        mock_stdin = MagicMock()
        mock_stdin.read.return_value = json.dumps(hook_data)

        # Mock handle_user_prompt_submit to raise exception
        with (
            patch("sys.stdin", mock_stdin),
            patch(
                "pacemaker.hook.user_commands.handle_user_prompt"
            ) as mock_user_commands,
            patch(
                "pacemaker.langfuse.orchestrator.handle_user_prompt_submit"
            ) as mock_handler,
            patch("sys.exit") as mock_exit,
        ):

            # Setup mocks
            mock_user_commands.return_value = {"intercepted": False, "output": ""}
            mock_handler.side_effect = Exception("Langfuse API error")

            # Run hook (should not raise exception)
            hook.run_user_prompt_submit()

            # Verify hook completed (called exit)
            assert mock_exit.called


class TestPostToolUseTracePerTurnIntegration:
    """Test PostToolUse hook integration with trace-per-turn handler."""

    def test_post_tool_use_calls_handle_post_tool_use(
        self, config_file, state_file, db_file, transcript_file, monkeypatch
    ):
        """
        Test that PostToolUse hook calls handle_post_tool_use().

        Verifies:
        - Correct function called (handle_post_tool_use, not run_incremental_push)
        - Tool parameters extracted from hook data
        - Tool name, input, and output passed correctly
        """
        # Patch constants
        monkeypatch.setattr("pacemaker.hook.DEFAULT_CONFIG_PATH", config_file)
        monkeypatch.setattr("pacemaker.hook.DEFAULT_STATE_PATH", state_file)
        monkeypatch.setattr("pacemaker.hook.DEFAULT_DB_PATH", db_file)

        # Mock stdin with PostToolUse hook data
        hook_data = {
            "session_id": "test-session-456",
            "transcript_path": transcript_file,
            "tool_name": "Read",
            "tool_input": {"file_path": "/home/user/test.py"},
            "tool_output": "File contents here",
        }
        mock_stdin = MagicMock()
        mock_stdin.read.return_value = json.dumps(hook_data)

        # Mock handle_post_tool_use
        with (
            patch("sys.stdin", mock_stdin),
            patch("pacemaker.pacing_engine.run_pacing_check") as mock_pacing,
            patch(
                "pacemaker.langfuse.orchestrator.handle_post_tool_use"
            ) as mock_handler,
        ):

            # Setup mocks
            mock_pacing.return_value = {"decision": {"should_throttle": False}}
            mock_handler.return_value = True

            # Run hook
            hook.run_hook()

            # Verify handle_post_tool_use was called (NOT run_incremental_push)
            assert mock_handler.called
            call_kwargs = mock_handler.call_args[1]

            # Verify parameters (refactored API - parses transcript instead of tool params)
            assert call_kwargs["session_id"] == "test-session-456"
            assert call_kwargs["transcript_path"] == transcript_file
            assert "config" in call_kwargs
            assert "state_dir" in call_kwargs

    def test_post_tool_use_handles_different_tools(
        self, config_file, state_file, db_file, transcript_file, monkeypatch
    ):
        """
        Test PostToolUse correctly extracts parameters for different tools.

        Verifies tool-specific parameter extraction (Write, Edit, Bash, etc).
        """
        # Patch constants
        monkeypatch.setattr("pacemaker.hook.DEFAULT_CONFIG_PATH", config_file)
        monkeypatch.setattr("pacemaker.hook.DEFAULT_STATE_PATH", state_file)
        monkeypatch.setattr("pacemaker.hook.DEFAULT_DB_PATH", db_file)

        # Test with Write tool
        hook_data = {
            "session_id": "test-session-789",
            "transcript_path": transcript_file,
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/home/user/new.py",
                "content": "print('hello')",
            },
            "tool_output": "File written successfully",
        }
        mock_stdin = MagicMock()
        mock_stdin.read.return_value = json.dumps(hook_data)

        with (
            patch("sys.stdin", mock_stdin),
            patch("pacemaker.pacing_engine.run_pacing_check") as mock_pacing,
            patch(
                "pacemaker.langfuse.orchestrator.handle_post_tool_use"
            ) as mock_handler,
        ):

            # Setup mocks
            mock_pacing.return_value = {"decision": {"should_throttle": False}}
            mock_handler.return_value = True

            # Run hook
            hook.run_hook()

            # Verify correct parameters (refactored API - parses transcript)
            assert mock_handler.called
            call_kwargs = mock_handler.call_args[1]
            assert call_kwargs["session_id"] == "test-session-789"
            assert call_kwargs["transcript_path"] == transcript_file

    def test_post_tool_use_graceful_failure_on_handler_error(
        self, config_file, state_file, db_file, transcript_file, monkeypatch
    ):
        """
        Test PostToolUse continues on handler error (graceful failure).

        Verifies AC5: Non-blocking behavior on Langfuse errors.
        """
        # Patch constants
        monkeypatch.setattr("pacemaker.hook.DEFAULT_CONFIG_PATH", config_file)
        monkeypatch.setattr("pacemaker.hook.DEFAULT_STATE_PATH", state_file)
        monkeypatch.setattr("pacemaker.hook.DEFAULT_DB_PATH", db_file)

        # Mock stdin
        hook_data = {
            "session_id": "test-session-error",
            "transcript_path": transcript_file,
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "tool_output": "file1.txt file2.txt",
        }
        mock_stdin = MagicMock()
        mock_stdin.read.return_value = json.dumps(hook_data)

        # Mock handle_post_tool_use to raise exception
        with (
            patch("sys.stdin", mock_stdin),
            patch("pacemaker.pacing_engine.run_pacing_check") as mock_pacing,
            patch(
                "pacemaker.langfuse.orchestrator.handle_post_tool_use"
            ) as mock_handler,
        ):

            # Setup mocks
            mock_pacing.return_value = {"decision": {"should_throttle": False}}
            mock_handler.side_effect = Exception("Langfuse span creation failed")

            # Run hook (should not raise exception)
            result = hook.run_hook()

            # Verify hook completed successfully despite error
            assert result is False  # No feedback provided

    def test_post_tool_use_skips_handler_when_missing_session_id(
        self, config_file, state_file, db_file, monkeypatch
    ):
        """
        Test PostToolUse skips handler when session_id is missing.

        Verifies defensive handling of incomplete hook data.
        """
        # Patch constants
        monkeypatch.setattr("pacemaker.hook.DEFAULT_CONFIG_PATH", config_file)
        monkeypatch.setattr("pacemaker.hook.DEFAULT_STATE_PATH", state_file)
        monkeypatch.setattr("pacemaker.hook.DEFAULT_DB_PATH", db_file)

        # Mock stdin with incomplete hook data (missing session_id)
        hook_data = {
            # Missing session_id
            "tool_name": "Read",
            "tool_input": {"file_path": "/test.py"},
            "tool_output": "content",
        }
        mock_stdin = MagicMock()
        mock_stdin.read.return_value = json.dumps(hook_data)

        with (
            patch("sys.stdin", mock_stdin),
            patch("pacemaker.pacing_engine.run_pacing_check") as mock_pacing,
            patch(
                "pacemaker.langfuse.orchestrator.handle_post_tool_use"
            ) as mock_handler,
        ):

            # Setup mocks
            mock_pacing.return_value = {"decision": {"should_throttle": False}}
            mock_handler.return_value = True

            # Run hook
            hook.run_hook()

            # Verify handle_post_tool_use was NOT called (no session_id)
            assert not mock_handler.called
