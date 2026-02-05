#!/usr/bin/env python3
"""
Tests for SubagentStop transcript path fallback mechanism.

Problem: Subagent traces have null output because parent_transcript_path is not available
when hook_data lacks session_id in SubagentStop.

Fix: SubagentStart stores parent_transcript_path in state, SubagentStop uses it as fallback.
"""

import json
import pytest
from unittest.mock import patch

from pacemaker.hook import run_subagent_start_hook, run_subagent_stop_hook


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
def mock_hook_data_subagent_start():
    """Hook data for SubagentStart with transcript_path."""
    return {
        "hook_event_name": "SubagentStart",
        "session_id": "main-session-123",
        "transcript_path": "/tmp/projects/main-session-123.jsonl",
        "agent_id": "subagent-abc",
    }


@pytest.fixture
def mock_hook_data_subagent_stop_no_session():
    """Hook data for SubagentStop WITHOUT session_id (edge case)."""
    return {
        "hook_event_name": "SubagentStop",
        # No session_id - this is the problematic scenario
    }


def test_subagent_start_stores_transcript_path_in_state(
    mock_config_langfuse_enabled, mock_hook_data_subagent_start
):
    """
    RED TEST: Verify SubagentStart stores parent_transcript_path in state.

    This test will FAIL until we add:
    state["current_subagent_parent_transcript_path"] = hook_data.get("transcript_path", "")
    """
    hook_data_json = json.dumps(mock_hook_data_subagent_start)

    mock_state = {"in_subagent": False, "subagent_counter": 0}
    saved_state = {}

    def capture_save_state(state, path):
        """Capture state when save_state is called."""
        saved_state.update(state)

    with (
        patch("pacemaker.hook.load_config", return_value=mock_config_langfuse_enabled),
        patch("pacemaker.hook.load_state", return_value=mock_state),
        patch("pacemaker.hook.save_state", side_effect=capture_save_state),
        patch("sys.stdin.read", return_value=hook_data_json),
        patch(
            "pacemaker.langfuse.orchestrator.handle_subagent_start",
            return_value="trace-123",
        ),
    ):

        # Run SubagentStart hook
        run_subagent_start_hook()

        # RED: This assertion will FAIL until we store parent_transcript_path
        assert "current_subagent_parent_transcript_path" in saved_state
        assert (
            saved_state["current_subagent_parent_transcript_path"]
            == "/tmp/projects/main-session-123.jsonl"
        )


def test_subagent_stop_uses_stored_transcript_path_when_hook_data_lacks_session(
    mock_config_langfuse_enabled, mock_hook_data_subagent_stop_no_session
):
    """
    RED TEST: Verify SubagentStop uses stored parent_transcript_path from state
    when hook_data lacks session_id.

    This test will FAIL until we implement fallback logic in SubagentStop.
    """
    hook_data_json = json.dumps(mock_hook_data_subagent_stop_no_session)

    # State includes stored parent_transcript_path from SubagentStart
    mock_state = {
        "in_subagent": True,
        "subagent_counter": 1,
        "current_subagent_trace_id": "trace-subagent-456",
        "current_subagent_agent_id": "subagent-abc",
        "current_subagent_parent_transcript_path": "/tmp/projects/main-session-123.jsonl",  # From SubagentStart
    }

    with (
        patch("pacemaker.hook.load_config", return_value=mock_config_langfuse_enabled),
        patch("pacemaker.hook.load_state", return_value=mock_state),
        patch("pacemaker.hook.save_state"),
        patch("sys.stdin.read", return_value=hook_data_json),
        patch("pacemaker.hook.get_transcript_path", return_value=None),
        patch("pacemaker.langfuse.orchestrator.handle_subagent_stop") as mock_finalize,
    ):

        # Run SubagentStop hook
        run_subagent_stop_hook()

        # Verify handle_subagent_stop was called
        mock_finalize.assert_called_once()
        call_kwargs = mock_finalize.call_args[1]

        # RED: This assertion will FAIL until we implement fallback to stored path
        assert (
            call_kwargs["parent_transcript_path"]
            == "/tmp/projects/main-session-123.jsonl"
        )
        assert call_kwargs["subagent_trace_id"] == "trace-subagent-456"
        assert call_kwargs["agent_id"] == "subagent-abc"


def test_subagent_stop_clears_stored_transcript_path(
    mock_config_langfuse_enabled, mock_hook_data_subagent_stop_no_session
):
    """
    RED TEST: Verify SubagentStop clears current_subagent_parent_transcript_path from state.

    This test will FAIL until we add cleanup logic.
    """
    hook_data_json = json.dumps(mock_hook_data_subagent_stop_no_session)

    mock_state = {
        "in_subagent": True,
        "subagent_counter": 1,
        "current_subagent_trace_id": "trace-subagent-456",
        "current_subagent_agent_id": "subagent-abc",
        "current_subagent_parent_transcript_path": "/tmp/projects/main-session-123.jsonl",
    }

    saved_state = {}

    def capture_save_state(state, path):
        """Capture state when save_state is called."""
        saved_state.clear()
        saved_state.update(state)

    with (
        patch("pacemaker.hook.load_config", return_value=mock_config_langfuse_enabled),
        patch("pacemaker.hook.load_state", return_value=mock_state),
        patch("pacemaker.hook.save_state", side_effect=capture_save_state),
        patch("sys.stdin.read", return_value=hook_data_json),
        patch("pacemaker.hook.get_transcript_path", return_value=None),
        patch("pacemaker.langfuse.orchestrator.handle_subagent_stop"),
    ):

        # Run SubagentStop hook
        run_subagent_stop_hook()

        # RED: This assertion will FAIL until we clear the stored path
        assert "current_subagent_parent_transcript_path" not in saved_state
        assert "current_subagent_trace_id" not in saved_state
        assert "current_subagent_agent_id" not in saved_state


def test_subagent_stop_prefers_hook_data_session_over_stored_path(
    mock_config_langfuse_enabled,
):
    """
    GREEN TEST (should pass after fix): Verify SubagentStop prefers hook_data session_id
    over stored path when both are available.
    """
    hook_data_with_session = {
        "hook_event_name": "SubagentStop",
        "session_id": "fresh-session-999",
    }
    hook_data_json = json.dumps(hook_data_with_session)

    # State has stored path but hook_data has session_id
    mock_state = {
        "in_subagent": True,
        "subagent_counter": 1,
        "current_subagent_trace_id": "trace-subagent-456",
        "current_subagent_agent_id": "subagent-abc",
        "current_subagent_parent_transcript_path": "/tmp/projects/OLD-PATH.jsonl",  # Should be ignored
    }

    with (
        patch("pacemaker.hook.load_config", return_value=mock_config_langfuse_enabled),
        patch("pacemaker.hook.load_state", return_value=mock_state),
        patch("pacemaker.hook.save_state"),
        patch("sys.stdin.read", return_value=hook_data_json),
        patch(
            "pacemaker.hook.get_transcript_path",
            return_value="/tmp/projects/fresh-session-999.jsonl",
        ),
        patch("pacemaker.langfuse.orchestrator.handle_subagent_stop") as mock_finalize,
    ):

        # Run SubagentStop hook
        run_subagent_stop_hook()

        # Verify handle_subagent_stop was called with FRESH path, not stored path
        mock_finalize.assert_called_once()
        call_kwargs = mock_finalize.call_args[1]
        assert (
            call_kwargs["parent_transcript_path"]
            == "/tmp/projects/fresh-session-999.jsonl"
        )
