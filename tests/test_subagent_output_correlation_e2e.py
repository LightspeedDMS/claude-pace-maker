"""
End-to-end integration test for subagent output correlation bug fix.

Tests the complete flow from hook.py through orchestrator.py to extract_task_tool_result()
to verify that when multiple subagents run sequentially, each subagent trace correctly
captures its OWN output (not another subagent's output).

This is the key bug we're fixing: before the fix, extract_task_tool_result() always
returned the most recent Task result regardless of which subagent it came from.
After the fix, it filters by agent_id to return the correct subagent's output.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch


from pacemaker.langfuse.orchestrator import (
    handle_subagent_stop,
    extract_task_tool_result,
)


def create_transcript_with_two_subagents():
    """
    Create a realistic transcript with two sequential subagent Task results.

    Simulates:
    1. Main context invokes code-reviewer subagent (agent_id: aaa1234)
    2. code-reviewer returns "Code looks good..."
    3. Main context invokes manual-test-executor subagent (agent_id: bbb5678)
    4. manual-test-executor returns "All tests passed..."

    Returns:
        Path to temporary transcript file
    """
    tmp_file = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl")

    # First subagent: code-reviewer
    tool_use_1 = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "task-reviewer-001",
                    "name": "Task",
                    "input": {
                        "subagent_type": "code-reviewer",
                        "instructions": "Review the code changes",
                    },
                }
            ]
        },
    }
    tmp_file.write(json.dumps(tool_use_1) + "\n")

    tool_result_1 = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "task-reviewer-001",
                    "content": "Code review completed. The implementation looks solid.\n\nagentId: aaa1234",
                }
            ]
        },
    }
    tmp_file.write(json.dumps(tool_result_1) + "\n")

    # Second subagent: manual-test-executor
    tool_use_2 = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "task-tester-002",
                    "name": "Task",
                    "input": {
                        "subagent_type": "manual-test-executor",
                        "instructions": "Execute manual tests",
                    },
                }
            ]
        },
    }
    tmp_file.write(json.dumps(tool_use_2) + "\n")

    tool_result_2 = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "task-tester-002",
                    "content": "All manual tests passed successfully. No issues found.\n\nagentId: bbb5678",
                }
            ]
        },
    }
    tmp_file.write(json.dumps(tool_result_2) + "\n")

    tmp_file.close()
    return tmp_file.name


def test_two_subagents_correct_output_correlation():
    """
    End-to-end test: Two sequential subagents each get their OWN output.

    Given: Transcript with 2 Task results from different subagents
    When: handle_subagent_stop() called for each with correct agent_id
    Then: Each subagent trace gets its OWN output (not the other's)

    This is the CRITICAL test that verifies the bug fix works end-to-end.
    """
    transcript_path = create_transcript_with_two_subagents()

    try:
        config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://test.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        }

        with patch(
            "pacemaker.langfuse.orchestrator.push.push_batch_events"
        ) as mock_push:
            mock_push.return_value = True

            # FIRST SUBAGENT: code-reviewer (agent_id: aaa1234)
            result1 = handle_subagent_stop(
                config=config,
                subagent_trace_id="trace-reviewer",
                parent_transcript_path=transcript_path,
                agent_id="aaa1234",
            )

            assert result1 is True
            assert mock_push.call_count == 1

            # Verify FIRST subagent got its OWN output
            first_call_batch = mock_push.call_args_list[0][0][3]
            first_output = first_call_batch[0]["body"]["output"]

            assert "Code review completed" in first_output
            assert "agentId: aaa1234" in first_output
            # CRITICAL: Should NOT contain second subagent's output
            assert "manual tests passed" not in first_output
            assert "agentId: bbb5678" not in first_output

            # SECOND SUBAGENT: manual-test-executor (agent_id: bbb5678)
            result2 = handle_subagent_stop(
                config=config,
                subagent_trace_id="trace-tester",
                parent_transcript_path=transcript_path,
                agent_id="bbb5678",
            )

            assert result2 is True
            assert mock_push.call_count == 2

            # Verify SECOND subagent got its OWN output
            second_call_batch = mock_push.call_args_list[1][0][3]
            second_output = second_call_batch[0]["body"]["output"]

            assert "All manual tests passed" in second_output
            assert "agentId: bbb5678" in second_output
            # CRITICAL: Should NOT contain first subagent's output
            assert "Code review completed" not in second_output
            assert "agentId: aaa1234" not in second_output

    finally:
        Path(transcript_path).unlink()


def test_extract_task_tool_result_integration_with_real_transcript():
    """
    Integration test: extract_task_tool_result() with real transcript format.

    Given: Realistic transcript with 2 Task results
    When: extract_task_tool_result() called with different agent_ids
    Then: Returns correct result for each agent_id
    """
    transcript_path = create_transcript_with_two_subagents()

    try:
        # Extract first subagent's output
        result1 = extract_task_tool_result(transcript_path, agent_id="aaa1234")
        assert result1 is not None
        assert "Code review completed" in result1
        assert "agentId: aaa1234" in result1
        assert "manual tests" not in result1

        # Extract second subagent's output
        result2 = extract_task_tool_result(transcript_path, agent_id="bbb5678")
        assert result2 is not None
        assert "All manual tests passed" in result2
        assert "agentId: bbb5678" in result2
        assert "Code review" not in result2

        # Backward compat: no agent_id returns most recent
        result_none = extract_task_tool_result(transcript_path, agent_id=None)
        assert result_none is not None
        assert "All manual tests passed" in result_none  # Most recent

    finally:
        Path(transcript_path).unlink()


def test_three_subagents_middle_one_extracted_correctly():
    """
    Edge case test: Three subagents, extract the MIDDLE one.

    Given: Transcript with 3 Task results (A, B, C)
    When: extract_task_tool_result() called with agent_id for B
    Then: Returns B's output (not A's or C's)
    """
    tmp_file = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl")

    # Create 3 Task results
    for idx, (task_id, agent_id, output) in enumerate(
        [
            ("task-001", "aaa", "First subagent output"),
            ("task-002", "bbb", "Second subagent output"),
            ("task-003", "ccc", "Third subagent output"),
        ]
    ):
        # tool_use
        tool_use = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": task_id,
                        "name": "Task",
                        "input": {"instructions": f"Task {idx}"},
                    }
                ]
            },
        }
        tmp_file.write(json.dumps(tool_use) + "\n")

        # tool_result
        tool_result = {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": task_id,
                        "content": f"{output}\n\nagentId: {agent_id}",
                    }
                ]
            },
        }
        tmp_file.write(json.dumps(tool_result) + "\n")

    tmp_file.close()

    try:
        # Extract MIDDLE subagent (bbb)
        result = extract_task_tool_result(tmp_file.name, agent_id="bbb")

        assert result is not None
        assert "Second subagent output" in result
        assert "agentId: bbb" in result
        # Should NOT contain first or third
        assert "First subagent" not in result
        assert "Third subagent" not in result

    finally:
        Path(tmp_file.name).unlink()


def test_bug_scenario_before_fix_would_fail():
    """
    Reproduce the original bug scenario that motivated this fix.

    BEFORE FIX: extract_task_tool_result() always returned most recent,
    so first subagent would incorrectly get second subagent's output.

    AFTER FIX: Each subagent gets its own output via agent_id filtering.
    """
    transcript_path = create_transcript_with_two_subagents()

    try:
        # Without agent_id (old behavior): returns most recent
        result_old = extract_task_tool_result(transcript_path, agent_id=None)
        assert "All manual tests passed" in result_old  # Most recent (second subagent)
        assert "agentId: bbb5678" in result_old

        # With agent_id (new behavior): returns correct one
        result_first = extract_task_tool_result(transcript_path, agent_id="aaa1234")
        assert "Code review completed" in result_first
        assert "agentId: aaa1234" in result_first

        result_second = extract_task_tool_result(transcript_path, agent_id="bbb5678")
        assert "All manual tests passed" in result_second
        assert "agentId: bbb5678" in result_second

        # Verify they're DIFFERENT (this would fail before the fix)
        assert result_first != result_second
        assert "Code review" in result_first and "Code review" not in result_second
        assert "manual tests" in result_second and "manual tests" not in result_first

    finally:
        Path(transcript_path).unlink()
