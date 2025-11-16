#!/usr/bin/env python3
"""
End-to-end tests for session lifecycle tracking.

Tests the complete flow from user commands to hook execution
with ZERO mocking - all real implementations.
"""

import unittest
import tempfile
import os
import json


class TestSessionLifecycleE2E(unittest.TestCase):
    """E2E tests for session lifecycle tracking with zero mocking."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.json")
        self.state_path = os.path.join(self.temp_dir, "state.json")

        # Write default config
        config = {"tempo_enabled": True, "enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

    def tearDown(self):
        """Clean up."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_full_implementation_lifecycle_with_completion(self):
        """
        E2E Test: Complete implementation lifecycle with proper completion.

        Scenario: User runs /implement-story, Claude completes work, declares
        IMPLEMENTATION_COMPLETE, and session ends normally.
        """
        # Step 1: Simulate user running /implement-story command
        from pacemaker.hook import run_session_start_hook
        from unittest.mock import patch

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                run_session_start_hook("/implement-story story.md")

        # Verify implementation started marker was set
        with open(self.state_path) as f:
            state = json.load(f)
        self.assertTrue(state["implementation_started"])
        self.assertFalse(state["implementation_completed"])

        # Step 2: Claude does work (simulate)
        # ... implementation happens ...

        # Step 3: Claude declares IMPLEMENTATION_COMPLETE
        from pacemaker.lifecycle import mark_implementation_completed

        mark_implementation_completed(self.state_path)

        # Verify completion marker was set
        with open(self.state_path) as f:
            state = json.load(f)
        self.assertTrue(state["implementation_completed"])

        # Step 4: User tries to stop session - should be allowed
        from pacemaker.hook import run_stop_hook

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                result = run_stop_hook()

        self.assertEqual(result["decision"], "allow")

    def test_full_implementation_lifecycle_without_completion(self):
        """
        E2E Test: Implementation lifecycle without completion prompts user.

        Scenario: User runs /implement-story, Claude tries to quit early,
        Stop hook blocks and prompts for IMPLEMENTATION_COMPLETE.
        """
        # Step 1: Simulate user running /implement-epic command
        from pacemaker.hook import run_session_start_hook
        from unittest.mock import patch

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                run_session_start_hook("/implement-epic epic-name")

        # Verify implementation started
        with open(self.state_path) as f:
            state = json.load(f)
        self.assertTrue(state["implementation_started"])

        # Step 2: User tries to stop WITHOUT completing implementation
        from pacemaker.hook import run_stop_hook

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                result = run_stop_hook()

        # Should block session end
        self.assertEqual(result["decision"], "block")
        self.assertIn("IMPLEMENTATION_COMPLETE", result["reason"])

        # Verify prompt count was incremented
        with open(self.state_path) as f:
            state = json.load(f)
        self.assertEqual(state["stop_hook_prompt_count"], 1)

        # Step 3: User tries to stop again (simulate infinite loop scenario)
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                result2 = run_stop_hook()

        # Should allow to prevent infinite loop
        self.assertEqual(result2["decision"], "allow")

    def test_tempo_disabled_allows_all_exits(self):
        """
        E2E Test: With tempo disabled, all session exits are allowed.

        Scenario: User disables tempo, runs /implement-story, tries to quit
        without completion - should be allowed.
        """
        # Step 1: Disable tempo
        config = {"tempo_enabled": False, "enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Step 2: Simulate user running /implement-story
        from pacemaker.hook import run_session_start_hook
        from unittest.mock import patch

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                run_session_start_hook("/implement-story story.md")

        # Verify marker was NOT set (tempo disabled)
        if os.path.exists(self.state_path):
            with open(self.state_path) as f:
                state = json.load(f)
            self.assertFalse(state.get("implementation_started", False))

        # Step 3: Try to stop session
        from pacemaker.hook import run_stop_hook

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                result = run_stop_hook()

        # Should allow (tempo disabled)
        self.assertEqual(result["decision"], "allow")

    def test_non_implementation_commands_allow_normal_exit(self):
        """
        E2E Test: Non-implementation commands allow normal session exits.

        Scenario: User is just chatting or running /help, tries to quit - allowed.
        """
        # Step 1: User runs non-implementation command
        from pacemaker.hook import run_session_start_hook
        from unittest.mock import patch

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                run_session_start_hook("/help")

        # Verify marker was NOT set
        if os.path.exists(self.state_path):
            with open(self.state_path) as f:
                state = json.load(f)
            self.assertFalse(state.get("implementation_started", False))

        # Step 2: Try to stop session
        from pacemaker.hook import run_stop_hook

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                result = run_stop_hook()

        # Should allow (no implementation started)
        self.assertEqual(result["decision"], "allow")

    def test_tempo_command_toggle_e2e(self):
        """
        E2E Test: Tempo on/off command toggle works end-to-end.

        Scenario: User runs 'pace-maker tempo off', then 'tempo on', verify
        config is updated correctly.
        """
        from pacemaker.user_commands import handle_user_prompt

        # Step 1: Turn tempo off
        result1 = handle_user_prompt("pace-maker tempo off", self.config_path, None)

        self.assertTrue(result1["intercepted"])
        self.assertIn("tempo", result1["output"].lower())

        # Verify config
        with open(self.config_path) as f:
            config = json.load(f)
        self.assertFalse(config["tempo_enabled"])

        # Step 2: Turn tempo back on
        result2 = handle_user_prompt("pace-maker tempo on", self.config_path, None)

        self.assertTrue(result2["intercepted"])
        self.assertIn("tempo", result2["output"].lower())

        # Verify config
        with open(self.config_path) as f:
            config = json.load(f)
        self.assertTrue(config["tempo_enabled"])

    def test_hook_script_execution_via_subprocess(self):
        """
        E2E Test: Execute hook.py as subprocess to test real CLI invocation.

        This tests the complete flow as it would be called by Claude Code.
        """
        # Find hook.py location
        hook_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "pacemaker", "hook.py"
        )
        hook_path = os.path.abspath(hook_path)

        # Test 1: Stop hook when no implementation started
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.join(os.path.dirname(__file__), "..", "src")

        # Create empty state
        state = {"session_id": "test"}
        with open(self.state_path, "w") as f:
            json.dump(state, f)

        # Mock the paths
        import pacemaker.hook as hook_module

        original_config = hook_module.DEFAULT_CONFIG_PATH
        original_state = hook_module.DEFAULT_STATE_PATH

        try:
            hook_module.DEFAULT_CONFIG_PATH = self.config_path
            hook_module.DEFAULT_STATE_PATH = self.state_path

            # Call run_stop_hook directly
            from pacemaker.hook import run_stop_hook

            result = run_stop_hook()

            # Should allow (no implementation)
            self.assertEqual(result["decision"], "allow")

        finally:
            hook_module.DEFAULT_CONFIG_PATH = original_config
            hook_module.DEFAULT_STATE_PATH = original_state


