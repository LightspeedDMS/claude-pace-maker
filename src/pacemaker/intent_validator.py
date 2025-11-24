#!/usr/bin/env python3
"""
Intent-based validation for Stop hook using Claude Agent SDK.

This module validates if Claude completed the user's original request by:
1. Reading stored user prompt (expanded for slash commands)
2. Extracting last N messages from transcript
3. Calling SDK to act as user proxy and judge completion
4. Parsing SDK response (APPROVED or BLOCKED)
"""

import os
import json
import logging
import asyncio
from typing import Optional, Dict, List, Any

from . import prompt_storage

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

YOUR ORIGINAL REQUESTS (last 5 user prompts, showing the evolution of your request):
{user_prompts}

CLAUDE'S WORK (Last 10 messages from conversation):
{conversation_messages}

CONTEXT:
You may have issued multiple prompts to steer the work:
- The first prompt is your core request
- Subsequent prompts (if any) are refinements, clarifications, or additional requirements
- All prompts together represent your COMPLETE intent

YOUR MISSION:
Judge if Claude delivered what YOU asked for across ALL your prompts. Did Claude meet YOUR objectives and complete the work to YOUR standards?

BE HONEST AND DIRECT:
- If Claude completed ALL your requests (initial + any steering) → respond with exactly: APPROVED
- If Claude did NOT complete your requests, avoided work, or left things incomplete → respond with: BLOCKED: [tell Claude specifically what's missing or incomplete, as if you're the user giving feedback]

RESPONSE FORMAT - Choose EXACTLY one:

APPROVED

OR

BLOCKED: [Your direct feedback as the user - be specific about what's incomplete or what Claude failed to deliver]

CRITICAL RULES:
- Consider ALL user prompts, not just the first one
- Output ONLY one of the two formats above
- NO extra text before or after
- Be honest about whether YOUR complete intent was fulfilled
- If in doubt, examine what YOU asked for (across all prompts) vs what Claude delivered
"""


def extract_last_n_messages(transcript_path: str, n: int = 10) -> List[str]:
    """
    Extract last N messages from JSONL transcript.

    Formats messages as:
    [USER]
    {text}

    [ASSISTANT]
    {text}

    Args:
        transcript_path: Path to JSONL transcript file
        n: Number of messages to extract (default: 10)

    Returns:
        List of formatted message strings (most recent last)
    """
    try:
        all_messages = []

        with open(transcript_path, "r") as f:
            for line in f:
                entry = json.loads(line)

                # Extract message content
                message = entry.get("message", {})
                role = message.get("role")
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
                    # Format with role prefix
                    formatted_message = f"[{role.upper()}]\n" + "\n".join(text_parts)
                    all_messages.append(formatted_message)

        # Return last N messages
        if len(all_messages) >= n:
            return all_messages[-n:]
        else:
            return all_messages

    except Exception as e:
        logger.debug(f"Failed to extract messages from transcript: {e}")
        return []


def read_stored_prompt(session_id: str, prompts_dir: str) -> Optional[Dict[str, Any]]:
    """
    Read stored prompt file for session.

    Handles backwards compatibility: converts old format to new format.

    Args:
        session_id: Session ID
        prompts_dir: Directory containing prompt files

    Returns:
        Prompt data dict (new format with 'prompts' array) or None if not found
    """
    try:
        prompt_file = os.path.join(prompts_dir, f"{session_id}.json")
        if not os.path.exists(prompt_file):
            return None

        with open(prompt_file, "r") as f:
            data = json.load(f)

            # Backwards compatibility: convert old format to new format
            data = prompt_storage.convert_old_format_to_new(data)

            return data
    except Exception as e:
        logger.debug(f"Failed to read stored prompt: {e}")
        return None


def build_validation_prompt(
    user_prompts: List[Dict[str, Any]], conversation_messages: List[str]
) -> str:
    """
    Build SDK validation prompt from template.

    Args:
        user_prompts: List of user prompt dicts (with raw_prompt, expanded_prompt, timestamp, sequence)
        conversation_messages: List of formatted conversation messages

    Returns:
        Complete validation prompt for SDK
    """
    # Format prompts chronologically
    prompts_text = []
    for i, prompt in enumerate(user_prompts, 1):
        expanded = prompt.get("expanded_prompt", "")
        timestamp = prompt.get("timestamp", "")
        prompts_text.append(f"Prompt {i} ({timestamp}):\n{expanded}")

    prompts_formatted = "\n\n".join(prompts_text)
    messages_text = "\n\n---\n\n".join(conversation_messages)

    # Fill template
    return VALIDATION_PROMPT_TEMPLATE.format(
        user_prompts=prompts_formatted, conversation_messages=messages_text
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
    user_prompts: List[Dict[str, Any]], conversation_messages: List[str]
) -> str:
    """
    Call Claude Agent SDK for intent validation.

    Args:
        user_prompts: List of user prompt dicts
        conversation_messages: Last N conversation messages

    Returns:
        SDK response text

    Raises:
        Exception if SDK call fails
    """
    if not SDK_AVAILABLE:
        raise ImportError("Claude Agent SDK not available")

    # Build validation prompt
    prompt = build_validation_prompt(user_prompts, conversation_messages)

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
    user_prompts: List[Dict[str, Any]], conversation_messages: List[str]
) -> str:
    """
    Synchronous wrapper for SDK validation call.

    Args:
        user_prompts: List of user prompt dicts
        conversation_messages: Last N conversation messages

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
        call_sdk_validation_async(user_prompts, conversation_messages)
    )


def validate_intent(
    session_id: str, transcript_path: str, prompts_dir: str
) -> Dict[str, Any]:
    """
    Validate if Claude completed user's original intent.

    Main function for Stop hook intent validation:
    1. Read stored prompts (last 5, expanded for slash commands)
    2. Extract last 10 messages from transcript
    3. Call SDK to validate completion
    4. Parse response and return decision

    Args:
        session_id: Session ID
        transcript_path: Path to conversation transcript
        prompts_dir: Directory containing stored prompts

    Returns:
        Decision dict:
        - {"continue": True} - Allow exit
        - {"decision": "block", "reason": "feedback"} - Block with feedback
    """
    try:
        # Read stored prompts (last 5)
        prompt_data = read_stored_prompt(session_id, prompts_dir)
        if prompt_data is None:
            # No stored prompt - fail open
            return {"continue": True}

        user_prompts = prompt_data.get("prompts", [])
        if not user_prompts:
            # Empty prompts list - fail open
            return {"continue": True}

        # Extract last N messages from transcript
        conversation_messages = extract_last_n_messages(transcript_path, n=10)
        if not conversation_messages:
            # No messages - fail open
            return {"continue": True}

        # Call SDK for validation with ALL prompts
        sdk_response = call_sdk_validation(user_prompts, conversation_messages)

        # Parse response
        return parse_sdk_response(sdk_response)

    except Exception as e:
        # Any error - fail open (graceful degradation)
        logger.debug(f"Intent validation error (failing open): {e}")
        return {"continue": True}
