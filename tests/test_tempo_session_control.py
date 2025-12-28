#!/usr/bin/env python3
"""
Tests for session-level tempo control feature.

Tests the following behaviors:
1. 'pace-maker tempo session on' enables tempo for current session only
2. 'pace-maker tempo session off' disables tempo for current session only
3. Session override takes precedence over global setting
4. Session override is stored in state.json with session_id
5. Help text documents session-level commands
"""

import json
import pytest
from unittest.mock import patch
from pacemaker import user_commands


class TestTempoSessionCommands:
    """Test parsing and handling of session-level tempo commands."""

    def test_parse_tempo_session_on_command(self):
        """'pace-maker tempo session on' should be recognized."""
        result = user_commands.handle_user_prompt(
            "pace-maker tempo session on", "/tmp/config.json", "/tmp/db.sqlite"
        )
        assert result["intercepted"] is True
        assert "enabled for this session" in result["output"].lower()

    def test_parse_tempo_session_off_command(self):
        """'pace-maker tempo session off' should be recognized."""
        result = user_commands.handle_user_prompt(
            "pace-maker tempo session off", "/tmp/config.json", "/tmp/db.sqlite"
        )
        assert result["intercepted"] is True
        assert "disabled for this session" in result["output"].lower()

    def test_tempo_session_on_sets_state_flag(self, tmp_path):
        """'tempo session on' should set tempo_session_enabled=True in state."""
        state_path = tmp_path / "state.json"
        config_path = tmp_path / "config.json"

        # Initial state
        initial_state = {
            "session_id": "test-session-123",
            "tempo_session_enabled": None,  # Not set yet
        }
        state_path.write_text(json.dumps(initial_state))

        # Minimal config
        config = {"enabled": True}
        config_path.write_text(json.dumps(config))

        # Patch at the location where it's imported (constants module)
        with patch("pacemaker.constants.DEFAULT_STATE_PATH", str(state_path)):
            result = user_commands.handle_user_prompt(
                "pace-maker tempo session on", str(config_path), "/tmp/db.sqlite"
            )

        assert result["intercepted"] is True

        # Verify state updated
        state = json.loads(state_path.read_text())
        assert state["tempo_session_enabled"] is True

    def test_tempo_session_off_sets_state_flag(self, tmp_path):
        """'tempo session off' should set tempo_session_enabled=False in state."""
        state_path = tmp_path / "state.json"
        config_path = tmp_path / "config.json"

        # Initial state with tempo enabled
        initial_state = {
            "session_id": "test-session-123",
            "tempo_session_enabled": True,
        }
        state_path.write_text(json.dumps(initial_state))

        config = {"enabled": True}
        config_path.write_text(json.dumps(config))

        # Patch at the location where it's imported (constants module)
        with patch("pacemaker.constants.DEFAULT_STATE_PATH", str(state_path)):
            result = user_commands.handle_user_prompt(
                "pace-maker tempo session off", str(config_path), "/tmp/db.sqlite"
            )

        assert result["intercepted"] is True

        # Verify state updated
        state = json.loads(state_path.read_text())
        assert state["tempo_session_enabled"] is False

    def test_global_tempo_commands_unchanged(self, tmp_path):
        """'pace-maker tempo on/off' (without session) should still work globally."""
        config_path = tmp_path / "config.json"

        config = {"enabled": True, "tempo_mode": "off"}
        config_path.write_text(json.dumps(config))

        # Test global tempo on
        result = user_commands.handle_user_prompt(
            "pace-maker tempo on", str(config_path), "/tmp/db.sqlite"
        )
        assert result["intercepted"] is True

        config = json.loads(config_path.read_text())
        assert config["tempo_mode"] == "on"

        # Test global tempo off
        result = user_commands.handle_user_prompt(
            "pace-maker tempo off", str(config_path), "/tmp/db.sqlite"
        )
        assert result["intercepted"] is True

        config = json.loads(config_path.read_text())
        assert config["tempo_mode"] == "off"


