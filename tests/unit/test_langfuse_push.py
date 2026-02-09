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

from pacemaker.langfuse.push import push_trace


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

    @patch("pacemaker.langfuse.push.requests.post")
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

    @patch("pacemaker.langfuse.push.requests.post")
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

    @patch("pacemaker.langfuse.push.requests.post")
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

    @patch("pacemaker.langfuse.push.requests.post")
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

    @patch("pacemaker.langfuse.push.requests.post")
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

    @patch("pacemaker.langfuse.push.requests.post")
    def test_push_trace_timeout_graceful_failure(self, mock_post):
        """AC5: Graceful failure on timeout"""
        # Arrange
        mock_post.side_effect = requests.exceptions.Timeout("Request timed out")

        # Act
        result = push_trace(self.base_url, self.public_key, self.secret_key, self.trace)

        # Assert - returns False, doesn't raise
        self.assertFalse(result, "Should return False on timeout")

    @patch("pacemaker.langfuse.push.requests.post")
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

    @patch("pacemaker.langfuse.push.requests.post")
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

    @patch("pacemaker.langfuse.push.requests.post")
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

    @patch("pacemaker.langfuse.push.requests.post")
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

    @patch("pacemaker.langfuse.push.requests.post")
    def test_push_trace_generic_exception_graceful_failure(self, mock_post):
        """AC5: Graceful failure on unexpected exceptions"""
        # Arrange
        mock_post.side_effect = ValueError("Unexpected error")

        # Act
        result = push_trace(self.base_url, self.public_key, self.secret_key, self.trace)

        # Assert - returns False, doesn't raise
        self.assertFalse(result, "Should return False on unexpected exception")

    @patch("pacemaker.langfuse.push.requests.post")
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

    @patch("pacemaker.langfuse.push.requests.post")
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

    @patch("pacemaker.langfuse.push.log_warning")
    @patch("pacemaker.langfuse.push.requests.post")
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


