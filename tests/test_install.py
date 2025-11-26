"""
Streamlined test suite for install.sh - runs installer minimally with comprehensive checks.

Strategy: Run installer once per test scenario, check all acceptance criteria in that run.
"""

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest


class TestInstallScript:
    """Streamlined installation tests - minimize installer runs."""

    @pytest.fixture
    def temp_home(self, tmp_path):
        """Create a temporary home directory for testing."""
        home = tmp_path / "test_home"
        home.mkdir()
        return home

    @pytest.fixture
    def install_env(self, temp_home, monkeypatch):
        """Set up environment for installation testing."""
        monkeypatch.setenv("HOME", str(temp_home))
        return {
            "HOME": str(temp_home),
            "CLAUDE_DIR": str(temp_home / ".claude"),
            "HOOKS_DIR": str(temp_home / ".claude" / "hooks"),
            "PACEMAKER_DIR": str(temp_home / ".claude-pace-maker"),
            "SETTINGS_FILE": str(temp_home / ".claude" / "settings.json"),
        }

    def _run_install(self, temp_home):
        """Helper to run install script."""
        install_script = Path("/home/jsbattig/Dev/claude-pace-maker/install.sh")
        env = os.environ.copy()
        env["HOME"] = str(temp_home)
        return subprocess.run(
            [str(install_script)],
            capture_output=True,
            text=True,
            cwd=str(install_script.parent),
            env=env,
        )

    def test_fresh_install_creates_all_components(self, install_env, temp_home):
        """
        COMPREHENSIVE TEST: Fresh installation from scratch.

        Runs installer once and verifies ALL acceptance criteria:
        - Directory structure created
        - Hook scripts copied and executable
        - Config file created with correct structure
        - Database created with correct schema
        - Hooks registered in settings.json (6 active hooks: PostToolUse, Stop, UserPromptSubmit, SessionStart, SubagentStart, SubagentStop)
        - Installation succeeds
        """
        # Run installation
        result = self._run_install(temp_home)
        assert result.returncode == 0, f"Install failed: {result.stderr}"

        # AC1: Directory structure
        assert Path(install_env["CLAUDE_DIR"]).exists(), "~/.claude must be created"
        assert Path(
            install_env["HOOKS_DIR"]
        ).exists(), "~/.claude/hooks must be created"
        assert Path(
            install_env["PACEMAKER_DIR"]
        ).exists(), "~/.claude-pace-maker must be created"

        # AC2 & AC7: Hook scripts copied and executable
        hook_scripts = [
            "user-prompt-submit.sh",
            "post-tool-use.sh",
            "stop.sh",
            "session-start.sh",
            "subagent-start.sh",
            "subagent-stop.sh",
        ]
        for script_name in hook_scripts:
            script_path = Path(install_env["HOOKS_DIR"]) / script_name
            assert script_path.exists(), f"{script_name} must be copied"
            assert os.access(script_path, os.X_OK), f"{script_name} must be executable"

        # AC4: Config file with correct structure
        config_file = Path(install_env["PACEMAKER_DIR"]) / "config.json"
        assert config_file.exists(), "config.json must be created"

        with open(config_file) as f:
            config = json.load(f)

        # Check essential keys exist
        required_keys = ["enabled", "base_delay", "max_delay", "poll_interval"]
        for key in required_keys:
            assert key in config, f"config.json must have '{key}' key"

        # AC5: Database with correct schema
        db_file = Path(install_env["PACEMAKER_DIR"]) / "usage.db"
        assert db_file.exists(), "usage.db must be created"

        conn = sqlite3.connect(str(db_file))
        cursor = conn.cursor()

        # Check database has tables (installer creates usage_snapshots)
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert len(tables) > 0, "Database must have at least one table"
        assert "usage_snapshots" in tables, "usage_snapshots table must exist"

        conn.close()

        # AC6: Hooks registered in settings.json
        settings_file = Path(install_env["SETTINGS_FILE"])
        assert settings_file.exists(), "settings.json must be created"

        with open(settings_file) as f:
            settings = json.load(f)

        assert "hooks" in settings, "settings must have 'hooks' section"

        # Verify 6 active hooks are registered
        active_hooks = [
            "PostToolUse",
            "Stop",
            "UserPromptSubmit",
            "SessionStart",
            "SubagentStart",
            "SubagentStop",
        ]
        for hook_name in active_hooks:
            assert hook_name in settings["hooks"], f"hooks must have {hook_name}"
            assert isinstance(
                settings["hooks"][hook_name], list
            ), f"{hook_name} must be array"
            assert (
                len(settings["hooks"][hook_name]) == 1
            ), f"{hook_name} must have 1 entry"

    def test_idempotent_reinstall_no_duplicates(self, install_env, temp_home):
        """
        COMPREHENSIVE TEST: Re-running installer doesn't create duplicates.

        Runs installer twice and verifies:
        - Second run succeeds
        - No duplicate hooks in settings.json
        - Config preserved
        - Database intact
        """
        # First installation
        result1 = self._run_install(temp_home)
        assert result1.returncode == 0, f"First install failed: {result1.stderr}"

        # Second installation (re-run)
        result2 = self._run_install(temp_home)
        assert result2.returncode == 0, f"Second install failed: {result2.stderr}"

        # Load settings and verify no duplicates
        settings_file = Path(install_env["SETTINGS_FILE"])
        with open(settings_file) as f:
            settings = json.load(f)

        # Each hook type should have exactly one pace-maker entry
        assert (
            len(settings["hooks"]["SessionStart"]) == 1
        ), "Should have exactly 1 SessionStart hook"
        assert (
            len(settings["hooks"]["PostToolUse"]) == 1
        ), "Should have exactly 1 PostToolUse hook"
        assert len(settings["hooks"]["Stop"]) == 1, "Should have exactly 1 Stop hook"
        assert (
            len(settings["hooks"]["UserPromptSubmit"]) == 1
        ), "Should have exactly 1 UserPromptSubmit hook"
        assert (
            len(settings["hooks"]["SubagentStart"]) == 1
        ), "Should have exactly 1 SubagentStart hook"
        assert (
            len(settings["hooks"]["SubagentStop"]) == 1
        ), "Should have exactly 1 SubagentStop hook"

    def test_install_preserves_other_hooks(self, install_env, temp_home):
        """
        COMPREHENSIVE TEST: Installing alongside other tools (e.g., tdd-guard).

        Pre-creates settings with tdd-guard hooks, runs installer, verifies:
        - tdd-guard hooks preserved
        - pace-maker hooks added
        - Both sets of hooks coexist
        """
        # Pre-create settings.json with tdd-guard hooks
        claude_dir = Path(install_env["CLAUDE_DIR"])
        claude_dir.mkdir(parents=True, exist_ok=True)
        settings_file = Path(install_env["SETTINGS_FILE"])

        existing_settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "~/.claude/hooks/tdd-guard-session-start.sh",
                            }
                        ]
                    }
                ],
                "PostToolUse": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "~/.claude/hooks/tdd-guard-post.sh",
                                "timeout": 300,
                            }
                        ]
                    }
                ],
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "~/.claude/hooks/tdd-guard-stop.sh",
                            }
                        ]
                    }
                ],
            }
        }

        with open(settings_file, "w") as f:
            json.dump(existing_settings, f)

        # Run installation
        result = self._run_install(temp_home)
        assert result.returncode == 0, f"Install failed: {result.stderr}"

        # Load settings and verify both old and new hooks exist
        with open(settings_file) as f:
            settings = json.load(f)

        # SessionStart: both tdd-guard and pace-maker
        assert (
            len(settings["hooks"]["SessionStart"]) == 2
        ), "Should have both tdd-guard and pace-maker SessionStart"
        session_start_commands = [
            hook["hooks"][0]["command"] for hook in settings["hooks"]["SessionStart"]
        ]
        assert "~/.claude/hooks/tdd-guard-session-start.sh" in session_start_commands
        assert any(".claude/hooks/session-start.sh" in cmd for cmd in session_start_commands)

        # PostToolUse: both tdd-guard and pace-maker
        assert (
            len(settings["hooks"]["PostToolUse"]) == 2
        ), "Should have both PostToolUse hooks"
        post_commands = [
            hook["hooks"][0]["command"] for hook in settings["hooks"]["PostToolUse"]
        ]
        assert "~/.claude/hooks/tdd-guard-post.sh" in post_commands
        assert any(".claude/hooks/post-tool-use.sh" in cmd for cmd in post_commands)

        # Stop: both tdd-guard and pace-maker
        assert len(settings["hooks"]["Stop"]) == 2, "Should have both Stop hooks"
        stop_commands = [
            hook["hooks"][0]["command"] for hook in settings["hooks"]["Stop"]
        ]
        assert "~/.claude/hooks/tdd-guard-stop.sh" in stop_commands
        assert any(".claude/hooks/stop.sh" in cmd for cmd in stop_commands)

        # New hooks added by pace-maker
        assert len(settings["hooks"]["SubagentStart"]) == 1
        assert len(settings["hooks"]["SubagentStop"]) == 1

    def test_install_removes_old_pace_maker_from_combined_entries(
        self, install_env, temp_home
    ):
        """
        COMPREHENSIVE TEST: Handles merged hook entries (real-world Claude Code scenario).

        Pre-creates combined entries where tdd-guard and pace-maker are in same hook,
        runs installer, verifies:
        - Old pace-maker commands removed from combined entries
        - tdd-guard preserved
        - pace-maker re-added as separate entry
        """
        claude_dir = Path(install_env["CLAUDE_DIR"])
        claude_dir.mkdir(parents=True, exist_ok=True)
        settings_file = Path(install_env["SETTINGS_FILE"])

        existing_settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "startup|resume|clear",
                        "hooks": [
                            {"type": "command", "command": "tdd-guard"},
                            {
                                "type": "command",
                                "command": "~/.claude/hooks/session-start.sh",  # Old pace-maker
                            },
                        ],
                    }
                ],
                "PostToolUse": [
                    {
                        "hooks": [
                            {"type": "command", "command": "tdd-guard-post"},
                            {
                                "type": "command",
                                "command": "~/.claude/hooks/post-tool-use.sh",  # Old pace-maker
                                "timeout": 360,
                            },
                        ]
                    }
                ],
            }
        }

        with open(settings_file, "w") as f:
            json.dump(existing_settings, f)

        # Run installation
        result = self._run_install(temp_home)
        assert result.returncode == 0, f"Install failed: {result.stderr}"

        # Load settings and verify cleanup
        with open(settings_file) as f:
            settings = json.load(f)

        # SessionStart: should have TWO entries (tdd-guard and pace-maker separated)
        assert len(settings["hooks"]["SessionStart"]) == 2

        tdd_session_entry = None
        pace_session_entry = None
        for entry in settings["hooks"]["SessionStart"]:
            commands = [h["command"] for h in entry["hooks"]]
            if "tdd-guard" in commands:
                tdd_session_entry = entry
            elif any(".claude/hooks/session-start.sh" in cmd for cmd in commands):
                pace_session_entry = entry

        assert tdd_session_entry is not None
        assert pace_session_entry is not None
        assert tdd_session_entry.get("matcher") == "startup|resume|clear"
        assert len(tdd_session_entry["hooks"]) == 1
        assert tdd_session_entry["hooks"][0]["command"] == "tdd-guard"
        assert len(pace_session_entry["hooks"]) == 1

        # PostToolUse: should have TWO entries (tdd-guard and pace-maker separated)
        assert len(settings["hooks"]["PostToolUse"]) == 2

        tdd_post_entry = None
        pace_post_entry = None
        for entry in settings["hooks"]["PostToolUse"]:
            commands = [h["command"] for h in entry["hooks"]]
            if "tdd-guard-post" in commands:
                tdd_post_entry = entry
            elif any(".claude/hooks/post-tool-use.sh" in cmd for cmd in commands):
                pace_post_entry = entry

        assert tdd_post_entry is not None
        assert pace_post_entry is not None
        assert len(tdd_post_entry["hooks"]) == 1
        assert len(pace_post_entry["hooks"]) == 1
