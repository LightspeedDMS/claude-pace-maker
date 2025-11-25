"""
Test suite for install.sh script following TDD methodology.

Tests cover all 10 acceptance criteria:
1. Creates directory structure
2. Copies hook scripts to ~/.claude/hooks/
3. Creates ~/.claude-pace-maker/ directory
4. Generates default configuration file
5. Initializes SQLite database with schema
6. Registers hooks in ~/.claude/settings.json
7. Sets executable permissions on hook scripts
8. Idempotent - safe to re-run
9. Verifies installation success
10. Provides clear feedback to user
"""

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest


class TestInstallScript:
    """Test suite for install.sh installation script."""

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

    def test_install_script_exists(self):
        """AC1: Installation script exists at project root."""
        install_script = Path("/home/jsbattig/Dev/claude-pace-maker/install.sh")
        assert install_script.exists(), "install.sh must exist at project root"
        assert install_script.is_file(), "install.sh must be a file"

    def test_install_script_is_executable(self):
        """AC7: Installation script has executable permissions."""
        install_script = Path("/home/jsbattig/Dev/claude-pace-maker/install.sh")
        assert os.access(install_script, os.X_OK), "install.sh must be executable"

    def test_install_creates_claude_directory(self, install_env, temp_home):
        """AC1: Installation creates ~/.claude directory."""
        result = self._run_install(temp_home)
        assert result.returncode == 0, f"Install failed: {result.stderr}"

        claude_dir = Path(install_env["CLAUDE_DIR"])
        assert claude_dir.exists(), "~/.claude directory must be created"
        assert claude_dir.is_dir(), "~/.claude must be a directory"

    def test_install_creates_hooks_directory(self, install_env, temp_home):
        """AC1, AC2: Installation creates ~/.claude/hooks/ directory."""
        result = self._run_install(temp_home)
        assert result.returncode == 0, f"Install failed: {result.stderr}"

        hooks_dir = Path(install_env["HOOKS_DIR"])
        assert hooks_dir.exists(), "~/.claude/hooks directory must be created"
        assert hooks_dir.is_dir(), "~/.claude/hooks must be a directory"

    def test_install_creates_pacemaker_directory(self, install_env, temp_home):
        """AC3: Installation creates ~/.claude-pace-maker/ directory."""
        result = self._run_install(temp_home)
        assert result.returncode == 0, f"Install failed: {result.stderr}"

        pacemaker_dir = Path(install_env["PACEMAKER_DIR"])
        assert pacemaker_dir.exists(), "~/.claude-pace-maker directory must be created"
        assert pacemaker_dir.is_dir(), "~/.claude-pace-maker must be a directory"

    def test_install_copies_hook_scripts(self, install_env, temp_home):
        """AC2: Installation copies all hook scripts to ~/.claude/hooks/."""
        result = self._run_install(temp_home)
        assert result.returncode == 0, f"Install failed: {result.stderr}"

        hooks_dir = Path(install_env["HOOKS_DIR"])
        expected_hooks = [
            "post-tool-use.sh",
            "stop.sh",
            "user-prompt-submit.sh",
        ]

        for hook in expected_hooks:
            hook_path = hooks_dir / hook
            assert hook_path.exists(), f"{hook} must be copied to hooks directory"
            assert hook_path.is_file(), f"{hook} must be a file"

    def test_hook_scripts_are_executable(self, install_env, temp_home):
        """AC7: Hook scripts have executable permissions."""
        result = self._run_install(temp_home)
        assert result.returncode == 0, f"Install failed: {result.stderr}"

        hooks_dir = Path(install_env["HOOKS_DIR"])
        expected_hooks = [
            "post-tool-use.sh",
            "stop.sh",
            "user-prompt-submit.sh",
        ]

        for hook in expected_hooks:
            hook_path = hooks_dir / hook
            assert os.access(hook_path, os.X_OK), f"{hook} must be executable"

    def test_install_creates_config_file(self, install_env, temp_home):
        """AC4: Installation generates default configuration file."""
        result = self._run_install(temp_home)
        assert result.returncode == 0, f"Install failed: {result.stderr}"

        config_file = Path(install_env["PACEMAKER_DIR"]) / "config.json"
        assert config_file.exists(), "config.json must be created"
        assert config_file.is_file(), "config.json must be a file"

    def test_config_has_correct_structure(self, install_env, temp_home):
        """AC4: Configuration file has correct structure and defaults."""
        result = self._run_install(temp_home)
        assert result.returncode == 0, f"Install failed: {result.stderr}"

        config_file = Path(install_env["PACEMAKER_DIR"]) / "config.json"
        with open(config_file) as f:
            config = json.load(f)

        assert "enabled" in config, "config must have 'enabled' field"
        assert "base_delay" in config, "config must have 'base_delay' field"
        assert "max_delay" in config, "config must have 'max_delay' field"
        assert (
            "threshold_percent" in config
        ), "config must have 'threshold_percent' field"
        assert "poll_interval" in config, "config must have 'poll_interval' field"

        assert config["enabled"] is True, "enabled should default to True"
        assert config["base_delay"] == 5, "base_delay should default to 5"
        assert config["max_delay"] == 120, "max_delay should default to 120"
        assert config["threshold_percent"] == 0, "threshold_percent should default to 0"
        assert config["poll_interval"] == 60, "poll_interval should default to 60"

    def test_install_creates_database(self, install_env, temp_home):
        """AC5: Installation initializes SQLite database."""
        result = self._run_install(temp_home)
        assert result.returncode == 0, f"Install failed: {result.stderr}"

        db_file = Path(install_env["PACEMAKER_DIR"]) / "usage.db"
        assert db_file.exists(), "usage.db must be created"
        assert db_file.is_file(), "usage.db must be a file"

    def test_database_has_correct_schema(self, install_env, temp_home):
        """AC5: Database has correct schema with usage_snapshots table."""
        result = self._run_install(temp_home)
        assert result.returncode == 0, f"Install failed: {result.stderr}"

        db_file = Path(install_env["PACEMAKER_DIR"]) / "usage.db"
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()

        # Check table exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='usage_snapshots'"
        )
        assert cursor.fetchone() is not None, "usage_snapshots table must exist"

        # Check columns
        cursor.execute("PRAGMA table_info(usage_snapshots)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}

        expected_columns = {
            "timestamp": "INTEGER",
            "five_hour_util": "REAL",
            "five_hour_resets_at": "TEXT",
            "seven_day_util": "REAL",
            "seven_day_resets_at": "TEXT",
            "session_id": "TEXT",
        }

        for col_name, col_type in expected_columns.items():
            assert col_name in columns, f"Column {col_name} must exist"
            assert (
                columns[col_name] == col_type
            ), f"Column {col_name} must be {col_type}"

        conn.close()

    def test_database_has_indexes(self, install_env, temp_home):
        """AC5: Database has appropriate indexes."""
        result = self._run_install(temp_home)
        assert result.returncode == 0, f"Install failed: {result.stderr}"

        db_file = Path(install_env["PACEMAKER_DIR"]) / "usage.db"
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()

        # Check indexes exist
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='usage_snapshots'"
        )
        indexes = [row[0] for row in cursor.fetchall()]

        assert "idx_timestamp" in indexes, "idx_timestamp index must exist"
        assert "idx_session" in indexes, "idx_session index must exist"

        conn.close()

    def test_install_registers_hooks_in_settings(self, install_env, temp_home):
        """AC6: Installation registers hooks in ~/.claude/settings.json."""
        result = self._run_install(temp_home)
        assert result.returncode == 0, f"Install failed: {result.stderr}"

        settings_file = Path(install_env["SETTINGS_FILE"])
        assert settings_file.exists(), "settings.json must be created"

        with open(settings_file) as f:
            settings = json.load(f)

        assert "hooks" in settings, "settings must have 'hooks' section"
        # SessionStart is deprecated/obsolete - should be empty array
        assert (
            "SessionStart" in settings["hooks"]
        ), "hooks must have SessionStart (empty)"
        assert (
            settings["hooks"]["SessionStart"] == []
        ), "SessionStart should be empty (deprecated)"

        # Active hooks that should be registered
        assert "PostToolUse" in settings["hooks"], "hooks must have PostToolUse"
        assert "Stop" in settings["hooks"], "hooks must have Stop"
        assert (
            "UserPromptSubmit" in settings["hooks"]
        ), "hooks must have UserPromptSubmit"
        assert "SubagentStart" in settings["hooks"], "hooks must have SubagentStart"
        assert "SubagentStop" in settings["hooks"], "hooks must have SubagentStop"

        # Verify array-based format for active hooks
        assert isinstance(
            settings["hooks"]["PostToolUse"], list
        ), "PostToolUse hook must be array"
        assert isinstance(settings["hooks"]["Stop"], list), "Stop hook must be array"
        assert isinstance(
            settings["hooks"]["UserPromptSubmit"], list
        ), "UserPromptSubmit hook must be array"
        assert isinstance(
            settings["hooks"]["SubagentStart"], list
        ), "SubagentStart hook must be array"
        assert isinstance(
            settings["hooks"]["SubagentStop"], list
        ), "SubagentStop hook must be array"

        # Verify hook commands contain the expected paths (5 active hooks)
        assert (
            ".claude/hooks/post-tool-use.sh"
            in settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
        )
        assert (
            ".claude/hooks/stop.sh"
            in settings["hooks"]["Stop"][0]["hooks"][0]["command"]
        )
        assert (
            ".claude/hooks/user-prompt-submit.sh"
            in settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        )
        assert (
            ".claude/hooks/subagent-start.sh"
            in settings["hooks"]["SubagentStart"][0]["hooks"][0]["command"]
        )
        assert (
            ".claude/hooks/subagent-stop.sh"
            in settings["hooks"]["SubagentStop"][0]["hooks"][0]["command"]
        )

    def test_install_is_idempotent(self, install_env, temp_home):
        """AC8: Installation can be run multiple times safely."""
        # Run installation twice
        result1 = self._run_install(temp_home)
        assert result1.returncode == 0, f"First install failed: {result1.stderr}"

        result2 = self._run_install(temp_home)
        assert result2.returncode == 0, f"Second install failed: {result2.stderr}"

        # Verify everything still works correctly
        pacemaker_dir = Path(install_env["PACEMAKER_DIR"])
        config_file = pacemaker_dir / "config.json"
        db_file = pacemaker_dir / "usage.db"

        assert config_file.exists(), "config.json must still exist after reinstall"
        assert db_file.exists(), "usage.db must still exist after reinstall"

        # Verify hooks still registered
        settings_file = Path(install_env["SETTINGS_FILE"])
        with open(settings_file) as f:
            settings = json.load(f)

        assert "hooks" in settings, "hooks must still be registered after reinstall"

    def test_install_verifies_installation(self, install_env, temp_home):
        """AC9: Installation includes verification step."""
        result = self._run_install(temp_home)
        assert result.returncode == 0, f"Install failed: {result.stderr}"

        # Verification is part of the script output
        assert (
            "Verifying installation" in result.stdout
            or "Verifying installation" in result.stderr
        )

    def test_install_provides_clear_feedback(self, install_env, temp_home):
        """AC10: Installation provides clear user feedback."""
        result = self._run_install(temp_home)
        assert result.returncode == 0, f"Install failed: {result.stderr}"

        output = result.stdout + result.stderr

        # Check for key feedback messages
        assert "Claude Pace Maker" in output, "Must show application name"
        assert "Installation" in output, "Must indicate installation process"
        assert (
            "success" in output.lower() or "complete" in output.lower()
        ), "Must indicate success"

    def test_install_fails_with_missing_dependencies(self, temp_home, monkeypatch):
        """Installation should check for required dependencies."""
        # Simulate missing sqlite3
        monkeypatch.setenv("HOME", str(temp_home))
        monkeypatch.setenv("PATH", "/nonexistent")

        result = self._run_install(temp_home)

        # Should fail with clear error message about missing dependencies
        assert result.returncode != 0, "Should fail when dependencies missing"
        assert (
            "dependencies" in result.stderr.lower()
            or "dependencies" in result.stdout.lower()
        )

    def test_install_preserves_existing_hooks(self, install_env, temp_home):
        """Installation must preserve existing hooks from other tools (e.g., tdd-guard)."""
        # Pre-create settings.json with existing hooks from another tool
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

        # Verify existing hooks preserved
        # SessionStart: pace-maker no longer uses this hook, so only tdd-guard hook should remain
        assert (
            len(settings["hooks"]["SessionStart"]) == 1
        ), "Should have only tdd-guard SessionStart hook (pace-maker doesn't use SessionStart)"
        assert (
            len(settings["hooks"]["PostToolUse"]) == 2
        ), "Should have both tdd-guard and pace-maker PostToolUse hooks"
        assert (
            len(settings["hooks"]["Stop"]) == 2
        ), "Should have both tdd-guard and pace-maker Stop hooks"

        # Verify tdd-guard SessionStart hook still present (pace-maker doesn't use SessionStart)
        session_start_commands = [
            hook["hooks"][0]["command"] for hook in settings["hooks"]["SessionStart"]
        ]
        assert (
            "~/.claude/hooks/tdd-guard-session-start.sh" in session_start_commands
        ), "tdd-guard SessionStart hook must be preserved"

        post_commands = [
            hook["hooks"][0]["command"] for hook in settings["hooks"]["PostToolUse"]
        ]
        assert (
            "~/.claude/hooks/tdd-guard-post.sh" in post_commands
        ), "tdd-guard PostToolUse hook must be preserved"

        stop_commands = [
            hook["hooks"][0]["command"] for hook in settings["hooks"]["Stop"]
        ]
        assert (
            "~/.claude/hooks/tdd-guard-stop.sh" in stop_commands
        ), "tdd-guard Stop hook must be preserved"

        # Verify pace-maker hooks added (check for path substring since it might be full path)
        # pace-maker no longer uses SessionStart, so skip that check
        assert any(
            ".claude/hooks/post-tool-use.sh" in cmd for cmd in post_commands
        ), "pace-maker PostToolUse hook must be added"
        assert any(
            ".claude/hooks/stop.sh" in cmd for cmd in stop_commands
        ), "pace-maker Stop hook must be added"

        # Verify pace-maker's new SubagentStart and SubagentStop hooks exist
        assert (
            "SubagentStart" in settings["hooks"]
        ), "pace-maker SubagentStart hook must be added"
        assert (
            "SubagentStop" in settings["hooks"]
        ), "pace-maker SubagentStop hook must be added"
        assert (
            len(settings["hooks"]["SubagentStart"]) == 1
        ), "Should have pace-maker SubagentStart hook"
        assert (
            len(settings["hooks"]["SubagentStop"]) == 1
        ), "Should have pace-maker SubagentStop hook"

        subagent_start_commands = [
            hook["hooks"][0]["command"] for hook in settings["hooks"]["SubagentStart"]
        ]
        subagent_stop_commands = [
            hook["hooks"][0]["command"] for hook in settings["hooks"]["SubagentStop"]
        ]
        assert any(
            ".claude/hooks/subagent-start.sh" in cmd for cmd in subagent_start_commands
        ), "pace-maker SubagentStart hook must be added"
        assert any(
            ".claude/hooks/subagent-stop.sh" in cmd for cmd in subagent_stop_commands
        ), "pace-maker SubagentStop hook must be added"

    def test_install_is_idempotent_no_duplicate_hooks(self, install_env, temp_home):
        """Running installation twice must not create duplicate hooks."""
        # First installation
        result1 = self._run_install(temp_home)
        assert result1.returncode == 0, f"First install failed: {result1.stderr}"

        # Second installation
        result2 = self._run_install(temp_home)
        assert result2.returncode == 0, f"Second install failed: {result2.stderr}"

        # Load settings and verify no duplicates
        settings_file = Path(install_env["SETTINGS_FILE"])
        with open(settings_file) as f:
            settings = json.load(f)

        # Each hook type should have exactly one pace-maker hook entry
        # SessionStart is deprecated - should be empty array
        assert (
            len(settings["hooks"]["SessionStart"]) == 0
        ), "SessionStart should be empty (deprecated, no pace-maker hook)"
        assert (
            len(settings["hooks"]["PostToolUse"]) == 1
        ), "Should have exactly one PostToolUse hook after reinstall"
        assert (
            len(settings["hooks"]["Stop"]) == 1
        ), "Should have exactly one Stop hook after reinstall"
        assert (
            len(settings["hooks"]["UserPromptSubmit"]) == 1
        ), "Should have exactly one UserPromptSubmit hook after reinstall"
        assert (
            len(settings["hooks"]["SubagentStart"]) == 1
        ), "Should have exactly one SubagentStart hook after reinstall"
        assert (
            len(settings["hooks"]["SubagentStop"]) == 1
        ), "Should have exactly one SubagentStop hook after reinstall"

        # Verify it's the pace-maker hook (check for path substring since it might be full path)
        assert (
            ".claude/hooks/post-tool-use.sh"
            in settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
        )
        assert (
            ".claude/hooks/stop.sh"
            in settings["hooks"]["Stop"][0]["hooks"][0]["command"]
        )
        assert (
            ".claude/hooks/user-prompt-submit.sh"
            in settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        )
        assert (
            ".claude/hooks/subagent-start.sh"
            in settings["hooks"]["SubagentStart"][0]["hooks"][0]["command"]
        )
        assert (
            ".claude/hooks/subagent-stop.sh"
            in settings["hooks"]["SubagentStop"][0]["hooks"][0]["command"]
        )

    def test_install_handles_empty_hook_arrays(self, install_env, temp_home):
        """Installation must handle existing but empty hook arrays."""
        # Pre-create settings.json with empty hook arrays
        claude_dir = Path(install_env["CLAUDE_DIR"])
        claude_dir.mkdir(parents=True, exist_ok=True)
        settings_file = Path(install_env["SETTINGS_FILE"])

        existing_settings = {
            "hooks": {
                "SessionStart": [],
                "PostToolUse": [],
                "Stop": [],
            }
        }

        with open(settings_file, "w") as f:
            json.dump(existing_settings, f)

        # Run installation
        result = self._run_install(temp_home)
        assert result.returncode == 0, f"Install failed: {result.stderr}"

        # Load settings and verify hooks added
        with open(settings_file) as f:
            settings = json.load(f)

        # Verify pace-maker hooks added to empty arrays
        # SessionStart is deprecated - should remain empty
        assert (
            len(settings["hooks"]["SessionStart"]) == 0
        ), "SessionStart should remain empty (deprecated)"
        assert (
            len(settings["hooks"]["PostToolUse"]) == 1
        ), "Should have one PostToolUse hook"
        assert len(settings["hooks"]["Stop"]) == 1, "Should have one Stop hook"
        # Verify new SubagentStart and SubagentStop hooks were added
        assert (
            len(settings["hooks"]["SubagentStart"]) == 1
        ), "Should have one SubagentStart hook"
        assert (
            len(settings["hooks"]["SubagentStop"]) == 1
        ), "Should have one SubagentStop hook"

        assert (
            ".claude/hooks/post-tool-use.sh"
            in settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
        )
        assert (
            ".claude/hooks/stop.sh"
            in settings["hooks"]["Stop"][0]["hooks"][0]["command"]
        )
        assert (
            ".claude/hooks/subagent-start.sh"
            in settings["hooks"]["SubagentStart"][0]["hooks"][0]["command"]
        )
        assert (
            ".claude/hooks/subagent-stop.sh"
            in settings["hooks"]["SubagentStop"][0]["hooks"][0]["command"]
        )

    def test_install_handles_missing_hook_types(self, install_env, temp_home):
        """Installation must handle when some hook types don't exist yet."""
        # Pre-create settings.json with only some hook types
        claude_dir = Path(install_env["CLAUDE_DIR"])
        claude_dir.mkdir(parents=True, exist_ok=True)
        settings_file = Path(install_env["SETTINGS_FILE"])

        existing_settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {"type": "command", "command": "~/other-session-start.sh"}
                        ]
                    }
                ],
                # PostToolUse, Stop, UserPromptSubmit are missing
            }
        }

        with open(settings_file, "w") as f:
            json.dump(existing_settings, f)

        # Run installation
        result = self._run_install(temp_home)
        assert result.returncode == 0, f"Install failed: {result.stderr}"

        # Load settings and verify all hook types now exist
        with open(settings_file) as f:
            settings = json.load(f)

        # Verify SessionStart hook preserved (pace-maker doesn't use SessionStart anymore)
        assert (
            len(settings["hooks"]["SessionStart"]) == 1
        ), "Should have only existing SessionStart hook (pace-maker doesn't use SessionStart)"
        session_start_commands = [
            hook["hooks"][0]["command"] for hook in settings["hooks"]["SessionStart"]
        ]
        assert "~/other-session-start.sh" in session_start_commands

        # Verify missing hook types were created
        assert (
            "PostToolUse" in settings["hooks"]
        ), "PostToolUse hook type must be created"
        assert "Stop" in settings["hooks"], "Stop hook type must be created"
        assert (
            "UserPromptSubmit" in settings["hooks"]
        ), "UserPromptSubmit hook type must be created"
        assert (
            "SubagentStart" in settings["hooks"]
        ), "SubagentStart hook type must be created"
        assert (
            "SubagentStop" in settings["hooks"]
        ), "SubagentStop hook type must be created"

        assert len(settings["hooks"]["PostToolUse"]) == 1
        assert len(settings["hooks"]["Stop"]) == 1
        assert len(settings["hooks"]["UserPromptSubmit"]) == 1
        assert len(settings["hooks"]["SubagentStart"]) == 1
        assert len(settings["hooks"]["SubagentStop"]) == 1

    def test_install_handles_combined_hook_entries(self, install_env, temp_home):
        """Installation must handle when other hooks and pace-maker are in same entry.

        This tests the real-world scenario where Claude Code merges hooks from different
        sources (e.g., tdd-guard + pace-maker) into a single hook entry with multiple commands.
        The install script must:
        1. Remove pace-maker commands from within combined entries
        2. Preserve other commands (e.g., tdd-guard) and their matchers
        3. Add pace-maker back as a separate entry
        """
        # Pre-create settings.json with combined hooks (code-indexer scenario)
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
                                "command": "~/.claude/hooks/session-start.sh",
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
                                "command": "~/.claude/hooks/post-tool-use.sh",
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

        # Load settings and verify
        with open(settings_file) as f:
            settings = json.load(f)

        # Should have ONE SessionStart entry: tdd-guard only (pace-maker doesn't use SessionStart)
        assert (
            len(settings["hooks"]["SessionStart"]) == 1
        ), f"Should have 1 SessionStart entry (tdd-guard only, pace-maker doesn't use SessionStart), got {len(settings['hooks']['SessionStart'])}"

        # Find tdd-guard entry
        tdd_entry = None
        for entry in settings["hooks"]["SessionStart"]:
            commands = [h["command"] for h in entry["hooks"]]
            if "tdd-guard" in commands:
                tdd_entry = entry

        assert tdd_entry is not None, "tdd-guard entry must exist"

        # Verify tdd-guard entry preserved matcher and has only tdd-guard command
        assert (
            tdd_entry.get("matcher") == "startup|resume|clear"
        ), "tdd-guard entry must preserve matcher"
        assert (
            len(tdd_entry["hooks"]) == 1
        ), "tdd-guard entry should have only tdd-guard command (pace-maker session-start.sh removed)"
        assert tdd_entry["hooks"][0]["command"] == "tdd-guard"

        # Same checks for PostToolUse
        assert (
            len(settings["hooks"]["PostToolUse"]) == 2
        ), f"Should have 2 PostToolUse entries, got {len(settings['hooks']['PostToolUse'])}"

        tdd_post_entry = None
        pace_post_entry = None
        for entry in settings["hooks"]["PostToolUse"]:
            commands = [h["command"] for h in entry["hooks"]]
            if "tdd-guard-post" in commands:
                tdd_post_entry = entry
            elif any(".claude/hooks/post-tool-use.sh" in cmd for cmd in commands):
                pace_post_entry = entry

        assert tdd_post_entry is not None, "tdd-guard PostToolUse entry must exist"
        assert pace_post_entry is not None, "pace-maker PostToolUse entry must exist"
        assert (
            len(tdd_post_entry["hooks"]) == 1
        ), "tdd-guard PostToolUse entry should have only tdd-guard-post command"
        assert tdd_post_entry["hooks"][0]["command"] == "tdd-guard-post"

    def _run_install(self, home_dir):
        """Helper to run install.sh with custom HOME directory."""
        install_script = Path("/home/jsbattig/Dev/claude-pace-maker/install.sh")

        # Run from home_dir to avoid detecting pace-maker's own .claude directory
        result = subprocess.run(
            [str(install_script)],
            env={**os.environ, "HOME": str(home_dir)},
            capture_output=True,
            text=True,
            cwd=str(home_dir),
        )

        return result


