#!/usr/bin/env python3
"""
Unit tests for Langfuse transformer (AC4).

Tests:
- AC4: Trace creation with correct structure
- session_id becomes trace_id
- user_id from OAuth email
- Token usage (input, output, cache)
- Tool calls metadata
- Timestamp handling (provided vs auto-generated)
- Cache tokens handling
"""

import unittest
from datetime import datetime

from src.pacemaker.langfuse.transformer import create_trace


class TestLangfuseTransformer(unittest.TestCase):
    """Test Langfuse trace transformation"""

    def setUp(self):
        """Set up test fixtures."""
        self.session_id = "test-session-abc123"
        self.user_id = "developer@example.com"
        self.model = "claude-sonnet-4-5-20250929"
        self.token_usage = {
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_read_tokens": 200,
        }
        self.tool_calls = ["Read", "Write", "Bash", "Grep"]

    def test_create_trace_basic_structure(self):
        """Test trace has required fields"""
        # Act
        trace = create_trace(
            self.session_id,
            self.user_id,
            self.model,
            self.token_usage,
            self.tool_calls,
        )

        # Assert - required top-level fields
        self.assertIn("id", trace)
        self.assertIn("name", trace)
        self.assertIn("userId", trace)
        self.assertIn("metadata", trace)
        self.assertIn("usage", trace)
        self.assertIn("timestamp", trace)

    def test_create_trace_session_id_becomes_trace_id(self):
        """AC4: session_id becomes trace_id"""
        # Act
        trace = create_trace(
            self.session_id,
            self.user_id,
            self.model,
            self.token_usage,
            self.tool_calls,
        )

        # Assert
        self.assertEqual(trace["id"], self.session_id)

    def test_create_trace_user_id_from_oauth(self):
        """AC4: user_id from OAuth profile email"""
        # Act
        trace = create_trace(
            self.session_id,
            self.user_id,
            self.model,
            self.token_usage,
            self.tool_calls,
        )

        # Assert
        self.assertEqual(trace["userId"], self.user_id)

    def test_create_trace_user_id_unknown_when_none(self):
        """Test user_id defaults to 'unknown' when None"""
        # Act
        trace = create_trace(
            self.session_id,
            None,  # No user_id
            self.model,
            self.token_usage,
            self.tool_calls,
        )

        # Assert
        self.assertEqual(trace["userId"], "unknown")

    def test_create_trace_name_format(self):
        """Test trace name format includes session prefix"""
        # Act
        trace = create_trace(
            self.session_id,
            self.user_id,
            self.model,
            self.token_usage,
            self.tool_calls,
        )

        # Assert
        self.assertTrue(trace["name"].startswith("claude-code-session-"))
        # Should include first 8 chars of session_id
        self.assertIn(self.session_id[:8], trace["name"])

    def test_create_trace_metadata_model(self):
        """AC4: metadata includes model"""
        # Act
        trace = create_trace(
            self.session_id,
            self.user_id,
            self.model,
            self.token_usage,
            self.tool_calls,
        )

        # Assert
        self.assertEqual(trace["metadata"]["model"], self.model)

    def test_create_trace_metadata_tool_calls(self):
        """AC4: metadata includes tool_calls list"""
        # Act
        trace = create_trace(
            self.session_id,
            self.user_id,
            self.model,
            self.token_usage,
            self.tool_calls,
        )

        # Assert
        self.assertEqual(trace["metadata"]["tool_calls"], self.tool_calls)

    def test_create_trace_metadata_tool_count(self):
        """Test metadata includes tool_count"""
        # Act
        trace = create_trace(
            self.session_id,
            self.user_id,
            self.model,
            self.token_usage,
            self.tool_calls,
        )

        # Assert
        self.assertEqual(trace["metadata"]["tool_count"], len(self.tool_calls))
        self.assertEqual(trace["metadata"]["tool_count"], 4)

    def test_create_trace_usage_input_tokens(self):
        """AC4: usage includes input tokens"""
        # Act
        trace = create_trace(
            self.session_id,
            self.user_id,
            self.model,
            self.token_usage,
            self.tool_calls,
        )

        # Assert
        self.assertEqual(trace["usage"]["input"], 1000)

    def test_create_trace_usage_output_tokens(self):
        """AC4: usage includes output tokens"""
        # Act
        trace = create_trace(
            self.session_id,
            self.user_id,
            self.model,
            self.token_usage,
            self.tool_calls,
        )

        # Assert
        self.assertEqual(trace["usage"]["output"], 500)

    def test_create_trace_usage_total_calculated(self):
        """Test usage total is sum of input + output"""
        # Act
        trace = create_trace(
            self.session_id,
            self.user_id,
            self.model,
            self.token_usage,
            self.tool_calls,
        )

        # Assert
        self.assertEqual(trace["usage"]["total"], 1500)
        self.assertEqual(
            trace["usage"]["total"],
            trace["usage"]["input"] + trace["usage"]["output"],
        )

    def test_create_trace_usage_cache_tokens_included(self):
        """AC4: usage includes cache_read_tokens when present"""
        # Act
        trace = create_trace(
            self.session_id,
            self.user_id,
            self.model,
            self.token_usage,
            self.tool_calls,
        )

        # Assert
        self.assertEqual(trace["usage"]["cache_read"], 200)

    def test_create_trace_usage_cache_tokens_omitted_when_zero(self):
        """Test cache_read not included when zero"""
        # Arrange
        token_usage_no_cache = {
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_read_tokens": 0,
        }

        # Act
        trace = create_trace(
            self.session_id,
            self.user_id,
            self.model,
            token_usage_no_cache,
            self.tool_calls,
        )

        # Assert
        self.assertNotIn("cache_read", trace["usage"])

    def test_create_trace_usage_cache_tokens_omitted_when_missing(self):
        """Test cache_read not included when key missing"""
        # Arrange
        token_usage_no_cache = {
            "input_tokens": 1000,
            "output_tokens": 500,
        }

        # Act
        trace = create_trace(
            self.session_id,
            self.user_id,
            self.model,
            token_usage_no_cache,
            self.tool_calls,
        )

        # Assert
        self.assertNotIn("cache_read", trace["usage"])

    def test_create_trace_timestamp_provided(self):
        """Test provided timestamp is used"""
        # Arrange
        custom_timestamp = "2024-02-03T10:30:00.000Z"

        # Act
        trace = create_trace(
            self.session_id,
            self.user_id,
            self.model,
            self.token_usage,
            self.tool_calls,
            timestamp=custom_timestamp,
        )

        # Assert
        self.assertEqual(trace["timestamp"], custom_timestamp)

    def test_create_trace_timestamp_auto_generated(self):
        """Test timestamp is auto-generated when not provided"""
        from datetime import timezone

        # Act - use timezone-aware datetimes for comparison
        before = datetime.now(timezone.utc)
        trace = create_trace(
            self.session_id,
            self.user_id,
            self.model,
            self.token_usage,
            self.tool_calls,
        )
        after = datetime.now(timezone.utc)

        # Assert
        self.assertIn("timestamp", trace)
        self.assertIsInstance(trace["timestamp"], str)

        # Parse and verify it's between before and after
        trace_time = datetime.fromisoformat(trace["timestamp"].replace("Z", "+00:00"))
        # Allow small time difference for test execution
        self.assertLessEqual((trace_time - before).total_seconds(), 1.0)
        self.assertGreaterEqual((trace_time - after).total_seconds(), -1.0)

    def test_create_trace_timestamp_iso_format(self):
        """Test auto-generated timestamp is ISO format"""
        # Act
        trace = create_trace(
            self.session_id,
            self.user_id,
            self.model,
            self.token_usage,
            self.tool_calls,
        )

        # Assert - should be parseable as ISO format
        timestamp = trace["timestamp"]
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        self.assertIsInstance(parsed, datetime)

    def test_create_trace_empty_tool_calls(self):
        """Test trace with no tool calls"""
        # Act
        trace = create_trace(
            self.session_id,
            self.user_id,
            self.model,
            self.token_usage,
            [],  # No tool calls
        )

        # Assert
        self.assertEqual(trace["metadata"]["tool_calls"], [])
        self.assertEqual(trace["metadata"]["tool_count"], 0)

    def test_create_trace_missing_token_fields_default_zero(self):
        """Test missing token fields default to 0"""
        # Arrange
        incomplete_usage = {}  # No fields

        # Act
        trace = create_trace(
            self.session_id,
            self.user_id,
            self.model,
            incomplete_usage,
            self.tool_calls,
        )

        # Assert
        self.assertEqual(trace["usage"]["input"], 0)
        self.assertEqual(trace["usage"]["output"], 0)
        self.assertEqual(trace["usage"]["total"], 0)

    def test_create_trace_single_tool_call(self):
        """Test trace with single tool call"""
        # Act
        trace = create_trace(
            self.session_id,
            self.user_id,
            self.model,
            self.token_usage,
            ["Read"],
        )

        # Assert
        self.assertEqual(trace["metadata"]["tool_calls"], ["Read"])
        self.assertEqual(trace["metadata"]["tool_count"], 1)

    def test_create_trace_many_tool_calls(self):
        """Test trace with many tool calls"""
        # Arrange
        many_tools = ["Read"] * 10 + ["Write"] * 5 + ["Bash"] * 3

        # Act
        trace = create_trace(
            self.session_id,
            self.user_id,
            self.model,
            self.token_usage,
            many_tools,
        )

        # Assert
        self.assertEqual(trace["metadata"]["tool_calls"], many_tools)
        self.assertEqual(trace["metadata"]["tool_count"], 18)


if __name__ == "__main__":
    unittest.main()
