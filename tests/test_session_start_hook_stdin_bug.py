#!/usr/bin/env python3
"""
TDD tests for SessionStart hook stdin reading and state reset bug.

Bug Description:
- run_session_start_hook() IGNORES stdin data from Claude Code
- Loads OLD state from previous session
- Only resets subagent_counter and in_subagent
- Keeps stale: session_id, last_user_interaction_time, tool_execution_count, last_poll_time

Expected Behavior:
- Read stdin to get new session data (session_id, source, transcript_path)
- Reset session state based on source:
  - source='startup': Full reset (new session)
  - source='resume': Update session_id but preserve counters
  - source='clear'/'compact': Reset counters but keep session_id
"""

import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
import io

import pytest

# Add src to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pacemaker import hook


class TestSessionStartHookStdinBug:
    """Test suite for SessionStart hook stdin reading and state reset."""

    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories for config and state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            yield {
                "tmpdir": tmpdir,
                "config_path": str(config_dir / "config.json"),
                "state_path": str(config_dir / "state.json"),
            }

    @pytest.fixture
    def mock_config(self, temp_dirs):
        """Create mock config with pace-maker enabled."""
        config = {
            "enabled": True,
            "intent_validation_enabled": False,  # Disable to simplify tests
        }
        with open(temp_dirs["config_path"], "w") as f:
            json.dump(config, f)
        return temp_dirs["config_path"]

    @pytest.fixture
    def stale_state(self, temp_dirs):
        """Create state file with STALE data from previous session."""
        # Simulate old session from 6 hours ago
        old_time = datetime.now() - timedelta(hours=6)

        state = {
            "session_id": "old-session-12345",
            "last_user_interaction_time": old_time.isoformat(),
            "tool_execution_count": 3669,  # Real accumulated value from bug report
            "last_poll_time": old_time.isoformat(),
            "subagent_counter": 2,
            "in_subagent": True,
        }
        with open(temp_dirs["state_path"], "w") as f:
            json.dump(state, f)
        return temp_dirs["state_path"]

    # ========================================================================
    # FAILING TESTS - Expose the bug
    # ========================================================================

    def test_session_start_reads_stdin(self, temp_dirs, mock_config, stale_state):
        """
        FAILING TEST: SessionStart hook should read stdin to get new session data.

        Current bug: Doesn't read stdin at all
        Expected: Parse JSON from stdin to get session_id, source, transcript_path
        """
        # SessionStart hook input schema
        hook_data = {
            "session_id": "new-session-abc123",
            "transcript_path": "/tmp/transcript.jsonl",
            "cwd": "/home/user/project",
            "permission_mode": "default",
            "hook_event_name": "SessionStart",
            "source": "startup",
            "model": "claude-sonnet-4-5-20250929",
        }

        mock_stdin = io.StringIO(json.dumps(hook_data))

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", stale_state):
                with patch("sys.stdin", mock_stdin):
                    hook.run_session_start_hook()

        # Read saved state
        with open(stale_state) as f:
            saved_state = json.load(f)

        # BUG: session_id should be NEW value from stdin, not old value
        assert (
            saved_state["session_id"] == "new-session-abc123"
        ), "Should update session_id from stdin"

    def test_session_start_resets_user_interaction_time_on_startup(
        self, temp_dirs, mock_config, stale_state
    ):
        """
        FAILING TEST: SessionStart with source='startup' should reset last_user_interaction_time.

        Current bug: Keeps old timestamp from 6 hours ago
        Expected: Reset to None for new session
        Impact: Stop hook makes wrong auto-tempo decisions with stale data
        """
        hook_data = {
            "session_id": "new-session-abc123",
            "source": "startup",  # NEW SESSION
            "transcript_path": "/tmp/transcript.jsonl",
            "hook_event_name": "SessionStart",
        }

        mock_stdin = io.StringIO(json.dumps(hook_data))

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", stale_state):
                with patch("sys.stdin", mock_stdin):
                    hook.run_session_start_hook()

        # Read saved state
        with open(stale_state) as f:
            saved_state = json.load(f)

        # BUG: last_user_interaction_time should be None for new session
        assert (
            saved_state.get("last_user_interaction_time") is None
        ), "Should reset last_user_interaction_time for new session"

    def test_session_start_resets_tool_count_on_startup(
        self, temp_dirs, mock_config, stale_state
    ):
        """
        FAILING TEST: SessionStart with source='startup' should reset tool_execution_count.

        Current bug: Keeps old count (3669 in real case!)
        Expected: Reset to 0 for new session
        Impact: Counter accumulates forever across sessions
        """
        hook_data = {
            "session_id": "new-session-abc123",
            "source": "startup",  # NEW SESSION
            "transcript_path": "/tmp/transcript.jsonl",
            "hook_event_name": "SessionStart",
        }

        mock_stdin = io.StringIO(json.dumps(hook_data))

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", stale_state):
                with patch("sys.stdin", mock_stdin):
                    hook.run_session_start_hook()

        # Read saved state
        with open(stale_state) as f:
            saved_state = json.load(f)

        # BUG: tool_execution_count should be 0 for new session
        assert (
            saved_state["tool_execution_count"] == 0
        ), "Should reset tool_execution_count for new session"

    def test_session_start_resets_poll_time_on_startup(
        self, temp_dirs, mock_config, stale_state
    ):
        """
        FAILING TEST: SessionStart with source='startup' should reset last_poll_time.

        Current bug: Keeps old timestamp
        Expected: Reset to None for new session
        """
        hook_data = {
            "session_id": "new-session-abc123",
            "source": "startup",  # NEW SESSION
            "transcript_path": "/tmp/transcript.jsonl",
            "hook_event_name": "SessionStart",
        }

        mock_stdin = io.StringIO(json.dumps(hook_data))

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", stale_state):
                with patch("sys.stdin", mock_stdin):
                    hook.run_session_start_hook()

        # Read saved state
        with open(stale_state) as f:
            saved_state = json.load(f)

        # BUG: last_poll_time should be None for new session
        assert (
            saved_state.get("last_poll_time") is None
        ), "Should reset last_poll_time for new session"

    # ========================================================================
    # PASSING TESTS - Current behavior that should be preserved
    # ========================================================================

    def test_session_start_still_resets_subagent_counter(
        self, temp_dirs, mock_config, stale_state
    ):
        """
        PASSING TEST: SessionStart already resets subagent_counter.

        This is existing behavior - must not break it during fix.
        """
        hook_data = {
            "session_id": "new-session-abc123",
            "source": "startup",
            "transcript_path": "/tmp/transcript.jsonl",
            "hook_event_name": "SessionStart",
        }

        mock_stdin = io.StringIO(json.dumps(hook_data))

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", stale_state):
                with patch("sys.stdin", mock_stdin):
                    hook.run_session_start_hook()

        # Read saved state
        with open(stale_state) as f:
            saved_state = json.load(f)

        # This already works
        assert saved_state["subagent_counter"] == 0

    def test_session_start_still_resets_in_subagent_flag(
        self, temp_dirs, mock_config, stale_state
    ):
        """
        PASSING TEST: SessionStart already resets in_subagent flag.

        This is existing behavior - must not break it during fix.
        """
        hook_data = {
            "session_id": "new-session-abc123",
            "source": "startup",
            "transcript_path": "/tmp/transcript.jsonl",
            "hook_event_name": "SessionStart",
        }

        mock_stdin = io.StringIO(json.dumps(hook_data))

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", stale_state):
                with patch("sys.stdin", mock_stdin):
                    hook.run_session_start_hook()

        # Read saved state
        with open(stale_state) as f:
            saved_state = json.load(f)

        # This already works
        assert saved_state["in_subagent"] is False

    # ========================================================================
    # TESTS for different 'source' values
    # ========================================================================

    def test_session_start_resume_preserves_counters(
        self, temp_dirs, mock_config, stale_state
    ):
        """
        TEST: source='resume' should update session_id but PRESERVE counters.

        Resume means continuing an existing session (e.g., after reconnect).
        Should NOT reset tool_execution_count or last_user_interaction_time.
        """
        hook_data = {
            "session_id": "resumed-session-xyz",
            "source": "resume",  # RESUME existing session
            "transcript_path": "/tmp/transcript.jsonl",
            "hook_event_name": "SessionStart",
        }

        # Read original state to compare
        with open(stale_state) as f:
            original_state = json.load(f)

        mock_stdin = io.StringIO(json.dumps(hook_data))

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", stale_state):
                with patch("sys.stdin", mock_stdin):
                    hook.run_session_start_hook()

        # Read saved state
        with open(stale_state) as f:
            saved_state = json.load(f)

        # Should update session_id
        assert saved_state["session_id"] == "resumed-session-xyz"

        # Should PRESERVE counters (resume = continue existing session)
        assert (
            saved_state["tool_execution_count"]
            == original_state["tool_execution_count"]
        )
        assert saved_state.get("last_user_interaction_time") == original_state.get(
            "last_user_interaction_time"
        )

    def test_session_start_clear_resets_counters_but_keeps_session_id(
        self, temp_dirs, mock_config, stale_state
    ):
        """
        TEST: source='clear' should reset counters but keep session_id.

        Clear means user ran /clear command - same session continues.
        """
        hook_data = {
            "session_id": "old-session-12345",  # SAME session_id
            "source": "clear",  # CLEAR command
            "transcript_path": "/tmp/transcript.jsonl",
            "hook_event_name": "SessionStart",
        }

        mock_stdin = io.StringIO(json.dumps(hook_data))

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", stale_state):
                with patch("sys.stdin", mock_stdin):
                    hook.run_session_start_hook()

        # Read saved state
        with open(stale_state) as f:
            saved_state = json.load(f)

        # Should keep SAME session_id
        assert saved_state["session_id"] == "old-session-12345"

        # Should reset counters (clear = fresh start for this session)
        assert saved_state["tool_execution_count"] == 0
        assert saved_state.get("last_user_interaction_time") is None

    def test_session_start_compact_resets_counters_but_keeps_session_id(
        self, temp_dirs, mock_config, stale_state
    ):
        """
        TEST: source='compact' should reset counters but keep session_id.

        Compact means user ran /compact command - same session continues.
        """
        hook_data = {
            "session_id": "old-session-12345",  # SAME session_id
            "source": "compact",  # COMPACT command
            "transcript_path": "/tmp/transcript.jsonl",
            "hook_event_name": "SessionStart",
        }

        mock_stdin = io.StringIO(json.dumps(hook_data))

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", stale_state):
                with patch("sys.stdin", mock_stdin):
                    hook.run_session_start_hook()

        # Read saved state
        with open(stale_state) as f:
            saved_state = json.load(f)

        # Should keep SAME session_id
        assert saved_state["session_id"] == "old-session-12345"

        # Should reset counters (compact = fresh context for this session)
        assert saved_state["tool_execution_count"] == 0
        assert saved_state.get("last_user_interaction_time") is None

    # ========================================================================
    # EDGE CASES
    # ========================================================================

    def test_session_start_handles_missing_stdin_gracefully(
        self, temp_dirs, mock_config, stale_state
    ):
        """
        TEST: SessionStart should handle missing stdin gracefully.

        If stdin is empty (shouldn't happen but defensive coding), should:
        - Reset subagent fields (existing behavior)
        - Not crash
        """
        mock_stdin = io.StringIO("")  # Empty stdin

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", stale_state):
                with patch("sys.stdin", mock_stdin):
                    # Should not raise exception
                    hook.run_session_start_hook()

        # Should still reset subagent fields
        with open(stale_state) as f:
            saved_state = json.load(f)

        assert saved_state["subagent_counter"] == 0
        assert saved_state["in_subagent"] is False

    def test_session_start_handles_malformed_json_gracefully(
        self, temp_dirs, mock_config, stale_state
    ):
        """
        TEST: SessionStart should handle malformed JSON gracefully.

        If stdin has invalid JSON, should:
        - Log warning
        - Reset subagent fields (existing behavior)
        - Not crash
        """
        mock_stdin = io.StringIO("{ invalid json }")

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", stale_state):
                with patch("sys.stdin", mock_stdin):
                    # Should not raise exception
                    hook.run_session_start_hook()

        # Should still reset subagent fields
        with open(stale_state) as f:
            saved_state = json.load(f)

        assert saved_state["subagent_counter"] == 0
        assert saved_state["in_subagent"] is False

    def test_session_start_handles_missing_source_field(
        self, temp_dirs, mock_config, stale_state
    ):
        """
        TEST: SessionStart should handle missing 'source' field gracefully.

        If stdin JSON doesn't have 'source', should default to 'startup' behavior.
        """
        hook_data = {
            "session_id": "new-session-abc123",
            # Missing 'source' field
            "transcript_path": "/tmp/transcript.jsonl",
            "hook_event_name": "SessionStart",
        }

        mock_stdin = io.StringIO(json.dumps(hook_data))

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", stale_state):
                with patch("sys.stdin", mock_stdin):
                    hook.run_session_start_hook()

        # Read saved state
        with open(stale_state) as f:
            saved_state = json.load(f)

        # Should default to startup behavior: reset everything
        assert saved_state["session_id"] == "new-session-abc123"
        assert saved_state["tool_execution_count"] == 0
        assert saved_state.get("last_user_interaction_time") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
