#!/usr/bin/env python3
"""
Tests for Langfuse orchestrator field-level truncation.

Tests that _truncate_field() in orchestrator.py truncates large fields
before creating spans, preventing oversized payloads from reaching
the Langfuse API.

TDD: These tests are written FIRST - production code comes after.
"""

import unittest

from pacemaker.langfuse.orchestrator import (
    _truncate_field,
    MAX_FIELD_SIZE_CHARS,
)


class TestMaxFieldSizeConstant(unittest.TestCase):
    """Test the MAX_FIELD_SIZE_CHARS constant."""

    def test_constant_exists_and_is_100k(self):
        """MAX_FIELD_SIZE_CHARS should be 100,000 (generous but safe limit)."""
        self.assertEqual(MAX_FIELD_SIZE_CHARS, 100_000)


class TestTruncateFieldStrings(unittest.TestCase):
    """Test _truncate_field() with string inputs."""

    def test_short_string_unchanged(self):
        """Strings under the limit should pass through unchanged."""
        value = "This is a short string"
        result = _truncate_field(value)
        self.assertEqual(result, value)

    def test_string_at_exact_limit_unchanged(self):
        """String exactly at the limit should not be truncated."""
        value = "x" * MAX_FIELD_SIZE_CHARS
        result = _truncate_field(value)
        self.assertEqual(result, value)

    def test_string_over_limit_truncated(self):
        """String over 100K chars should be truncated with marker."""
        value = "x" * 150_000  # 150K chars, over the 100K limit
        result = _truncate_field(value)

        # Result should be shorter than original
        self.assertLess(len(result), len(value))

        # Result should contain truncation marker
        self.assertIn("[TRUNCATED", result)
        self.assertIn("original size: 150000 chars", result)
        self.assertIn("limit:", result)

    def test_custom_max_chars(self):
        """_truncate_field should accept a custom max_chars parameter."""
        value = "x" * 500
        result = _truncate_field(value, max_chars=100)

        self.assertLess(len(result), 500)
        self.assertIn("[TRUNCATED", result)
        self.assertIn("original size: 500 chars", result)

    def test_truncation_preserves_beginning_of_string(self):
        """Truncated strings should preserve the beginning content."""
        value = "IMPORTANT_PREFIX_" + "x" * 200_000
        result = _truncate_field(value, max_chars=1000)

        # The beginning should be preserved
        self.assertTrue(result.startswith("IMPORTANT_PREFIX_"))

    def test_empty_string_unchanged(self):
        """Empty strings should pass through unchanged."""
        result = _truncate_field("")
        self.assertEqual(result, "")

    def test_none_value_unchanged(self):
        """None values should pass through unchanged."""
        result = _truncate_field(None)
        self.assertIsNone(result)


class TestTruncateFieldDicts(unittest.TestCase):
    """Test _truncate_field() with dict inputs."""

    def test_small_dict_unchanged(self):
        """Small dicts should pass through unchanged."""
        value = {"key": "value", "count": 42}
        result = _truncate_field(value)
        self.assertEqual(result, value)

    def test_large_dict_serialized_and_truncated(self):
        """Large dicts should be serialized to JSON string and truncated."""
        # Create a dict that serializes to > 100K chars
        value = {"data": "x" * 150_000}
        result = _truncate_field(value)

        # Result should be a string (serialized + truncated)
        self.assertIsInstance(result, str)
        self.assertIn("[TRUNCATED", result)
        self.assertIn("original size:", result)

    def test_small_dict_stays_as_dict(self):
        """Small dicts should remain as dict type, not be converted to string."""
        value = {"command": "ls -la", "path": "/home/user"}
        result = _truncate_field(value)

        self.assertIsInstance(result, dict)
        self.assertEqual(result["command"], "ls -la")


class TestTruncateFieldLists(unittest.TestCase):
    """Test _truncate_field() with list inputs."""

    def test_small_list_unchanged(self):
        """Small lists should pass through unchanged."""
        value = [1, 2, 3, "hello"]
        result = _truncate_field(value)
        self.assertEqual(result, value)

    def test_large_list_serialized_and_truncated(self):
        """Large lists should be serialized to JSON string and truncated."""
        value = ["x" * 50_000 for _ in range(5)]  # 250K chars total
        result = _truncate_field(value)

        self.assertIsInstance(result, str)
        self.assertIn("[TRUNCATED", result)

    def test_small_list_stays_as_list(self):
        """Small lists should remain as list type."""
        value = ["item1", "item2"]
        result = _truncate_field(value)

        self.assertIsInstance(result, list)


class TestTruncateFieldEdgeCases(unittest.TestCase):
    """Test edge cases for _truncate_field()."""

    def test_integer_value_unchanged(self):
        """Integer values should pass through unchanged."""
        result = _truncate_field(42)
        self.assertEqual(result, 42)

    def test_boolean_value_unchanged(self):
        """Boolean values should pass through unchanged."""
        result = _truncate_field(True)
        self.assertEqual(result, True)

    def test_float_value_unchanged(self):
        """Float values should pass through unchanged."""
        result = _truncate_field(3.14)
        self.assertEqual(result, 3.14)

    def test_truncation_marker_format(self):
        """Truncation marker must follow the exact format specified."""
        value = "x" * 200_000
        result = _truncate_field(value, max_chars=1000)

        # Must end with the truncation marker pattern
        self.assertIn(
            "\n\n... [TRUNCATED - original size: 200000 chars, limit: 1000 chars]",
            result,
        )


if __name__ == "__main__":
    unittest.main()
