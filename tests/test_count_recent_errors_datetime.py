#!/usr/bin/env python3
"""
Tests for _count_recent_errors() datetime timezone handling.

Bug: Line 478-481 in user_commands.py creates a naive datetime via strptime()
but then compares it to an aware datetime (cutoff_time uses timezone.utc),
which raises TypeError: can't compare offset-naive and offset-aware datetimes.

Fix: Add .replace(tzinfo=timezone.utc) after datetime.strptime().
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pacemaker.user_commands import _count_recent_errors


def _make_log_line(dt: datetime, level: str = "ERROR", msg: str = "Test error") -> str:
    """Create a log line matching the _ERROR_LOG_PATTERN format."""
    # Format: [2026-03-09 14:30:00] [ERROR] message
    ts = dt.strftime("%Y-%m-%d %H:%M:%S")
    return f"[{ts}] [{level}] {msg}\n"


class TestCountRecentErrorsDatetimeBug:
    """
    Tests that expose and verify the fix for the naive/aware datetime TypeError
    in _count_recent_errors().
    """

    def test_no_typeerror_when_log_contains_errors(self, tmp_path):
        """
        Regression test: _count_recent_errors() must NOT raise TypeError
        when log files contain ERROR entries.

        Before fix: raises TypeError: can't compare offset-naive and
        offset-aware datetimes.
        After fix: returns integer count without error.
        """
        now_utc = datetime.now(timezone.utc)
        recent = now_utc - timedelta(hours=1)

        # Use today's date in filename to match get_recent_log_paths() pattern
        today_str = datetime.now().strftime("%Y-%m-%d")
        log_file = tmp_path / f"pace-maker-{today_str}.log"
        log_file.write_text(_make_log_line(recent, "ERROR", "Something went wrong"))

        # Must not raise TypeError
        result = _count_recent_errors(hours=24, log_dir=str(tmp_path))
        assert isinstance(result, int)

    def test_counts_errors_within_window(self, tmp_path):
        """
        Errors with timestamps within the look-back window must be counted.
        """
        now_utc = datetime.now(timezone.utc)
        one_hour_ago = now_utc - timedelta(hours=1)

        today_str = datetime.now().strftime("%Y-%m-%d")
        log_file = tmp_path / f"pace-maker-{today_str}.log"
        log_file.write_text(
            _make_log_line(one_hour_ago, "ERROR", "Error within window")
            + _make_log_line(
                one_hour_ago - timedelta(minutes=30), "ERROR", "Another error"
            )
        )

        result = _count_recent_errors(hours=24, log_dir=str(tmp_path))
        assert result == 2

    def test_does_not_count_errors_outside_window(self, tmp_path):
        """
        Errors with timestamps older than the look-back window must NOT be counted.
        """
        now_utc = datetime.now(timezone.utc)
        two_days_ago = now_utc - timedelta(hours=49)

        today_str = datetime.now().strftime("%Y-%m-%d")
        log_file = tmp_path / f"pace-maker-{today_str}.log"
        log_file.write_text(
            _make_log_line(two_days_ago, "ERROR", "Old error outside window")
        )

        result = _count_recent_errors(hours=24, log_dir=str(tmp_path))
        assert result == 0

    def test_does_not_count_non_error_log_lines(self, tmp_path):
        """
        Lines with INFO or WARNING level must NOT be counted.
        """
        now_utc = datetime.now(timezone.utc)
        recent = now_utc - timedelta(hours=1)

        today_str = datetime.now().strftime("%Y-%m-%d")
        log_file = tmp_path / f"pace-maker-{today_str}.log"
        log_file.write_text(
            _make_log_line(recent, "INFO", "Info message")
            + _make_log_line(recent, "WARNING", "Warning message")
            + _make_log_line(recent, "DEBUG", "Debug message")
        )

        result = _count_recent_errors(hours=24, log_dir=str(tmp_path))
        assert result == 0

    def test_empty_log_directory_returns_zero(self, tmp_path):
        """
        An empty log directory (no log files) must return 0.
        """
        result = _count_recent_errors(hours=24, log_dir=str(tmp_path))
        assert result == 0

    def test_empty_log_file_returns_zero(self, tmp_path):
        """
        A log file that exists but is empty must return 0.
        """
        today_str = datetime.now().strftime("%Y-%m-%d")
        log_file = tmp_path / f"pace-maker-{today_str}.log"
        log_file.write_text("")

        result = _count_recent_errors(hours=24, log_dir=str(tmp_path))
        assert result == 0

    def test_mixed_within_and_outside_window(self, tmp_path):
        """
        Only errors within the look-back window are counted; older ones are ignored.
        """
        now_utc = datetime.now(timezone.utc)
        recent = now_utc - timedelta(hours=2)
        old = now_utc - timedelta(hours=48)

        today_str = datetime.now().strftime("%Y-%m-%d")
        log_file = tmp_path / f"pace-maker-{today_str}.log"
        log_file.write_text(
            _make_log_line(recent, "ERROR", "Recent error")
            + _make_log_line(old, "ERROR", "Old error")
            + _make_log_line(recent, "ERROR", "Another recent error")
        )

        result = _count_recent_errors(hours=24, log_dir=str(tmp_path))
        assert result == 2

    def test_returns_integer_type(self, tmp_path):
        """
        Return type must always be int, never None or a string.
        """
        result = _count_recent_errors(hours=24, log_dir=str(tmp_path))
        assert isinstance(result, int)
