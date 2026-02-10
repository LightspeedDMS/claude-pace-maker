#!/usr/bin/env python3
"""
Tests for flush_pending_trace() helper in orchestrator.py.

This helper extracts the "check pending_trace -> sanitize -> push -> clear state"
pattern into a shared function used by:
- handle_post_tool_use() (existing)
- handle_stop_finalize() (Bug #3)
- handle_user_prompt_submit() (Bug #4 - flush old before new)
- SubagentStop flow (Bug #1 completion)

Tests follow TDD: written BEFORE the implementation.
"""

import tempfile
from unittest.mock import patch


from pacemaker.langfuse import state


class TestFlushPendingTrace:
    """Test flush_pending_trace() helper function."""

    def setup_method(self):
        """Set up test fixtures."""
        self.state_dir = tempfile.mkdtemp()
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

    def _create_state_with_pending(
        self,
        session_id: str,
        pending_trace: list,
        trace_id: str = "test-trace-id",
        last_pushed_line: int = 0,
        is_first_trace: bool = False,
    ) -> dict:
        """Helper to create state file with pending_trace."""
        metadata = {
            "current_trace_id": trace_id,
            "trace_start_line": 0,
            "is_first_trace_in_session": is_first_trace,
        }
        self.state_manager.create_or_update(
            session_id=session_id,
            trace_id=trace_id,
            last_pushed_line=last_pushed_line,
            metadata=metadata,
            pending_trace=pending_trace,
        )
        return self.state_manager.read(session_id)

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    def test_flush_returns_true_when_pending_trace_pushed_successfully(self, mock_push):
        """Flush should return True when pending trace is pushed OK."""
        from pacemaker.langfuse.orchestrator import flush_pending_trace

        mock_push.return_value = (True, 1)
        session_id = "test-flush-ok"
        pending_trace = [
            {
                "id": "evt-1",
                "type": "trace-create",
                "body": {"id": "trace-1"},
                "timestamp": "2024-01-01T00:00:00Z",
            }
        ]
        existing_state = self._create_state_with_pending(session_id, pending_trace)

        result = flush_pending_trace(
            config=self.config,
            session_id=session_id,
            state_manager=self.state_manager,
            existing_state=existing_state,
            caller="test",
        )

        assert result is True

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    def test_flush_returns_false_when_no_pending_trace(self, mock_push):
        """Flush should return False when no pending_trace exists in state."""
        from pacemaker.langfuse.orchestrator import flush_pending_trace

        session_id = "test-no-pending"
        # Create state WITHOUT pending_trace
        self.state_manager.create_or_update(
            session_id=session_id,
            trace_id="some-trace",
            last_pushed_line=0,
            metadata={"current_trace_id": "some-trace"},
        )
        existing_state = self.state_manager.read(session_id)

        result = flush_pending_trace(
            config=self.config,
            session_id=session_id,
            state_manager=self.state_manager,
            existing_state=existing_state,
            caller="test",
        )

        assert result is False
        # push should NOT be called
        mock_push.assert_not_called()

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    def test_flush_clears_pending_trace_from_state_after_push(self, mock_push):
        """After successful flush, pending_trace should be removed from state."""
        from pacemaker.langfuse.orchestrator import flush_pending_trace

        mock_push.return_value = (True, 1)
        session_id = "test-clear-state"
        pending_trace = [
            {
                "id": "evt-1",
                "type": "trace-create",
                "body": {"id": "trace-1"},
                "timestamp": "2024-01-01T00:00:00Z",
            }
        ]
        existing_state = self._create_state_with_pending(session_id, pending_trace)

        flush_pending_trace(
            config=self.config,
            session_id=session_id,
            state_manager=self.state_manager,
            existing_state=existing_state,
            caller="test",
        )

        # Re-read state and verify pending_trace is gone
        updated_state = self.state_manager.read(session_id)
        assert "pending_trace" not in updated_state

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    def test_flush_clears_pending_trace_even_on_push_failure(self, mock_push):
        """Even when push fails, pending_trace should be cleared to prevent retry loops."""
        from pacemaker.langfuse.orchestrator import flush_pending_trace

        mock_push.return_value = (False, 0)
        session_id = "test-push-fail"
        pending_trace = [
            {
                "id": "evt-1",
                "type": "trace-create",
                "body": {"id": "trace-1"},
                "timestamp": "2024-01-01T00:00:00Z",
            }
        ]
        existing_state = self._create_state_with_pending(session_id, pending_trace)

        result = flush_pending_trace(
            config=self.config,
            session_id=session_id,
            state_manager=self.state_manager,
            existing_state=existing_state,
            caller="test",
        )

        # Should still return True (flush completed, even if push failed)
        # The important thing is that pending_trace is cleared
        assert result is True

        # Verify pending_trace is cleared
        updated_state = self.state_manager.read(session_id)
        assert "pending_trace" not in updated_state

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    def test_flush_sanitizes_trace_before_push(self, mock_push):
        """Flush should sanitize the trace batch before pushing to Langfuse."""
        from pacemaker.langfuse.orchestrator import flush_pending_trace

        mock_push.return_value = (True, 1)
        session_id = "test-sanitize"
        pending_trace = [
            {
                "id": "evt-1",
                "type": "trace-create",
                "body": {"id": "trace-1", "input": "test data"},
                "timestamp": "2024-01-01T00:00:00Z",
            }
        ]
        existing_state = self._create_state_with_pending(session_id, pending_trace)

        with patch("pacemaker.langfuse.orchestrator.sanitize_trace") as mock_sanitize:
            mock_sanitize.return_value = pending_trace  # Pass through

            flush_pending_trace(
                config=self.config,
                session_id=session_id,
                state_manager=self.state_manager,
                existing_state=existing_state,
                caller="test",
            )

            # sanitize_trace should have been called with the pending trace
            mock_sanitize.assert_called_once()
            call_args = mock_sanitize.call_args[0]
            assert call_args[0] == pending_trace  # First arg is the trace batch

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    @patch("pacemaker.langfuse.orchestrator.increment_metric")
    def test_flush_increments_traces_metric_on_success(self, mock_metric, mock_push):
        """Flush should increment traces metric on successful push."""
        from pacemaker.langfuse.orchestrator import flush_pending_trace

        mock_push.return_value = (True, 1)
        session_id = "test-metrics"
        pending_trace = [
            {
                "id": "evt-1",
                "type": "trace-create",
                "body": {"id": "trace-1"},
                "timestamp": "2024-01-01T00:00:00Z",
            }
        ]
        existing_state = self._create_state_with_pending(session_id, pending_trace)

        flush_pending_trace(
            config=self.config,
            session_id=session_id,
            state_manager=self.state_manager,
            existing_state=existing_state,
            caller="test",
        )

        # Should have incremented traces metric
        trace_calls = [c for c in mock_metric.call_args_list if c[0][0] == "traces"]
        assert len(trace_calls) >= 1

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    @patch("pacemaker.langfuse.orchestrator.increment_metric")
    def test_flush_increments_sessions_metric_on_first_trace(
        self, mock_metric, mock_push
    ):
        """Flush should increment sessions metric when is_first_trace_in_session is True."""
        from pacemaker.langfuse.orchestrator import flush_pending_trace

        mock_push.return_value = (True, 1)
        session_id = "test-first-trace"
        pending_trace = [
            {
                "id": "evt-1",
                "type": "trace-create",
                "body": {"id": "trace-1"},
                "timestamp": "2024-01-01T00:00:00Z",
            }
        ]
        existing_state = self._create_state_with_pending(
            session_id, pending_trace, is_first_trace=True
        )

        flush_pending_trace(
            config=self.config,
            session_id=session_id,
            state_manager=self.state_manager,
            existing_state=existing_state,
            caller="test",
        )

        # Should have incremented sessions metric
        session_calls = [c for c in mock_metric.call_args_list if c[0][0] == "sessions"]
        assert len(session_calls) >= 1

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    @patch("pacemaker.langfuse.orchestrator.increment_metric")
    def test_flush_does_not_increment_sessions_metric_when_not_first(
        self, mock_metric, mock_push
    ):
        """Flush should NOT increment sessions metric when is_first_trace_in_session is False."""
        from pacemaker.langfuse.orchestrator import flush_pending_trace

        mock_push.return_value = (True, 1)
        session_id = "test-not-first"
        pending_trace = [
            {
                "id": "evt-1",
                "type": "trace-create",
                "body": {"id": "trace-1"},
                "timestamp": "2024-01-01T00:00:00Z",
            }
        ]
        existing_state = self._create_state_with_pending(
            session_id, pending_trace, is_first_trace=False
        )

        flush_pending_trace(
            config=self.config,
            session_id=session_id,
            state_manager=self.state_manager,
            existing_state=existing_state,
            caller="test",
        )

        # Should NOT have incremented sessions metric
        session_calls = [c for c in mock_metric.call_args_list if c[0][0] == "sessions"]
        assert len(session_calls) == 0

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    def test_flush_preserves_existing_state_fields(self, mock_push):
        """Flush should preserve trace_id and last_pushed_line when clearing pending_trace."""
        from pacemaker.langfuse.orchestrator import flush_pending_trace

        mock_push.return_value = (True, 1)
        session_id = "test-preserve-fields"
        trace_id = "my-trace-id-123"
        last_pushed_line = 42
        pending_trace = [
            {
                "id": "evt-1",
                "type": "trace-create",
                "body": {"id": "trace-1"},
                "timestamp": "2024-01-01T00:00:00Z",
            }
        ]
        existing_state = self._create_state_with_pending(
            session_id,
            pending_trace,
            trace_id=trace_id,
            last_pushed_line=last_pushed_line,
        )

        flush_pending_trace(
            config=self.config,
            session_id=session_id,
            state_manager=self.state_manager,
            existing_state=existing_state,
            caller="test",
        )

        # Verify that trace_id and last_pushed_line are preserved
        updated_state = self.state_manager.read(session_id)
        assert updated_state["trace_id"] == trace_id
        assert updated_state["last_pushed_line"] == last_pushed_line

    def test_flush_returns_false_when_langfuse_disabled(self):
        """Flush should return False when Langfuse is not enabled."""
        from pacemaker.langfuse.orchestrator import flush_pending_trace

        disabled_config = {"langfuse_enabled": False}
        session_id = "test-disabled"
        self.state_manager.create_or_update(
            session_id=session_id,
            trace_id="trace-1",
            last_pushed_line=0,
            metadata={"current_trace_id": "trace-1"},
            pending_trace=[
                {
                    "id": "evt-1",
                    "type": "trace-create",
                    "body": {"id": "trace-1"},
                    "timestamp": "2024-01-01T00:00:00Z",
                }
            ],
        )
        existing_state = self.state_manager.read(session_id)

        result = flush_pending_trace(
            config=disabled_config,
            session_id=session_id,
            state_manager=self.state_manager,
            existing_state=existing_state,
            caller="test",
        )

        assert result is False

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    def test_flush_handles_none_existing_state(self, mock_push):
        """Flush should return False gracefully when existing_state is None."""
        from pacemaker.langfuse.orchestrator import flush_pending_trace

        result = flush_pending_trace(
            config=self.config,
            session_id="test-none",
            state_manager=self.state_manager,
            existing_state=None,
            caller="test",
        )

        assert result is False
        mock_push.assert_not_called()
