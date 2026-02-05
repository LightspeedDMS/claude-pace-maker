#!/usr/bin/env python3
"""
Token usage extractor for telemetry.

Extracts token usage statistics from Claude Code transcript files.
"""

import json
from typing import Dict

from ..logger import log_warning


def extract_token_usage(transcript_path: str) -> Dict[str, int]:
    """
    Extract token usage from transcript.

    Args:
        transcript_path: Path to JSONL transcript file

    Returns:
        Dict with input_tokens, output_tokens, cache_read_tokens
    """
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
    }

    try:
        with open(transcript_path, "r") as f:
            for line in f:
                entry = json.loads(line)

                # Look for usage metadata in message responses
                if "usage" in entry:
                    usage_data = entry["usage"]
                    usage["input_tokens"] += usage_data.get("input_tokens", 0)
                    usage["output_tokens"] += usage_data.get("output_tokens", 0)
                    usage["cache_read_tokens"] += usage_data.get(
                        "cache_read_input_tokens", 0
                    )

    except FileNotFoundError:
        log_warning(
            "token_extractor", f"Transcript file not found: {transcript_path}", None
        )
    except json.JSONDecodeError as e:
        log_warning(
            "token_extractor", f"Invalid JSON in transcript: {transcript_path}", e
        )
    except IOError as e:
        log_warning(
            "token_extractor", f"Failed to read transcript: {transcript_path}", e
        )

    return usage
