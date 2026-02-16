#!/usr/bin/env python3
"""
Tests for subagent trace creation (not span).

User requirement: Create REAL Langfuse traces for subagents, not just spans.

This test file ensures that handle_subagent_start creates a trace
(type: "trace-create") instead of a span (type: "span-create").
"""

import json
import pytest
from unittest.mock import patch

from pacemaker.langfuse import orchestrator


class TestSubagentTraceCreation:
    """Tests for handle_subagent_start creating traces (not spans)."""

    @pytest.fixture
    def config(self):
        """Config with Langfuse enabled."""
        return {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://langfuse.example.com",
            "langfuse_public_key": "pk-test-123",
            "langfuse_secret_key": "sk-test-456",
            "db_path": "/tmp/test.db",
        }

    @pytest.fixture
    def parent_transcript(self, tmp_path):
        """Parent transcript with Task tool call."""
        transcript = tmp_path / "parent-session.jsonl"

        task_tool_entry = {
            "type": "assistant",
            "uuid": "msg-123",
            "timestamp": "2025-01-01T10:00:00Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "task-tool-obs-456",
                        "name": "Task",
                        "input": {
                            "subagent_type": "code-reviewer",
                            "prompt": "Review the authentication code for security issues",
                        },
                    }
                ],
            },
        }

        with open(transcript, "w") as f:
            f.write(json.dumps(task_tool_entry) + "\n")

        return str(transcript)

    def test_handle_subagent_start_creates_trace_not_span(
        self, config, parent_transcript, tmp_path
    ):
        """
        Test handle_subagent_start creates a trace (not span) for subagent.

        Given a subagent starts
        When handle_subagent_start is called with parent_transcript_path
        Then it should create a trace (type: "trace-create") NOT a span
        And the trace should have input = subagent_prompt
        And the trace should have sessionId = parent_session_id
        """
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        # Create parent state with current_trace_id
        parent_state_file = state_dir / "parent-session-123.json"
        parent_state = {
            "trace_id": "main-trace-abc",
            "last_pushed_line": 10,
            "metadata": {"current_trace_id": "main-trace-abc"},
        }
        with open(parent_state_file, "w") as f:
            json.dump(parent_state, f)

        with (
            patch(
                "pacemaker.langfuse.push.push_batch_events", return_value=(True, 1)
            ) as mock_push,
            patch("pacemaker.langfuse.metrics.increment_metric"),
        ):

            # Call handle_subagent_start (parent_observation_id removed - Claude Code doesn't provide it)
            trace_id = orchestrator.handle_subagent_start(
                config=config,
                parent_session_id="parent-session-123",
                subagent_session_id="subagent-789",
                subagent_name="code-reviewer",
                parent_transcript_path=parent_transcript,
                state_dir=str(state_dir),
            )

            # Verify push was called
            assert mock_push.called
            batch = mock_push.call_args[0][3]  # 4th positional arg is batch

            # Verify batch has one event
            assert len(batch) == 1
            event = batch[0]

            # CRITICAL: Must be trace-create, NOT span-create
            assert (
                event["type"] == "trace-create"
            ), f"Expected trace-create but got {event['type']}"

            # Verify trace body
            trace_body = event["body"]
            assert "input" in trace_body, "Trace must have input field"
            assert (
                trace_body["input"]
                == "Review the authentication code for security issues"
            )

            # Verify sessionId links to parent session
            assert "sessionId" in trace_body, "Trace must have sessionId field"
            assert trace_body["sessionId"] == "parent-session-123"

            # Verify trace name
            assert trace_body["name"] == "subagent:code-reviewer"

            # Verify trace_id was returned
            assert trace_id is not None
            assert "subagent-code-reviewer" in trace_id

    def test_handle_subagent_start_updates_subagent_state_with_new_trace_id(
        self, config, parent_transcript, tmp_path
    ):
        """
        Test handle_subagent_start stores NEW subagent trace_id in subagent state.

        Given a subagent starts and a new trace is created
        When handle_subagent_start completes
        Then the subagent's state should have current_trace_id = subagent_trace_id
        And subsequent spans from subagent should link to THIS trace, not parent's
        """
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        # Create parent state
        parent_state_file = state_dir / "parent-session-123.json"
        parent_state = {
            "trace_id": "main-trace-abc",
            "last_pushed_line": 10,
            "metadata": {"current_trace_id": "main-trace-abc"},
        }
        with open(parent_state_file, "w") as f:
            json.dump(parent_state, f)

        with (
            patch("pacemaker.langfuse.push.push_batch_events", return_value=(True, 1)),
            patch("pacemaker.langfuse.metrics.increment_metric"),
        ):

            # Call handle_subagent_start (parent_observation_id removed - Claude Code doesn't provide it)
            subagent_trace_id = orchestrator.handle_subagent_start(
                config=config,
                parent_session_id="parent-session-123",
                subagent_session_id="subagent-789",
                subagent_name="code-reviewer",
                parent_transcript_path=parent_transcript,
                state_dir=str(state_dir),
            )

            # Verify subagent state was created with NEW trace_id
            subagent_state_file = state_dir / "subagent-789.json"
            assert subagent_state_file.exists(), "Subagent state file should be created"

            with open(subagent_state_file, "r") as f:
                subagent_state = json.load(f)

            # CRITICAL: Subagent state must have the NEW subagent trace_id
            # NOT the parent's trace_id
            assert (
                subagent_state["trace_id"] == subagent_trace_id
            ), "Subagent state should have new subagent trace_id"
            assert subagent_state["metadata"]["current_trace_id"] == subagent_trace_id

            # Verify it's NOT the parent's trace_id
            assert (
                subagent_state["trace_id"] != "main-trace-abc"
            ), "Subagent should NOT use parent's trace_id"
