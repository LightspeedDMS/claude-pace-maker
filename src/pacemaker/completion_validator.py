#!/usr/bin/env python3
"""
Implementation completion validation using Claude Agent SDK.

This module validates IMPLEMENTATION_COMPLETE claims by analyzing conversation
context with an AI judge to determine if work is truly finished.
"""

import asyncio
from typing import Dict, List, Union

try:
    from claude_agent_sdk import query
    from claude_agent_sdk.types import ClaudeAgentOptions, ResultMessage

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False


IMPLEMENTATION_VALIDATION_PROMPT = """You are a STRICT code completion validator with ZERO TOLERANCE for incomplete work.

CONVERSATION CONTEXT:
{conversation_messages}

CRITICAL ASSESSMENT:
The developer (Claude) claimed IMPLEMENTATION_COMPLETE in their last message.
Your job is to determine if this claim is TRUTHFUL or if Claude is LYING about completion.

YOU MUST CHALLENGE if you find ANY of these:
- ANY TODO, FIXME, or "Not implemented" statements
- ANY mention of "pending", "incomplete", "still need to", "missing"
- ANY unimplemented functions or methods (even if marked as "pass")
- ANY test failures or errors mentioned
- ANY admission that features are partial or incomplete
- ANY statements like "basic structure" without full implementation
- ANY scope reduction from original requirements
- ANY explicit statement that work is NOT done

ASSUME CLAUDE IS TRYING TO CHEAT - be aggressive in finding incomplete work.

REQUIRED RESPONSE FORMAT:
You must respond with EXACTLY ONE of these two options:

Option 1 - ONLY if work is 100% complete with ZERO TODOs, ZERO unimplemented features:
COMPLETION_CONFIRMED

Option 2 - If there is ANY doubt, ANY TODO, ANY incomplete feature:
CHALLENGE: [Quote the specific TODOs/FIXMEs/incomplete statements from Claude's message, then demand CONFIRMED_IMPLEMENTATION_COMPLETE only if they can prove 100% completion, otherwise continue working. If you are genuinely blocked by missing information or ambiguous requirements, respond with COMPLETELY_BLOCKED and explain the specific blocker requiring user intervention.]

CRITICAL RULES:
- NO other text before or after your response
- When in doubt → ALWAYS CHALLENGE
- Be ruthless: even ONE TODO = incomplete
- Quote Claude's own words as evidence of incomplete work
- If Claude admits anything is not done → CHALLENGE immediately
"""


BLOCKAGE_LEGITIMACY_PROMPT = """You are a FAIR arbitrator evaluating if Claude is genuinely blocked from completing work.

CONVERSATION CONTEXT:
{conversation_messages}

SITUATION:
Claude was challenged for incomplete work or work avoidance. Claude responded with COMPLETELY_BLOCKED and provided an explanation for why they cannot proceed.

Your job: Determine if the blockage is LEGITIMATE or if Claude is making excuses to avoid work.

LEGITIMATE BLOCKAGE REASONS (allow exit):
- Missing critical information from user that Claude cannot infer
- Ambiguous requirements where multiple valid interpretations exist
- User explicitly asked Claude to stop/wait before proceeding
- Technical environment issues (missing dependencies Claude cannot install)
- Contradictory requirements that need user clarification
- Ethical concerns or unsafe operations user is requesting

ILLEGITIMATE EXCUSES (reject):
- "Token budget concerns" or "running out of context"
- "This is taking too long" or "too complex"
- "User should do this part themselves"
- "I don't know how" when Claude should research/figure it out
- "Might break something" without attempting or asking
- Generic statements without specific blocking details
- Claiming missing information that was already provided

CRITICAL ASSESSMENT:
Does Claude's explanation identify a REAL blocker requiring user intervention, or is Claude making excuses to avoid doing work?

RESPONSE FORMAT - Choose EXACTLY one:

BLOCKAGE_LEGITIMATE

OR

BLOCKAGE_REJECTED: [Explain why the excuse is invalid, quote what Claude should do instead, demand they continue working]

CRITICAL:
- Output ONLY one of the two formats above
- NO extra text before or after
- When in doubt → BLOCKAGE_REJECTED
- Quote Claude's excuse if rejecting
"""