class TestSubagentStateIsolation(unittest.TestCase):
    """Test subagent state isolation in handle_post_tool_use"""

    @patch("pacemaker.langfuse.orchestrator.sanitize_trace")
    @patch("pacemaker.transcript_reader.get_last_n_assistant_messages")
    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    @patch("pacemaker.langfuse.orchestrator._create_spans_from_blocks")
    @patch("pacemaker.langfuse.orchestrator.incremental.extract_content_blocks")
    @patch("pacemaker.langfuse.orchestrator.state.StateManager")
    @patch("builtins.open")
    def test_subagent_context_reads_subagent_state(
        self,
        mock_open,
        mock_state_manager_class,
        mock_extract,
        mock_create_spans,
        mock_push,
        mock_get_msgs,
        mock_sanitize,
    ):
        """
        RED TEST: In subagent context, handle_post_tool_use should read state
        for subagent_session_id, NOT parent's session_id.

        BUG: Currently reads parent's state (session_id from hook_data).
        FIX: Should derive subagent_session_id and read that state instead.
        """
        from pacemaker.langfuse.orchestrator import handle_post_tool_use
        import json
        from unittest.mock import MagicMock, mock_open as mock_open_func

        # Setup: Pacemaker state shows we're in subagent context
        pacemaker_state = {
            "in_subagent": True,
            "current_subagent_agent_id": "agent-abc123",
            "current_subagent_trace_id": "trace-subagent-456",
        }

        # Mock file operations for pacemaker state
        mock_file = mock_open_func(read_data=json.dumps(pacemaker_state))
        mock_open.return_value = mock_file.return_value

        # Setup: Parent state (should NOT be read)
        parent_state = {
            "session_id": "parent-session",
            "trace_id": "trace-parent-123",
            "last_pushed_line": 100,
            "metadata": {"current_trace_id": "trace-parent-123"},
        }

        # Setup: Subagent state (SHOULD be read)
        subagent_state = {
            "session_id": "subagent-agent-abc123",
            "trace_id": "trace-subagent-456",
            "last_pushed_line": 50,  # Different from parent!
            "metadata": {"current_trace_id": "trace-subagent-456"},
        }

        # Mock StateManager to track which session_id is read
        mock_state_manager = MagicMock()
        mock_state_manager_class.return_value = mock_state_manager

        # Return different states based on session_id argument
        def state_read_side_effect(session_id):
            if session_id == "parent-session":
                return parent_state
            elif session_id == "subagent-agent-abc123":
                return subagent_state
            return None

        mock_state_manager.read.side_effect = state_read_side_effect

        # Mock content extraction (should use subagent's last_pushed_line)
        mock_extract.return_value = [
            {"role": "assistant", "content": "test", "line_number": 55}
        ]
        mock_create_spans.return_value = [{"id": "span-1"}]  # Mock span creation

        # Mock secrets parsing (returns empty - no secrets)
        mock_get_msgs.return_value = []

        # Mock sanitize to pass through unchanged
        mock_sanitize.side_effect = lambda x, db: x

        # Mock push success (returns tuple)
        mock_push.return_value = (True, 1)

        # Test config
        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://cloud.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }

        # ACT: Call handle_post_tool_use with parent's session_id in hook_data
        _result = handle_post_tool_use(
            config=config,
            session_id="parent-session",  # Hook data has parent's session_id
            transcript_path="/tmp/transcript.jsonl",
            state_dir="/tmp/langfuse_state",
        )

        # ASSERT: Should have read SUBAGENT state, not parent state
        # RED: This will FAIL because current code reads parent's state
        mock_state_manager.read.assert_any_call("subagent-agent-abc123")

        # Verify extract_content_blocks was called with subagent's last_pushed_line
        # RED: This will FAIL because current code uses parent's last_pushed_line (100)
        mock_extract.assert_called_once()
        call_kwargs = mock_extract.call_args[1]
        self.assertEqual(
            call_kwargs["start_line"],
            50,  # Should be subagent's last_pushed_line
            "Should use subagent's last_pushed_line, not parent's",
        )

    @patch("pacemaker.langfuse.orchestrator.sanitize_trace")
    @patch("pacemaker.transcript_reader.get_last_n_assistant_messages")
    @patch("pacemaker.langfuse.orchestrator.increment_metric")
    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    @patch("pacemaker.langfuse.orchestrator._create_spans_from_blocks")
    @patch("pacemaker.langfuse.orchestrator.incremental.extract_content_blocks")
    @patch("pacemaker.langfuse.orchestrator.state.StateManager")
    @patch("builtins.open")
    def test_subagent_context_updates_subagent_state(
        self,
        mock_open,
        mock_state_manager_class,
        mock_extract,
        mock_create_spans,
        mock_push,
        mock_increment,
        mock_get_msgs,
        mock_sanitize,
    ):
        """
        RED TEST: In subagent context, handle_post_tool_use should update state
        for subagent_session_id, NOT parent's session_id.

        BUG: Currently updates parent's state (line 877).
        FIX: Should update subagent_session_id state instead.
        """
        from pacemaker.langfuse.orchestrator import handle_post_tool_use
        import json
        from unittest.mock import MagicMock, mock_open as mock_open_func

        # Setup: Pacemaker state shows we're in subagent context
        pacemaker_state = {
            "in_subagent": True,
            "current_subagent_agent_id": "agent-xyz789",
            "current_subagent_trace_id": "trace-subagent-999",
        }

        mock_file = mock_open_func(read_data=json.dumps(pacemaker_state))
        mock_open.return_value = mock_file.return_value

        # Parent state (read first)
        parent_state = {
            "session_id": "parent-session",
            "trace_id": "trace-parent-123",
            "last_pushed_line": 100,
            "metadata": {"current_trace_id": "trace-parent-123"},
        }

        # Subagent state (read second when in_subagent=True)
        subagent_state = {
            "session_id": "subagent-agent-xyz789",
            "trace_id": "trace-subagent-999",
            "last_pushed_line": 20,
            "metadata": {"current_trace_id": "trace-subagent-999"},
        }

        # Mock StateManager
        mock_state_manager = MagicMock()
        mock_state_manager_class.return_value = mock_state_manager

        # Return different states based on session_id
        def state_read_side_effect(session_id):
            if session_id == "parent-session":
                return parent_state
            elif session_id == "subagent-agent-xyz789":
                return subagent_state
            return None

        mock_state_manager.read.side_effect = state_read_side_effect

        # Mock content extraction (new content at line 30)
        mock_extract.return_value = [
            {"role": "assistant", "content": "new content", "line_number": 30}
        ]
        mock_create_spans.return_value = [{"id": "span-1"}]  # Mock span creation

        # Mock secrets parsing (returns empty - no secrets)
        mock_get_msgs.return_value = []

        # Mock sanitize to pass through unchanged
        mock_sanitize.side_effect = lambda x, db: x

        # Mock push success (returns tuple)
        mock_push.return_value = (True, 1)

        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://cloud.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
            "db_path": "/tmp/metrics.db",
        }

        # ACT
        _result = handle_post_tool_use(
            config=config,
            session_id="parent-session",  # Hook data has parent session_id
            transcript_path="/tmp/transcript.jsonl",
            state_dir="/tmp/langfuse_state",
        )

        # ASSERT: Should have updated SUBAGENT state, not parent
        # RED: This will FAIL because current code updates parent's session_id
        mock_state_manager.create_or_update.assert_called_once()
        call_kwargs = mock_state_manager.create_or_update.call_args[1]

        self.assertEqual(
            call_kwargs["session_id"],
            "subagent-agent-xyz789",  # Should be subagent session_id
            "Should update subagent's state, not parent's",
        )
        self.assertEqual(
            call_kwargs["last_pushed_line"],
            30,  # New max line
            "Should update last_pushed_line to max line from content blocks",
        )
        self.assertEqual(
            call_kwargs["trace_id"],
            "trace-subagent-999",  # Subagent's trace_id
            "Should use subagent's trace_id",
        )