class TestHookScriptsExist:
    """Test that hook scripts exist in source before installation."""

    def test_post_tool_use_hook_exists(self):
        """Hook script sources must exist for installation to copy."""
        hook_path = Path(
            "/home/jsbattig/Dev/claude-pace-maker/src/hooks/post-tool-use.sh"
        )
        assert hook_path.exists(), "post-tool-use.sh must exist in src/hooks/"

    def test_stop_hook_exists(self):
        """Hook script sources must exist for installation to copy."""
        hook_path = Path("/home/jsbattig/Dev/claude-pace-maker/src/hooks/stop.sh")
        assert hook_path.exists(), "stop.sh must exist in src/hooks/"

    def test_user_prompt_submit_hook_exists(self):
        """Hook script sources must exist for installation to copy."""
        hook_path = Path(
            "/home/jsbattig/Dev/claude-pace-maker/src/hooks/user-prompt-submit.sh"
        )
        assert hook_path.exists(), "user-prompt-submit.sh must exist in src/hooks/"


class TestHookConflictDetection:
    """Test suite for hook conflict detection between global and project settings."""

    @pytest.fixture
    def temp_home(self, tmp_path):
        """Create a temporary home directory for testing."""
        home = tmp_path / "test_home"
        home.mkdir()
        return home

    @pytest.fixture
    def test_project(self, tmp_path):
        """Create a temporary project directory for testing."""
        project = tmp_path / "test_project"
        project.mkdir()
        return project

    @pytest.fixture
    def install_env(self, temp_home, monkeypatch):
        """Set up environment for installation testing."""
        monkeypatch.setenv("HOME", str(temp_home))
        return {"HOME": str(temp_home)}

    def test_no_conflict_on_fresh_global_install(self, temp_home, install_env):
        """Fresh global install with no existing hooks should not detect conflicts."""
        result = self._run_install(temp_home, [])
        assert result.returncode == 0, f"Global install failed: {result.stderr}"

        output = result.stdout + result.stderr
        assert (
            "WARNING: Hook Conflict" not in output
        ), "Should not warn about conflicts on fresh install"
        assert "conflict detected" not in output.lower(), "Should not detect conflicts"

    def test_no_conflict_on_fresh_local_install(
        self, temp_home, test_project, install_env
    ):
        """Fresh local install with no existing hooks should not detect conflicts."""
        result = self._run_install(temp_home, [str(test_project)])
        assert result.returncode == 0, f"Local install failed: {result.stderr}"

        output = result.stdout + result.stderr
        assert (
            "WARNING: Hook Conflict" not in output
        ), "Should not warn about conflicts on fresh install"
        assert "conflict detected" not in output.lower(), "Should not detect conflicts"

    def test_conflict_detection_global_then_local(
        self, temp_home, test_project, install_env
    ):
        """Installing local after global should detect conflict and warn user."""
        # First install globally
        result1 = self._run_install(temp_home, [])
        assert result1.returncode == 0, f"Global install failed: {result1.stderr}"

        # Then try to install locally - should detect conflict
        result2 = self._run_install_with_input(temp_home, [str(test_project)], "n\n")

        output = result2.stdout + result2.stderr
        assert (
            "WARNING: Hook Conflict" in output
            or "hook conflict detected" in output.lower()
        ), "Should warn about hook conflict"
        assert (
            "FIRE TWICE" in output or "fire twice" in output.lower()
        ), "Should warn about double-firing"

        # User answered 'n', so installation should abort
        assert result2.returncode != 0, "Should exit with error when user cancels"

        # Verify project settings were NOT created
        project_settings = test_project / ".claude" / "settings.json"
        assert (
            not project_settings.exists()
        ), "Project settings should not be created when user cancels"

    def test_conflict_detection_local_then_global(
        self, temp_home, test_project, install_env
    ):
        """Installing global after local should detect conflict when run from project directory."""
        # First install locally
        result1 = self._run_install(temp_home, [str(test_project)])
        assert result1.returncode == 0, f"Local install failed: {result1.stderr}"

        # Then try to install globally FROM THE PROJECT DIRECTORY - should detect conflict
        result2 = self._run_install_with_input_and_cwd(
            temp_home, [], "n\n", cwd=str(test_project)
        )

        output = result2.stdout + result2.stderr
        assert (
            "WARNING: Hook Conflict" in output
            or "hook conflict detected" in output.lower()
        ), "Should warn about hook conflict"

        # User answered 'n', so installation should abort
        assert result2.returncode != 0, "Should exit with error when user cancels"

    def test_user_proceeds_despite_conflict(self, temp_home, test_project, install_env):
        """User can choose to proceed despite conflict warning."""
        # First install globally
        result1 = self._run_install(temp_home, [])
        assert result1.returncode == 0, f"Global install failed: {result1.stderr}"

        # Then install locally with user answering 'y' to proceed
        result2 = self._run_install_with_input(temp_home, [str(test_project)], "y\n")

        output = result2.stdout + result2.stderr
        assert (
            "WARNING: Hook Conflict" in output
            or "hook conflict detected" in output.lower()
        ), "Should still warn about conflict"

        # User answered 'y', so installation should succeed
        assert (
            result2.returncode == 0
        ), f"Should succeed when user proceeds: {result2.stderr}"

        # Verify project settings were created
        project_settings = test_project / ".claude" / "settings.json"
        assert (
            project_settings.exists()
        ), "Project settings should be created when user proceeds"

    def test_conflict_check_handles_missing_opposite_file(
        self, temp_home, test_project, install_env
    ):
        """Conflict check should handle missing opposite settings file gracefully."""
        # Install locally when no global settings exist
        result = self._run_install(temp_home, [str(test_project)])
        assert result.returncode == 0, f"Local install failed: {result.stderr}"

        output = result.stdout + result.stderr
        assert (
            "WARNING: Hook Conflict" not in output
        ), "Should not warn when opposite file missing"

    def test_conflict_check_handles_empty_hooks_in_opposite_file(
        self, temp_home, test_project, install_env
    ):
        """Conflict check should not warn if opposite file has no pace-maker hooks."""
        # Create global settings with different hooks
        global_settings_dir = temp_home / ".claude"
        global_settings_dir.mkdir(parents=True, exist_ok=True)
        global_settings_file = global_settings_dir / "settings.json"

        other_settings = {
            "hooks": {
                "Start": [
                    {"hooks": [{"type": "command", "command": "~/other-hook.sh"}]}
                ]
            }
        }

        with open(global_settings_file, "w") as f:
            json.dump(other_settings, f)

        # Install locally - should not conflict (no pace-maker hooks in global)
        result = self._run_install(temp_home, [str(test_project)])
        assert result.returncode == 0, f"Local install failed: {result.stderr}"

        output = result.stdout + result.stderr
        assert (
            "WARNING: Hook Conflict" not in output
        ), "Should not warn when opposite file has no pace-maker hooks"

    def test_conflict_warning_shows_conflicting_file_path(
        self, temp_home, test_project, install_env
    ):
        """Conflict warning should show the path of the conflicting file."""
        # Install globally first
        result1 = self._run_install(temp_home, [])
        assert result1.returncode == 0

        # Try local install - should show global settings path in warning
        result2 = self._run_install_with_input(temp_home, [str(test_project)], "n\n")

        output = result2.stdout + result2.stderr
        str(temp_home / ".claude" / "settings.json")

        # Warning should mention the conflicting file
        assert (
            ".claude/settings.json" in output or "settings.json" in output
        ), "Should show conflicting file path in warning"

    def test_conflict_warning_provides_clear_recommendation(
        self, temp_home, test_project, install_env
    ):
        """Conflict warning should provide clear resolution recommendations."""
        # Install globally first
        result1 = self._run_install(temp_home, [])
        assert result1.returncode == 0

        # Try local install
        result2 = self._run_install_with_input(temp_home, [str(test_project)], "n\n")

        output = result2.stdout + result2.stderr
        assert (
            "Recommendation" in output or "recommendation" in output.lower()
        ), "Should provide recommendations"
        assert (
            "Remove" in output or "remove" in output.lower()
        ), "Should recommend removing hooks from one location"

    def _run_install(self, home_dir, args):
        """Helper to run install.sh with custom HOME directory and arguments."""
        install_script = Path("/home/jsbattig/Dev/claude-pace-maker/install.sh")

        # Run from home_dir to avoid detecting pace-maker's own .claude directory
        result = subprocess.run(
            [str(install_script)] + args,
            env={**os.environ, "HOME": str(home_dir)},
            capture_output=True,
            text=True,
            cwd=str(home_dir),
        )

        return result

    def _run_install_with_input(self, home_dir, args, user_input):
        """Helper to run install.sh with simulated user input."""
        install_script = Path("/home/jsbattig/Dev/claude-pace-maker/install.sh")

        # Run from home_dir to avoid detecting pace-maker's own .claude directory
        result = subprocess.run(
            [str(install_script)] + args,
            env={**os.environ, "HOME": str(home_dir)},
            input=user_input,
            capture_output=True,
            text=True,
            cwd=str(home_dir),
        )

        return result

    def _run_install_with_input_and_cwd(self, home_dir, args, user_input, cwd):
        """Helper to run install.sh with simulated user input and specific CWD."""
        install_script = Path("/home/jsbattig/Dev/claude-pace-maker/install.sh")

        result = subprocess.run(
            [str(install_script)] + args,
            env={**os.environ, "HOME": str(home_dir)},
            input=user_input,
            capture_output=True,
            text=True,
            cwd=cwd,
        )

        return result
