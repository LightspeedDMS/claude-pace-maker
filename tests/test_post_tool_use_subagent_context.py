#!/usr/bin/env python3
"""
Tests for handle_post_tool_use() subagent context selection.

Bug #158: the trace is selected from the hook payload identity (session_id +
agent_id), never from the machine-global pacemaker state.json. A call carrying
an agent_id belongs to that subagent's trace; a call without one belongs to the
session's main trace. See tests/test_issue_158_langfuse_attribution.py for the
interleaved multi-session coverage.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from pacemaker.langfuse.orchestrator import handle_post_tool_use
from pacemaker.langfuse.state import StateManager

PARENT_SESSION = "parent-session-123"
PARENT_TRACE = "parent-trace-456"
AGENT_ID = "agent-abc"
SUBAGENT_TRACE = "subagent-trace-789"


@pytest.fixture
def base_config():
    """Base Langfuse configuration."""
    return {
        "langfuse_enabled": True,
        "langfuse_base_url": "https://langfuse.example.com",
        "langfuse_public_key": "pk-test",
        "langfuse_secret_key": "sk-test",
    }


@pytest.fixture
def paths(tmp_path):
    """Transcript plus Langfuse state for a parent session and one subagent."""
    transcript = tmp_path / "session.jsonl"
    entry = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Test content"}],
        },
    }
    transcript.write_text(json.dumps(entry) + "\n")

    state_dir = tmp_path / "langfuse_state"
    sm = StateManager(str(state_dir))
    sm.create_or_update(
        session_id=PARENT_SESSION,
        trace_id=PARENT_TRACE,
        last_pushed_line=0,
        metadata={"current_trace_id": PARENT_TRACE, "trace_start_line": 0},
    )
    sm.create_or_update(
        session_id=f"subagent-{AGENT_ID}",
        trace_id=SUBAGENT_TRACE,
        last_pushed_line=0,
        metadata={"current_trace_id": SUBAGENT_TRACE, "trace_start_line": 0},
    )
    return {"transcript": str(transcript), "state_dir": str(state_dir)}


def _run(base_config, paths, **kwargs):
    with patch("pacemaker.langfuse.orchestrator.push") as mock_push:
        mock_push.push_batch_events = MagicMock(return_value=(True, 1))
        result = handle_post_tool_use(
            config=base_config,
            session_id=PARENT_SESSION,
            transcript_path=paths["transcript"],
            state_dir=paths["state_dir"],
            **kwargs,
        )
        args, _ = mock_push.push_batch_events.call_args
        return result, args[3]


class TestHandlePostToolUseSubagentContext:
    """Tests for subagent context selection in handle_post_tool_use."""

    def test_uses_subagent_trace_id_when_payload_has_agent_id(self, base_config, paths):
        result, batch = _run(base_config, paths, agent_id=AGENT_ID)

        assert result is True
        assert batch
        for event in batch:
            assert event["type"] == "span-create"
            assert event["body"]["traceId"] == SUBAGENT_TRACE

    def test_uses_parent_trace_id_when_payload_has_no_agent_id(
        self, base_config, paths
    ):
        result, batch = _run(base_config, paths)

        assert result is True
        for event in batch:
            assert event["type"] == "span-create"
            assert event["body"]["traceId"] == PARENT_TRACE
