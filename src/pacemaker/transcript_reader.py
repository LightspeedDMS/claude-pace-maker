#!/usr/bin/env python3
"""
Transcript reader functions for extracting user messages.

This module provides functions to extract user messages from JSONL transcripts
for use in intent validation context building.
"""

import json
import logging
from typing import List, Union

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 10000


def get_all_user_messages(transcript_path: str) -> List[str]:
    """
    Extract ALL user messages from JSONL transcript.

    This function provides complete user intent by extracting all user messages
    from the entire conversation, ensuring no context is lost.

    Args:
        transcript_path: Path to JSONL transcript file

    Returns:
        List of all user message texts in chronological order
    """
    try:
        all_user_messages = []

        with open(transcript_path, "r") as f:
            for line in f:
                entry = json.loads(line)

                # Extract message content
                message = entry.get("message", {})
                role = message.get("role")

                # Only process user messages
                if role != "user":
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

                if text_parts:
                    message_text = "\n".join(text_parts)
                    all_user_messages.append(message_text)

        return all_user_messages

    except Exception as e:
        logger.debug(f"Failed to extract all user messages from transcript: {e}")
        return []


def get_last_n_assistant_messages(transcript_path: str, n: int = 5) -> List[str]:
    """
    Extract last N assistant messages from JSONL transcript.

    This function provides the "recent assistant responses" to show what
    Claude actually did in the conversation.

    Args:
        transcript_path: Path to JSONL transcript file
        n: Number of assistant messages to extract (default: 5)

    Returns:
        List of assistant message texts (last N only, most recent last)
    """
    try:
        all_assistant_messages = []

        with open(transcript_path, "r") as f:
            for line in f:
                entry = json.loads(line)

                # Extract message content
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

                if text_parts:
                    message_text = "\n".join(text_parts)
                    all_assistant_messages.append(message_text)

        # Return last N messages
        if len(all_assistant_messages) >= n:
            return all_assistant_messages[-n:]
        else:
            return all_assistant_messages

    except Exception as e:
        logger.debug(
            f"Failed to extract last N assistant messages from transcript: {e}"
        )
        return []


def _extract_message_parts(content: Union[list, str]) -> dict:
    """Extract text and tool parts from message content."""
    text_parts = []
    tools = []

    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    tools.append(
                        {
                            "name": block.get("name", "unknown"),
                            "input": block.get("input", {}),
                        }
                    )
    elif isinstance(content, str):
        text_parts.append(content)

    return {"text": "\n".join(text_parts) if text_parts else "", "tools": tools}


def _format_message_with_tools(msg: dict) -> str:
    """Format a message including its tool information."""
    parts = [msg["text"]] if msg["text"] else []
    for tool in msg["tools"]:
        tool_str = f"[TOOL: {tool['name']}]\n"
        inp = tool["input"]
        for key in ["file_path", "content", "old_string", "new_string"]:
            if key in inp:
                tool_str += f"{key}: {inp[key]}\n"
        parts.append(tool_str)
    return "\n".join(parts)


def get_last_n_messages_for_validation(transcript_path: str, n: int = 5) -> List[str]:
    """
    Extract last N assistant messages for pre-tool validation context.

    Special formatting:
    - Messages 1 to N-1: Text only (tool parameters/code stripped)
    - Message N (most recent): Full content including tool parameters

    Args:
        transcript_path: Path to JSONL transcript file
        n: Number of messages to extract (default: 5)

    Returns:
        List of formatted message texts
    """
    try:
        messages = []

        with open(transcript_path, "r") as f:
            for line in f:
                entry = json.loads(line)
                message = entry.get("message", {})

                if message.get("role") != "assistant":
                    continue

                content = message.get("content", [])
                msg_parts = _extract_message_parts(content)
                messages.append(msg_parts)

        # Get last N messages
        recent = messages[-n:] if len(messages) >= n else messages

        # Format: text-only for first N-1, full for last
        result = []
        for i, msg in enumerate(recent):
            if i == len(recent) - 1 and msg["tools"]:
                # Last message: include tool info
                result.append(_format_message_with_tools(msg))
            else:
                # Earlier messages: text only
                result.append(msg["text"])

        return result

    except Exception as e:
        logger.debug(f"Failed to extract messages for validation: {e}")
        return []
