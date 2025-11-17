"""
End-to-end integration tests for installation process.

These tests verify the complete installation workflow with ZERO MOCKING.
Tests use real file system, real database, and real processes.
"""

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest


class TestInstallationE2E:
    """End-to-end tests for complete installation workflow with no mocking."""

    @pytest.fixture
    def isolated_home(self, tmp_path, monkeypatch):
        """Create an isolated home directory for E2E testing."""
        home = tmp_path / "test_home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        return home

    def test_complete_installation_workflow(self, isolated_home):
        """
        E2E Test: Complete installation from start to finish.

        This test verifies the entire installation process:
        1. Runs install.sh
        2. Verifies all directories created
        3. Verifies all files copied
        4. Verifies database schema
        5. Verifies hooks registered
        6. Verifies permissions set correctly
        """
        install_script = Path("/home/jsbattig/Dev/claude-pace-maker/install.sh")

        # Run installation
        result = subprocess.run(
            [str(install_script)],
            env={**os.environ, "HOME": str(isolated_home)},
            capture_output=True,
            text=True,
        )

        # Verify installation succeeded
        assert (
            result.returncode == 0
        ), f"Installation failed: {result.stderr}\n{result.stdout}"

        # Verify directory structure
        claude_dir = isolated_home / ".claude"
        hooks_dir = claude_dir / "hooks"
        pacemaker_dir = isolated_home / ".claude-pace-maker"

        assert claude_dir.exists() and claude_dir.is_dir()
        assert hooks_dir.exists() and hooks_dir.is_dir()
        assert pacemaker_dir.exists() and pacemaker_dir.is_dir()

        # Verify hook scripts copied and executable
        for hook in ["post-tool-use.sh", "stop.sh", "user-prompt-submit.sh"]:
            hook_path = hooks_dir / hook
            assert hook_path.exists() and hook_path.is_file()
            assert os.access(hook_path, os.X_OK), f"{hook} must be executable"

        # Verify configuration file
        config_file = pacemaker_dir / "config.json"
        assert config_file.exists() and config_file.is_file()

        with open(config_file) as f:
            config = json.load(f)

        assert config["enabled"] is True
        assert config["base_delay"] == 5
        assert config["max_delay"] == 120
        assert config["threshold_percent"] == 0
        assert config["poll_interval"] == 60

        # Verify database created with correct schema
        db_file = pacemaker_dir / "usage.db"
        assert db_file.exists() and db_file.is_file()

        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()

        # Check table exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='usage_snapshots'"
        )
        assert cursor.fetchone() is not None

        # Check columns
        cursor.execute("PRAGMA table_info(usage_snapshots)")
        columns = {row[1] for row in cursor.fetchall()}
        expected_columns = {
            "timestamp",
            "five_hour_util",
            "five_hour_resets_at",
            "seven_day_util",
            "seven_day_resets_at",
            "session_id",
        }
        assert columns == expected_columns

        # Check indexes
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='usage_snapshots'"
        )
        indexes = {row[0] for row in cursor.fetchall()}
        assert "idx_timestamp" in indexes
        assert "idx_session" in indexes

        conn.close()

        # Verify hooks registered in settings.json
        settings_file = claude_dir / "settings.json"
        assert settings_file.exists() and settings_file.is_file()

        with open(settings_file) as f:
            settings = json.load(f)

        assert "hooks" in settings
        # Verify new array-based format with PascalCase names
        assert (
            ".claude/hooks/session-start.sh"
            in settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        ), f"SessionStart hook not found: {settings['hooks'].get('SessionStart')}"
        assert (
            ".claude/hooks/post-tool-use.sh"
            in settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
        ), f"PostToolUse hook not found: {settings['hooks'].get('PostToolUse')}"
        assert (
            ".claude/hooks/stop.sh"
            in settings["hooks"]["Stop"][0]["hooks"][0]["command"]
        ), f"Stop hook not found: {settings['hooks'].get('Stop')}"
        assert (
            ".claude/hooks/user-prompt-submit.sh"
            in settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        ), f"UserPromptSubmit hook not found: {settings['hooks'].get('UserPromptSubmit')}"

    def test_idempotent_reinstallation(self, isolated_home):
        """
        E2E Test: Installation can be run multiple times safely.

        Verifies idempotency by:
        1. Running installation twice
        2. Verifying second run doesn't break anything
        3. Verifying configuration preserved
        4. Verifying database not corrupted
        """
        install_script = Path("/home/jsbattig/Dev/claude-pace-maker/install.sh")

        # First installation
        result1 = subprocess.run(
            [str(install_script)],
            env={**os.environ, "HOME": str(isolated_home)},
            capture_output=True,
            text=True,
        )
        assert result1.returncode == 0, f"First install failed: {result1.stderr}"

        # Modify config to verify it's preserved
        config_file = isolated_home / ".claude-pace-maker" / "config.json"
        with open(config_file) as f:
            config = json.load(f)

        config["enabled"] = False
        config["custom_field"] = "test_value"

        with open(config_file, "w") as f:
            json.dump(config, f)

        # Add data to database
        db_file = isolated_home / ".claude-pace-maker" / "usage.db"
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO usage_snapshots VALUES (?, ?, ?, ?, ?, ?)",
            (
                1234567890,
                0.5,
                "2025-11-13T10:00:00Z",
                0.3,
                "2025-11-20T10:00:00Z",
                "test-session",
            ),
        )
        conn.commit()
        conn.close()

        # Second installation (should be idempotent)
        result2 = subprocess.run(
            [str(install_script)],
            env={**os.environ, "HOME": str(isolated_home)},
            capture_output=True,
            text=True,
        )
        assert result2.returncode == 0, f"Second install failed: {result2.stderr}"

        # Verify config preserved
        with open(config_file) as f:
            config_after = json.load(f)

        assert config_after["enabled"] is False, "Config should be preserved"
        assert (
            config_after["custom_field"] == "test_value"
        ), "Custom config should be preserved"

        # Verify database data preserved
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM usage_snapshots WHERE timestamp = 1234567890"
        )
        count = cursor.fetchone()[0]
        assert count == 1, "Database data should be preserved"
        conn.close()

    def test_installation_with_existing_settings(self, isolated_home):
        """
        E2E Test: Installation merges with existing settings.json.

        Verifies that:
        1. Existing settings are preserved
        2. Hooks are added/updated correctly
        3. No data loss occurs
        """
        install_script = Path("/home/jsbattig/Dev/claude-pace-maker/install.sh")

        # Pre-create settings.json with existing content
        claude_dir = isolated_home / ".claude"
        claude_dir.mkdir(parents=True)
        settings_file = claude_dir / "settings.json"

        existing_settings = {
            "user": "test_user",
            "theme": "dark",
            "hooks": {"preExecution": "~/my-hook.sh"},
        }

        with open(settings_file, "w") as f:
            json.dump(existing_settings, f)

        # Run installation
        result = subprocess.run(
            [str(install_script)],
            env={**os.environ, "HOME": str(isolated_home)},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Installation failed: {result.stderr}"

        # Verify existing settings preserved and hooks added
        with open(settings_file) as f:
            settings = json.load(f)

        assert settings["user"] == "test_user", "Existing settings should be preserved"
        assert settings["theme"] == "dark", "Existing settings should be preserved"
        assert (
            settings["hooks"]["preExecution"] == "~/my-hook.sh"
        ), "Existing hooks should be preserved"
        # Verify new array-based format with PascalCase names
        assert (
            ".claude/hooks/session-start.sh"
            in settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        ), f"SessionStart hook not found: {settings['hooks'].get('SessionStart')}"
        assert (
            ".claude/hooks/post-tool-use.sh"
            in settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
        ), f"PostToolUse hook not found: {settings['hooks'].get('PostToolUse')}"
        assert (
            ".claude/hooks/stop.sh"
            in settings["hooks"]["Stop"][0]["hooks"][0]["command"]
        ), f"Stop hook not found: {settings['hooks'].get('Stop')}"
        assert (
            ".claude/hooks/user-prompt-submit.sh"
            in settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        ), f"UserPromptSubmit hook not found: {settings['hooks'].get('UserPromptSubmit')}"

    def test_hook_scripts_functionality(self, isolated_home):
        """
        E2E Test: Installed hook scripts are functional.

        Verifies that:
        1. Hook scripts can be executed
        2. Scripts have correct shebang and are executable
        3. Scripts contain expected functionality
        """
        install_script = Path("/home/jsbattig/Dev/claude-pace-maker/install.sh")

        # Run installation
        result = subprocess.run(
            [str(install_script)],
            env={**os.environ, "HOME": str(isolated_home)},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

        hooks_dir = isolated_home / ".claude" / "hooks"

        # Test each hook script is properly installed
        for hook in ["post-tool-use.sh", "stop.sh", "user-prompt-submit.sh"]:
            hook_path = hooks_dir / hook

            # Verify executable
            assert os.access(hook_path, os.X_OK), f"{hook} must be executable"

            # Verify has bash shebang
            with open(hook_path) as f:
                first_line = f.readline()
                assert first_line.startswith(
                    "#!/bin/bash"
                ), f"{hook} must have bash shebang"

            # Verify contains pacemaker hook invocation
            with open(hook_path) as f:
                content = f.read()
                assert (
                    "pacemaker.hook" in content
                ), f"{hook} must invoke pacemaker hook module"
                assert (
                    "PACEMAKER_DIR" in content
                ), f"{hook} must reference PACEMAKER_DIR"
                assert "CONFIG_FILE" in content, f"{hook} must check config file"

    def test_database_is_writable_after_install(self, isolated_home):
        """
        E2E Test: Database is writable and functional after installation.

        Verifies that:
        1. Database can accept writes
        2. Data can be queried back
        3. Indexes work correctly
        """
        install_script = Path("/home/jsbattig/Dev/claude-pace-maker/install.sh")

        # Run installation
        result = subprocess.run(
            [str(install_script)],
            env={**os.environ, "HOME": str(isolated_home)},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

        # Write test data
        db_file = isolated_home / ".claude-pace-maker" / "usage.db"
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()

        test_data = [
            (
                1234567890,
                0.5,
                "2025-11-13T10:00:00Z",
                0.3,
                "2025-11-20T10:00:00Z",
                "session-1",
            ),
            (
                1234567900,
                0.6,
                "2025-11-13T10:00:00Z",
                0.4,
                "2025-11-20T10:00:00Z",
                "session-1",
            ),
            (
                1234567910,
                0.7,
                "2025-11-13T10:00:00Z",
                0.5,
                "2025-11-20T10:00:00Z",
                "session-2",
            ),
        ]

        for row in test_data:
            cursor.execute(
                "INSERT INTO usage_snapshots VALUES (?, ?, ?, ?, ?, ?)",
                row,
            )

        conn.commit()

        # Query back using index
        cursor.execute(
            "SELECT COUNT(*) FROM usage_snapshots WHERE session_id = 'session-1'"
        )
        count = cursor.fetchone()[0]
        assert count == 2

        cursor.execute("SELECT * FROM usage_snapshots ORDER BY timestamp")
        rows = cursor.fetchall()
        assert len(rows) == 3

        conn.close()

    def test_installation_preserves_existing_hooks_from_other_tools(
        self, isolated_home
    ):
        """
        E2E Test: Installation preserves hooks from other tools (e.g., tdd-guard).

        Verifies that:
        1. Existing hooks from other tools are preserved
        2. Pace-maker hooks are appended to existing hooks
        3. Multiple tools can coexist
        """
        install_script = Path("/home/jsbattig/Dev/claude-pace-maker/install.sh")

        # Pre-create settings.json with hooks from another tool (tdd-guard)
        claude_dir = isolated_home / ".claude"
        claude_dir.mkdir(parents=True)
        settings_file = claude_dir / "settings.json"

        existing_settings = {
            "user": "test_user",
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
            },
        }

        with open(settings_file, "w") as f:
            json.dump(existing_settings, f, indent=2)

        # Run installation
        result = subprocess.run(
            [str(install_script)],
            env={**os.environ, "HOME": str(isolated_home)},
            capture_output=True,
            text=True,
        )
        assert (
            result.returncode == 0
        ), f"Installation failed: {result.stderr}\n{result.stdout}"

        # Verify hooks from both tools are present
        with open(settings_file) as f:
            settings = json.load(f)

        # Verify user settings preserved
        assert settings["user"] == "test_user"

        # Verify both tdd-guard and pace-maker hooks exist
        assert (
            len(settings["hooks"]["SessionStart"]) == 2
        ), "Should have hooks from both tools"
        assert (
            len(settings["hooks"]["PostToolUse"]) == 2
        ), "Should have hooks from both tools"
        assert len(settings["hooks"]["Stop"]) == 2, "Should have hooks from both tools"

        # Extract all commands for verification
        session_start_commands = [
            hook["hooks"][0]["command"] for hook in settings["hooks"]["SessionStart"]
        ]
        post_commands = [
            hook["hooks"][0]["command"] for hook in settings["hooks"]["PostToolUse"]
        ]
        stop_commands = [
            hook["hooks"][0]["command"] for hook in settings["hooks"]["Stop"]
        ]

        # Verify tdd-guard hooks preserved
        assert "~/.claude/hooks/tdd-guard-session-start.sh" in session_start_commands
        assert "~/.claude/hooks/tdd-guard-post.sh" in post_commands
        assert "~/.claude/hooks/tdd-guard-stop.sh" in stop_commands

        # Verify pace-maker hooks added (check for path substring since it might be full path)
        assert any(
            ".claude/hooks/session-start.sh" in cmd for cmd in session_start_commands
        )
        assert any(".claude/hooks/post-tool-use.sh" in cmd for cmd in post_commands)
        assert any(".claude/hooks/stop.sh" in cmd for cmd in stop_commands)

    def test_idempotent_installation_no_duplicate_hooks(self, isolated_home):
        """
        E2E Test: Running installation multiple times doesn't create duplicate hooks.

        Verifies that:
        1. Running install twice doesn't duplicate pace-maker hooks
        2. Idempotent behavior is maintained
        3. Hook configuration remains clean
        """
        install_script = Path("/home/jsbattig/Dev/claude-pace-maker/install.sh")

        # First installation
        result1 = subprocess.run(
            [str(install_script)],
            env={**os.environ, "HOME": str(isolated_home)},
            capture_output=True,
            text=True,
        )
        assert result1.returncode == 0, f"First install failed: {result1.stderr}"

        # Second installation
        result2 = subprocess.run(
            [str(install_script)],
            env={**os.environ, "HOME": str(isolated_home)},
            capture_output=True,
            text=True,
        )
        assert result2.returncode == 0, f"Second install failed: {result2.stderr}"

        # Third installation (just to be thorough)
        result3 = subprocess.run(
            [str(install_script)],
            env={**os.environ, "HOME": str(isolated_home)},
            capture_output=True,
            text=True,
        )
        assert result3.returncode == 0, f"Third install failed: {result3.stderr}"

        # Verify no duplicate hooks
        settings_file = isolated_home / ".claude" / "settings.json"
        with open(settings_file) as f:
            settings = json.load(f)

        # Should have exactly one hook per type (no duplicates)
        assert (
            len(settings["hooks"]["SessionStart"]) == 1
        ), "Should have exactly one SessionStart hook"
        assert (
            len(settings["hooks"]["PostToolUse"]) == 1
        ), "Should have exactly one PostToolUse hook"
        assert len(settings["hooks"]["Stop"]) == 1, "Should have exactly one Stop hook"
        assert (
            len(settings["hooks"]["UserPromptSubmit"]) == 1
        ), "Should have exactly one UserPromptSubmit hook"

        # Verify they're the correct pace-maker hooks
        assert (
            ".claude/hooks/session-start.sh"
            in settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        ), f"SessionStart hook not found: {settings['hooks'].get('SessionStart')}"
        assert (
            ".claude/hooks/post-tool-use.sh"
            in settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
        ), f"PostToolUse hook not found: {settings['hooks'].get('PostToolUse')}"
        assert (
            ".claude/hooks/stop.sh"
            in settings["hooks"]["Stop"][0]["hooks"][0]["command"]
        ), f"Stop hook not found: {settings['hooks'].get('Stop')}"
        assert (
            ".claude/hooks/user-prompt-submit.sh"
            in settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        ), f"UserPromptSubmit hook not found: {settings['hooks'].get('UserPromptSubmit')}"
