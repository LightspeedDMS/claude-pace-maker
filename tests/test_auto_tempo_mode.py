#!/usr/bin/env python3
"""
Unit tests for auto tempo mode functionality.

Tests should_run_tempo() logic with tempo_mode="auto" and related helper functions.
"""

import pytest
from datetime import datetime, timedelta


def test_should_run_tempo_mode_off():
    """Test tempo_mode='off' always returns False."""
    from pacemaker.hook import should_run_tempo

    config = {"tempo_mode": "off"}
    state = {}

    result = should_run_tempo(config, state)

    assert result is False


def test_should_run_tempo_mode_on():
    """Test tempo_mode='on' always returns True."""
    from pacemaker.hook import should_run_tempo

    config = {"tempo_mode": "on"}
    state = {}

    result = should_run_tempo(config, state)

    assert result is True


def test_should_run_tempo_auto_no_interaction():
    """Test tempo_mode='auto' with no recorded interaction returns True."""
    from pacemaker.hook import should_run_tempo

    config = {"tempo_mode": "auto", "auto_tempo_threshold_minutes": 10}
    state = {}  # No last_user_interaction_time

    result = should_run_tempo(config, state)

    assert result is True  # No interaction recorded, assume unattended


def test_should_run_tempo_auto_recent_interaction():
    """Test tempo_mode='auto' with recent interaction returns False."""
    from pacemaker.hook import should_run_tempo

    config = {"tempo_mode": "auto", "auto_tempo_threshold_minutes": 10}
    # User interacted 5 minutes ago (within threshold)
    last_interaction = datetime.now() - timedelta(minutes=5)
    state = {"last_user_interaction_time": last_interaction}

    result = should_run_tempo(config, state)

    assert result is False  # User active within threshold


def test_should_run_tempo_auto_stale_interaction():
    """Test tempo_mode='auto' with old interaction returns True."""
    from pacemaker.hook import should_run_tempo

    config = {"tempo_mode": "auto", "auto_tempo_threshold_minutes": 10}
    # User interacted 15 minutes ago (exceeds threshold)
    last_interaction = datetime.now() - timedelta(minutes=15)
    state = {"last_user_interaction_time": last_interaction}

    result = should_run_tempo(config, state)

    assert result is True  # User inactive, tempo should engage


def test_should_run_tempo_session_override_precedence():
    """Test session override takes precedence over auto mode."""
    from pacemaker.hook import should_run_tempo

    config = {"tempo_mode": "auto", "auto_tempo_threshold_minutes": 10}
    # User inactive for 15 minutes (would normally engage)
    last_interaction = datetime.now() - timedelta(minutes=15)
    # But session override says NO
    state = {
        "last_user_interaction_time": last_interaction,
        "tempo_session_enabled": False,
    }

    result = should_run_tempo(config, state)

    assert result is False  # Session override takes precedence


def test_should_run_tempo_session_override_enabled():
    """Test session override can force tempo on despite recent activity."""
    from pacemaker.hook import should_run_tempo

    config = {"tempo_mode": "auto", "auto_tempo_threshold_minutes": 10}
    # User active 2 minutes ago (would normally NOT engage)
    last_interaction = datetime.now() - timedelta(minutes=2)
    # But session override forces ON
    state = {
        "last_user_interaction_time": last_interaction,
        "tempo_session_enabled": True,
    }

    result = should_run_tempo(config, state)

    assert result is True  # Session override forces engagement


def test_should_run_tempo_auto_exact_threshold():
    """Test tempo_mode='auto' at exact threshold boundary."""
    from pacemaker.hook import should_run_tempo

    config = {"tempo_mode": "auto", "auto_tempo_threshold_minutes": 10}
    # User interacted exactly 10 minutes ago
    last_interaction = datetime.now() - timedelta(minutes=10)
    state = {"last_user_interaction_time": last_interaction}

    result = should_run_tempo(config, state)

    # At threshold, tempo should engage (>= comparison)
    assert result is True


def test_should_run_tempo_backward_compat_tempo_enabled_true():
    """Test backward compatibility: tempo_enabled=true maps to tempo_mode='on'."""
    from pacemaker.hook import should_run_tempo

    # Old config format
    config = {"tempo_enabled": True}  # No tempo_mode
    state = {}

    result = should_run_tempo(config, state)

    # Should behave as tempo_mode="on"
    assert result is True


def test_should_run_tempo_backward_compat_tempo_enabled_false():
    """Test backward compatibility: tempo_enabled=false maps to tempo_mode='off'."""
    from pacemaker.hook import should_run_tempo

    # Old config format
    config = {"tempo_enabled": False}  # No tempo_mode
    state = {}

    result = should_run_tempo(config, state)

    # Should behave as tempo_mode="off"
    assert result is False


def test_should_run_tempo_default_mode_is_auto():
    """Test default tempo_mode is 'auto' when not specified."""
    from pacemaker.hook import should_run_tempo

    config = {}  # No tempo_mode or tempo_enabled
    # User active 5 minutes ago
    last_interaction = datetime.now() - timedelta(minutes=5)
    state = {"last_user_interaction_time": last_interaction}

    result = should_run_tempo(config, state)

    # Default auto mode with default threshold (10 min), user active -> False
    assert result is False


def test_format_elapsed_time_none():
    """Test format_elapsed_time returns 'never' for None."""
    from pacemaker.hook import format_elapsed_time

    result = format_elapsed_time(None)

    assert result == "never"


