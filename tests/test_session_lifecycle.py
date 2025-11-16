#!/usr/bin/env python3
"""
Tests for session lifecycle tracking functionality.

Tests the tempo on/off commands, marker detection, and Stop hook
lifecycle checking according to Story #7 acceptance criteria.
"""

import unittest
import tempfile
import os
import json
import io
from unittest.mock import patch


class TestTempoCommands(unittest.TestCase):
    """Test tempo on/off commands."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.json")

    def tearDown(self):
        """Clean up."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_tempo_off_command_disables_tracking(self):
        """Should execute 'pace-maker tempo off' and disable lifecycle tracking."""
        from pacemaker.user_commands import handle_user_prompt

        result = handle_user_prompt("pace-maker tempo off", self.config_path, None)

        self.assertTrue(result["intercepted"])
        self.assertIn("tempo", result["output"].lower())

        # Verify config was updated
        with open(self.config_path) as f:
            config = json.load(f)
            self.assertFalse(config["tempo_enabled"])

    def test_tempo_on_command_enables_tracking(self):
        """Should execute 'pace-maker tempo on' and enable lifecycle tracking."""
        from pacemaker.user_commands import handle_user_prompt

        # First turn it off
        handle_user_prompt("pace-maker tempo off", self.config_path, None)

        # Then turn it back on
        result = handle_user_prompt("pace-maker tempo on", self.config_path, None)

        self.assertTrue(result["intercepted"])
        self.assertIn("tempo", result["output"].lower())

        # Verify config was updated
        with open(self.config_path) as f:
            config = json.load(f)
            self.assertTrue(config["tempo_enabled"])

    def test_config_defaults_to_tempo_enabled(self):
        """Should default tempo_enabled to true in new config."""
        from pacemaker.user_commands import _load_config

        config = _load_config(self.config_path)

        self.assertTrue(config["tempo_enabled"])


class TestSessionLifecycleMarkers(unittest.TestCase):
    """Test session lifecycle marker detection and management."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.state_path = os.path.join(self.temp_dir, "state.json")

    def tearDown(self):
        """Clean up."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_mark_implementation_started(self):
        """Should set IMPLEMENTATION_START marker in state."""
        from pacemaker.lifecycle import mark_implementation_started

        mark_implementation_started(self.state_path)

        # Verify marker was set in state file
        with open(self.state_path) as f:
            state = json.load(f)
        self.assertTrue(state.get("implementation_started", False))

    def test_mark_implementation_completed(self):
        """Should set IMPLEMENTATION_COMPLETE marker in state."""
        from pacemaker.lifecycle import mark_implementation_completed

        mark_implementation_completed(self.state_path)

        # Verify marker was set in state file
        with open(self.state_path) as f:
            state = json.load(f)
        self.assertTrue(state.get("implementation_completed", False))

    def test_clear_lifecycle_markers(self):
        """Should clear both lifecycle markers from state."""
        from pacemaker.lifecycle import (
            mark_implementation_started,
            mark_implementation_completed,
            clear_lifecycle_markers,
        )

        # Set both markers
        mark_implementation_started(self.state_path)
        mark_implementation_completed(self.state_path)

        # Clear markers
        clear_lifecycle_markers(self.state_path)

        # Verify both are cleared
        with open(self.state_path) as f:
            state = json.load(f)
        self.assertFalse(state.get("implementation_started", False))
        self.assertFalse(state.get("implementation_completed", False))


