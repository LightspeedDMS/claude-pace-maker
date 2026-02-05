#!/usr/bin/env python3
"""
Tests for SubagentStop hook finalization.

AC5: SubagentStop should finalize subagent span by flushing remaining lines
"""

import json
import pytest
from unittest.mock import patch

from pacemaker.hook import run_subagent_stop_hook


@pytest.fixture
def mock_config_langfuse_enabled():
    """Config with Langfuse enabled."""
    return {
        "enabled": True,
        "langfuse_enabled": True,
        "langfuse_base_url": "https://langfuse.example.com",
        "langfuse_public_key": "pk-test-123",
        "langfuse_secret_key": "sk-test-456",
    }


@pytest.fixture
def mock_hook_data_subagent_stop():
    """Hook data for SubagentStop."""
    return {
        "hook_event_name": "SubagentStop",
        "session_id": "agent-abc123",
        "agent_id": "agent-abc123",  # NEW: Added agent_id for dict lookup
        "transcript_path": "/tmp/projects/agent-abc123.jsonl",
        "agent_transcript_path": "/tmp/projects/agent-abc123.jsonl",  # NEW: Added for new signature
    }


def test_subagent_stop_finalizes_span(
    mock_config_langfuse_enabled, mock_hook_data_subagent_stop
):
    """
    AC5: SubagentStop should finalize subagent trace.

    Verifies:
    - Hook reads subagent session_id and transcript_path from stdin
    - Calls orchestrator.handle_subagent_stop() for trace finalization
    - Uses subagent's state file to get current_subagent_trace_id
    - Finalizes trace with subagent output from parent transcript
    """
    hook_data_json = json.dumps(mock_hook_data_subagent_stop)

    # State must include dict-based subagent_traces for finalization to run
    mock_state = {
        "in_subagent": True,
        "subagent_counter": 1,
        # NEW: Dict-based trace storage
        "subagent_traces": {
            "agent-abc123": {
                "trace_id": "trace-subagent-123",
                "parent_transcript_path": "/tmp/parent.jsonl",
            }
        },
        # OLD: Backward compat keys (still supported)
        "current_subagent_trace_id": "trace-subagent-123",
        "current_subagent_agent_id": "agent-abc123",
    }

    with (
        patch("pacemaker.hook.load_config", return_value=mock_config_langfuse_enabled),
        patch("pacemaker.hook.load_state", return_value=mock_state),
        patch("pacemaker.hook.save_state"),
        patch("sys.stdin.read", return_value=hook_data_json),
        patch(
            "pacemaker.hook.get_transcript_path",
            return_value="/tmp/projects/agent-abc123.jsonl",
        ),
        patch("pacemaker.langfuse.orchestrator.handle_subagent_stop") as mock_finalize,
    ):

        # Run SubagentStop hook
        run_subagent_stop_hook()

        # Verify handle_subagent_stop was called
        mock_finalize.assert_called_once()
        call_kwargs = mock_finalize.call_args[1]

        # AC5: Verify subagent trace_id is used for finalization
        assert call_kwargs["subagent_trace_id"] == "trace-subagent-123"

        # AC5: Verify parent transcript path is used (to extract subagent output)
        assert (
            call_kwargs["parent_transcript_path"] == "/tmp/projects/agent-abc123.jsonl"
        )

        # Verify agent_id is passed for output correlation
        assert call_kwargs["agent_id"] == "agent-abc123"

        # NEW: Verify agent_transcript_path is passed
        assert (
            call_kwargs["agent_transcript_path"] == "/tmp/projects/agent-abc123.jsonl"
        )

        # Verify config is passed
        assert call_kwargs["config"] == mock_config_langfuse_enabled


def test_subagent_stop_skips_finalization_when_langfuse_disabled(
    mock_hook_data_subagent_stop,
):
    """
    AC5: SubagentStop should skip finalization when Langfuse disabled.
    """
    config = {"enabled": True, "langfuse_enabled": False}
    hook_data_json = json.dumps(mock_hook_data_subagent_stop)

    # State includes dict-based trace storage but Langfuse is disabled
    mock_state = {
        "in_subagent": True,
        "subagent_counter": 1,
        # NEW: Dict-based trace storage
        "subagent_traces": {
            "agent-abc123": {
                "trace_id": "trace-subagent-123",
                "parent_transcript_path": "/tmp/parent.jsonl",
            }
        },
        # OLD: Backward compat keys
        "current_subagent_trace_id": "trace-subagent-123",
    }

    with (
        patch("pacemaker.hook.load_config", return_value=config),
        patch("pacemaker.hook.load_state", return_value=mock_state),
        patch("pacemaker.hook.save_state"),
        patch("sys.stdin.read", return_value=hook_data_json),
        patch("pacemaker.langfuse.orchestrator.handle_subagent_stop") as mock_finalize,
    ):

        # Run SubagentStop hook
        run_subagent_stop_hook()

        # Verify handle_subagent_stop was NOT called (Langfuse disabled)
        mock_finalize.assert_not_called()


def test_subagent_stop_handles_missing_stdin_data():
    """
    AC5: SubagentStop should handle missing stdin data gracefully.
    """
    config = {"enabled": True, "langfuse_enabled": True}

    # State includes trace_id but stdin is empty (hook_data will be None)
    mock_state = {
        "in_subagent": True,
        "subagent_counter": 1,
        "current_subagent_trace_id": "trace-subagent-123",
        "current_subagent_agent_id": "agent-xyz",
    }

    with (
        patch("pacemaker.hook.load_config", return_value=config),
        patch("pacemaker.hook.load_state", return_value=mock_state),
        patch("pacemaker.hook.save_state"),
        patch("sys.stdin.read", return_value=""),
        patch("pacemaker.hook.get_transcript_path", return_value=None),
        patch("pacemaker.langfuse.orchestrator.handle_subagent_stop") as mock_finalize,
    ):

        # Run SubagentStop hook - should not crash
        run_subagent_stop_hook()

        # Verify handle_subagent_stop WAS called (with None transcript path)
        # The hook still calls finalization but with None parent_transcript_path
        mock_finalize.assert_called_once()
        call_kwargs = mock_finalize.call_args[1]
        assert call_kwargs["parent_transcript_path"] is None