EXCHANGE_VALIDATION_PROMPT = """You are a RUTHLESS work-avoidance detector with ZERO TOLERANCE for laziness.

CONVERSATION CONTEXT:
{conversation_messages}

CRITICAL ASSESSMENT:
Claude claimed EXCHANGE_COMPLETE. Determine if LEGITIMATE (pure conversation/research) or CHEATING (avoiding implementation work user requested).

CHALLENGE if you find ANY of these:
- User asked to "implement", "create", "build", "write code", "fix bug", "add feature"
- User requested code file changes or new functionality
- Claude provided ONLY analysis, suggestions, or plans WITHOUT implementing
- Claude wrote docs but NOT actual code when code was requested
- Claude said "you should do X" instead of doing X
- Code changes discussed but not committed
- Files mentioned but not created/modified
- User objectives remain unmet

ALLOW only if:
- Pure research/investigation with NO implementation request
- Answering "how does X work?" questions
- Documentation when that was the request
- Planning where user explicitly didn't want implementation yet

ASSUME CLAUDE IS CHEATING - be aggressive.

RESPONSE FORMAT - Choose EXACTLY one:

EXCHANGE_LEGITIMATE

OR

WORK_REQUIRED: [Quote user's implementation request, list what Claude failed to do, demand IMPLEMENTATION_COMPLETE after doing the work. If you are genuinely blocked by missing information, user asking you to stop, or ambiguous requirements, respond with COMPLETELY_BLOCKED and explain the specific blocker requiring user intervention.]

CRITICAL:
- Output ONLY one of the two formats above
- NO extra text before or after
- When in doubt → WORK_REQUIRED
- Quote evidence from conversation
"""


async def validate_implementation_complete_async(
    messages: List[str],
) -> Dict[str, Union[bool, str, None]]:
    """
    Validate if IMPLEMENTATION_COMPLETE claim is legitimate using Claude Agent SDK.

    Args:
        messages: List of last 5 conversation messages (raw text)

    Returns:
        {
            'confirmed': True/False,
            'challenge_message': None or str with challenge text
        }
    """
    if not SDK_AVAILABLE:
        # SDK not available - allow completion (graceful degradation)
        return {"confirmed": True, "challenge_message": None}

    # Build conversation context
    conversation_text = "\n\n---\n\n".join(messages)
    prompt = IMPLEMENTATION_VALIDATION_PROMPT.format(
        conversation_messages=conversation_text
    )

    # Log the validation attempt
    import os

    log_file = os.path.join(
        os.path.expanduser("~/.claude-pace-maker"), "validation_debug.log"
    )
    try:
        with open(log_file, "a") as f:
            import datetime

            f.write(f"\n{'='*80}\n")
            f.write(f"[{datetime.datetime.now()}] SDK VALIDATION CALLED\n")
            f.write(f"Number of messages: {len(messages)}\n")
            f.write(
                f"Last message preview: {messages[-1][:300] if messages else 'NO MESSAGES'}...\n"
            )
            f.write(f"Prompt preview: {prompt[:500]}...\n")
    except Exception:
        pass

    # Configure SDK options
    options = ClaudeAgentOptions(
        max_turns=1,
        model="claude-sonnet-4-5",
        system_prompt="You are a strict code completion validator. Be precise and deterministic.",
        disallowed_tools=["Write", "Edit", "Bash", "TodoWrite", "Read", "Grep", "Glob"],
        max_thinking_tokens=2000,
    )

    # Call SDK
    response_text = ""
    try:
        async for message in query(prompt=prompt, options=options):
            # Messages are typed dataclass objects, not dictionaries
            if isinstance(message, ResultMessage):
                # ResultMessage has result attribute
                if hasattr(message, "result") and message.result:
                    response_text = message.result.strip()
                    break
    except Exception as e:
        # Fallback on SDK error - treat as incomplete with explanation
        return {
            "confirmed": False,
            "challenge_message": (
                f"Implementation completion validation failed (SDK error: {e}). "
                "Respond with CONFIRMED_IMPLEMENTATION_COMPLETE only if you completed "
                "100% of the work, otherwise continue."
            ),
        }

    # Parse deterministic response
    if response_text == "COMPLETION_CONFIRMED":
        return {"confirmed": True, "challenge_message": None}
    elif response_text.startswith("CHALLENGE:"):
        challenge = response_text.replace("CHALLENGE:", "").strip()
        return {"confirmed": False, "challenge_message": challenge}
    else:
        # Unexpected response - treat as incomplete
        return {
            "confirmed": False,
            "challenge_message": (
                "Implementation completion could not be verified. "
                "Respond with CONFIRMED_IMPLEMENTATION_COMPLETE only if you completed "
                "100% of the work, otherwise continue."
            ),
        }


