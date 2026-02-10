#!/usr/bin/env python3
"""
Unit tests for Langfuse subagent trace bugs.

Bug 1: No spans on subagent traces (early return kills subagent path)
Bug 2: Zero latency on subagent traces (missing startTime/endTime)

These tests define the expected behavior before fixing the bugs.
Following TDD: write failing tests, then implement fixes.
"""

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from pacemaker.langfuse import orchestrator, state


class TestSubagentSpanCreationBugFix:
    """
    Test Bug 1: Subagent traces have no spans due to early return.

    Root cause: handle_post_tool_use() reads parent state, finds no current_trace_id,
    and returns False BEFORE checking for subagent context (which would set the trace_id).

    Expected behavior: Subagent context detection should happen BEFORE the current_trace_id
    check, so spans get created using subagent's trace_id.
    """

    @pytest.fixture
    def config(self):
        """Langfuse configuration."""
        return {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://test.langfuse.com",
            "langfuse_public_key": "pk-test-123",
            "langfuse_secret_key": "sk-test-456",
        }

    @pytest.fixture
    def state_dir(self):
        """Create temporary state directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def pacemaker_state_file(self):
        """Create temporary pacemaker state file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            state_path = f.name
        yield state_path
        Path(state_path).unlink(missing_ok=True)

    @pytest.fixture
    def subagent_transcript(self):
        """Create subagent transcript with tool usage."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # Line 1: Session start
            f.write(
                json.dumps(
                    {
                        "type": "session_start",
                        "session_id": "subagent-123",
                        "isSidechain": True,
                    }
                )
                + "\n"
            )

            # Line 2: User message from Task tool
            f.write(
                json.dumps(
                    {
                        "type": "user",
                        "message": {"role": "user", "content": "Fix the bug"},
                    }
                )
                + "\n"
            )

            # Line 3: Assistant message with tool use
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "text", "text": "Let me read the file..."},
                                {
                                    "type": "tool_use",
                                    "id": "toolu_456",
                                    "name": "Read",
                                    "input": {"file_path": "/test.py"},
                                },
                            ],
                        },
                        "timestamp": "2024-01-01T12:00:00Z",
                        "uuid": "msg-uuid-456",
                    }
                )
                + "\n"
            )

            transcript_path = f.name

        yield transcript_path
        Path(transcript_path).unlink()

    def test_subagent_spans_created_when_parent_has_no_trace_id(
        self, config, state_dir, pacemaker_state_file, subagent_transcript
    ):
        """
        Test that spans are created for subagent even when parent has no current_trace_id.

        This is the failing test for Bug 1.

        Scenario:
        1. Parent session exists but has no current_trace_id (None or missing)
        2. Pacemaker state shows in_subagent=True with subagent_trace_id
        3. handle_post_tool_use() should use subagent's trace_id, not parent's
        4. Spans should be created successfully

        Bug: Current code returns False at line 848 before checking subagent context.
        Expected: Subagent context check should happen BEFORE the early return.
        """
        parent_session_id = "main-session-123"
        subagent_session_id = "subagent-abc-123"
        subagent_trace_id = f"{parent_session_id}-subagent-tdd-engineer-xyz123"

        # Setup parent state WITHOUT current_trace_id (simulates new session or trace gap)
        state_manager = state.StateManager(state_dir)
        state_manager.create_or_update(
            session_id=parent_session_id,
            trace_id="old-parent-trace-123",  # Old trace
            last_pushed_line=10,
            metadata={
                # NO current_trace_id - this is the key condition that triggers the bug
                "trace_start_line": 0,
            },
        )

        # Setup subagent state with its own trace_id
        state_manager.create_or_update(
            session_id=subagent_session_id,
            trace_id=subagent_trace_id,
            last_pushed_line=2,  # Lines 1-2 already processed
            metadata={
                "current_trace_id": subagent_trace_id,
                "trace_start_line": 0,
            },
        )

        # Setup pacemaker state indicating we're in subagent context
        with open(pacemaker_state_file, "w") as f:
            json.dump(
                {
                    "in_subagent": True,
                    "current_subagent_trace_id": subagent_trace_id,
                    "current_subagent_agent_id": "abc-123",
                },
                f,
            )

        # Patch DEFAULT_STATE_PATH to use our temp file
        with patch(
            "pacemaker.langfuse.orchestrator.DEFAULT_STATE_PATH", pacemaker_state_file
        ):
            # Mock push_batch_events to capture span creation
            with patch(
                "pacemaker.langfuse.orchestrator.push.push_batch_events"
            ) as mock_push:
                mock_push.return_value = (True, 2)

                # Call handle_post_tool_use with PARENT session_id
                # (This is what the hook provides - parent's session_id)
                success = orchestrator.handle_post_tool_use(
                    config=config,
                    session_id=parent_session_id,  # Parent session ID
                    transcript_path=subagent_transcript,
                    state_dir=state_dir,
                )

                # ASSERTION: Should succeed (not return False due to early return)
                assert success is True, (
                    "handle_post_tool_use should succeed when in subagent context, "
                    "even if parent has no current_trace_id"
                )

                # ASSERTION: push_batch_events should be called (spans were created)
                assert mock_push.called, "Spans should be created for subagent"

                # Extract the batch that was pushed
                call_args = mock_push.call_args
                batch = call_args[0][3]  # 4th positional arg

                # ASSERTION: Should have spans (text + tool_use)
                assert len(batch) >= 1, "At least one span should be created"

                # ASSERTION: Spans should use subagent's trace_id, not parent's
                for event in batch:
                    if event["type"] == "span-create":
                        span = event["body"]
                        assert span["traceId"] == subagent_trace_id, (
                            f"Span should use subagent trace_id {subagent_trace_id}, "
                            f"not parent's missing trace_id"
                        )

    def test_non_subagent_flow_still_requires_current_trace_id(
        self, config, state_dir, pacemaker_state_file, subagent_transcript
    ):
        """
        Regression test: Non-subagent sessions should still return False if no current_trace_id.

        This ensures our fix for Bug 1 doesn't break the existing behavior for normal sessions.
        """
        parent_session_id = "main-session-456"

        # Setup parent state WITHOUT current_trace_id
        state_manager = state.StateManager(state_dir)
        state_manager.create_or_update(
            session_id=parent_session_id,
            trace_id="old-trace-789",
            last_pushed_line=5,
            metadata={
                # NO current_trace_id
                "trace_start_line": 0,
            },
        )

        # Setup pacemaker state indicating NOT in subagent
        with open(pacemaker_state_file, "w") as f:
            json.dump(
                {
                    "in_subagent": False,
                    "current_subagent_trace_id": None,
                    "current_subagent_agent_id": None,
                },
                f,
            )

        # Patch DEFAULT_STATE_PATH to use our temp file
        with patch(
            "pacemaker.langfuse.orchestrator.DEFAULT_STATE_PATH", pacemaker_state_file
        ):
            # Mock push_batch_events
            with patch(
                "pacemaker.langfuse.orchestrator.push.push_batch_events"
            ) as mock_push:
                mock_push.return_value = (True, 1)

                # Call handle_post_tool_use
                success = orchestrator.handle_post_tool_use(
                    config=config,
                    session_id=parent_session_id,
                    transcript_path=subagent_transcript,
                    state_dir=state_dir,
                )

                # ASSERTION: Should return False (no current_trace_id in non-subagent context)
                assert (
                    success is False
                ), "Non-subagent sessions should return False if no current_trace_id"

                # ASSERTION: push_batch_events should NOT be called for spans
                # (Only pending_trace push might happen, which is checked separately)
                # We care that span creation didn't happen
                if mock_push.called:
                    call_args = mock_push.call_args
                    batch = call_args[0][3]
                    # If called, should only be for pending_trace, not span-create
                    span_creates = [e for e in batch if e["type"] == "span-create"]
                    assert (
                        len(span_creates) == 0
                    ), "No spans should be created without current_trace_id"


class TestSubagentTraceTimestampsBugFix:
    """
    Test Bug 2: Subagent traces show zero latency in Langfuse.

    Root cause:
    - handle_subagent_start() creates trace with only 'timestamp', no 'startTime'
    - handle_subagent_stop() updates trace with only 'output', no 'endTime'
    - Langfuse calculates latency from startTime to endTime, both missing = 0 latency

    Expected behavior:
    - handle_subagent_start() should set 'startTime' explicitly
    - handle_subagent_stop() should set 'endTime' explicitly
    """

    @pytest.fixture
    def config(self):
        """Langfuse configuration."""
        return {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://test.langfuse.com",
            "langfuse_public_key": "pk-test-123",
            "langfuse_secret_key": "sk-test-456",
        }

    @pytest.fixture
    def state_dir(self):
        """Create temporary state directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def parent_transcript(self):
        """Create parent transcript with Task tool call."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # Session start
            f.write(
                json.dumps({"type": "session_start", "session_id": "main-123"}) + "\n"
            )

            # Task tool call
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "task_123",
                                    "name": "Task",
                                    "input": {
                                        "prompt": "Fix the bug",
                                        "subagent_type": "tdd-engineer",
                                    },
                                }
                            ],
                        },
                    }
                )
                + "\n"
            )

            transcript_path = f.name

        yield transcript_path
        Path(transcript_path).unlink()

    @pytest.fixture
    def subagent_transcript(self):
        """Create subagent transcript with result."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # Session start
            f.write(
                json.dumps(
                    {
                        "type": "session_start",
                        "session_id": "subagent-456",
                        "isSidechain": True,
                    }
                )
                + "\n"
            )

            # Subagent work
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"role": "assistant", "content": "Fixed the bug"},
                    }
                )
                + "\n"
            )

            transcript_path = f.name

        yield transcript_path
        Path(transcript_path).unlink()

    def test_subagent_start_includes_startTime(
        self, config, state_dir, parent_transcript
    ):
        """
        Test that handle_subagent_start() creates trace with 'startTime'.

        This is the failing test for Bug 2 (part 1).

        Bug: Current code only sets 'timestamp', not 'startTime'
        Expected: Trace dict should have both 'timestamp' and 'startTime'
        """
        parent_session_id = "main-session-789"
        subagent_session_id = "subagent-xyz-789"
        subagent_name = "tdd-engineer"

        # Setup parent state
        state_manager = state.StateManager(state_dir)
        state_manager.create_or_update(
            session_id=parent_session_id,
            trace_id="parent-trace-123",
            last_pushed_line=0,
            metadata={"current_trace_id": "parent-trace-123"},
        )

        # Mock push_batch_events to capture the trace creation payload
        with patch(
            "pacemaker.langfuse.orchestrator.push.push_batch_events"
        ) as mock_push:
            mock_push.return_value = (True, 1)

            # Call handle_subagent_start
            trace_id = orchestrator.handle_subagent_start(
                config=config,
                parent_session_id=parent_session_id,
                subagent_session_id=subagent_session_id,
                subagent_name=subagent_name,
                parent_transcript_path=parent_transcript,
                state_dir=state_dir,
            )

            # ASSERTION: Should return trace_id
            assert trace_id is not None, "handle_subagent_start should return trace_id"

            # ASSERTION: push_batch_events should be called
            assert mock_push.called, "Trace should be pushed to Langfuse"

            # Extract the batch that was pushed
            call_args = mock_push.call_args
            batch = call_args[0][3]  # 4th positional arg

            # Find the trace-create event
            trace_create_events = [e for e in batch if e["type"] == "trace-create"]
            assert len(trace_create_events) == 1, "Should have one trace-create event"

            trace_event = trace_create_events[0]
            trace_body = trace_event["body"]

            # ASSERTION: Trace should have 'startTime' field
            assert (
                "startTime" in trace_body
            ), "Trace body should include 'startTime' for Langfuse latency calculation"

            # ASSERTION: startTime should be a valid ISO timestamp
            start_time = trace_body["startTime"]
            try:
                datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            except ValueError:
                pytest.fail(f"startTime '{start_time}' is not a valid ISO timestamp")

            # ASSERTION: Should also have 'timestamp' (for event ordering)
            assert "timestamp" in trace_event, "Event should have timestamp"

    def test_subagent_stop_includes_endTime(
        self, config, state_dir, subagent_transcript
    ):
        """
        Test that handle_subagent_stop() updates trace with 'endTime'.

        This is the failing test for Bug 2 (part 2).

        Bug: Current code only sets 'output', not 'endTime'
        Expected: Trace update should include 'endTime'
        """
        parent_session_id = "main-session-abc"
        subagent_session_id = "subagent-def-456"
        subagent_trace_id = f"{parent_session_id}-subagent-code-reviewer-xyz789"

        # Setup parent state
        state_manager = state.StateManager(state_dir)
        state_manager.create_or_update(
            session_id=parent_session_id,
            trace_id="parent-trace-456",
            last_pushed_line=0,
            metadata={"current_trace_id": "parent-trace-456"},
        )

        # Setup subagent state
        state_manager.create_or_update(
            session_id=subagent_session_id,
            trace_id=subagent_trace_id,
            last_pushed_line=0,
            metadata={"current_trace_id": subagent_trace_id},
        )

        # Mock push_batch_events to capture the trace update payload
        with patch(
            "pacemaker.langfuse.orchestrator.push.push_batch_events"
        ) as mock_push:
            mock_push.return_value = (True, 1)

            # Call handle_subagent_stop
            success = orchestrator.handle_subagent_stop(
                config=config,
                subagent_trace_id=subagent_trace_id,
                parent_transcript_path=None,  # Will read from subagent transcript
                agent_id="def-456",
                agent_transcript_path=subagent_transcript,
            )

            # ASSERTION: Should succeed
            assert success is True, "handle_subagent_stop should succeed"

            # ASSERTION: push_batch_events should be called
            assert mock_push.called, "Trace update should be pushed to Langfuse"

            # Extract the batch that was pushed
            call_args = mock_push.call_args
            batch = call_args[0][3]  # 4th positional arg

            # Find the trace-create event (upsert semantics)
            trace_update_events = [e for e in batch if e["type"] == "trace-create"]
            assert (
                len(trace_update_events) == 1
            ), "Should have one trace-create event for update"

            trace_event = trace_update_events[0]
            trace_body = trace_event["body"]

            # ASSERTION: Trace update should have 'endTime' field
            assert (
                "endTime" in trace_body
            ), "Trace update should include 'endTime' for Langfuse latency calculation"

            # ASSERTION: endTime should be a valid ISO timestamp
            end_time = trace_body["endTime"]
            try:
                datetime.fromisoformat(end_time.replace("Z", "+00:00"))
            except ValueError:
                pytest.fail(f"endTime '{end_time}' is not a valid ISO timestamp")

            # ASSERTION: Should also have 'output' (the subagent result)
            assert "output" in trace_body, "Trace update should include output"

    def test_subagent_timestamps_allow_latency_calculation(
        self, config, state_dir, parent_transcript, subagent_transcript
    ):
        """
        Integration test: Verify full subagent lifecycle produces timestamps for latency.

        This tests that both handle_subagent_start and handle_subagent_stop work together
        to provide the data Langfuse needs for latency calculation.
        """
        parent_session_id = "main-full-test"
        subagent_session_id = "subagent-full-test"
        subagent_name = "manual-test-executor"

        # Setup parent state
        state_manager = state.StateManager(state_dir)
        state_manager.create_or_update(
            session_id=parent_session_id,
            trace_id="parent-full-trace",
            last_pushed_line=0,
            metadata={"current_trace_id": "parent-full-trace"},
        )

        captured_start_time = None
        captured_end_time = None

        # Mock push_batch_events to capture both start and stop
        with patch(
            "pacemaker.langfuse.orchestrator.push.push_batch_events"
        ) as mock_push:
            mock_push.return_value = (True, 1)

            # STEP 1: Start subagent
            trace_id = orchestrator.handle_subagent_start(
                config=config,
                parent_session_id=parent_session_id,
                subagent_session_id=subagent_session_id,
                subagent_name=subagent_name,
                parent_transcript_path=parent_transcript,
                state_dir=state_dir,
            )

            assert trace_id is not None

            # Capture startTime from first call
            first_call_args = mock_push.call_args_list[0]
            first_batch = first_call_args[0][3]
            trace_create = [e for e in first_batch if e["type"] == "trace-create"][0]
            captured_start_time = trace_create["body"].get("startTime")

            # STEP 2: Stop subagent
            success = orchestrator.handle_subagent_stop(
                config=config,
                subagent_trace_id=trace_id,
                parent_transcript_path=None,
                agent_id="full-test",
                agent_transcript_path=subagent_transcript,
            )

            assert success is True

            # Capture endTime from second call
            second_call_args = mock_push.call_args_list[1]
            second_batch = second_call_args[0][3]
            trace_update = [e for e in second_batch if e["type"] == "trace-create"][0]
            captured_end_time = trace_update["body"].get("endTime")

        # ASSERTION: Both timestamps should exist
        assert (
            captured_start_time is not None
        ), "startTime should be set on trace creation"
        assert (
            captured_end_time is not None
        ), "endTime should be set on trace finalization"

        # ASSERTION: Both should be valid ISO timestamps
        start_dt = datetime.fromisoformat(captured_start_time.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(captured_end_time.replace("Z", "+00:00"))

        # ASSERTION: endTime should be >= startTime (chronological order)
        assert end_dt >= start_dt, "endTime should be after or equal to startTime"
