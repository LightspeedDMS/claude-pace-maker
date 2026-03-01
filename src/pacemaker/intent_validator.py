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
import asyncio
import contextlib
from typing import Any, Dict, List

from .transcript_reader import (
    build_stop_hook_context,
    format_stop_hook_context,
)
from .constants import DEFAULT_CONFIG
from .logger import log_warning, log_debug


@contextlib.contextmanager
def _clean_sdk_env():
    """Temporarily remove env vars that prevent SDK subprocess from starting.

    Claude Code sets CLAUDECODE=1 in the environment. The Claude Agent SDK copies
    os.environ to the subprocess it spawns. If CLAUDECODE is present, the subprocess
    CLI detects a nested session and refuses to start (exit code 1).

    This context manager strips CLAUDECODE before SDK calls and restores it after.
    """
    removed = {}
    for key in ("CLAUDECODE",):
        if key in os.environ:
            removed[key] = os.environ.pop(key)
    try:
        yield
    finally:
        os.environ.update(removed)


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

    Loads from: src/pacemaker/prompts/stop/stop_hook_validator_prompt.md

    Returns:
        Validation prompt template string

    Raises:
        FileNotFoundError: If template file is missing (installation broken)
        Exception: If template cannot be loaded
    """
    module_dir = os.path.dirname(__file__)
    prompt_path = os.path.join(
        module_dir, "prompts", "stop", "stop_hook_validator_prompt.md"
    )

    if not os.path.exists(prompt_path):
        raise FileNotFoundError(
            f"Stop hook validator prompt template not found at {prompt_path}. "
            "This indicates a broken installation. Run ./install.sh to fix."
        )

    return load_prompt_template(prompt_path)


def get_pre_tool_prompt_template() -> str:
    """
    Get pre-tool validation prompt template from external file.

    Loads from: src/pacemaker/prompts/pre_tool_use/pre_tool_validator_prompt.md

    Returns:
        Pre-tool validation prompt template string

    Raises:
        FileNotFoundError: If template file is missing (installation broken)
        Exception: If template cannot be loaded
    """
    module_dir = os.path.dirname(__file__)
    prompt_path = os.path.join(
        module_dir, "prompts", "pre_tool_use", "pre_tool_validator_prompt.md"
    )

    if not os.path.exists(prompt_path):
        raise FileNotFoundError(
            f"Pre-tool validator prompt template not found at {prompt_path}. "
            "This indicates a broken installation. Run ./install.sh to fix."
        )

    return load_prompt_template(prompt_path)


def build_validation_prompt(conversation_context: str) -> str:
    """
    Build SDK validation prompt from template.

    Args:
        conversation_context: Formatted conversation context from format_stop_hook_context()

    Returns:
        Complete validation prompt for SDK
    """
    # Get template from external file
    template = get_prompt_template()

    # Fill template
    return template.format(conversation_context=conversation_context)


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
    log_debug(
        "intent_validator",
        f"_fresh_sdk_call: START model={model}, prompt_len={len(prompt)}",
    )

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

    log_debug("intent_validator", "_fresh_sdk_call: Starting async iteration")

    response_text = ""
    # SDK may throw exception after returning result (e.g., on usage limit)
    # Capture the response before any exception
    try:
        with _clean_sdk_env():
            async for message in fresh_query(prompt=prompt, options=options):
                log_debug(
                    "intent_validator",
                    f"_fresh_sdk_call: Got message type={type(message).__name__}",
                )
                if isinstance(message, FreshResult):
                    if hasattr(message, "result") and message.result:
                        response_text = message.result.strip()
    except Exception:
        # Exception after getting response is OK - we have what we need
        import traceback

        log_debug(
            "intent_validator", f"_fresh_sdk_call: EXCEPTION: {traceback.format_exc()}"
        )

    log_debug(
        "intent_validator",
        f"_fresh_sdk_call: RETURN response_len={len(response_text)}, preview={response_text[:100] if response_text else 'EMPTY'}",
    )

    return response_text


async def call_sdk_validation_async(conversation_context: str) -> str:
    """
    Call Claude Agent SDK for intent validation.
    Tries sonnet first, falls back to opus if sonnet hits usage limit.

    Args:
        conversation_context: Formatted conversation context from format_stop_hook_context()

    Returns:
        SDK response text
    """
    if not SDK_AVAILABLE:
        raise ImportError("Claude Agent SDK not available")

    prompt = build_validation_prompt(conversation_context)

    # Try sonnet first
    response = await _fresh_sdk_call(prompt, "claude-sonnet-4-5")

    # If sonnet hit usage limit, fall back to opus
    if _is_limit_error(response):
        response = await _fresh_sdk_call(prompt, "claude-opus-4-5")

    return response


def call_sdk_validation(conversation_context: str) -> str:
    """
    Synchronous wrapper for SDK validation call.

    Args:
        conversation_context: Formatted conversation context from format_stop_hook_context()

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

    return loop.run_until_complete(call_sdk_validation_async(conversation_context))


