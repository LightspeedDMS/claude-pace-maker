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
import sys
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
    import claude_agent_sdk  # type: ignore[import-not-found]  # noqa: F401

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


def get_pre_tool_prompt_template() -> str:
    """
    Get pre-tool validation prompt template from external file.

    Loads from: src/pacemaker/prompts/pre_tool_validator_prompt.md

    Returns:
        Pre-tool validation prompt template string

    Raises:
        FileNotFoundError: If template file is missing (installation broken)
        Exception: If template cannot be loaded
    """
    module_dir = os.path.dirname(__file__)
    prompt_path = os.path.join(module_dir, "prompts", "pre_tool_validator_prompt.md")

    if not os.path.exists(prompt_path):
        raise FileNotFoundError(
            f"Pre-tool validator prompt template not found at {prompt_path}. "
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


def _is_limit_error(response: str) -> bool:
    """Check if response indicates usage limit error."""
    if not response:
        return False
    lower = response.lower()
    return "usage limit" in lower or "limit reached" in lower or "resets" in lower


async def _fresh_sdk_call(prompt: str, model: str) -> str:
    """Call SDK with fresh imports and objects for each call."""
    # Fresh import to avoid any cached state
    from claude_agent_sdk import query as fresh_query
    from claude_agent_sdk.types import (  # type: ignore[import-not-found]
        ClaudeAgentOptions as FreshOptions,
        ResultMessage as FreshResult,
    )

    # Create fresh options object
    options = FreshOptions(
        max_turns=1,
        model=model,
        max_thinking_tokens=4000,
        system_prompt="You are acting as the user who originally made this request. Judge if Claude delivered what you asked for.",
        disallowed_tools=["Write", "Edit", "Bash", "TodoWrite", "Read", "Grep", "Glob"],
    )

    response_text = ""
    # SDK may throw exception after returning result (e.g., on usage limit)
    # Capture the response before any exception
    try:
        async for message in fresh_query(prompt=prompt, options=options):
            if isinstance(message, FreshResult):
                if hasattr(message, "result") and message.result:
                    response_text = message.result.strip()
    except Exception:
        # Exception after getting response is OK - we have what we need
        pass
    return response_text


async def call_sdk_validation_async(
    all_user_messages: List[str],
    last_assistant_messages: List[str],
    last_assistant: str,
) -> str:
    """
    Call Claude Agent SDK for intent validation.
    Tries sonnet first, falls back to opus if sonnet hits usage limit.
    """
    if not SDK_AVAILABLE:
        raise ImportError("Claude Agent SDK not available")

    prompt = build_validation_prompt(
        all_user_messages, last_assistant_messages, last_assistant
    )

    # Try sonnet first
    response = await _fresh_sdk_call(prompt, "claude-sonnet-4-5-20250929")

    # If sonnet hit usage limit, fall back to opus
    if _is_limit_error(response):
        response = await _fresh_sdk_call(prompt, "claude-opus-4-5-20251101")

    return response


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

        # Log raw SDK response for debugging
        from .constants import DEFAULT_CONFIG_PATH

        debug_log = os.path.join(
            os.path.dirname(DEFAULT_CONFIG_PATH), "stop_hook_debug.log"
        )
        with open(debug_log, "a") as f:
            f.write(
                f"SDK raw response: {sdk_response[:500] if sdk_response else 'EMPTY'}\n"
            )

        # Parse response
        return parse_sdk_response(sdk_response)

    except Exception as e:
        # Any error - fail open (graceful degradation)
        from .constants import DEFAULT_CONFIG_PATH

        debug_log = os.path.join(
            os.path.dirname(DEFAULT_CONFIG_PATH), "stop_hook_debug.log"
        )
        with open(debug_log, "a") as f:
            f.write(f"SDK ERROR (failing open): {e}\n")
        return {"continue": True}


def _build_intent_declaration_prompt(
    messages: List[str], file_path: str, tool_name: str
) -> str:
    """
    Build SDK prompt for checking if intent was declared.

    Args:
        messages: Last N assistant messages
        file_path: Target file path
        tool_name: Tool being used (Write/Edit)

    Returns:
        Prompt string for SDK
    """
    filename = os.path.basename(file_path)
    action = "create or modify" if tool_name == "Write" else "edit"

    messages_text = "\n\n".join(
        [f"Message {i+1}:\n{msg}" for i, msg in enumerate(messages)]
    )

    return f"""You are checking if Claude declared intent before attempting to {action} a file.

File to be modified: {filename}
Tool being used: {tool_name}

Recent assistant messages:
{messages_text}

Question: Did Claude clearly declare intent to {action} {filename} in these messages?

Intent declaration should include:
1. What file is being modified
2. What changes are being made
3. Why/goal of the changes

Respond with ONLY:
- "YES" if intent was clearly declared
- "NO" if intent was not declared or unclear"""


async def _call_sdk_intent_validation_async(prompt: str) -> str:
    """
    Call SDK with Haiku for fast intent validation.

    Args:
        prompt: Validation prompt

    Returns:
        SDK response text (YES or NO)
    """
    if not SDK_AVAILABLE:
        raise ImportError("Claude Agent SDK not available")

    # Fresh import to avoid cached state
    from claude_agent_sdk import query as fresh_query
    from claude_agent_sdk.types import (  # type: ignore[import-not-found]
        ClaudeAgentOptions as FreshOptions,
        ResultMessage as FreshResult,
    )

    # Use Sonnet for better natural language understanding (Haiku failed tests)
    options = FreshOptions(
        max_turns=1,
        model="claude-sonnet-4-5-20250929",
        max_thinking_tokens=2000,
        system_prompt="You are validating if intent was declared. Respond with YES or NO only.",
        disallowed_tools=["Write", "Edit", "Bash", "TodoWrite", "Read", "Grep", "Glob"],
    )

    response_text = ""
    try:
        async for message in fresh_query(prompt=prompt, options=options):
            if isinstance(message, FreshResult):
                if hasattr(message, "result") and message.result:
                    response_text = message.result.strip()
    except Exception:
        # Exception after getting response is OK
        pass

    return response_text


def _call_sdk_intent_validation(prompt: str) -> str:
    """
    Synchronous wrapper for SDK intent validation.

    Args:
        prompt: Validation prompt

    Returns:
        SDK response text (YES or NO)
    """
    loop = asyncio.get_event_loop()
    if loop.is_running():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(_call_sdk_intent_validation_async(prompt))


def validate_intent_declared(
    messages: List[str], file_path: str, tool_name: str
) -> Dict[str, Any]:
    """
    Validate if intent to modify file was declared in messages.

    Args:
        messages: Last N assistant messages from transcript
        file_path: Target file path
        tool_name: Tool being used (Write/Edit)

    Returns:
        {
            "intent_found": True/False
        }
    """
    try:
        # Build prompt
        prompt = _build_intent_declaration_prompt(messages, file_path, tool_name)

        # Call SDK
        response = _call_sdk_intent_validation(prompt)

        # Parse YES/NO response (case-insensitive)
        response_upper = response.strip().upper()

        if response_upper == "YES":
            return {"intent_found": True}
        else:
            # NO or any other response = no intent found
            return {"intent_found": False}

    except Exception:
        # Fail open: return False on any error
        return {"intent_found": False}


def validate_intent_and_code(
    messages: List[str], code: str, file_path: str, tool_name: str
) -> dict:
    """
    Unified pre-tool validation: intent declaration + code review.

    Args:
        messages: Last 2 assistant messages (current + 1 before)
        code: Proposed code that will be written
        file_path: Target file path
        tool_name: Write or Edit

    Returns:
        {"approved": True} if all checks pass
        {"approved": False, "feedback": "..."} if violations found
    """
    if not SDK_AVAILABLE:
        # Fail open if SDK unavailable
        return {"approved": True}

    # Format messages for template
    messages_text = "\n".join(f"Message {i+1}: {msg}" for i, msg in enumerate(messages))

    # Load template and fill with parameters
    template = get_pre_tool_prompt_template()
    prompt = template.format(
        tool_name=tool_name,
        file_path=file_path,
        messages=messages_text,
        code=code,
    )

    # Call SDK with Sonnet
    try:
        feedback = asyncio.run(_call_unified_validation_async(prompt))

        if feedback.strip() == "":
            # Empty response = approved
            return {"approved": True}
        else:
            # Feedback = blocked
            return {"approved": False, "feedback": feedback}

    except Exception as e:
        # Fail open on errors - log to stderr so we can see it
        print(
            f"[SDK ERROR] Unified validation failed: {e}", file=sys.stderr, flush=True
        )
        import traceback

        traceback.print_exc(file=sys.stderr)
        logger.debug(f"Unified validation error: {e}")
        return {"approved": True}


async def _call_unified_validation_async(prompt: str) -> str:
    """Call SDK for unified validation with Sonnet."""
    if not SDK_AVAILABLE:
        return ""

    from claude_agent_sdk import query as fresh_query
    from claude_agent_sdk.types import (
        ClaudeAgentOptions as FreshOptions,
        ResultMessage as FreshResult,
    )

    options = FreshOptions(
        max_turns=1,
        model="claude-sonnet-4-5-20250929",
        max_thinking_tokens=4000,
        system_prompt="You are a strict code validator. Return empty response ONLY if all checks pass. Otherwise return detailed feedback.",
        disallowed_tools=["Write", "Edit", "Bash", "TodoWrite", "Read", "Grep", "Glob"],
    )

    response_text = ""
    try:
        async for message in fresh_query(prompt=prompt, options=options):
            if isinstance(message, FreshResult):
                if hasattr(message, "result") and message.result:
                    response_text = message.result.strip()
    except Exception as e:
        # Fail open - validation errors don't block execution
        print(
            f"[SDK ERROR] Unified validation failed: {e}", file=sys.stderr, flush=True
        )
        import traceback

        traceback.print_exc(file=sys.stderr)

    return response_text
