#!/usr/bin/env python3
"""
Tests for Langfuse state management in trace-per-turn architecture.

Tests state tracking for:
- current_trace_id: Which trace is currently active (changes per user turn)
- trace_start_line: Where token accumulation starts for current trace
- session_id: Constant across all traces in a session (Langfuse sessionId)
"""

import tempfile

from pacemaker.langfuse.state import StateManager


class TestTracePerTurnState:
    """Test state management for trace-per-turn architecture."""

    def test_state_tracks_current_trace_id(self):
        """
        Test that state stores and retrieves current_trace_id.

        State must track which trace is active for span/generation linking.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            state_mgr = StateManager(tmpdir)
            session_id = "test-session-123"
            trace_id = f"{session_id}-turn-1"

            # Create state with current_trace_id
            success = state_mgr.create_or_update(
                session_id=session_id,
                trace_id=trace_id,
                last_pushed_line=10,
                metadata={
                    "current_trace_id": trace_id,
                    "trace_start_line": 5,
                },
            )

            assert success

            # Read state back
            state = state_mgr.read(session_id)
            assert state is not None
            assert state["metadata"]["current_trace_id"] == trace_id
            assert state["metadata"]["trace_start_line"] == 5

    def test_state_updates_current_trace_on_new_turn(self):
        """
        Test that state updates current_trace_id when new user turn starts.

        Each UserPromptSubmit creates new trace, state must update current_trace_id.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            state_mgr = StateManager(tmpdir)
            session_id = "test-session-456"

            # First turn - trace 1
            trace_id_1 = f"{session_id}-turn-1"
            state_mgr.create_or_update(
                session_id=session_id,
                trace_id=trace_id_1,
                last_pushed_line=10,
                metadata={
                    "current_trace_id": trace_id_1,
                    "trace_start_line": 0,
                },
            )

            state = state_mgr.read(session_id)
            assert state["metadata"]["current_trace_id"] == trace_id_1
            assert state["metadata"]["trace_start_line"] == 0

            # Second turn - trace 2 (new user prompt)
            trace_id_2 = f"{session_id}-turn-2"
            state_mgr.create_or_update(
                session_id=session_id,
                trace_id=trace_id_2,
                last_pushed_line=25,
                metadata={
                    "current_trace_id": trace_id_2,
                    "trace_start_line": 10,  # Tokens since line 10
                },
            )

            state = state_mgr.read(session_id)
            assert state["metadata"]["current_trace_id"] == trace_id_2
            assert state["metadata"]["trace_start_line"] == 10
            assert state["last_pushed_line"] == 25

    def test_state_preserves_session_id_across_traces(self):
        """
        Test that session_id (Langfuse sessionId) remains constant across traces.

        All traces in a session must link to same sessionId for grouping.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            state_mgr = StateManager(tmpdir)
            session_id = "test-session-789"

            # Create 3 traces in same session
            for turn in range(1, 4):
                trace_id = f"{session_id}-turn-{turn}"
                state_mgr.create_or_update(
                    session_id=session_id,
                    trace_id=trace_id,
                    last_pushed_line=turn * 10,
                    metadata={
                        "current_trace_id": trace_id,
                        "trace_start_line": (turn - 1) * 10,
                    },
                )

            # Session ID should be same for all reads
            state = state_mgr.read(session_id)
            assert state["session_id"] == session_id
