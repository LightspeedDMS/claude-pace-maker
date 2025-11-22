#!/usr/bin/env python3
"""
Tests for SessionStart hook.
"""

import unittest
import tempfile
import os
import json
import sys
import io
from unittest.mock import patch


class TestSessionStartHook(unittest.TestCase):
    """Test SessionStart hook behavior."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.json")

    def tearDown(self):
        """Clean up."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_session_start_shows_reminder_when_tempo_enabled(self):
        """Should show reminder text when tempo is enabled."""
        from src.pacemaker.hook import run_session_start

        # Create config with tempo enabled
        config = {"tempo_enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Capture stdout
        captured = io.StringIO()
        sys.stdout = captured

        try:
            with patch("src.pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
                run_session_start()

            output = captured.getvalue()

            # Should show reminder text
            self.assertIn("SESSION COMPLETION PROTOCOL", output)
            self.assertIn("IMPLEMENTATION_COMPLETE", output)
            self.assertIn("EXCHANGE_COMPLETE", output)
        finally:
            sys.stdout = sys.__stdout__

    def test_session_start_silent_when_tempo_disabled(self):
        """Should NOT show reminder when tempo is disabled."""
        from src.pacemaker.hook import run_session_start

        # Create config with tempo disabled
        config = {"tempo_enabled": False}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Capture stdout
        captured = io.StringIO()
        sys.stdout = captured

        try:
            with patch("src.pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
                run_session_start()

            output = captured.getvalue()

            # Should be silent
            self.assertEqual(output.strip(), "")
        finally:
            sys.stdout = sys.__stdout__

    def test_session_start_defaults_to_enabled(self):
        """Should default to enabled if tempo_enabled not in config."""
        from src.pacemaker.hook import run_session_start

        # Create config without tempo_enabled key
        config = {"enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Capture stdout
        captured = io.StringIO()
        sys.stdout = captured

        try:
            with patch("src.pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
                run_session_start()

            output = captured.getvalue()

            # Should show reminder (default to enabled)
            self.assertIn("SESSION COMPLETION PROTOCOL", output)
        finally:
            sys.stdout = sys.__stdout__

    def test_session_start_graceful_degradation_on_error(self):
        """Should not crash if config is missing or invalid."""
        from src.pacemaker.hook import run_session_start

        # Point to non-existent config
        fake_path = os.path.join(self.temp_dir, "nonexistent.json")

        # Capture stderr
        captured = io.StringIO()
        sys.stderr = captured

        try:
            with patch("src.pacemaker.hook.DEFAULT_CONFIG_PATH", fake_path):
                # Should not raise exception
                run_session_start()
        finally:
            sys.stderr = sys.__stderr__

    def test_session_start_reminder_exact_format(self):
        """Should output exact reminder format."""
        from src.pacemaker.hook import run_session_start
        from src.pacemaker.lifecycle import IMPLEMENTATION_REMINDER_TEXT

        # Create config with tempo enabled
        config = {"tempo_enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Capture stdout
        captured = io.StringIO()
        sys.stdout = captured

        try:
            with patch("src.pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
                run_session_start()

            output = captured.getvalue()

            # Should match exact reminder text
            self.assertIn(IMPLEMENTATION_REMINDER_TEXT, output)
        finally:
            sys.stdout = sys.__stdout__


if __name__ == "__main__":
    unittest.main()
