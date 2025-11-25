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
        """SubagentStart hook should set in_subagent to True."""
        state_path = tmp_path / "state.json"

        # Initial state - not in subagent
        initial_state = {
            "session_id": "test-session",
            "in_subagent": False,
            "subagent_depth": 0,
            "tool_execution_count": 0,
        }
        state_path.write_text(json.dumps(initial_state))

        # Run SubagentStart hook
        with patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path)):
            hook.run_subagent_start_hook()

        # Verify state updated
        state = json.loads(state_path.read_text())
        assert state["in_subagent"] is True
        assert state["subagent_depth"] == 1
        assert state["tool_execution_count"] == 0  # Counter unchanged

    def test_subagent_stop_clears_flag(self, tmp_path):
        """SubagentStop hook should set in_subagent to False."""
        state_path = tmp_path / "state.json"

        # Initial state - in subagent
        initial_state = {
            "session_id": "test-session",
            "in_subagent": True,
            "subagent_depth": 1,
            "tool_execution_count": 10,
        }
        state_path.write_text(json.dumps(initial_state))

        # Run SubagentStop hook
        with patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path)):
            hook.run_subagent_stop_hook()

        # Verify state updated
        state = json.loads(state_path.read_text())
        assert state["in_subagent"] is False
        assert state["subagent_depth"] == 0
        assert state["tool_execution_count"] == 10  # Counter unchanged

    def test_nested_subagents_tracking(self, tmp_path):
        """Subagent depth tracks nesting (though Claude Code prevents this)."""
        state_path = tmp_path / "state.json"

        initial_state = {
            "session_id": "test-session",
            "in_subagent": False,
            "subagent_depth": 0,
            "tool_execution_count": 0,
        }
        state_path.write_text(json.dumps(initial_state))

        with patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path)):
            # Enter first subagent
            hook.run_subagent_start_hook()
            state = json.loads(state_path.read_text())
            assert state["subagent_depth"] == 1
            assert state["in_subagent"] is True

            # Enter nested subagent (hypothetical)
            hook.run_subagent_start_hook()
            state = json.loads(state_path.read_text())
            assert state["subagent_depth"] == 2
            assert state["in_subagent"] is True

            # Exit first nested subagent
            hook.run_subagent_stop_hook()
            state = json.loads(state_path.read_text())
            assert state["subagent_depth"] == 1
            assert state["in_subagent"] is True  # Still in outer subagent

            # Exit outer subagent
            hook.run_subagent_stop_hook()
            state = json.loads(state_path.read_text())
            assert state["subagent_depth"] == 0
            assert state["in_subagent"] is False


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
            "subagent_depth": 0,
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
            "subagent_depth": 0,
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
            result = hook.should_inject_reminder(state, config)
            assert result == expected, f"Count {count} should return {expected}"

    def test_should_inject_reminder_disabled_in_subagent(self, tmp_path):
        """should_inject_reminder returns False when in subagent context."""
        config = {"subagent_reminder_enabled": True, "subagent_reminder_frequency": 5}

        # Even at count 5, 10, 15 - no reminder in subagent
        for count in [5, 10, 15, 20]:
            state = {"in_subagent": True, "tool_execution_count": count}
            result = hook.should_inject_reminder(state, config)
            assert (
                result is False
            ), f"Count {count} in subagent should not inject reminder"

    def test_should_inject_reminder_disabled_via_config(self, tmp_path):
        """should_inject_reminder returns False when feature disabled."""
        config = {"subagent_reminder_enabled": False, "subagent_reminder_frequency": 5}

        state = {"in_subagent": False, "tool_execution_count": 5}

        result = hook.should_inject_reminder(state, config)
        assert result is False

    @patch("builtins.print")
    def test_inject_subagent_reminder_prints_message(self, mock_print):
        """inject_subagent_reminder prints JSON with block decision to stdout."""
        config = {"subagent_reminder_message": "TEST REMINDER MESSAGE"}

        hook.inject_subagent_reminder(config)

        # Verify print called with JSON output to stdout
        import sys
        import json

        mock_print.assert_called_once()
        call_args = mock_print.call_args

        # Parse the JSON output
        json_output = json.loads(call_args[0][0])
        assert json_output["decision"] == "block"
        assert json_output["reason"] == "TEST REMINDER MESSAGE"
        assert call_args[1]["file"] == sys.stdout
        assert call_args[1]["flush"] is True

    @patch("builtins.print")
    def test_inject_subagent_reminder_default_message(self, mock_print):
        """inject_subagent_reminder uses default message if not configured."""
        config = {}

        hook.inject_subagent_reminder(config)

        # Verify default message used
        import sys
        import json

        mock_print.assert_called_once()
        call_args = mock_print.call_args

        # Parse JSON output
        json_output = json.loads(call_args[0][0])
        assert json_output["decision"] == "block"
        assert "Task tool" in json_output["reason"]
        assert call_args[1]["file"] == sys.stdout


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
            "subagent_depth": 0,
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

            # Verify reminder was printed as JSON with block decision
            import sys

            reminder_printed = False
            for call in mock_print.call_args_list:
                if call[0] and call[1].get("file") == sys.stdout:
                    try:
                        json_output = json.loads(call[0][0])
                        if json_output.get(
                            "decision"
                        ) == "block" and "TEST REMINDER" in json_output.get(
                            "reason", ""
                        ):
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
            "subagent_depth": 0,
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
            "subagent_depth": 1,
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
                        if json_output.get(
                            "decision"
                        ) == "block" and "TEST REMINDER" in json_output.get(
                            "reason", ""
                        ):
                            reminder_printed = True
                            break
                    except (json.JSONDecodeError, AttributeError):
                        continue

            assert (
                reminder_printed
            ), "Reminder should print at count 10 after exiting subagent"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
