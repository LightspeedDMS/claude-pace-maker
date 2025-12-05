#!/usr/bin/env python3
"""
Tests for centralized logging system.

Tests log level configuration, file writing, level filtering,
and exception handling.
"""

import pytest
import os
import tempfile
import json
from datetime import datetime

from src.pacemaker.logger import (
    log_error,
    log_warning,
    log_info,
    log_debug,
    _get_log_level,
    _ensure_log_dir,
)
from src.pacemaker.constants import (
    LOG_LEVEL_OFF,
    LOG_LEVEL_ERROR,
    LOG_LEVEL_WARNING,
    LOG_LEVEL_INFO,
    LOG_LEVEL_DEBUG,
)


@pytest.fixture
def temp_log_dir(monkeypatch):
    """Create temporary log directory and config."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = os.path.join(tmpdir, "pace-maker.log")
        config_path = os.path.join(tmpdir, "config.json")

        # Monkeypatch paths
        monkeypatch.setattr("src.pacemaker.logger.DEFAULT_LOG_PATH", log_path)
        monkeypatch.setattr("src.pacemaker.logger.DEFAULT_CONFIG_PATH", config_path)

        yield {
            "log_path": log_path,
            "config_path": config_path,
            "dir": tmpdir,
        }


def write_config(config_path: str, log_level: int):
    """Helper to write config file with log level."""
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w") as f:
        json.dump({"log_level": log_level}, f)


def read_log_lines(log_path: str) -> list:
    """Helper to read log file lines."""
    if not os.path.exists(log_path):
        return []
    with open(log_path, "r") as f:
        return f.readlines()


class TestLogLevelRetrieval:
    """Test _get_log_level() function."""

    def test_returns_default_when_config_missing(self, temp_log_dir, monkeypatch):
        """When config file doesn't exist, should return WARNING level."""
        monkeypatch.setattr(
            "src.pacemaker.logger.DEFAULT_CONFIG_PATH", "/nonexistent/config.json"
        )
        assert _get_log_level() == LOG_LEVEL_WARNING

    def test_returns_config_value_when_present(self, temp_log_dir):
        """When config exists, should return configured log level."""
        write_config(temp_log_dir["config_path"], LOG_LEVEL_DEBUG)
        assert _get_log_level() == LOG_LEVEL_DEBUG

    def test_returns_default_when_log_level_key_missing(self, temp_log_dir):
        """When config exists but log_level key missing, should return WARNING."""
        with open(temp_log_dir["config_path"], "w") as f:
            json.dump({"enabled": True}, f)
        assert _get_log_level() == LOG_LEVEL_WARNING

    def test_returns_default_when_config_corrupted(self, temp_log_dir):
        """When config is corrupted JSON, should return WARNING level."""
        with open(temp_log_dir["config_path"], "w") as f:
            f.write("not valid json {")
        assert _get_log_level() == LOG_LEVEL_WARNING


class TestLogDirectoryCreation:
    """Test _ensure_log_dir() function."""

    def test_creates_directory_when_missing(self, temp_log_dir):
        """Should create log directory if it doesn't exist."""
        nested_log = os.path.join(temp_log_dir["dir"], "nested", "pace-maker.log")

        import src.pacemaker.logger as logger_module

        original_path = logger_module.DEFAULT_LOG_PATH
        logger_module.DEFAULT_LOG_PATH = nested_log

        try:
            _ensure_log_dir()
            assert os.path.exists(os.path.dirname(nested_log))
        finally:
            logger_module.DEFAULT_LOG_PATH = original_path

    def test_succeeds_when_directory_exists(self, temp_log_dir):
        """Should succeed silently when directory already exists."""
        os.makedirs(os.path.dirname(temp_log_dir["log_path"]), exist_ok=True)
        _ensure_log_dir()  # Should not raise


