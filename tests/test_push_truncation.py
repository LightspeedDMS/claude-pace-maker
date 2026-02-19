#!/usr/bin/env python3
"""
Tests for Langfuse push payload size validation and truncation.

Tests that push_batch_events() truncates oversized payloads before sending
to prevent HTTP 413 (Payload Too Large) errors on Langfuse Cloud which
enforces a 1MB body limit on POST /api/public/ingestion.

TDD: These tests are written FIRST - production code comes after.
"""

import json
import unittest
from unittest.mock import patch, MagicMock

from pacemaker.langfuse.push import (
    push_batch_events,
    _truncate_batch_to_fit,
    MAX_BATCH_PAYLOAD_BYTES,
)


class TestMaxBatchPayloadConstant(unittest.TestCase):
    """Test the MAX_BATCH_PAYLOAD_BYTES constant exists and has correct value."""

    def test_constant_exists_and_is_900kb(self):
        """MAX_BATCH_PAYLOAD_BYTES should be 900,000 (safely under 1MB Cloud limit)."""
        self.assertEqual(MAX_BATCH_PAYLOAD_BYTES, 900_000)


class TestTruncateBatchToFit(unittest.TestCase):
    """Test the _truncate_batch_to_fit() helper function."""

    def test_small_payload_unchanged(self):
        """Payloads under the limit should pass through unchanged."""
        payload = {
            "batch": [
                {
                    "id": "event-1",
                    "type": "span-create",
                    "body": {
                        "input": "short input",
                        "output": "short output",
                    },
                }
            ]
        }
        result = _truncate_batch_to_fit(payload, MAX_BATCH_PAYLOAD_BYTES)

        # Values should be identical - no truncation
        self.assertEqual(result["batch"][0]["body"]["input"], "short input")
        self.assertEqual(result["batch"][0]["body"]["output"], "short output")

    def test_oversized_output_gets_truncated(self):
        """A batch with a 2MB output field should be truncated to fit."""
        large_output = "x" * 2_000_000  # 2MB string
        payload = {
            "batch": [
                {
                    "id": "event-1",
                    "type": "span-create",
                    "body": {
                        "input": "small input",
                        "output": large_output,
                    },
                }
            ]
        }
        result = _truncate_batch_to_fit(payload, MAX_BATCH_PAYLOAD_BYTES)

        # The serialized result must be under the limit
        serialized_size = len(json.dumps(result).encode("utf-8"))
        self.assertLessEqual(serialized_size, MAX_BATCH_PAYLOAD_BYTES)

        # The output should contain the truncation marker
        truncated_output = result["batch"][0]["body"]["output"]
        self.assertIn("[TRUNCATED", truncated_output)
        self.assertIn("original size:", truncated_output)

    def test_oversized_input_gets_truncated(self):
        """A batch with a large input field should be truncated."""
        large_input = "y" * 1_500_000  # 1.5MB string
        payload = {
            "batch": [
                {
                    "id": "event-1",
                    "type": "span-create",
                    "body": {
                        "input": large_input,
                        "output": "small output",
                    },
                }
            ]
        }
        result = _truncate_batch_to_fit(payload, MAX_BATCH_PAYLOAD_BYTES)

        serialized_size = len(json.dumps(result).encode("utf-8"))
        self.assertLessEqual(serialized_size, MAX_BATCH_PAYLOAD_BYTES)

        truncated_input = result["batch"][0]["body"]["input"]
        self.assertIn("[TRUNCATED", truncated_input)

    def test_oversized_text_field_gets_truncated(self):
        """A batch with a large 'text' field should be truncated."""
        large_text = "z" * 1_500_000
        payload = {
            "batch": [
                {
                    "id": "event-1",
                    "type": "span-create",
                    "body": {
                        "text": large_text,
                    },
                }
            ]
        }
        result = _truncate_batch_to_fit(payload, MAX_BATCH_PAYLOAD_BYTES)

        serialized_size = len(json.dumps(result).encode("utf-8"))
        self.assertLessEqual(serialized_size, MAX_BATCH_PAYLOAD_BYTES)

        truncated_text = result["batch"][0]["body"]["text"]
        self.assertIn("[TRUNCATED", truncated_text)

    def test_multiple_large_fields_all_truncated(self):
        """When both input and output are large, both should be truncated."""
        payload = {
            "batch": [
                {
                    "id": "event-1",
                    "type": "span-create",
                    "body": {
                        "input": "a" * 600_000,
                        "output": "b" * 600_000,
                    },
                }
            ]
        }
        result = _truncate_batch_to_fit(payload, MAX_BATCH_PAYLOAD_BYTES)

        serialized_size = len(json.dumps(result).encode("utf-8"))
        self.assertLessEqual(serialized_size, MAX_BATCH_PAYLOAD_BYTES)

    def test_largest_field_truncated_first(self):
        """Progressive truncation should target the largest field first."""
        payload = {
            "batch": [
                {
                    "id": "event-1",
                    "type": "span-create",
                    "body": {
                        "input": "small",  # 5 chars
                        "output": "x" * 1_500_000,  # 1.5MB - largest
                    },
                }
            ]
        }
        result = _truncate_batch_to_fit(payload, MAX_BATCH_PAYLOAD_BYTES)

        # Small input should remain unchanged
        self.assertEqual(result["batch"][0]["body"]["input"], "small")

        # Large output should be truncated
        self.assertIn("[TRUNCATED", result["batch"][0]["body"]["output"])

    def test_truncation_marker_format(self):
        """Truncation marker must include original size and limit."""
        large_output = "x" * 2_000_000
        payload = {
            "batch": [
                {
                    "id": "event-1",
                    "type": "span-create",
                    "body": {
                        "output": large_output,
                    },
                }
            ]
        }
        result = _truncate_batch_to_fit(payload, MAX_BATCH_PAYLOAD_BYTES)

        truncated = result["batch"][0]["body"]["output"]
        # Must contain the specific marker format
        self.assertIn("... [TRUNCATED - original size: 2000000 chars", truncated)
        self.assertIn("limit:", truncated)

    def test_non_string_fields_not_touched(self):
        """Non-string fields like dicts and ints should not be affected."""
        payload = {
            "batch": [
                {
                    "id": "event-1",
                    "type": "span-create",
                    "body": {
                        "input": {"key": "value"},  # dict, not string
                        "output": "x" * 1_500_000,  # string, should be truncated
                        "metadata": {"count": 42},  # dict, not string
                    },
                }
            ]
        }
        result = _truncate_batch_to_fit(payload, MAX_BATCH_PAYLOAD_BYTES)

        # Dict fields should be preserved
        self.assertEqual(result["batch"][0]["body"]["input"], {"key": "value"})
        self.assertEqual(result["batch"][0]["body"]["metadata"], {"count": 42})

    def test_multiple_events_in_batch(self):
        """Truncation should work across multiple events in a batch."""
        payload = {
            "batch": [
                {
                    "id": "event-1",
                    "type": "span-create",
                    "body": {
                        "output": "a" * 500_000,
                    },
                },
                {
                    "id": "event-2",
                    "type": "span-create",
                    "body": {
                        "output": "b" * 500_000,
                    },
                },
            ]
        }
        result = _truncate_batch_to_fit(payload, MAX_BATCH_PAYLOAD_BYTES)

        serialized_size = len(json.dumps(result).encode("utf-8"))
        self.assertLessEqual(serialized_size, MAX_BATCH_PAYLOAD_BYTES)