def validate_intent(
    session_id: str,
    transcript_path: str,
    conversation_context_size: int = 5,
) -> Dict[str, Any]:
    """
    Validate if Claude completed user's original intent.

    Uses first-pairs + backwards-walk algorithm:
    1. Extract first N user/assistant pairs (session goals)
    2. Walk backwards from end to fill remaining token budget
    3. Format context with truncation marker
    4. Call SDK to validate completion
    5. Parse response and return decision

    Args:
        session_id: Session ID (currently unused but kept for compatibility)
        transcript_path: Path to conversation transcript
        conversation_context_size: Deprecated - now uses config settings

    Returns:
        Decision dict:
        - {"continue": True} - Allow exit
        - {"decision": "block", "reason": "feedback"} - Block with feedback
    """
    try:
        # Verify transcript exists
        if not os.path.exists(transcript_path):
            return {"continue": True}

        # Get config values
        from .constants import DEFAULT_CONFIG

        token_budget = DEFAULT_CONFIG.get("stop_hook_token_budget", 48000)
        first_n_pairs = DEFAULT_CONFIG.get("stop_hook_first_n_pairs", 10)

        # Build context using new algorithm
        context = build_stop_hook_context(
            transcript_path=transcript_path,
            first_n_pairs=first_n_pairs,
            token_budget=token_budget,
        )

        # Fail open if no context available
        if not context["first_pairs"] and not context["backwards_messages"]:
            return {"continue": True}

        # Format context for prompt
        formatted_context = format_stop_hook_context(context)

        # Call SDK for validation
        sdk_response = call_sdk_validation(formatted_context)

        # Log raw SDK response for debugging
        log_debug(
            "intent_validator",
            f"SDK raw response: {sdk_response[:500] if sdk_response else 'EMPTY'}",
        )

        # Parse response
        return parse_sdk_response(sdk_response)

    except Exception as e:
        # Any error - fail open (graceful degradation)
        log_debug("intent_validator", f"SDK ERROR (failing open): {e}")
        return {"continue": True}


def _build_intent_declaration_prompt(
    messages: List[str], file_path: str, tool_name: str
) -> str:
    """
    Build SDK prompt for checking if intent was declared using external template.

    Args:
        messages: Last N assistant messages
        file_path: Target file path
        tool_name: Tool being used (Write/Edit)

    Returns:
        Prompt string for SDK with variables replaced
    """
    from .prompt_loader import PromptLoader

    filename = os.path.basename(file_path)
    action = "create or modify" if tool_name == "Write" else "edit"

    messages_text = "\n\n".join(
        [f"Message {i+1}:\n{msg}" for i, msg in enumerate(messages)]
    )

    # Load template and replace variables
    loader = PromptLoader()
    return loader.load_prompt(
        "intent_declaration_prompt.md",
        subfolder="common",
        variables={
            "action": action,
            "filename": filename,
            "tool_name": tool_name,
            "messages_text": messages_text,
        },
    )


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
        model="claude-sonnet-4-5",
        max_thinking_tokens=2000,
        system_prompt="You are validating if intent was declared. Respond with YES or NO only.",
        disallowed_tools=["Write", "Edit", "Bash", "TodoWrite", "Read", "Grep", "Glob"],
    )

    response_text = ""
    try:
        with _clean_sdk_env():
            async for message in fresh_query(prompt=prompt, options=options):
                if isinstance(message, FreshResult):
                    if hasattr(message, "result") and message.result:
                        response_text = message.result.strip()
    except Exception as e:
        log_warning("intent_validator", "SDK intent validation call failed", e)

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

    except Exception as e:
        log_warning("intent_validator", "Intent declaration validation failed", e)
        return {"intent_found": False}


