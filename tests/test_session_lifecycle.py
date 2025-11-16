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
import sys
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

    def test_detect_implementation_start_marker_in_state(self):
        """Should detect IMPLEMENTATION_START marker in state file."""
        from pacemaker.lifecycle import has_implementation_started

        # Create state with IMPLEMENTATION_START marker
        state = {
            "session_id": "test-session",
            "implementation_started": True,
            "implementation_completed": False,
        }

        with open(self.state_path, "w") as f:
            json.dump(state, f)

        result = has_implementation_started(self.state_path)
        self.assertTrue(result)

    def test_detect_implementation_complete_marker_in_state(self):
        """Should detect IMPLEMENTATION_COMPLETE marker in state file."""
        from pacemaker.lifecycle import has_implementation_completed

        # Create state with IMPLEMENTATION_COMPLETE marker
        state = {
            "session_id": "test-session",
            "implementation_started": True,
            "implementation_completed": True,
        }

        with open(self.state_path, "w") as f:
            json.dump(state, f)

        result = has_implementation_completed(self.state_path)
        self.assertTrue(result)

    def test_no_markers_when_state_empty(self):
        """Should return False for both markers when state is empty."""
        from pacemaker.lifecycle import (
            has_implementation_started,
            has_implementation_completed,
        )

        # Create empty state
        state = {"session_id": "test-session"}

        with open(self.state_path, "w") as f:
            json.dump(state, f)

        self.assertFalse(has_implementation_started(self.state_path))
        self.assertFalse(has_implementation_completed(self.state_path))

    def test_mark_implementation_started(self):
        """Should set IMPLEMENTATION_START marker in state."""
        from pacemaker.lifecycle import (
            mark_implementation_started,
            has_implementation_started,
        )

        mark_implementation_started(self.state_path)

        # Verify marker was set
        self.assertTrue(has_implementation_started(self.state_path))

    def test_mark_implementation_completed(self):
        """Should set IMPLEMENTATION_COMPLETE marker in state."""
        from pacemaker.lifecycle import (
            mark_implementation_completed,
            has_implementation_completed,
        )

        mark_implementation_completed(self.state_path)

        # Verify marker was set
        self.assertTrue(has_implementation_completed(self.state_path))

    def test_clear_lifecycle_markers(self):
        """Should clear both lifecycle markers from state."""
        from pacemaker.lifecycle import (
            mark_implementation_started,
            mark_implementation_completed,
            clear_lifecycle_markers,
            has_implementation_started,
            has_implementation_completed,
        )

        # Set both markers
        mark_implementation_started(self.state_path)
        mark_implementation_completed(self.state_path)

        # Clear markers
        clear_lifecycle_markers(self.state_path)

        # Verify both are cleared
        self.assertFalse(has_implementation_started(self.state_path))
        self.assertFalse(has_implementation_completed(self.state_path))


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
        from pacemaker.lifecycle import has_implementation_started

        # Create config with tempo enabled
        config = {"tempo_enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Simulate user input with /implement-story
        user_input = "/implement-story story.md"

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                run_session_start_hook(user_input)

        # Verify marker was set
        self.assertTrue(has_implementation_started(self.state_path))

    def test_session_start_hook_disabled_when_tempo_off(self):
        """Should not mark implementation started when tempo is disabled."""
        from pacemaker.hook import run_session_start_hook
        from pacemaker.lifecycle import has_implementation_started

        # Create config with tempo disabled
        config = {"tempo_enabled": False}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Simulate user input with /implement-story
        user_input = "/implement-story story.md"

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                run_session_start_hook(user_input)

        # Verify marker was NOT set
        self.assertFalse(has_implementation_started(self.state_path))


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

    def test_stop_hook_prompts_when_implementation_incomplete(self):
        """Should prompt when IMPLEMENTATION_START exists but COMPLETE is missing."""
        from pacemaker.hook import run_stop_hook
        from pacemaker.lifecycle import mark_implementation_started

        # Create config with tempo enabled
        config = {"tempo_enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Mark implementation started but not completed
        mark_implementation_started(self.state_path)

        # Capture stdout
        captured = io.StringIO()
        sys.stdout = captured

        try:
            with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
                with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                    result = run_stop_hook()

            output = captured.getvalue()

            # Should block session end
            self.assertEqual(result["decision"], "block")
            self.assertIn("IMPLEMENTATION_COMPLETE", output)
        finally:
            sys.stdout = sys.__stdout__

    def test_stop_hook_allows_when_implementation_complete(self):
        """Should allow session end when IMPLEMENTATION_COMPLETE is detected."""
        from pacemaker.hook import run_stop_hook
        from pacemaker.lifecycle import (
            mark_implementation_started,
            mark_implementation_completed,
        )

        # Create config with tempo enabled
        config = {"tempo_enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Mark implementation started AND completed
        mark_implementation_started(self.state_path)
        mark_implementation_completed(self.state_path)

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                result = run_stop_hook()

        # Should allow session end
        self.assertEqual(result["decision"], "allow")

    def test_stop_hook_allows_when_no_implementation_started(self):
        """Should allow session end when no IMPLEMENTATION_START marker exists."""
        from pacemaker.hook import run_stop_hook

        # Create config with tempo enabled
        config = {"tempo_enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # No markers set - just exploring code
        state = {"session_id": "test-session"}
        with open(self.state_path, "w") as f:
            json.dump(state, f)

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                result = run_stop_hook()

        # Should allow session end
        self.assertEqual(result["decision"], "allow")

    def test_stop_hook_allows_when_tempo_disabled(self):
        """Should allow session end when tempo tracking is disabled."""
        from pacemaker.hook import run_stop_hook
        from pacemaker.lifecycle import mark_implementation_started

        # Create config with tempo DISABLED
        config = {"tempo_enabled": False}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Mark implementation started (should be ignored)
        mark_implementation_started(self.state_path)

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                result = run_stop_hook()

        # Should allow session end (tempo disabled)
        self.assertEqual(result["decision"], "allow")

    def test_stop_hook_prevents_infinite_loop(self):
        """Should prevent infinite loop by tracking prompt count."""
        from pacemaker.hook import run_stop_hook
        from pacemaker.lifecycle import mark_implementation_started

        # Create config with tempo enabled
        config = {"tempo_enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Mark implementation started
        mark_implementation_started(self.state_path)

        # First call - should prompt
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                result1 = run_stop_hook()

        self.assertEqual(result1["decision"], "block")

        # Second call - should allow (prevent loop)
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                result2 = run_stop_hook()

        self.assertEqual(result2["decision"], "allow")


class TestImplementationCompleteDetection(unittest.TestCase):
    """Test detection of IMPLEMENTATION_COMPLETE in Claude responses."""

    def test_detect_exact_implementation_complete_response(self):
        """Should detect exact 'IMPLEMENTATION_COMPLETE' response."""
        from pacemaker.lifecycle import is_implementation_complete_response

        self.assertTrue(is_implementation_complete_response("IMPLEMENTATION_COMPLETE"))

    def test_reject_implementation_complete_with_extra_text(self):
        """Should reject IMPLEMENTATION_COMPLETE with additional text."""
        from pacemaker.lifecycle import is_implementation_complete_response

        self.assertFalse(
            is_implementation_complete_response(
                "IMPLEMENTATION_COMPLETE - all tests pass"
            )
        )
        self.assertFalse(
            is_implementation_complete_response("Yes, IMPLEMENTATION_COMPLETE")
        )
        self.assertFalse(
            is_implementation_complete_response("implementation_complete")
        )  # case sensitive

    def test_allow_implementation_complete_with_whitespace(self):
        """Should allow IMPLEMENTATION_COMPLETE with surrounding whitespace."""
        from pacemaker.lifecycle import is_implementation_complete_response

        self.assertTrue(
            is_implementation_complete_response("  IMPLEMENTATION_COMPLETE  ")
        )
        self.assertTrue(
            is_implementation_complete_response("\nIMPLEMENTATION_COMPLETE\n")
        )


if __name__ == "__main__":
    unittest.main()
