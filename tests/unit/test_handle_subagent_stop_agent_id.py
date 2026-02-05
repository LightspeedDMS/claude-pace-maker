"""
Unit tests for handle_subagent_stop() with agent_id parameter.

Tests verify that handle_subagent_stop() correctly passes agent_id
to extract_task_tool_result() for filtering subagent outputs.

Key scenarios:
1. handle_subagent_stop() with agent_id passes it to extract_task_tool_result()
2. handle_subagent_stop() with None agent_id works (backward compat)
3. Integration: agent_id flows correctly through the call chain
"""

from unittest.mock import patch


from pacemaker.langfuse.orchestrator import (
    handle_subagent_stop,
)


def test_handle_subagent_stop_passes_agent_id_to_extractor():
    """
    Test that handle_subagent_stop() passes agent_id to extract_task_tool_result().

    Given: handle_subagent_stop() called with agent_id="test123"
    When: Langfuse is enabled and configured
    Then: extract_task_tool_result() is called with agent_id="test123"
    """
    config = {
        "langfuse_enabled": True,
        "langfuse_base_url": "https://test.langfuse.com",
        "langfuse_public_key": "pk-test",
        "langfuse_secret_key": "sk-test",
    }

    subagent_trace_id = "trace-abc123"
    parent_transcript_path = "/tmp/test_transcript.jsonl"
    agent_id = "test123"

    with patch(
        "pacemaker.langfuse.orchestrator.extract_task_tool_result"
    ) as mock_extract:
        with patch(
            "pacemaker.langfuse.orchestrator.push.push_batch_events"
        ) as mock_push:
            # Configure mocks
            mock_extract.return_value = "Subagent output\n\nagentId: test123"
            mock_push.return_value = True

            # Call handle_subagent_stop with agent_id
            result = handle_subagent_stop(
                config=config,
                subagent_trace_id=subagent_trace_id,
                parent_transcript_path=parent_transcript_path,
                agent_id=agent_id,
            )

            # Verify extract_task_tool_result was called with agent_id
            mock_extract.assert_called_once_with(
                parent_transcript_path, agent_id=agent_id
            )

            # Verify result
            assert result is True


def test_handle_subagent_stop_backward_compat_no_agent_id():
    """
    Test backward compatibility when agent_id is not provided.

    Given: handle_subagent_stop() called without agent_id parameter
    When: Langfuse is enabled
    Then: extract_task_tool_result() is called with agent_id=None (default)
    """
    config = {
        "langfuse_enabled": True,
        "langfuse_base_url": "https://test.langfuse.com",
        "langfuse_public_key": "pk-test",
        "langfuse_secret_key": "sk-test",
    }

    subagent_trace_id = "trace-xyz789"
    parent_transcript_path = "/tmp/test_transcript.jsonl"

    with patch(
        "pacemaker.langfuse.orchestrator.extract_task_tool_result"
    ) as mock_extract:
        with patch(
            "pacemaker.langfuse.orchestrator.push.push_batch_events"
        ) as mock_push:
            # Configure mocks
            mock_extract.return_value = "Generic subagent output"
            mock_push.return_value = True

            # Call handle_subagent_stop WITHOUT agent_id
            result = handle_subagent_stop(
                config=config,
                subagent_trace_id=subagent_trace_id,
                parent_transcript_path=parent_transcript_path,
                # No agent_id parameter
            )

            # Verify extract_task_tool_result was called with agent_id=None
            mock_extract.assert_called_once_with(parent_transcript_path, agent_id=None)

            # Verify result
            assert result is True


def test_handle_subagent_stop_with_explicit_none_agent_id():
    """
    Test that explicit agent_id=None works correctly.

    Given: handle_subagent_stop() called with agent_id=None explicitly
    When: Langfuse is enabled
    Then: extract_task_tool_result() is called with agent_id=None
    """
    config = {
        "langfuse_enabled": True,
        "langfuse_base_url": "https://test.langfuse.com",
        "langfuse_public_key": "pk-test",
        "langfuse_secret_key": "sk-test",
    }

    subagent_trace_id = "trace-none-test"
    parent_transcript_path = "/tmp/test_transcript.jsonl"

    with patch(
        "pacemaker.langfuse.orchestrator.extract_task_tool_result"
    ) as mock_extract:
        with patch(
            "pacemaker.langfuse.orchestrator.push.push_batch_events"
        ) as mock_push:
            # Configure mocks
            mock_extract.return_value = "Output without agent_id"
            mock_push.return_value = True

            # Call with explicit agent_id=None
            result = handle_subagent_stop(
                config=config,
                subagent_trace_id=subagent_trace_id,
                parent_transcript_path=parent_transcript_path,
                agent_id=None,
            )

            # Verify extract_task_tool_result was called with agent_id=None
            mock_extract.assert_called_once_with(parent_transcript_path, agent_id=None)

            assert result is True