def extract_current_assistant_message(messages: List[str]) -> str:
    """
    Extract the CURRENT assistant message by finding "intent:" marker.

    Searches backward up to 3 messages to find intent declaration.
    Case-insensitive search for "intent:" anywhere in message text.
    Handles intermediate tool calls (Read, Grep, etc.) between intent and Write/Edit.

    Args:
        messages: List of messages (most recent last)

    Returns:
        Combined text with intent declaration + current tool call
    """
    if not messages:
        return ""

    if len(messages) == 1:
        return messages[-1]

    # Current tool message (always last)
    current_tool = messages[-1]

    # Search backward up to 3 messages for "intent:" marker (case-insensitive)
    for i in range(min(3, len(messages) - 1)):
        msg = messages[-(i + 2)]  # Check messages[-2], messages[-3], messages[-4]
        if msg and "intent:" in msg.lower():
            return f"{msg}\n\n{current_tool}"

    # Fallback: if no intent: found, return just the current tool
    # (This will trigger validation failure as expected)
    return current_tool


def _build_stage1_prompt(current_message: str, file_path: str, tool_name: str) -> str:
    """
    Build Stage 1 validation prompt from external template.

    Args:
        current_message: Current assistant message
        file_path: Target file path
        tool_name: Write or Edit

    Returns:
        Stage 1 prompt string with variables replaced
    """
    module_dir = os.path.dirname(__file__)
    prompt_path = os.path.join(
        module_dir, "prompts", "pre_tool_use", "stage1_declaration_check.md"
    )

    if not os.path.exists(prompt_path):
        raise FileNotFoundError(
            f"Stage 1 prompt template not found at {prompt_path}. "
            "This indicates a broken installation. Run ./install.sh to fix."
        )

    template = load_prompt_template(prompt_path)

    # Load excluded paths and format for prompt
    from .constants import DEFAULT_EXCLUDED_PATHS_PATH
    from . import excluded_paths

    exclusions = excluded_paths.load_exclusions(DEFAULT_EXCLUDED_PATHS_PATH)
    excluded_paths_text = excluded_paths.format_exclusions_for_prompt(exclusions)

    # Replace placeholders
    prompt = template.format(
        current_message=current_message, file_path=file_path, tool_name=tool_name
    )

    # Replace excluded_paths placeholder
    # NOTE: Template has {{excluded_paths}} (double braces), but .format() above
    # consumes one layer, leaving {excluded_paths} (single braces)
    prompt = prompt.replace("{excluded_paths}", excluded_paths_text)

    return prompt


async def _call_stage1_validation_async(prompt: str) -> str:
    """
    Call SDK with Haiku for Stage 1 fast validation.

    Args:
        prompt: Stage 1 validation prompt

    Returns:
        SDK response text (YES, NO, or NO_TDD)
    """
    if not SDK_AVAILABLE:
        raise ImportError("Claude Agent SDK not available")

    # Fresh import to avoid cached state
    from claude_agent_sdk import query as fresh_query
    from claude_agent_sdk.types import (  # type: ignore[import-not-found]
        ClaudeAgentOptions as FreshOptions,
        ResultMessage as FreshResult,
    )

    # Use Sonnet for better intent detection (Stage 1)
    options = FreshOptions(
        max_turns=1,
        model="claude-sonnet-4-5",
        max_thinking_tokens=1024,  # API minimum is 1024
        system_prompt="You are validating intent declarations. Respond with YES, NO, or NO_TDD only.",
        disallowed_tools=["Write", "Edit", "Bash", "TodoWrite", "Read", "Grep", "Glob"],
    )

    response_text = ""
    try:
        with _clean_sdk_env():
            async for message in fresh_query(prompt=prompt, options=options):
                if isinstance(message, FreshResult):
                    if hasattr(message, "result") and message.result:
                        response_text = message.result.strip()
    except Exception as e:
        log_warning("intent_validator", "Stage 1 validation call failed", e)

    return response_text


def _call_stage1_validation(prompt: str) -> str:
    """
    Synchronous wrapper for Stage 1 validation.

    Args:
        prompt: Stage 1 validation prompt

    Returns:
        SDK response text (YES, NO, or NO_TDD)
    """
    loop = asyncio.get_event_loop()
    if loop.is_running():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(_call_stage1_validation_async(prompt))


