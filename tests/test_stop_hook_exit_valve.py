#!/usr/bin/env python3
"""
Unit tests for the stop hook exit valve counter.

The exit valve prevents infinite block loops when an agent only produces text
(arguing about why it cannot do E2E, for example) without using any tools.
After 5 consecutive stop-hook blocks without an intervening tool use, the valve
activates: it returns {"continue": True} and resets the counter to 0.

Counter lifecycle:
- Incremented in run_stop_hook() when decision == "block"
- Reset in run_stop_hook() when decision != "block" (APPROVED / COMPLETE)
- Reset in handle_post_tool_use() whenever a tool is executed
  (tool use = agent doing real work, breaking the text-only loop)

Exit valve threshold:
- _EXIT_VALVE_THRESHOLD = 4  (local constant in run_stop_hook)
- When counter >= 4 AND another block would fire -> activate valve (5th consecutive block)
"""

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from pacemaker.hook import STOP_EXIT_VALVE_THRESHOLD as EXIT_VALVE_THRESHOLD


# ---------------------------------------------------------------------------
# JSONL helpers
# ---------------------------------------------------------------------------


def make_assistant_text_entry(text="I cannot do E2E testing for this work."):
    """Create a JSONL assistant text entry."""
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        },
    }


def make_user_entry(text="Please continue."):
    """Create a JSONL user text entry."""
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": text}],
        },
    }


