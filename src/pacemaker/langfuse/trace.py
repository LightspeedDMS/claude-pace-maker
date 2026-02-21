#!/usr/bin/env python3
"""
Langfuse trace creation for user turns.

Each UserPromptSubmit creates a NEW trace (not one trace per session).
Traces are linked to the session via sessionId field.
"""

import json
from datetime import datetime, timezone
from typing import Optional


def create_trace_for_turn(
    session_id: str,
    trace_id: str,
    user_message: str,
    user_id: Optional[str],
    project_context: Optional[dict] = None,
    model: Optional[str] = None,
) -> dict:
    """
    Create new trace for a user turn.

    Each UserPromptSubmit creates a new trace. The trace represents
    the conversation turn from user prompt through tool calls to response.

    Args:
        session_id: Claude Code session ID (used as sessionId for grouping)
        trace_id: Unique trace ID for this turn (e.g., "{session_id}-turn-{uuid}")
        user_message: User's prompt text
        user_id: User identifier (OAuth email, or None)
        project_context: Optional dict with project metadata (project_path, project_name, git_remote, git_branch)
        model: Optional model name (e.g., "claude-opus-4-5-20250929")

    Returns:
        Trace dict ready for Langfuse API (trace-create event body)
    """
    # Truncate user message for trace name (max 100 chars total to avoid UI clutter)
    MAX_NAME_LENGTH = 100
    PREFIX = "User prompt: "
    max_message_length = MAX_NAME_LENGTH - len(PREFIX)

    if len(user_message) > max_message_length:
        truncated_message = user_message[: max_message_length - 3] + "..."
    else:
        truncated_message = user_message

    # Build metadata with project context if provided
    metadata = {}
    if project_context:
        metadata["project_path"] = project_context.get("project_path")
        metadata["project_name"] = project_context.get("project_name")
        metadata["git_remote"] = project_context.get("git_remote")
        metadata["git_branch"] = project_context.get("git_branch")

    # Add model to metadata if provided
    if model:
        metadata["model"] = model

    # Build trace structure for Langfuse API
    trace = {
        "id": trace_id,
        "sessionId": session_id,  # Links trace to session (auto-created in Langfuse)
        "name": f"User prompt: {truncated_message}",
        "userId": user_id or "unknown",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "input": user_message,  # Full user message (not truncated)
        "metadata": metadata,
    }

    return trace


def finalize_trace_with_output(
    trace_id: str,
    transcript_path: str,
    trace_start_line: int,
) -> dict:
    """
    Finalize trace with Claude's output from transcript.

    Reads transcript from trace_start_line forward to find assistant responses.
    Extracts text content from the LAST assistant message and creates a
    trace-update event with output field and token counts.

    Args:
        trace_id: Trace ID to finalize
        transcript_path: Path to transcript JSONL file
        trace_start_line: Line number where this trace started (0-indexed)

    Returns:
        Trace-update event dict with output field and metadata containing token counts
    """
    from ..logger import log_warning
    from . import incremental

    # Read transcript lines from trace_start_line forward
    assistant_messages = []

    try:
        with open(transcript_path, "r") as f:
            for line_num, line in enumerate(f):
                # Skip lines before trace_start_line
                if line_num < trace_start_line:
                    continue

                try:
                    entry = json.loads(line)
                    message = entry.get("message", {})
                    role = message.get("role")

                    # Only process assistant messages
                    if role != "assistant":
                        continue

                    content = message.get("content", [])

                    # Extract text from content blocks
                    text_parts = []
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text_parts.append(block.get("text", ""))
                    elif isinstance(content, str):
                        text_parts.append(content)

                    # Store this assistant message
                    if text_parts:
                        message_text = "\n".join(text_parts)
                        assistant_messages.append(message_text)
                    else:
                        # No text content (e.g., only tool calls)
                        assistant_messages.append("")

                except json.JSONDecodeError:
                    # Skip malformed lines
                    continue

    except FileNotFoundError:
        # Transcript doesn't exist - log warning and return empty output
        log_warning("trace", f"Transcript not found: {transcript_path}", None)

    # Extract the LAST NON-EMPTY assistant response (most recent with actual text)
    # Many assistant messages are empty (thinking/tool_use only), skip those
    non_empty_messages = [m for m in assistant_messages if m]
    output = non_empty_messages[-1] if non_empty_messages else ""

    # Strip intel line from output (§ marker with metadata)
    if output:
        from ..intel.parser import strip_intel_line

        output = strip_intel_line(output)

    # Extract token counts from trace_start_line forward
    incremental_data = incremental.parse_incremental_lines(
        transcript_path, trace_start_line
    )
    token_usage = incremental_data.get("token_usage", {})

    # Create trace-update event with output and token counts
    now = datetime.now(timezone.utc)
    trace_update = {
        "id": trace_id,
        "output": output,
        "timestamp": now.isoformat(),
        "endTime": now.isoformat(),
        "metadata": {
            "input_tokens": token_usage.get("input_tokens", 0),
            "output_tokens": token_usage.get("output_tokens", 0),
            "cache_read_tokens": token_usage.get("cache_read_tokens", 0),
            "cache_creation_tokens": token_usage.get("cache_creation_tokens", 0),
        },
    }

    return trace_update
