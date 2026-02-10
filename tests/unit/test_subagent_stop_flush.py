#!/usr/bin/env python3
"""
Tests for Bug #1 completion: SubagentStop must flush parent's pending_trace.

The pending_trace is only consumed in PostToolUse. When a subagent runs and
stops, the SubagentStop hook fires but never flushes the parent's pending_trace.
This means 91% of traces are lost (the typical flow is: UserPromptSubmit ->
SubagentStart -> SubagentStop -> Stop, with NO PostToolUse firing).

Fix: In run_subagent_stop_hook(), after handle_subagent_stop(), also flush
the parent session's pending_trace via flush_pending_trace().

Tests use the orchestrator.flush_pending_trace directly since hook integration
with stdin/stdout makes unit testing the full hook complex.
"""

import json
import tempfile
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone


from pacemaker.langfuse import state as langfuse_state
from pacemaker.langfuse.orchestrator import flush_pending_trace


class TestSubagentStopFlushesPendingTrace:
    """Bug #1 completion: SubagentStop must flush parent's pending_trace."""

    def setup_method(self):
        """Set up test fixtures."""
        self.state_dir = tempfile.mkdtemp()
        self.state_manager = langfuse_state.StateManager(self.state_dir)
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

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    def testflush_pending_trace_works_for_parent_session(self, mock_push):
        """
        The flush_pending_trace helper should work when called with parent
        session's state during SubagentStop.
        """
        mock_push.return_value = (True, 1)

        parent_session_id = "parent-session-abc"
        trace_id = f"{parent_session_id}-turn-123"

        # Create parent state with pending_trace
        pending_trace = [
            {
                "id": trace_id,
                "type": "trace-create",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "body": {"id": trace_id, "input": "user's question"},
            }
        ]
        self.state_manager.create_or_update(
            session_id=parent_session_id,
            trace_id=trace_id,
            last_pushed_line=0,
            metadata={
                "current_trace_id": trace_id,
                "trace_start_line": 0,
            },
            pending_trace=pending_trace,
        )

        # Read parent state
        parent_state = self.state_manager.read(parent_session_id)

        # Flush from SubagentStop context
        result = flush_pending_trace(
            config=self.config,
            session_id=parent_session_id,
            state_manager=self.state_manager,
            existing_state=parent_state,
            caller="subagent_stop",
        )

        assert result is True
        assert mock_push.called

        # Verify pending_trace is cleared
        updated_state = self.state_manager.read(parent_session_id)
        assert "pending_trace" not in updated_state

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    def test_no_flush_when_parent_has_no_pending_trace(self, mock_push):
        """
        If parent has no pending_trace (it was already flushed by PostToolUse),
        SubagentStop should not push anything extra.
        """
        parent_session_id = "parent-no-pending"
        trace_id = f"{parent_session_id}-turn-456"

        # Create parent state WITHOUT pending_trace
        self.state_manager.create_or_update(
            session_id=parent_session_id,
            trace_id=trace_id,
            last_pushed_line=5,
            metadata={"current_trace_id": trace_id},
        )

        parent_state = self.state_manager.read(parent_session_id)

        result = flush_pending_trace(
            config=self.config,
            session_id=parent_session_id,
            state_manager=self.state_manager,
            existing_state=parent_state,
            caller="subagent_stop",
        )

        assert result is False
        mock_push.assert_not_called()

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    def test_flush_after_subagent_stop_typical_flow(self, mock_push):
        """
        Test the typical flow: UserPromptSubmit stores pending_trace,
        SubagentStart/SubagentStop happen, then Stop finalizes.

        The pending_trace from UserPromptSubmit should be flushed
        when SubagentStop calls flush_pending_trace.
        """
        mock_push.return_value = (True, 1)

        parent_session_id = "parent-typical"
        trace_id = f"{parent_session_id}-turn-typical"

        # Step 1: UserPromptSubmit stores pending_trace
        pending_trace = [
            {
                "id": trace_id,
                "type": "trace-create",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "body": {"id": trace_id, "input": "implement feature"},
            }
        ]
        self.state_manager.create_or_update(
            session_id=parent_session_id,
            trace_id=trace_id,
            last_pushed_line=0,
            metadata={
                "current_trace_id": trace_id,
                "trace_start_line": 0,
                "is_first_trace_in_session": True,
            },
            pending_trace=pending_trace,
        )

        # Step 2: SubagentStop fires - flush parent's pending_trace
        parent_state = self.state_manager.read(parent_session_id)
        result = flush_pending_trace(
            config=self.config,
            session_id=parent_session_id,
            state_manager=self.state_manager,
            existing_state=parent_state,
            caller="subagent_stop",
        )

        assert result is True

        # Verify the pending_trace was pushed to Langfuse
        pushed_batch = mock_push.call_args[0][3]
        assert len(pushed_batch) == 1
        assert pushed_batch[0]["body"]["id"] == trace_id

        # Step 3: Verify state is clean (pending_trace removed)
        final_state = self.state_manager.read(parent_session_id)
        assert "pending_trace" not in final_state
        # But trace_id and other fields should be preserved
        assert final_state["trace_id"] == trace_id


