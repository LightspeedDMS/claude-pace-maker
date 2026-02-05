#!/usr/bin/env python3
"""
Tests for Langfuse span creation for tool calls.

Spans represent individual tool executions (Read, Edit, Bash, etc.) within a trace.
Each PostToolUse hook creates a span linked to the current trace.
"""

from datetime import datetime, timezone

from pacemaker.langfuse.span import create_span


class TestSpanCreation:
    """Test span creation for tool calls within traces."""

    def test_create_span_for_tool_call(self):
        """
        Test creating span for a tool call.

        Spans represent individual tool executions (Read, Edit, Bash, etc.)
        within a trace.
        """
        trace_id = "test-session-abc-turn-1"
        tool_name = "Read"
        tool_input = {"file_path": "/path/to/file.py"}
        tool_output = "File contents here..."
        start_time = datetime.now(timezone.utc)
        end_time = datetime.now(timezone.utc)

        span = create_span(
            trace_id=trace_id,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output=tool_output,
            start_time=start_time,
            end_time=end_time,
        )

        # Span must link to trace
        assert span["traceId"] == trace_id
        assert span["name"] == f"Tool - {tool_name}"

        # Span must have input/output
        assert span["input"] == tool_input
        assert span["output"] == tool_output

        # Span must have timing
        assert "startTime" in span
        assert "endTime" in span

        # Span must have unique ID
        assert "id" in span
        assert span["id"] != trace_id

    def test_create_span_applies_filtering(self):
        """
        Test that span creation applies truncation and redaction.

        Tool outputs must be filtered before creating spans.
        """
        trace_id = "test-session-def-turn-1"
        tool_name = "Bash"
        tool_input = {"command": "cat secrets.env"}
        # Output contains secret (should be redacted)
        tool_output = "API_KEY=sk-1234567890abcdefghij\nDATABASE=postgres"
        start_time = datetime.now(timezone.utc)
        end_time = datetime.now(timezone.utc)

        span = create_span(
            trace_id=trace_id,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output=tool_output,
            start_time=start_time,
            end_time=end_time,
        )

        # Output should be redacted
        assert "[REDACTED]" in span["output"]
        assert "sk-1234567890abcdefghij" not in span["output"]

    def test_create_span_truncates_large_output(self):
        """
        Test that span creation truncates large tool outputs.

        Prevents sending massive outputs to Langfuse.
        """
        trace_id = "test-session-ghi-turn-1"
        tool_name = "Grep"
        tool_input = {"pattern": "function", "path": "/src"}
        # Create 20KB output (exceeds 10KB limit)
        tool_output = "A" * 20480
        start_time = datetime.now(timezone.utc)
        end_time = datetime.now(timezone.utc)

        span = create_span(
            trace_id=trace_id,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output=tool_output,
            start_time=start_time,
            end_time=end_time,
        )

        # Output should be truncated
        assert "[TRUNCATED" in span["output"]
        assert len(span["output"].encode("utf-8")) <= 10240 + 100  # Max + marker

    def test_create_multiple_spans_for_trace(self):
        """
        Test creating multiple spans for same trace.

        Each tool call in a turn creates separate span linked to same trace.
        """
        trace_id = "test-session-jkl-turn-1"
        now = datetime.now(timezone.utc)

        # Create 3 spans for same trace (Read, Edit, Bash)
        spans = []
        for tool_name in ["Read", "Edit", "Bash"]:
            span = create_span(
                trace_id=trace_id,
                tool_name=tool_name,
                tool_input={},
                tool_output=f"{tool_name} output",
                start_time=now,
                end_time=now,
            )
            spans.append(span)

        # All spans link to same trace
        assert all(s["traceId"] == trace_id for s in spans)

        # Each span has unique ID
        span_ids = [s["id"] for s in spans]
        assert len(span_ids) == len(set(span_ids))  # All unique
