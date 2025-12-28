#!/usr/bin/env python3
"""
Tests for constants module.

Validates that all required configuration path constants are defined correctly.
"""

import os
from pathlib import Path

from src.pacemaker import constants


class TestConfigurationPaths:
    """Test configuration path constants."""

    def test_default_excluded_paths_path_exists(self):
        """Should define DEFAULT_EXCLUDED_PATHS_PATH constant."""
        assert hasattr(constants, "DEFAULT_EXCLUDED_PATHS_PATH")
        assert constants.DEFAULT_EXCLUDED_PATHS_PATH is not None

    def test_default_excluded_paths_path_location(self):
        """Should point to ~/.claude-pace-maker/excluded_paths.yaml."""
        expected = str(Path.home() / ".claude-pace-maker" / "excluded_paths.yaml")
        assert constants.DEFAULT_EXCLUDED_PATHS_PATH == expected

    def test_default_excluded_paths_path_is_absolute(self):
        """Should be an absolute path."""
        assert os.path.isabs(constants.DEFAULT_EXCLUDED_PATHS_PATH)