class TestSessionStartHook(unittest.TestCase):
    """Test session start hook that detects /implement-* commands."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.json")
        self.state_path = os.path.join(self.temp_dir, "state.json")

    def tearDown(self):
        """Clean up."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_detect_implement_story_command(self):
        """Should detect /implement-story command and set marker."""
        from pacemaker.lifecycle import should_mark_implementation_start

        result = should_mark_implementation_start("/implement-story story.md")
        self.assertTrue(result)

    def test_detect_implement_epic_command(self):
        """Should detect /implement-epic command and set marker."""
        from pacemaker.lifecycle import should_mark_implementation_start

        result = should_mark_implementation_start("/implement-epic epic-name")
        self.assertTrue(result)

    def test_ignore_non_implementation_commands(self):
        """Should not detect non-implementation commands."""
        from pacemaker.lifecycle import should_mark_implementation_start

        self.assertFalse(should_mark_implementation_start("/help"))
        self.assertFalse(should_mark_implementation_start("pace-maker status"))
        self.assertFalse(should_mark_implementation_start("Can you help me?"))
        self.assertFalse(should_mark_implementation_start("/write-epic epic-name"))

    def test_session_start_hook_marks_implementation_started(self):
        """Should mark implementation started when /implement-* is detected."""
        from pacemaker.hook import run_session_start_hook

        # Create config with tempo enabled
        config = {"tempo_enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Simulate user input with /implement-story
        user_input = "/implement-story story.md"

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                run_session_start_hook(user_input)

        # Verify marker was set in state
        with open(self.state_path) as f:
            state = json.load(f)
        self.assertTrue(state.get("implementation_started", False))

    def test_session_start_hook_disabled_when_tempo_off(self):
        """Should not mark implementation started when tempo is disabled."""
        from pacemaker.hook import run_session_start_hook

        # Create config with tempo disabled
        config = {"tempo_enabled": False}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Simulate user input with /implement-story
        user_input = "/implement-story story.md"

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                run_session_start_hook(user_input)

        # Verify marker was NOT set (state file shouldn't exist)
        self.assertFalse(os.path.exists(self.state_path))


class TestStopHook(unittest.TestCase):
    """Test Stop hook lifecycle checking."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.json")
        self.state_path = os.path.join(self.temp_dir, "state.json")

    def tearDown(self):
        """Clean up."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    # NOTE: Stop hook tests have been moved to test_stop_hook_conversation_scanning.py
    # The Stop hook now scans conversation transcripts instead of state files

    def test_stop_hook_allows_when_tempo_disabled(self):
        """Should allow session end when tempo tracking is disabled."""
        from pacemaker.hook import run_stop_hook

        # Create config with tempo DISABLED
        config = {"tempo_enabled": False}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Mock stdin (doesn't matter what's in transcript when tempo disabled)
        mock_stdin = io.StringIO(json.dumps({"transcript_path": "/nonexistent"}))

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                with patch("sys.stdin", mock_stdin):
                    result = run_stop_hook()

        # Should allow session end (tempo disabled)
        self.assertEqual(result["decision"], "allow")

    def test_stop_hook_prevents_infinite_loop(self):
        """Should prevent infinite loop by tracking prompt count - see test_stop_hook_conversation_scanning.py for full test."""
        # This test is now in test_stop_hook_conversation_scanning.py
        # which uses the conversation scanning approach
        pass


class TestImplementationCompleteDetection(unittest.TestCase):
    """Test detection of IMPLEMENTATION_COMPLETE in Claude responses."""

    def test_detect_exact_implementation_complete_response(self):
        """Should detect exact 'IMPLEMENTATION_COMPLETE' response."""
        from pacemaker.lifecycle import is_implementation_complete_response

        self.assertTrue(is_implementation_complete_response("IMPLEMENTATION_COMPLETE"))

    def test_detect_implementation_complete_with_text_before(self):
        """Should detect IMPLEMENTATION_COMPLETE with text before it."""
        from pacemaker.lifecycle import is_implementation_complete_response

        self.assertTrue(
            is_implementation_complete_response("All done. IMPLEMENTATION_COMPLETE")
        )
        self.assertTrue(
            is_implementation_complete_response(
                "Tests passed, code reviewed.\n\nIMPLEMENTATION_COMPLETE"
            )
        )

    def test_detect_implementation_complete_with_text_after(self):
        """Should detect IMPLEMENTATION_COMPLETE with text after it."""
        from pacemaker.lifecycle import is_implementation_complete_response

        self.assertTrue(
            is_implementation_complete_response("IMPLEMENTATION_COMPLETE. Moving on.")
        )
        self.assertTrue(
            is_implementation_complete_response(
                "IMPLEMENTATION_COMPLETE\n\nDeploying now."
            )
        )

    def test_detect_implementation_complete_with_text_before_and_after(self):
        """Should detect IMPLEMENTATION_COMPLETE with text before and after."""
        from pacemaker.lifecycle import is_implementation_complete_response

        self.assertTrue(
            is_implementation_complete_response(
                "All tests pass.\n\nIMPLEMENTATION_COMPLETE\n\nDeploying now."
            )
        )
        self.assertTrue(
            is_implementation_complete_response(
                "Code review approved. IMPLEMENTATION_COMPLETE Ready for production."
            )
        )

    def test_detect_implementation_complete_multiline(self):
        """Should detect IMPLEMENTATION_COMPLETE in multiline response."""
        from pacemaker.lifecycle import is_implementation_complete_response

        multiline = """All tasks completed successfully:
- Unit tests: PASS
- Integration tests: PASS
- Code review: APPROVED

IMPLEMENTATION_COMPLETE

Ready for deployment."""

        self.assertTrue(is_implementation_complete_response(multiline))

    def test_reject_lowercase_implementation_complete(self):
        """Should reject lowercase 'implementation_complete' (case sensitive)."""
        from pacemaker.lifecycle import is_implementation_complete_response

        self.assertFalse(is_implementation_complete_response("implementation_complete"))
        self.assertFalse(is_implementation_complete_response("Implementation_Complete"))
        self.assertFalse(is_implementation_complete_response("IMPLEMENTATION_complete"))

    def test_reject_partial_match_in_variable_name(self):
        """Should reject partial matches like MY_IMPLEMENTATION_COMPLETE_VAR."""
        from pacemaker.lifecycle import is_implementation_complete_response

        self.assertFalse(
            is_implementation_complete_response("MY_IMPLEMENTATION_COMPLETE_THING")
        )
        self.assertFalse(
            is_implementation_complete_response("IMPLEMENTATION_COMPLETE_VAR")
        )
        self.assertFalse(
            is_implementation_complete_response("PREFIX_IMPLEMENTATION_COMPLETE")
        )

    def test_allow_implementation_complete_with_whitespace(self):
        """Should allow IMPLEMENTATION_COMPLETE with surrounding whitespace."""
        from pacemaker.lifecycle import is_implementation_complete_response

        self.assertTrue(
            is_implementation_complete_response("  IMPLEMENTATION_COMPLETE  ")
        )
        self.assertTrue(
            is_implementation_complete_response("\nIMPLEMENTATION_COMPLETE\n")
        )
        self.assertTrue(
            is_implementation_complete_response("\t\tIMPLEMENTATION_COMPLETE\t\t")
        )

    def test_reject_empty_or_missing_marker(self):
        """Should reject empty strings or responses without marker."""
        from pacemaker.lifecycle import is_implementation_complete_response

        self.assertFalse(is_implementation_complete_response(""))
        self.assertFalse(is_implementation_complete_response("All tasks complete"))
        self.assertFalse(is_implementation_complete_response("Done"))
        self.assertFalse(
            is_implementation_complete_response("Implementation is complete")
        )


if __name__ == "__main__":
    unittest.main()
