#!/usr/bin/env python3
"""
Integration test for orchestrator creating spans from transcript parsing.

This tests the REFACTORED architecture where:
- handle_post_tool_use() receives transcript_path (not just tool_input/tool_output)
- Orchestrator parses transcript incrementally using extract_content_blocks()
- Creates spans for ALL content: text blocks AND tool_use blocks
- Updates last_pushed_line in state after successful push
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from pacemaker.langfuse import orchestrator, state


class TestOrchestratorIncrementalSpans:
    """Test orchestrator creates text AND tool spans from transcript."""

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
    def transcript_with_text_and_tool(self):
        """Create transcript with text and tool_use content."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # Line 1: Session start
            f.write(
                json.dumps({"type": "session_start", "session_id": "test-123"}) + "\n"
            )

            # Line 2: User message
            f.write(
                json.dumps(
                    {
                        "type": "user",
                        "message": {"role": "user", "content": "Read the file"},
                    }
                )
                + "\n"
            )

            # Line 3: Assistant message with TEXT then TOOL_USE
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "text", "text": "Let me check that file..."},
                                {
                                    "type": "tool_use",
                                    "id": "toolu_123",
                                    "name": "Read",
                                    "input": {"file_path": "/test.py"},
                                },
                            ],
                        },
                        "timestamp": "2024-01-01T12:00:00Z",
                        "uuid": "msg-uuid-123",
                    }
                )
                + "\n"
            )

            transcript_path = f.name

        yield transcript_path
        Path(transcript_path).unlink()

    @pytest.fixture
    def state_dir(self):
        """Create temporary state directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_handle_post_tool_use_creates_text_and_tool_spans(
        self, config, transcript_with_text_and_tool, state_dir
    ):
        """
        Test that handle_post_tool_use creates BOTH text and tool spans.

        This verifies the refactoring from hook-data-driven to transcript-parsing:
        - OLD: Only tool span created from hook parameters
        - NEW: Text span + tool span created from transcript parsing
        """
        session_id = "test-session-123"
        trace_id = f"{session_id}-turn-abc"

        # Setup: Create state with current_trace_id and last_pushed_line=2
        # (Simulate that lines 1-2 were already processed)
        # Note: No pending_trace since it was already pushed in a previous call
        state_manager = state.StateManager(state_dir)
        state_manager.create_or_update(
            session_id=session_id,
            trace_id=trace_id,
            last_pushed_line=2,
            metadata={
                "current_trace_id": trace_id,
                "trace_start_line": 0,
            },
            # No pending_trace - simulate it was already pushed
        )

        # Mock push_batch_events to capture what spans are created
        with patch(
            "pacemaker.langfuse.orchestrator.push.push_batch_events"
        ) as mock_push:
            mock_push.return_value = (True, 2)

            # Call handle_post_tool_use with transcript_path
            success = orchestrator.handle_post_tool_use(
                config=config,
                session_id=session_id,
                transcript_path=transcript_with_text_and_tool,
                state_dir=state_dir,
            )

            assert success is True

            # Verify push_batch_events was called
            assert mock_push.called

            # Extract the batch that was pushed
            call_args = mock_push.call_args
            batch = call_args[0][3]  # 4th positional arg

            # Should have pushed 2 spans: text + tool_use
            assert len(batch) == 2

            # First span should be text span
            text_event = batch[0]
            assert text_event["type"] == "span-create"
            text_span = text_event["body"]
            assert text_span["traceId"] == trace_id
            assert text_span["name"] == "Assistant Response"
            assert text_span["output"] == "Let me check that file..."
            assert text_span["metadata"]["type"] == "text"

            # Second span should be tool span
            tool_event = batch[1]
            assert tool_event["type"] == "span-create"
            tool_span = tool_event["body"]
            assert tool_span["traceId"] == trace_id
            assert tool_span["name"] == "Tool - Read"
            assert tool_span["input"] == {"file_path": "/test.py"}

        # Verify state was updated with new last_pushed_line
        updated_state = state_manager.read(session_id)
        assert updated_state["last_pushed_line"] == 3  # Line 3 was processed

    def test_handle_post_tool_use_incremental_no_duplicates(
        self, config, transcript_with_text_and_tool, state_dir
    ):
        """
        Test that repeated calls don't create duplicate spans.

        Verifies that last_pushed_line prevents re-processing.
        """
        session_id = "test-session-456"
        trace_id = f"{session_id}-turn-def"

        # Setup state with last_pushed_line=2
        state_manager = state.StateManager(state_dir)
        state_manager.create_or_update(
            session_id=session_id,
            trace_id=trace_id,
            last_pushed_line=2,
            metadata={"current_trace_id": trace_id},
        )

        with patch(
            "pacemaker.langfuse.orchestrator.push.push_batch_events"
        ) as mock_push:
            mock_push.return_value = (True, 2)

            # First call - should create 2 spans (line 3)
            orchestrator.handle_post_tool_use(
                config=config,
                session_id=session_id,
                transcript_path=transcript_with_text_and_tool,
                state_dir=state_dir,
            )

            first_batch = mock_push.call_args[0][3]
            assert len(first_batch) == 2

            # Second call - should create 0 spans (no new lines)
            orchestrator.handle_post_tool_use(
                config=config,
                session_id=session_id,
                transcript_path=transcript_with_text_and_tool,
                state_dir=state_dir,
            )

            # Push should not be called again (no new content)
            # Mock call count should still be 1
            assert mock_push.call_count == 1

    def test_handle_post_tool_use_returns_true_when_no_new_content(
        self, config, transcript_with_text_and_tool, state_dir
    ):
        """
        Test that function returns True when no new content to push.

        This is not an error condition - just means transcript hasn't advanced.
        """
        session_id = "test-session-789"
        trace_id = f"{session_id}-turn-ghi"

        # Setup state with last_pushed_line=3 (all content already processed)
        state_manager = state.StateManager(state_dir)
        state_manager.create_or_update(
            session_id=session_id,
            trace_id=trace_id,
            last_pushed_line=3,
            metadata={"current_trace_id": trace_id},
            # No pending_trace - already processed
        )

        success = orchestrator.handle_post_tool_use(
            config=config,
            session_id=session_id,
            transcript_path=transcript_with_text_and_tool,
            state_dir=state_dir,
        )

        # Should return True (success, just no new content)
        assert success is True
