"""Tests for per-turn token counting in generation observations."""

import json
import tempfile
import os
from unittest.mock import patch


def _create_transcript_with_turns(path, turns):
    """Create a JSONL transcript with multiple turns.

    Each turn is a list of entries (dicts). Each entry becomes a JSONL line.
    """
    with open(path, "w") as f:
        for turn in turns:
            for entry in turn:
                f.write(json.dumps(entry) + "\n")


def _make_assistant_message(input_tokens, output_tokens, cache_read=0):
    """Create a transcript entry with token usage."""
    return {
        "type": "assistant",
        "message": {
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read,
            },
            "content": [{"type": "text", "text": "response"}],
        },
    }


def test_trace_start_line_uses_transcript_line_count():
    """trace_start_line should be set from transcript line count, not last_pushed_line."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create transcript with 10 lines (simulating previous turns)
        transcript_path = os.path.join(tmpdir, "test.jsonl")
        with open(transcript_path, "w") as f:
            for i in range(10):
                f.write(json.dumps({"type": "text", "line": i}) + "\n")

        state_dir = os.path.join(tmpdir, "state")
        os.makedirs(state_dir)

        # Create existing state with last_pushed_line=0 (the bug condition)
        state_file = os.path.join(state_dir, "test-session.json")
        with open(state_file, "w") as f:
            json.dump(
                {
                    "session_id": "test-session",
                    "trace_id": "old-trace",
                    "last_pushed_line": 0,
                    "metadata": {
                        "current_trace_id": "old-trace",
                        "trace_start_line": 0,
                    },
                },
                f,
            )

        from pacemaker.langfuse.orchestrator import handle_user_prompt_submit

        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "http://localhost:3000",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }

        # Mock push to not actually call Langfuse
        with patch("pacemaker.langfuse.push.push_batch_events", return_value=(True, 1)):
            with patch(
                "pacemaker.telemetry.jsonl_parser.extract_user_id",
                return_value="test-user",
            ):
                with patch(
                    "pacemaker.telemetry.jsonl_parser.parse_session_metadata",
                    return_value={"model": "test"},
                ):
                    handle_user_prompt_submit(
                        config=config,
                        session_id="test-session",
                        transcript_path=transcript_path,
                        state_dir=state_dir,
                        user_message="test prompt",
                    )

        # Read back state and check trace_start_line
        with open(state_file, "r") as f:
            new_state = json.load(f)

        # trace_start_line should be 10 (number of lines), NOT 0 (last_pushed_line)
        assert (
            new_state["metadata"]["trace_start_line"] == 10
        ), f"Expected trace_start_line=10 (transcript lines), got {new_state['metadata']['trace_start_line']}"


def test_generation_uses_trace_start_line_not_zero():
    """handle_stop_finalize should use trace_start_line, not hardcoded 0."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create transcript with 2 turns:
        # Turn 1 (lines 1-5): 1000 input, 5000 output
        # Turn 2 (lines 6-8): 100 input, 200 output
        transcript_path = os.path.join(tmpdir, "test.jsonl")
        turn1 = [
            _make_assistant_message(500, 2000),
            {"type": "tool_use", "name": "Read"},
            _make_assistant_message(500, 3000),
            {"type": "text", "text": "turn 1 end"},
            {"type": "user", "text": "next prompt"},
        ]
        turn2 = [
            _make_assistant_message(50, 100),
            {"type": "tool_use", "name": "Write"},
            _make_assistant_message(
                50, 100, cache_read=1
            ),  # Different from first to avoid dedup
        ]
        _create_transcript_with_turns(transcript_path, [turn1, turn2])

        state_dir = os.path.join(tmpdir, "state")
        os.makedirs(state_dir)

        # State with trace_start_line=5 (turn 2 starts at line 5)
        state_file = os.path.join(state_dir, "test-session.json")
        with open(state_file, "w") as f:
            json.dump(
                {
                    "session_id": "test-session",
                    "trace_id": "test-trace-turn2",
                    "last_pushed_line": 0,
                    "metadata": {
                        "current_trace_id": "test-trace-turn2",
                        "trace_start_line": 5,
                    },
                },
                f,
            )

        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "http://localhost:3000",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }

        # Track what gets pushed
        pushed_batches = []

        def capture_push(base_url, pk, sk, batch, timeout=None):
            pushed_batches.append(batch)
            return (True, len(batch))

        with patch(
            "pacemaker.langfuse.push.push_batch_events", side_effect=capture_push
        ):
            with patch(
                "pacemaker.telemetry.jsonl_parser.parse_session_metadata",
                return_value={"model": "claude-opus-4-6"},
            ):
                from pacemaker.langfuse.orchestrator import handle_stop_finalize

                handle_stop_finalize(
                    config=config,
                    session_id="test-session",
                    transcript_path=transcript_path,
                    state_dir=state_dir,
                )

        # Find the generation event in pushed batches
        generation = None
        for batch in pushed_batches:
            for event in batch:
                if event.get("type") == "generation-create":
                    generation = event["body"]

        assert generation is not None, "No generation event found"

        # Should only have turn 2's tokens (100 input, 200 output), NOT accumulated (1100, 5200)
        assert (
            generation["usage"]["input"] == 100
        ), f"Expected 100 input tokens (turn 2 only), got {generation['usage']['input']}"
        assert (
            generation["usage"]["output"] == 200
        ), f"Expected 200 output tokens (turn 2 only), got {generation['usage']['output']}"


def test_first_turn_counts_all_tokens():
    """First turn (trace_start_line=0) should count all tokens."""
    with tempfile.TemporaryDirectory() as tmpdir:
        transcript_path = os.path.join(tmpdir, "test.jsonl")
        entries = [
            _make_assistant_message(500, 2000),
            _make_assistant_message(500, 3000),
        ]
        _create_transcript_with_turns(transcript_path, [entries])

        state_dir = os.path.join(tmpdir, "state")
        os.makedirs(state_dir)

        state_file = os.path.join(state_dir, "test-session.json")
        with open(state_file, "w") as f:
            json.dump(
                {
                    "session_id": "test-session",
                    "trace_id": "test-trace-first",
                    "last_pushed_line": 0,
                    "metadata": {
                        "current_trace_id": "test-trace-first",
                        "trace_start_line": 0,
                    },
                },
                f,
            )

        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "http://localhost:3000",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }

        pushed_batches = []

        def capture_push(base_url, pk, sk, batch, timeout=None):
            pushed_batches.append(batch)
            return (True, len(batch))

        with patch(
            "pacemaker.langfuse.push.push_batch_events", side_effect=capture_push
        ):
            with patch(
                "pacemaker.telemetry.jsonl_parser.parse_session_metadata",
                return_value={"model": "claude-opus-4-6"},
            ):
                from pacemaker.langfuse.orchestrator import handle_stop_finalize

                handle_stop_finalize(
                    config=config,
                    session_id="test-session",
                    transcript_path=transcript_path,
                    state_dir=state_dir,
                )

        generation = None
        for batch in pushed_batches:
            for event in batch:
                if event.get("type") == "generation-create":
                    generation = event["body"]

        assert generation is not None, "No generation event found"
        # First turn: all tokens (1000 input, 5000 output)
        assert generation["usage"]["input"] == 1000
        assert generation["usage"]["output"] == 5000
