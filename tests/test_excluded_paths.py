#!/usr/bin/env python3
"""
Unit tests for excluded_paths module.

Tests CRUD operations for managing TDD exclusion paths.
"""

import os
import tempfile
import pytest
import yaml

# Import will fail initially - this is expected in TDD
from src.pacemaker import excluded_paths


class TestGetDefaultExclusions:
    """Test default exclusions list."""

    def test_returns_expected_defaults(self):
        """Should return expected default exclusion paths."""
        defaults = excluded_paths.get_default_exclusions()

        # Should include common non-production folders
        assert ".tmp/" in defaults
        assert "test/" in defaults
        assert "tests/" in defaults
        assert "fixtures/" in defaults
        assert "__pycache__/" in defaults
        assert "node_modules/" in defaults
        assert "vendor/" in defaults
        assert "dist/" in defaults
        assert "build/" in defaults
        assert ".git/" in defaults

    def test_returns_list(self):
        """Should return a list."""
        defaults = excluded_paths.get_default_exclusions()
        assert isinstance(defaults, list)

    def test_all_paths_have_trailing_slash(self):
        """All default paths should have trailing slash."""
        defaults = excluded_paths.get_default_exclusions()
        for path in defaults:
            assert path.endswith("/"), f"Path {path} missing trailing slash"


class TestLoadExclusions:
    """Test loading exclusions from YAML config."""

    def test_file_missing_returns_defaults(self):
        """Should return defaults when config file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "nonexistent", "excluded_paths.yaml")
            exclusions = excluded_paths.load_exclusions(config_path)

            # Should return defaults
            assert ".tmp/" in exclusions
            assert "tests/" in exclusions

    def test_valid_yaml_returns_custom_paths(self):
        """Should parse and return custom paths from valid YAML."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "excluded_paths.yaml")

            # Write valid config
            config_data = {"excluded_paths": [".custom/", "generated/", ".tmp/"]}
            with open(config_path, "w") as f:
                yaml.safe_dump(config_data, f)

            exclusions = excluded_paths.load_exclusions(config_path)

            assert exclusions == [".custom/", "generated/", ".tmp/"]

    def test_invalid_yaml_returns_defaults(self):
        """Should return defaults when YAML has syntax errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "excluded_paths.yaml")

            # Write invalid YAML
            with open(config_path, "w") as f:
                f.write("invalid: yaml: syntax: [unclosed")

            exclusions = excluded_paths.load_exclusions(config_path)

            # Should fall back to defaults
            assert ".tmp/" in exclusions
            assert "tests/" in exclusions

    def test_missing_excluded_paths_key_returns_defaults(self):
        """Should return defaults when YAML missing 'excluded_paths' key."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "excluded_paths.yaml")

            # Write YAML without excluded_paths key
            with open(config_path, "w") as f:
                yaml.safe_dump({"other_key": "value"}, f)

            exclusions = excluded_paths.load_exclusions(config_path)

            # Should fall back to defaults
            assert ".tmp/" in exclusions

    def test_empty_excluded_paths_returns_defaults(self):
        """Should return defaults when excluded_paths is empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "excluded_paths.yaml")

            # Write YAML with empty list
            with open(config_path, "w") as f:
                yaml.safe_dump({"excluded_paths": []}, f)

            exclusions = excluded_paths.load_exclusions(config_path)

            # Should fall back to defaults
            assert ".tmp/" in exclusions

    def test_excluded_paths_not_a_list_returns_defaults(self):
        """Should return defaults when excluded_paths is not a list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "excluded_paths.yaml")

            # Write YAML with string instead of list
            with open(config_path, "w") as f:
                yaml.safe_dump({"excluded_paths": "not_a_list"}, f)

            exclusions = excluded_paths.load_exclusions(config_path)

            # Should fall back to defaults
            assert ".tmp/" in exclusions


