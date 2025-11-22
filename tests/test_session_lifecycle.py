#!/usr/bin/env python3
"""
Tests for session lifecycle - Stop hook functionality.
"""

import unittest
import tempfile
import os
import json
import io
from unittest.mock import patch


class TestStopHook(unittest.TestCase):
    """Test Stop hook lifecycle checking."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.json")
        self.state_path = os.path.join(self.temp_dir, "state.json")

    def tearDown(self):
        """Clean up."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_stop_hook_allows_when_tempo_disabled(self):
        """Should allow session end when tempo tracking is disabled."""
        from src.pacemaker.hook import run_stop_hook

        # Create config with tempo DISABLED
        config = {"tempo_enabled": False}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Mock stdin
        mock_stdin = io.StringIO(json.dumps({"transcript_path": "/nonexistent"}))

        with patch("src.pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("src.pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                with patch("sys.stdin", mock_stdin):
                    result = run_stop_hook()

        # Should return continue: True when tempo disabled
        self.assertEqual(result.get("continue"), True)

    def test_stop_hook_prevents_infinite_loop(self):
        """Should allow exit when stop_hook_active is true (prevent infinite loop)."""
        from src.pacemaker.hook import run_stop_hook

        # Create enabled tempo config
        config = {"tempo_enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Mock stdin with stop_hook_active = true
        hook_data = {"transcript_path": "/some/path", "stop_hook_active": True}
        mock_stdin = io.StringIO(json.dumps(hook_data))

        with patch("src.pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("src.pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                with patch("sys.stdin", mock_stdin):
                    result = run_stop_hook()

        # Should allow exit to prevent infinite loop
        self.assertEqual(result.get("continue"), True)


if __name__ == "__main__":
    unittest.main()
