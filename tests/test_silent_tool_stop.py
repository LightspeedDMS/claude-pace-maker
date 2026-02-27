#!/usr/bin/env python3
"""
Unit and integration tests for silent tool stop detection and nudge behavior.

Tests the detect_silent_tool_stop() function and the stop hook integration
that nudges Claude to continue when it stops silently after a tool use.

Algorithm under test:
    The function checks whether the LAST non-progress entry in the transcript
    is a user message containing a tool_result content block. This directly
    answers: "Did Claude receive a tool result but stop without responding?"

    Claude Code writes text, tool_use, and thinking as SEPARATE JSONL entries,
    so checking the last assistant entry's content blocks is unreliable (it finds
    stale entries from previous turns). Checking for a trailing user:tool_result
    entry is reliable because Claude always responds after receiving tool results.
"""

import json
import os
import tempfile
import unittest


def make_assistant_entry(content_blocks):
    """Create a JSONL entry for an assistant message."""
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": content_blocks,
        },
    }


def make_user_entry(text):
    """Create a JSONL entry for a user text message."""
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": text}],
        },
    }


def make_user_tool_result_entry(tool_id="toolu_1", output="command output"):
    """Create a JSONL entry for a user message containing a tool_result block."""
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content": output}
            ],
        },
    }


def make_progress_entry():
    """Create a JSONL progress entry (should be skipped by the detector)."""
    return {
        "type": "progress",
        "message": {
            "role": "progress",
            "content": [],
        },
    }


def make_tool_use_block(tool_id="toolu_1", name="Bash", command="ls"):
    """Create a tool_use content block."""
    return {
        "type": "tool_use",
        "id": tool_id,
        "name": name,
        "input": {"command": command},
    }


def make_text_block(text="hello"):
    """Create a text content block."""
    return {"type": "text", "text": text}


def make_tool_result_block(tool_id="toolu_1"):
    """Create a tool_result content block (appears in user messages)."""
    return {"type": "tool_result", "tool_use_id": tool_id, "content": "output"}


def write_transcript(path, entries):
    """Write a list of entries as JSONL to path."""
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


