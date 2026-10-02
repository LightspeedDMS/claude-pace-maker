"""
Tests for concurrent subagent trace tracking fix.

Problem: When multiple subagents run concurrently, each SubagentStart
overwrites the single legacy current_subagent_trace_id slot. When the first
subagent finishes, SubagentStop can't find its trace_id there.

History: the first fix kept a dict in the shared state.json
(state["subagent_traces"][agent_id]); bug #161 showed that any concurrent hook
could clobber that file, losing traces. SubagentStop now resolves the trace from
the agent's OWN langfuse_state/subagent-<agent_id>.json (see
tests/test_issue_161_subagent_finalize_per_agent_state.py for the full scenario).
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from pacemaker.hook import (
    run_subagent_start_hook,
    run_subagent_stop_hook,
    load_state,
    save_state,
)
from pacemaker.langfuse.state import StateManager

PARENT_TRANSCRIPT = "/tmp/main-transcript.jsonl"
SESSION = "sess-concurrent-0001"
# Real trace-id shape (orchestrator.handle_subagent_start):
# "<parent_session_id>-subagent-<agent_type>-<uuid8>". SubagentStop refuses a
# trace that does not start with "<payload session_id>-subagent-".
TRACE_EXPLORE = f"{SESSION}-subagent-Explore-aaaa1111"
TRACE_PLAN = f"{SESSION}-subagent-Plan-bbbb2222"


def trace_for(letter: str) -> str:
    return f"{SESSION}-subagent-general-purpose-{letter * 8}"


def _register_agent(agent_id: str, trace_id: str) -> None:
    """Create the subagent's own state file the way SubagentStart does.

    Hermetic: tests/conftest.py's autouse _guard_production_db points HOME at a
    per-test temp dir, and the hook locates this directory via expanduser."""
    StateManager(
        str(Path.home() / ".claude-pace-maker" / "langfuse_state")
    ).create_or_update(
        session_id=f"subagent-{agent_id}",
        trace_id=trace_id,
        last_pushed_line=0,
        metadata={"parent_transcript_path": PARENT_TRANSCRIPT},
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
    """Concurrent subagents: each SubagentStop finds its own trace (bug #161)."""

    def test_two_subagents_start_write_no_global_trace_map(
        self, temp_state_file, temp_config, monkeypatch
    ):
        """
        Bug #161: starting two subagents must not put per-agent traces in the
        shared state.json (any concurrent hook could clobber them). Only the
        legacy single slot remains -- the LAST agent wins there, which is why
        SubagentStop resolves traces from each agent's own state file.
        """
        # Patch state/config paths
        monkeypatch.setattr("pacemaker.hook.DEFAULT_STATE_PATH", temp_state_file)
        monkeypatch.setattr("pacemaker.hook.DEFAULT_CONFIG_PATH", temp_config)

        # Mock Langfuse subagent start to return trace IDs
        with patch("pacemaker.hook._handle_langfuse_subagent_start") as mock_langfuse:
            # First subagent: Explore
            mock_langfuse.return_value = TRACE_EXPLORE
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
            mock_langfuse.return_value = TRACE_PLAN
            hook_data_plan = {
                "agent_id": "agent-plan",
                "agent_name": "Plan",
                "transcript_path": "/tmp/main-transcript.jsonl",
            }
            with patch("sys.stdin", MagicMock(read=lambda: json.dumps(hook_data_plan))):
                with patch("sys.stdout", MagicMock()):
                    run_subagent_start_hook()

        state = load_state(temp_state_file)

        assert "subagent_traces" not in state
        assert state["current_subagent_agent_id"] == "agent-plan"
        assert state["current_subagent_trace_id"] == TRACE_PLAN

    def test_first_subagent_stop_finds_trace(
        self, temp_state_file, temp_config, monkeypatch
    ):
        """
        Test that when first subagent stops, it finds its own trace from its
        own state file (even though the second subagent is still running and
        the shared state.json knows nothing about either).
        """
        # Setup: two subagents have started (each has its own state file)
        _register_agent("agent-explore", TRACE_EXPLORE)
        _register_agent("agent-plan", TRACE_PLAN)
        save_state({"subagent_counter": 2, "in_subagent": True}, temp_state_file)

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
                    "session_id": SESSION,
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
                    assert call_kwargs["subagent_trace_id"] == TRACE_EXPLORE
                    assert call_kwargs["agent_id"] == "agent-explore"
                    assert call_kwargs["parent_transcript_path"] == PARENT_TRANSCRIPT

        # The other agent's own state is untouched and no global map appears
        plan_state = StateManager(
            str(Path.home() / ".claude-pace-maker" / "langfuse_state")
        ).read("subagent-agent-plan")
        assert plan_state["trace_id"] == TRACE_PLAN
        assert "subagent_traces" not in load_state(temp_state_file)

    def test_second_subagent_stop_finds_trace(
        self, temp_state_file, temp_config, monkeypatch
    ):
        """
        Test that when second subagent stops, it finds its own trace from its
        own state file (after the first subagent has already stopped).
        """
        # Setup: Explore has stopped, only Plan remains
        _register_agent("agent-plan", TRACE_PLAN)
        save_state({"subagent_counter": 1, "in_subagent": True}, temp_state_file)

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
                    "session_id": SESSION,
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
                    assert call_kwargs["subagent_trace_id"] == TRACE_PLAN
                    assert call_kwargs["agent_id"] == "agent-plan"

        assert "subagent_traces" not in load_state(temp_state_file)

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
            "current_subagent_trace_id": f"{SESSION}-subagent-legacy-cccc3333",
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
                    "session_id": SESSION,
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
                    assert (
                        call_kwargs["subagent_trace_id"]
                        == f"{SESSION}-subagent-legacy-cccc3333"
                    )
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
        Bug #161: every stop finalizes ITS OWN trace (from its own state file),
        and a state.json written by an older version that still carries the
        retired `subagent_traces` map is cleaned of it, leaving no stale data.
        """
        # Setup: three subagents started; the shared file is a pre-#161 one
        for agent_id in ["agent-a", "agent-b", "agent-c"]:
            _register_agent(agent_id, trace_for(agent_id[-1]))
        save_state(
            {
                "subagent_counter": 3,
                "in_subagent": True,
                "subagent_traces": {"agent-a": {"trace_id": "stale-leftover"}},
            },
            temp_state_file,
        )

        # Patch paths
        monkeypatch.setattr("pacemaker.hook.DEFAULT_STATE_PATH", temp_state_file)
        monkeypatch.setattr("pacemaker.hook.DEFAULT_CONFIG_PATH", temp_config)

        # Enable langfuse in config
        config = json.load(open(temp_config))
        config["langfuse_enabled"] = True
        json.dump(config, open(temp_config, "w"))

        # Stop all three subagents
        with patch("pacemaker.hook.get_transcript_path", return_value=None):
            with patch(
                "pacemaker.langfuse.orchestrator.handle_subagent_stop"
            ) as mock_stop:
                for agent_id in ["agent-a", "agent-b", "agent-c"]:
                    hook_data = {"agent_id": agent_id, "session_id": SESSION}
                    with patch(
                        "sys.stdin", MagicMock(read=lambda d=hook_data: json.dumps(d))
                    ):
                        run_subagent_stop_hook()

        finalized = {
            c.kwargs["agent_id"]: c.kwargs["subagent_trace_id"]
            for c in mock_stop.call_args_list
        }
        assert finalized == {
            "agent-a": trace_for("a"),
            "agent-b": trace_for("b"),
            "agent-c": trace_for("c"),
        }
        assert "subagent_traces" not in load_state(temp_state_file)
