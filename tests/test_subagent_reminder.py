#!/usr/bin/env python3
"""
Tests for subagent reminder injection feature.

Tests the following behaviors:
1. Global tool execution counter increments on every PostToolUse
2. Reminder injected every 5 executions when in main context
3. Reminder NOT injected when in subagent context
4. SubagentStart sets in_subagent flag
5. SubagentStop clears in_subagent flag
6. State persists across hook invocations
"""

import json
import pytest
from unittest.mock import patch

# Import functions we'll be testing (these don't exist yet - TDD!)
from pacemaker import hook


class TestSubagentReminderState:
    """Test state management for subagent reminder feature."""

    def test_subagent_start_sets_flag(self, tmp_path):
        """SubagentStart hook should set in_subagent to True and increment counter."""
        state_path = tmp_path / "state.json"

        # Initial state - not in subagent
        initial_state = {
            "session_id": "test-session",
            "in_subagent": False,
            "subagent_counter": 0,
            "tool_execution_count": 0,
        }
        state_path.write_text(json.dumps(initial_state))

        # Run SubagentStart hook
        with patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path)):
            hook.run_subagent_start_hook()

        # Verify state updated
        state = json.loads(state_path.read_text())
        assert state["in_subagent"] is True
        assert state["subagent_counter"] == 1
        assert state["tool_execution_count"] == 0  # Counter unchanged

    def test_subagent_stop_clears_flag(self, tmp_path):
        """SubagentStop hook should decrement counter and update flag."""
        state_path = tmp_path / "state.json"

        # Initial state - in subagent
        initial_state = {
            "session_id": "test-session",
            "in_subagent": True,
            "subagent_counter": 1,
            "tool_execution_count": 10,
        }
        state_path.write_text(json.dumps(initial_state))

        # Run SubagentStop hook
        with patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path)):
            hook.run_subagent_stop_hook()

        # Verify state updated
        state = json.loads(state_path.read_text())
        assert state["in_subagent"] is False
        assert state["subagent_counter"] == 0
        assert state["tool_execution_count"] == 10  # Counter unchanged

    def test_session_start_resets_flag(self, tmp_path):
        """SessionStart hook should reset counter and flag, preventing state corruption."""
        state_path = tmp_path / "state.json"

        # Initial state - corrupted (in_subagent=True from cancelled subagent)
        initial_state = {
            "session_id": "test-session",
            "in_subagent": True,  # Corrupted state
            "subagent_counter": 2,  # Corrupted counter
            "tool_execution_count": 15,
        }
        state_path.write_text(json.dumps(initial_state))

        # Run SessionStart hook
        with patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path)):
            hook.run_session_start_hook()

        # Verify state reset - counter and flag should be reset
        state = json.loads(state_path.read_text())
        assert state["in_subagent"] is False
        assert state["subagent_counter"] == 0
        assert state["tool_execution_count"] == 15  # Counter unchanged

    def test_parallel_subagents_tracking(self, tmp_path):
        """Counter-based approach correctly handles parallel subagents."""
        state_path = tmp_path / "state.json"

        # Initial state - not in subagent
        initial_state = {
            "session_id": "test-session",
            "in_subagent": False,
            "subagent_counter": 0,
            "tool_execution_count": 0,
        }
        state_path.write_text(json.dumps(initial_state))

        with patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path)):
            # Start first subagent
            hook.run_subagent_start_hook()
            state = json.loads(state_path.read_text())
            assert state["subagent_counter"] == 1
            assert state["in_subagent"] is True

            # Start second subagent (parallel)
            hook.run_subagent_start_hook()
            state = json.loads(state_path.read_text())
            assert state["subagent_counter"] == 2
            assert state["in_subagent"] is True

            # Stop first subagent (second still running)
            hook.run_subagent_stop_hook()
            state = json.loads(state_path.read_text())
            assert state["subagent_counter"] == 1
            assert state["in_subagent"] is True  # Still in subagent!

            # Stop second subagent (back to main context)
            hook.run_subagent_stop_hook()
            state = json.loads(state_path.read_text())
            assert state["subagent_counter"] == 0
            assert state["in_subagent"] is False

    def test_counter_never_goes_negative(self, tmp_path):
        """Counter should never go below 0 even with extra stops."""
        state_path = tmp_path / "state.json"

        # Initial state - already at 0
        initial_state = {
            "session_id": "test-session",
            "in_subagent": False,
            "subagent_counter": 0,
            "tool_execution_count": 0,
        }
        state_path.write_text(json.dumps(initial_state))

        with patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path)):
            # Try to stop when counter is 0 (should stay at 0)
            hook.run_subagent_stop_hook()
            state = json.loads(state_path.read_text())
            assert state["subagent_counter"] == 0
            assert state["in_subagent"] is False

    def test_user_prompt_submit_resets_counter(self, tmp_path):
        """UserPromptSubmit hook should reset subagent counter to fix orphaned state from ESC cancellations."""
        state_path = tmp_path / "state.json"

        # Initial state - orphaned from cancelled subagent (ESC pressed during Task tool)
        initial_state = {
            "session_id": "test-session",
            "in_subagent": True,  # Stuck flag from cancelled subagent
            "subagent_counter": 2,  # Stuck counter from cancelled parallel subagents
            "tool_execution_count": 10,
        }
        state_path.write_text(json.dumps(initial_state))

        # Mock stdin with a simple user prompt
        mock_input = "Hello Claude"

        with (
            patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path)),
            patch("sys.stdin.read", return_value=mock_input),
            patch("pacemaker.hook.user_commands.handle_user_prompt") as mock_handler,
        ):
            # Mock user_commands to NOT intercept (normal prompt passthrough)
            mock_handler.return_value = {"intercepted": False, "output": ""}

            # Run UserPromptSubmit hook - expect sys.exit(0)
            with pytest.raises(SystemExit) as exc_info:
                hook.run_user_prompt_submit()

            assert exc_info.value.code == 0

        # Verify state reset - counter and flag should be 0/False
        state = json.loads(state_path.read_text())
        assert (
            state["subagent_counter"] == 0
        ), "Counter should reset to 0 on new user prompt"
        assert (
            state["in_subagent"] is False
        ), "in_subagent flag should reset to False on new user prompt"
        assert (
            state["tool_execution_count"] == 10
        ), "Tool execution count should remain unchanged"


