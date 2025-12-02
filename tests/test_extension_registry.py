#!/usr/bin/env python3
"""
Unit tests for extension_registry module.

Tests the source code extension registry functionality including:
- Loading extensions from config file
- Fallback to default extensions
- Checking if files are source code based on extension
"""

import os
import tempfile
import json

# Import module under test
from pacemaker import extension_registry


class TestGetDefaultExtensions:
    """Test get_default_extensions() function."""

    def test_returns_list(self):
        """Should return a list of extensions."""
        result = extension_registry.get_default_extensions()
        assert isinstance(result, list)

    def test_contains_common_extensions(self):
        """Should include common source code extensions."""
        extensions = extension_registry.get_default_extensions()

        # Check for Python
        assert ".py" in extensions

        # Check for JavaScript/TypeScript
        assert ".js" in extensions
        assert ".ts" in extensions

        # Check for compiled languages
        assert ".go" in extensions
        assert ".java" in extensions
        assert ".cpp" in extensions
        assert ".c" in extensions

    def test_all_extensions_start_with_dot(self):
        """All extensions should start with a dot."""
        extensions = extension_registry.get_default_extensions()

        for ext in extensions:
            assert ext.startswith("."), f"Extension {ext} does not start with dot"

    def test_no_duplicates(self):
        """Should not contain duplicate extensions."""
        extensions = extension_registry.get_default_extensions()
        assert len(extensions) == len(set(extensions))


class TestLoadExtensions:
    """Test load_extensions() function."""

    def test_loads_from_valid_config_file(self):
        """Should load extensions from valid JSON config file."""
        # Create temporary config file
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
            config_data = {"extensions": [".py", ".js", ".ts", ".go"]}
            json.dump(config_data, f)
            config_path = f.name

        try:
            result = extension_registry.load_extensions(config_path)

            assert isinstance(result, list)
            assert len(result) == 4
            assert ".py" in result
            assert ".js" in result
            assert ".ts" in result
            assert ".go" in result
        finally:
            os.unlink(config_path)

    def test_falls_back_to_defaults_when_file_missing(self):
        """Should return default extensions when config file doesn't exist."""
        non_existent_path = "/tmp/does_not_exist_12345.json"
        assert not os.path.exists(non_existent_path)

        result = extension_registry.load_extensions(non_existent_path)

        # Should return defaults
        assert isinstance(result, list)
        assert ".py" in result
        assert ".js" in result

    def test_falls_back_to_defaults_when_file_invalid_json(self):
        """Should return default extensions when config file has invalid JSON."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
            f.write("{ invalid json }")
            config_path = f.name

        try:
            result = extension_registry.load_extensions(config_path)

            # Should return defaults
            assert isinstance(result, list)
            assert ".py" in result
        finally:
            os.unlink(config_path)

    def test_falls_back_to_defaults_when_missing_extensions_key(self):
        """Should return default extensions when config file missing 'extensions' key."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
            config_data = {"other_key": ["value"]}
            json.dump(config_data, f)
            config_path = f.name

        try:
            result = extension_registry.load_extensions(config_path)

            # Should return defaults
            assert isinstance(result, list)
            assert ".py" in result
        finally:
            os.unlink(config_path)

    def test_handles_empty_extensions_list(self):
        """Should return defaults when extensions list is empty."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
            config_data = {"extensions": []}
            json.dump(config_data, f)
            config_path = f.name

        try:
            result = extension_registry.load_extensions(config_path)

            # Should return defaults (empty list not useful)
            assert isinstance(result, list)
            assert len(result) > 0
        finally:
            os.unlink(config_path)


class TestIsSourceCodeFile:
    """Test is_source_code_file() function."""

    def test_returns_true_for_matching_extension(self):
        """Should return True when file extension matches registry."""
        extensions = [".py", ".js", ".ts"]

        assert (
            extension_registry.is_source_code_file("/path/to/file.py", extensions)
            is True
        )
        assert (
            extension_registry.is_source_code_file("/path/to/file.js", extensions)
            is True
        )
        assert (
            extension_registry.is_source_code_file("/path/to/file.ts", extensions)
            is True
        )

    def test_returns_false_for_non_matching_extension(self):
        """Should return False when file extension doesn't match registry."""
        extensions = [".py", ".js", ".ts"]

        assert (
            extension_registry.is_source_code_file("/path/to/file.md", extensions)
            is False
        )
        assert (
            extension_registry.is_source_code_file("/path/to/file.txt", extensions)
            is False
        )
        assert (
            extension_registry.is_source_code_file("/path/to/README", extensions)
            is False
        )

    def test_case_insensitive_matching(self):
        """Should match extensions case-insensitively."""
        extensions = [".py", ".js"]

        # Uppercase extensions should match
        assert (
            extension_registry.is_source_code_file("/path/to/file.PY", extensions)
            is True
        )
        assert (
            extension_registry.is_source_code_file("/path/to/file.Js", extensions)
            is True
        )
        assert (
            extension_registry.is_source_code_file("/path/to/file.JS", extensions)
            is True
        )

    def test_handles_no_extension(self):
        """Should return False for files without extension."""
        extensions = [".py", ".js"]

        assert (
            extension_registry.is_source_code_file("/path/to/Makefile", extensions)
            is False
        )
        assert (
            extension_registry.is_source_code_file("/path/to/README", extensions)
            is False
        )

    def test_handles_empty_extensions_list(self):
        """Should return False when extensions list is empty."""
        extensions = []

        assert (
            extension_registry.is_source_code_file("/path/to/file.py", extensions)
            is False
        )

    def test_handles_complex_file_paths(self):
        """Should correctly extract extension from complex paths."""
        extensions = [".py", ".js"]

        # Multiple dots in filename
        assert (
            extension_registry.is_source_code_file("/path/to/file.test.py", extensions)
            is True
        )

        # Dots in directory name
        assert (
            extension_registry.is_source_code_file(
                "/path.with.dots/file.js", extensions
            )
            is True
        )

        # Hidden file
        assert (
            extension_registry.is_source_code_file("/path/to/.hidden.py", extensions)
            is True
        )

    def test_handles_relative_paths(self):
        """Should work with relative file paths."""
        extensions = [".py"]

        assert (
            extension_registry.is_source_code_file("src/module.py", extensions) is True
        )
        assert extension_registry.is_source_code_file("./test.py", extensions) is True
        assert (
            extension_registry.is_source_code_file("../parent/file.py", extensions)
            is True
        )