def validate_implementation_complete(
    messages: List[str],
) -> Dict[str, Union[bool, str, None]]:
    """
    Synchronous wrapper for async validation function.

    Args:
        messages: List of last 5 conversation messages (raw text)

    Returns:
        {
            'confirmed': True/False,
            'challenge_message': None or str with challenge text
        }
    """
    try:
        # Run async function in event loop
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already in async context - create new loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(validate_implementation_complete_async(messages))
    except Exception as e:
        # Fallback on any error - allow completion with warning
        import sys

        print(
            f"[PACE-MAKER WARNING] Completion validation error: {e}",
            file=sys.stderr,
        )
        return {"confirmed": True, "challenge_message": None}


async def validate_exchange_complete_async(
    messages: List[str],
) -> Dict[str, Union[bool, str, None]]:
    """
    Validate if EXCHANGE_COMPLETE claim is legitimate (not avoiding work).

    Detects cases where Claude uses EXCHANGE_COMPLETE to escape from doing
    implementation work that the user requested.

    Args:
        messages: List of last 5 conversation messages (raw text)

    Returns:
        {
            'legitimate': True/False,
            'challenge_message': None or str with challenge text
        }
    """
    if not SDK_AVAILABLE:
        # SDK not available - allow completion (graceful degradation)
        return {"legitimate": True, "challenge_message": None}

    # Build conversation context
    conversation_text = "\n\n---\n\n".join(messages)
    prompt = EXCHANGE_VALIDATION_PROMPT.format(conversation_messages=conversation_text)

    # Log the validation attempt
    import os

    log_file = os.path.join(
        os.path.expanduser("~/.claude-pace-maker"), "validation_debug.log"
    )
    try:
        with open(log_file, "a") as f:
            import datetime

            f.write(f"\n{'='*80}\n")
            f.write(f"[{datetime.datetime.now()}] EXCHANGE VALIDATION CALLED\n")
            f.write(f"Number of messages: {len(messages)}\n")
            f.write(
                f"Last message preview: {messages[-1][:300] if messages else 'NO MESSAGES'}...\n"
            )
            f.write(f"Prompt preview: {prompt[:500]}...\n")
    except Exception:
        pass

    # Configure SDK options
    options = ClaudeAgentOptions(
        max_turns=1,
        model="claude-sonnet-4-5",
        system_prompt="You are a ruthless work-avoidance detector. Catch any attempt to escape from doing implementation work.",
        disallowed_tools=["Write", "Edit", "Bash", "TodoWrite", "Read", "Grep", "Glob"],
        max_thinking_tokens=2000,
    )

    # Call SDK
    response_text = ""
    try:
        async for message in query(prompt=prompt, options=options):
            # Messages are typed dataclass objects, not dictionaries
            if isinstance(message, ResultMessage):
                # ResultMessage has result attribute
                if hasattr(message, "result") and message.result:
                    response_text = message.result.strip()
                    break
    except Exception as e:
        # Fallback on SDK error - treat as work required with explanation
        return {
            "legitimate": False,
            "challenge_message": (
                f"Exchange completion validation failed (SDK error: {e}). "
                "If you were asked to do implementation work, use IMPLEMENTATION_COMPLETE "
                "only after completing it. If this was truly just conversation, continue."
            ),
        }

    # Log the actual response for debugging
    try:
        with open(log_file, "a") as f:
            f.write(f"SDK RESPONSE: '{response_text}'\n")
            f.write(f"Response length: {len(response_text)}\n")
    except Exception:
        pass

    # Parse deterministic response
    if response_text == "EXCHANGE_LEGITIMATE":
        return {"legitimate": True, "challenge_message": None}
    elif response_text.startswith("WORK_REQUIRED:"):
        challenge = response_text.replace("WORK_REQUIRED:", "").strip()
        return {"legitimate": False, "challenge_message": challenge}
    else:
        # Unexpected response - treat as work required
        # Log the unexpected format
        try:
            with open(log_file, "a") as f:
                f.write(
                    "UNEXPECTED RESPONSE FORMAT - falling back to generic message\n"
                )
        except Exception:
            pass
        return {
            "legitimate": False,
            "challenge_message": (
                "Exchange completion could not be verified. "
                "If the user requested implementation work, you must DO it and use "
                "IMPLEMENTATION_COMPLETE. If this was truly just conversation, continue."
            ),
        }


