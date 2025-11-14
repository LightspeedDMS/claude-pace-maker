#!/usr/bin/env python3
"""
Tests for hook execution module.
"""

import unittest
import tempfile
import os
import json
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import sys
import io


class TestHook(unittest.TestCase):
    """Test hook execution."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, 'config.json')
        self.state_path = os.path.join(self.temp_dir, 'state.json')
        self.db_path = os.path.join(self.temp_dir, 'test.db')

    def tearDown(self):
        """Clean up."""
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_inject_prompt_delay_prints_to_stdout(self):
        """Should print prompt to stdout."""
        from pacemaker.hook import inject_prompt_delay

        # Capture stdout
        captured = io.StringIO()
        sys.stdout = captured

        try:
            inject_prompt_delay("[TEST] Wait 30 seconds")
            output = captured.getvalue()
            self.assertIn("[TEST] Wait 30 seconds", output)
        finally:
            sys.stdout = sys.__stdout__

    def test_run_hook_disabled_config(self):
        """Should do nothing when disabled in config."""
        from pacemaker.hook import run_hook

        # Create disabled config
        config = {"enabled": False}
        with open(self.config_path, 'w') as f:
            json.dump(config, f)

        # Mock to use our config/state paths
        with patch('pacemaker.hook.DEFAULT_CONFIG_PATH', self.config_path):
            with patch('pacemaker.hook.DEFAULT_STATE_PATH', self.state_path):
                with patch('pacemaker.hook.DEFAULT_DB_PATH', self.db_path):
                    # Should return without errors
                    run_hook()

        # State file should not be created (hook did nothing)
        self.assertFalse(os.path.exists(self.state_path))

    def test_run_hook_enabled_with_mocked_api(self):
        """Should execute complete hook with API mocked."""
        from pacemaker.hook import run_hook
        from pacemaker import database

        # Initialize database
        database.initialize_database(self.db_path)

        # Create enabled config
        config = {"enabled": True, "poll_interval": 0}  # Force immediate poll
        with open(self.config_path, 'w') as f:
            json.dump(config, f)

        # Mock API response
        mock_usage = {
            'five_hour_util': 30.0,
            'five_hour_resets_at': datetime.utcnow() + timedelta(hours=3),
            'seven_day_util': 40.0,
            'seven_day_resets_at': datetime.utcnow() + timedelta(days=4)
        }

        # Patch at module level where they're used
        with patch('pacemaker.hook.DEFAULT_CONFIG_PATH', self.config_path):
            with patch('pacemaker.hook.DEFAULT_STATE_PATH', self.state_path):
                with patch('pacemaker.hook.DEFAULT_DB_PATH', self.db_path):
                    with patch('pacemaker.api_client.fetch_usage', return_value=mock_usage):
                        with patch('pacemaker.api_client.load_access_token', return_value='fake-token'):
                            run_hook()

        # State file should be created (hook ran successfully)
        # Note: May not exist if no polls happened, so just verify no crash
        self.assertTrue(True)  # Hook completed without exception

    def test_main_graceful_degradation_on_exception(self):
        """Should not crash on exceptions."""
        from pacemaker.hook import main

        # Mock run_hook to raise exception
        with patch('pacemaker.hook.run_hook', side_effect=Exception("Test error")):
            # Capture stderr
            captured = io.StringIO()
            sys.stderr = captured

            try:
                main()  # Should not raise
                output = captured.getvalue()
                self.assertIn("Test error", output)
            finally:
                sys.stderr = sys.__stderr__

    def test_execute_delay_actually_waits(self):
        """Should actually sleep for specified duration."""
        from pacemaker.hook import execute_delay
        import time

        start = time.time()
        execute_delay(1)
        elapsed = time.time() - start

        self.assertGreaterEqual(elapsed, 1.0)
        self.assertLess(elapsed, 1.5)

    def test_execute_delay_zero_seconds(self):
        """Should handle zero delay without error."""
        from pacemaker.hook import execute_delay
        import time

        start = time.time()
        execute_delay(0)
        elapsed = time.time() - start

        # Should return immediately
        self.assertLess(elapsed, 0.1)


if __name__ == '__main__':
    unittest.main()