def test_format_elapsed_time_seconds():
    """Test format_elapsed_time formats seconds correctly."""
    from pacemaker.hook import format_elapsed_time

    # 30 seconds ago
    timestamp = datetime.now() - timedelta(seconds=30)

    result = format_elapsed_time(timestamp)

    assert "30 seconds ago" in result or "29 seconds ago" in result  # Allow 1s variance


def test_format_elapsed_time_minutes():
    """Test format_elapsed_time formats minutes correctly."""
    from pacemaker.hook import format_elapsed_time

    # 5 minutes ago
    timestamp = datetime.now() - timedelta(minutes=5)

    result = format_elapsed_time(timestamp)

    assert "5 minutes ago" in result or "4 minutes ago" in result  # Allow variance


def test_format_elapsed_time_hours():
    """Test format_elapsed_time formats hours correctly."""
    from pacemaker.hook import format_elapsed_time

    # 2.5 hours ago
    timestamp = datetime.now() - timedelta(hours=2.5)

    result = format_elapsed_time(timestamp)

    assert "2.5 hours ago" in result or "2.4 hours ago" in result  # Allow variance


def test_parse_tempo_command_on():
    """Test parsing 'pace-maker tempo on' command."""
    from pacemaker.user_commands import parse_command

    result = parse_command("pace-maker tempo on")

    assert result["is_pace_maker_command"] is True
    assert result["command"] == "tempo"
    assert result["subcommand"] == "on"


def test_parse_tempo_command_off():
    """Test parsing 'pace-maker tempo off' command."""
    from pacemaker.user_commands import parse_command

    result = parse_command("pace-maker tempo off")

    assert result["is_pace_maker_command"] is True
    assert result["command"] == "tempo"
    assert result["subcommand"] == "off"


def test_parse_tempo_command_auto():
    """Test parsing 'pace-maker tempo auto' command."""
    from pacemaker.user_commands import parse_command

    result = parse_command("pace-maker tempo auto")

    assert result["is_pace_maker_command"] is True
    assert result["command"] == "tempo"
    assert result["subcommand"] == "auto"


def test_execute_tempo_command_auto():
    """Test 'pace-maker tempo auto' sets tempo_mode to 'auto'."""
    import tempfile
    import json
    from pacemaker.user_commands import execute_command

    # Create temp config
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        config_path = f.name
        json.dump({"tempo_mode": "on"}, f)

    try:
        result = execute_command("tempo", config_path, subcommand="auto")

        assert result["success"] is True
        assert "auto" in result["message"].lower()

        # Verify config was updated
        with open(config_path) as f:
            config = json.load(f)
        assert config["tempo_mode"] == "auto"

    finally:
        import os

        os.unlink(config_path)


def test_execute_tempo_command_on():
    """Test 'pace-maker tempo on' sets tempo_mode to 'on'."""
    import tempfile
    import json
    from pacemaker.user_commands import execute_command

    # Create temp config
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        config_path = f.name
        json.dump({"tempo_mode": "auto"}, f)

    try:
        result = execute_command("tempo", config_path, subcommand="on")

        assert result["success"] is True

        # Verify config was updated
        with open(config_path) as f:
            config = json.load(f)
        assert config["tempo_mode"] == "on"

    finally:
        import os

        os.unlink(config_path)


def test_execute_tempo_command_off():
    """Test 'pace-maker tempo off' sets tempo_mode to 'off'."""
    import tempfile
    import json
    from pacemaker.user_commands import execute_command

    # Create temp config
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        config_path = f.name
        json.dump({"tempo_mode": "auto"}, f)

    try:
        result = execute_command("tempo", config_path, subcommand="off")

        assert result["success"] is True

        # Verify config was updated
        with open(config_path) as f:
            config = json.load(f)
        assert config["tempo_mode"] == "off"

    finally:
        import os

        os.unlink(config_path)


def test_user_prompt_submit_tracks_interaction_time():
    """Test run_user_prompt_submit() updates last_user_interaction_time and resets subagent counter."""
    import tempfile
    import json
    import io
    import sys
    from datetime import datetime
    from pacemaker.hook import run_user_prompt_submit

    # Create temp state file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        state_path = f.name
        json.dump({"subagent_counter": 2, "in_subagent": True}, f)

    # Mock stdin with user input
    original_stdin = sys.stdin
    sys.stdin = io.StringIO('{"session_id": "test", "prompt": "hello"}')

    # Temporarily replace DEFAULT_STATE_PATH
    import pacemaker.hook as hook_module

    original_state_path = hook_module.DEFAULT_STATE_PATH
    hook_module.DEFAULT_STATE_PATH = state_path

    try:
        # Run hook (will print to stdout and call sys.exit)
        original_stdout = sys.stdout
        sys.stdout = io.StringIO()  # Suppress output

        # Expect SystemExit since hook calls sys.exit(0)
        with pytest.raises(SystemExit) as exc_info:
            run_user_prompt_submit()

        sys.stdout = original_stdout
        assert exc_info.value.code == 0

        # Load state after hook runs
        with open(state_path) as f:
            state = json.load(f)

        # Verify last_user_interaction_time was set
        assert "last_user_interaction_time" in state
        interaction_time_str = state["last_user_interaction_time"]
        interaction_time = datetime.fromisoformat(interaction_time_str)

        # Verify timestamp is recent (within last few seconds)
        elapsed = (datetime.now() - interaction_time).total_seconds()
        assert elapsed < 5  # Should be very recent

        # Verify subagent counter was reset
        assert state["subagent_counter"] == 0
        assert state["in_subagent"] is False

    finally:
        sys.stdin = original_stdin
        hook_module.DEFAULT_STATE_PATH = original_state_path
        import os

        os.unlink(state_path)
