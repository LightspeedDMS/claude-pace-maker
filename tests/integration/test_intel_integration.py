#!/usr/bin/env python3
"""
Integration tests for intel parsing in orchestrator.

Tests the full flow:
1. Parse intel from assistant response
2. Store in pending_intel state
3. Attach to trace metadata
4. Strip intel line from output
"""

import json
import tempfile
from pathlib import Path


def test_orchestrator_parses_intel_from_transcript(tmp_path):
    """Test orchestrator extracts intel from assistant messages and pushes immediately."""
    from pacemaker.langfuse.orchestrator import handle_post_tool_use
    from pacemaker.langfuse.state import StateManager
    from unittest.mock import patch

    # Create transcript with intel line
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "§ △0.8 ◎surg ■bug ◇0.7 ↻2\nActual response content",
                        }
                    ],
                },
            }
        )
        + "\n"
    )

    # Create state directory
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    # Create initial state with metadata
    state_manager = StateManager(str(state_dir))
    state_manager.create_or_update(
        session_id="test_session",
        trace_id="test_trace",
        last_pushed_line=0,
        metadata={"current_trace_id": "test_trace"},
    )

    # Mock config (ENABLED to allow push)
    config = {
        "langfuse_enabled": True,
        "langfuse_base_url": "http://localhost",
        "langfuse_public_key": "pk-test",
        "langfuse_secret_key": "sk-test",
        "db_path": str(tmp_path / "test.db"),
    }

    # Mock push_batch_events to capture intel batch
    with patch("pacemaker.langfuse.push.push_batch_events") as mock_push:
        mock_push.return_value = (True, 1)

        # Call orchestrator
        handle_post_tool_use(
            config=config,
            session_id="test_session",
            transcript_path=str(transcript),
            state_dir=str(state_dir),
        )

        # Verify intel was pushed (should have at least one call with intel metadata)
        assert mock_push.called

        # Check if any call contained intel metadata
        intel_pushed = False
        for call in mock_push.call_args_list:
            batch = call[0][3]  # Fourth argument is the batch
            for event in batch:
                if event.get("type") == "trace-create":
                    body = event.get("body", {})
                    metadata = body.get("metadata", {})
                    if "intel_frustration" in metadata:
                        intel_pushed = True
                        assert metadata["intel_frustration"] == 0.8
                        assert metadata["intel_specificity"] == "surg"
                        assert metadata["intel_task_type"] == "bug"
                        assert metadata["intel_quality"] == 0.7
                        assert metadata["intel_iteration"] == 2
                        break

        assert intel_pushed, "Intel metadata should have been pushed to Langfuse"


def test_orchestrator_stores_partial_intel(tmp_path):
    """Test orchestrator handles partial intel with missing fields and pushes immediately."""
    from pacemaker.langfuse.orchestrator import handle_post_tool_use
    from pacemaker.langfuse.state import StateManager
    from unittest.mock import patch

    # Create transcript with partial intel
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "§ △0.5 ■feat\nResponse"}],
                },
            }
        )
        + "\n"
    )

    state_dir = tmp_path / "state"
    state_dir.mkdir()

    state_manager = StateManager(str(state_dir))
    state_manager.create_or_update(
        session_id="test_session",
        trace_id="test_trace",
        last_pushed_line=0,
        metadata={"current_trace_id": "test_trace"},
    )

    config = {
        "langfuse_enabled": True,
        "langfuse_base_url": "http://localhost",
        "langfuse_public_key": "pk-test",
        "langfuse_secret_key": "sk-test",
        "db_path": str(tmp_path / "test.db"),
    }

    # Mock push_batch_events to capture intel batch
    with patch("pacemaker.langfuse.push.push_batch_events") as mock_push:
        mock_push.return_value = (True, 1)

        handle_post_tool_use(
            config=config,
            session_id="test_session",
            transcript_path=str(transcript),
            state_dir=str(state_dir),
        )

        # Verify partial intel was pushed
        intel_pushed = False
        for call in mock_push.call_args_list:
            batch = call[0][3]
            for event in batch:
                if event.get("type") == "trace-create":
                    body = event.get("body", {})
                    metadata = body.get("metadata", {})
                    if "intel_frustration" in metadata:
                        intel_pushed = True
                        assert metadata["intel_frustration"] == 0.5
                        assert metadata["intel_task_type"] == "feat"
                        # Missing fields should not be present
                        assert "intel_specificity" not in metadata
                        assert "intel_quality" not in metadata
                        assert "intel_iteration" not in metadata
                        break

        assert intel_pushed, "Partial intel should have been pushed to Langfuse"


