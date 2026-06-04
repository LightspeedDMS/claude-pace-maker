#!/usr/bin/env python3
"""
Code reviewer module for validating code changes against declared intent.

This module provides post-tool validation by calling the Claude Agent SDK to
review if modified code matches the intent Claude declared before making changes.
"""

from typing import List

from .logger import log_warning, log_info
from .inference import resolve_and_call


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


def call_sdk_review(prompt: str, hook_model: str = "auto") -> str:
    """
    Synchronous SDK code review via provider abstraction.

    Args:
        prompt: Review prompt
        hook_model: Model selection - "auto", "sonnet", "opus", "gpt-5.4", "gpt-5.5" (legacy: "gpt-5")

    Returns:
        SDK response text (feedback or empty string)
    """
    return resolve_and_call(
        hook_model=hook_model,
        prompt=prompt,
        system_prompt="You are a code reviewer. Provide feedback only if code doesn't match intent. Return empty response if code is correct.",
        call_context="code_review",
        max_thinking_tokens=4000,
    )


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
            log_info("code_reviewer", f"File not found: {file_path}")
            return ""
        except Exception as e:
            log_warning("code_reviewer", "Failed to read file for validation", e)
            return ""

        # 2. Extract intent from messages
        intent = _extract_intent_from_messages(messages)

        # 3. Build review prompt
        prompt = build_review_prompt(intent, code)

        # 4. Call SDK
        feedback = call_sdk_review(prompt)

        # 5. Return feedback (empty if OK, text if issues)
        return feedback

    except Exception as e:
        log_warning("code_reviewer", "Code validation failed", e)
        return ""
