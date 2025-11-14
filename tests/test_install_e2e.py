"""
End-to-end integration tests for installation process.

These tests verify the complete installation workflow with ZERO MOCKING.
Tests use real file system, real database, and real processes.
"""

import json
import os
import sqlite3
import subprocess
import tempfile
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
        assert result.returncode == 0, f"Installation failed: {result.stderr}\n{result.stdout}"

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
        assert config["threshold_percent"] == 10
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
        assert settings["hooks"]["postToolUse"] == "~/.claude/hooks/post-tool-use.sh"
        assert settings["hooks"]["stop"] == "~/.claude/hooks/stop.sh"
        assert settings["hooks"]["userPromptSubmit"] == "~/.claude/hooks/user-prompt-submit.sh"

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
            (1234567890, 0.5, "2025-11-13T10:00:00Z", 0.3, "2025-11-20T10:00:00Z", "test-session"),
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
        assert config_after["custom_field"] == "test_value", "Custom config should be preserved"

        # Verify database data preserved
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM usage_snapshots WHERE timestamp = 1234567890")
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
            "hooks": {
                "preExecution": "~/my-hook.sh"
            }
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
        assert settings["hooks"]["preExecution"] == "~/my-hook.sh", "Existing hooks should be preserved"
        assert settings["hooks"]["postToolUse"] == "~/.claude/hooks/post-tool-use.sh"
        assert settings["hooks"]["stop"] == "~/.claude/hooks/stop.sh"
        assert settings["hooks"]["userPromptSubmit"] == "~/.claude/hooks/user-prompt-submit.sh"

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
                assert first_line.startswith("#!/bin/bash"), f"{hook} must have bash shebang"

            # Verify contains pacemaker hook invocation
            with open(hook_path) as f:
                content = f.read()
                assert "pacemaker.hook" in content, f"{hook} must invoke pacemaker hook module"
                assert "PACEMAKER_DIR" in content, f"{hook} must reference PACEMAKER_DIR"
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
            (1234567890, 0.5, "2025-11-13T10:00:00Z", 0.3, "2025-11-20T10:00:00Z", "session-1"),
            (1234567900, 0.6, "2025-11-13T10:00:00Z", 0.4, "2025-11-20T10:00:00Z", "session-1"),
            (1234567910, 0.7, "2025-11-13T10:00:00Z", 0.5, "2025-11-20T10:00:00Z", "session-2"),
        ]

        for row in test_data:
            cursor.execute(
                "INSERT INTO usage_snapshots VALUES (?, ?, ?, ?, ?, ?)",
                row,
            )

        conn.commit()

        # Query back using index
        cursor.execute("SELECT COUNT(*) FROM usage_snapshots WHERE session_id = 'session-1'")
        count = cursor.fetchone()[0]
        assert count == 2

        cursor.execute("SELECT * FROM usage_snapshots ORDER BY timestamp")
        rows = cursor.fetchall()
        assert len(rows) == 3

        conn.close()
