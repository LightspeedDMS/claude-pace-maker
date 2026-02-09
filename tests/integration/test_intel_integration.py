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
    """Test orchestrator extracts intel from assistant messages."""
    from pacemaker.langfuse.orchestrator import handle_post_tool_use
    from pacemaker.langfuse.state import StateManager

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

    # Mock config (disabled push)
    config = {
        "langfuse_enabled": False,
        "langfuse_base_url": "http://localhost",
        "langfuse_public_key": "pk-test",
        "langfuse_secret_key": "sk-test",
        "db_path": str(tmp_path / "test.db"),
    }

    # Call orchestrator
    handle_post_tool_use(
        config=config,
        session_id="test_session",
        transcript_path=str(transcript),
        state_dir=str(state_dir),
    )

    # Verify intel was stored in state
    state = state_manager.read("test_session")
    assert state is not None
    assert "pending_intel" in state

    intel = state["pending_intel"]
    assert intel["frustration"] == 0.8
    assert intel["specificity"] == "surg"
    assert intel["task_type"] == "bug"
    assert intel["quality"] == 0.7
    assert intel["iteration"] == 2


def test_orchestrator_stores_partial_intel(tmp_path):
    """Test orchestrator handles partial intel with missing fields."""
    from pacemaker.langfuse.orchestrator import handle_post_tool_use
    from pacemaker.langfuse.state import StateManager

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

    # Verify partial intel stored
    state = state_manager.read("test_session")
    assert state is not None
    assert "pending_intel" in state

    intel = state["pending_intel"]
    assert intel["frustration"] == 0.5
    assert intel["task_type"] == "feat"
    # Missing fields should not be present
    assert "specificity" not in intel
    assert "quality" not in intel
    assert "iteration" not in intel


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
    import tempfile

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
    """Test that pending_intel is attached to trace metadata when trace is pushed."""
    from pacemaker.langfuse.orchestrator import handle_user_prompt_submit
    from pacemaker.langfuse.state import StateManager

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Create transcript with user message
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "user_message",
                    "message": {"role": "user", "content": "Test prompt"},
                }
            )
            + "\n"
        )

        state_dir = tmp_path / "state"
        state_dir.mkdir()

        # Create state with pending_intel
        state_manager = StateManager(str(state_dir))
        state_manager.create_or_update(
            session_id="test_session",
            trace_id="test_trace_001",
            last_pushed_line=0,
            metadata={
                "current_trace_id": "test_trace_001",
            },
        )

        # Add pending_intel to state
        existing_state = state_manager.read("test_session")
        existing_state["pending_intel"] = {
            "frustration": 0.8,
            "specificity": "surg",
            "task_type": "bug",
            "quality": 0.7,
            "iteration": 2,
        }
        state_manager.create_or_update(
            session_id="test_session",
            trace_id=existing_state["trace_id"],
            last_pushed_line=existing_state["last_pushed_line"],
            metadata=existing_state.get("metadata", {}),
        )
        # Manually add pending_intel (state manager doesn't have parameter for it yet)
        state_file = state_dir / "test_session.json"
        with open(state_file, "r") as f:
            state_data = json.load(f)
        state_data["pending_intel"] = existing_state["pending_intel"]
        with open(state_file, "w") as f:
            json.dump(state_data, f)

        config = {
            "langfuse_enabled": True,  # Enable to create trace (stored as pending_trace, not pushed)
            "langfuse_base_url": "http://localhost",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
            "db_path": str(tmp_path / "test.db"),
        }

        # Call user_prompt_submit (creates trace)
        handle_user_prompt_submit(
            config=config,
            session_id="test_session",
            transcript_path=str(transcript),
            state_dir=str(state_dir),
            user_message="Test prompt",
        )

        # Verify pending_trace was created with intel in metadata
        state = state_manager.read("test_session")
        assert state is not None
        assert "pending_trace" in state

        # Find trace-create event in pending_trace
        trace_event = None
        for event in state["pending_trace"]:
            if event.get("type") == "trace-create":
                trace_event = event
                break

        assert trace_event is not None
        trace_body = trace_event.get("body", {})
        metadata = trace_body.get("metadata", {})

        # Verify intel fields are in metadata
        assert "intel_frustration" in metadata
        assert metadata["intel_frustration"] == 0.8
        assert metadata["intel_specificity"] == "surg"
        assert metadata["intel_task_type"] == "bug"
        assert metadata["intel_quality"] == 0.7
        assert metadata["intel_iteration"] == 2
