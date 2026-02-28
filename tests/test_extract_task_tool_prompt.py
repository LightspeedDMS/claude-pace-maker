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

    def test_extract_agent_tool_prompt_by_observation_id(self, tmp_path):
        """
        Test extracting prompt from an "Agent" tool call using parent_observation_id.

        Given a parent transcript with an Agent tool call (new name in Claude Code 2.x+)
        When we search for the tool_use block with matching id (parent_observation_id)
        Then we should extract the prompt from tool_input["prompt"]

        This covers the case where Claude Code renamed "Task" -> "Agent".
        """
        transcript = tmp_path / "parent-session.jsonl"

        agent_tool_entry = {
            "type": "assistant",
            "uuid": "msg-789",
            "timestamp": "2025-01-01T10:00:00Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I'll invoke the tdd-engineer subagent"},
                    {
                        "type": "tool_use",
                        "id": "agent-tool-obs-999",  # This is parent_observation_id
                        "name": "Agent",  # New name in Claude Code 2.x+
                        "input": {
                            "subagent_type": "tdd-engineer",
                            "prompt": "Implement the new feature with full TDD",
                        },
                    },
                ],
            },
        }

        with open(transcript, "w") as f:
            f.write(json.dumps(agent_tool_entry) + "\n")

        from pacemaker.langfuse.orchestrator import extract_task_tool_prompt

        prompt = extract_task_tool_prompt(
            transcript_path=str(transcript), parent_observation_id="agent-tool-obs-999"
        )

        assert prompt == "Implement the new feature with full TDD"

    def test_extract_agent_tool_prompt_returns_last_when_no_id(self, tmp_path):
        """
        Test extracting last "Agent" tool prompt when no observation_id is provided.

        Given a parent transcript with an Agent tool call
        When we search without a parent_observation_id
        Then we should return the last Agent tool prompt found
        """
        transcript = tmp_path / "parent-session.jsonl"

        agent_tool_entry = {
            "type": "assistant",
            "uuid": "msg-321",
            "timestamp": "2025-01-01T11:00:00Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "agent-tool-obs-111",
                        "name": "Agent",
                        "input": {
                            "subagent_type": "code-reviewer",
                            "prompt": "Review the new module for quality",
                        },
                    }
                ],
            },
        }

        with open(transcript, "w") as f:
            f.write(json.dumps(agent_tool_entry) + "\n")

        from pacemaker.langfuse.orchestrator import extract_task_tool_prompt

        prompt = extract_task_tool_prompt(
            transcript_path=str(transcript), parent_observation_id=None
        )

        assert prompt == "Review the new module for quality"

    def test_find_task_results_with_agent_tool_name(self, tmp_path):
        """
        Test that _find_task_results() recognizes "Agent" tool name.

        Given a transcript where the subagent was invoked via "Agent" tool (not "Task")
        When _find_task_results() looks for the tool result
        Then it should find and return the result content

        This covers the case where Claude Code renamed "Task" -> "Agent".
        """
        transcript = tmp_path / "session.jsonl"

        # First entry: assistant invokes Agent tool
        invoke_entry = {
            "type": "assistant",
            "uuid": "msg-invoke",
            "timestamp": "2025-01-01T12:00:00Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "agent-call-id-777",
                        "name": "Agent",  # New tool name
                        "input": {
                            "subagent_type": "tdd-engineer",
                            "prompt": "Write tests for the parser",
                        },
                    }
                ],
            },
        }

        # Second entry: tool result returned
        result_entry = {
            "type": "user",
            "uuid": "msg-result",
            "timestamp": "2025-01-01T12:05:00Z",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "agent-call-id-777",
                        "content": "Tests written successfully. All 5 tests pass.",
                    }
                ],
            },
        }

        with open(transcript, "w") as f:
            f.write(json.dumps(invoke_entry) + "\n")
            f.write(json.dumps(result_entry) + "\n")

        from pacemaker.langfuse.orchestrator import _find_task_results

        tool_id_to_name = {"agent-call-id-777": "Agent"}
        result = _find_task_results(
            str(transcript), tool_id_to_name=tool_id_to_name, agent_id=None
        )

        assert result == "Tests written successfully. All 5 tests pass."
