#!/usr/bin/env python3
"""
Langfuse span creation for tool calls.

Spans represent individual tool executions (Read, Edit, Bash, etc.) within a trace.
Each PostToolUse hook creates a span linked to the current trace.
"""

import uuid
from datetime import datetime
from typing import Dict, Any

from .filter import filter_tool_result


def create_text_span(
    trace_id: str,
    text: str,
    start_time: datetime,
    end_time: datetime,
    line_number: int,
) -> Dict[str, Any]:
    """
    Create span for assistant text output within a trace.

    Text spans represent Claude's text responses between tool calls.
    This allows Langfuse to show the full conversation flow, not just tools.

    Args:
        trace_id: Trace ID to link span to (current_trace_id from state)
        text: Text content from assistant message
        start_time: When response was generated
        end_time: When response completed
        line_number: Line number in transcript (for unique span ID)

    Returns:
        Span dict ready for Langfuse API (span-create event body)
    """
    # Generate unique span ID incorporating line number
    span_id = f"{trace_id}-text-{line_number}-{str(uuid.uuid4())[:8]}"

    # Build span structure for Langfuse API
    span = {
        "id": span_id,
        "traceId": trace_id,
        "name": "Assistant Response",
        "startTime": start_time.isoformat(),
        "endTime": end_time.isoformat(),
        "output": text,  # Text content in output field
        "metadata": {
            "type": "text",  # Mark as text span (vs tool span)
        },
    }

    return span


def create_span(
    trace_id: str,
    tool_name: str,
    tool_input: Dict[str, Any],
    tool_output: str,
    start_time: datetime,
    end_time: datetime,
) -> Dict[str, Any]:
    """
    Create span for a tool call within a trace.

    Spans are observations within traces that represent tool executions.
    Each tool call (Read, Edit, Bash, etc.) creates a separate span.

    Args:
        trace_id: Trace ID to link span to (current_trace_id from state)
        tool_name: Name of tool (Read, Edit, Bash, Grep, etc.)
        tool_input: Tool input parameters (dict)
        tool_output: Tool output/result (string)
        start_time: When tool execution started
        end_time: When tool execution completed

    Returns:
        Span dict ready for Langfuse API (span-create event body)
    """
    # Generate unique span ID
    span_id = f"{trace_id}-span-{tool_name.lower()}-{str(uuid.uuid4())[:8]}"

    # Apply filtering (redaction + truncation) to output
    filtered_output = filter_tool_result(
        output=tool_output,
        max_bytes=10240,  # 10KB limit
        enable_redaction=True,
    )

    # Build span structure for Langfuse API
    span = {
        "id": span_id,
        "traceId": trace_id,
        "name": f"Tool - {tool_name}",
        "startTime": start_time.isoformat(),
        "endTime": end_time.isoformat(),
        "input": tool_input,
        "output": filtered_output,
        "metadata": {
            "tool": tool_name,
        },
    }

    return span
