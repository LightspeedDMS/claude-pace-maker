"""
Unit tests for status display enhancements.

Tests for:
- Version display (Pace Maker and Usage Console)
- Langfuse connectivity status
- 24-hour error count with color coding
"""

from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from pacemaker.user_commands import (
    _execute_status,
    _count_recent_errors,
)


class TestErrorCounting:
    """Test error counting from log files."""

    def test_count_errors_no_errors(self, tmp_path):
        """Test counting when there are no errors in last 24 hours."""
        # Create log file with no ERROR entries using daily rotation naming
        from datetime import datetime

        log_file = tmp_path / f"pace-maker-{datetime.now().strftime('%Y-%m-%d')}.log"
        now = datetime.now()
        log_file.write_text(
            f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] [INFO] [module] Some info message\n"
            f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] [WARNING] [module] Some warning\n"
        )

        count = _count_recent_errors(hours=24, log_dir=str(tmp_path))
        assert count == 0

    def test_count_errors_within_24_hours(self, tmp_path):
        """Test counting errors within the last 24 hours."""
        now = datetime.now()
        log_file = tmp_path / f"pace-maker-{now.strftime('%Y-%m-%d')}.log"

        # Create log with 3 errors in last 24h
        log_entries = []
        for i in range(3):
            timestamp = now - timedelta(hours=i)
            log_entries.append(
                f"[{timestamp.strftime('%Y-%m-%d %H:%M:%S')}] [ERROR] [module] Error {i}\n"
            )

        log_file.write_text("".join(log_entries))

        count = _count_recent_errors(hours=24, log_dir=str(tmp_path))
        assert count == 3

    def test_count_errors_ignores_old_errors(self, tmp_path):
        """Test that errors older than 24h are ignored."""
        now = datetime.now()
        log_file = tmp_path / f"pace-maker-{now.strftime('%Y-%m-%d')}.log"

        # Create log with 2 recent errors and 3 old errors
        log_entries = []

        # Recent errors (within 24h)
        for i in range(2):
            timestamp = now - timedelta(hours=i)
            log_entries.append(
                f"[{timestamp.strftime('%Y-%m-%d %H:%M:%S')}] [ERROR] [module] Recent error {i}\n"
            )

        # Old errors (older than 24h)
        for i in range(3):
            timestamp = now - timedelta(hours=25 + i)
            log_entries.append(
                f"[{timestamp.strftime('%Y-%m-%d %H:%M:%S')}] [ERROR] [module] Old error {i}\n"
            )

        log_file.write_text("".join(log_entries))

        count = _count_recent_errors(hours=24, log_dir=str(tmp_path))
        assert count == 2

    def test_count_errors_missing_log_file(self, tmp_path):
        """Test graceful handling when log file doesn't exist."""
        non_existent_dir = tmp_path / "non-existent"

        # Should return 0 and not raise exception
        count = _count_recent_errors(hours=24, log_dir=str(non_existent_dir))
        assert count == 0

    def test_count_errors_empty_log_file(self, tmp_path):
        """Test counting when log file is empty."""
        now = datetime.now()
        log_file = tmp_path / f"pace-maker-{now.strftime('%Y-%m-%d')}.log"
        log_file.write_text("")

        count = _count_recent_errors(hours=24, log_dir=str(tmp_path))
        assert count == 0

    def test_count_errors_malformed_timestamps(self, tmp_path):
        """Test handling of malformed timestamp entries."""
        now = datetime.now()
        log_file = tmp_path / f"pace-maker-{now.strftime('%Y-%m-%d')}.log"

        # Mix of valid and invalid entries
        log_file.write_text(
            f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] [ERROR] [module] Valid error\n"
            f"[INVALID TIMESTAMP] [ERROR] [module] Invalid timestamp\n"
            f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] [ERROR] [module] Another valid error\n"
        )

        # Should count only valid entries
        count = _count_recent_errors(hours=24, log_dir=str(tmp_path))
        assert count == 2

    def test_count_errors_custom_hours(self, tmp_path):
        """Test counting with custom hour threshold."""
        now = datetime.now()
        log_file = tmp_path / f"pace-maker-{now.strftime('%Y-%m-%d')}.log"

        # Create errors at different time intervals
        log_entries = []
        for hours_ago in [1, 3, 6, 10]:
            timestamp = now - timedelta(hours=hours_ago)
            log_entries.append(
                f"[{timestamp.strftime('%Y-%m-%d %H:%M:%S')}] [ERROR] [module] Error at -{hours_ago}h\n"
            )

        log_file.write_text("".join(log_entries))

        # Test with 5-hour window (should count 2 errors)
        count = _count_recent_errors(hours=5, log_dir=str(tmp_path))
        assert count == 2

        # Test with 12-hour window (should count 4 errors)
        count = _count_recent_errors(hours=12, log_dir=str(tmp_path))
        assert count == 4


