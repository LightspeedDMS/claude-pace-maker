#!/usr/bin/env python3
"""
Tests for Langfuse orchestrator trace-per-turn workflow.

Tests orchestrator functions:
- handle_user_prompt_submit: Creates new trace for user turn
- handle_post_tool_use: Creates span for tool call
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from pacemaker.langfuse.orchestrator import (
    handle_user_prompt_submit,
    handle_post_tool_use,
)


class TestUserPromptSubmitHandler:
    """Test handle_user_prompt_submit orchestrator function."""

    @pytest.fixture
    def mock_config(self):
        """Mock configuration with Langfuse enabled."""
        return {
            "langfuse_enabled": True,
            "langfuse_base_url": "http://192.168.68.42:3000",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }

    @pytest.fixture
    def transcript_with_user_prompt(self):
        """Create transcript with user prompt."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # Session start
            f.write(
                json.dumps(
                    {
                        "type": "session_start",
                        "session_id": "test-123",
                        "model": "claude-sonnet-4-5",
                        "user_email": "user@example.com",
                    }
                )
                + "\n"
            )
            # User prompt
            f.write(
                json.dumps(
                    {"message": {"role": "user", "content": "implement feature X"}}
                )
                + "\n"
            )
            transcript_path = f.name

        yield transcript_path

        Path(transcript_path).unlink()

    def test_creates_new_trace_on_user_prompt(
        self, mock_config, transcript_with_user_prompt
    ):
        """
        Test that UserPromptSubmit creates new trace.

        Should:
        1. Generate unique trace_id
        2. Extract user message from transcript
        3. Create trace via trace module
        4. Push trace to Langfuse
        5. Update state with current_trace_id and trace_start_line
        """
        with tempfile.TemporaryDirectory() as state_dir:
            session_id = "test-session-abc"
            user_message = "implement feature X"

            # Mock push to avoid network call
            with patch(
                "pacemaker.langfuse.orchestrator.push.push_batch_events"
            ) as mock_push:
                mock_push.return_value = True

                result = handle_user_prompt_submit(
                    config=mock_config,
                    session_id=session_id,
                    transcript_path=transcript_with_user_prompt,
                    state_dir=state_dir,
                    user_message=user_message,
                )

                # Should succeed
                assert result is True

                # Should have called push with trace event
                assert mock_push.called
                batch = mock_push.call_args[0][3]  # batch argument
                assert len(batch) >= 1

                # First event should be trace-create
                trace_event = batch[0]
                assert trace_event["type"] == "trace-create"
                assert trace_event["body"]["sessionId"] == session_id
                assert user_message in trace_event["body"]["name"]

    def test_updates_state_with_current_trace_id(
        self, mock_config, transcript_with_user_prompt
    ):
        """
        Test that state is updated with current_trace_id.

        State must track which trace is active for subsequent span creation.
        """
        with tempfile.TemporaryDirectory() as state_dir:
            session_id = "test-session-def"
            user_message = "fix bug"

            with patch(
                "pacemaker.langfuse.orchestrator.push.push_batch_events"
            ) as mock_push:
                mock_push.return_value = True

                handle_user_prompt_submit(
                    config=mock_config,
                    session_id=session_id,
                    transcript_path=transcript_with_user_prompt,
                    state_dir=state_dir,
                    user_message=user_message,
                )

                # Check state was updated
                from pacemaker.langfuse.state import StateManager

                state_mgr = StateManager(state_dir)
                state = state_mgr.read(session_id)

                assert state is not None
                assert "metadata" in state
                assert "current_trace_id" in state["metadata"]
                assert "trace_start_line" in state["metadata"]

    def test_user_prompt_submit_includes_model_in_trace(self, mock_config):
        """
        Test that handle_user_prompt_submit extracts model and includes it in trace.

        Should extract model from transcript metadata and pass to create_trace_for_turn.
        """
        with tempfile.TemporaryDirectory() as state_dir:
            # Create transcript with model in session_start
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".jsonl", delete=False
            ) as f:
                f.write(
                    json.dumps(
                        {
                            "type": "session_start",
                            "session_id": "test-123",
                            "model": "claude-opus-4-5-20250929",
                            "user_email": "user@example.com",
                        }
                    )
                    + "\n"
                )
                f.write(
                    json.dumps(
                        {"message": {"role": "user", "content": "implement feature X"}}
                    )
                    + "\n"
                )
                transcript_path = f.name

            try:
                session_id = "test-session-model"
                user_message = "implement feature X"

                with patch(
                    "pacemaker.langfuse.orchestrator.push.push_batch_events"
                ) as mock_push:
                    mock_push.return_value = True

                    result = handle_user_prompt_submit(
                        config=mock_config,
                        session_id=session_id,
                        transcript_path=transcript_path,
                        state_dir=state_dir,
                        user_message=user_message,
                    )

                    assert result is True
                    assert mock_push.called

                    # Extract trace from batch
                    batch = mock_push.call_args[0][3]
                    trace_event = batch[0]
                    trace = trace_event["body"]

                    # Trace metadata should include model
                    assert "metadata" in trace
                    assert "model" in trace["metadata"]
                    assert trace["metadata"]["model"] == "claude-opus-4-5-20250929"

            finally:
                Path(transcript_path).unlink()