class TestToolExecutionCounter:
    """Test global tool execution counter."""

    @patch("pacemaker.hook.database")
    @patch("pacemaker.hook.pacing_engine")
    def test_counter_increments_every_tool_call(self, mock_pacing, mock_db, tmp_path):
        """Tool execution counter should increment on every PostToolUse."""
        state_path = tmp_path / "state.json"
        config_path = tmp_path / "config.json"

        initial_state = {
            "session_id": "test-session",
            "in_subagent": False,
            "subagent_counter": 0,
            "tool_execution_count": 0,
        }
        state_path.write_text(json.dumps(initial_state))

        config = {"enabled": True, "subagent_reminder_enabled": True}
        config_path.write_text(json.dumps(config))

        # Mock pacing engine to return no throttle
        mock_pacing.run_pacing_check.return_value = {
            "polled": False,
            "decision": {"should_throttle": False, "delay_seconds": 0},
        }

        with (
            patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path)),
            patch("pacemaker.hook.DEFAULT_CONFIG_PATH", str(config_path)),
            patch("pacemaker.hook.DEFAULT_DB_PATH", str(tmp_path / "db.sqlite")),
        ):

            # Call hook 3 times
            for i in range(3):
                hook.run_hook()

            # Verify counter incremented 3 times
            state = json.loads(state_path.read_text())
            assert state["tool_execution_count"] == 3

    @patch("pacemaker.hook.database")
    @patch("pacemaker.hook.pacing_engine")
    def test_counter_persists_in_subagent(self, mock_pacing, mock_db, tmp_path):
        """Tool execution counter should continue incrementing in subagent context."""
        state_path = tmp_path / "state.json"
        config_path = tmp_path / "config.json"

        initial_state = {
            "session_id": "test-session",
            "in_subagent": False,
            "subagent_counter": 0,
            "tool_execution_count": 3,
        }
        state_path.write_text(json.dumps(initial_state))

        config = {"enabled": True, "subagent_reminder_enabled": True}
        config_path.write_text(json.dumps(config))

        mock_pacing.run_pacing_check.return_value = {
            "polled": False,
            "decision": {"should_throttle": False, "delay_seconds": 0},
        }

        with (
            patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path)),
            patch("pacemaker.hook.DEFAULT_CONFIG_PATH", str(config_path)),
            patch("pacemaker.hook.DEFAULT_DB_PATH", str(tmp_path / "db.sqlite")),
        ):

            # Enter subagent
            hook.run_subagent_start_hook()

            # Execute 2 tools in subagent
            hook.run_hook()
            hook.run_hook()

            # Verify counter continued incrementing
            state = json.loads(state_path.read_text())
            assert state["tool_execution_count"] == 5
            assert state["in_subagent"] is True


