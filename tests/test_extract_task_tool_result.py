#!/usr/bin/env python3
"""
Tests for extract_task_tool_result helper function.

This helper extracts the most recent Task tool result from parent transcript
to capture subagent output when SubagentStop fires.
"""

import json


class TestExtractTaskToolResult:
    """Tests for extracting Task tool result from parent transcript."""

    def test_extract_task_tool_result_finds_most_recent(self, tmp_path):
        """
        Test extracting most recent Task tool result from transcript.

        Given a parent transcript with multiple Tool results including Task
        When we extract the Task tool result
        Then we should get the most recent Task tool result content
        """
        # Create parent transcript with Task tool result
        transcript = tmp_path / "parent-session.jsonl"

        # Earlier Task tool result
        older_task_result = {
            "type": "user",
            "uuid": "msg-100",
            "timestamp": "2025-01-01T10:00:00Z",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "task-older",
                        "content": "Older subagent output here",
                    }
                ],
            },
        }

        # Different tool result (not Task)
        other_tool_result = {
            "type": "user",
            "uuid": "msg-101",
            "timestamp": "2025-01-01T10:01:00Z",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "read-123",
                        "content": "File contents...",
                    }
                ],
            },
        }

        # Most recent Task tool result
        recent_task_result = {
            "type": "user",
            "uuid": "msg-102",
            "timestamp": "2025-01-01T10:02:00Z",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "task-recent",
                        "content": "Recent subagent completed successfully",
                    }
                ],
            },
        }

        # Need to map tool_use_id to tool name - look back in transcript
        # for the tool_use blocks that created these IDs
        task_older_call = {
            "type": "assistant",
            "uuid": "msg-099",
            "timestamp": "2025-01-01T09:59:00Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "task-older",
                        "name": "Task",
                        "input": {"prompt": "Older task"},
                    }
                ],
            },
        }

        read_call = {
            "type": "assistant",
            "uuid": "msg-100a",
            "timestamp": "2025-01-01T10:00:30Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "read-123",
                        "name": "Read",
                        "input": {"file_path": "/some/file"},
                    }
                ],
            },
        }

        task_recent_call = {
            "type": "assistant",
            "uuid": "msg-101a",
            "timestamp": "2025-01-01T10:01:30Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "task-recent",
                        "name": "Task",
                        "input": {"prompt": "Recent task"},
                    }
                ],
            },
        }

        with open(transcript, "w") as f:
            f.write(json.dumps(task_older_call) + "\n")
            f.write(json.dumps(older_task_result) + "\n")
            f.write(json.dumps(read_call) + "\n")
            f.write(json.dumps(other_tool_result) + "\n")
            f.write(json.dumps(task_recent_call) + "\n")
            f.write(json.dumps(recent_task_result) + "\n")

        # Extract most recent Task tool result
        from pacemaker.langfuse.orchestrator import extract_task_tool_result

        result = extract_task_tool_result(str(transcript))

        # Verify we got the most recent Task result
        assert result == "Recent subagent completed successfully"

    def test_extract_task_tool_result_returns_none_when_not_found(self, tmp_path):
        """
        Test extracting Task tool result returns None when no Task results exist.

        Given a transcript with only non-Task tool results
        When we extract Task tool result
        Then we should get None
        """
        transcript = tmp_path / "parent-session.jsonl"

        # Only Read tool result (not Task)
        read_result = {
            "type": "user",
            "uuid": "msg-101",
            "timestamp": "2025-01-01T10:01:00Z",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "read-123",
                        "content": "File contents...",
                    }
                ],
            },
        }

        read_call = {
            "type": "assistant",
            "uuid": "msg-100",
            "timestamp": "2025-01-01T10:00:00Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "read-123",
                        "name": "Read",
                        "input": {"file_path": "/some/file"},
                    }
                ],
            },
        }

        with open(transcript, "w") as f:
            f.write(json.dumps(read_call) + "\n")
            f.write(json.dumps(read_result) + "\n")

        # Extract Task tool result
        from pacemaker.langfuse.orchestrator import extract_task_tool_result

        result = extract_task_tool_result(str(transcript))

        # Verify returns None when no Task results found
        assert result is None

    def test_extract_task_tool_result_handles_empty_transcript(self, tmp_path):
        """
        Test extracting Task tool result handles empty transcript gracefully.

        Given an empty transcript file
        When we extract Task tool result
        Then we should get None without crashing
        """
        transcript = tmp_path / "empty-session.jsonl"
        transcript.write_text("")

        # Extract Task tool result
        from pacemaker.langfuse.orchestrator import extract_task_tool_result

        result = extract_task_tool_result(str(transcript))

        # Verify graceful failure
        assert result is None

    def test_extract_task_tool_result_handles_missing_file(self, tmp_path):
        """
        Test extracting Task tool result handles missing file gracefully.

        Given a non-existent transcript path
        When we extract Task tool result
        Then we should get None without crashing
        """
        transcript = tmp_path / "nonexistent.jsonl"

        # Extract Task tool result
        from pacemaker.langfuse.orchestrator import extract_task_tool_result

        result = extract_task_tool_result(str(transcript))

        # Verify graceful failure
        assert result is None

    def test_extract_task_tool_result_handles_malformed_json(self, tmp_path):
        """
        Test extracting Task tool result skips malformed JSON lines.

        Given a transcript with some malformed JSON lines
        When we extract Task tool result
        Then we should skip bad lines and return valid Task result if found
        """
        transcript = tmp_path / "malformed-session.jsonl"

        # Create transcript with malformed line followed by valid Task result
        with open(transcript, "w") as f:
            f.write("{ malformed json\n")  # Bad JSON
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "task-123",
                                    "name": "Task",
                                    "input": {"prompt": "Test"},
                                }
                            ],
                        },
                    }
                )
                + "\n"
            )
            f.write("another bad line\n")  # Bad JSON
            f.write(
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "task-123",
                                    "content": "Task completed",
                                }
                            ],
                        },
                    }
                )
                + "\n"
            )

        # Extract Task tool result
        from pacemaker.langfuse.orchestrator import extract_task_tool_result

        result = extract_task_tool_result(str(transcript))

        # Verify we got the valid result despite malformed lines
        assert result == "Task completed"

    def test_extract_task_tool_result_with_array_content(self, tmp_path):
        """
        Test extracting Task tool result handles array content format.

        Given a Task tool result with content as array of strings
        When we extract the result
        Then we should join the array elements
        """
        transcript = tmp_path / "parent-session.jsonl"

        task_call = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "task-456",
                        "name": "Task",
                        "input": {"prompt": "Test"},
                    }
                ],
            },
        }

        # Tool result with array content
        task_result = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "task-456",
                        "content": ["Line 1\n", "Line 2\n", "Line 3"],
                    }
                ],
            },
        }

        with open(transcript, "w") as f:
            f.write(json.dumps(task_call) + "\n")
            f.write(json.dumps(task_result) + "\n")

        # Extract Task tool result
        from pacemaker.langfuse.orchestrator import extract_task_tool_result

        result = extract_task_tool_result(str(transcript))

        # Verify array was joined
        assert result == "Line 1\nLine 2\nLine 3"