class TestPostToolUseHandler:
    """Test handle_post_tool_use orchestrator function."""

    @pytest.fixture
    def mock_config(self):
        """Mock configuration with Langfuse enabled."""
        return {
            "langfuse_enabled": True,
            "langfuse_base_url": "http://192.168.68.42:3000",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }

    @pytest.mark.skip(
        reason="Obsolete: Test uses old hook-data-driven API. Replaced by test_langfuse_orchestrator_incremental_spans.py"
    )
    def test_creates_span_for_tool_call(self, mock_config):
        """
        Test that PostToolUse creates span for tool call.

        OBSOLETE: This test uses the old signature (tool_name/tool_input/tool_output).
        After refactoring to transcript-parsing, handle_post_tool_use() now accepts
        transcript_path instead. See test_langfuse_orchestrator_incremental_spans.py
        for comprehensive tests of the new behavior.

        Should:
        1. Extract tool details (name, input, output, timing)
        2. Get current_trace_id from state
        3. Create span via span module
        4. Push span to Langfuse
        """
        with tempfile.TemporaryDirectory() as state_dir:
            session_id = "test-session-ghi"
            trace_id = f"{session_id}-turn-1"

            # Setup state with current_trace_id
            from pacemaker.langfuse.state import StateManager

            state_mgr = StateManager(state_dir)
            state_mgr.create_or_update(
                session_id=session_id,
                trace_id=trace_id,
                last_pushed_line=5,
                metadata={
                    "current_trace_id": trace_id,
                    "trace_start_line": 0,
                },
            )

            tool_name = "Read"
            tool_input = {"file_path": "/src/file.py"}
            tool_output = "File contents..."

            # Mock push to avoid network call
            with patch(
                "pacemaker.langfuse.orchestrator.push.push_batch_events"
            ) as mock_push:
                mock_push.return_value = True

                result = handle_post_tool_use(
                    config=mock_config,
                    session_id=session_id,
                    state_dir=state_dir,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    tool_output=tool_output,
                )

                # Should succeed
                assert result is True

                # Should have called push with span event
                assert mock_push.called
                batch = mock_push.call_args[0][3]  # batch argument
                assert len(batch) == 1

                # Event should be span-create
                span_event = batch[0]
                assert span_event["type"] == "span-create"
                assert span_event["body"]["traceId"] == trace_id
                assert span_event["body"]["name"] == f"Tool - {tool_name}"

    @pytest.mark.skip(
        reason="Obsolete: Test uses old hook-data-driven API. Replaced by test_langfuse_orchestrator_incremental_spans.py"
    )
    def test_fails_if_no_current_trace(self, mock_config):
        """
        Test that PostToolUse fails gracefully if no current trace.

        OBSOLETE: This test uses the old signature (tool_name/tool_input/tool_output).
        After refactoring to transcript-parsing, handle_post_tool_use() now accepts
        transcript_path instead. See test_langfuse_orchestrator_incremental_spans.py
        for comprehensive tests of the new behavior.

        If UserPromptSubmit hasn't run yet, there's no trace to link span to.
        """
        with tempfile.TemporaryDirectory() as state_dir:
            session_id = "test-session-jkl"

            # No state setup - no current_trace_id

            result = handle_post_tool_use(
                config=mock_config,
                session_id=session_id,
                state_dir=state_dir,
                tool_name="Read",
                tool_input={},
                tool_output="output",
            )

            # Should fail gracefully (no crash)
            assert result is False
