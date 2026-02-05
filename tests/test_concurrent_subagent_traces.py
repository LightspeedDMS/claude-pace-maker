"""
Tests for concurrent subagent trace tracking fix.

Problem: When multiple subagents run concurrently, each SubagentStart
overwrites the previous current_subagent_trace_id. When first subagent
finishes, SubagentStop can't find its trace_id.

Solution: Store trace info in dict keyed by agent_id:
  state["subagent_traces"][agent_id] = {"trace_id": ..., "parent_transcript_path": ...}
"""

import json
import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from pacemaker.hook import (
    run_subagent_start_hook,
    run_subagent_stop_hook,
    load_state,
    save_state,
)


@pytest.fixture
def temp_state_file():
    """Create temporary state file."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
        state_path = f.name
        save_state({}, state_path)
    yield state_path
    if os.path.exists(state_path):
        os.remove(state_path)


@pytest.fixture
def temp_config():
    """Create temporary config with langfuse disabled (for isolated testing)."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
        config_path = f.name
        config = {
            "langfuse_enabled": False,
            "intent_validation_enabled": False,
        }
        json.dump(config, f)
    yield config_path
    if os.path.exists(config_path):
        os.remove(config_path)


class TestConcurrentSubagentTraces:
    """Test concurrent subagent trace tracking with dict-based storage."""

    def test_two_subagents_start_both_stored(
        self, temp_state_file, temp_config, monkeypatch
    ):
        """
        Test that when two subagents start concurrently, both trace_ids
        are stored in the dict without overwriting each other.
        """
        # Patch state/config paths
        monkeypatch.setattr("pacemaker.hook.DEFAULT_STATE_PATH", temp_state_file)
        monkeypatch.setattr("pacemaker.hook.DEFAULT_CONFIG_PATH", temp_config)

        # Mock Langfuse subagent start to return trace IDs
        with patch("pacemaker.hook._handle_langfuse_subagent_start") as mock_langfuse:
            # First subagent: Explore
            mock_langfuse.return_value = "trace-explore-123"
            hook_data_explore = {
                "agent_id": "agent-explore",
                "agent_name": "Explore",
                "transcript_path": "/tmp/main-transcript.jsonl",
            }
            with patch(
                "sys.stdin", MagicMock(read=lambda: json.dumps(hook_data_explore))
            ):
                with patch("sys.stdout", MagicMock()):
                    run_subagent_start_hook()

            # Second subagent: Plan
            mock_langfuse.return_value = "trace-plan-456"
            hook_data_plan = {
                "agent_id": "agent-plan",
                "agent_name": "Plan",
                "transcript_path": "/tmp/main-transcript.jsonl",
            }
            with patch("sys.stdin", MagicMock(read=lambda: json.dumps(hook_data_plan))):
                with patch("sys.stdout", MagicMock()):
                    run_subagent_start_hook()

        # Verify both trace_ids are stored in dict
        state = load_state(temp_state_file)
        subagent_traces = state.get("subagent_traces", {})

        assert "agent-explore" in subagent_traces, "Explore trace not stored"
        assert "agent-plan" in subagent_traces, "Plan trace not stored"

        assert subagent_traces["agent-explore"]["trace_id"] == "trace-explore-123"
        assert subagent_traces["agent-plan"]["trace_id"] == "trace-plan-456"

        assert (
            subagent_traces["agent-explore"]["parent_transcript_path"]
            == "/tmp/main-transcript.jsonl"
        )
        assert (
            subagent_traces["agent-plan"]["parent_transcript_path"]
            == "/tmp/main-transcript.jsonl"
        )

    def test_first_subagent_stop_finds_trace(
        self, temp_state_file, temp_config, monkeypatch
    ):
        """
        Test that when first subagent stops, it correctly finds its
        trace_id from the dict (even though second subagent is still running).
        """
        # Setup: two subagents have started
        state = {
            "subagent_counter": 2,
            "in_subagent": True,
            "subagent_traces": {
                "agent-explore": {
                    "trace_id": "trace-explore-123",
                    "parent_transcript_path": "/tmp/main-transcript.jsonl",
                },
                "agent-plan": {
                    "trace_id": "trace-plan-456",
                    "parent_transcript_path": "/tmp/main-transcript.jsonl",
                },
            },
        }
        save_state(state, temp_state_file)

        # Patch paths
        monkeypatch.setattr("pacemaker.hook.DEFAULT_STATE_PATH", temp_state_file)
        monkeypatch.setattr("pacemaker.hook.DEFAULT_CONFIG_PATH", temp_config)

        # Mock langfuse handle_subagent_stop
        with patch("pacemaker.hook.get_transcript_path", return_value=None):
            with patch(
                "pacemaker.langfuse.orchestrator.handle_subagent_stop"
            ) as mock_stop:
                # First subagent (Explore) stops
                hook_data_explore = {
                    "agent_id": "agent-explore",
                    "session_id": None,
                }
                with patch(
                    "sys.stdin", MagicMock(read=lambda: json.dumps(hook_data_explore))
                ):
                    # Enable langfuse temporarily to trigger finalization
                    config = json.load(open(temp_config))
                    config["langfuse_enabled"] = True
                    json.dump(config, open(temp_config, "w"))

                    run_subagent_stop_hook()

                    # Verify handle_subagent_stop was called with correct trace_id
                    assert mock_stop.called, "handle_subagent_stop not called"
                    call_kwargs = mock_stop.call_args.kwargs
                    assert call_kwargs["subagent_trace_id"] == "trace-explore-123"
                    assert call_kwargs["agent_id"] == "agent-explore"

        # Verify Explore trace removed, Plan trace still present
        state = load_state(temp_state_file)
        subagent_traces = state.get("subagent_traces", {})

        assert "agent-explore" not in subagent_traces, "Explore trace should be removed"
        assert "agent-plan" in subagent_traces, "Plan trace should still be present"

    def test_second_subagent_stop_finds_trace(
        self, temp_state_file, temp_config, monkeypatch
    ):
        """
        Test that when second subagent stops, it correctly finds its
        trace_id from the dict (after first subagent has already stopped).
        """
        # Setup: Explore has stopped, only Plan remains
        state = {
            "subagent_counter": 1,
            "in_subagent": True,
            "subagent_traces": {
                "agent-plan": {
                    "trace_id": "trace-plan-456",
                    "parent_transcript_path": "/tmp/main-transcript.jsonl",
                },
            },
        }
        save_state(state, temp_state_file)

        # Patch paths
        monkeypatch.setattr("pacemaker.hook.DEFAULT_STATE_PATH", temp_state_file)
        monkeypatch.setattr("pacemaker.hook.DEFAULT_CONFIG_PATH", temp_config)

        # Mock langfuse handle_subagent_stop
        with patch("pacemaker.hook.get_transcript_path", return_value=None):
            with patch(
                "pacemaker.langfuse.orchestrator.handle_subagent_stop"
            ) as mock_stop:
                # Second subagent (Plan) stops
                hook_data_plan = {
                    "agent_id": "agent-plan",
                    "session_id": None,
                }
                with patch(
                    "sys.stdin", MagicMock(read=lambda: json.dumps(hook_data_plan))
                ):
                    # Enable langfuse temporarily
                    config = json.load(open(temp_config))
                    config["langfuse_enabled"] = True
                    json.dump(config, open(temp_config, "w"))

                    run_subagent_stop_hook()

                    # Verify handle_subagent_stop called with correct trace_id
                    assert mock_stop.called, "handle_subagent_stop not called"
                    call_kwargs = mock_stop.call_args.kwargs
                    assert call_kwargs["subagent_trace_id"] == "trace-plan-456"
                    assert call_kwargs["agent_id"] == "agent-plan"

        # Verify Plan trace removed, dict is now empty
        state = load_state(temp_state_file)
        subagent_traces = state.get("subagent_traces", {})

        assert "agent-plan" not in subagent_traces, "Plan trace should be removed"
        assert len(subagent_traces) == 0, "All traces should be cleaned up"

    def test_backward_compat_fallback_to_old_keys(
        self, temp_state_file, temp_config, monkeypatch
    ):
        """
        Test backward compatibility: if new dict doesn't have trace,
        fallback to old current_subagent_trace_id keys.
        """
        # Setup: old-style state (before dict migration)
        state = {
            "subagent_counter": 1,
            "in_subagent": True,
            "current_subagent_trace_id": "trace-old-legacy",
            "current_subagent_agent_id": "agent-legacy",
            "current_subagent_parent_transcript_path": "/tmp/legacy.jsonl",
        }
        save_state(state, temp_state_file)

        # Patch paths
        monkeypatch.setattr("pacemaker.hook.DEFAULT_STATE_PATH", temp_state_file)
        monkeypatch.setattr("pacemaker.hook.DEFAULT_CONFIG_PATH", temp_config)

        # Mock langfuse handle_subagent_stop
        with patch("pacemaker.hook.get_transcript_path", return_value=None):
            with patch(
                "pacemaker.langfuse.orchestrator.handle_subagent_stop"
            ) as mock_stop:
                # Subagent stops (agent_id matches old key)
                hook_data = {
                    "agent_id": "agent-legacy",
                    "session_id": None,
                }
                with patch("sys.stdin", MagicMock(read=lambda: json.dumps(hook_data))):
                    # Enable langfuse
                    config = json.load(open(temp_config))
                    config["langfuse_enabled"] = True
                    json.dump(config, open(temp_config, "w"))

                    run_subagent_stop_hook()

                    # Verify fallback worked
                    assert mock_stop.called, "handle_subagent_stop not called"
                    call_kwargs = mock_stop.call_args.kwargs
                    assert call_kwargs["subagent_trace_id"] == "trace-old-legacy"
                    assert call_kwargs["agent_id"] == "agent-legacy"

        # Verify old keys cleaned up
        state = load_state(temp_state_file)
        assert "current_subagent_trace_id" not in state
        assert "current_subagent_agent_id" not in state
        assert "current_subagent_parent_transcript_path" not in state

    def test_no_stale_data_after_cleanup(
        self, temp_state_file, temp_config, monkeypatch
    ):
        """
        Test that trace entries are properly removed after SubagentStop,
        leaving no stale data in the dict.
        """
        # Setup: three subagents started
        state = {
            "subagent_counter": 3,
            "in_subagent": True,
            "subagent_traces": {
                "agent-a": {
                    "trace_id": "trace-a",
                    "parent_transcript_path": "/tmp/main.jsonl",
                },
                "agent-b": {
                    "trace_id": "trace-b",
                    "parent_transcript_path": "/tmp/main.jsonl",
                },
                "agent-c": {
                    "trace_id": "trace-c",
                    "parent_transcript_path": "/tmp/main.jsonl",
                },
            },
        }
        save_state(state, temp_state_file)

        # Patch paths
        monkeypatch.setattr("pacemaker.hook.DEFAULT_STATE_PATH", temp_state_file)
        monkeypatch.setattr("pacemaker.hook.DEFAULT_CONFIG_PATH", temp_config)

        # Enable langfuse in config
        config = json.load(open(temp_config))
        config["langfuse_enabled"] = True
        json.dump(config, open(temp_config, "w"))

        # Stop all three subagents
        with patch("pacemaker.hook.get_transcript_path", return_value=None):
            with patch("pacemaker.langfuse.orchestrator.handle_subagent_stop"):
                for agent_id in ["agent-a", "agent-b", "agent-c"]:
                    hook_data = {"agent_id": agent_id, "session_id": None}
                    with patch(
                        "sys.stdin", MagicMock(read=lambda d=hook_data: json.dumps(d))
                    ):
                        run_subagent_stop_hook()

        # Verify all traces cleaned up
        state = load_state(temp_state_file)
        subagent_traces = state.get("subagent_traces", {})
        assert len(subagent_traces) == 0, "All traces should be removed after cleanup"
