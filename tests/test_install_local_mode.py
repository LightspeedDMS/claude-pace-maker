"""
Test suite for install.sh local project installation mode.

Tests cover local installation requirements:
1. Help message displays correctly
2. Invalid directory path shows error
3. Local install creates .claude directory in project
4. Local install creates settings.json in project .claude/
5. Local install preserves existing project settings
6. Hook scripts still installed globally in ~/.claude/hooks/
7. State directory still created in ~/.claude-pace-maker/
8. Global install still works (no regression)
"""

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import pytest


class TestLocalInstallMode:
    """Test suite for local project installation mode."""

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
        return {
            "HOME": str(temp_home),
        }

    def test_help_message_displays(self):
        """Help message should display usage information."""
        result = self._run_install_with_args(["--help"])

        assert result.returncode == 0, "Help should exit successfully"
        output = result.stdout + result.stderr

        # Check for key elements in help message
        assert "Usage:" in output or "usage:" in output.lower()
        assert "install.sh" in output
        assert "global" in output.lower()
        assert "local" in output.lower()
        assert "project" in output.lower()

    def test_help_message_with_h_flag(self):
        """Help message should work with -h flag."""
        result = self._run_install_with_args(["-h"])

        assert result.returncode == 0, "Help should exit successfully"
        output = result.stdout + result.stderr

        assert "Usage:" in output or "usage:" in output.lower()

    def test_invalid_directory_shows_error(self, temp_home, install_env):
        """Invalid directory path should show clear error message."""
        nonexistent_path = "/nonexistent/invalid/path"

        result = self._run_install(temp_home, [nonexistent_path])

        assert result.returncode != 0, "Should fail with invalid directory"
        output = result.stdout + result.stderr

        assert "Error" in output or "error" in output
        assert "does not exist" in output or "not exist" in output or "not found" in output

    def test_local_install_creates_project_claude_directory(self, temp_home, test_project, install_env):
        """Local install should create .claude directory in project."""
        result = self._run_install(temp_home, [str(test_project)])

        assert result.returncode == 0, f"Local install failed: {result.stderr}"

        project_claude_dir = test_project / ".claude"
        assert project_claude_dir.exists(), "Project .claude directory must be created"
        assert project_claude_dir.is_dir(), "Project .claude must be a directory"

    def test_local_install_creates_project_settings(self, temp_home, test_project, install_env):
        """Local install should create settings.json in project .claude/."""
        result = self._run_install(temp_home, [str(test_project)])

        assert result.returncode == 0, f"Local install failed: {result.stderr}"

        project_settings = test_project / ".claude" / "settings.json"
        assert project_settings.exists(), "Project settings.json must be created"

        with open(project_settings) as f:
            settings = json.load(f)

        assert "hooks" in settings, "Project settings must have hooks section"

    def test_local_install_registers_hooks_in_project_settings(self, temp_home, test_project, install_env):
        """Local install should register hooks in project settings.json."""
        result = self._run_install(temp_home, [str(test_project)])

        assert result.returncode == 0, f"Local install failed: {result.stderr}"

        project_settings = test_project / ".claude" / "settings.json"
        with open(project_settings) as f:
            settings = json.load(f)

        # Verify all hooks are registered
        assert "Start" in settings["hooks"]
        assert "PostToolUse" in settings["hooks"]
        assert "Stop" in settings["hooks"]
        assert "UserPromptSubmit" in settings["hooks"]

        # Verify hooks point to global location
        assert "~/.claude/hooks/start.sh" in settings["hooks"]["Start"][0]["hooks"][0]["command"]
        assert "~/.claude/hooks/post-tool-use.sh" in settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
        assert "~/.claude/hooks/stop.sh" in settings["hooks"]["Stop"][0]["hooks"][0]["command"]
        assert "~/.claude/hooks/user-prompt-submit.sh" in settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]

    def test_local_install_preserves_existing_settings(self, temp_home, test_project, install_env):
        """Local install should preserve existing project settings."""
        # Create existing settings with custom configuration
        project_claude_dir = test_project / ".claude"
        project_claude_dir.mkdir()

        existing_settings = {
            "model": "claude-sonnet-4",
            "maxTokens": 8192,
            "customField": "should be preserved"
        }

        project_settings_file = project_claude_dir / "settings.json"
        with open(project_settings_file, "w") as f:
            json.dump(existing_settings, f)

        # Run local install
        result = self._run_install(temp_home, [str(test_project)])

        assert result.returncode == 0, f"Local install failed: {result.stderr}"

        # Verify existing settings preserved
        with open(project_settings_file) as f:
            settings = json.load(f)

        assert settings["model"] == "claude-sonnet-4", "Existing model setting must be preserved"
        assert settings["maxTokens"] == 8192, "Existing maxTokens setting must be preserved"
        assert settings["customField"] == "should be preserved", "Custom field must be preserved"

        # Verify hooks were added
        assert "hooks" in settings, "Hooks must be added"

    def test_local_install_creates_backup_when_merging(self, temp_home, test_project, install_env):
        """Local install should create backup when merging with existing settings."""
        # Create existing settings
        project_claude_dir = test_project / ".claude"
        project_claude_dir.mkdir()

        existing_settings = {"model": "claude-sonnet-4"}
        project_settings_file = project_claude_dir / "settings.json"
        with open(project_settings_file, "w") as f:
            json.dump(existing_settings, f)

        # Run local install
        result = self._run_install(temp_home, [str(test_project)])

        assert result.returncode == 0, f"Local install failed: {result.stderr}"

        # Verify backup was created
        backup_file = project_claude_dir / "settings.json.backup"
        assert backup_file.exists(), "Backup file must be created when merging"

        with open(backup_file) as f:
            backup = json.load(f)

        assert backup == existing_settings, "Backup must contain original settings"

    def test_local_install_hook_scripts_still_global(self, temp_home, test_project, install_env):
        """Local install should still install hook scripts in global ~/.claude/hooks/."""
        result = self._run_install(temp_home, [str(test_project)])

        assert result.returncode == 0, f"Local install failed: {result.stderr}"

        # Verify hook scripts in global location
        global_hooks_dir = temp_home / ".claude" / "hooks"
        assert global_hooks_dir.exists(), "Global hooks directory must be created"

        expected_hooks = [
            "post-tool-use.sh",
            "stop.sh",
            "user-prompt-submit.sh",
            "start.sh",
        ]

        for hook in expected_hooks:
            hook_path = global_hooks_dir / hook
            assert hook_path.exists(), f"{hook} must exist in global hooks directory"
            assert os.access(hook_path, os.X_OK), f"{hook} must be executable"

        # Verify NO hook scripts in project directory
        project_hooks_dir = test_project / ".claude" / "hooks"
        assert not project_hooks_dir.exists(), "Hook scripts should NOT be copied to project"

    def test_local_install_creates_global_state_directory(self, temp_home, test_project, install_env):
        """Local install should still create global ~/.claude-pace-maker/ state directory."""
        result = self._run_install(temp_home, [str(test_project)])

        assert result.returncode == 0, f"Local install failed: {result.stderr}"

        # Verify global state directory
        pacemaker_dir = temp_home / ".claude-pace-maker"
        assert pacemaker_dir.exists(), "Global pace-maker state directory must be created"

        # Verify config and database
        config_file = pacemaker_dir / "config.json"
        db_file = pacemaker_dir / "usage.db"

        assert config_file.exists(), "Global config.json must be created"
        assert db_file.exists(), "Global usage.db must be created"

    def test_local_install_success_message(self, temp_home, test_project, install_env):
        """Local install should display appropriate success message."""
        result = self._run_install(temp_home, [str(test_project)])

        assert result.returncode == 0, f"Local install failed: {result.stderr}"

        output = result.stdout + result.stderr

        # Check for local install indicators
        assert "local" in output.lower() or str(test_project) in output
        assert "success" in output.lower() or "complete" in output.lower()

    def test_global_install_still_works(self, temp_home, install_env):
        """Global install (no parameters) should still work as before."""
        result = self._run_install(temp_home, [])

        assert result.returncode == 0, f"Global install failed: {result.stderr}"

        # Verify global settings
        global_settings = temp_home / ".claude" / "settings.json"
        assert global_settings.exists(), "Global settings.json must be created"

        with open(global_settings) as f:
            settings = json.load(f)

        assert "hooks" in settings, "Global settings must have hooks"

    def test_local_install_with_relative_path(self, temp_home, test_project, install_env):
        """Local install should handle relative paths by converting to absolute."""
        # Change to parent directory and use relative path
        parent_dir = test_project.parent
        relative_path = test_project.name

        result = subprocess.run(
            ["/home/jsbattig/Dev/claude-pace-maker/install.sh", relative_path],
            env={**os.environ, "HOME": str(temp_home)},
            capture_output=True,
            text=True,
            cwd=str(parent_dir),
        )

        assert result.returncode == 0, f"Local install with relative path failed: {result.stderr}"

        # Verify project settings created
        project_settings = test_project / ".claude" / "settings.json"
        assert project_settings.exists(), "Project settings must be created with relative path"

    def test_local_install_idempotent(self, temp_home, test_project, install_env):
        """Local install should be idempotent - safe to run multiple times."""
        # Run install twice
        result1 = self._run_install(temp_home, [str(test_project)])
        assert result1.returncode == 0, f"First local install failed: {result1.stderr}"

        result2 = self._run_install(temp_home, [str(test_project)])
        assert result2.returncode == 0, f"Second local install failed: {result2.stderr}"

        # Verify settings still valid
        project_settings = test_project / ".claude" / "settings.json"
        with open(project_settings) as f:
            settings = json.load(f)

        assert "hooks" in settings, "Hooks must still be registered after reinstall"

    def _run_install(self, home_dir, args):
        """Helper to run install.sh with custom HOME directory and arguments."""
        install_script = Path("/home/jsbattig/Dev/claude-pace-maker/install.sh")

        result = subprocess.run(
            [str(install_script)] + args,
            env={**os.environ, "HOME": str(home_dir)},
            capture_output=True,
            text=True,
        )

        return result

    def _run_install_with_args(self, args):
        """Helper to run install.sh with arguments (no HOME override)."""
        install_script = Path("/home/jsbattig/Dev/claude-pace-maker/install.sh")

        result = subprocess.run(
            [str(install_script)] + args,
            capture_output=True,
            text=True,
        )

        return result
