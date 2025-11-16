#!/usr/bin/env python3
"""
Negative test cases for user_commands.py error handling.

Tests exception paths, help command, weekly-limit commands,
and error conditions to achieve >90% code coverage.
"""

import unittest
import tempfile
import os
import json
import sqlite3
from unittest.mock import patch
from datetime import datetime, timedelta


class TestUserCommandsErrorHandling(unittest.TestCase):
    """Test error handling in user_commands module."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.json")
        self.db_path = os.path.join(self.temp_dir, "test.db")

    def tearDown(self):
        """Clean up."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_execute_help_command(self):
        """Should display help text when 'pace-maker help' is executed."""
        from pacemaker.user_commands import execute_command

        result = execute_command("help", self.config_path)

        self.assertTrue(result["success"])
        self.assertIn("COMMANDS:", result["message"])
        self.assertIn("pace-maker on", result["message"])
        self.assertIn("pace-maker off", result["message"])
        self.assertIn("pace-maker status", result["message"])
        self.assertIn("pace-maker help", result["message"])
        self.assertIn("pace-maker weekly-limit", result["message"])
        self.assertIn("pace-maker tempo", result["message"])

    def test_execute_help_via_handle_user_prompt(self):
        """Should intercept and execute help command."""
        from pacemaker.user_commands import handle_user_prompt

        result = handle_user_prompt("pace-maker help", self.config_path)

        self.assertTrue(result["intercepted"])
        self.assertIn("COMMANDS:", result["output"])

    def test_execute_unknown_command(self):
        """Should return error for unknown command."""
        from pacemaker.user_commands import execute_command

        result = execute_command("unknown", self.config_path)

        self.assertFalse(result["success"])
        self.assertIn("Unknown command", result["message"])

    def test_execute_weekly_limit_on(self):
        """Should enable weekly limit throttling."""
        from pacemaker.user_commands import execute_command

        result = execute_command("weekly-limit", self.config_path, subcommand="on")

        self.assertTrue(result["success"])
        self.assertIn("Weekly limit ENABLED", result["message"])

        # Verify config was updated
        with open(self.config_path) as f:
            config = json.load(f)
            self.assertTrue(config["weekly_limit_enabled"])

    def test_execute_weekly_limit_off(self):
        """Should disable weekly limit throttling."""
        from pacemaker.user_commands import execute_command

        # First enable it
        execute_command("weekly-limit", self.config_path, subcommand="on")

        # Then disable it
        result = execute_command("weekly-limit", self.config_path, subcommand="off")

        self.assertTrue(result["success"])
        self.assertIn("Weekly limit DISABLED", result["message"])

        # Verify config was updated
        with open(self.config_path) as f:
            config = json.load(f)
            self.assertFalse(config["weekly_limit_enabled"])

    def test_execute_weekly_limit_invalid_subcommand(self):
        """Should return error for invalid weekly-limit subcommand."""
        from pacemaker.user_commands import execute_command

        result = execute_command("weekly-limit", self.config_path, subcommand="invalid")

        self.assertFalse(result["success"])
        self.assertIn("Unknown subcommand", result["message"])
        self.assertIn("weekly-limit [on|off]", result["message"])

    def test_execute_weekly_limit_via_handle_user_prompt(self):
        """Should parse and execute weekly-limit commands."""
        from pacemaker.user_commands import handle_user_prompt

        result = handle_user_prompt("pace-maker weekly-limit on", self.config_path)

        self.assertTrue(result["intercepted"])
        self.assertIn("Weekly limit ENABLED", result["output"])

    def test_execute_tempo_invalid_subcommand(self):
        """Should return error for invalid tempo subcommand."""
        from pacemaker.user_commands import execute_command

        result = execute_command("tempo", self.config_path, subcommand="invalid")

        self.assertFalse(result["success"])
        self.assertIn("Unknown subcommand", result["message"])
        self.assertIn("tempo [on|off]", result["message"])

    def test_execute_on_with_file_write_error(self):
        """Should handle file write errors gracefully."""
        from pacemaker.user_commands import _execute_on

        # Mock _write_config_atomic to raise exception
        with patch(
            "pacemaker.user_commands._write_config_atomic",
            side_effect=IOError("Disk full"),
        ):
            result = _execute_on(self.config_path)

        self.assertFalse(result["success"])
        self.assertIn("Error enabling pace maker", result["message"])

    def test_execute_off_with_file_write_error(self):
        """Should handle file write errors gracefully."""
        from pacemaker.user_commands import _execute_off

        # Mock _write_config_atomic to raise exception
        with patch(
            "pacemaker.user_commands._write_config_atomic",
            side_effect=PermissionError("Access denied"),
        ):
            result = _execute_off(self.config_path)

        self.assertFalse(result["success"])
        self.assertIn("Error disabling pace maker", result["message"])

    def test_execute_status_with_exception(self):
        """Should handle exceptions during status execution."""
        from pacemaker.user_commands import _execute_status

        # Mock _load_config to raise exception
        with patch(
            "pacemaker.user_commands._load_config",
            side_effect=ValueError("Config error"),
        ):
            result = _execute_status(self.config_path)

        self.assertFalse(result["success"])
        self.assertIn("Error getting status", result["message"])

    def test_execute_weekly_limit_on_with_exception(self):
        """Should handle exceptions when enabling weekly limit."""
        from pacemaker.user_commands import execute_command

        # Mock _write_config_atomic to raise exception
        with patch(
            "pacemaker.user_commands._write_config_atomic",
            side_effect=OSError("I/O error"),
        ):
            result = execute_command("weekly-limit", self.config_path, subcommand="on")

        self.assertFalse(result["success"])
        self.assertIn("Error enabling weekly limit", result["message"])

    def test_execute_weekly_limit_off_with_exception(self):
        """Should handle exceptions when disabling weekly limit."""
        from pacemaker.user_commands import execute_command

        # Mock _write_config_atomic to raise exception
        with patch(
            "pacemaker.user_commands._write_config_atomic",
            side_effect=RuntimeError("Runtime error"),
        ):
            result = execute_command("weekly-limit", self.config_path, subcommand="off")

        self.assertFalse(result["success"])
        self.assertIn("Error disabling weekly limit", result["message"])

    def test_execute_tempo_on_with_exception(self):
        """Should handle exceptions when enabling tempo."""
        from pacemaker.user_commands import execute_command

        # Mock _write_config_atomic to raise exception
        with patch(
            "pacemaker.user_commands._write_config_atomic",
            side_effect=IOError("Write error"),
        ):
            result = execute_command("tempo", self.config_path, subcommand="on")

        self.assertFalse(result["success"])
        self.assertIn("Error enabling tempo", result["message"])

    def test_execute_tempo_off_with_exception(self):
        """Should handle exceptions when disabling tempo."""
        from pacemaker.user_commands import execute_command

        # Mock _write_config_atomic to raise exception
        with patch(
            "pacemaker.user_commands._write_config_atomic",
            side_effect=Exception("Generic error"),
        ):
            result = execute_command("tempo", self.config_path, subcommand="off")

        self.assertFalse(result["success"])
        self.assertIn("Error disabling tempo", result["message"])

    def test_get_latest_usage_with_database_error(self):
        """Should return None when database query fails."""
        from pacemaker.user_commands import _get_latest_usage

        # Create invalid database
        with open(self.db_path, "w") as f:
            f.write("not a database")

        # Should return None instead of crashing
        result = _get_latest_usage(self.db_path)
        self.assertIsNone(result)

    def test_get_latest_usage_with_datetime_parse_error(self):
        """Should handle datetime parsing errors gracefully."""
        from pacemaker.user_commands import _get_latest_usage

        # Create database with invalid datetime strings
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE usage_snapshots (
                timestamp TEXT,
                five_hour_util REAL,
                seven_day_util REAL,
                five_hour_resets_at TEXT,
                seven_day_resets_at TEXT
            )
        """
        )
        cursor.execute(
            """
            INSERT INTO usage_snapshots VALUES (
                '2025-11-16 10:00:00',
                42.5,
                18.3,
                'invalid datetime format',
                'also invalid'
            )
        """
        )
        conn.commit()
        conn.close()

        # Should return data with None for unparseable datetimes
        result = _get_latest_usage(self.db_path)
        self.assertIsNotNone(result)
        self.assertEqual(result["five_hour_util"], 42.5)
        self.assertIsNone(result["five_hour_resets_at"])
        self.assertIsNone(result["seven_day_resets_at"])

    def test_get_latest_usage_with_empty_database(self):
        """Should return None when database has no data."""
        from pacemaker.user_commands import _get_latest_usage

        # Create empty database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE usage_snapshots (
                timestamp TEXT,
                five_hour_util REAL,
                seven_day_util REAL,
                five_hour_resets_at TEXT,
                seven_day_resets_at TEXT
            )
        """
        )
        conn.commit()
        conn.close()

        # Should return None
        result = _get_latest_usage(self.db_path)
        self.assertIsNone(result)

    def test_execute_status_with_detailed_pacing_info(self):
        """Should display detailed pacing information when enabled and over budget."""
        from pacemaker.user_commands import execute_command
        from pacemaker.database import initialize_database, insert_usage_snapshot

        # Initialize database and insert usage data
        initialize_database(self.db_path)
        now = datetime.utcnow()
        insert_usage_snapshot(
            db_path=self.db_path,
            timestamp=now,
            five_hour_util=85.0,  # Over budget
            five_hour_resets_at=now + timedelta(hours=1),
            seven_day_util=45.0,
            seven_day_resets_at=now + timedelta(days=3),
            session_id="test-session",
        )

        # Enable pace maker
        execute_command("on", self.config_path)

        # Get status
        result = execute_command("status", self.config_path, self.db_path)

        self.assertTrue(result["success"])
        self.assertIn("ACTIVE", result["message"])
        self.assertIn("85.0% used", result["message"])
        self.assertIn("Pacing Status:", result["message"])

    def test_execute_status_shows_7day_window_for_pro_max(self):
        """Should display 7-day window when it has non-zero utilization."""
        from pacemaker.user_commands import execute_command
        from pacemaker.database import initialize_database, insert_usage_snapshot

        # Initialize database with 7-day window usage
        initialize_database(self.db_path)
        now = datetime.utcnow()
        insert_usage_snapshot(
            db_path=self.db_path,
            timestamp=now,
            five_hour_util=25.0,
            five_hour_resets_at=now + timedelta(hours=2),
            seven_day_util=15.0,  # Non-zero, should be displayed
            seven_day_resets_at=now + timedelta(days=5),
            session_id="test-session",
        )

        # Get status
        result = execute_command("status", self.config_path, self.db_path)

        self.assertTrue(result["success"])
        self.assertIn("7-day window:", result["message"])
        self.assertIn("15.0% used", result["message"])


if __name__ == "__main__":
    unittest.main()
