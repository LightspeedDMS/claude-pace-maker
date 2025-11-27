#!/usr/bin/env python3
"""
Intent-based validation for Stop hook using Claude Agent SDK.

This module validates if Claude completed the user's original request by:
1. Extracting ALL user messages from transcript (complete user intent)
2. Extracting last N assistant messages from transcript (what Claude has been doing)
3. Extracting last assistant message from transcript (what Claude just said)
4. Calling SDK to act as user proxy and judge completion
5. Parsing SDK response (APPROVED or BLOCKED)
"""

import os
import logging
import asyncio
from typing import Any, Dict, List

from .transcript_reader import (
    get_all_user_messages,
    get_last_n_assistant_messages,
)
from .constants import DEFAULT_CONFIG

logger = logging.getLogger(__name__)


def get_config(key: str) -> Any:
    """
    Get configuration value for the given key.

    Args:
        key: Configuration key to retrieve

    Returns:
        Configuration value from DEFAULT_CONFIG
    """
    return DEFAULT_CONFIG.get(key)


def truncate_user_message(message: str, max_length: int) -> str:
    """
    Truncate user message if it exceeds max_length.

    Args:
        message: User message to potentially truncate
        max_length: Maximum allowed length for message

    Returns:
        Truncated message with suffix if over limit, otherwise original message
    """
    if not message or len(message) <= max_length:
        return message

    # Truncate and append suffix
    truncated = message[:max_length]
    return f"{truncated}[TRUNCATED>{max_length} CHARS]"


# Try to import Claude Agent SDK
try:
    from claude_agent_sdk import query
    from claude_agent_sdk.types import ClaudeAgentOptions, ResultMessage

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False


def load_prompt_template(file_path: str) -> str:
    """
    Load validation prompt template from external file.

    Args:
        file_path: Path to the prompt template markdown file

    Returns:
        Template content as string

    Raises:
        FileNotFoundError: If the template file doesn't exist
    """
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def get_prompt_template() -> str:
    """
    Get validation prompt template from external file.

    Loads from: src/pacemaker/prompts/stop_hook_validator_prompt.md

    Returns:
        Validation prompt template string

    Raises:
        FileNotFoundError: If template file is missing (installation broken)
        Exception: If template cannot be loaded
    """
    module_dir = os.path.dirname(__file__)
    prompt_path = os.path.join(module_dir, "prompts", "stop_hook_validator_prompt.md")

    if not os.path.exists(prompt_path):
        raise FileNotFoundError(
            f"Stop hook validator prompt template not found at {prompt_path}. "
            "This indicates a broken installation. Run ./install.sh to fix."
        )

    return load_prompt_template(prompt_path)


def build_validation_prompt(
    all_user_messages: List[str],
    last_assistant_messages: List[str],
    last_assistant: str,
) -> str:
    """
    Build SDK validation prompt from template.

    Args:
        all_user_messages: ALL user messages (complete user intent from start to finish)
        last_assistant_messages: Last N assistant messages (recent responses showing what Claude did)
        last_assistant: Last assistant message (what Claude just said, highlighted separately)

    Returns:
        Complete validation prompt for SDK
    """
    # Get max length from config
    max_length = get_config("user_message_max_length")

    # Format all user messages with truncation
    if all_user_messages:
        truncated_messages = [
            truncate_user_message(msg, max_length) for msg in all_user_messages
        ]
        all_user_text = "\n\n".join(
            [f"Message {i+1}:\n{msg}" for i, msg in enumerate(truncated_messages)]
        )
    else:
        all_user_text = "(No messages available)"

    # Format last assistant messages
    if last_assistant_messages:
        assistant_messages_text = "\n\n".join(
            [f"Message {i+1}:\n{msg}" for i, msg in enumerate(last_assistant_messages)]
        )
    else:
        assistant_messages_text = "(No messages available)"

    # Format last assistant
    assistant_text = last_assistant if last_assistant else "(No response available)"

    # Determine N for template (number of assistant messages shown)
    n = len(last_assistant_messages) if last_assistant_messages else 5

    # Get template from external file
    template = get_prompt_template()

    # Fill template
    return template.format(
        n=n,
        all_user_messages=all_user_text,
        last_assistant_messages=assistant_messages_text,
        last_assistant=assistant_text,
    )


