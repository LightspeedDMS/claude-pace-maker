#!/usr/bin/env python3
"""
Unit tests for batch push error handling (response body validation).

CRITICAL BUG FIX: Langfuse returns HTTP 200 even when items fail.
The actual success/failure is in the response body:
{"successes": [...], "errors": [...]}

These tests verify push_batch_events() correctly:
1. Returns (True, N) where N = len(successes) when items succeed
2. Returns (False, 0) when all items fail
3. Returns (True, N) for partial success
4. Returns (False, 0) with warning when response unparseable
5. Logs errors array when failures occur
"""

import unittest
from unittest.mock import patch, MagicMock
import requests

from pacemaker.langfuse.push import push_batch_events


class TestBatchPushResponseValidation(unittest.TestCase):
    """Test push_batch_events validates response body for actual success/failure"""

    def setUp(self):
        """Set up test fixtures."""
        self.base_url = "https://cloud.langfuse.com"
        self.public_key = "pk-lf-test-123"
        self.secret_key = "sk-lf-test-secret-456"
        self.batch = [
            {
                "id": "trace-123",
                "timestamp": "2024-01-01T00:00:00Z",
                "type": "trace-create",
                "body": {"id": "trace-123", "name": "test-trace"},
            },
            {
                "id": "span-456",
                "timestamp": "2024-01-01T00:00:01Z",
                "type": "span-create",
                "body": {"id": "span-456", "traceId": "trace-123"},
            },
            {
                "id": "span-789",
                "timestamp": "2024-01-01T00:00:02Z",
                "type": "span-create",
                "body": {"id": "span-789", "traceId": "trace-123"},
            },
        ]

    @patch("pacemaker.langfuse.push.requests.post")
    def test_all_items_succeed_returns_true_with_count(self, mock_post):
        """
        RED TEST: All items succeed → returns (True, 3)

        When Langfuse returns HTTP 200 with all items in "successes" array,
        should return (True, success_count) where success_count = len(successes).
        """
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "successes": [
                {"id": "trace-123", "status": 201},
                {"id": "span-456", "status": 201},
                {"id": "span-789", "status": 201},
            ],
            "errors": [],
        }
        mock_post.return_value = mock_response

        # Act
        success, count = push_batch_events(
            self.base_url, self.public_key, self.secret_key, self.batch
        )

        # Assert
        self.assertTrue(success, "Should return True when all items succeed")
        self.assertEqual(count, 3, "Should return count of 3 successful items")

    @patch("pacemaker.langfuse.push.requests.post")
    def test_all_items_fail_returns_false_with_zero_count(self, mock_post):
        """
        RED TEST: All items fail → returns (False, 0)

        When Langfuse returns HTTP 200 with all items in "errors" array,
        should return (False, 0) because no items succeeded.
        """
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "successes": [],
            "errors": [
                {
                    "id": "trace-123",
                    "status": 400,
                    "message": "Invalid trace format",
                },
                {
                    "id": "span-456",
                    "status": 400,
                    "message": "Missing traceId",
                },
                {
                    "id": "span-789",
                    "status": 400,
                    "message": "Invalid timestamp",
                },
            ],
        }
        mock_post.return_value = mock_response

        # Act
        success, count = push_batch_events(
            self.base_url, self.public_key, self.secret_key, self.batch
        )

        # Assert
        self.assertFalse(success, "Should return False when all items fail")
        self.assertEqual(count, 0, "Should return count of 0 when all fail")

    @patch("pacemaker.langfuse.push.requests.post")
    def test_partial_success_returns_true_with_actual_count(self, mock_post):
        """
        RED TEST: Partial success (2/3) → returns (True, 2)

        When some items succeed and some fail, should return (True, N)
        where N is the actual number of successful items.
        """
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "successes": [
                {"id": "trace-123", "status": 201},
                {"id": "span-456", "status": 201},
            ],
            "errors": [
                {
                    "id": "span-789",
                    "status": 400,
                    "message": "Invalid timestamp",
                }
            ],
        }
        mock_post.return_value = mock_response

        # Act
        success, count = push_batch_events(
            self.base_url, self.public_key, self.secret_key, self.batch
        )

        # Assert
        self.assertTrue(success, "Should return True when some items succeed")
        self.assertEqual(count, 2, "Should return count of 2 (actual successful items)")

    @patch("pacemaker.langfuse.push.log_warning")
    @patch("pacemaker.langfuse.push.requests.post")
    def test_unparseable_response_returns_false_with_warning(
        self, mock_post, mock_log_warning
    ):
        """
        RED TEST: Unparseable response body → returns (False, 0) with warning

        When response.json() fails or response has unexpected format,
        should log warning and return (False, 0).
        """
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_post.return_value = mock_response

        # Act
        success, count = push_batch_events(
            self.base_url, self.public_key, self.secret_key, self.batch
        )

        # Assert
        self.assertFalse(success, "Should return False when response unparseable")
        self.assertEqual(count, 0, "Should return count of 0 when unparseable")

        # Verify warning was logged
        mock_log_warning.assert_called()
        warning_message = mock_log_warning.call_args[0][1]
        self.assertIn("Failed to parse response", warning_message)

    @patch("pacemaker.langfuse.push.requests.post")
    def test_empty_batch_returns_true_with_zero_count(self, mock_post):
        """
        RED TEST: Empty batch → returns (True, 0)

        When batch is empty, should return (True, 0) - not an error condition.
        """
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"successes": [], "errors": []}
        mock_post.return_value = mock_response

        # Act
        success, count = push_batch_events(
            self.base_url, self.public_key, self.secret_key, []
        )

        # Assert
        self.assertTrue(success, "Empty batch is not an error")
        self.assertEqual(count, 0, "Should return count of 0 for empty batch")

    @patch("pacemaker.langfuse.push.log_warning")
    @patch("pacemaker.langfuse.push.requests.post")
    def test_errors_logged_when_present(self, mock_post, mock_log_warning):
        """
        RED TEST: Errors array should be logged when present

        When response contains errors, should log them for debugging.
        """
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "successes": [{"id": "trace-123", "status": 201}],
            "errors": [
                {
                    "id": "span-456",
                    "status": 400,
                    "message": "Invalid format",
                },
                {
                    "id": "span-789",
                    "status": 400,
                    "message": "Missing field",
                },
            ],
        }
        mock_post.return_value = mock_response

        # Act
        push_batch_events(self.base_url, self.public_key, self.secret_key, self.batch)

        # Assert - verify errors were logged
        mock_log_warning.assert_called()
        warning_message = mock_log_warning.call_args[0][1]
        self.assertIn("2 errors", warning_message)

    @patch("pacemaker.langfuse.push.requests.post")
    def test_http_error_codes_return_false_with_zero_count(self, mock_post):
        """
        RED TEST: HTTP error codes → returns (False, 0)

        Non-success HTTP codes should return (False, 0) tuple.
        """
        # Test HTTP 400
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_post.return_value = mock_response

        success, count = push_batch_events(
            self.base_url, self.public_key, self.secret_key, self.batch
        )

        self.assertFalse(success, "Should return False for HTTP 400")
        self.assertEqual(count, 0, "Should return count 0 for HTTP 400")

        # Test HTTP 500
        mock_response.status_code = 500
        success, count = push_batch_events(
            self.base_url, self.public_key, self.secret_key, self.batch
        )

        self.assertFalse(success, "Should return False for HTTP 500")
        self.assertEqual(count, 0, "Should return count 0 for HTTP 500")

    @patch("pacemaker.langfuse.push.requests.post")
    def test_timeout_exception_returns_false_with_zero_count(self, mock_post):
        """
        RED TEST: Timeout exception → returns (False, 0)

        Timeout should return (False, 0) tuple.
        """
        # Arrange
        mock_post.side_effect = requests.exceptions.Timeout("Request timed out")

        # Act
        success, count = push_batch_events(
            self.base_url, self.public_key, self.secret_key, self.batch
        )

        # Assert
        self.assertFalse(success, "Should return False on timeout")
        self.assertEqual(count, 0, "Should return count 0 on timeout")

    @patch("pacemaker.langfuse.push.requests.post")
    def test_connection_error_returns_false_with_zero_count(self, mock_post):
        """
        RED TEST: Connection error → returns (False, 0)

        Connection error should return (False, 0) tuple.
        """
        # Arrange
        mock_post.side_effect = requests.exceptions.ConnectionError(
            "Unable to reach server"
        )

        # Act
        success, count = push_batch_events(
            self.base_url, self.public_key, self.secret_key, self.batch
        )

        # Assert
        self.assertFalse(success, "Should return False on connection error")
        self.assertEqual(count, 0, "Should return count 0 on connection error")

    @patch("pacemaker.langfuse.push.requests.post")
    def test_generic_exception_returns_false_with_zero_count(self, mock_post):
        """
        RED TEST: Generic exception → returns (False, 0)

        Any unexpected exception should return (False, 0) tuple.
        """
        # Arrange
        mock_post.side_effect = ValueError("Unexpected error")

        # Act
        success, count = push_batch_events(
            self.base_url, self.public_key, self.secret_key, self.batch
        )

        # Assert
        self.assertFalse(success, "Should return False on unexpected exception")
        self.assertEqual(count, 0, "Should return count 0 on unexpected exception")

    @patch("pacemaker.langfuse.push.log_info")
    @patch("pacemaker.langfuse.push.requests.post")
    def test_success_info_logged_with_actual_count(self, mock_post, mock_log_info):
        """
        RED TEST: Success log should show actual count (not batch length)

        When logging success, should show actual success_count, not len(batch).
        This is critical for diagnosing the over-counting bug.
        """
        # Arrange - 2 succeed, 1 fails
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "successes": [
                {"id": "trace-123", "status": 201},
                {"id": "span-456", "status": 201},
            ],
            "errors": [{"id": "span-789", "status": 400, "message": "Invalid"}],
        }
        mock_post.return_value = mock_response

        # Act
        push_batch_events(self.base_url, self.public_key, self.secret_key, self.batch)

        # Assert - verify log shows actual count (2), not batch length (3)
        mock_log_info.assert_called()
        info_message = mock_log_info.call_args[0][1]
        self.assertIn("2/3", info_message, "Should show '2/3 events'")

    @patch("pacemaker.langfuse.push.log_warning")
    @patch("pacemaker.langfuse.push.requests.post")
    def test_all_failures_warning_logged(self, mock_post, mock_log_warning):
        """
        RED TEST: When all items fail, log specific warning

        Should log clear message when all items failed.
        """
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "successes": [],
            "errors": [{"id": "trace-123", "status": 400, "message": "Bad format"}],
        }
        mock_post.return_value = mock_response

        # Act
        push_batch_events(
            self.base_url, self.public_key, self.secret_key, [self.batch[0]]
        )

        # Assert
        mock_log_warning.assert_called()
        warning_message = mock_log_warning.call_args[0][1]
        self.assertIn("All", warning_message)
        self.assertIn("failed", warning_message)


if __name__ == "__main__":
    unittest.main()
