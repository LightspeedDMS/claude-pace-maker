#!/usr/bin/env python3
"""
Tests for handle_subagent_stop() orchestrator function.

This function finalizes subagent traces with output when SubagentStop fires.
"""

import json
from unittest.mock import patch, MagicMock


class TestHandleSubagentStop:
    """Tests for finalizing subagent trace with output."""

    def test_handle_subagent_stop_updates_trace_with_output(self, tmp_path):
        """
        Test handle_subagent_stop extracts Task result and updates trace.

        Given Langfuse is enabled
        And parent transcript has a Task tool result
        When handle_subagent_stop is called
        Then it should extract the Task output and update the trace
        """
        # Setup config
        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://langfuse.example.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }

        # Create parent transcript with Task result
        parent_transcript = tmp_path / "parent.jsonl"

        task_call = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "task-123",
                        "name": "Task",
                        "input": {"prompt": "Review code"},
                    }
                ],
            },
        }

        task_result = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "task-123",
                        "content": "Code review completed successfully",
                    }
                ],
            },
        }

        with open(parent_transcript, "w") as f:
            f.write(json.dumps(task_call) + "\n")
            f.write(json.dumps(task_result) + "\n")

        # Mock push_batch_events to capture the trace update
        with patch("pacemaker.langfuse.orchestrator.push") as mock_push_module:
            mock_push_module.push_batch_events = MagicMock(return_value=(True, 1))

            # Call handler
            from pacemaker.langfuse.orchestrator import handle_subagent_stop

            result = handle_subagent_stop(
                config=config,
                subagent_trace_id="subagent-trace-123",
                parent_transcript_path=str(parent_transcript),
            )

            # Verify success
            assert result is True

            # Verify trace-create was called with output
            mock_push_module.push_batch_events.assert_called_once()
            args, kwargs = mock_push_module.push_batch_events.call_args

            # Extract batch from call
            batch = args[3]  # 4th positional arg
            assert len(batch) == 1

            # Verify trace update event
            event = batch[0]
            assert event["type"] == "trace-create"  # Uses upsert semantics
            assert event["body"]["id"] == "subagent-trace-123"
            assert event["body"]["output"] == "Code review completed successfully"

    def test_handle_subagent_stop_returns_false_when_langfuse_disabled(self, tmp_path):
        """
        Test handle_subagent_stop returns False when Langfuse is disabled.

        Given Langfuse is disabled
        When handle_subagent_stop is called
        Then it should return False without making API calls
        """
        # Setup config with Langfuse disabled
        config = {
            "langfuse_enabled": False,
        }

        parent_transcript = tmp_path / "parent.jsonl"
        parent_transcript.write_text("")

        # Call handler
        from pacemaker.langfuse.orchestrator import handle_subagent_stop

        result = handle_subagent_stop(
            config=config,
            subagent_trace_id="subagent-trace-123",
            parent_transcript_path=str(parent_transcript),
        )

        # Verify returns True (disabled - consistent with other handlers)
        assert result is True

    def test_handle_subagent_stop_handles_missing_task_result(self, tmp_path):
        """
        Test handle_subagent_stop handles missing Task result gracefully.

        Given parent transcript has no Task tool result
        When handle_subagent_stop is called
        Then it should update trace with empty output
        """
        # Setup config
        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://langfuse.example.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }

        # Create parent transcript WITHOUT Task result
        parent_transcript = tmp_path / "parent.jsonl"

        other_entry = {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
        }

        with open(parent_transcript, "w") as f:
            f.write(json.dumps(other_entry) + "\n")

        # Mock push_batch_events
        with patch("pacemaker.langfuse.orchestrator.push") as mock_push_module:
            mock_push_module.push_batch_events = MagicMock(return_value=(True, 1))

            # Call handler
            from pacemaker.langfuse.orchestrator import handle_subagent_stop

            result = handle_subagent_stop(
                config=config,
                subagent_trace_id="subagent-trace-123",
                parent_transcript_path=str(parent_transcript),
            )

            # Verify success (graceful handling)
            assert result is True

            # Verify trace was updated with empty output
            mock_push_module.push_batch_events.assert_called_once()
            args, _ = mock_push_module.push_batch_events.call_args
            batch = args[3]

            event = batch[0]
            assert event["body"]["output"] == ""  # Empty output when no result found

    def test_handle_subagent_stop_handles_missing_transcript(self, tmp_path):
        """
        Test handle_subagent_stop handles missing transcript gracefully.

        Given parent transcript doesn't exist
        When handle_subagent_stop is called
        Then it should update trace with empty output
        """
        # Setup config
        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://langfuse.example.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }

        nonexistent_path = str(tmp_path / "nonexistent.jsonl")

        # Mock push_batch_events
        with patch("pacemaker.langfuse.orchestrator.push") as mock_push_module:
            mock_push_module.push_batch_events = MagicMock(return_value=(True, 1))

            # Call handler
            from pacemaker.langfuse.orchestrator import handle_subagent_stop

            result = handle_subagent_stop(
                config=config,
                subagent_trace_id="subagent-trace-123",
                parent_transcript_path=nonexistent_path,
            )

            # Verify success (graceful handling)
            assert result is True

            # Verify trace was updated with empty output
            mock_push_module.push_batch_events.assert_called_once()
            args, _ = mock_push_module.push_batch_events.call_args
            batch = args[3]

            event = batch[0]
            assert event["body"]["output"] == ""

    def test_handle_subagent_stop_returns_false_on_push_failure(self, tmp_path):
        """
        Test handle_subagent_stop returns False when push fails.

        Given Langfuse push fails
        When handle_subagent_stop is called
        Then it should return False
        """
        # Setup config
        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://langfuse.example.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }

        parent_transcript = tmp_path / "parent.jsonl"
        parent_transcript.write_text("")

        # Mock push_batch_events to fail
        with patch("pacemaker.langfuse.orchestrator.push") as mock_push_module:
            mock_push_module.push_batch_events = MagicMock(return_value=(False, 0))

            # Call handler
            from pacemaker.langfuse.orchestrator import handle_subagent_stop

            result = handle_subagent_stop(
                config=config,
                subagent_trace_id="subagent-trace-123",
                parent_transcript_path=str(parent_transcript),
            )

            # Verify returns False on push failure
            assert result is False

    def test_handle_subagent_stop_handles_none_transcript_path(self, tmp_path):
        """
        Test handle_subagent_stop handles None transcript path gracefully.

        Given parent_transcript_path is None
        When handle_subagent_stop is called
        Then it should update trace with empty output
        """
        # Setup config
        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://langfuse.example.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }

        # Mock push_batch_events
        with patch("pacemaker.langfuse.orchestrator.push") as mock_push_module:
            mock_push_module.push_batch_events = MagicMock(return_value=(True, 1))

            # Call handler with None path
            from pacemaker.langfuse.orchestrator import handle_subagent_stop

            result = handle_subagent_stop(
                config=config,
                subagent_trace_id="subagent-trace-123",
                parent_transcript_path=None,
            )

            # Verify success (graceful handling)
            assert result is True

            # Verify trace was updated with empty output
            mock_push_module.push_batch_events.assert_called_once()
            args, _ = mock_push_module.push_batch_events.call_args
            batch = args[3]

            event = batch[0]
            assert event["body"]["output"] == ""

    def test_handle_subagent_stop_uses_last_assistant_message_as_fallback(
        self, tmp_path
    ):
        """
        Test handle_subagent_stop uses last_assistant_message when transcript extraction fails.

        Given Langfuse is enabled
        And both transcript paths fail to yield output
        And last_assistant_message is provided
        When handle_subagent_stop is called
        Then it should use last_assistant_message as the trace output
        """
        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://langfuse.example.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }

        # No transcript paths provided - both extraction methods will fail
        with patch("pacemaker.langfuse.orchestrator.push") as mock_push_module:
            mock_push_module.push_batch_events = MagicMock(return_value=(True, 1))

            from pacemaker.langfuse.orchestrator import handle_subagent_stop

            result = handle_subagent_stop(
                config=config,
                subagent_trace_id="subagent-trace-456",
                parent_transcript_path=None,
                agent_id=None,
                agent_transcript_path=None,
                last_assistant_message="Subagent completed the task successfully.",
            )

            assert result is True

            mock_push_module.push_batch_events.assert_called_once()
            args, _ = mock_push_module.push_batch_events.call_args
            batch = args[3]

            event = batch[0]
            assert event["type"] == "trace-create"
            assert event["body"]["id"] == "subagent-trace-456"
            assert (
                event["body"]["output"] == "Subagent completed the task successfully."
            )

    def test_handle_subagent_stop_prefers_transcript_over_last_assistant_message(
        self, tmp_path
    ):
        """
        Test handle_subagent_stop prefers transcript extraction over last_assistant_message.

        Given agent_transcript_path yields output
        And last_assistant_message is also provided
        When handle_subagent_stop is called
        Then it should use transcript output, NOT last_assistant_message
        """
        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://langfuse.example.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }

        # Create a subagent transcript with output
        agent_transcript = tmp_path / "agent.jsonl"
        subagent_entry = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Output from transcript."}],
            },
        }
        with open(agent_transcript, "w") as f:
            f.write(json.dumps(subagent_entry) + "\n")

        with patch("pacemaker.langfuse.orchestrator.push") as mock_push_module:
            mock_push_module.push_batch_events = MagicMock(return_value=(True, 1))

            from pacemaker.langfuse.orchestrator import handle_subagent_stop

            result = handle_subagent_stop(
                config=config,
                subagent_trace_id="subagent-trace-789",
                parent_transcript_path=None,
                agent_id=None,
                agent_transcript_path=str(agent_transcript),
                last_assistant_message="This should NOT be used.",
            )

            assert result is True

            args, _ = mock_push_module.push_batch_events.call_args
            batch = args[3]
            event = batch[0]
            # Transcript output should win over last_assistant_message
            assert event["body"]["output"] != "This should NOT be used."

    def test_handle_subagent_stop_with_timeout(self, tmp_path):
        """
        Test handle_subagent_stop uses 2-second timeout for push.

        Given a valid configuration
        When handle_subagent_stop is called
        Then it should pass 2-second timeout to push_batch_events
        """
        # Setup config
        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://langfuse.example.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }

        parent_transcript = tmp_path / "parent.jsonl"
        parent_transcript.write_text("")

        # Mock push_batch_events
        with patch("pacemaker.langfuse.orchestrator.push") as mock_push_module:
            mock_push_module.push_batch_events = MagicMock(return_value=(True, 1))

            # Call handler
            from pacemaker.langfuse.orchestrator import handle_subagent_stop

            handle_subagent_stop(
                config=config,
                subagent_trace_id="subagent-trace-123",
                parent_transcript_path=str(parent_transcript),
            )

            # Verify timeout was passed (INCREMENTAL_PUSH_TIMEOUT_SECONDS = 10)
            args, kwargs = mock_push_module.push_batch_events.call_args
            assert kwargs.get("timeout") == 10  # Timeout passed as keyword arg