class TestPushBatchEventsWithTruncation(unittest.TestCase):
    """Test that push_batch_events integrates truncation before sending."""

    def setUp(self):
        self.base_url = "https://cloud.langfuse.com"
        self.public_key = "pk-lf-test"
        self.secret_key = "sk-lf-test"

    @patch("pacemaker.langfuse.push.requests.post")
    def test_oversized_batch_is_truncated_before_send(self, mock_post):
        """push_batch_events should truncate oversized payloads before HTTP POST."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "successes": [{"id": "event-1"}],
            "errors": [],
        }
        mock_post.return_value = mock_response

        # Create a batch with a 2MB output field
        large_batch = [
            {
                "id": "event-1",
                "type": "span-create",
                "body": {
                    "output": "x" * 2_000_000,
                },
            }
        ]

        success, count = push_batch_events(
            self.base_url, self.public_key, self.secret_key, large_batch
        )

        self.assertTrue(success)

        # Verify the payload that was actually sent is under the limit
        call_args = mock_post.call_args
        sent_payload = call_args[1]["json"]
        sent_size = len(json.dumps(sent_payload).encode("utf-8"))
        self.assertLessEqual(sent_size, MAX_BATCH_PAYLOAD_BYTES)

    @patch("pacemaker.langfuse.push.requests.post")
    def test_small_batch_sent_unchanged(self, mock_post):
        """push_batch_events should not modify payloads under the size limit."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "successes": [{"id": "event-1"}],
            "errors": [],
        }
        mock_post.return_value = mock_response

        small_batch = [
            {
                "id": "event-1",
                "type": "span-create",
                "body": {
                    "output": "small output",
                },
            }
        ]

        success, count = push_batch_events(
            self.base_url, self.public_key, self.secret_key, small_batch
        )

        self.assertTrue(success)

        # Verify the payload was sent with original content
        call_args = mock_post.call_args
        sent_payload = call_args[1]["json"]
        self.assertEqual(sent_payload["batch"][0]["body"]["output"], "small output")


if __name__ == "__main__":
    unittest.main()
