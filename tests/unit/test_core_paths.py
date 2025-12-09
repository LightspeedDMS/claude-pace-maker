#!/usr/bin/env python3
"""
Unit tests for core_paths module.

Tests cover:
- Loading default core paths when no config exists
- Loading paths from custom YAML config
- Adding new core paths
- Removing core paths
- Path matching against file paths
- Path normalization (trailing slashes)
- Duplicate path detection
"""

import os
import tempfile
import pytest
import yaml

from pacemaker import core_paths


def test_get_default_paths_returns_expected_list():
    """Test that default paths include all expected core directories."""
    defaults = core_paths.get_default_paths()

    assert isinstance(defaults, list)
    assert "src/" in defaults
    assert "lib/" in defaults
    assert "core/" in defaults
    assert "source/" in defaults
    assert "libraries/" in defaults
    assert "kernel/" in defaults
    assert len(defaults) == 6


def test_load_paths_with_missing_config_returns_defaults():
    """Test that missing config file returns default paths."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "nonexistent.yaml")
        paths = core_paths.load_paths(config_path)

        assert paths == core_paths.get_default_paths()


def test_load_paths_with_custom_config_returns_custom_paths():
    """Test loading paths from custom YAML config."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "core_paths.yaml")

        # Write custom config
        custom_paths = ["custom/src/", "custom/lib/"]
        with open(config_path, "w") as f:
            yaml.safe_dump({"paths": custom_paths}, f)

        # Load and verify
        paths = core_paths.load_paths(config_path)
        assert paths == custom_paths


def test_load_paths_with_empty_config_returns_defaults():
    """Test that empty config file returns defaults."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "core_paths.yaml")

        # Write empty file
        with open(config_path, "w") as f:
            f.write("")

        paths = core_paths.load_paths(config_path)
        assert paths == core_paths.get_default_paths()


def test_load_paths_with_missing_paths_key_returns_defaults():
    """Test that config missing 'paths' key returns defaults."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "core_paths.yaml")

        # Write config without 'paths' key
        with open(config_path, "w") as f:
            yaml.safe_dump({"other_key": "value"}, f)

        paths = core_paths.load_paths(config_path)
        assert paths == core_paths.get_default_paths()


def test_load_paths_with_empty_paths_list_returns_defaults():
    """Test that empty paths list returns defaults."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "core_paths.yaml")

        # Write config with empty paths list
        with open(config_path, "w") as f:
            yaml.safe_dump({"paths": []}, f)

        paths = core_paths.load_paths(config_path)
        assert paths == core_paths.get_default_paths()


def test_add_path_creates_config_with_defaults_if_missing():
    """Test adding path when config doesn't exist creates file with defaults + new path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "core_paths.yaml")

        # Add new path
        core_paths.add_path(config_path, "custom/")

        # Verify file was created and contains defaults + new path
        paths = core_paths.load_paths(config_path)
        assert "custom/" in paths
        assert "src/" in paths  # Defaults should be included


def test_add_path_appends_to_existing_config():
    """Test adding path to existing config."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "core_paths.yaml")

        # Create initial config
        initial_paths = ["src/", "lib/"]
        with open(config_path, "w") as f:
            yaml.safe_dump({"paths": initial_paths}, f)

        # Add new path
        core_paths.add_path(config_path, "custom/")

        # Verify path was appended
        paths = core_paths.load_paths(config_path)
        assert paths == ["src/", "lib/", "custom/"]


def test_add_path_raises_on_duplicate():
    """Test that adding duplicate path raises ValueError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "core_paths.yaml")

        # Create initial config
        with open(config_path, "w") as f:
            yaml.safe_dump({"paths": ["src/"]}, f)

        # Attempt to add duplicate
        with pytest.raises(ValueError, match="already exists"):
            core_paths.add_path(config_path, "src/")


def test_add_path_normalizes_trailing_slash():
    """Test that adding path without trailing slash normalizes it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "core_paths.yaml")

        # Add path without trailing slash
        core_paths.add_path(config_path, "custom")

        # Verify it was normalized with trailing slash
        paths = core_paths.load_paths(config_path)
        assert "custom/" in paths
        assert "custom" not in paths


def test_remove_path_removes_from_config():
    """Test removing path from config."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "core_paths.yaml")

        # Create config with paths
        with open(config_path, "w") as f:
            yaml.safe_dump({"paths": ["src/", "lib/", "custom/"]}, f)

        # Remove path
        core_paths.remove_path(config_path, "lib/")

        # Verify path was removed
        paths = core_paths.load_paths(config_path)
        assert paths == ["src/", "custom/"]


def test_remove_path_raises_if_not_found():
    """Test that removing non-existent path raises ValueError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "core_paths.yaml")

        # Create config
        with open(config_path, "w") as f:
            yaml.safe_dump({"paths": ["src/"]}, f)

        # Attempt to remove non-existent path
        with pytest.raises(ValueError, match="not found"):
            core_paths.remove_path(config_path, "nonexistent/")


def test_is_core_path_matches_prefix():
    """Test that is_core_path correctly matches file path prefixes."""
    paths = ["src/", "lib/"]

    # Exact matches
    assert core_paths.is_core_path("src/main.py", paths) is True
    assert core_paths.is_core_path("lib/utils.py", paths) is True

    # Nested paths
    assert core_paths.is_core_path("src/subdir/file.py", paths) is True
    assert core_paths.is_core_path("lib/deep/nested/file.py", paths) is True

    # Non-matches
    assert core_paths.is_core_path("tests/test_main.py", paths) is False
    assert core_paths.is_core_path("docs/readme.md", paths) is False


def test_is_core_path_handles_absolute_paths():
    """Test that is_core_path works with absolute file paths."""
    paths = ["src/", "lib/"]

    # Absolute paths should match based on suffix
    assert core_paths.is_core_path("/home/user/project/src/main.py", paths) is True
    assert core_paths.is_core_path("/home/user/project/lib/utils.py", paths) is True
    assert core_paths.is_core_path("/home/user/project/tests/test.py", paths) is False


def test_is_core_path_with_empty_paths_returns_false():
    """Test that is_core_path with empty paths list returns False."""
    assert core_paths.is_core_path("src/main.py", []) is False


def test_format_paths_for_display():
    """Test formatting paths for CLI display."""
    paths = ["src/", "lib/", "custom/"]

    output = core_paths.format_paths_for_display(paths)

    assert "src/" in output
    assert "lib/" in output
    assert "custom/" in output


def test_format_paths_for_display_empty_list():
    """Test formatting empty paths list."""
    output = core_paths.format_paths_for_display([])
    assert "No core paths configured" in output


def test_format_paths_for_prompt():
    """Test formatting paths for prompt injection."""
    paths = ["src/", "lib/"]

    output = core_paths.format_paths_for_prompt(paths)

    # Should be newline-separated with indentation
    assert "src/" in output
    assert "lib/" in output
    assert "\n" in output
