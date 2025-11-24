#!/usr/bin/env python3
"""
Intent-based validation for Stop hook using Claude Agent SDK.

This module validates if Claude completed the user's original request by:
1. Extracting first N user messages from transcript (original mission context)
2. Extracting last N user messages from transcript (recent context)
3. Extracting last assistant message from transcript (what Claude just said)
4. Calling SDK to act as user proxy and judge completion
5. Parsing SDK response (APPROVED or BLOCKED)
"""

import os
import logging
import asyncio
from typing import Any, Dict, List

from .transcript_reader import (
    get_first_n_user_messages,
    get_last_n_user_messages,
    get_last_n_assistant_messages,
)

logger = logging.getLogger(__name__)

# Try to import Claude Agent SDK
try:
    from claude_agent_sdk import query
    from claude_agent_sdk.types import ClaudeAgentOptions, ResultMessage

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False


# SDK validation prompt template
VALIDATION_PROMPT_TEMPLATE = """You are the USER who originally requested this work from Claude Code.

YOUR ORIGINAL REQUEST (first {n} user messages from the beginning of conversation):
{first_messages}

YOUR RECENT CONTEXT (last {n} user messages showing any refinements or clarifications):
{last_messages}

CLAUDE'S RECENT RESPONSES (last {n} assistant messages showing what Claude did):
{last_assistant_messages}

>>> CLAUDE'S VERY LAST RESPONSE (most recent, right before trying to exit): <<<
{last_assistant}

⚠️ CRITICAL: INCOMPLETE CONTEXT LIMITATION ⚠️

You are seeing LIMITED CONTEXT from what may be a LONG conversation:
- FIRST {n} user messages (original mission and early discussion)
- LAST {n} user messages (recent steering and refinements)
- LAST assistant response (Claude's final statement)

⚠️ YOU ARE NOT SEEING THE MIDDLE PORTION where work may have been completed! ⚠️

This means Claude may have:
- Already completed the work in the missing middle conversation
- Fixed issues, implemented features, ran tests
- Committed and pushed changes
- Answered questions and provided solutions

BEFORE CONCLUDING THE MISSION IS INCOMPLETE:

1. Check if Claude's last response references PAST work:
   - "I already fixed that earlier"
   - "That was completed in the previous steps"
   - "As I did before..."
   - "The changes were committed..."

2. Check if Claude is responding to a NEW/DIFFERENT request in recent context that differs from the original

3. If Claude's response suggests work was done in missing middle context, you should APPROVE rather than block

4. Only BLOCK if there's clear evidence Claude:
   - Explicitly refuses to do the work
   - Admits the work is incomplete
   - Shows confusion about what was requested
   - Provides no indication of having done anything

YOUR MISSION:
Judge if Claude delivered what YOU asked for across ALL your messages. Did Claude meet YOUR objectives and complete the work to YOUR standards?

BE HONEST AND DIRECT:
- If Claude completed ALL your requests (initial + any steering) → respond with exactly: APPROVED
- If Claude explicitly references past completion in missing context → APPROVED
- If Claude did NOT complete your requests, avoided work, or left things incomplete → respond with: BLOCKED: [tell Claude specifically what's missing or incomplete, as if you're the user giving feedback]

RESPONSE FORMAT - Choose EXACTLY one:

APPROVED

OR

BLOCKED: [Your direct feedback as the user - be specific about what's incomplete or what Claude failed to deliver]

CRITICAL RULES:
- Consider the LIMITED CONTEXT issue - work may be in missing middle
- Consider ALL user messages (original + recent context), not just the first one
- Look for evidence of past completion in Claude's response
- Output ONLY one of the two formats above
- NO extra text before or after
- Be honest about whether YOUR complete intent was fulfilled
- When in doubt due to missing context, lean toward APPROVED if Claude references past work
"""


def build_validation_prompt(
    first_messages: List[str],
    last_messages: List[str],
    last_assistant_messages: List[str],
    last_assistant: str,
) -> str:
    """
    Build SDK validation prompt from template.

    Args:
        first_messages: First N user messages (original mission context)
        last_messages: Last N user messages (recent context)
        last_assistant_messages: Last N assistant messages (recent responses showing what Claude did)
        last_assistant: Last assistant message (what Claude just said, highlighted separately)

    Returns:
        Complete validation prompt for SDK
    """
    # Format first messages
    if first_messages:
        first_text = "\n\n".join(
            [f"Message {i+1}:\n{msg}" for i, msg in enumerate(first_messages)]
        )
    else:
        first_text = "(No messages available)"

    # Format last messages
    if last_messages:
        last_text = "\n\n".join(
            [f"Message {i+1}:\n{msg}" for i, msg in enumerate(last_messages)]
        )
    else:
        last_text = "(No messages available)"

    # Format last assistant messages
    if last_assistant_messages:
        assistant_messages_text = "\n\n".join(
            [f"Message {i+1}:\n{msg}" for i, msg in enumerate(last_assistant_messages)]
        )
    else:
        assistant_messages_text = "(No messages available)"

    # Format last assistant
    assistant_text = last_assistant if last_assistant else "(No response available)"

    # Determine N for template
    n = max(len(first_messages), len(last_messages), len(last_assistant_messages), 5)

    # Fill template
    return VALIDATION_PROMPT_TEMPLATE.format(
        n=n,
        first_messages=first_text,
        last_messages=last_text,
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
    first_messages: List[str],
    last_messages: List[str],
    last_assistant_messages: List[str],
    last_assistant: str,
) -> str:
    """
    Call Claude Agent SDK for intent validation.

    Args:
        first_messages: First N user messages (original mission)
        last_messages: Last N user messages (recent context)
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
        first_messages, last_messages, last_assistant_messages, last_assistant
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
    first_messages: List[str],
    last_messages: List[str],
    last_assistant_messages: List[str],
    last_assistant: str,
) -> str:
    """
    Synchronous wrapper for SDK validation call.

    Args:
        first_messages: First N user messages
        last_messages: Last N user messages
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
            first_messages, last_messages, last_assistant_messages, last_assistant
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
    1. Extract first N user messages from transcript (original mission)
    2. Extract last N user messages from transcript (recent context)
    3. Extract last N assistant messages from transcript (what Claude did)
    4. Extract very last assistant message (highlighted separately)
    5. Call SDK to validate completion
    6. Parse response and return decision

    Args:
        session_id: Session ID (currently unused but kept for compatibility)
        transcript_path: Path to conversation transcript
        conversation_context_size: Number of messages to extract (default: 5)

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
        first_messages = get_first_n_user_messages(
            transcript_path, n=conversation_context_size
        )
        last_messages = get_last_n_user_messages(
            transcript_path, n=conversation_context_size
        )
        last_assistant_messages = get_last_n_assistant_messages(
            transcript_path, n=conversation_context_size
        )

        # Extract very last assistant message from the list
        last_assistant = last_assistant_messages[-1] if last_assistant_messages else ""

        # Fail open if no context available
        if (
            not first_messages
            and not last_messages
            and not last_assistant_messages
            and not last_assistant
        ):
            return {"continue": True}

        # Call SDK for validation
        sdk_response = call_sdk_validation(
            first_messages=first_messages,
            last_messages=last_messages,
            last_assistant_messages=last_assistant_messages,
            last_assistant=last_assistant,
        )

        # Parse response
        return parse_sdk_response(sdk_response)

    except Exception as e:
        # Any error - fail open (graceful degradation)
        logger.debug(f"Intent validation error (failing open): {e}")
        return {"continue": True}
