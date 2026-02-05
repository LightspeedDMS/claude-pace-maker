#!/usr/bin/env python3
"""
Tool call extractor for telemetry.

Extracts tool call names from Claude Code transcript files.
"""

import json
from typing import List

from ..logger import log_warning


def extract_tool_calls(transcript_path: str) -> List[str]:
    """
    Extract tool calls from transcript.

    Parses JSONL transcript and extracts tool names from tool_use entries.
    Preserves order and includes duplicate calls.

    Args:
        transcript_path: Path to JSONL transcript file

    Returns:
        List of tool names in order they were called
    """
    tool_calls = []

    try:
        with open(transcript_path, "r") as f:
            for line in f:
                entry = json.loads(line)

                # Look for tool_use entries
                if entry.get("type") == "tool_use" and "name" in entry:
                    tool_calls.append(entry["name"])

    except FileNotFoundError:
        log_warning(
            "tool_call_extractor", f"Transcript file not found: {transcript_path}", None
        )
    except json.JSONDecodeError as e:
        log_warning(
            "tool_call_extractor", f"Invalid JSON in transcript: {transcript_path}", e
        )
    except IOError as e:
        log_warning(
            "tool_call_extractor", f"Failed to read transcript: {transcript_path}", e
        )

    return tool_calls