def validate_exchange_complete(
    messages: List[str],
) -> Dict[str, Union[bool, str, None]]:
    """
    Synchronous wrapper for async exchange validation function.

    Args:
        messages: List of last 5 conversation messages (raw text)

    Returns:
        {
            'legitimate': True/False,
            'challenge_message': None or str with challenge text
        }
    """
    try:
        # Run async function in event loop
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already in async context - create new loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(validate_exchange_complete_async(messages))
    except Exception as e:
        # Fallback on any error - allow completion with warning
        import sys

        print(
            f"[PACE-MAKER WARNING] Exchange validation error: {e}",
            file=sys.stderr,
        )
        return {"legitimate": True, "challenge_message": None}


async def validate_blockage_legitimacy_async(
    messages: List[str],
) -> Dict[str, Union[bool, str, None]]:
    """
    Validate if COMPLETELY_BLOCKED claim is legitimate.

    Determines if Claude is genuinely blocked from proceeding or making excuses
    to avoid work.

    Args:
        messages: List of last 5 conversation messages (raw text)

    Returns:
        {
            'legitimate': True/False,
            'challenge_message': None or str with challenge text
        }
    """
    if not SDK_AVAILABLE:
        # SDK not available - allow exit (graceful degradation)
        return {"legitimate": True, "challenge_message": None}

    # Build conversation context
    conversation_text = "\n\n---\n\n".join(messages)
    prompt = BLOCKAGE_LEGITIMACY_PROMPT.format(conversation_messages=conversation_text)

    # Log the validation attempt
    import os

    log_file = os.path.join(
        os.path.expanduser("~/.claude-pace-maker"), "validation_debug.log"
    )
    try:
        with open(log_file, "a") as f:
            import datetime

            f.write(f"\n{'='*80}\n")
            f.write(f"[{datetime.datetime.now()}] BLOCKAGE VALIDATION CALLED\n")
            f.write(f"Number of messages: {len(messages)}\n")
            f.write(
                f"Last message preview: {messages[-1][:300] if messages else 'NO MESSAGES'}...\n"
            )
            f.write(f"Prompt preview: {prompt[:500]}...\n")
    except Exception:
        pass

    # Configure SDK options
    options = ClaudeAgentOptions(
        max_turns=1,
        model="claude-sonnet-4-5",
        system_prompt="You are a fair arbitrator evaluating blockage claims. Be balanced and objective.",
        disallowed_tools=["Write", "Edit", "Bash", "TodoWrite", "Read", "Grep", "Glob"],
        max_thinking_tokens=2000,
    )

    # Call SDK
    response_text = ""
    try:
        async for message in query(prompt=prompt, options=options):
            # Messages are typed dataclass objects, not dictionaries
            if isinstance(message, ResultMessage):
                # ResultMessage has result attribute
                if hasattr(message, "result") and message.result:
                    response_text = message.result.strip()
                    break
    except Exception:
        # Fallback on SDK error - allow exit (benefit of doubt on error)
        return {
            "legitimate": True,
            "challenge_message": None,
        }

    # Log the actual response for debugging
    try:
        with open(log_file, "a") as f:
            f.write(f"SDK RESPONSE: '{response_text}'\n")
            f.write(f"Response length: {len(response_text)}\n")
    except Exception:
        pass

    # Parse deterministic response
    if response_text == "BLOCKAGE_LEGITIMATE":
        return {"legitimate": True, "challenge_message": None}
    elif response_text.startswith("BLOCKAGE_REJECTED:"):
        challenge = response_text.replace("BLOCKAGE_REJECTED:", "").strip()
        return {"legitimate": False, "challenge_message": challenge}
    else:
        # Unexpected response - log and allow exit (benefit of doubt)
        try:
            with open(log_file, "a") as f:
                f.write("UNEXPECTED RESPONSE FORMAT - falling back to allow exit\n")
        except Exception:
            pass
        return {"legitimate": True, "challenge_message": None}


def validate_blockage_legitimacy(
    messages: List[str],
) -> Dict[str, Union[bool, str, None]]:
    """
    Synchronous wrapper for async blockage legitimacy validation.

    Args:
        messages: List of last 5 conversation messages (raw text)

    Returns:
        {
            'legitimate': True/False,
            'challenge_message': None or str with challenge text
        }
    """
    try:
        # Run async function in event loop
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already in async context - create new loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(validate_blockage_legitimacy_async(messages))
    except Exception as e:
        # Fallback on any error - allow exit with warning
        import sys

        print(
            f"[PACE-MAKER WARNING] Blockage validation error: {e}",
            file=sys.stderr,
        )
        return {"legitimate": True, "challenge_message": None}