def _build_stage2_prompt(
    messages: List[str], code: str, file_path: str, tool_name: str
) -> str:
    """
    Build Stage 2 validation prompt from external template.

    Args:
        messages: Last 4 messages for context
        code: Proposed code
        file_path: Target file path
        tool_name: Write or Edit

    Returns:
        Stage 2 prompt string with variables replaced
    """
    module_dir = os.path.dirname(__file__)
    prompt_path = os.path.join(
        module_dir, "prompts", "pre_tool_use", "stage2_code_review.md"
    )

    if not os.path.exists(prompt_path):
        raise FileNotFoundError(
            f"Stage 2 prompt template not found at {prompt_path}. "
            "This indicates a broken installation. Run ./install.sh to fix."
        )

    template = load_prompt_template(prompt_path)

    # Format messages for template
    messages_text = "\n".join(f"Message {i+1}: {msg}" for i, msg in enumerate(messages))

    # Load clean code rules
    from .constants import DEFAULT_CLEAN_CODE_RULES_PATH
    from . import clean_code_rules

    rules = clean_code_rules.load_rules(DEFAULT_CLEAN_CODE_RULES_PATH)
    clean_code_rules_text = clean_code_rules.format_rules_for_validation(rules)

    return template.format(
        messages=messages_text,
        code=code,
        file_path=file_path,
        clean_code_rules=clean_code_rules_text,
    )


def generate_validation_prompt(
    messages: List[str],
    code: str,
    file_path: str,
    tool_name: str,
    config: Dict[str, Any] = None,
) -> str:
    """
    Generate validation prompt for pre-tool validation.

    Extracted for testability - generates prompt without calling SDK.

    Args:
        messages: Last 4 assistant messages (current + 3 before)
        code: Proposed code that will be written
        file_path: Target file path
        tool_name: Write or Edit
        config: Optional config dict (for testing). If None, loads from file.

    Returns:
        Formatted validation prompt string
    """
    # Format messages for template
    messages_text = "\n".join(f"Message {i+1}: {msg}" for i, msg in enumerate(messages))

    # Load clean code rules
    from .constants import DEFAULT_CLEAN_CODE_RULES_PATH
    from . import clean_code_rules

    rules = clean_code_rules.load_rules(DEFAULT_CLEAN_CODE_RULES_PATH)
    clean_code_rules_text = clean_code_rules.format_rules_for_validation(rules)

    # Load config to check TDD state
    if config is None:
        from .hook import load_config

        config = load_config()
    intent_validation_enabled = config.get("intent_validation_enabled", False)
    tdd_enabled = config.get("tdd_enabled", True)

    # Determine TDD section content
    # TDD active only when BOTH intent_validation AND tdd_enabled are true
    tdd_section = ""
    if intent_validation_enabled and tdd_enabled:
        # Load TDD section from external file
        module_dir = os.path.dirname(__file__)
        tdd_section_path = os.path.join(
            module_dir, "prompts", "pre_tool_use", "tdd_section.md"
        )
        if os.path.exists(tdd_section_path):
            with open(tdd_section_path, "r", encoding="utf-8") as f:
                tdd_section_template = f.read()

            # Load core paths and format for prompt
            from .constants import DEFAULT_CORE_PATHS_PATH
            from . import core_paths

            paths = core_paths.load_paths(DEFAULT_CORE_PATHS_PATH)
            core_paths_text = core_paths.format_paths_for_prompt(paths)

            # Replace placeholder
            tdd_section = tdd_section_template.replace(
                "{{core_paths}}", core_paths_text
            )
        else:
            # File missing - log warning and continue without TDD section
            log_warning(
                "intent_validator",
                f"TDD section file not found: {tdd_section_path}. TDD enforcement disabled.",
                None,
            )

    # Load template and fill with parameters
    template = get_pre_tool_prompt_template()
    prompt = template.format(
        tool_name=tool_name,
        file_path=file_path,
        messages=messages_text,
        code=code,
        clean_code_rules=clean_code_rules_text,
        tdd_section=tdd_section,
    )

    return prompt


