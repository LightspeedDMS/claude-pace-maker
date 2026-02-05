"""
Unit tests for extract_task_tool_result() with agent_id correlation.

Tests verify that extract_task_tool_result() can filter Task results
by agent_id to correctly correlate subagent outputs when multiple
subagents produce Task results in the same transcript.

Key scenarios:
1. Multiple Task results, filter by agent_id returns correct one
2. No agent_id provided (None) returns most recent (backward compat)
3. Agent_id provided but no match found returns None
4. Agent_id in result with whitespace variations
"""

import json
import tempfile
from pathlib import Path
from typing import List, Dict, Any


from pacemaker.langfuse.orchestrator import extract_task_tool_result


def create_transcript_with_task_results(task_results: List[Dict[str, Any]]) -> str:
    """
    Create a temporary transcript JSONL file with Task tool_use and tool_result entries.

    Args:
        task_results: List of dicts with keys:
            - tool_use_id: str
            - content: str (result content, may include "agentId: XXX")

    Returns:
        Path to temporary transcript file
    """
    tmp_file = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl")

    # Write tool_use entries (assistant messages)
    for idx, result in enumerate(task_results):
        tool_use_entry = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": result["tool_use_id"],
                        "name": "Task",
                        "input": {"instructions": f"Test task {idx}"},
                    }
                ]
            },
        }
        tmp_file.write(json.dumps(tool_use_entry) + "\n")

    # Write tool_result entries (user messages)
    for result in task_results:
        tool_result_entry = {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": result["tool_use_id"],
                        "content": result["content"],
                    }
                ]
            },
        }
        tmp_file.write(json.dumps(tool_result_entry) + "\n")

    tmp_file.close()
    return tmp_file.name


def test_extract_task_result_with_agent_id_filter():
    """
    Test that extract_task_tool_result() filters by agent_id correctly.

    Given: Transcript with 3 Task results from different agents
    When: extract_task_tool_result(transcript, agent_id="bbb")
    Then: Returns only the result containing "agentId: bbb"
    """
    task_results = [
        {"tool_use_id": "task-001", "content": "First subagent output\n\nagentId: aaa"},
        {
            "tool_use_id": "task-002",
            "content": "Second subagent output\n\nagentId: bbb",
        },
        {"tool_use_id": "task-003", "content": "Third subagent output\n\nagentId: ccc"},
    ]

    transcript_path = create_transcript_with_task_results(task_results)

    try:
        # Filter by agent_id "bbb"
        result = extract_task_tool_result(transcript_path, agent_id="bbb")

        # Should return ONLY the second result
        assert result is not None
        assert "Second subagent output" in result
        assert "agentId: bbb" in result
        assert "First subagent" not in result
        assert "Third subagent" not in result

    finally:
        Path(transcript_path).unlink()


def test_extract_task_result_backward_compat_no_agent_id():
    """
    Test backward compatibility when agent_id is None.

    Given: Transcript with multiple Task results
    When: extract_task_tool_result(transcript, agent_id=None)
    Then: Returns the MOST RECENT Task result (existing behavior)
    """
    task_results = [
        {"tool_use_id": "task-001", "content": "First result"},
        {"tool_use_id": "task-002", "content": "Second result"},
        {"tool_use_id": "task-003", "content": "Third result - most recent"},
    ]

    transcript_path = create_transcript_with_task_results(task_results)

    try:
        # No agent_id provided (None)
        result = extract_task_tool_result(transcript_path, agent_id=None)

        # Should return most recent
        assert result is not None
        assert "Third result - most recent" in result

    finally:
        Path(transcript_path).unlink()


def test_extract_task_result_agent_id_not_found():
    """
    Test that None is returned when agent_id has no match.

    Given: Transcript with Task results but none matching agent_id
    When: extract_task_tool_result(transcript, agent_id="nonexistent")
    Then: Returns None (not most recent)
    """
    task_results = [
        {"tool_use_id": "task-001", "content": "First output\n\nagentId: aaa"},
        {"tool_use_id": "task-002", "content": "Second output\n\nagentId: bbb"},
    ]

    transcript_path = create_transcript_with_task_results(task_results)

    try:
        # Request nonexistent agent_id
        result = extract_task_tool_result(transcript_path, agent_id="zzz")

        # Should return None (no fallback to most recent)
        assert result is None

    finally:
        Path(transcript_path).unlink()


def test_extract_task_result_agent_id_whitespace_variations():
    """
    Test that agent_id matching handles whitespace variations.

    Given: Transcript with "agentId: xxx" with different whitespace
    When: extract_task_tool_result(transcript, agent_id="xxx")
    Then: Matches correctly regardless of spaces/newlines
    """
    task_results = [
        {
            "tool_use_id": "task-001",
            "content": "Output with extra spaces\n\nagentId:   abc123  \n",
        }
    ]

    transcript_path = create_transcript_with_task_results(task_results)

    try:
        # Should match despite whitespace
        result = extract_task_tool_result(transcript_path, agent_id="abc123")

        assert result is not None
        assert "Output with extra spaces" in result

    finally:
        Path(transcript_path).unlink()


def test_extract_task_result_agent_id_array_content():
    """
    Test agent_id filtering when content is array format.

    Given: Task result with content as array [{"type": "text", "text": "..."}]
    When: extract_task_tool_result(transcript, agent_id="xyz")
    Then: Correctly extracts text and filters by agent_id
    """
    tmp_file = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl")

    # Tool use
    tool_use_entry = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "task-001",
                    "name": "Task",
                    "input": {"instructions": "Test"},
                }
            ]
        },
    }
    tmp_file.write(json.dumps(tool_use_entry) + "\n")

    # Tool result with array content
    tool_result_entry = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "task-001",
                    "content": [
                        {"type": "text", "text": "Array content output\n\nagentId: xyz"}
                    ],
                }
            ]
        },
    }
    tmp_file.write(json.dumps(tool_result_entry) + "\n")
    tmp_file.close()

    try:
        result = extract_task_tool_result(tmp_file.name, agent_id="xyz")

        assert result is not None
        assert "Array content output" in result
        assert "agentId: xyz" in result

    finally:
        Path(tmp_file.name).unlink()


def test_extract_task_result_multiple_matches_returns_last():
    """
    Test that when multiple results match agent_id, the LAST one is returned.

    Given: Transcript with 2 Task results with same agent_id
    When: extract_task_tool_result(transcript, agent_id="dupe")
    Then: Returns the most recent match
    """
    task_results = [
        {"tool_use_id": "task-001", "content": "First occurrence\n\nagentId: dupe"},
        {"tool_use_id": "task-002", "content": "Second occurrence\n\nagentId: dupe"},
    ]

    transcript_path = create_transcript_with_task_results(task_results)

    try:
        result = extract_task_tool_result(transcript_path, agent_id="dupe")

        assert result is not None
        assert "Second occurrence" in result
        assert "First occurrence" not in result

    finally:
        Path(transcript_path).unlink()


def test_extract_task_result_empty_transcript():
    """
    Test that None is returned for empty transcript.

    Given: Empty transcript file
    When: extract_task_tool_result(transcript, agent_id="any")
    Then: Returns None
    """
    tmp_file = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl")
    tmp_file.close()

    try:
        result = extract_task_tool_result(tmp_file.name, agent_id="any")
        assert result is None

    finally:
        Path(tmp_file.name).unlink()
