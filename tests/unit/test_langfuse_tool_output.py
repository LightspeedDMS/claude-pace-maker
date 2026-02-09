#!/usr/bin/env python3
"""
Unit tests for tool output capture in Langfuse spans.

Tests that tool_response from PostToolUse hook is correctly passed to
orchestrator and used when creating tool spans.

BUG: Tool output is missing from Langfuse spans because tool_response
from hook_data is not extracted and passed to orchestrator.

FIX: Extract tool_response in hook.py and pass to handle_post_tool_use(),
then use it when creating the current tool's span.
"""

import unittest
from unittest.mock import patch, MagicMock, mock_open as mock_open_func
import json


class TestToolOutputCapture(unittest.TestCase):
    """Test that tool output from hook is captured in Langfuse spans"""

    @patch("pacemaker.langfuse.orchestrator.sanitize_trace")
    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    @patch("pacemaker.langfuse.orchestrator.incremental.extract_content_blocks")
    @patch("pacemaker.langfuse.orchestrator.state.StateManager")
    @patch("builtins.open")
    def test_tool_name_and_input_from_hook_used_in_span(
        self,
        mock_open,
        mock_state_manager_class,
        mock_extract,
        mock_push,
        mock_sanitize,
    ):
        """
        RED TEST: tool_name and tool_input from PostToolUse hook should be used in tool span.

        BUG: Currently tool_name and tool_input are extracted from transcript parsing
        which may not have the current tool yet. The hook provides tool_name and
        tool_input which are the ACTUAL values from the tool that just executed.

        FIX: Pass tool_name and tool_input to handle_post_tool_use() and use them
        when creating the span for the current tool execution.
        """
        from pacemaker.langfuse.orchestrator import handle_post_tool_use

        # Setup: Not in subagent context
        pacemaker_state = {"in_subagent": False}
        mock_file = mock_open_func(read_data=json.dumps(pacemaker_state))
        mock_open.return_value = mock_file.return_value

        # Existing state
        existing_state = {
            "session_id": "test-session",
            "trace_id": "trace-123",
            "last_pushed_line": 10,
            "metadata": {"current_trace_id": "trace-123"},
        }

        # Mock StateManager
        mock_state_manager = MagicMock()
        mock_state_manager_class.return_value = mock_state_manager
        mock_state_manager.read.return_value = existing_state

        # Mock content extraction - returns empty (current tool not in transcript yet)
        mock_extract.return_value = []

        # Mock sanitize to pass through
        mock_sanitize.side_effect = lambda x, db: x

        # Mock push success
        mock_push.return_value = (True, 1)

        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://cloud.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
            "db_path": "/tmp/test.db",
        }

        # THE KEY: tool_name, tool_input, tool_response from hook
        tool_name = "Write"
        tool_input = {"file_path": "/home/user/test.py", "content": "print('hello')"}
        tool_response = "File /home/user/test.py written successfully (1024 bytes)"

        # ACT: Call handle_post_tool_use WITH tool_name, tool_input, tool_response
        result = handle_post_tool_use(
            config=config,
            session_id="test-session",
            transcript_path="/tmp/transcript.jsonl",
            state_dir="/tmp/state",
            tool_name=tool_name,  # NEW parameter
            tool_input=tool_input,  # NEW parameter
            tool_response=tool_response,  # NEW parameter
        )

        # ASSERT: Should succeed
        self.assertTrue(result)

        # Verify push was called with batch containing the tool name, input, and output
        mock_push.assert_called_once()
        push_args = mock_push.call_args[0]
        batch = push_args[3]  # 4th positional arg is batch

        # Should have created a span with tool_name, tool_input, and tool_response
        # Even though extract_content_blocks returned empty
        self.assertEqual(len(batch), 1, "Should create span for current tool")

        span_event = batch[0]
        self.assertEqual(span_event["type"], "span-create")

        span_body = span_event["body"]
        self.assertEqual(
            span_body["name"],
            f"Tool - {tool_name}",
            "Span name should be formatted as 'Tool - {tool_name}'",
        )
        self.assertIn("input", span_body, "Span should have input field")
        self.assertEqual(
            span_body["input"],
            tool_input,
            "Span input should match tool_input from hook",
        )
        self.assertIn("output", span_body, "Span should have output field")
        self.assertEqual(
            span_body["output"],
            tool_response,
            "Span output should match tool_response from hook",
        )

    @patch("pacemaker.langfuse.orchestrator.sanitize_trace")
    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    @patch("pacemaker.langfuse.orchestrator.incremental.extract_content_blocks")
    @patch("pacemaker.langfuse.orchestrator.state.StateManager")
    @patch("builtins.open")
    def test_tool_response_from_hook_used_in_span(
        self,
        mock_open,
        mock_state_manager_class,
        mock_extract,
        mock_push,
        mock_sanitize,
    ):
        """
        RED TEST: tool_response from PostToolUse hook should be used in tool span.

        BUG: Currently tool output is extracted from transcript parsing which may
        not have the current tool's output yet. The hook provides tool_response
        which is the ACTUAL output from the tool that just executed.

        FIX: Pass tool_response to handle_post_tool_use() and use it when creating
        the span for the current tool execution.
        """
        from pacemaker.langfuse.orchestrator import handle_post_tool_use

        # Setup: Not in subagent context
        pacemaker_state = {"in_subagent": False}
        mock_file = mock_open_func(read_data=json.dumps(pacemaker_state))
        mock_open.return_value = mock_file.return_value

        # Existing state
        existing_state = {
            "session_id": "test-session",
            "trace_id": "trace-123",
            "last_pushed_line": 10,
            "metadata": {"current_trace_id": "trace-123"},
        }

        # Mock StateManager
        mock_state_manager = MagicMock()
        mock_state_manager_class.return_value = mock_state_manager
        mock_state_manager.read.return_value = existing_state

        # Mock content extraction - returns empty (current tool not in transcript yet)
        mock_extract.return_value = []

        # Mock sanitize to pass through
        mock_sanitize.side_effect = lambda x, db: x

        # Mock push success
        mock_push.return_value = (True, 1)

        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://cloud.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
            "db_path": "/tmp/test.db",
        }

        # THE KEY: tool_response from hook (the ACTUAL output from tool that just ran)
        tool_response = "File /home/user/test.py written successfully (1024 bytes)"

        # ACT: Call handle_post_tool_use WITH tool_response
        result = handle_post_tool_use(
            config=config,
            session_id="test-session",
            transcript_path="/tmp/transcript.jsonl",
            state_dir="/tmp/state",
            tool_response=tool_response,  # NEW parameter
        )

        # ASSERT: Should succeed
        self.assertTrue(result)

        # Verify push was called with batch containing the tool output
        mock_push.assert_called_once()
        push_args = mock_push.call_args[0]
        batch = push_args[3]  # 4th positional arg is batch

        # Should have created a span with tool_response as output
        # Even though extract_content_blocks returned empty
        self.assertEqual(len(batch), 1, "Should create span for current tool")

        span_event = batch[0]
        self.assertEqual(span_event["type"], "span-create")

        span_body = span_event["body"]
        self.assertIn("output", span_body, "Span should have output field")
        self.assertEqual(
            span_body["output"],
            tool_response,
            "Span output should match tool_response from hook",
        )

    @patch("pacemaker.langfuse.orchestrator.sanitize_trace")
    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    @patch("pacemaker.langfuse.orchestrator.incremental.extract_content_blocks")
    @patch("pacemaker.langfuse.orchestrator.state.StateManager")
    @patch("builtins.open")
    def test_tool_response_none_uses_transcript_parsing(
        self,
        mock_open,
        mock_state_manager_class,
        mock_extract,
        mock_push,
        mock_sanitize,
    ):
        """
        Test backward compatibility: if tool_response is None, fall back to
        extracting output from transcript parsing.
        """
        from pacemaker.langfuse.orchestrator import handle_post_tool_use

        # Setup: Not in subagent context
        pacemaker_state = {"in_subagent": False}
        mock_file = mock_open_func(read_data=json.dumps(pacemaker_state))
        mock_open.return_value = mock_file.return_value

        # Existing state
        existing_state = {
            "session_id": "test-session",
            "trace_id": "trace-123",
            "last_pushed_line": 10,
            "metadata": {"current_trace_id": "trace-123"},
        }

        # Mock StateManager
        mock_state_manager = MagicMock()
        mock_state_manager_class.return_value = mock_state_manager
        mock_state_manager.read.return_value = existing_state

        # Mock content extraction - returns tool call with output from transcript
        mock_extract.return_value = [
            {
                "content_type": "tool_use",
                "tool_name": "Read",
                "tool_input": {"file_path": "/test.py"},
                "tool_output": "Output from transcript parsing",
                "line_number": 15,
            }
        ]

        # Mock sanitize to pass through
        mock_sanitize.side_effect = lambda x, db: x

        # Mock push success
        mock_push.return_value = (True, 1)

        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://cloud.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
            "db_path": "/tmp/test.db",
        }

        # ACT: Call WITHOUT tool_response (backward compatibility)
        result = handle_post_tool_use(
            config=config,
            session_id="test-session",
            transcript_path="/tmp/transcript.jsonl",
            state_dir="/tmp/state",
            tool_response=None,  # Explicitly None
        )

        # ASSERT: Should succeed
        self.assertTrue(result)

        # Verify push was called with span using transcript output
        mock_push.assert_called_once()
        push_args = mock_push.call_args[0]
        batch = push_args[3]

        self.assertEqual(len(batch), 1)
        span_body = batch[0]["body"]
        self.assertEqual(
            span_body["output"],
            "Output from transcript parsing",
            "Should use output from transcript when tool_response is None",
        )


