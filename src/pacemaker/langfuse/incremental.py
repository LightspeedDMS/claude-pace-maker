#!/usr/bin/env python3
"""
Langfuse incremental push logic.

Implements incremental parsing of transcript lines and trace updates:
- Parse only new lines (after last_pushed_line)
- Extract token usage and tool calls from new lines
- Create or update Langfuse trace with accumulated data
"""

import json
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

from ..logger import log_warning, log_debug


def _create_block_metadata(
    current_line: int, position: int, timestamp: str, message_uuid: str
) -> Dict[str, Any]:
    """Create base metadata dict for content blocks."""
    return {
        "line_number": current_line,
        "position_in_message": position,
        "timestamp": timestamp,
        "message_uuid": message_uuid,
    }


def _process_content_item(
    content_item: Dict[str, Any],
    current_line: int,
    position: int,
    timestamp: str,
    message_uuid: str,
) -> Optional[Dict[str, Any]]:
    """
    Process a single content item and return block dict or None.

    Args:
        content_item: Content dict from message.content array
        current_line: Line number in transcript
        position: Index in content array
        timestamp: Message timestamp
        message_uuid: Message UUID

    Returns:
        Content block dict or None if not text/tool_use
    """
    if not isinstance(content_item, dict):
        return None

    content_type = content_item.get("type")
    base = _create_block_metadata(current_line, position, timestamp, message_uuid)

    if content_type == "text":
        return {
            **base,
            "content_type": "text",
            "text": content_item.get("text", ""),
        }
    elif content_type == "tool_use":
        return {
            **base,
            "content_type": "tool_use",
            "tool_name": content_item.get("name", ""),
            "tool_id": content_item.get("id", ""),
            "tool_input": content_item.get("input", {}),
        }

    return None


def extract_content_blocks(
    transcript_path: str, start_line: int = 0
) -> List[Dict[str, Any]]:
    """
    Extract ALL content blocks from transcript for span creation.

    Parses assistant messages and extracts:
    - Text blocks (type="text") → for text spans
    - Tool use blocks (type="tool_use") → for tool spans

    This replaces the hook-data-driven approach where only tool info was available.
    By parsing transcript, we can create spans for ALL content (text + tools).

    Args:
        transcript_path: Path to JSONL transcript file
        start_line: Line number to start from (0 = parse all, N = skip first N lines)

    Returns:
        List of content block dicts with:
        - content_type: "text" or "tool_use"
        - line_number: Line in transcript (for state tracking)
        - position_in_message: Index in content array
        - timestamp: Message timestamp (ISO format)
        - message_uuid: Message UUID
        - For text blocks: "text" field
        - For tool_use blocks: "tool_name", "tool_id", "tool_input" fields
    """
    content_blocks = []
    current_line = 0

    try:
        with open(transcript_path, "r") as f:
            for line in f:
                current_line += 1

                # Skip lines before start_line
                if current_line <= start_line:
                    continue

                try:
                    entry = json.loads(line)

                    # Only process assistant messages
                    if entry.get("type") != "assistant":
                        continue

                    message = entry.get("message", {})
                    if not isinstance(message, dict):
                        continue

                    # Skip non-assistant roles (shouldn't happen if type="assistant")
                    if message.get("role") != "assistant":
                        continue

                    # Extract metadata
                    timestamp = entry.get("timestamp", "")
                    message_uuid = entry.get("uuid", "")

                    # Process content array
                    content = message.get("content", [])
                    if not isinstance(content, list):
                        continue

                    for position, content_item in enumerate(content):
                        block = _process_content_item(
                            content_item,
                            current_line,
                            position,
                            timestamp,
                            message_uuid,
                        )
                        if block is not None:
                            content_blocks.append(block)

                except json.JSONDecodeError:
                    # Skip malformed lines
                    log_debug(
                        "incremental", f"Skipping malformed JSON at line {current_line}"
                    )
                    continue

    except FileNotFoundError:
        log_warning("incremental", f"Transcript not found: {transcript_path}", None)
    except IOError as e:
        log_warning("incremental", f"Failed to read transcript: {transcript_path}", e)

    return content_blocks


