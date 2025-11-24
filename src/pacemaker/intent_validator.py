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
import asyncio
from typing import Optional, Dict, List, Any

# Try to import Claude Agent SDK
try:
    from claude_agent_sdk import query
    from claude_agent_sdk.types import ClaudeAgentOptions, ResultMessage

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False


# SDK validation prompt template
VALIDATION_PROMPT_TEMPLATE = """You are the USER who originally requested this work from Claude Code.

YOUR ORIGINAL REQUEST:
{user_prompt}

CLAUDE'S WORK (Last 10 messages from conversation):
{conversation_messages}

YOUR MISSION:
Judge if Claude delivered what YOU asked for. Did Claude meet YOUR objectives and complete the work to YOUR standards?

BE HONEST AND DIRECT:
- If Claude completed your request → respond with exactly: APPROVED
- If Claude did NOT complete your request, avoided work, or left things incomplete → respond with: BLOCKED: [tell Claude specifically what's missing or incomplete, as if you're the user giving feedback]

RESPONSE FORMAT - Choose EXACTLY one:

APPROVED

OR

BLOCKED: [Your direct feedback as the user - be specific about what's incomplete or what Claude failed to deliver]

CRITICAL RULES:
- Output ONLY one of the two formats above
- NO extra text before or after
- Be honest about whether YOUR original request was actually fulfilled
- If in doubt, examine what YOU asked for vs what Claude delivered
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

    except Exception:
        return []


def read_stored_prompt(session_id: str, prompts_dir: str) -> Optional[Dict[str, Any]]:
    """
    Read stored prompt file for session.

    Args:
        session_id: Session ID
        prompts_dir: Directory containing prompt files

    Returns:
        Prompt data dict or None if not found
    """
    try:
        prompt_file = os.path.join(prompts_dir, f"{session_id}.json")
        if not os.path.exists(prompt_file):
            return None

        with open(prompt_file, "r") as f:
            return json.load(f)
    except Exception:
        return None


def build_validation_prompt(user_prompt: str, conversation_messages: List[str]) -> str:
    """
    Build SDK validation prompt from template.

    Args:
        user_prompt: User's original request (expanded if slash command)
        conversation_messages: List of formatted conversation messages

    Returns:
        Complete validation prompt for SDK
    """
    # Join messages with separator
    messages_text = "\n\n---\n\n".join(conversation_messages)

    # Fill template
    return VALIDATION_PROMPT_TEMPLATE.format(
        user_prompt=user_prompt, conversation_messages=messages_text
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
    user_prompt: str, conversation_messages: List[str]
) -> str:
    """
    Call Claude Agent SDK for intent validation.

    Args:
        user_prompt: User's original request
        conversation_messages: Last N conversation messages

    Returns:
        SDK response text

    Raises:
        Exception if SDK call fails
    """
    if not SDK_AVAILABLE:
        raise ImportError("Claude Agent SDK not available")

    # Build validation prompt
    prompt = build_validation_prompt(user_prompt, conversation_messages)

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


def call_sdk_validation(user_prompt: str, conversation_messages: List[str]) -> str:
    """
    Synchronous wrapper for SDK validation call.

    Args:
        user_prompt: User's original request
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
        call_sdk_validation_async(user_prompt, conversation_messages)
    )


def validate_intent(
    session_id: str, transcript_path: str, prompts_dir: str
) -> Dict[str, Any]:
    """
    Validate if Claude completed user's original intent.

    Main function for Stop hook intent validation:
    1. Read stored prompt (expanded for slash commands)
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
        # Read stored prompt
        prompt_data = read_stored_prompt(session_id, prompts_dir)
        if prompt_data is None:
            # No stored prompt - fail open
            return {"continue": True}

        user_prompt = prompt_data.get("expanded_prompt", "")
        if not user_prompt:
            # Empty prompt - fail open
            return {"continue": True}

        # Extract last N messages from transcript
        conversation_messages = extract_last_n_messages(transcript_path, n=10)
        if not conversation_messages:
            # No messages - fail open
            return {"continue": True}

        # Call SDK for validation
        sdk_response = call_sdk_validation(user_prompt, conversation_messages)

        # Parse response
        return parse_sdk_response(sdk_response)

    except Exception:
        # Any error - fail open (graceful degradation)
        return {"continue": True}
