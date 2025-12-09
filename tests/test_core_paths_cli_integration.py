#!/usr/bin/env python3
"""
Integration tests for core-paths CLI commands.

Tests all 9 acceptance criteria from Story #15:
1. List default paths when no config
2. List paths from custom config
3. Add new core path
4. Remove core path
5. Placeholder replacement in prompt
6. File in custom core path requires TDD
7. File outside core paths skips TDD
8. Error on duplicate path
9. Path normalization (trailing slash)
"""

import os
import tempfile
import yaml
import pytest

from pacemaker import user_commands, core_paths


def test_ac1_list_default_paths_when_no_config():
    """AC1: List default paths when no config file exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "nonexistent.yaml")

        # Test directly through core_paths module
        paths = core_paths.load_paths(config_path)

        assert "src/" in paths
        assert "lib/" in paths
        assert "core/" in paths
        assert "source/" in paths
        assert "libraries/" in paths
        assert "kernel/" in paths


def test_ac2_list_paths_from_custom_config():
    """AC2: List paths from custom YAML config."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "core_paths.yaml")

        # Create custom config
        custom_paths = ["custom/src/", "custom/lib/"]
        with open(config_path, "w") as f:
            yaml.safe_dump({"paths": custom_paths}, f)

        # Test directly through core_paths module
        paths = core_paths.load_paths(config_path)

        assert "custom/src/" in paths
        assert "custom/lib/" in paths
        # Default paths should NOT be present
        assert "kernel/" not in paths


def test_ac3_add_new_core_path():
    """AC3: Add new core path to config."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "core_paths.yaml")

        # Add path directly through core_paths module
        core_paths.add_path(config_path, "myapp/")

        # Verify path was added
        paths = core_paths.load_paths(config_path)
        assert "myapp/" in paths
        # Defaults should be included
        assert "src/" in paths


def test_ac4_remove_core_path():
    """AC4: Remove core path from config."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "core_paths.yaml")

        # Create config with paths
        with open(config_path, "w") as f:
            yaml.safe_dump({"paths": ["src/", "lib/", "custom/"]}, f)

        # Remove path directly through core_paths module
        core_paths.remove_path(config_path, "lib/")

        # Verify path was removed
        paths = core_paths.load_paths(config_path)
        assert "lib/" not in paths
        assert "src/" in paths
        assert "custom/" in paths


def test_ac5_placeholder_replacement_in_prompt():
    """AC5: {{core_paths}} placeholder is replaced with actual paths in TDD section."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "core_paths.yaml")

        # Create custom config
        custom_paths = ["custom/"]
        with open(config_path, "w") as f:
            yaml.safe_dump({"paths": custom_paths}, f)

        # Load paths and format
        paths = core_paths.load_paths(config_path)
        formatted = core_paths.format_paths_for_prompt(paths)

        # Verify formatted output contains custom path
        assert "custom/" in formatted
        assert formatted.startswith("  -")  # Proper indentation


def test_ac6_file_in_custom_core_path_requires_tdd():
    """AC6: File in custom core path is detected as requiring TDD."""
    custom_paths = ["myapp/core/"]

    # File in custom core path
    assert core_paths.is_core_path("myapp/core/auth.py", custom_paths) is True

    # File in nested path
    assert core_paths.is_core_path("myapp/core/utils/helper.py", custom_paths) is True


def test_ac7_file_outside_core_paths_skips_tdd():
    """AC7: File outside core paths is NOT detected as requiring TDD."""
    custom_paths = ["src/", "lib/"]

    # Files outside core paths
    assert core_paths.is_core_path("tests/test_main.py", custom_paths) is False
    assert core_paths.is_core_path("docs/readme.md", custom_paths) is False
    assert core_paths.is_core_path("scripts/deploy.sh", custom_paths) is False


def test_ac8_error_on_duplicate_path():
    """AC8: Adding duplicate path returns error."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "core_paths.yaml")

        # Create config with path
        with open(config_path, "w") as f:
            yaml.safe_dump({"paths": ["src/"]}, f)

        # Try to add duplicate directly through core_paths module
        with pytest.raises(ValueError, match="already exists"):
            core_paths.add_path(config_path, "src/")


def test_ac9_path_normalization_trailing_slash():
    """AC9: Adding path without trailing slash normalizes it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "core_paths.yaml")

        # Add path without trailing slash directly through core_paths module
        core_paths.add_path(config_path, "myapp")

        # Verify normalization in config
        paths = core_paths.load_paths(config_path)
        assert "myapp/" in paths
        assert "myapp" not in paths


def test_parse_command_core_paths_list():
    """Test command parsing for 'pace-maker core-paths list'."""
    parsed = user_commands.parse_command("pace-maker core-paths list")

    assert parsed["is_pace_maker_command"] is True
    assert parsed["command"] == "core-paths"
    assert parsed["subcommand"] == "list"


def test_parse_command_core_paths_add():
    """Test command parsing for 'pace-maker core-paths add PATH'."""
    parsed = user_commands.parse_command("pace-maker core-paths add custom/")

    assert parsed["is_pace_maker_command"] is True
    assert parsed["command"] == "core-paths"
    assert parsed["subcommand"] == "add custom/"


def test_parse_command_core_paths_remove():
    """Test command parsing for 'pace-maker core-paths remove PATH'."""
    parsed = user_commands.parse_command("pace-maker core-paths remove lib/")

    assert parsed["is_pace_maker_command"] is True
    assert parsed["command"] == "core-paths"
    assert parsed["subcommand"] == "remove lib/"


def test_error_removing_nonexistent_path():
    """Test error handling when removing non-existent path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "core_paths.yaml")

        # Create config
        with open(config_path, "w") as f:
            yaml.safe_dump({"paths": ["src/"]}, f)

        # Try to remove non-existent path directly through core_paths module
        with pytest.raises(ValueError, match="not found"):
            core_paths.remove_path(config_path, "nonexistent/")


def test_add_path_to_existing_config():
    """Test adding path to existing config preserves other paths."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "core_paths.yaml")

        # Create initial config
        with open(config_path, "w") as f:
            yaml.safe_dump({"paths": ["src/", "lib/"]}, f)

        # Add new path directly through core_paths module
        core_paths.add_path(config_path, "custom/")

        # Verify all paths present
        paths = core_paths.load_paths(config_path)
        assert len(paths) == 3
        assert "src/" in paths
        assert "lib/" in paths
        assert "custom/" in paths