def validate_intent_and_code(
    messages: List[str], code: str, file_path: str, tool_name: str
) -> dict:
    """
    Two-stage pre-tool validation with short-circuit logic.

    Stage 1: Fast declaration check (CURRENT message only)
      - Checks intent declaration exists in CURRENT message
      - Checks TDD declaration exists for core paths
      - Uses Haiku for speed (<500ms)

    Stage 2: Comprehensive code review (only if Stage 1 passes)
      - Validates code matches declared intent
      - Checks for clean code violations
      - Uses Opus for quality

    Args:
        messages: Last 4 assistant messages (current + 3 before)
        code: Proposed code that will be written
        file_path: Target file path
        tool_name: Write or Edit

    Returns:
        {"approved": True} if all checks pass
        {"approved": False, "feedback": "..."} if violations found
    """
    if not SDK_AVAILABLE:
        # Fail closed when SDK unavailable - no fallback validation
        return {
            "approved": False,
            "feedback": """⛔ Intent Validation Unavailable

Claude Agent SDK is not available for intent validation.

This is a REQUIRED dependency for intent validation to function.
Please install the SDK or disable intent validation in config:

  pace-maker tdd off

System failing closed to prevent bypassing intent declaration requirements.""",
        }

    try:
        # STAGE 1: Fast declaration check (CURRENT message only)
        current_message = extract_current_assistant_message(messages)
        log_debug("intent_validator", "=== STAGE 1 VALIDATION START ===")
        log_debug("intent_validator", f"File path: {file_path}")
        log_debug("intent_validator", f"Tool name: {tool_name}")
        log_debug(
            "intent_validator", f"Current message length: {len(current_message)} chars"
        )
        log_debug(
            "intent_validator", f"Current message preview: {current_message[:200]}..."
        )

        stage1_prompt = _build_stage1_prompt(current_message, file_path, tool_name)
        log_debug(
            "intent_validator", f"Stage 1 prompt length: {len(stage1_prompt)} chars"
        )
        log_debug("intent_validator", "=" * 80)
        log_debug("intent_validator", "STAGE 1 COMPLETE PROMPT BEING SENT TO SDK:")
        log_debug("intent_validator", "=" * 80)
        log_debug("intent_validator", stage1_prompt)
        log_debug("intent_validator", "=" * 80)
        log_debug("intent_validator", "END OF STAGE 1 PROMPT")
        log_debug("intent_validator", "=" * 80)

        stage1_response = _call_stage1_validation(stage1_prompt)
        log_debug("intent_validator", f"Stage 1 SDK response: '{stage1_response}'")

        # Parse Stage 1 response
        stage1_response_upper = stage1_response.strip().upper()
        log_debug(
            "intent_validator", f"Stage 1 parsed response: '{stage1_response_upper}'"
        )

        if stage1_response_upper == "NO":
            # Intent declaration missing
            return {
                "approved": False,
                "feedback": """⛔ Intent declaration required

You must declare your intent BEFORE using Write/Edit tools.

⚠️  CRITICAL: Start with "INTENT:" marker!

Required format - include ALL 3 components IN YOUR CURRENT MESSAGE:
  1. FILE: Which file you're modifying
  2. CHANGES: What specific changes you're making
  3. GOAL: Why you're making these changes

Example (all in same message as Write/Edit):
  "INTENT: Modify src/auth.py to add a validate_input() function
   that checks user input for XSS attacks, to improve security."

Then use your Write/Edit tool in the same message.""",
            }

        elif stage1_response_upper == "NO_TDD":
            # TDD declaration missing for core path
            return {
                "approved": False,
                "tdd_failure": True,
                "feedback": f"""⛔ TDD Required for Core Code

You're modifying core code: {file_path}

No test declaration found in your CURRENT message. Before modifying core code, you must either:

1. Declare the corresponding test IN YOUR CURRENT MESSAGE:
   - TEST FILE: Which test file covers this change
   - TEST SCOPE: What behavior the test validates

2. OR quote the user's explicit permission to skip TDD

Example with test declaration (in same message as Write/Edit):
  "INTENT: Modify src/auth.py to add password validation.
   Test coverage: tests/test_auth.py - test_password_validation_rejects_weak_passwords()"

Example citing user permission (in same message as Write/Edit):
  "INTENT: Modify src/auth.py to add password validation.
   User permission to skip TDD: User said 'skip tests for this' in message 3."

CRITICAL: Quote must reference actual user words from recent context.""",
            }

        # Stage 1 passed - proceed to Stage 2
        log_debug("intent_validator", "=== STAGE 1 PASSED - PROCEEDING TO STAGE 2 ===")

        # STAGE 2: Comprehensive code review
        stage2_prompt = _build_stage2_prompt(messages, code, file_path, tool_name)
        log_debug(
            "intent_validator", f"Stage 2 prompt length: {len(stage2_prompt)} chars"
        )

        stage2_feedback = asyncio.run(_call_unified_validation_async(stage2_prompt))
        log_debug(
            "intent_validator",
            f"Stage 2 SDK response length: {len(stage2_feedback)} chars",
        )
        log_debug(
            "intent_validator",
            f"Stage 2 feedback preview: {stage2_feedback[:200] if stage2_feedback else '(empty)'}...",
        )

        if stage2_feedback.strip().upper() == "APPROVED":
            # APPROVED response = approved
            log_debug("intent_validator", "=== STAGE 2 APPROVED ===")
            return {"approved": True}
        else:
            # Any other response = blocked with feedback
            log_debug("intent_validator", "=== STAGE 2 BLOCKED (has feedback) ===")

            # Detect if this is a clean code violation
            feedback_lower = stage2_feedback.lower()
            clean_code_keywords = [
                "clean code",
                "hardcoded secret",
                "sql injection",
                "bare except",
                "swallowed exception",
                "commented-out code",
                "magic number",
                "mutable default",
                "god class",
                "long method",
                "deeply nested",
                "code smell",
                "code violation",
            ]
            is_clean_code_failure = any(
                kw in feedback_lower for kw in clean_code_keywords
            )

            return {
                "approved": False,
                "feedback": stage2_feedback,
                "clean_code_failure": is_clean_code_failure,
            }

    except Exception as e:
        log_warning("intent_validator", "Two-stage validation failed", e)
        log_debug("intent_validator", f"=== VALIDATION EXCEPTION: {str(e)} ===")
        # Fail closed on unexpected errors
        return {
            "approved": False,
            "feedback": f"""⛔ Intent Validation System Error

An unexpected error occurred during intent validation: {str(e)}

Failing closed to prevent bypassing validation requirements.
Please retry your operation or report this issue.""",
        }