def parse_incremental_lines(
    transcript_path: str, last_pushed_line: int
) -> Dict[str, Any]:
    """
    Parse only new lines from transcript since last push.

    AC1/AC2: Parse only new lines (unpushed) from transcript

    Args:
        transcript_path: Path to JSONL transcript file
        last_pushed_line: Line number of last successful push (0 = parse all)

    Returns:
        Dict with:
        - lines_parsed: Number of new lines parsed
        - last_line: Total line count after parsing
        - token_usage: Dict with input_tokens, output_tokens, cache_read_tokens
        - tool_calls: List of tool names from new lines
    """
    lines_parsed = 0
    current_line = 0
    token_usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
    }
    tool_calls = []

    try:
        with open(transcript_path, "r") as f:
            for line in f:
                current_line += 1

                # Skip lines already pushed
                if current_line <= last_pushed_line:
                    continue

                # Parse new line
                lines_parsed += 1

                try:
                    entry = json.loads(line)

                    # Extract token usage from nested message.usage structure
                    message = entry.get("message", {})
                    if isinstance(message, dict) and "usage" in message:
                        usage_data = message["usage"]
                        token_usage["input_tokens"] += usage_data.get("input_tokens", 0)
                        token_usage["output_tokens"] += usage_data.get(
                            "output_tokens", 0
                        )
                        token_usage["cache_read_tokens"] += usage_data.get(
                            "cache_read_input_tokens", 0
                        )

                    # Extract tool calls from message.content array
                    if isinstance(message, dict) and "content" in message:
                        content = message.get("content", [])
                        if isinstance(content, list):
                            for item in content:
                                if (
                                    isinstance(item, dict)
                                    and item.get("type") == "tool_use"
                                ):
                                    tool_name = item.get("name")
                                    if tool_name:
                                        tool_calls.append(tool_name)

                except json.JSONDecodeError:
                    # Skip malformed lines
                    log_debug(
                        "incremental", f"Skipping malformed JSON at line {current_line}"
                    )
                    continue

    except FileNotFoundError:
        log_warning("incremental", f"Transcript not found: {transcript_path}", None)
    except IOError as e:
        log_warning("incremental", f"Failed to read transcript: {transcript_path}", e)

    return {
        "lines_parsed": lines_parsed,
        "last_line": current_line,
        "token_usage": token_usage,
        "tool_calls": tool_calls,
    }


