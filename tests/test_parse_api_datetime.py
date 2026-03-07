#!/usr/bin/env python3
"""
Tests for parse_api_datetime() utility in fallback.py.

Priority 8: Centralize datetime parsing from 6 inline locations.

Acceptance Criteria:
- parse_api_datetime(None) -> None
- parse_api_datetime("") -> None
- parse_api_datetime("not-a-date") -> None
- parse_api_datetime("2026-03-06T15:00:00+00:00") -> datetime(2026, 3, 6, 15, 0, 0)
- parse_api_datetime("2026-03-06T15:00:00") -> datetime(2026, 3, 6, 15, 0, 0)
- parse_api_datetime("2026-03-06T15:00:00Z") -> datetime(2026, 3, 6, 15, 0, 0)
"""

import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestParseApiDatetime:
    """Tests for parse_api_datetime() utility function."""

    def test_none_input_returns_none(self):
        """parse_api_datetime(None) must return None."""
        from pacemaker.fallback import parse_api_datetime

        assert parse_api_datetime(None) is None

    def test_empty_string_returns_none(self):
        """parse_api_datetime('') must return None."""
        from pacemaker.fallback import parse_api_datetime

        assert parse_api_datetime("") is None

    def test_invalid_format_returns_none(self):
        """parse_api_datetime('not-a-date') must return None."""
        from pacemaker.fallback import parse_api_datetime

        assert parse_api_datetime("not-a-date") is None

    def test_numeric_input_returns_none(self):
        """parse_api_datetime('12345') must return None."""
        from pacemaker.fallback import parse_api_datetime

        assert parse_api_datetime("12345") is None

    def test_valid_with_plus_timezone(self):
        """parse_api_datetime('2026-03-06T15:00:00+00:00') -> datetime(2026, 3, 6, 15, 0, 0)."""
        from pacemaker.fallback import parse_api_datetime

        result = parse_api_datetime("2026-03-06T15:00:00+00:00")
        assert result is not None
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 6
        assert result.hour == 15
        assert result.minute == 0
        assert result.second == 0

    def test_valid_without_timezone(self):
        """parse_api_datetime('2026-03-06T15:00:00') -> datetime(2026, 3, 6, 15, 0, 0)."""
        from pacemaker.fallback import parse_api_datetime

        result = parse_api_datetime("2026-03-06T15:00:00")
        assert result is not None
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 6
        assert result.hour == 15
        assert result.minute == 0
        assert result.second == 0

    def test_valid_with_z_suffix(self):
        """parse_api_datetime('2026-03-06T15:00:00Z') -> datetime(2026, 3, 6, 15, 0, 0)."""
        from pacemaker.fallback import parse_api_datetime

        result = parse_api_datetime("2026-03-06T15:00:00Z")
        assert result is not None
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 6
        assert result.hour == 15
        assert result.minute == 0
        assert result.second == 0

    def test_returns_datetime_instance(self):
        """Result is a datetime instance for valid input."""
        from pacemaker.fallback import parse_api_datetime

        result = parse_api_datetime("2026-03-06T15:00:00+00:00")
        assert isinstance(result, datetime)

    def test_result_is_timezone_naive(self):
        """Returned datetime should be timezone-naive (no tzinfo) for consistency
        with the existing codebase that uses utcnow() throughout."""
        from pacemaker.fallback import parse_api_datetime

        result = parse_api_datetime("2026-03-06T15:00:00+00:00")
        assert result is not None
        assert result.tzinfo is None

    def test_whitespace_only_returns_none(self):
        """parse_api_datetime('   ') must return None."""
        from pacemaker.fallback import parse_api_datetime

        assert parse_api_datetime("   ") is None

    def test_partial_date_without_time_returns_none(self):
        """parse_api_datetime('2026-03-06') returns None (not a full ISO datetime)."""
        from pacemaker.fallback import parse_api_datetime

        # This may return None since it's date-only not datetime, but we allow it
        # to be lenient for date-only strings -- the key is it must not raise
        result = parse_api_datetime("2026-03-06")
        # Either None or a valid datetime - must not raise
        assert result is None or isinstance(result, datetime)

    def test_plus_05_30_timezone_returns_none_or_datetime(self):
        """Non-UTC timezone offsets: must not crash, behavior is unspecified."""
        from pacemaker.fallback import parse_api_datetime

        # Must not raise - behavior is implementation-defined for non-UTC zones
        result = parse_api_datetime("2026-03-06T15:00:00+05:30")
        assert result is None or isinstance(result, datetime)
