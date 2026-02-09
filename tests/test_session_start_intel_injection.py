#!/usr/bin/env python3
"""
Unit tests for Session Start Intel Guidance Injection.

Tests that the intel_guidance.md prompt is loaded and injected into
Claude's context at session start, enabling Prompt Intelligence Telemetry.
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


class TestSessionStartIntelInjection:
    """Test suite for intel guidance prompt injection at session start."""

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
        """Config with pace-maker enabled."""
        config = {
            "enabled": True,
            "intent_validation_enabled": False,  # Focus on intel only
        }
        with open(temp_dirs["config_path"], "w") as f:
            json.dump(config, f)
        return temp_dirs["config_path"]

    @pytest.fixture
    def mock_state(self, temp_dirs):
        """Create mock state file."""
        state = {
            "session_id": "test-session",
            "subagent_counter": 0,
            "in_subagent": False,
        }
        with open(temp_dirs["state_path"], "w") as f:
            json.dump(state, f)
        return temp_dirs["state_path"]

    def test_intel_guidance_is_injected(
        self, temp_dirs, mock_config, mock_state, capsys
    ):
        """
        Test that intel guidance prompt is included in session start output.

        This is the core requirement - intel_guidance.md must be loaded and printed.
        """
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", mock_state):
                hook.run_session_start_hook()

        # Capture output
        captured = capsys.readouterr()

        # Should contain intel guidance header
        assert "Prompt Intelligence Telemetry" in captured.out

    def test_intel_guidance_includes_symbol_vocabulary(
        self, temp_dirs, mock_config, mock_state, capsys
    ):
        """
        Test that intel guidance includes the symbol vocabulary.

        Users need to see what symbols mean to emit correct intel lines.
        """
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", mock_state):
                hook.run_session_start_hook()

        # Capture output
        captured = capsys.readouterr()

        # Should contain symbol definitions
        assert "§" in captured.out  # Intel line marker
        assert "△" in captured.out  # Frustration
        assert "◎" in captured.out  # Specificity
        assert "■" in captured.out  # Task type
        assert "◇" in captured.out  # Quality
        assert "↻" in captured.out  # Iteration

    def test_intel_guidance_includes_examples(
        self, temp_dirs, mock_config, mock_state, capsys
    ):
        """
        Test that intel guidance includes concrete examples.

        Examples help Claude understand how to emit intel lines correctly.
        """
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", mock_state):
                hook.run_session_start_hook()

        # Capture output
        captured = capsys.readouterr()

        # Should contain example section
        assert "Examples" in captured.out

        # Should contain at least one complete example
        assert "§ △" in captured.out  # Example intel line

    def test_intel_guidance_explains_when_to_emit(
        self, temp_dirs, mock_config, mock_state, capsys
    ):
        """
        Test that intel guidance explains when to emit intel lines.

        Claude needs to know this is optional and when to use it.
        """
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", mock_state):
                hook.run_session_start_hook()

        # Capture output
        captured = capsys.readouterr()

        # Should explain when to emit
        assert "When to Emit" in captured.out
        assert "Optional" in captured.out or "optional" in captured.out

    def test_session_start_preserves_existing_functionality(
        self, temp_dirs, mock_config, mock_state
    ):
        """
        Test that adding intel injection doesn't break existing session start behavior.

        Subagent counter reset and state management must still work.
        """
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", mock_state):
                hook.run_session_start_hook()

        # Verify state was updated correctly
        with open(mock_state) as f:
            state = json.load(f)

        # Existing behavior must be preserved
        assert state["subagent_counter"] == 0
        assert state["in_subagent"] is False

    def test_intel_injection_works_with_intent_validation(
        self, temp_dirs, mock_state, capsys
    ):
        """
        Test that intel guidance and intent validation can both be displayed.

        Both features should work together without conflict.
        """
        # Config with both features enabled
        config = {
            "enabled": True,
            "intent_validation_enabled": True,
        }
        config_path = temp_dirs["config_path"]
        with open(config_path, "w") as f:
            json.dump(config, f)

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", mock_state):
                hook.run_session_start_hook()

        # Capture output
        captured = capsys.readouterr()

        # Should contain BOTH intel guidance AND intent validation
        assert "Prompt Intelligence Telemetry" in captured.out
        assert "INTENT VALIDATION ENABLED" in captured.out

    def test_graceful_failure_if_intel_prompt_missing(
        self, temp_dirs, mock_config, mock_state, capsys
    ):
        """
        Test that session start doesn't crash if intel_guidance.md is missing.

        Should log warning but continue with session start.
        """
        # Mock the prompt loader to raise FileNotFoundError
        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", mock_config):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", mock_state):
                with patch(
                    "pacemaker.prompt_loader.PromptLoader.load_prompt",
                    side_effect=FileNotFoundError("intel_guidance.md not found"),
                ):
                    # Should not raise exception
                    hook.run_session_start_hook()

        # Verify state was still updated (session start continued)
        with open(mock_state) as f:
            state = json.load(f)

        assert state["subagent_counter"] == 0

    def test_intel_guidance_is_present_with_other_features(
        self, temp_dirs, mock_state, capsys
    ):
        """
        Test that intel guidance is present when multiple features are enabled.

        Should work alongside intent validation and model nudges.
        """
        # Config with multiple features
        config = {
            "enabled": True,
            "intent_validation_enabled": True,
            "preferred_subagent_model": "opus-4.5",  # Triggers model nudge
        }
        config_path = temp_dirs["config_path"]
        with open(config_path, "w") as f:
            json.dump(config, f)

        with patch("pacemaker.hook.DEFAULT_CONFIG_PATH", config_path):
            with patch("pacemaker.hook.DEFAULT_STATE_PATH", mock_state):
                hook.run_session_start_hook()

        # Capture output
        captured = capsys.readouterr()

        # Intel guidance must be present in output
        assert (
            "Prompt Intelligence Telemetry" in captured.out
        ), "Intel guidance must be present even when other features are enabled"