class TestHookToolResponseExtraction(unittest.TestCase):
    """Test that hook.py extracts tool_response and passes to orchestrator"""

    @patch("pacemaker.langfuse.orchestrator.handle_post_tool_use")
    @patch("pacemaker.hook.database.initialize_database")
    @patch("pacemaker.hook.pacing_engine.run_pacing_check")
    @patch("pacemaker.hook.load_state")
    @patch("pacemaker.hook.load_config")
    def test_hook_extracts_tool_name_and_input_from_stdin(
        self,
        mock_load_config,
        mock_load_state,
        mock_pacing,
        mock_init_db,
        mock_handle_post_tool,
    ):
        """
        RED TEST: hook.py should extract tool_name and tool_input from hook_data
        and pass to orchestrator.handle_post_tool_use().

        BUG: Currently hook_data is read but tool_name and tool_input are not extracted.
        FIX: Extract tool_name and tool_input and pass as parameters.
        """
        from pacemaker.hook import run_hook
        from unittest.mock import patch
        import sys
        from io import StringIO

        # Mock config
        mock_load_config.return_value = {
            "enabled": True,
            "langfuse_enabled": True,
            "poll_interval": 60,
        }

        # Mock state
        mock_load_state.return_value = {
            "session_id": "test-session",
            "tool_execution_count": 0,
            "in_subagent": False,
        }

        # Mock pacing result
        mock_pacing.return_value = {
            "polled": False,
            "decision": {"should_throttle": False},
        }

        # Mock orchestrator success
        mock_handle_post_tool.return_value = True

        # Prepare hook_data with tool_name, tool_input, tool_response
        hook_data = {
            "tool_name": "Write",
            "tool_input": {"file_path": "/test.py", "content": "print('hello')"},
            "session_id": "test-session",
            "transcript_path": "/tmp/transcript.jsonl",
            "tool_response": "File written successfully (512 bytes)",
        }

        # Patch stdin to provide hook_data
        with patch.object(sys, "stdin", StringIO(json.dumps(hook_data))):
            # ACT: Run the hook
            run_hook()

        # ASSERT: handle_post_tool_use should have been called WITH tool_name and tool_input
        mock_handle_post_tool.assert_called_once()
        call_kwargs = mock_handle_post_tool.call_args[1]

        self.assertIn(
            "tool_name",
            call_kwargs,
            "Should pass tool_name to handle_post_tool_use",
        )
        self.assertEqual(
            call_kwargs["tool_name"],
            "Write",
            "Should pass correct tool_name value from hook_data",
        )

        self.assertIn(
            "tool_input",
            call_kwargs,
            "Should pass tool_input to handle_post_tool_use",
        )
        self.assertEqual(
            call_kwargs["tool_input"],
            {"file_path": "/test.py", "content": "print('hello')"},
            "Should pass correct tool_input value from hook_data",
        )

    @patch("pacemaker.langfuse.orchestrator.handle_post_tool_use")
    @patch("pacemaker.hook.database.initialize_database")
    @patch("pacemaker.hook.pacing_engine.run_pacing_check")
    @patch("pacemaker.hook.load_state")
    @patch("pacemaker.hook.load_config")
    def test_hook_extracts_tool_response_from_stdin(
        self,
        mock_load_config,
        mock_load_state,
        mock_pacing,
        mock_init_db,
        mock_handle_post_tool,
    ):
        """
        RED TEST: hook.py should extract tool_response from hook_data and pass
        to orchestrator.handle_post_tool_use().

        BUG: Currently hook_data is read but tool_response is not extracted.
        FIX: Extract tool_response and pass as parameter.
        """
        from pacemaker.hook import run_hook
        from unittest.mock import patch
        import sys
        from io import StringIO

        # Mock config
        mock_load_config.return_value = {
            "enabled": True,
            "langfuse_enabled": True,
            "poll_interval": 60,
        }

        # Mock state
        mock_load_state.return_value = {
            "session_id": "test-session",
            "tool_execution_count": 0,
            "in_subagent": False,
        }

        # Mock pacing result
        mock_pacing.return_value = {
            "polled": False,
            "decision": {"should_throttle": False},
        }

        # Mock orchestrator success
        mock_handle_post_tool.return_value = True

        # Prepare hook_data with tool_response
        hook_data = {
            "tool_name": "Write",
            "session_id": "test-session",
            "transcript_path": "/tmp/transcript.jsonl",
            "tool_response": "File written successfully (512 bytes)",  # THE KEY
        }

        # Patch stdin to provide hook_data
        with patch.object(sys, "stdin", StringIO(json.dumps(hook_data))):
            # ACT: Run the hook
            run_hook()

        # ASSERT: handle_post_tool_use should have been called WITH tool_response
        mock_handle_post_tool.assert_called_once()
        call_kwargs = mock_handle_post_tool.call_args[1]

        self.assertIn(
            "tool_response",
            call_kwargs,
            "Should pass tool_response to handle_post_tool_use",
        )
        self.assertEqual(
            call_kwargs["tool_response"],
            "File written successfully (512 bytes)",
            "Should pass correct tool_response value from hook_data",
        )


if __name__ == "__main__":
    unittest.main()
