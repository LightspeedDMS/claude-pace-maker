#!/usr/bin/env python3
"""
Transcript reader functions for extracting user messages.

This module provides functions to extract user messages from JSONL transcripts
for use in intent validation context building.
"""

import json
import logging
from typing import List

logger = logging.getLogger(__name__)


def get_first_n_user_messages(transcript_path: str, n: int = 5) -> List[str]:
    """
    Extract first N user messages from JSONL transcript.

    This function provides the "original mission context" by extracting
    the first N user messages from the beginning of the conversation.

    Args:
        transcript_path: Path to JSONL transcript file
        n: Number of user messages to extract (default: 5)

    Returns:
        List of user message texts (first N only)
    """
    try:
        user_messages: List[str] = []

        with open(transcript_path, "r") as f:
            for line in f:
                # Stop if we already have N messages
                if len(user_messages) >= n:
                    break

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
                    user_messages.append(message_text)

        return user_messages

    except Exception as e:
        logger.debug(f"Failed to extract first N user messages from transcript: {e}")
        return []


def get_last_n_user_messages(transcript_path: str, n: int = 5) -> List[str]:
    """
    Extract last N user messages from JSONL transcript.

    This function provides the "recent context" by extracting the last N
    user messages from the conversation.

    Args:
        transcript_path: Path to JSONL transcript file
        n: Number of user messages to extract (default: 5)

    Returns:
        List of user message texts (last N only)
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

        # Return last N messages
        if len(all_user_messages) >= n:
            return all_user_messages[-n:]
        else:
            return all_user_messages

    except Exception as e:
        logger.debug(f"Failed to extract last N user messages from transcript: {e}")
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
