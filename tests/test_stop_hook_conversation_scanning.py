#!/usr/bin/env python3
"""
Tests for Stop hook conversation scanning functionality.

Tests that Stop hook correctly scans conversation transcripts for
IMPLEMENTATION_START and IMPLEMENTATION_COMPLETE markers.
"""

import unittest
import tempfile
import os
import json
from unittest.mock import patch
import sys
import io


class TestStopHookConversationScanning(unittest.TestCase):
    """Test Stop hook scans conversation for markers."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.json")
        self.state_path = os.path.join(self.temp_dir, "state.json")
        self.transcript_path = os.path.join(self.temp_dir, "transcript.jsonl")

    def tearDown(self):
        """Clean up."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def create_transcript_with_markers(self, has_start=False, has_complete=False):
        """Create a mock transcript file with optional markers."""
        messages = []

        # User message
        messages.append(
            {
                "role": "user",
                "content": [{"type": "text", "text": "Please implement this feature"}],
            }
        )

        # Assistant message with optional markers
        assistant_text = "I will implement this feature.\n\n"
        if has_start:
            assistant_text += "IMPLEMENTATION_START\n\n"
            assistant_text += "Working on the implementation...\n\n"

        if has_complete:
            assistant_text += "All tasks are done.\n\nIMPLEMENTATION_COMPLETE"

        messages.append(
            {"role": "assistant", "content": [{"type": "text", "text": assistant_text}]}
        )

        # Write transcript
        with open(self.transcript_path, "w") as f:
            for msg in messages:
                f.write(json.dumps(msg) + "\n")

    def test_read_conversation_extracts_text_from_transcript(self):
        """Should extract all text from JSONL transcript."""
        from pacemaker.hook import read_conversation_from_transcript

        # Create transcript
        self.create_transcript_with_markers(has_start=True, has_complete=False)

        # Read conversation
        text = read_conversation_from_transcript(self.transcript_path)

        # Should contain messages
        self.assertIn("Please implement this feature", text)
        self.assertIn("IMPLEMENTATION_START", text)
        self.assertIn("Working on the implementation", text)

    def test_read_conversation_handles_missing_transcript(self):
        """Should return empty string for missing transcript."""
        from pacemaker.hook import read_conversation_from_transcript

        text = read_conversation_from_transcript("/nonexistent/transcript.jsonl")

        self.assertEqual(text, "")

    def test_stop_hook_allows_exit_when_no_markers(self):
        """Should allow exit when no IMPLEMENTATION markers in conversation."""
        from pacemaker.hook import run_stop_hook

        # Create transcript without markers
        self.create_transcript_with_markers(has_start=False, has_complete=False)

        # Create enabled tempo config
        config = {"tempo_enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Mock stdin with hook data
        hook_data = {"transcript_path": self.transcript_path}
        mock_stdin = io.StringIO(json.dumps(hook_data))

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                with patch("sys.stdin", mock_stdin):
                    result = run_stop_hook()

        # Should allow exit
        self.assertEqual(result["decision"], "allow")

    def test_stop_hook_blocks_when_start_without_complete(self):
        """Should block exit when IMPLEMENTATION_START without COMPLETE."""
        from pacemaker.hook import run_stop_hook

        # Create transcript with START but no COMPLETE
        self.create_transcript_with_markers(has_start=True, has_complete=False)

        # Create enabled tempo config
        config = {"tempo_enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Mock stdin with hook data
        hook_data = {"transcript_path": self.transcript_path}
        mock_stdin = io.StringIO(json.dumps(hook_data))

        # Capture stdout
        captured = io.StringIO()
        sys.stdout = captured

        try:
            with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
                with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                    with patch("sys.stdin", mock_stdin):
                        result = run_stop_hook()

            output = captured.getvalue()

            # Should block
            self.assertEqual(result["decision"], "block")
            self.assertIn("reason", result)

            # Should print prompt
            self.assertIn("IMPLEMENTATION_COMPLETE", output)
        finally:
            sys.stdout = sys.__stdout__

    def test_stop_hook_allows_when_both_markers_present(self):
        """Should allow exit when both START and COMPLETE present."""
        from pacemaker.hook import run_stop_hook

        # Create transcript with both markers
        self.create_transcript_with_markers(has_start=True, has_complete=True)

        # Create enabled tempo config
        config = {"tempo_enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Mock stdin with hook data
        hook_data = {"transcript_path": self.transcript_path}
        mock_stdin = io.StringIO(json.dumps(hook_data))

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                with patch("sys.stdin", mock_stdin):
                    result = run_stop_hook()

        # Should allow exit
        self.assertEqual(result["decision"], "allow")

    def test_stop_hook_prevents_infinite_loop_after_one_prompt(self):
        """Should allow exit after prompting once to prevent infinite loop."""
        from pacemaker.hook import run_stop_hook

        # Create transcript with START but no COMPLETE
        self.create_transcript_with_markers(has_start=True, has_complete=False)

        # Create enabled tempo config
        config = {"tempo_enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Set prompt count to 1 (already prompted once)
        state = {"stop_hook_prompt_count": 1}
        with open(self.state_path, "w") as f:
            json.dump(state, f)

        # Mock stdin with hook data
        hook_data = {"transcript_path": self.transcript_path}
        mock_stdin = io.StringIO(json.dumps(hook_data))

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                with patch("sys.stdin", mock_stdin):
                    result = run_stop_hook()

        # Should allow exit (infinite loop prevention)
        self.assertEqual(result["decision"], "allow")

    def test_stop_hook_increments_prompt_count_on_block(self):
        """Should increment prompt count when blocking."""
        from pacemaker.hook import run_stop_hook
        from pacemaker import lifecycle

        # Create transcript with START but no COMPLETE
        self.create_transcript_with_markers(has_start=True, has_complete=False)

        # Create enabled tempo config
        config = {"tempo_enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Mock stdin with hook data
        hook_data = {"transcript_path": self.transcript_path}
        mock_stdin = io.StringIO(json.dumps(hook_data))

        # Suppress stdout
        captured = io.StringIO()
        sys.stdout = captured

        try:
            with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
                with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                    with patch("sys.stdin", mock_stdin):
                        run_stop_hook()

            # Should have incremented prompt count
            prompt_count = lifecycle.get_stop_hook_prompt_count(self.state_path)
            self.assertEqual(prompt_count, 1)
        finally:
            sys.stdout = sys.__stdout__

    def test_stop_hook_allows_when_tempo_disabled(self):
        """Should allow exit when tempo_enabled is False."""
        from pacemaker.hook import run_stop_hook

        # Create transcript with START but no COMPLETE
        self.create_transcript_with_markers(has_start=True, has_complete=False)

        # Create disabled tempo config
        config = {"tempo_enabled": False}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Mock stdin with hook data
        hook_data = {"transcript_path": self.transcript_path}
        mock_stdin = io.StringIO(json.dumps(hook_data))

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                with patch("sys.stdin", mock_stdin):
                    result = run_stop_hook()

        # Should allow exit (tempo disabled)
        self.assertEqual(result["decision"], "allow")

    def test_stop_hook_allows_when_no_transcript_path(self):
        """Should allow exit when transcript_path not provided."""
        from pacemaker.hook import run_stop_hook

        # Create enabled tempo config
        config = {"tempo_enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Mock stdin with empty hook data
        hook_data = {}
        mock_stdin = io.StringIO(json.dumps(hook_data))

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                with patch("sys.stdin", mock_stdin):
                    result = run_stop_hook()

        # Should allow exit (graceful degradation)
        self.assertEqual(result["decision"], "allow")

    def test_stop_hook_allows_when_transcript_missing(self):
        """Should allow exit when transcript file doesn't exist."""
        from pacemaker.hook import run_stop_hook

        # Create enabled tempo config
        config = {"tempo_enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Mock stdin with non-existent transcript path
        hook_data = {"transcript_path": "/nonexistent/transcript.jsonl"}
        mock_stdin = io.StringIO(json.dumps(hook_data))

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                with patch("sys.stdin", mock_stdin):
                    result = run_stop_hook()

        # Should allow exit (graceful degradation)
        self.assertEqual(result["decision"], "allow")


if __name__ == "__main__":
    unittest.main()
