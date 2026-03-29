#!/usr/bin/env python3
"""
Tests for constants module.

Validates that all required configuration path constants are defined correctly.
"""

import os

from src.pacemaker import constants


class TestConfigurationPaths:
    """Test configuration path constants."""

    def test_default_excluded_paths_path_exists(self):
        """Should define DEFAULT_EXCLUDED_PATHS_PATH constant."""
        assert hasattr(constants, "DEFAULT_EXCLUDED_PATHS_PATH")
        assert constants.DEFAULT_EXCLUDED_PATHS_PATH is not None

    def test_default_excluded_paths_path_location(self):
        """Should point to ~/.claude-pace-maker/excluded_paths.yaml."""
        # Constants are computed at module import time (before conftest overrides HOME),
        # so we verify the path ends with the expected suffix rather than recomputing
        # from Path.home() which returns the fake home set by conftest.
        assert constants.DEFAULT_EXCLUDED_PATHS_PATH.endswith(
            os.path.join(".claude-pace-maker", "excluded_paths.yaml")
        )

    def test_default_excluded_paths_path_is_absolute(self):
        """Should be an absolute path."""
        assert os.path.isabs(constants.DEFAULT_EXCLUDED_PATHS_PATH)
