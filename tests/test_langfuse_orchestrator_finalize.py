#!/usr/bin/env python3
"""
Tests for Langfuse orchestrator handle_stop_finalize function.

Tests the Stop hook finalization workflow that extracts Claude's output
and pushes trace-update to Langfuse.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from pacemaker.langfuse.orchestrator import handle_stop_finalize


class TestHandleStopFinalize:
    """Test trace finalization in Stop hook."""

    def setup_method(self):
        """Set up test fixtures before each test."""
        # Create temporary directories for state and transcript
        self.state_dir = tempfile.mkdtemp()
        self.transcript_dir = tempfile.mkdtemp()

    def teardown_method(self):
        """Clean up after each test."""
        import shutil

        shutil.rmtree(self.state_dir, ignore_errors=True)
        shutil.rmtree(self.transcript_dir, ignore_errors=True)

    def create_state_file(self, session_id: str, state_data: dict):
        """Create state file for testing."""
        state_path = Path(self.state_dir) / f"{session_id}.json"
        with open(state_path, "w") as f:
            json.dump(state_data, f)

    def create_transcript(self, messages: list) -> str:
        """Create temporary transcript file."""
        transcript_path = Path(self.transcript_dir) / "transcript.jsonl"
        with open(transcript_path, "w") as f:
            for msg in messages:
                entry = {
                    "type": msg["role"],
                    "message": {"role": msg["role"], "content": msg["content"]},
                }
                f.write(json.dumps(entry) + "\n")
        return str(transcript_path)

    def test_finalize_disabled_when_langfuse_not_enabled(self):
        """
        Test that finalization is skipped when Langfuse is disabled.

        Should return True (success) without doing anything.
        """
        config = {"langfuse_enabled": False}
        session_id = "test-session-disabled"

        result = handle_stop_finalize(
            config=config,
            session_id=session_id,
            transcript_path="/tmp/nonexistent.jsonl",
            state_dir=self.state_dir,
        )

        # Should succeed without error (graceful skip)
        assert result is True

    def test_finalize_disabled_when_credentials_missing(self):
        """
        Test that finalization is skipped when credentials are incomplete.

        Should return True (graceful skip) when enabled but credentials missing.
        Follows graceful degradation pattern used by other orchestrator functions.
        """
        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://example.com",
            # Missing public_key and secret_key
        }
        session_id = "test-session-no-creds"

        result = handle_stop_finalize(
            config=config,
            session_id=session_id,
            transcript_path="/tmp/nonexistent.jsonl",
            state_dir=self.state_dir,
        )

        # Should succeed gracefully (misconfiguration doesn't crash the hook)
        assert result is True

    def test_finalize_fails_when_no_state_exists(self):
        """
        Test that finalization fails when no state file exists for session.

        Cannot finalize without knowing current_trace_id and trace_start_line.
        """
        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://example.com",
            "langfuse_public_key": "pk_test",
            "langfuse_secret_key": "sk_test",
        }
        session_id = "test-session-no-state"

        result = handle_stop_finalize(
            config=config,
            session_id=session_id,
            transcript_path="/tmp/nonexistent.jsonl",
            state_dir=self.state_dir,
        )

        # Should fail (no state to finalize)
        assert result is False

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    def test_finalize_creates_trace_update_with_output(self, mock_push):
        """
        Test that finalization creates trace-update event with output field.

        Workflow:
        1. Read state to get current_trace_id and trace_start_line
        2. Extract output from transcript
        3. Create trace-update event
        4. Push to Langfuse
        """
        # Setup
        mock_push.return_value = True

        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://example.com",
            "langfuse_public_key": "pk_test",
            "langfuse_secret_key": "sk_test",
        }
        session_id = "test-session-finalize"

        # Create state file with current trace info
        state_data = {
            "session_id": session_id,
            "trace_id": f"{session_id}-turn-1",
            "last_pushed_line": 0,
            "metadata": {
                "current_trace_id": f"{session_id}-turn-1",
                "trace_start_line": 0,
            },
        }
        self.create_state_file(session_id, state_data)

        # Create transcript with assistant response
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "What is 2+2?"}]},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "The answer is 4."}],
            },
        ]
        transcript_path = self.create_transcript(messages)

        # Execute
        result = handle_stop_finalize(
            config=config,
            session_id=session_id,
            transcript_path=transcript_path,
            state_dir=self.state_dir,
        )

        # Verify success
        assert result is True

        # Verify push was called with trace-update event
        assert mock_push.called
        call_args = mock_push.call_args
        batch = call_args[0][3]  # Fourth positional arg is batch

        # Verify batch structure
        assert len(batch) == 1
        event = batch[0]
        # Note: Implementation uses trace-create for upsert semantics (not trace-update)
        assert event["type"] == "trace-create"
        assert event["body"]["id"] == f"{session_id}-turn-1"
        assert event["body"]["output"] == "The answer is 4."

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    def test_finalize_uses_trace_start_line_from_state(self, mock_push):
        """
        Test that finalization uses trace_start_line from state.

        When trace starts at line 5, should only read transcript from line 5 forward.
        """
        # Setup
        mock_push.return_value = True

        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://example.com",
            "langfuse_public_key": "pk_test",
            "langfuse_secret_key": "sk_test",
        }
        session_id = "test-session-offset"

        # Create state with trace_start_line=2
        state_data = {
            "session_id": session_id,
            "trace_id": f"{session_id}-turn-2",
            "last_pushed_line": 2,
            "metadata": {
                "current_trace_id": f"{session_id}-turn-2",
                "trace_start_line": 2,
            },
        }
        self.create_state_file(session_id, state_data)

        # Create transcript with multiple turns
        messages = [
            # First turn (lines 0-1) - should be ignored
            {"role": "user", "content": [{"type": "text", "text": "First"}]},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "First response"}],
            },
            # Second turn (lines 2-3) - this is what we want
            {"role": "user", "content": [{"type": "text", "text": "Second"}]},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Second response"}],
            },
        ]
        transcript_path = self.create_transcript(messages)

        # Execute
        result = handle_stop_finalize(
            config=config,
            session_id=session_id,
            transcript_path=transcript_path,
            state_dir=self.state_dir,
        )

        # Verify success
        assert result is True

        # Verify output is from second turn only
        call_args = mock_push.call_args
        batch = call_args[0][3]
        event = batch[0]
        assert event["body"]["output"] == "Second response"

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    def test_finalize_fails_when_push_fails(self, mock_push):
        """
        Test that finalization returns False when push to Langfuse fails.

        Should handle push failures gracefully and return False.
        """
        # Setup: push fails
        mock_push.return_value = False

        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://example.com",
            "langfuse_public_key": "pk_test",
            "langfuse_secret_key": "sk_test",
        }
        session_id = "test-session-push-fail"

        # Create state
        state_data = {
            "session_id": session_id,
            "trace_id": f"{session_id}-turn-1",
            "last_pushed_line": 0,
            "metadata": {
                "current_trace_id": f"{session_id}-turn-1",
                "trace_start_line": 0,
            },
        }
        self.create_state_file(session_id, state_data)

        # Create transcript
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "Test"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Response"}]},
        ]
        transcript_path = self.create_transcript(messages)

        # Execute
        result = handle_stop_finalize(
            config=config,
            session_id=session_id,
            transcript_path=transcript_path,
            state_dir=self.state_dir,
        )

        # Should fail when push fails
        assert result is False

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    def test_finalize_gracefully_handles_exceptions(self, mock_push):
        """
        Test that finalization handles exceptions gracefully.

        Should catch exceptions and return False without crashing.
        """
        # Setup: push raises exception
        mock_push.side_effect = Exception("Network error")

        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://example.com",
            "langfuse_public_key": "pk_test",
            "langfuse_secret_key": "sk_test",
        }
        session_id = "test-session-exception"

        # Create state
        state_data = {
            "session_id": session_id,
            "trace_id": f"{session_id}-turn-1",
            "last_pushed_line": 0,
            "metadata": {
                "current_trace_id": f"{session_id}-turn-1",
                "trace_start_line": 0,
            },
        }
        self.create_state_file(session_id, state_data)

        # Create transcript
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "Test"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Response"}]},
        ]
        transcript_path = self.create_transcript(messages)

        # Execute - should not raise exception
        result = handle_stop_finalize(
            config=config,
            session_id=session_id,
            transcript_path=transcript_path,
            state_dir=self.state_dir,
        )

        # Should fail gracefully
        assert result is False