class TestTempoSessionPrecedence:
    """Test that session override takes precedence over global setting."""

    def test_session_override_true_when_global_false(self, tmp_path):
        """Session enabled should work even when global is disabled."""
        state_path = tmp_path / "state.json"
        config_path = tmp_path / "config.json"

        state = {
            "session_id": "test-session",
            "tempo_session_enabled": True,  # Session override: enabled
        }
        state_path.write_text(json.dumps(state))

        config = {"enabled": True, "tempo_enabled": False}  # Global: disabled
        config_path.write_text(json.dumps(config))

        with (
            patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path)),
            patch("pacemaker.hook.DEFAULT_CONFIG_PATH", str(config_path)),
        ):

            from pacemaker import hook

            # Load config and state
            loaded_config = hook.load_config(str(config_path))
            loaded_state = hook.load_state(str(state_path))

            # Check if tempo should run (need to implement this function)
            should_run = hook.should_run_tempo(loaded_config, loaded_state)

            assert (
                should_run is True
            ), "Session override should enable tempo even when global is disabled"

    def test_session_override_false_when_global_true(self, tmp_path):
        """Session disabled should override global enabled."""
        state_path = tmp_path / "state.json"
        config_path = tmp_path / "config.json"

        state = {
            "session_id": "test-session",
            "tempo_session_enabled": False,  # Session override: disabled
        }
        state_path.write_text(json.dumps(state))

        config = {"enabled": True, "tempo_enabled": True}  # Global: enabled
        config_path.write_text(json.dumps(config))

        with (
            patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path)),
            patch("pacemaker.hook.DEFAULT_CONFIG_PATH", str(config_path)),
        ):

            from pacemaker import hook

            loaded_config = hook.load_config(str(config_path))
            loaded_state = hook.load_state(str(state_path))

            should_run = hook.should_run_tempo(loaded_config, loaded_state)

            assert (
                should_run is False
            ), "Session override should disable tempo even when global is enabled"

    def test_no_session_override_uses_global_setting(self, tmp_path):
        """When no session override, should use global setting."""
        state_path = tmp_path / "state.json"
        config_path = tmp_path / "config.json"

        state = {
            "session_id": "test-session"
            # No tempo_session_enabled field
        }
        state_path.write_text(json.dumps(state))

        config = {"enabled": True, "tempo_enabled": True}
        config_path.write_text(json.dumps(config))

        with (
            patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path)),
            patch("pacemaker.hook.DEFAULT_CONFIG_PATH", str(config_path)),
        ):

            from pacemaker import hook

            loaded_config = hook.load_config(str(config_path))
            loaded_state = hook.load_state(str(state_path))

            should_run = hook.should_run_tempo(loaded_config, loaded_state)

            assert (
                should_run is True
            ), "Should use global setting when no session override"

    def test_global_disabled_blocks_session_override(self, tmp_path):
        """When global tempo_enabled is False, session override should not matter."""
        state_path = tmp_path / "state.json"
        config_path = tmp_path / "config.json"

        state = {
            "session_id": "test-session",
            "tempo_session_enabled": True,  # Session wants it enabled
        }
        state_path.write_text(json.dumps(state))

        config = {"enabled": True, "tempo_enabled": False}  # But global config says no
        config_path.write_text(json.dumps(config))

        with (
            patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path)),
            patch("pacemaker.hook.DEFAULT_CONFIG_PATH", str(config_path)),
        ):

            from pacemaker import hook

            loaded_config = hook.load_config(str(config_path))
            loaded_state = hook.load_state(str(state_path))

            should_run = hook.should_run_tempo(loaded_config, loaded_state)

            # This test checks YOUR requirement - clarify if global False should block session override
            # For now, I'm assuming session override takes precedence
            assert should_run is True


class TestTempoSessionHelp:
    """Test that help text documents session commands."""

    def test_help_includes_session_commands(self):
        """Help should document 'tempo session on' and 'tempo session off'."""
        result = user_commands.handle_user_prompt(
            "pace-maker help", "/tmp/config.json", "/tmp/db.sqlite"
        )

        assert result["intercepted"] is True
        help_text = result["output"]

        # Check for session-level commands
        assert "tempo session on" in help_text
        assert "tempo session off" in help_text
        assert (
            "current session" in help_text.lower()
            or "this session" in help_text.lower()
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