def test_handle_subagent_stop_disabled_langfuse():
    """
    Test that handle_subagent_stop() returns True when Langfuse is disabled.

    Given: Langfuse is disabled in config
    When: handle_subagent_stop() is called with agent_id
    Then: Returns True without calling extract_task_tool_result()
    """
    config = {
        "langfuse_enabled": False,
    }

    subagent_trace_id = "trace-disabled"
    parent_transcript_path = "/tmp/test_transcript.jsonl"
    agent_id = "should-not-be-used"

    with patch(
        "pacemaker.langfuse.orchestrator.extract_task_tool_result"
    ) as mock_extract:
        result = handle_subagent_stop(
            config=config,
            subagent_trace_id=subagent_trace_id,
            parent_transcript_path=parent_transcript_path,
            agent_id=agent_id,
        )

        # Should not call extract when disabled
        mock_extract.assert_not_called()

        # Should still return True (not an error)
        assert result is True


def test_handle_subagent_stop_no_parent_transcript():
    """
    Test that handle_subagent_stop() handles missing parent_transcript_path.

    Given: parent_transcript_path is None
    When: handle_subagent_stop() is called
    Then: Does not call extract_task_tool_result(), uses empty output
    """
    config = {
        "langfuse_enabled": True,
        "langfuse_base_url": "https://test.langfuse.com",
        "langfuse_public_key": "pk-test",
        "langfuse_secret_key": "sk-test",
    }

    subagent_trace_id = "trace-no-parent"
    agent_id = "test-agent"

    with patch(
        "pacemaker.langfuse.orchestrator.extract_task_tool_result"
    ) as mock_extract:
        with patch(
            "pacemaker.langfuse.orchestrator.push.push_batch_events"
        ) as mock_push:
            mock_push.return_value = True

            # Call with parent_transcript_path=None
            result = handle_subagent_stop(
                config=config,
                subagent_trace_id=subagent_trace_id,
                parent_transcript_path=None,
                agent_id=agent_id,
            )

            # Should not call extract when no transcript path
            mock_extract.assert_not_called()

            # Should still push with empty output
            assert mock_push.called
            batch_arg = mock_push.call_args[0][3]  # Fourth positional arg is batch
            assert batch_arg[0]["body"]["output"] == ""

            assert result is True


def test_handle_subagent_stop_extractor_returns_none():
    """
    Test that handle_subagent_stop() handles when extractor returns None.

    Given: extract_task_tool_result() returns None (no matching result)
    When: handle_subagent_stop() is called with agent_id
    Then: Uses empty string as output, still pushes to Langfuse
    """
    config = {
        "langfuse_enabled": True,
        "langfuse_base_url": "https://test.langfuse.com",
        "langfuse_public_key": "pk-test",
        "langfuse_secret_key": "sk-test",
    }

    subagent_trace_id = "trace-no-match"
    parent_transcript_path = "/tmp/test_transcript.jsonl"
    agent_id = "nonexistent-agent"

    with patch(
        "pacemaker.langfuse.orchestrator.extract_task_tool_result"
    ) as mock_extract:
        with patch(
            "pacemaker.langfuse.orchestrator.push.push_batch_events"
        ) as mock_push:
            # extractor returns None (no match)
            mock_extract.return_value = None
            mock_push.return_value = True

            result = handle_subagent_stop(
                config=config,
                subagent_trace_id=subagent_trace_id,
                parent_transcript_path=parent_transcript_path,
                agent_id=agent_id,
            )

            # Verify extract was called with agent_id
            mock_extract.assert_called_once_with(
                parent_transcript_path, agent_id=agent_id
            )

            # Should still push with empty output
            assert mock_push.called
            batch_arg = mock_push.call_args[0][3]
            assert batch_arg[0]["body"]["output"] == ""

            assert result is True
