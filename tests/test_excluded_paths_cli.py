#!/usr/bin/env python3
"""
CLI integration tests for excluded paths feature.

Tests the following commands:
1. 'pace-maker excluded-paths add <path>' - Add exclusion
2. 'pace-maker excluded-paths remove <path>' - Remove exclusion
3. 'pace-maker excluded-paths list' - List all exclusions
4. Help text documents excluded-paths commands
"""

from unittest.mock import patch
from pacemaker import user_commands
from pacemaker import excluded_paths


class TestExcludedPathsCLI:
    """Test CLI commands for excluded paths management."""

    def test_parse_excluded_paths_list_command(self):
        """'pace-maker excluded-paths list' should be recognized."""
        result = user_commands.handle_user_prompt(
            "pace-maker excluded-paths list", "/tmp/config.json", "/tmp/db.sqlite"
        )
        assert result["intercepted"] is True
        assert "excluded paths" in result["output"].lower()

    def test_excluded_paths_add_new_path(self, tmp_path):
        """'pace-maker excluded-paths add <path>' should add new path successfully."""
        config_path = tmp_path / "excluded_paths.yaml"

        with patch("pacemaker.constants.DEFAULT_EXCLUDED_PATHS_PATH", str(config_path)):
            result = user_commands.handle_user_prompt(
                "pace-maker excluded-paths add .custom/",
                "/tmp/config.json",
                "/tmp/db.sqlite",
            )

        assert result["intercepted"] is True
        assert "added successfully" in result["output"].lower()

    def test_excluded_paths_remove_existing_path(self, tmp_path):
        """'pace-maker excluded-paths remove <path>' should remove existing path."""
        config_path = tmp_path / "excluded_paths.yaml"

        # Add path first so removal is deterministic
        excluded_paths.add_exclusion(str(config_path), ".custom/")

        with patch("pacemaker.constants.DEFAULT_EXCLUDED_PATHS_PATH", str(config_path)):
            result = user_commands.handle_user_prompt(
                "pace-maker excluded-paths remove .custom/",
                "/tmp/config.json",
                "/tmp/db.sqlite",
            )

        assert result["intercepted"] is True
        assert "removed successfully" in result["output"].lower()

    def test_excluded_paths_list_shows_defaults(self):
        """'excluded-paths list' should display default exclusions."""
        result = user_commands.handle_user_prompt(
            "pace-maker excluded-paths list", "/tmp/config.json", "/tmp/db.sqlite"
        )

        assert result["intercepted"] is True
        output = result["output"]

        # Should contain some default paths
        assert ".tmp/" in output
        assert "tests/" in output
        assert "test/" in output

    def test_excluded_paths_add_creates_config(self, tmp_path):
        """'excluded-paths add' should create config file if missing."""
        config_path = tmp_path / "excluded_paths.yaml"

        # Config doesn't exist yet
        assert not config_path.exists()

        with patch("pacemaker.constants.DEFAULT_EXCLUDED_PATHS_PATH", str(config_path)):
            result = user_commands.handle_user_prompt(
                "pace-maker excluded-paths add .generated/",
                "/tmp/config.json",
                "/tmp/db.sqlite",
            )

        assert result["intercepted"] is True

        # Config must exist after add operation
        assert config_path.exists(), "Config file should be created by add command"
        exclusions = excluded_paths.load_exclusions(str(config_path))
        assert ".generated/" in exclusions

    def test_excluded_paths_add_normalizes_path(self, tmp_path):
        """'excluded-paths add' should normalize path with trailing slash."""
        config_path = tmp_path / "excluded_paths.yaml"

        with patch("pacemaker.constants.DEFAULT_EXCLUDED_PATHS_PATH", str(config_path)):
            # Add path without trailing slash
            result = user_commands.handle_user_prompt(
                "pace-maker excluded-paths add .custom",
                "/tmp/config.json",
                "/tmp/db.sqlite",
            )

        assert result["intercepted"] is True

        # Config must exist and path must be normalized
        assert config_path.exists(), "Config file should be created"
        exclusions = excluded_paths.load_exclusions(str(config_path))
        # Should be normalized to have trailing slash
        assert ".custom/" in exclusions

    def test_excluded_paths_add_duplicate_error(self, tmp_path):
        """'excluded-paths add' should error if path already exists."""
        config_path = tmp_path / "excluded_paths.yaml"

        with patch("pacemaker.constants.DEFAULT_EXCLUDED_PATHS_PATH", str(config_path)):
            # Add path first time
            user_commands.handle_user_prompt(
                "pace-maker excluded-paths add .custom/",
                "/tmp/config.json",
                "/tmp/db.sqlite",
            )

            # Try to add same path again
            result2 = user_commands.handle_user_prompt(
                "pace-maker excluded-paths add .custom/",
                "/tmp/config.json",
                "/tmp/db.sqlite",
            )

        assert result2["intercepted"] is True
        # Should indicate path already exists
        assert "already exists" in result2["output"].lower()

    def test_excluded_paths_remove_success(self, tmp_path):
        """'excluded-paths remove' should remove existing path."""
        config_path = tmp_path / "excluded_paths.yaml"

        # Add a path first
        excluded_paths.add_exclusion(str(config_path), ".custom/")

        with patch("pacemaker.constants.DEFAULT_EXCLUDED_PATHS_PATH", str(config_path)):
            result = user_commands.handle_user_prompt(
                "pace-maker excluded-paths remove .custom/",
                "/tmp/config.json",
                "/tmp/db.sqlite",
            )

        assert result["intercepted"] is True
        assert "removed successfully" in result["output"].lower()

        # Verify path was removed
        exclusions = excluded_paths.load_exclusions(str(config_path))
        assert ".custom/" not in exclusions

    def test_excluded_paths_remove_not_found_error(self, tmp_path):
        """'excluded-paths remove' should error if path not found."""
        config_path = tmp_path / "excluded_paths.yaml"

        with patch("pacemaker.constants.DEFAULT_EXCLUDED_PATHS_PATH", str(config_path)):
            result = user_commands.handle_user_prompt(
                "pace-maker excluded-paths remove .nonexistent/",
                "/tmp/config.json",
                "/tmp/db.sqlite",
            )

        assert result["intercepted"] is True
        # Should indicate path not found
        assert "not found" in result["output"].lower()

    def test_excluded_paths_remove_from_defaults(self, tmp_path):
        """'excluded-paths remove' on default path should create config."""
        config_path = tmp_path / "excluded_paths.yaml"

        # Config doesn't exist - only defaults active
        assert not config_path.exists()

        with patch("pacemaker.constants.DEFAULT_EXCLUDED_PATHS_PATH", str(config_path)):
            result = user_commands.handle_user_prompt(
                "pace-maker excluded-paths remove .tmp/",
                "/tmp/config.json",
                "/tmp/db.sqlite",
            )

        assert result["intercepted"] is True

        # Config must be created with defaults minus removed path
        assert (
            config_path.exists()
        ), "Config should be created when removing from defaults"
        exclusions = excluded_paths.load_exclusions(str(config_path))
        assert ".tmp/" not in exclusions
        assert "tests/" in exclusions  # Other defaults remain

    def test_help_documents_excluded_paths_commands(self):
        """'pace-maker help' should document excluded-paths commands."""
        result = user_commands.handle_user_prompt(
            "pace-maker help", "/tmp/config.json", "/tmp/db.sqlite"
        )

        assert result["intercepted"] is True
        # Help text uses "excluded-paths" format
        assert "excluded-paths" in result["output"].lower()

    def test_excluded_paths_invalid_subcommand(self):
        """Invalid excluded-paths subcommand should not be intercepted (doesn't match pattern)."""
        result = user_commands.handle_user_prompt(
            "pace-maker excluded-paths invalid",
            "/tmp/config.json",
            "/tmp/db.sqlite",
        )

        # Not intercepted - doesn't match any regex pattern
        assert result["intercepted"] is False

    def test_excluded_paths_add_missing_path_argument(self):
        """'excluded-paths add' without path argument should not be intercepted (doesn't match pattern)."""
        result = user_commands.handle_user_prompt(
            "pace-maker excluded-paths add", "/tmp/config.json", "/tmp/db.sqlite"
        )

        # Not intercepted - regex requires a path argument
        assert result["intercepted"] is False

    def test_excluded_paths_remove_missing_path_argument(self):
        """'excluded-paths remove' without path argument should not be intercepted (doesn't match pattern)."""
        result = user_commands.handle_user_prompt(
            "pace-maker excluded-paths remove", "/tmp/config.json", "/tmp/db.sqlite"
        )

        # Not intercepted - regex requires a path argument
        assert result["intercepted"] is False

    def test_excluded_paths_no_subcommand(self):
        """'excluded-paths' without subcommand should not be intercepted (doesn't match pattern)."""
        result = user_commands.handle_user_prompt(
            "pace-maker excluded-paths", "/tmp/config.json", "/tmp/db.sqlite"
        )

        # Should not be intercepted (doesn't match any pattern)
        assert result["intercepted"] is False