class TestReminderInjection:
    """Test reminder injection logic."""

    def test_should_inject_reminder_main_context_every_5(self, tmp_path):
        """should_inject_reminder returns True every 5 executions in main context."""
        config = {"subagent_reminder_enabled": True, "subagent_reminder_frequency": 5}

        # Test counts 0-10
        test_cases = [
            (0, False),  # Count 0 - no reminder
            (1, False),  # Count 1 - no reminder
            (4, False),  # Count 4 - no reminder
            (5, True),  # Count 5 - REMINDER
            (6, False),  # Count 6 - no reminder
            (9, False),  # Count 9 - no reminder
            (10, True),  # Count 10 - REMINDER
            (15, True),  # Count 15 - REMINDER
            (20, True),  # Count 20 - REMINDER
        ]

        for count, expected in test_cases:
            state = {"in_subagent": False, "tool_execution_count": count}
            result = hook.should_inject_reminder(state, config, tool_name=None)
            assert result == expected, f"Count {count} should return {expected}"

    def test_should_inject_reminder_write_tool_immediate(self, tmp_path):
        """should_inject_reminder returns True immediately when Write tool is used."""
        config = {"subagent_reminder_enabled": True, "subagent_reminder_frequency": 5}

        # Test various counts - all should trigger with Write tool
        test_cases = [1, 2, 3, 4, 6, 7, 8, 9]  # Non-multiples of 5

        for count in test_cases:
            state = {"in_subagent": False, "tool_execution_count": count}
            result = hook.should_inject_reminder(state, config, tool_name="Write")
            assert (
                result is True
            ), f"Count {count} with Write tool should trigger immediate reminder"

    def test_should_inject_reminder_write_tool_bypasses_counter(self, tmp_path):
        """Write tool triggers reminder even at count 0 or count 1."""
        config = {"subagent_reminder_enabled": True, "subagent_reminder_frequency": 5}

        # Even at count 0, Write tool should trigger
        state = {"in_subagent": False, "tool_execution_count": 0}
        result = hook.should_inject_reminder(state, config, tool_name="Write")
        assert result is True, "Write tool should trigger at count 0"

        # Even at count 1, Write tool should trigger
        state = {"in_subagent": False, "tool_execution_count": 1}
        result = hook.should_inject_reminder(state, config, tool_name="Write")
        assert result is True, "Write tool should trigger at count 1"

    def test_should_inject_reminder_other_tools_no_immediate(self, tmp_path):
        """Other tools (not Write/Edit) don't trigger immediate reminder."""
        config = {"subagent_reminder_enabled": True, "subagent_reminder_frequency": 5}

        # Edit now triggers immediate (same as Write), so exclude from this test
        other_tools = ["Read", "Bash", "Glob", "Grep"]

        for tool_name in other_tools:
            # At count 3 (not a multiple of 5)
            state = {"in_subagent": False, "tool_execution_count": 3}
            result = hook.should_inject_reminder(state, config, tool_name=tool_name)
            assert (
                result is False
            ), f"{tool_name} at count 3 should not trigger reminder"

            # At count 5 (multiple of 5) - should still trigger due to counter
            state = {"in_subagent": False, "tool_execution_count": 5}
            result = hook.should_inject_reminder(state, config, tool_name=tool_name)
            assert (
                result is True
            ), f"{tool_name} at count 5 should trigger due to counter"

    def test_should_inject_reminder_edit_triggers_immediate(self, tmp_path):
        """Edit tool triggers immediate reminder (same as Write)."""
        config = {"subagent_reminder_enabled": True, "subagent_reminder_frequency": 5}

        # Edit at count 3 (not a multiple of 5) should still trigger
        state = {"in_subagent": False, "tool_execution_count": 3}
        result = hook.should_inject_reminder(state, config, tool_name="Edit")
        assert result is True, "Edit tool should trigger immediate reminder"

        # Even at count 1, Edit tool should trigger
        state = {"in_subagent": False, "tool_execution_count": 1}
        result = hook.should_inject_reminder(state, config, tool_name="Edit")
        assert result is True, "Edit tool should trigger at count 1"

    def test_should_inject_reminder_disabled_in_subagent(self, tmp_path):
        """should_inject_reminder returns False when in subagent context."""
        config = {"subagent_reminder_enabled": True, "subagent_reminder_frequency": 5}

        # Even at count 5, 10, 15 - no reminder in subagent
        for count in [5, 10, 15, 20]:
            state = {"in_subagent": True, "tool_execution_count": count}
            result = hook.should_inject_reminder(state, config, tool_name=None)
            assert (
                result is False
            ), f"Count {count} in subagent should not inject reminder"

        # Even Write tool in subagent should not trigger
        state = {"in_subagent": True, "tool_execution_count": 3}
        result = hook.should_inject_reminder(state, config, tool_name="Write")
        assert result is False, "Write tool in subagent should not inject reminder"

    def test_should_inject_reminder_disabled_via_config(self, tmp_path):
        """should_inject_reminder returns False when feature disabled."""
        config = {"subagent_reminder_enabled": False, "subagent_reminder_frequency": 5}

        state = {"in_subagent": False, "tool_execution_count": 5}

        result = hook.should_inject_reminder(state, config, tool_name=None)
        assert result is False

        # Even Write tool should not trigger when disabled
        result = hook.should_inject_reminder(state, config, tool_name="Write")
        assert result is False

    def test_inject_subagent_reminder_returns_message(self):
        """inject_subagent_reminder returns reminder message (no longer prints)."""
        config = {"subagent_reminder_message": "TEST REMINDER MESSAGE"}

        result = hook.inject_subagent_reminder(config)

        # Verify message returned
        assert result == "TEST REMINDER MESSAGE"

    def test_inject_subagent_reminder_default_message(self):
        """inject_subagent_reminder uses default message if not configured."""
        config = {}

        result = hook.inject_subagent_reminder(config)

        # Verify default message used
        assert "Task tool" in result


