#!/usr/bin/env python3
"""
Tests for PostToolUse hook with subagent context.

AC4: PostToolUse should detect subagent context and use subagent's state file
"""

import json
import pytest
from unittest.mock import patch
from pathlib import Path

from pacemaker.hook import run_hook


@pytest.fixture
def mock_config_langfuse_enabled():
    """Config with Langfuse enabled."""
    return {
        "enabled": True,
        "langfuse_enabled": True,
        "langfuse_base_url": "https://langfuse.example.com",
        "langfuse_public_key": "pk-test-123",
        "langfuse_secret_key": "sk-test-456",
        "subagent_reminder_enabled": False,
    }


@pytest.fixture
def mock_hook_data_subagent_posttooluse():
    """Hook data for PostToolUse in subagent context."""
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": "Read",
        "session_id": "agent-abc123",  # Subagent session ID
        "transcript_path": "/tmp/projects/agent-abc123.jsonl",  # Subagent transcript
    }


def test_posttooluse_calls_incremental_push_for_subagent(
    mock_config_langfuse_enabled, mock_hook_data_subagent_posttooluse
):
    """
    AC4: PostToolUse should call handle_post_tool_use with subagent's session_id and transcript.

    Verifies:
    - Hook reads subagent session_id and transcript_path from stdin
    - Calls orchestrator.handle_post_tool_use() with subagent's data
    - Uses subagent's state file (via session_id) for span creation
    - This enables independent state tracking per subagent (AC2 + AC4)
    """
    hook_data_json = json.dumps(mock_hook_data_subagent_posttooluse)

    with (
        patch("pacemaker.hook.load_config", return_value=mock_config_langfuse_enabled),
        patch(
            "pacemaker.hook.load_state",
            return_value={
                "session_id": "main-123",
                "in_subagent": True,
                "subagent_counter": 1,
                "tool_execution_count": 0,
            },
        ),
        patch("pacemaker.hook.save_state"),
        patch("sys.stdin.read", return_value=hook_data_json),
        patch("pacemaker.hook.database.initialize_database"),
        patch(
            "pacemaker.hook.pacing_engine.run_pacing_check",
            return_value={"polled": False, "decision": {}},
        ),
        patch("pacemaker.langfuse.orchestrator.handle_post_tool_use") as mock_handle,
    ):

        # Run PostToolUse hook
        run_hook()

        # Verify handle_post_tool_use was called with subagent's session_id and transcript
        mock_handle.assert_called_once()
        call_kwargs = mock_handle.call_args[1]

        # AC4: Verify subagent session_id is used (enables subagent state file lookup)
        assert (
            call_kwargs["session_id"] == "agent-abc123"
        ), "Should use subagent session_id"

        # AC4: Verify subagent transcript path is used
        assert (
            call_kwargs["transcript_path"] == "/tmp/projects/agent-abc123.jsonl"
        ), "Should use subagent transcript"

        # Verify state directory
        assert str(call_kwargs["state_dir"]) == str(
            Path.home() / ".claude-pace-maker/langfuse_state"
        )