class TestRunSubagentStopHookFlush:
    """Integration: run_subagent_stop_hook() must call flush_pending_trace."""

    def setup_method(self):
        """Set up test fixtures."""
        self.state_dir = tempfile.mkdtemp()
        self.state_manager = langfuse_state.StateManager(self.state_dir)

    def teardown_method(self):
        """Clean up."""
        import shutil

        shutil.rmtree(self.state_dir, ignore_errors=True)

    @patch("pacemaker.langfuse.orchestrator.flush_pending_trace")
    @patch("pacemaker.langfuse.orchestrator.handle_subagent_stop")
    @patch("pacemaker.langfuse.state.StateManager")
    @patch("pacemaker.hook.load_config")
    @patch("pacemaker.hook.load_state")
    @patch("pacemaker.hook.save_state")
    @patch("sys.stdin")
    def test_run_subagent_stop_hook_callsflush_pending_trace(
        self,
        mock_stdin,
        mock_save,
        mock_load_state,
        mock_config,
        mock_state_mgr_cls,
        mock_subagent_stop,
        mock_flush,
    ):
        """
        Bug #1: run_subagent_stop_hook() must call flush_pending_trace()
        for the parent session after handle_subagent_stop().
        """
        from pacemaker.hook import run_subagent_stop_hook

        # Mock stdin with hook data
        hook_data = json.dumps(
            {
                "session_id": "parent-session-id",
                "agent_id": "agent-xyz",
            }
        )
        mock_stdin.read.return_value = hook_data

        # Mock config with Langfuse enabled
        mock_config.return_value = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://test.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }

        # Mock state with subagent trace info
        mock_load_state.return_value = {
            "session_id": "parent-session-id",
            "subagent_counter": 1,
            "in_subagent": True,
            "subagent_traces": {
                "agent-xyz": {
                    "trace_id": "subagent-trace-id",
                    "parent_transcript_path": "/path/to/transcript.jsonl",
                }
            },
            "current_subagent_trace_id": "subagent-trace-id",
            "current_subagent_agent_id": "agent-xyz",
            "current_subagent_parent_transcript_path": "/path/to/transcript.jsonl",
        }

        # Mock the StateManager instance to return state with pending_trace
        mock_state_mgr_instance = MagicMock()
        mock_state_mgr_instance.read.return_value = {
            "session_id": "parent-session-id",
            "trace_id": "parent-trace-id",
            "last_pushed_line": 0,
            "metadata": {
                "current_trace_id": "parent-trace-id",
                "trace_start_line": 0,
            },
            "pending_trace": [
                {
                    "id": "parent-trace-id",
                    "type": "trace-create",
                    "body": {"id": "parent-trace-id", "input": "test"},
                    "timestamp": "2024-01-01T00:00:00Z",
                }
            ],
        }
        mock_state_mgr_cls.return_value = mock_state_mgr_instance

        mock_subagent_stop.return_value = True
        mock_flush.return_value = True

        # Run the hook
        run_subagent_stop_hook()

        # Verify flush_pending_trace was called
        assert mock_flush.called, (
            "Bug #1: run_subagent_stop_hook() did not call flush_pending_trace(). "
            "Parent's pending trace will be lost."
        )