class TestIntegration:
    """Integration tests for complete workflow."""

    @patch("pacemaker.hook.database")
    @patch("pacemaker.hook.pacing_engine")
    @patch("builtins.print")
    def test_reminder_injected_at_count_5_in_main_context(
        self, mock_print, mock_pacing, mock_db, tmp_path
    ):
        """Integration: Reminder injected on 5th tool execution in main context."""
        state_path = tmp_path / "state.json"
        config_path = tmp_path / "config.json"

        initial_state = {
            "session_id": "test-session",
            "in_subagent": False,
            "subagent_counter": 0,
            "tool_execution_count": 0,
        }
        state_path.write_text(json.dumps(initial_state))

        config = {
            "enabled": True,
            "subagent_reminder_enabled": True,
            "subagent_reminder_frequency": 5,
            "subagent_reminder_message": "[TEST REMINDER]",
        }
        config_path.write_text(json.dumps(config))

        mock_pacing.run_pacing_check.return_value = {
            "polled": False,
            "decision": {"should_throttle": False, "delay_seconds": 0},
        }

        with (
            patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path)),
            patch("pacemaker.hook.DEFAULT_CONFIG_PATH", str(config_path)),
            patch("pacemaker.hook.DEFAULT_DB_PATH", str(tmp_path / "db.sqlite")),
        ):

            # Execute 4 tools - no reminder
            for i in range(4):
                mock_print.reset_mock()
                hook.run_hook()
                # Check that reminder was NOT printed
                printed_messages = [
                    call[0][0]
                    for call in mock_print.call_args_list
                    if call[0] and isinstance(call[0][0], str)
                ]
                assert not any("[TEST REMINDER]" in msg for msg in printed_messages)

            # Execute 5th tool - should see reminder
            mock_print.reset_mock()
            hook.run_hook()

            # Verify reminder was printed as JSON with additionalContext
            import sys

            reminder_printed = False
            for call in mock_print.call_args_list:
                if call[0] and call[1].get("file") == sys.stdout:
                    try:
                        json_output = json.loads(call[0][0])
                        hook_output = json_output.get("hookSpecificOutput", {})
                        if "TEST REMINDER" in hook_output.get("additionalContext", ""):
                            reminder_printed = True
                            break
                    except (json.JSONDecodeError, AttributeError):
                        continue

            assert reminder_printed, "Reminder should be printed on 5th execution"

    @patch("pacemaker.hook.database")
    @patch("pacemaker.hook.pacing_engine")
    @patch("builtins.print")
    def test_no_reminder_in_subagent_context(
        self, mock_print, mock_pacing, mock_db, tmp_path
    ):
        """Integration: No reminder injected when in subagent context."""
        state_path = tmp_path / "state.json"
        config_path = tmp_path / "config.json"

        initial_state = {
            "session_id": "test-session",
            "in_subagent": False,
            "subagent_counter": 0,
            "tool_execution_count": 3,
        }
        state_path.write_text(json.dumps(initial_state))

        config = {
            "enabled": True,
            "subagent_reminder_enabled": True,
            "subagent_reminder_frequency": 5,
            "subagent_reminder_message": "[TEST REMINDER]",
        }
        config_path.write_text(json.dumps(config))

        mock_pacing.run_pacing_check.return_value = {
            "polled": False,
            "decision": {"should_throttle": False, "delay_seconds": 0},
        }

        with (
            patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path)),
            patch("pacemaker.hook.DEFAULT_CONFIG_PATH", str(config_path)),
            patch("pacemaker.hook.DEFAULT_DB_PATH", str(tmp_path / "db.sqlite")),
        ):

            # Enter subagent
            hook.run_subagent_start_hook()

            # Execute 2 tools in subagent (total count now 5)
            mock_print.reset_mock()
            hook.run_hook()
            hook.run_hook()

            # Verify NO reminder printed (even though count=5)
            printed_messages = [
                call[0][0]
                for call in mock_print.call_args_list
                if call[0] and isinstance(call[0][0], str)
            ]
            assert not any("[TEST REMINDER]" in msg for msg in printed_messages)

            # Verify we're at count 5
            state = json.loads(state_path.read_text())
            assert state["tool_execution_count"] == 5
            assert state["in_subagent"] is True

    @patch("pacemaker.hook.database")
    @patch("pacemaker.hook.pacing_engine")
    @patch("builtins.print")
    def test_reminder_resumes_after_exiting_subagent(
        self, mock_print, mock_pacing, mock_db, tmp_path
    ):
        """Integration: Reminder resumes after exiting subagent at next multiple of 5."""
        state_path = tmp_path / "state.json"
        config_path = tmp_path / "config.json"

        initial_state = {
            "session_id": "test-session",
            "in_subagent": True,  # Start in subagent
            "subagent_counter": 1,
            "tool_execution_count": 9,  # One away from 10
        }
        state_path.write_text(json.dumps(initial_state))

        config = {
            "enabled": True,
            "subagent_reminder_enabled": True,
            "subagent_reminder_frequency": 5,
            "subagent_reminder_message": "[TEST REMINDER]",
        }
        config_path.write_text(json.dumps(config))

        mock_pacing.run_pacing_check.return_value = {
            "polled": False,
            "decision": {"should_throttle": False, "delay_seconds": 0},
        }

        with (
            patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path)),
            patch("pacemaker.hook.DEFAULT_CONFIG_PATH", str(config_path)),
            patch("pacemaker.hook.DEFAULT_DB_PATH", str(tmp_path / "db.sqlite")),
        ):

            # Exit subagent
            hook.run_subagent_stop_hook()

            # Execute 1 tool in main context (count becomes 10)
            mock_print.reset_mock()
            hook.run_hook()

            # Verify reminder WAS printed (count=10, in_subagent=false)
            import sys

            reminder_printed = False
            for call in mock_print.call_args_list:
                if call[0] and call[1].get("file") == sys.stdout:
                    try:
                        json_output = json.loads(call[0][0])
                        hook_output = json_output.get("hookSpecificOutput", {})
                        if "TEST REMINDER" in hook_output.get("additionalContext", ""):
                            reminder_printed = True
                            break
                    except (json.JSONDecodeError, AttributeError):
                        continue

            assert (
                reminder_printed
            ), "Reminder should print at count 10 after exiting subagent"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
