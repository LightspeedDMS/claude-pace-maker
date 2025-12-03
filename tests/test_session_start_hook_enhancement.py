#!/usr/bin/env python3
"""
Unit tests for Phase 6: Session Start Hook Enhancement.

Tests the enhanced run_session_start_hook() function that displays
intent validation mandate when feature is enabled.
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Add src to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pacemaker import hook


class TestSessionStartHookEnhancement:
    """Test suite for enhanced session start hook with intent validation mandate."""

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
    def mock_config_disabled(self, temp_dirs):
        """Config with intent validation disabled."""
        config = {
            "enabled": True,
            "intent_validation_enabled": False,
        }
        with open(temp_dirs["config_path"], "w") as f:
            json.dump(config, f)
        return temp_dirs["config_path"]

    @pytest.fixture
    def mock_config_enabled(self, temp_dirs):
        """Config with intent validation enabled."""
        config = {
            "enabled": True,
            "intent_validation_enabled": True,
        }
        with open(temp_dirs["config_path"], "w") as f:
            json.dump(config, f)
        return temp_dirs["config_path"]

    @pytest.fixture
    def mock_state(self, temp_dirs):
        """Create mock state file."""
        state = {
            "session_id": "test-session",
            "subagent_counter": 5,  # Will be reset to 0
            "in_subagent": True,  # Will be reset to False
        }
        with open(temp_dirs["state_path"], "w") as f:
            json.dump(state, f)
        return temp_dirs["state_path"]

    def test_resets_subagent_counter(self, temp_dirs, mock_state):
        """
        Test that session start resets subagent_counter to 0.

        This is existing behavior - must not break it.
        """
        with patch("pacemaker.hook.DEFAULT_STATE_PATH", mock_state):
            hook.run_session_start_hook()

            # Read state and verify counter reset
            with open(mock_state) as f:
                state = json.load(f)

            assert state["subagent_counter"] == 0

    def test_resets_in_subagent_flag(self, temp_dirs, mock_state):
        """
        Test that session start resets in_subagent to False.

        This is existing behavior - must not break it.
        """
        with patch("pacemaker.hook.DEFAULT_STATE_PATH", mock_state):
            hook.run_session_start_hook()

            # Read state and verify flag reset
            with open(mock_state) as f:
                state = json.load(f)

            assert state["in_subagent"] is False

    def test_no_mandate_display_when_disabled(
        self, temp_dirs, mock_config_disabled, mock_state, capsys
    ):
        """
        Test that intent validation mandate is NOT displayed when disabled.

        Feature disabled = no mandate output.
        """
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config_disabled):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", mock_state):
                hook.run_session_start_hook()

        # Capture output
        captured = capsys.readouterr()

        # Should NOT contain mandate
        assert "INTENT VALIDATION ENABLED" not in captured.out
        assert "declare your intent" not in captured.out

    def test_mandate_displayed_when_enabled(
        self, temp_dirs, mock_config_enabled, mock_state, capsys
    ):
        """
        Test that intent validation mandate IS displayed when enabled.

        This is the core Phase 6 functionality.
        """
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config_enabled):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", mock_state):
                hook.run_session_start_hook()

        # Capture output
        captured = capsys.readouterr()

        # Should contain mandate header
        assert "INTENT VALIDATION ENABLED" in captured.out

        # Should contain instructions
        assert "declare your intent" in captured.out
        assert "What file you're modifying" in captured.out
        assert "What changes you're making" in captured.out
        assert "Why/goal of the changes" in captured.out

    def test_mandate_includes_example(
        self, temp_dirs, mock_config_enabled, mock_state, capsys
    ):
        """
        Test that mandate includes example of proper intent declaration.

        Users should see concrete example of what to do.
        """
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config_enabled):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", mock_state):
                hook.run_session_start_hook()

        # Capture output
        captured = capsys.readouterr()

        # Should contain example
        assert "Example:" in captured.out
        assert "I will modify" in captured.out

    def test_mandate_has_visual_separators(
        self, temp_dirs, mock_config_enabled, mock_state, capsys
    ):
        """
        Test that mandate uses visual separators for emphasis.

        Should use === borders for visibility.
        """
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config_enabled):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", mock_state):
                hook.run_session_start_hook()

        # Capture output
        captured = capsys.readouterr()

        # Should contain separators
        assert "=" * 70 in captured.out

    def test_graceful_failure_on_config_error(self, temp_dirs, mock_state, capsys):
        """
        Test that hook fails gracefully when config loading fails.

        Missing config should not crash session start.
        """
        # Use non-existent config path
        with patch(
            "pacemaker.hook.DEFAULT_CONFIG_PATH", "/tmp/nonexistent-config.json"
        ):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", mock_state):
                # Should not raise exception
                hook.run_session_start_hook()

        # Should still reset state
        with open(mock_state) as f:
            state = json.load(f)

        assert state["subagent_counter"] == 0

    def test_mandate_format_is_readable(
        self, temp_dirs, mock_config_enabled, mock_state, capsys
    ):
        """
        Test that mandate is formatted for readability.

        Should use newlines, spacing, and structure.
        """
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config_enabled):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", mock_state):
                hook.run_session_start_hook()

        # Capture output
        captured = capsys.readouterr()

        # Should have multiple newlines for spacing
        assert "\n" in captured.out

        # Should have numbered list
        assert "1." in captured.out
        assert "2." in captured.out
        assert "3." in captured.out

    def test_mandate_includes_tdd_enforcement_section(
        self, temp_dirs, mock_config_enabled, mock_state, capsys
    ):
        """
        Test that mandate includes TDD enforcement section when enabled.

        Should display core paths, test declaration format, and user permission format.
        """
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config_enabled):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", mock_state):
                hook.run_session_start_hook()

        # Capture output
        captured = capsys.readouterr()

        # Should contain TDD enforcement header
        assert "TDD ENFORCEMENT FOR CORE CODE" in captured.out

        # Should contain core paths list
        assert "src/" in captured.out
        assert "lib/" in captured.out
        assert "core/" in captured.out
        assert "source/" in captured.out
        assert "libraries/" in captured.out
        assert "kernel/" in captured.out

        # Should contain Option A (test declaration)
        assert "Option A" in captured.out
        assert "Test coverage:" in captured.out

        # Should contain Option B (user permission)
        assert "Option B" in captured.out
        assert "User permission to skip TDD:" in captured.out

        # Should contain warning about fabricated quotes
        assert "quoted permission MUST exist" in captured.out
        assert "Fabricated quotes are rejected" in captured.out