def test_orchestrator_handles_no_intel(tmp_path):
    """Test orchestrator when no intel line present."""
    from pacemaker.langfuse.orchestrator import handle_post_tool_use
    from pacemaker.langfuse.state import StateManager

    # Create transcript without intel
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Regular response without intel"}
                    ],
                },
            }
        )
        + "\n"
    )

    state_dir = tmp_path / "state"
    state_dir.mkdir()

    state_manager = StateManager(str(state_dir))
    state_manager.create_or_update(
        session_id="test_session",
        trace_id="test_trace",
        last_pushed_line=0,
        metadata={"current_trace_id": "test_trace"},
    )

    config = {
        "langfuse_enabled": False,
        "langfuse_base_url": "http://localhost",
        "langfuse_public_key": "pk-test",
        "langfuse_secret_key": "sk-test",
        "db_path": str(tmp_path / "test.db"),
    }

    handle_post_tool_use(
        config=config,
        session_id="test_session",
        transcript_path=str(transcript),
        state_dir=str(state_dir),
    )

    # Verify no pending_intel in state
    state = state_manager.read("test_session")
    assert state is not None
    # pending_intel should not exist or be None
    assert state.get("pending_intel") is None


def test_trace_output_strips_intel_line():
    """Test that trace finalization strips intel line from output."""
    from pacemaker.langfuse.trace import finalize_trace_with_output

    # Create temp transcript with intel line
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        transcript_path = f.name
        # Write assistant message with intel
        f.write(
            json.dumps(
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": "§ △0.8 ◎surg ■bug ◇0.7 ↻2\nActual response content here",
                            }
                        ],
                    }
                }
            )
            + "\n"
        )

    try:
        # Finalize trace (reads from line 0)
        trace_update = finalize_trace_with_output(
            trace_id="test_trace", transcript_path=transcript_path, trace_start_line=0
        )

        # Verify output does not contain intel marker
        output = trace_update.get("output", "")
        assert "§" not in output
        assert "△" not in output
        assert "Actual response content here" in output
    finally:
        # Cleanup
        Path(transcript_path).unlink()


def test_trace_metadata_includes_intel():
    """Test that intel is pushed immediately to current trace (new architecture).

    NOTE: This test is now redundant with test_orchestrator_parses_intel_from_transcript
    since intel is pushed immediately in handle_post_tool_use, not stored as pending_intel.
    Keeping this test for backward compatibility but it just verifies the immediate push flow.
    """
    from pacemaker.langfuse.orchestrator import handle_post_tool_use
    from pacemaker.langfuse.state import StateManager
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Create transcript with intel line in assistant response
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": "§ △0.8 ◎surg ■bug ◇0.7 ↻2\nTest response",
                            }
                        ],
                    },
                }
            )
            + "\n"
        )

        state_dir = tmp_path / "state"
        state_dir.mkdir()

        # Create initial state
        state_manager = StateManager(str(state_dir))
        state_manager.create_or_update(
            session_id="test_session",
            trace_id="test_trace_001",
            last_pushed_line=0,
            metadata={"current_trace_id": "test_trace_001"},
        )

        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "http://localhost",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
            "db_path": str(tmp_path / "test.db"),
        }

        # Mock push to capture intel batch
        with patch("pacemaker.langfuse.push.push_batch_events") as mock_push:
            mock_push.return_value = (True, 1)

            # Call post_tool_use (parses intel and pushes immediately)
            handle_post_tool_use(
                config=config,
                session_id="test_session",
                transcript_path=str(transcript),
                state_dir=str(state_dir),
            )

            # Verify intel was pushed in trace metadata
            intel_found = False
            for call in mock_push.call_args_list:
                batch = call[0][3]
                for event in batch:
                    if event.get("type") == "trace-create":
                        metadata = event.get("body", {}).get("metadata", {})
                        if "intel_frustration" in metadata:
                            intel_found = True
                            assert metadata["intel_frustration"] == 0.8
                            assert metadata["intel_specificity"] == "surg"
                            assert metadata["intel_task_type"] == "bug"
                            assert metadata["intel_quality"] == 0.7
                            assert metadata["intel_iteration"] == 2

            assert intel_found, "Intel should be pushed immediately to current trace"