class TestStatusDisplayErrorCount:
    """Test error count display in status command."""

    @patch("pacemaker.user_commands._count_recent_errors")
    @patch("pacemaker.user_commands._load_config")
    def test_status_shows_zero_errors_green(
        self, mock_config, mock_count_errors, tmp_path
    ):
        """Test status displays 0 errors in green."""
        # Setup mocks
        mock_config.return_value = {"enabled": False}
        mock_count_errors.return_value = 0

        config_path = str(tmp_path / "config.json")

        result = _execute_status(config_path, db_path=None)

        assert result["success"] is True
        # Check for green color code and 0 errors
        assert "\033[32m" in result["message"]  # Green color
        assert "0 errors" in result["message"].lower() or "0)" in result["message"]
        assert "\033[0m" in result["message"]  # Reset color

    @patch("pacemaker.user_commands._count_recent_errors")
    @patch("pacemaker.user_commands._load_config")
    def test_status_shows_few_errors_yellow(
        self, mock_config, mock_count_errors, tmp_path
    ):
        """Test status displays 1-10 errors in yellow."""
        # Setup mocks
        mock_config.return_value = {"enabled": False}
        mock_count_errors.return_value = 5

        config_path = str(tmp_path / "config.json")

        result = _execute_status(config_path, db_path=None)

        assert result["success"] is True
        # Check for yellow color code and 5 errors
        assert "\033[33m" in result["message"]  # Yellow color
        assert "5 errors" in result["message"].lower() or "5)" in result["message"]
        assert "\033[0m" in result["message"]  # Reset color

    @patch("pacemaker.user_commands._count_recent_errors")
    @patch("pacemaker.user_commands._load_config")
    def test_status_shows_many_errors_red(
        self, mock_config, mock_count_errors, tmp_path
    ):
        """Test status displays >10 errors in red."""
        # Setup mocks
        mock_config.return_value = {"enabled": False}
        mock_count_errors.return_value = 15

        config_path = str(tmp_path / "config.json")

        result = _execute_status(config_path, db_path=None)

        assert result["success"] is True
        # Check for red color code and 15 errors
        assert "\033[31m" in result["message"]  # Red color
        assert "15 errors" in result["message"].lower() or "15)" in result["message"]
        assert "\033[0m" in result["message"]  # Reset color


class TestStatusDisplayVersions:
    """Test version display in status command."""

    @patch("pacemaker.user_commands._load_config")
    def test_status_shows_pacemaker_version(self, mock_config, tmp_path):
        """Test status displays Pace Maker version."""
        mock_config.return_value = {"enabled": False}
        config_path = str(tmp_path / "config.json")

        result = _execute_status(config_path, db_path=None)

        assert result["success"] is True
        assert "Pace Maker: v" in result["message"]
        # Version should be semver format
        import re

        assert re.search(r"Pace Maker: v\d+\.\d+\.\d+", result["message"])

    @patch("pacemaker.user_commands._load_config")
    def test_status_shows_usage_console_version_when_installed(
        self, mock_config, tmp_path
    ):
        """Test status displays Usage Console version when installed."""
        mock_config.return_value = {"enabled": False}
        config_path = str(tmp_path / "config.json")

        # Mock claude_usage module as installed
        with patch.dict(
            "sys.modules", {"claude_usage": MagicMock(__version__="2.1.0")}
        ):
            result = _execute_status(config_path, db_path=None)

        assert result["success"] is True
        assert "Usage Console: v2.1.0" in result["message"]

    @patch("pacemaker.user_commands._load_config")
    def test_status_shows_usage_console_not_installed(self, mock_config, tmp_path):
        """Test status displays 'not installed' when Usage Console missing."""
        mock_config.return_value = {"enabled": False}
        config_path = str(tmp_path / "config.json")

        # Mock ImportError when trying to import claude_usage
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "claude_usage":
                raise ImportError("No module named 'claude_usage'")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=mock_import):
            result = _execute_status(config_path, db_path=None)

        assert result["success"] is True
        assert "Usage Console: not installed" in result["message"]


class TestStatusDisplayLangfuse:
    """Test Langfuse status display in status command."""

    @patch("pacemaker.user_commands._langfuse_test_connection")
    @patch("pacemaker.user_commands._load_config")
    def test_status_shows_langfuse_disabled(self, mock_config, mock_test, tmp_path):
        """Test status displays Langfuse as DISABLED."""
        mock_config.return_value = {
            "enabled": False,
            "langfuse_enabled": False,
        }
        config_path = str(tmp_path / "config.json")

        result = _execute_status(config_path, db_path=None)

        assert result["success"] is True
        assert "Langfuse: DISABLED" in result["message"]
        # Should not call test_connection when disabled
        mock_test.assert_not_called()

    @patch("pacemaker.user_commands._langfuse_test_connection")
    @patch("pacemaker.user_commands._load_config")
    def test_status_shows_langfuse_enabled_connected(
        self, mock_config, mock_test, tmp_path
    ):
        """Test status displays Langfuse as ENABLED with green checkmark when connected."""
        mock_config.return_value = {
            "enabled": False,
            "langfuse_enabled": True,
        }
        mock_test.return_value = {
            "connected": True,
            "message": "Connected successfully",
        }
        config_path = str(tmp_path / "config.json")

        result = _execute_status(config_path, db_path=None)

        assert result["success"] is True
        assert "Langfuse: ENABLED" in result["message"]
        assert "\033[32m✓ Connected successfully\033[0m" in result["message"]
        mock_test.assert_called_once()

    @patch("pacemaker.user_commands._langfuse_test_connection")
    @patch("pacemaker.user_commands._load_config")
    def test_status_shows_langfuse_enabled_failed(
        self, mock_config, mock_test, tmp_path
    ):
        """Test status displays Langfuse as ENABLED with red X when connection fails."""
        mock_config.return_value = {
            "enabled": False,
            "langfuse_enabled": True,
        }
        mock_test.return_value = {
            "connected": False,
            "message": "Connection timeout",
        }
        config_path = str(tmp_path / "config.json")

        result = _execute_status(config_path, db_path=None)

        assert result["success"] is True
        assert "Langfuse: ENABLED" in result["message"]
        assert "\033[31m✗ Connection timeout\033[0m" in result["message"]
        mock_test.assert_called_once()