class TestIsExcludedPath:
    """Test path exclusion matching logic."""

    def test_match_returns_true(self):
        """Should return True when file path contains exclusion pattern."""
        exclusions = [".tmp/", "tests/", "__pycache__/"]

        assert excluded_paths.is_excluded_path(".tmp/test.py", exclusions) is True
        assert excluded_paths.is_excluded_path("tests/test_foo.py", exclusions) is True
        assert (
            excluded_paths.is_excluded_path("lib/__pycache__/module.pyc", exclusions)
            is True
        )

    def test_no_match_returns_false(self):
        """Should return False when file path doesn't match any exclusion."""
        exclusions = [".tmp/", "tests/"]

        assert excluded_paths.is_excluded_path("src/module.py", exclusions) is False
        assert excluded_paths.is_excluded_path("lib/core.py", exclusions) is False

    def test_absolute_path_matching(self):
        """Should handle absolute paths correctly."""
        exclusions = [".tmp/", "tests/"]

        # Absolute paths should also match
        assert (
            excluded_paths.is_excluded_path(
                "/home/user/project/.tmp/file.py", exclusions
            )
            is True
        )
        assert (
            excluded_paths.is_excluded_path(
                "/home/user/project/tests/test.py", exclusions
            )
            is True
        )
        assert (
            excluded_paths.is_excluded_path(
                "/home/user/project/src/module.py", exclusions
            )
            is False
        )

    def test_empty_exclusions_returns_false(self):
        """Should return False when exclusions list is empty."""
        assert excluded_paths.is_excluded_path("any/path.py", []) is False

    def test_windows_path_separators(self):
        """Should handle Windows-style path separators."""
        exclusions = [".tmp/", "tests/"]

        # Backslash paths (Windows)
        assert excluded_paths.is_excluded_path(".tmp\\test.py", exclusions) is True
        assert excluded_paths.is_excluded_path("tests\\test_foo.py", exclusions) is True


class TestAddExclusion:
    """Test adding exclusions to config."""

    def test_add_new_exclusion_success(self):
        """Should successfully add new exclusion to config file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "excluded_paths.yaml")

            # Add first exclusion
            excluded_paths.add_exclusion(config_path, ".custom/")

            # Verify written to file
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)

            assert ".custom/" in config["excluded_paths"]

    def test_add_normalizes_path_with_trailing_slash(self):
        """Should normalize path by adding trailing slash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "excluded_paths.yaml")

            # Add without trailing slash
            excluded_paths.add_exclusion(config_path, ".custom")

            # Should be normalized with trailing slash
            exclusions = excluded_paths.load_exclusions(config_path)
            assert ".custom/" in exclusions

    def test_add_duplicate_raises_error(self):
        """Should raise ValueError when adding duplicate exclusion."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "excluded_paths.yaml")

            # Add first time
            excluded_paths.add_exclusion(config_path, ".custom/")

            # Try to add duplicate
            with pytest.raises(ValueError, match="already exists"):
                excluded_paths.add_exclusion(config_path, ".custom/")

    def test_add_preserves_existing_exclusions(self):
        """Should preserve existing exclusions when adding new one."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "excluded_paths.yaml")

            # Add first exclusion
            excluded_paths.add_exclusion(config_path, ".custom/")

            # Add second exclusion
            excluded_paths.add_exclusion(config_path, "generated/")

            # Both should be present
            exclusions = excluded_paths.load_exclusions(config_path)
            assert ".custom/" in exclusions
            assert "generated/" in exclusions

    def test_add_creates_directory_if_missing(self):
        """Should create parent directory if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "subdir", "excluded_paths.yaml")

            # Directory doesn't exist yet
            assert not os.path.exists(os.path.dirname(config_path))

            # Add exclusion
            excluded_paths.add_exclusion(config_path, ".custom/")

            # Directory should be created
            assert os.path.exists(os.path.dirname(config_path))
            assert os.path.exists(config_path)


class TestRemoveExclusion:
    """Test removing exclusions from config."""

    def test_remove_existing_exclusion_success(self):
        """Should successfully remove existing exclusion."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "excluded_paths.yaml")

            # Add two exclusions
            excluded_paths.add_exclusion(config_path, ".custom/")
            excluded_paths.add_exclusion(config_path, "generated/")

            # Remove one
            excluded_paths.remove_exclusion(config_path, ".custom/")

            # Verify removal
            exclusions = excluded_paths.load_exclusions(config_path)
            assert ".custom/" not in exclusions
            assert "generated/" in exclusions

    def test_remove_nonexistent_raises_error(self):
        """Should raise ValueError when removing non-existent exclusion."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "excluded_paths.yaml")

            # Add one exclusion
            excluded_paths.add_exclusion(config_path, ".custom/")

            # Try to remove non-existent
            with pytest.raises(ValueError, match="not found"):
                excluded_paths.remove_exclusion(config_path, "nonexistent/")

    def test_remove_from_defaults_creates_config(self):
        """Should create config file when removing default exclusion from missing config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "excluded_paths.yaml")

            # Config doesn't exist - will load defaults
            # Remove a default exclusion
            excluded_paths.remove_exclusion(config_path, ".tmp/")

            # Should create config file with defaults minus the removed item
            assert os.path.exists(config_path)
            exclusions = excluded_paths.load_exclusions(config_path)
            assert ".tmp/" not in exclusions
            assert "tests/" in exclusions  # Other defaults still present
