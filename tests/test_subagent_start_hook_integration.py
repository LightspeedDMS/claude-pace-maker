#!/usr/bin/env python3
"""
Tests for SubagentStart hook integration with Langfuse child span creation.

AC3: SubagentStart hook should call handle_subagent_start() when Langfuse enabled
"""

import json
import pytest
from unittest.mock import patch
from pathlib import Path

from pacemaker.hook import run_subagent_start_hook


@pytest.fixture
def mock_config_langfuse_enabled():
    """Config with Langfuse enabled."""
    return {
        "enabled": True,
        "langfuse_enabled": True,
        "langfuse_base_url": "https://langfuse.example.com",
        "langfuse_public_key": "pk-test-123",
        "langfuse_secret_key": "sk-test-456",
        "intent_validation_enabled": False,
    }


@pytest.fixture
def mock_config_langfuse_disabled():
    """Config with Langfuse disabled."""
    return {
        "enabled": True,
        "langfuse_enabled": False,
        "intent_validation_enabled": False,
    }


@pytest.fixture
def mock_hook_data_subagent():
    """Hook data for SubagentStart."""
    return {
        "hook_event_name": "SubagentStart",
        "session_id": "main-session-123",  # Parent's session ID (Claude Code's naming)
        "agent_id": "agent-abc123",  # Subagent's identifier
        "transcript_path": "/tmp/projects/main-session-123.jsonl",  # Parent's transcript
        "parent_session_id": "main-session-123",  # Redundant but kept for compatibility
        "parent_observation_id": "obs-task-456",
        "agent_type": "code-reviewer",  # Changed from subagent_name to agent_type
    }


def test_subagent_start_hook_calls_handle_subagent_start(
    mock_config_langfuse_enabled, mock_hook_data_subagent
):
    """
    AC3: SubagentStart hook should call orchestrator.handle_subagent_start() when Langfuse enabled.

    Verifies:
    - Hook reads stdin for subagent metadata
    - Calls orchestrator.handle_subagent_start() with correct parameters
    - Uses real API integration (no SimpleLangfuseClient stub)
    - Gracefully handles failures (doesn't crash)
    - Passes parent_transcript_path for prompt extraction
    """
    hook_data_json = json.dumps(mock_hook_data_subagent)

    with (
        patch("pacemaker.hook.load_config", return_value=mock_config_langfuse_enabled),
        patch(
            "pacemaker.hook.load_state",
            return_value={"in_subagent": False, "subagent_counter": 0},
        ),
        patch("pacemaker.hook.save_state"),
        patch("sys.stdin.read", return_value=hook_data_json),
        patch(
            "pacemaker.hook.get_transcript_path",
            return_value="/tmp/parent-transcript.jsonl",
        ),
        patch("pacemaker.langfuse.orchestrator.handle_subagent_start") as mock_handle,
        patch("pacemaker.hook.display_intent_validation_guidance", return_value=""),
    ):

        # Run hook
        run_subagent_start_hook()

        # Verify handle_subagent_start was called with correct parameters
        mock_handle.assert_called_once()
        call_args = mock_handle.call_args[1]  # Get keyword arguments

        # Verify new orchestrator signature (config instead of client)
        assert call_args["config"] == mock_config_langfuse_enabled
        assert str(call_args["state_dir"]) == str(
            Path.home() / ".claude-pace-maker/langfuse_state"
        )
        # NEW: Hook adds "subagent-" prefix to agent_id for session_id
        assert call_args["subagent_session_id"] == "subagent-agent-abc123"
        assert call_args["parent_session_id"] == "main-session-123"
        assert call_args["subagent_name"] == "code-reviewer"
        # Hook uses transcript_path from hook_data (not get_transcript_path)
        assert (
            call_args["parent_transcript_path"]
            == "/tmp/projects/main-session-123.jsonl"
        )


def test_subagent_start_hook_skips_when_langfuse_disabled(
    mock_config_langfuse_disabled, mock_hook_data_subagent
):
    """
    AC3: SubagentStart hook should NOT call handle_subagent_start() when Langfuse disabled.

    Verifies graceful skip when feature disabled.
    """
    hook_data_json = json.dumps(mock_hook_data_subagent)

    with (
        patch("pacemaker.hook.load_config", return_value=mock_config_langfuse_disabled),
        patch(
            "pacemaker.hook.load_state",
            return_value={"in_subagent": False, "subagent_counter": 0},
        ),
        patch("pacemaker.hook.save_state"),
        patch("sys.stdin.read", return_value=hook_data_json),
        patch("pacemaker.langfuse.orchestrator.handle_subagent_start") as mock_handle,
        patch("pacemaker.hook.display_intent_validation_guidance", return_value=""),
    ):

        # Run hook
        run_subagent_start_hook()

        # Verify handle_subagent_start was NOT called
        mock_handle.assert_not_called()


def test_subagent_start_hook_graceful_failure_on_exception(
    mock_config_langfuse_enabled, mock_hook_data_subagent
):
    """
    AC3: SubagentStart hook should handle exceptions gracefully.

    Verifies:
    - Hook doesn't crash on Langfuse errors
    - State tracking still works (counter increment)
    - Intent validation guidance still displays
    """
    hook_data_json = json.dumps(mock_hook_data_subagent)

    with (
        patch("pacemaker.hook.load_config", return_value=mock_config_langfuse_enabled),
        patch(
            "pacemaker.hook.load_state",
            return_value={"in_subagent": False, "subagent_counter": 0},
        ),
        patch("pacemaker.hook.save_state") as mock_save_state,
        patch("sys.stdin.read", return_value=hook_data_json),
        patch(
            "pacemaker.langfuse.orchestrator.handle_subagent_start",
            side_effect=Exception("Langfuse error"),
        ),
        patch(
            "pacemaker.hook.display_intent_validation_guidance", return_value="guidance"
        ),
        patch("sys.stdout.write"),
    ):

        # Run hook - should not raise exception (graceful failure)
        run_subagent_start_hook()

        # Verify state was still saved (counter incremented)
        mock_save_state.assert_called_once()
        saved_state = mock_save_state.call_args[0][0]
        assert saved_state["subagent_counter"] == 1
        assert saved_state["in_subagent"] is True


def test_subagent_start_hook_skips_when_no_stdin_data(mock_config_langfuse_enabled):
    """
    AC3: SubagentStart hook should handle missing stdin data gracefully.

    Verifies hook continues with state tracking when stdin is empty.
    """
    with (
        patch("pacemaker.hook.load_config", return_value=mock_config_langfuse_enabled),
        patch(
            "pacemaker.hook.load_state",
            return_value={"in_subagent": False, "subagent_counter": 0},
        ),
        patch("pacemaker.hook.save_state") as mock_save_state,
        patch("sys.stdin.read", return_value=""),
        patch("pacemaker.langfuse.orchestrator.handle_subagent_start") as mock_handle,
        patch("pacemaker.hook.display_intent_validation_guidance", return_value=""),
    ):

        # Run hook
        run_subagent_start_hook()

        # Verify handle_subagent_start was NOT called (no data)
        mock_handle.assert_not_called()

        # But state tracking still works
        mock_save_state.assert_called_once()
        saved_state = mock_save_state.call_args[0][0]
        assert saved_state["subagent_counter"] == 1
