"""
Unit tests for pace-maker cross-session-awareness CLI toggle command.

Spec: CLI command `pace-maker cross-session-awareness on/off` toggles the
`cross_session_awareness_enabled` flag in config.json.

Tests:
- test_csa_on_sets_flag: 'on' subcommand sets flag True and returns success
- test_csa_off_clears_flag: 'off' subcommand sets flag False and returns success
- test_csa_unknown_subcommand: unknown subcommand returns success=False
- test_csa_wired_in_execute_command: execute_command dispatches 'cross-session-awareness'
"""

import json

# ── Constants ─────────────────────────────────────────────────────────────────
CONFIG_KEY = "cross_session_awareness_enabled"


def _write_config(path: str, data: dict) -> None:
    """Write JSON config to path."""
    with open(path, "w") as f:
        json.dump(data, f)


def _read_config(path: str) -> dict:
    """Read JSON config from path."""
    with open(path) as f:
        return json.load(f)


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_csa_on_sets_flag(tmp_path):
    """'cross-session-awareness on' sets cross_session_awareness_enabled=True in config."""
    config_path = str(tmp_path / "config.json")
    _write_config(config_path, {CONFIG_KEY: False, "enabled": True})

    from pacemaker.user_commands import _execute_cross_session_awareness

    result = _execute_cross_session_awareness(config_path, "on")

    assert result["success"] is True, f"Expected success, got: {result}"
    cfg = _read_config(config_path)
    assert (
        cfg[CONFIG_KEY] is True
    ), f"Expected {CONFIG_KEY}=True after 'on', got {cfg[CONFIG_KEY]!r}"


def test_csa_off_clears_flag(tmp_path):
    """'cross-session-awareness off' sets cross_session_awareness_enabled=False in config."""
    config_path = str(tmp_path / "config.json")
    _write_config(config_path, {CONFIG_KEY: True, "enabled": True})

    from pacemaker.user_commands import _execute_cross_session_awareness

    result = _execute_cross_session_awareness(config_path, "off")

    assert result["success"] is True, f"Expected success, got: {result}"
    cfg = _read_config(config_path)
    assert (
        cfg[CONFIG_KEY] is False
    ), f"Expected {CONFIG_KEY}=False after 'off', got {cfg[CONFIG_KEY]!r}"


def test_csa_unknown_subcommand(tmp_path):
    """Unknown subcommand returns success=False with informative message."""
    config_path = str(tmp_path / "config.json")
    _write_config(config_path, {"enabled": True})

    from pacemaker.user_commands import _execute_cross_session_awareness

    result = _execute_cross_session_awareness(config_path, "toggle")

    assert result["success"] is False
    msg_lower = result["message"].lower()
    assert (
        "on" in msg_lower
        or "off" in msg_lower
        or "unknown" in msg_lower
        or "usage" in msg_lower
    )


def test_csa_wired_in_execute_command(tmp_path):
    """execute_command dispatches 'cross-session-awareness' to the toggle handler."""
    config_path = str(tmp_path / "config.json")
    _write_config(config_path, {CONFIG_KEY: False, "enabled": True})

    from pacemaker.user_commands import execute_command

    result = execute_command(
        command="cross-session-awareness",
        config_path=config_path,
        db_path=str(tmp_path / "usage.db"),
        subcommand="on",
    )

    assert result["success"] is True, f"execute_command dispatch failed: {result}"
    cfg = _read_config(config_path)
    assert cfg[CONFIG_KEY] is True