def create_or_update_trace(
    session_id: str,
    model: str,
    user_id: Optional[str],
    incremental_data: Dict[str, Any],
    existing_trace: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create new trace or update existing trace with incremental data.

    AC4: Single trace per session - accumulate token usage and append spans

    Langfuse hierarchy:
    - Session: Claude Code session (auto-created via sessionId)
    - Trace: Conversation flow within session
    - Metadata: Model, tool calls, token summary

    Args:
        session_id: Session identifier (used for both trace_id and sessionId)
        model: Model name used
        user_id: User identifier (OAuth email)
        incremental_data: Dict with token_usage, tool_calls from parse_incremental_lines
        existing_trace: Existing trace from previous push (None for first push)

    Returns:
        Updated trace dict ready for Langfuse API
    """
    if existing_trace is None:
        # First push - create new trace linked to session
        trace = {
            "id": session_id,
            "sessionId": session_id,  # Links trace to Langfuse session (auto-created)
            "name": f"claude-code-session-{session_id[:8]}",
            "userId": user_id or "unknown",
            "metadata": {
                "model": model,
                "tool_calls": incremental_data["tool_calls"],
                "tool_count": len(incremental_data["tool_calls"]),
                "input_tokens": incremental_data["token_usage"]["input_tokens"],
                "output_tokens": incremental_data["token_usage"]["output_tokens"],
                "cache_read_tokens": incremental_data["token_usage"][
                    "cache_read_tokens"
                ],
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        return trace

    else:
        # Subsequent push - update existing trace
        trace = existing_trace.copy()

        # Get existing metadata or create new one
        if "metadata" not in trace:
            trace["metadata"] = {}

        # Type annotation for mypy - metadata is Dict[str, Any]
        metadata: Dict[str, Any] = trace["metadata"]  # type: ignore[assignment]

        # Append new tool calls
        existing_tools = metadata.get("tool_calls", [])
        metadata["tool_calls"] = existing_tools + incremental_data["tool_calls"]
        metadata["tool_count"] = len(metadata["tool_calls"])

        # Accumulate token usage in metadata
        metadata["input_tokens"] = (
            metadata.get("input_tokens", 0)
            + incremental_data["token_usage"]["input_tokens"]
        )
        metadata["output_tokens"] = (
            metadata.get("output_tokens", 0)
            + incremental_data["token_usage"]["output_tokens"]
        )
        metadata["cache_read_tokens"] = (
            metadata.get("cache_read_tokens", 0)
            + incremental_data["token_usage"]["cache_read_tokens"]
        )

        # Update trace with modified metadata
        trace["metadata"] = metadata

        return trace


def create_generation(
    trace_id: str,
    model: str,
    incremental_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Create generation observation for token tracking.

    Langfuse expects token usage on generation observations (not trace level).
    Generation is an observation within a trace.

    TRACE-PER-TURN: Generation links to trace_id (not session_id).
    Each trace has its own generation with per-trace token usage.

    Args:
        trace_id: Trace identifier (used as traceId to link generation to trace)
        model: Model name used
        incremental_data: Dict with token_usage from parse_incremental_lines

    Returns:
        Generation dict ready for Langfuse API
    """
    import uuid

    token_usage = incremental_data["token_usage"]

    # Build usage dict with type annotation for mypy
    usage: Dict[str, int] = {
        "input": token_usage["input_tokens"],
        "output": token_usage["output_tokens"],
        "total": token_usage["input_tokens"] + token_usage["output_tokens"],
    }

    # Add cache tokens if present
    if token_usage.get("cache_read_tokens", 0) > 0:
        usage["cache_read"] = token_usage["cache_read_tokens"]

    generation = {
        "id": f"{trace_id}-gen-{str(uuid.uuid4())[:8]}",  # Unique ID for generation
        "traceId": trace_id,  # Link to parent trace (trace-per-turn architecture)
        "type": "generation",
        "name": "claude-code-generation",
        "model": model,
        "usage": usage,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return generation


def create_batch_event(
    session_id: str,
    model: str,
    user_id: Optional[str],
    incremental_data: Dict[str, Any],
    existing_trace: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Create batch event for Langfuse ingestion API.

    Langfuse ingestion API expects batch array with event objects:
    - type: "trace-create" or "trace-update"
    - body: trace/generation object
    - timestamp: ISO timestamp

    Args:
        session_id: Session identifier
        model: Model name used
        user_id: User identifier (OAuth email)
        incremental_data: Dict with token_usage, tool_calls from parse_incremental_lines
        existing_trace: Existing trace from previous push (None for first push)

    Returns:
        List of batch event objects: [trace_event, generation_event]
    """
    # Create/update trace
    trace = create_or_update_trace(
        session_id=session_id,
        model=model,
        user_id=user_id,
        incremental_data=incremental_data,
        existing_trace=existing_trace,
    )

    # Create generation with accumulated token usage from trace
    # Extract accumulated tokens from trace metadata
    accumulated_tokens = {
        "input_tokens": trace["metadata"]["input_tokens"],
        "output_tokens": trace["metadata"]["output_tokens"],
        "cache_read_tokens": trace["metadata"]["cache_read_tokens"],
    }
    accumulated_data = {
        "token_usage": accumulated_tokens,
        "tool_calls": incremental_data["tool_calls"],  # Not accumulated in generation
        "lines_parsed": incremental_data["lines_parsed"],
        "last_line": incremental_data["last_line"],
    }

    generation = create_generation(
        trace_id=session_id,  # Note: session_id used as trace_id (old architecture)
        model=model,
        incremental_data=accumulated_data,  # Use accumulated tokens
    )

    # Determine event types based on whether this is first push
    is_first_push = existing_trace is None
    trace_event_type = "trace-create" if is_first_push else "trace-update"
    generation_event_type = (
        "generation-create" if is_first_push else "generation-update"
    )

    # Build batch event array - Langfuse requires id and timestamp at event level
    now = datetime.now(timezone.utc).isoformat()
    batch = [
        {
            "id": trace["id"],  # Event ID (required by Langfuse)
            "timestamp": now,  # Event timestamp (required by Langfuse)
            "type": trace_event_type,
            "body": trace,
        },
        {
            "id": generation["id"],  # Event ID (required by Langfuse)
            "timestamp": now,  # Event timestamp (required by Langfuse)
            "type": generation_event_type,
            "body": generation,
        },
    ]

    return batch