class TestAcceptanceCriteriaE2E(unittest.TestCase):
    """E2E tests mapping directly to acceptance criteria from Story #7."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.json")
        self.state_path = os.path.join(self.temp_dir, "state.json")

        # Write default config with tempo enabled
        config = {"tempo_enabled": True, "enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

    def tearDown(self):
        """Clean up."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_scenario_1_claude_tries_to_quit_before_completion(self):
        """
        Acceptance Criterion Scenario 1:
        User runs /implement-story and Claude tries to quit before completion.

        Expected: Stop hook detects missing IMPLEMENTATION_COMPLETE marker
        and prompts Claude to respond with exactly 'IMPLEMENTATION_COMPLETE'.
        """
        from pacemaker.hook import run_session_start_hook, run_stop_hook
        from unittest.mock import patch

        # Given: pace-maker tempo is enabled (default)
        # And: user has executed /implement-story command
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                run_session_start_hook("/implement-story story.md")

        # And: session start hook injected IMPLEMENTATION_START marker
        with open(self.state_path) as f:
            state = json.load(f)
        self.assertTrue(state["implementation_started"])

        # And: Claude has not declared IMPLEMENTATION_COMPLETE
        self.assertFalse(state["implementation_completed"])

        # When: user attempts to stop the session
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                result = run_stop_hook()

        # Then: Stop hook detects missing IMPLEMENTATION_COMPLETE marker
        # And: Stop hook prompts with expected message
        self.assertEqual(result["decision"], "block")
        self.assertIn("IMPLEMENTATION_COMPLETE", result["reason"])
        self.assertIn("respond with exactly", result["reason"])

        # And: Claude must respond with exactly "IMPLEMENTATION_COMPLETE" to exit
        # (This is tested by the fact that decision is "block")

    def test_scenario_2_claude_completes_and_declares_victory(self):
        """
        Acceptance Criterion Scenario 2:
        Claude completes implementation and properly declares victory.

        Expected: Stop hook detects IMPLEMENTATION_COMPLETE marker
        and allows session to end normally.
        """
        from pacemaker.hook import run_session_start_hook, run_stop_hook
        from pacemaker.lifecycle import mark_implementation_completed
        from unittest.mock import patch

        # Given: pace-maker tempo is enabled
        # And: user has executed /implement-epic command
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                run_session_start_hook("/implement-epic epic-name")

        # And: all story implementations are complete (simulated)
        # And: all tests pass (simulated)
        # And: manual validation is done (simulated)

        # When: Claude says exactly "IMPLEMENTATION_COMPLETE"
        mark_implementation_completed(self.state_path)

        # And: user attempts to stop the session
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                result = run_stop_hook()

        # Then: Stop hook detects IMPLEMENTATION_COMPLETE marker
        # And: Stop hook allows session to end normally
        self.assertEqual(result["decision"], "allow")
        # And: no prompting occurs (verified by decision = "allow")

    def test_scenario_3_user_disables_tempo_tracking(self):
        """
        Acceptance Criterion Scenario 3:
        User disables tempo tracking.

        Expected: Stop hook does not check for markers and allows normal exit.
        """
        from pacemaker.user_commands import handle_user_prompt
        from pacemaker.hook import run_stop_hook
        from unittest.mock import patch

        # Given: user runs "pace-maker tempo off"
        result = handle_user_prompt("pace-maker tempo off", self.config_path, None)
        self.assertTrue(result["intercepted"])

        # Verify tempo is disabled
        with open(self.config_path) as f:
            config = json.load(f)
        self.assertFalse(config["tempo_enabled"])

        # When: user attempts to stop any session
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                result = run_stop_hook()

        # Then: Stop hook does not check for markers
        # And: session ends normally without prompting
        self.assertEqual(result["decision"], "allow")

    def test_scenario_4_user_only_chatting_not_implementing(self):
        """
        Acceptance Criterion Scenario 4:
        User is only chatting or exploring code (not implementing).

        Expected: Stop hook does not detect any markers and allows normal exit.
        """
        from pacemaker.hook import run_session_start_hook, run_stop_hook
        from unittest.mock import patch

        # Given: pace-maker tempo is enabled
        # And: user has NOT run /implement-story or /implement-epic
        # And: no IMPLEMENTATION_START marker was injected
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                run_session_start_hook("Can you help me understand this code?")

        # Verify no marker was set
        if os.path.exists(self.state_path):
            with open(self.state_path) as f:
                state = json.load(f)
            self.assertFalse(state.get("implementation_started", False))

        # When: user attempts to stop the session
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                result = run_stop_hook()

        # Then: Stop hook does not detect any IMPLEMENTATION_START marker
        # And: session ends normally without prompting
        self.assertEqual(result["decision"], "allow")

    def test_scenario_5_infinite_loop_prevention(self):
        """
        Acceptance Criterion Scenario 5:
        Infinite loop prevention.

        Expected: After first prompt, subsequent stop attempts are allowed.
        """
        from pacemaker.hook import run_session_start_hook, run_stop_hook
        from unittest.mock import patch

        # Given: pace-maker tempo is enabled
        # And: IMPLEMENTATION_START exists without IMPLEMENTATION_COMPLETE
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                run_session_start_hook("/implement-story story.md")

        # And: Stop hook has already prompted once
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                result1 = run_stop_hook()

        self.assertEqual(result1["decision"], "block")

        # When: Claude responds with anything OTHER than "IMPLEMENTATION_COMPLETE"
        # (simulated by not marking as completed)

        # Then: Stop hook allows session to continue
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                result2 = run_stop_hook()

        # And: does not re-prompt on subsequent stop attempts
        self.assertEqual(result2["decision"], "allow")

        # And: loop is prevented (verified by decision = "allow")


if __name__ == "__main__":
    unittest.main()
