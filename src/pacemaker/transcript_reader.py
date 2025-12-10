#!/usr/bin/env python3
"""
Transcript reader functions for extracting user messages.

This module provides functions to extract user messages from JSONL transcripts
for use in intent validation context building.
"""

import json
from typing import List, Union

from .logger import log_warning

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
        log_warning("transcript_reader", "Failed to extract user messages", e)
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
        log_warning("transcript_reader", "Failed to extract assistant messages", e)
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
    from .logger import log_debug

    try:
        log_debug("transcript_reader", f"Reading transcript: {transcript_path}")
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

        log_debug(
            "transcript_reader", f"Total assistant messages found: {len(messages)}"
        )

        # Get last N messages
        recent = messages[-n:] if len(messages) >= n else messages
        log_debug("transcript_reader", f"Extracting last {len(recent)} messages")

        # Format: text-only for first N-1, full for last
        result = []
        for i, msg in enumerate(recent):
            if i == len(recent) - 1 and msg["tools"]:
                # Last message: include tool info
                formatted = _format_message_with_tools(msg)
                log_debug(
                    "transcript_reader",
                    f"Message {i} (with tools): {formatted[:100]}...",
                )
                result.append(formatted)
            else:
                # Earlier messages: text only
                log_debug(
                    "transcript_reader",
                    f"Message {i} (text only): {msg['text'][:100]}...",
                )
                result.append(msg["text"])

        log_debug("transcript_reader", f"Returning {len(result)} formatted messages")
        return result

    except Exception as e:
        log_warning("transcript_reader", "Failed to extract messages for validation", e)
        return []


def _extract_text_only(content: Union[list, str]) -> str:
    """
    Extract only text content from message content blocks.

    Skips tool_use and tool_result blocks, returning only text content.

    Args:
        content: Message content (list of blocks or string)

    Returns:
        Extracted text content
    """
    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        return "\n".join(text_parts)
    elif isinstance(content, str):
        return content
    return ""


def build_stop_hook_context(
    transcript_path: str,
    first_n_pairs: int = 10,
    token_budget: int = 48000,
) -> dict:
    """
    Build context for stop hook validation with first-pairs + backwards-walk algorithm.

    This function extracts:
    1. First N user/assistant message pairs from the beginning (to understand session goals)
    2. Recent messages by walking backwards from end (to understand current state)

    A "pair" consists of a user message and all assistant messages that follow it
    before the next user message.

    Args:
        transcript_path: Path to JSONL transcript file
        first_n_pairs: Number of initial user/assistant pairs to include (default: 10)
        token_budget: Maximum tokens for entire context (default: 48000)

    Returns:
        Dictionary with:
        - 'first_pairs': List of (user_msg, assistant_msgs) tuples from beginning
        - 'backwards_messages': List of (role, text) tuples from backwards walk (most recent first)
        - 'truncated_count': Number of messages omitted in the middle
        - 'total_tokens': Estimated token count
    """
    try:
        # Parse transcript and extract all messages (text only)
        all_messages = []

        with open(transcript_path, "r") as f:
            for line in f:
                entry = json.loads(line)
                message = entry.get("message", {})
                role = message.get("role")

                # Only process user and assistant messages
                if role not in ["user", "assistant"]:
                    continue

                content = message.get("content", [])
                text = _extract_text_only(content)

                # Only add messages with text content
                if text.strip():
                    all_messages.append({"role": role, "text": text})

        if not all_messages:
            return {
                "first_pairs": [],
                "backwards_messages": [],
                "truncated_count": 0,
                "total_tokens": 0,
            }

        # Build first N pairs
        first_pairs = []
        pairs_found = 0
        i = 0

        while i < len(all_messages) and pairs_found < first_n_pairs:
            if all_messages[i]["role"] == "user":
                user_msg = all_messages[i]["text"]
                assistant_msgs = []

                # Collect all assistant messages until next user message
                j = i + 1
                while j < len(all_messages) and all_messages[j]["role"] == "assistant":
                    assistant_msgs.append(all_messages[j]["text"])
                    j += 1

                first_pairs.append((user_msg, assistant_msgs))
                pairs_found += 1
                i = j
            else:
                i += 1

        # Calculate tokens used by first pairs (estimate: 4 chars per token)
        first_pairs_text = ""
        for user_msg, assistant_msgs in first_pairs:
            first_pairs_text += user_msg + "\n".join(assistant_msgs)
        first_pairs_tokens = len(first_pairs_text) // 4

        # Find the index where first pairs ended
        first_pairs_end_index = 0
        if first_pairs:
            # Count how many messages were included in first pairs
            for user_msg, assistant_msgs in first_pairs:
                first_pairs_end_index += 1 + len(assistant_msgs)  # user + assistants

        # Walk backwards from end until budget exhausted
        remaining_budget = token_budget - first_pairs_tokens
        backwards_messages = []
        backwards_tokens = 0

        for i in range(len(all_messages) - 1, first_pairs_end_index - 1, -1):
            msg = all_messages[i]
            msg_tokens = len(msg["text"]) // 4

            if backwards_tokens + msg_tokens > remaining_budget:
                break

            backwards_messages.append((msg["role"], msg["text"]))
            backwards_tokens += msg_tokens

        # Calculate truncated count
        truncated_count = (
            len(all_messages) - first_pairs_end_index - len(backwards_messages)
        )
        if truncated_count < 0:
            truncated_count = 0

        total_tokens = first_pairs_tokens + backwards_tokens

        return {
            "first_pairs": first_pairs,
            "backwards_messages": backwards_messages,
            "truncated_count": truncated_count,
            "total_tokens": total_tokens,
        }

    except Exception as e:
        log_warning("transcript_reader", "Failed to build stop hook context", e)
        return {
            "first_pairs": [],
            "backwards_messages": [],
            "truncated_count": 0,
            "total_tokens": 0,
        }


def format_stop_hook_context(context: dict) -> str:
    """
    Format the context dict into a string for the stop hook prompt.

    Args:
        context: Context dictionary from build_stop_hook_context()

    Returns:
        Formatted string with first pairs, truncation marker, and recent messages
    """
    output = []

    # First pairs section
    if context["first_pairs"]:
        output.append(
            "=== BEGINNING OF SESSION (First 10 user requests and responses) ===\n"
        )

        for idx, (user_msg, assistant_msgs) in enumerate(context["first_pairs"], 1):
            output.append(f"[USER {idx}]")
            output.append(user_msg)
            output.append("")

            for asst_idx, assistant_msg in enumerate(assistant_msgs, 1):
                if len(assistant_msgs) > 1:
                    output.append(f"[ASSISTANT {idx}.{asst_idx}]")
                else:
                    output.append(f"[ASSISTANT {idx}]")
                output.append(assistant_msg)
                output.append("")

    # Truncation marker
    if context["truncated_count"] > 0:
        output.append(
            f"=== [TRUNCATED - ~{context['truncated_count']} messages omitted] ===\n"
        )

    # Recent messages section
    if context["backwards_messages"]:
        output.append("=== RECENT CONVERSATION (Most recent messages) ===\n")

        # Reverse to show chronologically (oldest to newest)
        for role, text in reversed(context["backwards_messages"]):
            output.append(f"[{role.upper()}]")
            output.append(text)
            output.append("")

    return "\n".join(output)
