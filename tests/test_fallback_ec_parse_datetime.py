#!/usr/bin/env python3
"""
Edge-case tests for parse_api_datetime() in fallback.py.

Part 1 of 7 split edge-case test suite for the fallback pacing system.
Tests adversarial inputs, boundary conditions, and type-safety for ISO 8601
datetime parsing used throughout the fallback state machine.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pacemaker.fallback import parse_api_datetime


class TestParseApiDatetimeEdgeCases:
    """Exhaustive edge cases for parse_api_datetime()."""

    def test_none_returns_none(self):
        assert parse_api_datetime(None) is None

    def test_empty_string_returns_none(self):
        assert parse_api_datetime("") is None

    def test_whitespace_only_returns_none(self):
        assert parse_api_datetime("   ") is None
        assert parse_api_datetime("\t\n") is None

    def test_valid_utc_offset_format(self):
        result = parse_api_datetime("2026-03-06T15:00:00+00:00")
        assert result == datetime(2026, 3, 6, 15, 0, 0)

    def test_valid_z_suffix_format(self):
        result = parse_api_datetime("2026-03-06T15:00:00Z")
        assert result == datetime(2026, 3, 6, 15, 0, 0)

    def test_valid_plain_iso_no_timezone(self):
        result = parse_api_datetime("2026-03-06T15:00:00")
        assert result == datetime(2026, 3, 6, 15, 0, 0)

    def test_invalid_string_returns_none(self):
        assert parse_api_datetime("not-a-date") is None

    def test_invalid_date_values_returns_none(self):
        # Month 13, day 45 — clearly invalid
        assert parse_api_datetime("2026-13-45T00:00:00") is None

    def test_integer_input_returns_none(self):
        assert parse_api_datetime(12345) is None  # type: ignore[arg-type]

    def test_boolean_input_returns_none(self):
        assert parse_api_datetime(True) is None  # type: ignore[arg-type]
        assert parse_api_datetime(False) is None  # type: ignore[arg-type]

    def test_list_input_returns_none(self):
        assert parse_api_datetime([]) is None  # type: ignore[arg-type]

    def test_microsecond_precision(self):
        result = parse_api_datetime("2026-03-06T15:00:00.123456+00:00")
        assert result == datetime(2026, 3, 6, 15, 0, 0, 123456)

    def test_non_utc_offset_does_not_crash(self):
        # +05:00 offset: our function strips +00:00 only — "+05:00" stays,
        # causing fromisoformat to fail on Python 3.9 (no tz-aware ISO parse).
        # The important contract: it does NOT raise an exception.
        result = parse_api_datetime("2026-03-06T15:00:00+05:00")
        # Result may be None (parse error) or a datetime — either is acceptable
        assert result is None or isinstance(result, datetime)

    def test_date_only_does_not_crash(self):
        # Dates without time component may or may not parse — must not raise.
        result = parse_api_datetime("2026-03-06")
        assert result is None or isinstance(result, datetime)

    def test_returns_naive_datetime_not_aware(self):
        result = parse_api_datetime("2026-03-06T15:00:00+00:00")
        assert result is not None
        assert result.tzinfo is None  # naive, not timezone-aware

    def test_leading_and_trailing_whitespace_stripped(self):
        result = parse_api_datetime("  2026-03-06T15:00:00+00:00  ")
        assert result == datetime(2026, 3, 6, 15, 0, 0)

    def test_midnight_boundary(self):
        result = parse_api_datetime("2026-01-01T00:00:00+00:00")
        assert result == datetime(2026, 1, 1, 0, 0, 0)

    def test_end_of_year_boundary(self):
        result = parse_api_datetime("2026-12-31T23:59:59+00:00")
        assert result == datetime(2026, 12, 31, 23, 59, 59)

    def test_numbers_as_string_returns_none(self):
        assert parse_api_datetime("12345") is None

    def test_partial_datetime_returns_none(self):
        assert parse_api_datetime("2026-03-06T") is None
