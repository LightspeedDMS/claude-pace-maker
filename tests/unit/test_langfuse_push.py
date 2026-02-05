#!/usr/bin/env python3
"""
Unit tests for Langfuse push functionality (AC4, AC5).

Tests:
- AC4: Push completes within 2 seconds (timeout enforcement)
- AC5: Graceful failure handling when server unreachable
- HTTP success codes (200, 201, 202)
- HTTP error codes (400, 500, etc.)
- Connection errors
- Timeout errors
- Malformed trace data handling
"""

import unittest
from unittest.mock import patch, MagicMock
import requests

from src.pacemaker.langfuse.push import push_trace


class TestLangfusePush(unittest.TestCase):
    """Test Langfuse trace push functionality"""

    def setUp(self):
        """Set up test fixtures."""
        self.base_url = "https://cloud.langfuse.com"
        self.public_key = "pk-lf-test-123"
        self.secret_key = "sk-lf-test-secret-456"
        self.trace = {
            "id": "test-session-123",
            "name": "claude-code-session-test-ses",
            "userId": "test@example.com",
            "metadata": {
                "model": "claude-sonnet-4-5",
                "tool_calls": ["Read", "Write", "Bash"],
                "tool_count": 3,
            },
            "usage": {
                "input": 1000,
                "output": 500,
                "total": 1500,
            },
        }

    @patch("src.pacemaker.langfuse.push.requests.post")
    def test_push_trace_success_200(self, mock_post):
        """Test successful push with HTTP 200"""
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        # Act
        result = push_trace(self.base_url, self.public_key, self.secret_key, self.trace)

        # Assert
        self.assertTrue(result, "Push should succeed with HTTP 200")
        mock_post.assert_called_once()

        # Verify correct endpoint (traces endpoint for single trace push)
        call_args = mock_post.call_args
        self.assertEqual(
            call_args[0][0], "https://cloud.langfuse.com/api/public/traces"
        )

        # Verify auth
        self.assertEqual(call_args[1]["auth"], (self.public_key, self.secret_key))

        # Verify payload is direct trace (not wrapped in batch)
        payload = call_args[1]["json"]
        self.assertEqual(payload["id"], self.trace["id"])

    @patch("src.pacemaker.langfuse.push.requests.post")
    def test_push_trace_success_201(self, mock_post):
        """Test successful push with HTTP 201 Created"""
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_post.return_value = mock_response

        # Act
        result = push_trace(self.base_url, self.public_key, self.secret_key, self.trace)

        # Assert
        self.assertTrue(result, "Push should succeed with HTTP 201")

    @patch("src.pacemaker.langfuse.push.requests.post")
    def test_push_trace_success_202(self, mock_post):
        """Test successful push with HTTP 202 Accepted"""
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_post.return_value = mock_response

        # Act
        result = push_trace(self.base_url, self.public_key, self.secret_key, self.trace)

        # Assert
        self.assertTrue(result, "Push should succeed with HTTP 202")

    @patch("src.pacemaker.langfuse.push.requests.post")
    def test_push_trace_timeout_default_2s(self, mock_post):
        """AC4: Default timeout is 2 seconds"""
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        # Act
        push_trace(self.base_url, self.public_key, self.secret_key, self.trace)

        # Assert - verify timeout parameter
        call_args = mock_post.call_args
        self.assertEqual(call_args[1]["timeout"], 2, "Default timeout should be 2s")

    @patch("src.pacemaker.langfuse.push.requests.post")
    def test_push_trace_custom_timeout(self, mock_post):
        """Test custom timeout can be specified"""
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        # Act
        push_trace(
            self.base_url,
            self.public_key,
            self.secret_key,
            self.trace,
            timeout=5,
        )

        # Assert
        call_args = mock_post.call_args
        self.assertEqual(call_args[1]["timeout"], 5)

    @patch("src.pacemaker.langfuse.push.requests.post")
    def test_push_trace_timeout_graceful_failure(self, mock_post):
        """AC5: Graceful failure on timeout"""
        # Arrange
        mock_post.side_effect = requests.exceptions.Timeout("Request timed out")

        # Act
        result = push_trace(self.base_url, self.public_key, self.secret_key, self.trace)

        # Assert - returns False, doesn't raise
        self.assertFalse(result, "Should return False on timeout")

    @patch("src.pacemaker.langfuse.push.requests.post")
    def test_push_trace_connection_error_graceful_failure(self, mock_post):
        """AC5: Graceful failure when server unreachable"""
        # Arrange
        mock_post.side_effect = requests.exceptions.ConnectionError(
            "Unable to reach server"
        )

        # Act
        result = push_trace(self.base_url, self.public_key, self.secret_key, self.trace)

        # Assert - returns False, doesn't raise
        self.assertFalse(result, "Should return False on connection error")

    @patch("src.pacemaker.langfuse.push.requests.post")
    def test_push_trace_http_400_failure(self, mock_post):
        """Test HTTP 400 Bad Request returns False"""
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_post.return_value = mock_response

        # Act
        result = push_trace(self.base_url, self.public_key, self.secret_key, self.trace)

        # Assert
        self.assertFalse(result, "Should return False on HTTP 400")

    @patch("src.pacemaker.langfuse.push.requests.post")
    def test_push_trace_http_401_unauthorized(self, mock_post):
        """Test HTTP 401 Unauthorized returns False"""
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_post.return_value = mock_response

        # Act
        result = push_trace(self.base_url, self.public_key, self.secret_key, self.trace)

        # Assert
        self.assertFalse(result, "Should return False on HTTP 401")

    @patch("src.pacemaker.langfuse.push.requests.post")
    def test_push_trace_http_500_server_error(self, mock_post):
        """Test HTTP 500 Server Error returns False"""
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_post.return_value = mock_response

        # Act
        result = push_trace(self.base_url, self.public_key, self.secret_key, self.trace)

        # Assert
        self.assertFalse(result, "Should return False on HTTP 500")

    @patch("src.pacemaker.langfuse.push.requests.post")
    def test_push_trace_generic_exception_graceful_failure(self, mock_post):
        """AC5: Graceful failure on unexpected exceptions"""
        # Arrange
        mock_post.side_effect = ValueError("Unexpected error")

        # Act
        result = push_trace(self.base_url, self.public_key, self.secret_key, self.trace)

        # Assert - returns False, doesn't raise
        self.assertFalse(result, "Should return False on unexpected exception")

    @patch("src.pacemaker.langfuse.push.requests.post")
    def test_push_trace_base_url_trailing_slash_handled(self, mock_post):
        """Test base URL with trailing slash is handled correctly"""
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response
        base_url_with_slash = "https://cloud.langfuse.com/"

        # Act
        push_trace(base_url_with_slash, self.public_key, self.secret_key, self.trace)

        # Assert - should not have double slash
        call_args = mock_post.call_args
        url = call_args[0][0]
        self.assertEqual(url, "https://cloud.langfuse.com/api/public/traces")
        self.assertNotIn("//api", url, "Should not have double slash")

    @patch("src.pacemaker.langfuse.push.requests.post")
    def test_push_trace_content_type_header(self, mock_post):
        """Test Content-Type header is set correctly"""
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        # Act
        push_trace(self.base_url, self.public_key, self.secret_key, self.trace)

        # Assert
        call_args = mock_post.call_args
        headers = call_args[1]["headers"]
        self.assertEqual(headers["Content-Type"], "application/json")

    @patch("src.pacemaker.langfuse.push.log_warning")
    @patch("src.pacemaker.langfuse.push.requests.post")
    def test_push_trace_no_secret_key_logging(self, mock_post, mock_log_warning):
        """Ensure secret key is never logged"""
        # Arrange
        mock_post.side_effect = requests.exceptions.ConnectionError("Connection failed")

        # Act
        push_trace(self.base_url, self.public_key, self.secret_key, self.trace)

        # Assert - check no log call contains secret key
        for call in mock_log_warning.call_args_list:
            for arg in call[0]:
                self.assertNotIn(
                    self.secret_key,
                    str(arg),
                    "Secret key should never be logged",
                )


if __name__ == "__main__":
    unittest.main()
