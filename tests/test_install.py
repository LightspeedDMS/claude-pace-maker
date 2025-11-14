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
import shutil
import sqlite3
import subprocess
import tempfile
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
        assert "threshold_percent" in config, "config must have 'threshold_percent' field"
        assert "poll_interval" in config, "config must have 'poll_interval' field"

        assert config["enabled"] is True, "enabled should default to True"
        assert config["base_delay"] == 5, "base_delay should default to 5"
        assert config["max_delay"] == 120, "max_delay should default to 120"
        assert config["threshold_percent"] == 10, "threshold_percent should default to 10"
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
            assert columns[col_name] == col_type, f"Column {col_name} must be {col_type}"

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
        assert "postToolUse" in settings["hooks"], "hooks must have postToolUse"
        assert "stop" in settings["hooks"], "hooks must have stop"
        assert "userPromptSubmit" in settings["hooks"], "hooks must have userPromptSubmit"

        assert settings["hooks"]["postToolUse"] == "~/.claude/hooks/post-tool-use.sh"
        assert settings["hooks"]["stop"] == "~/.claude/hooks/stop.sh"
        assert settings["hooks"]["userPromptSubmit"] == "~/.claude/hooks/user-prompt-submit.sh"

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
        assert "Verifying installation" in result.stdout or "Verifying installation" in result.stderr

    def test_install_provides_clear_feedback(self, install_env, temp_home):
        """AC10: Installation provides clear user feedback."""
        result = self._run_install(temp_home)
        assert result.returncode == 0, f"Install failed: {result.stderr}"

        output = result.stdout + result.stderr

        # Check for key feedback messages
        assert "Claude Pace Maker" in output, "Must show application name"
        assert "Installation" in output, "Must indicate installation process"
        assert "success" in output.lower() or "complete" in output.lower(), "Must indicate success"

    def test_install_fails_with_missing_dependencies(self, temp_home, monkeypatch):
        """Installation should check for required dependencies."""
        # Simulate missing sqlite3
        monkeypatch.setenv("HOME", str(temp_home))
        monkeypatch.setenv("PATH", "/nonexistent")

        result = self._run_install(temp_home)

        # Should fail with clear error message about missing dependencies
        assert result.returncode != 0, "Should fail when dependencies missing"
        assert "dependencies" in result.stderr.lower() or "dependencies" in result.stdout.lower()

    def _run_install(self, home_dir):
        """Helper to run install.sh with custom HOME directory."""
        install_script = Path("/home/jsbattig/Dev/claude-pace-maker/install.sh")

        result = subprocess.run(
            [str(install_script)],
            env={**os.environ, "HOME": str(home_dir)},
            capture_output=True,
            text=True,
        )

        return result


class TestHookScriptsExist:
    """Test that hook scripts exist in source before installation."""

    def test_post_tool_use_hook_exists(self):
        """Hook script sources must exist for installation to copy."""
        hook_path = Path("/home/jsbattig/Dev/claude-pace-maker/src/hooks/post-tool-use.sh")
        assert hook_path.exists(), "post-tool-use.sh must exist in src/hooks/"

    def test_stop_hook_exists(self):
        """Hook script sources must exist for installation to copy."""
        hook_path = Path("/home/jsbattig/Dev/claude-pace-maker/src/hooks/stop.sh")
        assert hook_path.exists(), "stop.sh must exist in src/hooks/"

    def test_user_prompt_submit_hook_exists(self):
        """Hook script sources must exist for installation to copy."""
        hook_path = Path("/home/jsbattig/Dev/claude-pace-maker/src/hooks/user-prompt-submit.sh")
        assert hook_path.exists(), "user-prompt-submit.sh must exist in src/hooks/"