class TestTimeoutHandling(unittest.TestCase):
    """Test timeout handling to prevent duplicate spans"""

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    @patch("pacemaker.langfuse.orchestrator.incremental.parse_incremental_lines")
    @patch("pacemaker.langfuse.orchestrator.state.StateManager")
    def test_timeout_value_is_10_seconds(
        self, mock_state_manager_class, mock_parse, mock_push
    ):
        """
        RED TEST: Verify timeout is 10 seconds (not 2).

        This prevents premature timeouts that cause duplicate spans.
        """
        from pacemaker.langfuse.orchestrator import (
            INCREMENTAL_PUSH_TIMEOUT_SECONDS,
        )

        # ASSERT: Timeout should be 10 seconds
        self.assertEqual(
            INCREMENTAL_PUSH_TIMEOUT_SECONDS,
            10,
            "Timeout should be 10 seconds to prevent premature failures",
        )

    @patch("pacemaker.langfuse.orchestrator.sanitize_trace")
    @patch("pacemaker.langfuse.orchestrator.increment_metric")
    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    @patch("pacemaker.langfuse.orchestrator.incremental.create_batch_event")
    @patch("pacemaker.langfuse.orchestrator.incremental.parse_incremental_lines")
    @patch("pacemaker.langfuse.orchestrator.jsonl_parser.parse_session_metadata")
    @patch("pacemaker.langfuse.orchestrator.jsonl_parser.extract_user_id")
    @patch("pacemaker.langfuse.orchestrator.state.StateManager")
    def test_state_updated_on_timeout_to_prevent_duplicates(
        self,
        mock_state_manager_class,
        mock_extract_user,
        mock_parse_metadata,
        mock_parse_incremental,
        mock_create_batch,
        mock_push,
        mock_increment,
        mock_sanitize,
    ):
        """
        RED TEST: State should be updated even on timeout.

        When push times out, the data was likely sent to Langfuse (server just
        didn't respond in time). We should update last_pushed_line to prevent
        duplicate spans on next hook call.

        This is the core fix for the duplicate spans issue.
        """
        from pacemaker.langfuse.orchestrator import run_incremental_push
        from unittest.mock import MagicMock

        # Setup: State manager
        mock_state_manager = MagicMock()
        mock_state_manager_class.return_value = mock_state_manager

        # Existing state shows we pushed up to line 100
        existing_state = {
            "session_id": "test-session",
            "trace_id": "trace-123",
            "last_pushed_line": 100,
            "metadata": {"tool_calls": [], "tool_count": 0},
        }
        mock_state_manager.read.return_value = existing_state

        # Parse incremental lines: new lines 101-110
        mock_parse_incremental.return_value = {
            "lines_parsed": 10,
            "last_line": 110,
            "entries": [],
        }

        # Mock batch creation
        mock_create_batch.return_value = [
            {
                "id": "trace-123",
                "timestamp": "2024-01-01T00:00:00Z",
                "type": "trace-create",
                "body": {
                    "id": "trace-123",
                    "metadata": {"tool_calls": [], "tool_count": 0},
                },
            }
        ]

        # Mock metadata
        mock_parse_metadata.return_value = {"model": "claude-sonnet-4-5"}
        mock_extract_user.return_value = "user@example.com"

        # Mock sanitize to pass through unchanged
        mock_sanitize.side_effect = lambda x, db: x

        # SIMULATE TIMEOUT: push_batch_events returns (False, 0)
        mock_push.return_value = (False, 0)

        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://cloud.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
            "db_path": "/tmp/metrics.db",
        }

        # ACT: Run incremental push (simulates timeout)
        result = run_incremental_push(
            config=config,
            session_id="test-session",
            transcript_path="/tmp/transcript.jsonl",
            state_dir="/tmp/state",
            hook_type="post_tool_use",
        )

        # ASSERT: State should be updated DESPITE timeout
        # This prevents re-processing lines 101-110 on next hook call
        mock_state_manager.create_or_update.assert_called_once()
        call_kwargs = mock_state_manager.create_or_update.call_args[1]

        self.assertEqual(
            call_kwargs["last_pushed_line"],
            110,
            "Should update last_pushed_line even on timeout to prevent duplicates",
        )

        # Function should still return False to indicate timeout occurred
        self.assertFalse(result, "Should return False to signal timeout")

    @patch("pacemaker.langfuse.orchestrator.sanitize_trace")
    @patch("pacemaker.transcript_reader.get_last_n_assistant_messages")
    @patch("pacemaker.langfuse.orchestrator.increment_metric")
    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    @patch("pacemaker.langfuse.orchestrator._create_spans_from_blocks")
    @patch("pacemaker.langfuse.orchestrator.incremental.extract_content_blocks")
    @patch("pacemaker.langfuse.orchestrator.state.StateManager")
    @patch("builtins.open")
    def test_handle_post_tool_use_updates_state_on_timeout(
        self,
        mock_open,
        mock_state_manager_class,
        mock_extract,
        mock_create_spans,
        mock_push,
        mock_increment,
        mock_get_msgs,
        mock_sanitize,
    ):
        """
        RED TEST: handle_post_tool_use should update state even on timeout.

        BUG: Currently returns False before updating state (lines 899-901 in orchestrator.py).
        FIX: Should update state BEFORE checking success, like run_incremental_push does.

        This prevents duplicate spans when push times out but data was already sent.
        """
        from pacemaker.langfuse.orchestrator import handle_post_tool_use
        import json
        from unittest.mock import MagicMock, mock_open as mock_open_func

        # Setup: Not in subagent context (simple case)
        pacemaker_state = {
            "in_subagent": False,
        }

        mock_file = mock_open_func(read_data=json.dumps(pacemaker_state))
        mock_open.return_value = mock_file.return_value

        # Existing state shows we pushed up to line 50
        existing_state = {
            "session_id": "test-session",
            "trace_id": "trace-456",
            "last_pushed_line": 50,
            "metadata": {"current_trace_id": "trace-456"},
        }

        # Mock StateManager
        mock_state_manager = MagicMock()
        mock_state_manager_class.return_value = mock_state_manager
        mock_state_manager.read.return_value = existing_state

        # Mock content extraction: new content at lines 55-60
        mock_extract.return_value = [
            {"role": "assistant", "content": "line 55", "line_number": 55},
            {"role": "assistant", "content": "line 60", "line_number": 60},
        ]
        mock_create_spans.return_value = [{"id": "span-1"}, {"id": "span-2"}]

        # Mock secrets parsing (returns empty - no secrets)
        mock_get_msgs.return_value = []

        # Mock sanitize to pass through unchanged
        mock_sanitize.side_effect = lambda x, db: x

        # SIMULATE TIMEOUT: push_batch_events returns (False, 0)
        mock_push.return_value = (False, 0)

        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://cloud.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
            "db_path": "/tmp/metrics.db",
        }

        # ACT: Call handle_post_tool_use (simulates timeout)
        result = handle_post_tool_use(
            config=config,
            session_id="test-session",
            transcript_path="/tmp/transcript.jsonl",
            state_dir="/tmp/state",
        )

        # ASSERT: State should be updated DESPITE timeout
        # This prevents re-processing lines 55-60 on next hook call
        mock_state_manager.create_or_update.assert_called_once()
        call_kwargs = mock_state_manager.create_or_update.call_args[1]

        self.assertEqual(
            call_kwargs["last_pushed_line"],
            60,  # Should be max line from content blocks
            "Should update last_pushed_line even on timeout to prevent duplicates",
        )
        self.assertEqual(
            call_kwargs["session_id"], "test-session", "Should use correct session_id"
        )
        self.assertEqual(
            call_kwargs["trace_id"], "trace-456", "Should preserve trace_id"
        )

        # Function should still return False to indicate timeout occurred
        self.assertFalse(result, "Should return False to signal timeout")

        # Metrics should NOT be incremented on timeout (only on confirmed success)
        mock_increment.assert_not_called()


if __name__ == "__main__":
    unittest.main()