class TestLogFiltering:
    """Test log level filtering behavior."""

    def test_off_level_writes_nothing(self, temp_log_dir):
        """LOG_LEVEL_OFF should prevent all logging."""
        write_config(temp_log_dir["config_path"], LOG_LEVEL_OFF)

        log_error("test", "error message")
        log_warning("test", "warning message")
        log_info("test", "info message")
        log_debug("test", "debug message")

        lines = read_log_lines(temp_log_dir["log_path"])
        assert len(lines) == 0

    def test_error_level_writes_only_errors(self, temp_log_dir):
        """LOG_LEVEL_ERROR should only log errors."""
        write_config(temp_log_dir["config_path"], LOG_LEVEL_ERROR)

        log_error("test", "error message")
        log_warning("test", "warning message")
        log_info("test", "info message")
        log_debug("test", "debug message")

        lines = read_log_lines(temp_log_dir["log_path"])
        assert len(lines) == 1
        assert "[ERROR]" in lines[0]
        assert "error message" in lines[0]

    def test_warning_level_writes_errors_and_warnings(self, temp_log_dir):
        """LOG_LEVEL_WARNING should log warnings and errors."""
        write_config(temp_log_dir["config_path"], LOG_LEVEL_WARNING)

        log_error("test", "error message")
        log_warning("test", "warning message")
        log_info("test", "info message")
        log_debug("test", "debug message")

        lines = read_log_lines(temp_log_dir["log_path"])
        assert len(lines) == 2
        assert "[ERROR]" in lines[0]
        assert "[WARNING]" in lines[1]

    def test_info_level_writes_info_warnings_errors(self, temp_log_dir):
        """LOG_LEVEL_INFO should log info, warnings, and errors."""
        write_config(temp_log_dir["config_path"], LOG_LEVEL_INFO)

        log_error("test", "error message")
        log_warning("test", "warning message")
        log_info("test", "info message")
        log_debug("test", "debug message")

        lines = read_log_lines(temp_log_dir["log_path"])
        assert len(lines) == 3
        assert "[ERROR]" in lines[0]
        assert "[WARNING]" in lines[1]
        assert "[INFO]" in lines[2]

    def test_debug_level_writes_all_messages(self, temp_log_dir):
        """LOG_LEVEL_DEBUG should log all messages."""
        write_config(temp_log_dir["config_path"], LOG_LEVEL_DEBUG)

        log_error("test", "error message")
        log_warning("test", "warning message")
        log_info("test", "info message")
        log_debug("test", "debug message")

        lines = read_log_lines(temp_log_dir["log_path"])
        assert len(lines) == 4
        assert "[ERROR]" in lines[0]
        assert "[WARNING]" in lines[1]
        assert "[INFO]" in lines[2]
        assert "[DEBUG]" in lines[3]


class TestLogFormat:
    """Test log message format."""

    def test_includes_timestamp(self, temp_log_dir):
        """Log entries should include ISO timestamp."""
        write_config(temp_log_dir["config_path"], LOG_LEVEL_DEBUG)

        log_info("test", "message")

        lines = read_log_lines(temp_log_dir["log_path"])
        assert len(lines) == 1

        # Should start with [YYYY-MM-DD HH:MM:SS]
        assert lines[0].startswith("[")
        timestamp_end = lines[0].find("]")
        timestamp_str = lines[0][1:timestamp_end]

        # Should parse as datetime
        datetime.fromisoformat(timestamp_str)

    def test_includes_log_level(self, temp_log_dir):
        """Log entries should include log level."""
        write_config(temp_log_dir["config_path"], LOG_LEVEL_DEBUG)

        log_error("test", "message")
        log_warning("test", "message")
        log_info("test", "message")
        log_debug("test", "message")

        lines = read_log_lines(temp_log_dir["log_path"])
        assert "[ERROR]" in lines[0]
        assert "[WARNING]" in lines[1]
        assert "[INFO]" in lines[2]
        assert "[DEBUG]" in lines[3]

    def test_includes_component(self, temp_log_dir):
        """Log entries should include component name."""
        write_config(temp_log_dir["config_path"], LOG_LEVEL_DEBUG)

        log_info("hook", "message from hook")
        log_info("api_client", "message from API")

        lines = read_log_lines(temp_log_dir["log_path"])
        assert "[hook]" in lines[0]
        assert "[api_client]" in lines[1]

    def test_includes_message(self, temp_log_dir):
        """Log entries should include the log message."""
        write_config(temp_log_dir["config_path"], LOG_LEVEL_DEBUG)

        log_info("test", "this is the message")

        lines = read_log_lines(temp_log_dir["log_path"])
        assert "this is the message" in lines[0]

    def test_includes_exception_when_provided(self, temp_log_dir):
        """Log entries should include exception details when provided."""
        write_config(temp_log_dir["config_path"], LOG_LEVEL_ERROR)

        try:
            raise ValueError("test error")
        except ValueError as e:
            log_error("test", "operation failed", exc=e)

        lines = read_log_lines(temp_log_dir["log_path"])
        assert "operation failed" in lines[0]
        assert "Exception: ValueError: test error" in lines[0]


