#!/usr/bin/env python3
"""
Langfuse trace transformer.

Transforms session telemetry data into Langfuse trace format.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone


def create_trace(
    session_id: str,
    user_id: Optional[str],
    model: str,
    token_usage: Dict[str, int],
    tool_calls: List[str],
    timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create Langfuse trace from session data.

    Args:
        session_id: Session identifier (becomes trace_id)
        user_id: User identifier (OAuth email)
        model: Model name used
        token_usage: Dict with input_tokens, output_tokens, cache_read_tokens
        tool_calls: List of tool names called in session
        timestamp: ISO timestamp (optional, defaults to now with UTC timezone)

    Returns:
        Langfuse trace dict ready for API submission
    """
    trace = {
        "id": session_id,
        "name": f"claude-code-session-{session_id[:8]}",
        "userId": user_id or "unknown",
        "metadata": {
            "model": model,
            "tool_calls": tool_calls,
            "tool_count": len(tool_calls),
        },
        "usage": {
            "input": token_usage.get("input_tokens", 0),
            "output": token_usage.get("output_tokens", 0),
            "total": token_usage.get("input_tokens", 0)
            + token_usage.get("output_tokens", 0),
        },
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
    }

    # Add cache tokens if present
    if token_usage.get("cache_read_tokens", 0) > 0:
        trace["usage"]["cache_read"] = token_usage["cache_read_tokens"]

    return trace
