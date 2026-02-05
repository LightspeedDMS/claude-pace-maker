#!/usr/bin/env python3
"""
JSONL parser for extracting session telemetry data.

Parses Claude Code transcript JSONL files to extract session metadata,
user information, and usage statistics for Langfuse telemetry.
"""

import json
from datetime import datetime
from typing import Dict, Any, Optional

from ..logger import log_warning
from ..api_client import get_user_email


def parse_session_metadata(transcript_path: str) -> Dict[str, Any]:
    """
    Extract session metadata from transcript.

    Args:
        transcript_path: Path to JSONL transcript file

    Returns:
        Dict with session_id, model, timestamp (defaults if not found)
    """
    metadata = {
        "session_id": "unknown",
        "model": "unknown",
        "timestamp": datetime.now().isoformat(),
    }

    try:
        with open(transcript_path, "r") as f:
            for line in f:
                entry = json.loads(line)

                # Look for session start or first message with session info
                if entry.get("type") == "session_start":
                    metadata["session_id"] = entry.get("session_id", "unknown")
                    metadata["model"] = entry.get("model", "unknown")
                    metadata["timestamp"] = entry.get(
                        "timestamp", datetime.now().isoformat()
                    )
                    break

                # Fallback: extract from first message metadata
                if "session_id" in entry:
                    metadata["session_id"] = entry["session_id"]
                # Model is nested in message.model for assistant messages
                message = entry.get("message", {})
                if isinstance(message, dict) and "model" in message:
                    metadata["model"] = message["model"]
                elif "model" in entry:
                    metadata["model"] = entry["model"]
                if "timestamp" in entry:
                    metadata["timestamp"] = entry["timestamp"]

    except FileNotFoundError:
        log_warning(
            "jsonl_parser", f"Transcript file not found: {transcript_path}", None
        )
    except json.JSONDecodeError as e:
        log_warning("jsonl_parser", f"Invalid JSON in transcript: {transcript_path}", e)
    except IOError as e:
        log_warning("jsonl_parser", f"Failed to read transcript: {transcript_path}", e)

    return metadata


def extract_user_id(transcript_path: str) -> Optional[str]:
    """
    Extract user_id from OAuth profile.

    First tries to extract email from transcript (backwards compatibility).
    Falls back to OAuth API if transcript doesn't contain email.

    Args:
        transcript_path: Path to JSONL transcript file

    Returns:
        User email from transcript or OAuth API, or None if not found
    """
    # First: try to extract from transcript (backwards compatibility)
    try:
        with open(transcript_path, "r") as f:
            for line in f:
                entry = json.loads(line)

                # Look for auth profile event
                if entry.get("type") == "auth_profile":
                    profile = entry.get("profile", {})
                    email = profile.get("email")
                    if email:
                        return email

                # Fallback: check for profile in metadata
                if "profile" in entry:
                    profile = entry["profile"]
                    if isinstance(profile, dict) and "email" in profile:
                        return profile["email"]

    except FileNotFoundError:
        log_warning(
            "jsonl_parser", f"Transcript file not found: {transcript_path}", None
        )
    except json.JSONDecodeError as e:
        log_warning("jsonl_parser", f"Invalid JSON in transcript: {transcript_path}", e)
    except IOError as e:
        log_warning("jsonl_parser", f"Failed to read transcript: {transcript_path}", e)

    # Second: fall back to OAuth API if transcript didn't have email
    return get_user_email()


def count_messages(transcript_path: str) -> int:
    """
    Count total messages in transcript.

    Args:
        transcript_path: Path to JSONL transcript file

    Returns:
        Total number of messages (user + assistant), 0 if file unreadable
    """
    count = 0

    try:
        with open(transcript_path, "r") as f:
            for line in f:
                entry = json.loads(line)

                # Count entries with message field
                if "message" in entry:
                    message = entry["message"]
                    if isinstance(message, dict) and "role" in message:
                        count += 1

    except FileNotFoundError:
        log_warning(
            "jsonl_parser", f"Transcript file not found: {transcript_path}", None
        )
    except json.JSONDecodeError as e:
        log_warning("jsonl_parser", f"Invalid JSON in transcript: {transcript_path}", e)
    except IOError as e:
        log_warning("jsonl_parser", f"Failed to read transcript: {transcript_path}", e)

    return count