def write_transcript(path, entries):
    """Write a list of entries as JSONL to path."""
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStopHookExitValve(unittest.TestCase):
    """Tests for the exit valve counter in run_stop_hook() and handle_post_tool_use()."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.transcript_path = os.path.join(self.temp_dir, "transcript.jsonl")
        self.state_path = os.path.join(self.temp_dir, "state.json")
        self.db_path = os.path.join(self.temp_dir, "test.db")

        write_transcript(
            self.transcript_path,
            [
                make_user_entry("Please implement something."),
                make_assistant_text_entry(
                    "I cannot run E2E tests because the environment is not set up."
                ),
            ],
        )

    def tearDown(self):
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    # ------------------------------------------------------------------
    # Shared patch helpers
    # ------------------------------------------------------------------

    def _make_stop_hook_data(self):
        return {
            "session_id": "test-session-123",
            "transcript_path": self.transcript_path,
        }

    @contextmanager
    def _run_stop_hook_with_mock(self, initial_count, validator_result):
        """Wire up all standard stop-hook patches for run_stop_hook() tests.

        Yields mock_save_state so callers can inspect what was saved.
        """
        state = {
            "consecutive_stop_blocks": initial_count,
            "session_id": "test-session-123",
        }
        config = {
            "enabled": True,
            "tempo_mode": "on",
            "hook_model": "auto",
            "conversation_context_size": 5,
        }
        with (
            patch("pacemaker.hook.load_config", return_value=config),
            patch("pacemaker.hook.load_state", return_value=state),
            patch("pacemaker.hook.save_state") as mock_save_state,
            patch("pacemaker.hook.is_context_exhaustion_detected", return_value=False),
            patch(
                "pacemaker.transcript_reader.detect_silent_tool_stop",
                return_value=False,
            ),
            patch("pacemaker.hook.should_run_tempo", return_value=True),
            patch("pacemaker.langfuse.orchestrator.handle_stop_finalize"),
            patch(
                "pacemaker.intent_validator.validate_intent",
                return_value=validator_result,
            ),
            patch("pacemaker.hook.record_blockage"),
            patch("pacemaker.hook.record_activity_event"),
            patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch("sys.stdin") as mock_stdin,
        ):
            mock_stdin.read.return_value = json.dumps(self._make_stop_hook_data())
            yield mock_save_state

    @contextmanager
    def _post_tool_use_patches(self, initial_state):
        """Wire up all patches for handle_post_tool_use() tests.

        Yields a list that accumulates every save_state call's state snapshot.
        """
        captured_states = []

        def capture_save_state(state, path=None):
            captured_states.append(dict(state))

        with (
            patch(
                "pacemaker.hook.load_config",
                return_value={
                    "enabled": True,
                    "tempo_mode": "on",
                },
            ),
            patch("pacemaker.hook.load_state", return_value=dict(initial_state)),
            patch("pacemaker.hook.save_state", side_effect=capture_save_state),
            patch("pacemaker.hook.database.initialize_database"),
            patch(
                "pacemaker.hook.pacing_engine.run_pacing_check",
                return_value={"decision": {}, "polled": False},
            ),
            patch("pacemaker.hook.should_inject_reminder", return_value=False),
            patch("pacemaker.hook.get_secrets_nudge", return_value=None),
            patch("pacemaker.hook.record_activity_event"),
            patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch("pacemaker.hook._accumulate_fallback_cost"),
        ):
            yield captured_states

    def _saved_states_with_count(self, mock_save_state, count):
        """Return save_state calls whose first positional arg has consecutive_stop_blocks == count."""
        return [
            call.args[0]
            for call in mock_save_state.call_args_list
            if call.args
            and isinstance(call.args[0], dict)
            and call.args[0].get("consecutive_stop_blocks") == count
        ]

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_consecutive_stop_blocks_increments_on_each_block(self):
        """Counter increments by 1 on each consecutive block up to threshold.

        For initial counts 0 through EXIT_VALVE_THRESHOLD-1, a block result
        must increment the counter by 1 without triggering the exit valve.
        """
        from pacemaker.hook import run_stop_hook

        block_result = {"decision": "block", "reason": "Work is incomplete."}

        for initial_count in range(EXIT_VALVE_THRESHOLD):
            expected_count = initial_count + 1
            with self.subTest(initial_count=initial_count):
                with self._run_stop_hook_with_mock(
                    initial_count, block_result
                ) as mock_save:
                    result = run_stop_hook()

                assert (
                    result.get("decision") == "block"
                ), f"Expected block at initial_count={initial_count}, got {result}"
                incremented = self._saved_states_with_count(mock_save, expected_count)
                assert len(incremented) >= 1, (
                    f"Expected consecutive_stop_blocks={expected_count} to be saved "
                    f"when initial={initial_count}. "
                    f"Calls: {[c.args[0] for c in mock_save.call_args_list if c.args]}"
                )

    def test_exit_valve_activates_at_fifth_block(self):
        """Exit valve fires on the 5th consecutive block without tool use.

        When consecutive_stop_blocks == EXIT_VALVE_THRESHOLD (4 previous blocks)
        and the intent validator would block again:
        - Returns {"continue": True}
        - Resets counter to 0
        """
        from pacemaker.hook import run_stop_hook

        block_result = {"decision": "block", "reason": "Still incomplete."}

        with self._run_stop_hook_with_mock(
            EXIT_VALVE_THRESHOLD, block_result
        ) as mock_save:
            result = run_stop_hook()

        assert (
            result.get("continue") is True
        ), f"Expected exit valve to allow exit at 5th block, got {result}"
        assert (
            "decision" not in result
        ), f"Exit valve result must not contain 'decision', got {result}"
        reset_states = self._saved_states_with_count(mock_save, 0)
        assert len(reset_states) >= 1, (
            "Exit valve must reset consecutive_stop_blocks to 0 on activation. "
            f"save_state calls: {[c.args[0] for c in mock_save.call_args_list if c.args]}"
        )

    def test_counter_resets_on_approve(self):
        """Counter resets to 0 when intent validator returns APPROVED.

        After EXIT_VALVE_THRESHOLD - 1 previous blocks, when the validator
        returns APPROVED, consecutive_stop_blocks must be reset to 0.
        """
        from pacemaker.hook import run_stop_hook

        approve_result = {"decision": "approve", "reason": "Work is complete."}
        prior_blocks = EXIT_VALVE_THRESHOLD - 1

        with self._run_stop_hook_with_mock(prior_blocks, approve_result) as mock_save:
            result = run_stop_hook()

        assert (
            result.get("decision") != "block"
        ), f"Expected APPROVED result, got {result}"
        reset_states = self._saved_states_with_count(mock_save, 0)
        assert len(reset_states) >= 1, (
            "APPROVED result must reset consecutive_stop_blocks to 0. "
            f"save_state calls: {[c.args[0] for c in mock_save.call_args_list if c.args]}"
        )

    def test_counter_resets_in_post_tool_use(self):
        """PostToolUse handler resets consecutive_stop_blocks to 0 on tool execution.

        When an agent uses any tool, if the exit valve counter is > 0 it must
        be reset to 0 (the agent is doing real work, breaking the text-only loop).
        """
        hook_data = {
            "session_id": "test-session-123",
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
            "tool_response": "file1.txt\nfile2.txt",
            "transcript_path": self.transcript_path,
        }
        initial_state = {
            "consecutive_stop_blocks": EXIT_VALVE_THRESHOLD - 1,
            "session_id": "test-session-123",
            "tool_execution_count": 5,
            "in_subagent": False,
        }

        with (
            self._post_tool_use_patches(initial_state) as captured_states,
            patch("sys.stdin") as mock_stdin,
        ):
            mock_stdin.read.return_value = json.dumps(hook_data)

            from pacemaker.hook import handle_post_tool_use

            handle_post_tool_use()

        assert (
            len(captured_states) >= 1
        ), "handle_post_tool_use must call save_state at least once"
        final_state = captured_states[-1]
        assert final_state.get("consecutive_stop_blocks") == 0, (
            "PostToolUse must reset consecutive_stop_blocks to 0 when tool is used. "
            f"Final saved state: {final_state}"
        )


if __name__ == "__main__":
    unittest.main()
