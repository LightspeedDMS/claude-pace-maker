#!/usr/bin/env python3
"""
Tests for Bug #3 and Bug #4 fixes.

Bug #3: handle_stop_finalize() must flush pending_trace BEFORE finalize_trace_with_output().
Bug #4: handle_user_prompt_submit() must flush old pending_trace BEFORE storing new one.

Written TDD-first to verify the bugs exist and then confirm fixes.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone


from pacemaker.langfuse import state
from pacemaker.langfuse.orchestrator import (
    handle_stop_finalize,
    handle_user_prompt_submit,
)


class TestStopFinalizeFlushesPendingTrace:
    """Bug #3: handle_stop_finalize() must flush pending_trace before finalize."""

    def setup_method(self):
        """Set up test fixtures."""
        self.state_dir = tempfile.mkdtemp()
        self.transcript_dir = tempfile.mkdtemp()
        self.state_manager = state.StateManager(self.state_dir)
        self.config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://test.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }

    def teardown_method(self):
        """Clean up."""
        import shutil

        shutil.rmtree(self.state_dir, ignore_errors=True)
        shutil.rmtree(self.transcript_dir, ignore_errors=True)

    def _create_transcript(self, messages: list) -> str:
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

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    def test_stop_finalize_flushes_pending_trace(self, mock_push):
        """
        Bug #3: handle_stop_finalize() should flush pending_trace before finalize.

        If a pending_trace exists in state when Stop fires, it means the
        trace from handle_user_prompt_submit() was never pushed (no PostToolUse
        happened). Stop must flush it before finalizing with output.
        """
        mock_push.return_value = (True, 1)

        session_id = "test-stop-flush"
        trace_id = f"{session_id}-turn-abc"

        # Create state WITH pending_trace (simulates UserPromptSubmit stored it
        # but no PostToolUse consumed it)
        pending_trace = [
            {
                "id": trace_id,
                "type": "trace-create",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "body": {"id": trace_id, "input": "test question"},
            }
        ]
        self.state_manager.create_or_update(
            session_id=session_id,
            trace_id=trace_id,
            last_pushed_line=0,
            metadata={
                "current_trace_id": trace_id,
                "trace_start_line": 0,
            },
            pending_trace=pending_trace,
        )

        # Create transcript
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "What is 2+2?"}]},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "The answer is 4."}],
            },
        ]
        transcript_path = self._create_transcript(messages)

        # Call handle_stop_finalize
        result = handle_stop_finalize(
            config=self.config,
            session_id=session_id,
            transcript_path=transcript_path,
            state_dir=self.state_dir,
        )

        assert result is True

        # push_batch_events should be called TWICE:
        # 1. First call: flush the pending_trace (trace-create)
        # 2. Second call: finalize trace with output (trace-update/create)
        assert mock_push.call_count >= 2, (
            f"Expected at least 2 push calls (flush + finalize), got {mock_push.call_count}. "
            f"Bug #3: Stop hook does not flush pending_trace."
        )

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    def test_stop_finalize_works_without_pending_trace(self, mock_push):
        """
        Stop should still work normally when there is no pending_trace.

        This is the common path: PostToolUse already flushed the pending_trace.
        """
        mock_push.return_value = (True, 1)

        session_id = "test-stop-no-pending"
        trace_id = f"{session_id}-turn-def"

        # Create state WITHOUT pending_trace
        self.state_manager.create_or_update(
            session_id=session_id,
            trace_id=trace_id,
            last_pushed_line=0,
            metadata={
                "current_trace_id": trace_id,
                "trace_start_line": 0,
            },
        )

        messages = [
            {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Hi there!"}]},
        ]
        transcript_path = self._create_transcript(messages)

        result = handle_stop_finalize(
            config=self.config,
            session_id=session_id,
            transcript_path=transcript_path,
            state_dir=self.state_dir,
        )

        assert result is True
        # Only 1 call: finalize trace with output (no pending flush needed)
        assert mock_push.call_count == 1


class TestUserPromptSubmitFlushesOldPending:
    """Bug #4: handle_user_prompt_submit() must flush old pending before new."""

    def setup_method(self):
        """Set up test fixtures."""
        self.state_dir = tempfile.mkdtemp()
        self.transcript_dir = tempfile.mkdtemp()
        self.state_manager = state.StateManager(self.state_dir)
        self.config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://test.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }

    def teardown_method(self):
        """Clean up."""
        import shutil

        shutil.rmtree(self.state_dir, ignore_errors=True)
        shutil.rmtree(self.transcript_dir, ignore_errors=True)

    def _create_transcript(self) -> str:
        """Create minimal transcript file."""
        transcript_path = Path(self.transcript_dir) / "transcript.jsonl"
        with open(transcript_path, "w") as f:
            f.write(
                json.dumps(
                    {
                        "type": "session_start",
                        "session_id": "test-123",
                        "model": "claude-sonnet-4-5",
                        "user_email": "user@test.com",
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "message": {"role": "user", "content": "first prompt"},
                    }
                )
                + "\n"
            )
        return str(transcript_path)

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    def test_user_prompt_flushes_old_pending_before_new(self, mock_push):
        """
        Bug #4: Rapid-fire prompts must not silently lose traces.

        When handle_user_prompt_submit() is called and there is already a
        pending_trace in state, it must flush the old one before storing the new.
        """
        mock_push.return_value = (True, 1)

        session_id = "test-rapid-fire"
        old_trace_id = f"{session_id}-turn-old"

        # Create state WITH existing pending_trace (first prompt stored but not pushed)
        old_pending_trace = [
            {
                "id": old_trace_id,
                "type": "trace-create",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "body": {"id": old_trace_id, "input": "first question"},
            }
        ]
        self.state_manager.create_or_update(
            session_id=session_id,
            trace_id=old_trace_id,
            last_pushed_line=0,
            metadata={
                "current_trace_id": old_trace_id,
                "trace_start_line": 0,
            },
            pending_trace=old_pending_trace,
        )

        transcript_path = self._create_transcript()

        # Now submit a NEW prompt (second one, rapid-fire)
        result = handle_user_prompt_submit(
            config=self.config,
            session_id=session_id,
            transcript_path=transcript_path,
            state_dir=self.state_dir,
            user_message="second question",
        )

        assert result is True

        # push_batch_events should have been called at least once (to flush old pending)
        assert mock_push.call_count >= 1, (
            f"Expected at least 1 push call (flush old pending), got {mock_push.call_count}. "
            f"Bug #4: Old pending_trace silently lost."
        )

        # Verify state now has a NEW pending_trace (not the old one)
        new_state = self.state_manager.read(session_id)
        assert new_state is not None
        new_pending = new_state.get("pending_trace")
        assert new_pending is not None, "New pending_trace should be stored"

        # Verify the new pending trace has a different trace_id than the old one
        new_trace_body = new_pending[0]["body"]
        assert (
            new_trace_body["id"] != old_trace_id
        ), "New pending trace should have a different ID than the old one"

    def test_user_prompt_works_without_existing_pending(self):
        """
        First prompt: no old pending to flush, should store new pending normally.

        This is the happy path where there's no prior pending_trace.
        """
        session_id = "test-first-prompt"
        transcript_path = self._create_transcript()

        result = handle_user_prompt_submit(
            config=self.config,
            session_id=session_id,
            transcript_path=transcript_path,
            state_dir=self.state_dir,
            user_message="first question ever",
        )

        assert result is True

        # Verify pending_trace is stored
        new_state = self.state_manager.read(session_id)
        assert new_state is not None
        assert "pending_trace" in new_state