def parse_sdk_response(response_text: str) -> Dict[str, Any]:
    """
    Parse SDK response into Claude Code Stop hook format.

    Expected formats:
    - "APPROVED" → {"continue": true}
    - "BLOCKED: feedback" → {"decision": "block", "reason": "feedback"}
    - Unexpected → {"continue": true} (fail open)

    Args:
        response_text: Raw SDK response text

    Returns:
        Decision dict for Claude Code Stop hook
    """
    trimmed = response_text.strip()

    if trimmed == "APPROVED":
        return {"continue": True}

    elif trimmed.startswith("BLOCKED:"):
        feedback = trimmed.replace("BLOCKED:", "").strip()
        return {"decision": "block", "reason": feedback}

    else:
        # Unexpected format - fail open
        return {"continue": True}


async def call_sdk_validation_async(
    all_user_messages: List[str],
    last_assistant_messages: List[str],
    last_assistant: str,
) -> str:
    """
    Call Claude Agent SDK for intent validation.

    Args:
        all_user_messages: ALL user messages (complete user intent)
        last_assistant_messages: Last N assistant messages (recent responses)
        last_assistant: Last assistant message (very last, highlighted separately)

    Returns:
        SDK response text

    Raises:
        Exception if SDK call fails
    """
    if not SDK_AVAILABLE:
        raise ImportError("Claude Agent SDK not available")

    # Build validation prompt
    prompt = build_validation_prompt(
        all_user_messages, last_assistant_messages, last_assistant
    )

    # Configure SDK options
    options = ClaudeAgentOptions(
        max_turns=1,
        model="claude-sonnet-4-5",
        system_prompt="You are acting as the user who originally made this request. Judge if Claude delivered what you asked for.",
        disallowed_tools=[
            "Write",
            "Edit",
            "Bash",
            "TodoWrite",
            "Read",
            "Grep",
            "Glob",
        ],
        max_thinking_tokens=2000,
    )

    # Call SDK
    response_text = ""
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            if hasattr(message, "result") and message.result:
                response_text = message.result.strip()
                break

    return response_text


def call_sdk_validation(
    all_user_messages: List[str],
    last_assistant_messages: List[str],
    last_assistant: str,
) -> str:
    """
    Synchronous wrapper for SDK validation call.

    Args:
        all_user_messages: ALL user messages (complete user intent)
        last_assistant_messages: Last N assistant messages
        last_assistant: Last assistant message

    Returns:
        SDK response text

    Raises:
        Exception if SDK call fails
    """
    loop = asyncio.get_event_loop()
    if loop.is_running():
        # Create new loop if already in async context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(
        call_sdk_validation_async(
            all_user_messages,
            last_assistant_messages,
            last_assistant,
        )
    )


def validate_intent(
    session_id: str,
    transcript_path: str,
    conversation_context_size: int = 5,
) -> Dict[str, Any]:
    """
    Validate if Claude completed user's original intent.

    Main function for Stop hook intent validation:
    1. Extract ALL user messages from transcript (complete user intent)
    2. Extract last N assistant messages from transcript (what Claude did)
    3. Extract very last assistant message (highlighted separately)
    4. Call SDK to validate completion
    5. Parse response and return decision

    Args:
        session_id: Session ID (currently unused but kept for compatibility)
        transcript_path: Path to conversation transcript
        conversation_context_size: Number of assistant messages to extract (default: 5)
                                   Note: ALL user messages are always extracted

    Returns:
        Decision dict:
        - {"continue": True} - Allow exit
        - {"decision": "block", "reason": "feedback"} - Block with feedback
    """
    try:
        # Verify transcript exists
        if not os.path.exists(transcript_path):
            return {"continue": True}

        # Extract context from transcript
        all_user_messages = get_all_user_messages(transcript_path)
        last_assistant_messages = get_last_n_assistant_messages(
            transcript_path, n=conversation_context_size
        )

        # Extract very last assistant message from the list
        last_assistant = last_assistant_messages[-1] if last_assistant_messages else ""

        # Fail open if no context available
        if not all_user_messages and not last_assistant_messages and not last_assistant:
            return {"continue": True}

        # Call SDK for validation
        sdk_response = call_sdk_validation(
            all_user_messages=all_user_messages,
            last_assistant_messages=last_assistant_messages,
            last_assistant=last_assistant,
        )

        # Parse response
        return parse_sdk_response(sdk_response)

    except Exception as e:
        # Any error - fail open (graceful degradation)
        logger.debug(f"Intent validation error (failing open): {e}")
        return {"continue": True}
