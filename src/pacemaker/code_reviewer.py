#!/usr/bin/env python3
"""
Code reviewer module for validating code changes against declared intent.

This module provides post-tool validation by calling the Claude Agent SDK to
review if modified code matches the intent Claude declared before making changes.
"""

import asyncio
import logging
from typing import List

logger = logging.getLogger(__name__)

# Try to import Claude Agent SDK
try:
    import claude_agent_sdk  # type: ignore[import-not-found]  # noqa: F401

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False


def build_review_prompt(intent: str, code: str) -> str:
    """
    Build SDK prompt for code review against intent.

    Args:
        intent: The declared intent extracted from assistant messages
        code: The modified file content

    Returns:
        Prompt string for SDK code review
    """
    return f"""You are a strict code reviewer enforcing intent-to-code alignment.

DECLARED INTENT:
{intent}

ACTUAL CODE IN FILE:
{code}

YOUR TASK:
Compare the code against the declared intent and check for violations:

1. EXACT MATCH: Does the code implement EXACTLY what was declared? No more, no less.

2. SCOPE CREEP: Is there ANY code added that was NOT part of the declared intent?
   - Extra functions, classes, or methods not mentioned in intent
   - Additional features or functionality beyond what was declared
   - Refactoring or cleanup of unrelated code
   - Comments, docstrings, or type hints not mentioned in intent

3. UNAUTHORIZED REMOVALS: Was ANY code removed that was NOT declared to be removed?
   - Deleted functions, classes, or code blocks
   - Removed functionality not mentioned in intent

4. UNAUTHORIZED MODIFICATIONS: Was ANY code modified outside the declared scope?
   - Changes to existing code not mentioned in intent
   - Refactoring or renaming of unrelated code
   - Style changes to code outside the declared scope

5. CLEAN CODE VIOLATIONS (Always check regardless of intent):
   - Hardcoded secrets (API keys, passwords, tokens, credentials)
   - SQL injection vulnerabilities (string concatenation in queries instead of parameters)
   - Bare except clauses (catch specific exceptions, not `except:`)
   - Silently swallowed exceptions (logging or re-raising required, not just `pass`)
   - Commented-out code blocks (delete or document WHY kept)
   - Magic numbers (use named constants for clarity)
   - Mutable default arguments (Python: `def func(items=[]):` is dangerous)
   - Overnested if statements (excessive indentation levels)
   - Blatant logic bugs not aligned with intent
   - Missing boundary condition checks (null/None checks before dereferencing, math overflows, array bounds)
   - Lack of comments in complicated or brittle code sections

RESPONSE FORMAT:
- If code EXACTLY matches intent with NO violations: Return empty response (no text at all)
- If ANY violations found: Return specific feedback explaining EACH violation

Be strict: Even small additions or modifications outside the declared intent are violations.
Only return empty response if the code implements EXACTLY what was declared, nothing more."""


async def _call_sdk_review_async(prompt: str) -> str:
    """
    Call SDK for code review with Sonnet + fallback to Opus.

    Args:
        prompt: Review prompt

    Returns:
        SDK response text (feedback or empty)
    """
    if not SDK_AVAILABLE:
        raise ImportError("Claude Agent SDK not available")

    # Fresh import to avoid cached state
    from claude_agent_sdk import query as fresh_query
    from claude_agent_sdk.types import (  # type: ignore[import-not-found]
        ClaudeAgentOptions as FreshOptions,
        ResultMessage as FreshResult,
    )

    # Try Sonnet first with thinking tokens for better review
    options = FreshOptions(
        max_turns=1,
        model="claude-sonnet-4-5",
        max_thinking_tokens=4000,
        system_prompt="You are a code reviewer. Provide feedback only if code doesn't match intent. Return empty response if code is correct.",
        disallowed_tools=["Write", "Edit", "Bash", "TodoWrite", "Read", "Grep", "Glob"],
    )

    response_text = ""
    try:
        async for message in fresh_query(prompt=prompt, options=options):
            if isinstance(message, FreshResult):
                if hasattr(message, "result") and message.result:
                    response_text = message.result.strip()
    except Exception as e:
        # If Sonnet hit usage limit, try Opus
        error_str = str(e).lower()
        if "usage limit" in error_str or "limit reached" in error_str:
            options.model = "claude-opus-4-5"
            try:
                async for message in fresh_query(prompt=prompt, options=options):
                    if isinstance(message, FreshResult):
                        if hasattr(message, "result") and message.result:
                            response_text = message.result.strip()
            except Exception:
                # Fail open: return empty feedback
                pass
        else:
            # Other errors: fail open
            pass

    return response_text


def call_sdk_review(prompt: str) -> str:
    """
    Synchronous wrapper for SDK code review call.

    Uses Sonnet with 4000 thinking tokens for detailed review.
    Falls back to Opus on usage limit.

    Args:
        prompt: Review prompt

    Returns:
        SDK response text (feedback or empty string)
    """
    loop = asyncio.get_event_loop()
    if loop.is_running():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(_call_sdk_review_async(prompt))


def _extract_intent_from_messages(messages: List[str]) -> str:
    """
    Extract intent declaration from assistant messages.

    Args:
        messages: List of recent assistant messages

    Returns:
        Combined intent text from messages
    """
    # Combine all messages - intent may be spread across multiple messages
    return "\n\n".join(messages)


def validate_code_against_intent(file_path: str, messages: List[str]) -> str:
    """
    Main entry point for post-tool code validation.

    Validates that code changes in the file match the intent declared in
    the assistant messages.

    Args:
        file_path: Path to modified file
        messages: Last N assistant messages (containing intent declaration)

    Returns:
        Feedback string if issues found, empty string if code matches intent
    """
    try:
        # 1. Read file content from disk
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                code = f.read()
        except FileNotFoundError:
            # File doesn't exist - fail open (no feedback)
            return ""
        except Exception:
            # Other read errors - fail open
            return ""

        # 2. Extract intent from messages
        intent = _extract_intent_from_messages(messages)

        # 3. Build review prompt
        prompt = build_review_prompt(intent, code)

        # 4. Call SDK
        feedback = call_sdk_review(prompt)

        # 5. Return feedback (empty if OK, text if issues)
        return feedback

    except Exception:
        # Any error - fail open (graceful degradation)
        return ""
