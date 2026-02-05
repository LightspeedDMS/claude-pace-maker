#!/usr/bin/env python3
"""
Tests for extract_task_tool_prompt helper function.

This helper extracts the Task tool prompt from parent transcript
using parent_observation_id to find the matching tool_use block.
"""

import json


class TestExtractTaskToolPrompt:
    """Tests for extracting Task tool prompt from parent transcript."""

    def test_extract_task_tool_prompt_by_observation_id(self, tmp_path):
        """
        Test extracting Task tool prompt using parent_observation_id.

        Given a parent transcript with a Task tool call
        When we search for the tool_use block with matching id (parent_observation_id)
        Then we should extract the prompt from tool_input["prompt"]
        """
        # Create parent transcript with Task tool call
        transcript = tmp_path / "parent-session.jsonl"

        # Assistant message with Task tool call
        task_tool_entry = {
            "type": "assistant",
            "uuid": "msg-123",
            "timestamp": "2025-01-01T10:00:00Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I'll invoke the code-reviewer subagent"},
                    {
                        "type": "tool_use",
                        "id": "task-tool-obs-456",  # This is parent_observation_id
                        "name": "Task",
                        "input": {
                            "subagent_type": "code-reviewer",
                            "prompt": "Review the authentication code for security issues",
                        },
                    },
                ],
            },
        }

        with open(transcript, "w") as f:
            f.write(json.dumps(task_tool_entry) + "\n")

        # Extract prompt
        from pacemaker.langfuse.orchestrator import extract_task_tool_prompt

        prompt = extract_task_tool_prompt(
            transcript_path=str(transcript), parent_observation_id="task-tool-obs-456"
        )

        # Verify prompt extracted
        assert prompt == "Review the authentication code for security issues"

    def test_extract_task_tool_prompt_returns_none_if_not_found(self, tmp_path):
        """
        Test extracting Task tool prompt returns None if observation_id not found.

        Given a parent transcript without the matching observation_id
        When we search for a non-existent parent_observation_id
        Then we should return None
        """
        # Create parent transcript with different tool call
        transcript = tmp_path / "parent-session.jsonl"

        task_tool_entry = {
            "type": "assistant",
            "uuid": "msg-123",
            "timestamp": "2025-01-01T10:00:00Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "different-obs-id",
                        "name": "Task",
                        "input": {
                            "subagent_type": "code-reviewer",
                            "prompt": "Review code",
                        },
                    }
                ],
            },
        }

        with open(transcript, "w") as f:
            f.write(json.dumps(task_tool_entry) + "\n")

        # Extract prompt with non-matching ID
        from pacemaker.langfuse.orchestrator import extract_task_tool_prompt

        prompt = extract_task_tool_prompt(
            transcript_path=str(transcript),
            parent_observation_id="task-tool-obs-456",  # Doesn't exist
        )

        # Verify returns None
        assert prompt is None

    def test_extract_task_tool_prompt_handles_missing_prompt_field(self, tmp_path):
        """
        Test extracting Task tool prompt handles missing prompt field gracefully.

        Given a Task tool call without a prompt field
        When we search for it
        Then we should return None or empty string
        """
        # Create parent transcript with Task tool call but no prompt
        transcript = tmp_path / "parent-session.jsonl"

        task_tool_entry = {
            "type": "assistant",
            "uuid": "msg-123",
            "timestamp": "2025-01-01T10:00:00Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "task-tool-obs-456",
                        "name": "Task",
                        "input": {
                            "subagent_type": "code-reviewer"
                            # No prompt field
                        },
                    }
                ],
            },
        }

        with open(transcript, "w") as f:
            f.write(json.dumps(task_tool_entry) + "\n")

        # Extract prompt
        from pacemaker.langfuse.orchestrator import extract_task_tool_prompt

        prompt = extract_task_tool_prompt(
            transcript_path=str(transcript), parent_observation_id="task-tool-obs-456"
        )

        # Verify returns None or empty string (graceful failure)
        assert prompt is None or prompt == ""