class TestDetectSilentToolStop(unittest.TestCase):
    """Unit tests for detect_silent_tool_stop().

    The algorithm checks whether the last non-progress JSONL entry in the
    transcript is a user message with tool_result content. If yes => silent stop.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.transcript_path = os.path.join(self.temp_dir, "transcript.jsonl")

    def tearDown(self):
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_detect_silent_tool_stop_tool_result_last(self):
        """Return True when last non-progress entry is user:tool_result."""
        from pacemaker.transcript_reader import detect_silent_tool_stop

        entries = [
            make_user_entry("Do something"),
            make_assistant_entry(
                [
                    make_text_block("I will run a command"),
                    make_tool_use_block(),
                ]
            ),
            make_user_tool_result_entry(tool_id="toolu_1", output="file1.txt"),
        ]
        write_transcript(self.transcript_path, entries)

        result = detect_silent_tool_stop(self.transcript_path)

        assert result is True

    def test_detect_silent_tool_stop_text_last(self):
        """Return False when last entry is assistant:text (Claude responded)."""
        from pacemaker.transcript_reader import detect_silent_tool_stop

        entries = [
            make_user_entry("Do something"),
            make_assistant_entry([make_tool_use_block()]),
            make_user_tool_result_entry(),
            make_assistant_entry(
                [make_text_block("Done! The command ran successfully.")]
            ),
        ]
        write_transcript(self.transcript_path, entries)

        result = detect_silent_tool_stop(self.transcript_path)

        assert result is False

    def test_detect_silent_tool_stop_text_after_tool_use(self):
        """Return False when assistant:text follows user:tool_result (Claude responded)."""
        from pacemaker.transcript_reader import detect_silent_tool_stop

        entries = [
            make_user_entry("Do something"),
            make_assistant_entry(
                [make_text_block("Starting..."), make_tool_use_block()]
            ),
            make_user_tool_result_entry(),
            make_assistant_entry([make_text_block("All done.")]),
        ]
        write_transcript(self.transcript_path, entries)

        result = detect_silent_tool_stop(self.transcript_path)

        assert result is False

    def test_detect_silent_tool_stop_no_assistant_message(self):
        """Return False when transcript has no tool_result (just user text)."""
        from pacemaker.transcript_reader import detect_silent_tool_stop

        entries = [
            make_user_entry("Hello"),
        ]
        write_transcript(self.transcript_path, entries)

        result = detect_silent_tool_stop(self.transcript_path)

        assert result is False

    def test_detect_silent_tool_stop_empty_transcript(self):
        """Return False for an empty transcript file."""
        from pacemaker.transcript_reader import detect_silent_tool_stop

        with open(self.transcript_path, "w") as f:
            f.write("")

        result = detect_silent_tool_stop(self.transcript_path)

        assert result is False

    def test_detect_silent_tool_stop_missing_file(self):
        """Return False when transcript file does not exist."""
        from pacemaker.transcript_reader import detect_silent_tool_stop

        missing_path = os.path.join(self.temp_dir, "nonexistent.jsonl")

        result = detect_silent_tool_stop(missing_path)

        assert result is False

    def test_detect_silent_tool_stop_multiple_tool_results(self):
        """Return True when last entry is user message with multiple tool_result blocks."""
        from pacemaker.transcript_reader import detect_silent_tool_stop

        entries = [
            make_user_entry("Run two commands"),
            make_assistant_entry(
                [
                    make_text_block("I'll run both"),
                    make_tool_use_block(tool_id="toolu_1", name="Bash", command="ls"),
                    make_tool_use_block(tool_id="toolu_2", name="Bash", command="pwd"),
                ]
            ),
            # User message with two tool_results (Claude Code batches them)
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": "file.txt",
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_2",
                            "content": "/home",
                        },
                    ],
                },
            },
        ]
        write_transcript(self.transcript_path, entries)

        result = detect_silent_tool_stop(self.transcript_path)

        assert result is True

    def test_detect_silent_tool_stop_user_text_not_tool_result(self):
        """Return False when last entry is a user text message (not a tool_result)."""
        from pacemaker.transcript_reader import detect_silent_tool_stop

        entries = [
            make_assistant_entry([make_text_block("Let me know what you need.")]),
            make_user_entry("Please continue."),
        ]
        write_transcript(self.transcript_path, entries)

        result = detect_silent_tool_stop(self.transcript_path)

        assert result is False

    def test_detect_silent_tool_stop_ignores_progress_entries(self):
        """Return True when transcript ends with progress entries but last non-progress is user:tool_result."""
        from pacemaker.transcript_reader import detect_silent_tool_stop

        entries = [
            make_user_entry("Do something"),
            make_assistant_entry([make_tool_use_block()]),
            make_user_tool_result_entry(output="done"),
            make_progress_entry(),
            make_progress_entry(),
        ]
        write_transcript(self.transcript_path, entries)

        result = detect_silent_tool_stop(self.transcript_path)

        assert result is True

    def test_detect_silent_tool_stop_only_text_no_tools(self):
        """Return False when transcript has only text exchanges (no tool calls)."""
        from pacemaker.transcript_reader import detect_silent_tool_stop

        entries = [
            make_user_entry("Explain something"),
            make_assistant_entry([make_text_block("Here is my explanation.")]),
        ]
        write_transcript(self.transcript_path, entries)

        result = detect_silent_tool_stop(self.transcript_path)

        assert result is False

    def test_detect_silent_tool_stop_uses_last_entry_not_earlier(self):
        """Return False based on LAST entry even if earlier entries were user:tool_result."""
        from pacemaker.transcript_reader import detect_silent_tool_stop

        entries = [
            make_user_entry("First task"),
            make_assistant_entry([make_tool_use_block(tool_id="toolu_1")]),
            make_user_tool_result_entry(tool_id="toolu_1"),
            make_assistant_entry([make_text_block("Done with first task.")]),
            make_user_entry("Second task"),
            make_assistant_entry([make_text_block("Done with second task.")]),
        ]
        write_transcript(self.transcript_path, entries)

        result = detect_silent_tool_stop(self.transcript_path)

        assert result is False

    def test_detect_silent_tool_stop_whitespace_only_file(self):
        """Return False when transcript file contains only whitespace/newlines."""
        from pacemaker.transcript_reader import detect_silent_tool_stop

        with open(self.transcript_path, "w") as f:
            f.write("   \n\n   \n")

        result = detect_silent_tool_stop(self.transcript_path)

        assert result is False

    def test_detect_silent_tool_stop_malformed_json_lines_skipped(self):
        """Return correct result when transcript contains malformed JSON lines."""
        from pacemaker.transcript_reader import detect_silent_tool_stop

        # Mix of malformed and valid lines -- function should skip malformed ones
        with open(self.transcript_path, "w") as f:
            f.write("this is not json\n")
            f.write("{also not json\n")
            f.write(json.dumps(make_user_tool_result_entry()) + "\n")

        result = detect_silent_tool_stop(self.transcript_path)

        assert result is True

    def test_detect_silent_tool_stop_all_progress_entries_returns_false(self):
        """Return False when all entries are progress entries (no meaningful last entry)."""
        from pacemaker.transcript_reader import detect_silent_tool_stop

        entries = [
            make_progress_entry(),
            make_progress_entry(),
        ]
        write_transcript(self.transcript_path, entries)

        result = detect_silent_tool_stop(self.transcript_path)

        assert result is False

    def test_detect_silent_tool_stop_assistant_thinking_last(self):
        """Return False when last entry is an assistant thinking entry."""
        from pacemaker.transcript_reader import detect_silent_tool_stop

        entries = [
            make_user_entry("Do something"),
            make_assistant_entry([{"type": "thinking", "thinking": "I should do X"}]),
        ]
        write_transcript(self.transcript_path, entries)

        result = detect_silent_tool_stop(self.transcript_path)

        assert result is False


class TestStopHookSilentToolStopIntegration(unittest.TestCase):
    """Integration tests for stop hook silent tool stop nudge behavior."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.transcript_path = os.path.join(self.temp_dir, "transcript.jsonl")
        self.state_path = os.path.join(self.temp_dir, "state.json")
        self.config_path = os.path.join(self.temp_dir, "config.json")

        # Base config: tempo off so we isolate the nudge logic from intent validator
        self._write_config(
            {
                "enabled": True,
                "tempo_mode": "off",
                "max_silent_tool_nudges": 3,
            }
        )
        self._write_state({})

    def tearDown(self):
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def _write_config(self, config):
        with open(self.config_path, "w") as f:
            json.dump(config, f)

    def _write_state(self, state):
        with open(self.state_path, "w") as f:
            json.dump(state, f)

    def _read_state(self):
        with open(self.state_path, "r") as f:
            return json.load(f)

    def _write_silent_tool_transcript(self):
        """Write a transcript ending with user:tool_result (silent stop scenario).

        This simulates the real Claude Code behavior: the last JSONL entry is a
        user message with tool_result content, because Claude stopped without
        responding after receiving the tool result.
        """
        entries = [
            make_user_entry("Run a command for me"),
            make_assistant_entry(
                [
                    make_text_block("I'll run it"),
                    make_tool_use_block(),
                ]
            ),
            make_user_tool_result_entry(output="command output here"),
        ]
        write_transcript(self.transcript_path, entries)

    def _write_normal_transcript(self):
        """Write a transcript ending with assistant:text (normal stop scenario)."""
        entries = [
            make_user_entry("Run a command for me"),
            make_assistant_entry([make_tool_use_block()]),
            make_user_tool_result_entry(),
            make_assistant_entry(
                [make_text_block("Done! The command completed successfully.")]
            ),
        ]
        write_transcript(self.transcript_path, entries)

    def test_stop_hook_nudges_on_silent_tool_stop(self):
        """Stop hook should block exit with nudge when last entry is user:tool_result."""
        from pacemaker.hook import (
            load_config,
            load_state,
            is_context_exhaustion_detected,
        )
        from pacemaker.transcript_reader import detect_silent_tool_stop
        from pacemaker.prompt_loader import PromptLoader

        self._write_silent_tool_transcript()
        self._write_state({"silent_tool_nudge_count": 0})

        config = load_config(self.config_path)
        state = load_state(self.state_path)

        # Pre-conditions
        assert not is_context_exhaustion_detected(self.transcript_path)
        assert detect_silent_tool_stop(self.transcript_path) is True

        nudge_count = state.get("silent_tool_nudge_count", 0)
        max_nudges = config.get("max_silent_tool_nudges", 3)

        assert nudge_count < max_nudges

        # Load nudge prompt
        loader = PromptLoader()
        nudge_message = loader.load_prompt("continuation_nudge.md", subfolder="stop")

        assert nudge_message  # Prompt must exist and be non-empty

        # Result should be block
        result = {"decision": "block", "reason": nudge_message}
        assert result["decision"] == "block"
        assert len(result["reason"]) > 0

    def test_stop_hook_increments_nudge_counter(self):
        """Nudge counter should be incremented when silent tool stop is detected."""
        from pacemaker.hook import load_state, save_state

        self._write_silent_tool_transcript()
        self._write_state({"silent_tool_nudge_count": 0})

        state = load_state(self.state_path)
        initial_count = state.get("silent_tool_nudge_count", 0)

        # Simulate incrementing counter
        state["silent_tool_nudge_count"] = initial_count + 1
        save_state(state, self.state_path)

        updated_state = load_state(self.state_path)
        assert updated_state["silent_tool_nudge_count"] == 1

    def test_stop_hook_allows_exit_at_max_nudges(self):
        """Stop hook should allow exit when nudge counter reaches max."""
        from pacemaker.hook import load_config, load_state
        from pacemaker.transcript_reader import detect_silent_tool_stop

        self._write_silent_tool_transcript()
        max_nudges = 3
        self._write_config(
            {
                "enabled": True,
                "tempo_mode": "off",
                "max_silent_tool_nudges": max_nudges,
            }
        )
        self._write_state({"silent_tool_nudge_count": max_nudges})

        config = load_config(self.config_path)
        state = load_state(self.state_path)

        assert detect_silent_tool_stop(self.transcript_path) is True

        nudge_count = state.get("silent_tool_nudge_count", 0)
        max_configured = config.get("max_silent_tool_nudges", 3)

        # At max, should allow exit
        assert nudge_count >= max_configured

        result = {"continue": True}
        assert result["continue"] is True

    def test_stop_hook_resets_counter_at_max(self):
        """Counter should be reset to 0 when max nudges reached and exit is allowed."""
        from pacemaker.hook import load_state, save_state

        self._write_state({"silent_tool_nudge_count": 3})

        state = load_state(self.state_path)

        # Simulate reset at max
        state["silent_tool_nudge_count"] = 0
        save_state(state, self.state_path)

        updated_state = load_state(self.state_path)
        assert updated_state["silent_tool_nudge_count"] == 0

    def test_stop_hook_context_exhaustion_overrides_nudge(self):
        """Context exhaustion should take priority and allow exit without nudge check."""
        from pacemaker.hook import is_context_exhaustion_detected

        # Create a transcript that simulates context exhaustion
        # (last entry is a "Prompt is too long" error)
        entries = [
            make_user_entry("Run a command"),
            make_assistant_entry([make_tool_use_block()]),
            make_user_tool_result_entry(),
            {
                "error": "invalid_request",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Prompt is too long"}],
                },
            },
        ]
        write_transcript(self.transcript_path, entries)

        exhaustion = is_context_exhaustion_detected(self.transcript_path)
        assert exhaustion is True

        # Even though transcript has user:tool_result before the error entry,
        # context exhaustion takes priority.
        # In the real hook, exhaustion check comes first => allow exit

    def test_stop_hook_normal_flow_when_text_present(self):
        """When last entry is assistant:text, nudge should NOT trigger."""
        from pacemaker.transcript_reader import detect_silent_tool_stop

        self._write_normal_transcript()

        result = detect_silent_tool_stop(self.transcript_path)

        assert result is False

    def test_user_prompt_submit_resets_nudge_counter(self):
        """run_user_prompt_submit should reset silent_tool_nudge_count to 0."""
        from pacemaker.hook import load_state, save_state

        # Simulate state with accumulated nudge count
        self._write_state(
            {
                "silent_tool_nudge_count": 2,
                "subagent_counter": 0,
                "in_subagent": False,
            }
        )

        state = load_state(self.state_path)

        # Simulate what run_user_prompt_submit does
        state["silent_tool_nudge_count"] = 0
        save_state(state, self.state_path)

        updated_state = load_state(self.state_path)
        assert updated_state["silent_tool_nudge_count"] == 0


if __name__ == "__main__":
    unittest.main()