class TestLogFunctions:
    """Test convenience log functions."""

    def test_log_error_writes_error_level(self, temp_log_dir):
        """log_error() should write ERROR level."""
        write_config(temp_log_dir["config_path"], LOG_LEVEL_ERROR)

        log_error("test", "error message")

        lines = read_log_lines(temp_log_dir["log_path"])
        assert len(lines) == 1
        assert "[ERROR]" in lines[0]

    def test_log_warning_writes_warning_level(self, temp_log_dir):
        """log_warning() should write WARNING level."""
        write_config(temp_log_dir["config_path"], LOG_LEVEL_WARNING)

        log_warning("test", "warning message")

        lines = read_log_lines(temp_log_dir["log_path"])
        assert len(lines) == 1
        assert "[WARNING]" in lines[0]

    def test_log_info_writes_info_level(self, temp_log_dir):
        """log_info() should write INFO level."""
        write_config(temp_log_dir["config_path"], LOG_LEVEL_INFO)

        log_info("test", "info message")

        lines = read_log_lines(temp_log_dir["log_path"])
        assert len(lines) == 1
        assert "[INFO]" in lines[0]

    def test_log_debug_writes_debug_level(self, temp_log_dir):
        """log_debug() should write DEBUG level."""
        write_config(temp_log_dir["config_path"], LOG_LEVEL_DEBUG)

        log_debug("test", "debug message")

        lines = read_log_lines(temp_log_dir["log_path"])
        assert len(lines) == 1
        assert "[DEBUG]" in lines[0]


class TestLogRobustness:
    """Test logging robustness and error handling."""

    def test_logging_never_crashes_on_write_error(self, temp_log_dir, monkeypatch):
        """Logging should never crash even if file write fails."""
        write_config(temp_log_dir["config_path"], LOG_LEVEL_DEBUG)

        # Monkeypatch open() to raise exception
        original_open = open

        def failing_open(*args, **kwargs):
            if "pace-maker.log" in str(args[0]):
                raise OSError("Disk full")
            return original_open(*args, **kwargs)

        monkeypatch.setattr("builtins.open", failing_open)

        # Should not raise
        log_info("test", "message")

    def test_logging_appends_to_existing_file(self, temp_log_dir):
        """Logging should append to existing log file."""
        write_config(temp_log_dir["config_path"], LOG_LEVEL_DEBUG)

        log_info("test", "first message")
        log_info("test", "second message")

        lines = read_log_lines(temp_log_dir["log_path"])
        assert len(lines) == 2
        assert "first message" in lines[0]
        assert "second message" in lines[1]

    def test_multiple_components_log_to_same_file(self, temp_log_dir):
        """Multiple components should write to the same log file."""
        write_config(temp_log_dir["config_path"], LOG_LEVEL_DEBUG)

        log_info("hook", "hook message")
        log_info("api_client", "api message")
        log_info("pacing_engine", "pacing message")

        lines = read_log_lines(temp_log_dir["log_path"])
        assert len(lines) == 3
        assert "[hook]" in lines[0]
        assert "[api_client]" in lines[1]
        assert "[pacing_engine]" in lines[2]