async def _call_unified_validation_async(prompt: str) -> str:
    """Call SDK for unified validation with Opus (fallback to Sonnet on usage limits)."""
    if not SDK_AVAILABLE:
        return ""

    from claude_agent_sdk import query as fresh_query
    from claude_agent_sdk.types import (
        ClaudeAgentOptions as FreshOptions,
        ResultMessage as FreshResult,
    )

    # Try Opus first for better validation quality
    options = FreshOptions(
        max_turns=1,
        model="claude-opus-4-5",
        max_thinking_tokens=4000,
        system_prompt="You are a strict code validator. Return empty response ONLY if all checks pass. Otherwise return detailed feedback.",
        disallowed_tools=["Write", "Edit", "Bash", "TodoWrite", "Read", "Grep", "Glob"],
    )

    response_text = ""
    try:
        with _clean_sdk_env():
            async for message in fresh_query(prompt=prompt, options=options):
                if isinstance(message, FreshResult):
                    if hasattr(message, "result") and message.result:
                        response_text = message.result.strip()
    except Exception as e:
        log_warning("intent_validator", "Unified validation SDK call failed", e)

    # If Opus hit usage limit, fall back to Sonnet
    if _is_limit_error(response_text):
        options_sonnet = FreshOptions(
            max_turns=1,
            model="claude-sonnet-4-5",
            max_thinking_tokens=4000,
            system_prompt="You are a strict code validator. Return empty response ONLY if all checks pass. Otherwise return detailed feedback.",
            disallowed_tools=[
                "Write",
                "Edit",
                "Bash",
                "TodoWrite",
                "Read",
                "Grep",
                "Glob",
            ],
        )

        response_text = ""
        try:
            with _clean_sdk_env():
                async for message in fresh_query(prompt=prompt, options=options_sonnet):
                    if isinstance(message, FreshResult):
                        if hasattr(message, "result") and message.result:
                            response_text = message.result.strip()
        except Exception as e:
            log_warning(
                "intent_validator", "Unified validation fallback to Sonnet failed", e
            )

    return response_text
