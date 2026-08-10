"""
Unit tests for status display enhancements.

Tests for:
- Version display (Pace Maker and Usage Console)
- Langfuse connectivity status
- 24-hour error count with color coding
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from pacemaker.user_commands import (
    _execute_status,
    _count_recent_errors,
)


class TestErrorCounting:
    """Test error counting from log files.

    Issue #95: log filenames (logger.get_log_path_for_date) and log line
    timestamps (logger.log()) are BOTH written using naive LOCAL time. These
    tests therefore construct filenames and timestamps the same way
    production does -- via plain `datetime.now()` -- instead of
    `datetime.now(timezone.utc)`. Building expected filenames/timestamps
    from a different time base than production causes these tests to fail
    only during the part of the day (or in timezones) where the local date
    and UTC date diverge, which is exactly how issue #95 hid as flakiness.
    """

    def test_count_errors_no_errors(self, tmp_path):
        """Test counting when there are no errors in last 24 hours."""
        # Create log file with no ERROR entries using daily rotation naming.
        # Local time throughout, matching production's logger.py.
        now = datetime.now()
        log_file = tmp_path / f"pace-maker-{now.strftime('%Y-%m-%d')}.log"
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

    def test_count_errors_short_window_counts_entry_written_just_now(self, tmp_path):
        """Regression test for issue #95: a log entry written mere seconds
        ago must be counted under a short (1-hour) window.

        Before the fix, cutoff_time was computed as UTC-aware
        (`datetime.now(timezone.utc)`) while the parsed line timestamp was
        asserted to be UTC even though it was written in local time. On a
        machine with a negative UTC offset (e.g. UTC-5, the reporter's
        environment) the reinterpreted timestamp appears to be 5 hours in
        the past relative to the UTC-aware cutoff, so a genuinely-current
        error falls outside a short window and is undercounted -- silently,
        because the default 24h window usually still absorbs the skew. This
        is exactly the scenario the issue's "hours=1 query ... can report 0
        while errors are actively being logged" describes.
        """
        now = datetime.now()
        log_file = tmp_path / f"pace-maker-{now.strftime('%Y-%m-%d')}.log"
        log_file.write_text(
            f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] [ERROR] [module] "
            f"Error written just now\n"
        )

        count = _count_recent_errors(hours=1, log_dir=str(tmp_path))
        assert count == 1

    def test_count_errors_correct_when_local_date_differs_from_utc_date(
        self, monkeypatch, tmp_path
    ):
        """Regression test for issue #95, pinned with a frozen, non-UTC
        clock so it cannot hide behind time-of-day flakiness.

        Freezes "now" to a fixed LOCAL wall-clock reading whose calendar
        date (2026-08-09) differs from what the SAME instant would be in
        UTC under a hardcoded, machine-independent -5:00 offset
        (2026-08-10) -- i.e. the exact local/UTC date mismatch from the bug
        report, but deterministic regardless of the host's real timezone or
        the real time of day the suite happens to run.

        Both invariants are exercised together:
          - Invariant 1 (file selection, never actually defective in
            production): the test's own filename must match production's
            local-date basis (pace-maker-2026-08-09.log) -- logger.py's
            get_recent_log_paths() always selected files by local date;
            issue #95 was a test-side mismatch (expected filename built
            from a UTC date), not a production file-selection bug.
          - Defect 2 (timestamp parsing): the line timestamp, written in
            local time, must be compared against a cutoff in that SAME
            local time base -- not asserted to be UTC and compared to a
            genuinely UTC cutoff, which would place it ~5 hours further
            into the past than it really is and drop it out of a 1-hour
            window.

        `timezone` (imported at module level) is used here only to build
        the fixture's UTC reading -- production no longer touches
        `timezone.utc` anywhere in `_count_recent_errors`.
        """
        import pacemaker.logger as logger_module

        fixed_utc = datetime(2026, 8, 10, 4, 30, 0, tzinfo=timezone.utc)
        fixed_local_offset = timedelta(hours=-5)
        fixed_local = (fixed_utc + fixed_local_offset).replace(tzinfo=None)
        assert fixed_local.strftime("%Y-%m-%d") == "2026-08-09"
        assert fixed_utc.strftime("%Y-%m-%d") == "2026-08-10"

        class _FixedClockDatetime(datetime):
            """datetime.now() returns a fixed reading; the local reading's
            calendar date deliberately differs from the UTC reading's, via
            a hardcoded offset independent of the host machine's real tz.
            """

            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return fixed_local
                return fixed_utc.astimezone(tz)

        # Patch every place a `datetime` class name is bound and reachable
        # from the code under test:
        #   - the global `datetime` module attribute, picked up by
        #     _count_recent_errors' per-call `from datetime import datetime`
        #   - pacemaker.logger's module-level `datetime` name, bound once
        #     at import time and used by get_recent_log_paths /
        #     get_log_path_for_date for file naming and selection
        monkeypatch.setattr("datetime.datetime", _FixedClockDatetime)
        monkeypatch.setattr(logger_module, "datetime", _FixedClockDatetime)

        # Prove the patch actually reaches every call site this test
        # depends on, rather than trusting it implicitly: a FRESH
        # `from datetime import datetime` -- the exact statement
        # _count_recent_errors executes on every invocation -- must
        # observe the frozen clock, and so must pacemaker.logger's
        # module-level `datetime` binding used by get_recent_log_paths().
        # (This test never calls the file-level `datetime.now()` imported
        # at the top of this test module after patching -- fixed_local /
        # fixed_utc above were computed via explicit constructors before
        # any patch was applied, not via `.now()`.)
        from datetime import datetime as _reimported_datetime

        assert _reimported_datetime.now() == fixed_local
        assert logger_module.datetime.now() == fixed_local

        # Production names/writes the log file using the local reading.
        log_file = tmp_path / f"pace-maker-{fixed_local.strftime('%Y-%m-%d')}.log"
        recent = fixed_local - timedelta(minutes=30)
        log_file.write_text(
            f"[{recent.strftime('%Y-%m-%d %H:%M:%S')}] [ERROR] [module] "
            f"Error 30 minutes before frozen local now\n"
        )

        count = _count_recent_errors(hours=1, log_dir=str(tmp_path))
        assert count == 1

    def test_count_errors_wide_window_spans_multiple_log_files(self, tmp_path):
        """Regression test: get_recent_log_paths() was previously called
        with a hardcoded days=2, which only covers hours<=24. For a wider
        window (hours=48, hours=72) errors that fall inside the requested
        window but land in a daily log file older than 2 days back were
        silently dropped, undercounting -- e.g. with an entry 47h old,
        hours=48 used to return 0 (true answer 1) and hours=72 used to
        return 2 (true answer 3).

        This writes one ERROR entry each at 1h, 47h, and 70h old, each
        landing in the daily log file matching ITS OWN local calendar
        date (mirroring production's per-day rotation), and asserts both
        the hours=48 and hours=72 windows count the correct subset.
        """
        now = datetime.now()
        ages_hours = [1, 47, 70]

        # Group entries by the calendar date they fall on so multiple
        # entries landing on the same day append to the same file.
        entries_by_date: dict = {}
        for age in ages_hours:
            ts = now - timedelta(hours=age)
            date_str = ts.strftime("%Y-%m-%d")
            line = f"[{ts.strftime('%Y-%m-%d %H:%M:%S')}] [ERROR] [module] Error {age}h old\n"
            entries_by_date.setdefault(date_str, []).append(line)

        for date_str, lines in entries_by_date.items():
            log_file = tmp_path / f"pace-maker-{date_str}.log"
            log_file.write_text("".join(lines))

        # hours=48 window: 1h and 47h old entries are inside, 70h is not.
        count_48 = _count_recent_errors(hours=48, log_dir=str(tmp_path))
        assert count_48 == 2, f"expected 2 errors within 48h window, got {count_48}"

        # hours=72 window: all three entries are inside.
        count_72 = _count_recent_errors(hours=72, log_dir=str(tmp_path))
        assert count_72 == 3, f"expected 3 errors within 72h window, got {count_72}"


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
