#!/usr/bin/env python3
"""
Tests for generation observation creation in handle_subagent_stop.

Verifies that SubagentStop hook creates generation-create events with proper
token usage from subagent transcripts so Langfuse can compute totalCost correctly.

BUG FIX: Subagent traces were missing cost data because handle_subagent_stop()
only pushed trace updates without generation observations.
"""

from unittest.mock import patch

from pacemaker.langfuse.orchestrator import handle_subagent_stop


class TestSubagentStopGenerationObservation:
    """Test generation observation creation in SubagentStop hook."""

    def setup_method(self):
        """Set up test fixtures."""
        self.config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://test.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
            "db_path": ":memory:",
        }

    @patch(
        "pacemaker.langfuse.orchestrator.jsonl_parser.parse_session_metadata",
        return_value={"model": "claude-sonnet-4-5-20250929"},
    )
    @patch(
        "pacemaker.langfuse.orchestrator.incremental.parse_incremental_lines",
        return_value={
            "token_usage": {
                "input_tokens": 3000,
                "output_tokens": 1500,
                "cache_read_tokens": 50000,
            },
            "lines_parsed": 8,
            "last_line": 8,
            "tool_calls": [],
        },
    )
    @patch("pacemaker.langfuse.orchestrator.extract_subagent_output")
    @patch(
        "pacemaker.langfuse.orchestrator.sanitize_trace",
        side_effect=lambda batch, db_path: batch,
    )
    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    def test_subagent_stop_creates_generation_with_tokens(
        self,
        mock_push,
        mock_sanitize,
        mock_extract_output,
        mock_parse_incremental,
        mock_parse_metadata,
    ):
        """
        Test that handle_subagent_stop creates generation with token usage.

        When subagent transcript has tokens, SubagentStop should create 2 events:
        1. trace-create (trace update with output + endTime)
        2. generation-create (with usage data for cost calculation)
        """
        subagent_trace_id = "subagent-trace-abc123"
        agent_transcript_path = "/tmp/subagent_transcript.jsonl"

        # Mock subagent output extraction
        mock_extract_output.return_value = "Subagent completed task successfully"

        # Mock push to capture batch
        captured_batch = None

        def capture_push(base_url, pk, sk, batch, timeout=10):
            nonlocal captured_batch
            captured_batch = batch
            return (True, len(batch))

        mock_push.side_effect = capture_push

        # Call handle_subagent_stop with agent_transcript_path
        result = handle_subagent_stop(
            config=self.config,
            subagent_trace_id=subagent_trace_id,
            parent_transcript_path=None,  # Not needed when agent_transcript_path provided
            agent_id=None,
            agent_transcript_path=agent_transcript_path,
        )

        assert result is True
        assert captured_batch is not None
        assert len(captured_batch) == 2, "Should have trace-create + generation-create"

        # Verify trace-create event (trace update)
        trace_event = captured_batch[0]
        assert trace_event["type"] == "trace-create"
        assert trace_event["body"]["id"] == subagent_trace_id
        assert trace_event["body"]["output"] == "Subagent completed task successfully"
        assert "endTime" in trace_event["body"]

        # Verify generation-create event
        gen_event = captured_batch[1]
        assert gen_event["type"] == "generation-create"
        gen_body = gen_event["body"]

        # Verify generation body structure
        assert gen_body["traceId"] == subagent_trace_id
        assert gen_body["name"] == "claude-code-generation"
        assert gen_body["model"] == "claude-sonnet-4-5-20250929"
        assert "startTime" in gen_body
        assert "id" in gen_body  # Generation must have unique ID

        # Verify usage fields
        usage = gen_body["usage"]
        assert usage["input"] == 3000
        assert usage["output"] == 1500
        assert usage["total"] == 54500  # 3000 + 1500 + 50000
        # Cache tokens must be in usageDetails (Langfuse drops them from usage)
        usage_details = gen_body["usageDetails"]
        assert usage_details["cache_read_input_tokens"] == 50000

        # Verify parse_incremental_lines was called with correct args
        mock_parse_incremental.assert_called_once_with(agent_transcript_path, 0)

        # Verify parse_session_metadata was called
        mock_parse_metadata.assert_called_once_with(agent_transcript_path)

    @patch("pacemaker.langfuse.orchestrator.extract_subagent_output")
    @patch(
        "pacemaker.langfuse.orchestrator.sanitize_trace",
        side_effect=lambda batch, db_path: batch,
    )
    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    def test_subagent_stop_no_generation_when_no_agent_transcript(
        self,
        mock_push,
        mock_sanitize,
        mock_extract_output,
    ):
        """
        Test that generation is NOT created when agent_transcript_path is None.

        When using fallback mode (no agent transcript), only trace-create should
        be sent. This maintains backward compatibility.
        """
        subagent_trace_id = "subagent-trace-fallback"
        parent_transcript_path = "/tmp/parent_transcript.jsonl"

        # Mock extract from parent (fallback path)
        with patch(
            "pacemaker.langfuse.orchestrator.extract_task_tool_result"
        ) as mock_extract_task:
            mock_extract_task.return_value = "Fallback output from parent transcript"

            captured_batch = None

            def capture_push(base_url, pk, sk, batch, timeout=10):
                nonlocal captured_batch
                captured_batch = batch
                return (True, len(batch))

            mock_push.side_effect = capture_push

            # Call handle_subagent_stop WITHOUT agent_transcript_path
            result = handle_subagent_stop(
                config=self.config,
                subagent_trace_id=subagent_trace_id,
                parent_transcript_path=parent_transcript_path,
                agent_id=None,
                agent_transcript_path=None,  # Fallback mode
            )

            assert result is True
            assert captured_batch is not None
            assert (
                len(captured_batch) == 1
            ), "Should only have trace-create (no generation in fallback mode)"
            assert captured_batch[0]["type"] == "trace-create"
            assert captured_batch[0]["body"]["output"] == (
                "Fallback output from parent transcript"
            )

    @patch(
        "pacemaker.langfuse.orchestrator.jsonl_parser.parse_session_metadata",
        return_value={"model": "claude-opus-4-6"},
    )
    @patch(
        "pacemaker.langfuse.orchestrator.incremental.parse_incremental_lines",
        return_value={
            "token_usage": {
                "input_tokens": 2000,
                "output_tokens": 800,
                "cache_read_tokens": 0,
            },
            "lines_parsed": 5,
            "last_line": 5,
            "tool_calls": [],
        },
    )
    @patch("pacemaker.langfuse.orchestrator.extract_subagent_output")
    @patch(
        "pacemaker.langfuse.orchestrator.sanitize_trace",
        side_effect=lambda batch, db_path: batch,
    )
    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    def test_subagent_stop_generation_omits_cache_read_when_zero(
        self,
        mock_push,
        mock_sanitize,
        mock_extract_output,
        mock_parse_incremental,
        mock_parse_metadata,
    ):
        """
        Test that cache_read is omitted from usage when zero.

        When cache_read_tokens is 0, the usage dict should NOT include
        the cache_read field at all.
        """
        subagent_trace_id = "subagent-trace-no-cache"
        agent_transcript_path = "/tmp/subagent_no_cache.jsonl"

        mock_extract_output.return_value = "Output without cache usage"

        captured_batch = None

        def capture_push(base_url, pk, sk, batch, timeout=10):
            nonlocal captured_batch
            captured_batch = batch
            return (True, len(batch))

        mock_push.side_effect = capture_push

        result = handle_subagent_stop(
            config=self.config,
            subagent_trace_id=subagent_trace_id,
            parent_transcript_path=None,
            agent_id=None,
            agent_transcript_path=agent_transcript_path,
        )

        assert result is True
        assert captured_batch is not None
        assert len(captured_batch) == 2

        # Verify generation usage
        gen_event = captured_batch[1]
        usage = gen_event["body"]["usage"]

        assert usage["input"] == 2000
        assert usage["output"] == 800
        assert usage["total"] == 2800
        assert "cache_read_input_tokens" not in usage  # Should NOT be in usage
        # usageDetails should also not have cache_read when zero
        usage_details = gen_event["body"]["usageDetails"]
        assert "cache_read_input_tokens" not in usage_details

    @patch(
        "pacemaker.langfuse.orchestrator.incremental.parse_incremental_lines",
        return_value={
            "token_usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
            },
            "lines_parsed": 0,
            "last_line": 0,
            "tool_calls": [],
        },
    )
    @patch("pacemaker.langfuse.orchestrator.extract_subagent_output")
    @patch(
        "pacemaker.langfuse.orchestrator.sanitize_trace",
        side_effect=lambda batch, db_path: batch,
    )
    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    def test_subagent_stop_skips_generation_when_no_tokens(
        self,
        mock_push,
        mock_sanitize,
        mock_extract_output,
        mock_parse_incremental,
    ):
        """
        Test that generation is NOT created when no token usage exists.

        If subagent transcript has no tokens or zero tokens, only trace-create
        should be sent.
        """
        subagent_trace_id = "subagent-trace-no-tokens"
        agent_transcript_path = "/tmp/subagent_no_tokens.jsonl"

        mock_extract_output.return_value = "Output with no tokens"

        captured_batch = None

        def capture_push(base_url, pk, sk, batch, timeout=10):
            nonlocal captured_batch
            captured_batch = batch
            return (True, len(batch))

        mock_push.side_effect = capture_push

        result = handle_subagent_stop(
            config=self.config,
            subagent_trace_id=subagent_trace_id,
            parent_transcript_path=None,
            agent_id=None,
            agent_transcript_path=agent_transcript_path,
        )

        assert result is True
        assert captured_batch is not None
        assert len(captured_batch) == 1, "Should only have trace-create (no generation)"
        assert captured_batch[0]["type"] == "trace-create"
