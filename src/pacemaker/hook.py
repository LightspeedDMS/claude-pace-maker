#!/usr/bin/env python3
"""
Post-tool-use hook entry point for Credit-Aware Adaptive Throttling.

This module is called by the Claude Code post-tool-use hook.
It runs pacing checks and applies adaptive throttling.
"""

import os
import sys
import time
from pathlib import Path
from datetime import datetime
import json
from typing import Optional, Dict, Any

from . import pacing_engine, database, user_commands
from .constants import (
    DEFAULT_CONFIG,
    DEFAULT_DB_PATH,
    DEFAULT_CONFIG_PATH,
    DEFAULT_STATE_PATH,
    DEFAULT_EXTENSION_REGISTRY_PATH,
    MAX_DELAY_SECONDS,
)
from .transcript_reader import (
    get_last_n_messages_for_validation,
)
from .logger import log_warning, log_debug


def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    """Load configuration from file."""
    try:
        if os.path.exists(config_path):
            with open(config_path) as f:
                return json.load(f)
    except Exception as e:
        log_warning("hook", "Failed to load config, using defaults", e)

    return DEFAULT_CONFIG.copy()


def load_state(state_path: str = DEFAULT_STATE_PATH) -> dict:
    """Load hook state (last poll time, last cleanup time, session ID)."""
    # Default state
    default_state = {
        "session_id": f"session-{int(time.time())}",
        "last_poll_time": None,
        "last_cleanup_time": None,
        "in_subagent": False,
        "subagent_counter": 0,
        "tool_execution_count": 0,
    }

    try:
        if os.path.exists(state_path):
            with open(state_path) as f:
                data = json.load(f)

                # Convert timestamp strings back to datetime
                if data.get("last_poll_time"):
                    data["last_poll_time"] = datetime.fromisoformat(
                        data["last_poll_time"]
                    )
                if data.get("last_cleanup_time"):
                    data["last_cleanup_time"] = datetime.fromisoformat(
                        data["last_cleanup_time"]
                    )

                # Merge with defaults to ensure all required fields exist
                # Loaded data takes precedence, defaults fill in missing fields
                return {**default_state, **data}
    except Exception as e:
        log_warning("hook", "Failed to load state, using defaults", e)

    # File doesn't exist or failed to load - return defaults
    return default_state


def save_state(state: dict, state_path: str = DEFAULT_STATE_PATH):
    """Save hook state for next invocation."""
    try:
        # Ensure directory exists
        Path(state_path).parent.mkdir(parents=True, exist_ok=True)

        # Convert datetime to string for JSON serialization
        state_copy = state.copy()
        if isinstance(state_copy.get("last_poll_time"), datetime):
            state_copy["last_poll_time"] = state_copy["last_poll_time"].isoformat()
        if isinstance(state_copy.get("last_cleanup_time"), datetime):
            state_copy["last_cleanup_time"] = state_copy[
                "last_cleanup_time"
            ].isoformat()

        with open(state_path, "w") as f:
            json.dump(state_copy, f)
    except Exception as e:
        log_warning("hook", "Failed to save state", e)


def execute_delay(delay_seconds: int):
    """Execute direct delay (sleep)."""
    if delay_seconds > 0:
        # Cap at MAX_DELAY_SECONDS (360s timeout - 10s safety margin)
        actual_delay = min(delay_seconds, MAX_DELAY_SECONDS)
        time.sleep(actual_delay)


def inject_prompt_delay(prompt: str):
    """Inject prompt for Claude to wait."""
    # Print to stdout so Claude sees it
    print(prompt, file=sys.stdout, flush=True)


def display_intent_validation_guidance() -> str:
    """
    Generate intent validation guidance to Claude.

    Shared helper used by both SessionStart and SubagentStart hooks.
    Shows requirements for intent declaration and TDD enforcement.

    Returns:
        String containing the guidance text
    """
    lines = []
    lines.append("\n" + "=" * 70)
    lines.append("⚠️  INTENT VALIDATION ENABLED")
    lines.append("=" * 70)
    lines.append(
        "\nBefore modifying code files, you MUST declare your intent explicitly:"
    )
    lines.append("\nDeclare EXACTLY these 3 components:")
    lines.append("  1. What file you're modifying")
    lines.append("  2. What changes you're making")
    lines.append("  3. Why/goal of the changes")
    lines.append("\nGOOD Example:")
    lines.append("  'I will modify src/auth.py to add a validate_token() function")
    lines.append("   that checks JWT expiration, to fix the security vulnerability.'")
    lines.append("\nBAD Examples:")
    lines.append("  ✗ 'Fixing auth bug' - Missing file and specifics")
    lines.append("  ✗ 'Updating code' - Too vague")
    lines.append("\n" + "-" * 70)
    lines.append("TDD ENFORCEMENT FOR CORE CODE")
    lines.append("-" * 70)
    lines.append(
        "\nFiles in core paths (src/, lib/, core/, source/, libraries/, kernel/)"
    )
    lines.append("require test declarations.")
    lines.append("\n**Option A - Declare test coverage:**")
    lines.append("  'I will modify src/auth.py to add a validate_password() function")
    lines.append("   that checks password strength, to improve security.")
    lines.append(
        "   Test coverage: tests/test_auth.py - test_validate_password_rejects_weak()'"
    )
    lines.append("\n**Option B - Quote user permission to skip TDD:**")
    lines.append("  'I will modify src/auth.py to add a validate_password() function")
    lines.append("   that checks password strength, to improve security.")
    lines.append(
        '   User permission to skip TDD: User said "skip tests for this" in message 3.\''
    )
    lines.append(
        "\nThe quoted permission MUST exist in the last 5 messages. Fabricated quotes are rejected."
    )
    lines.append(
        "\n⚠️  MANDATORY: Declare intent in the SAME message as the Write/Edit tool call."
    )
    lines.append("=" * 70 + "\n")

    return "\n".join(lines)


def run_session_start_hook():
    """
    Handle SessionStart hook - beginning of new session.

    Resets subagent_counter to 0 and in_subagent to false to ensure clean state.
    This prevents state corruption from cancelled subagents.

    Also displays intent validation mandate if feature is enabled.
    """
    # Load config to check master switch
    config = load_config(DEFAULT_CONFIG_PATH)

    # Master switch - all features disabled
    if not config.get("enabled", True):
        return

    # Load state
    state = load_state(DEFAULT_STATE_PATH)

    # Reset counter
    state["subagent_counter"] = 0

    # Ensure we start in main context
    state["in_subagent"] = False

    # Save state
    save_state(state, DEFAULT_STATE_PATH)

    # Display intent validation mandate if enabled
    try:
        if config.get("intent_validation_enabled", False):
            guidance = display_intent_validation_guidance()
            print(guidance, file=sys.stdout, flush=True)
    except Exception as e:
        # Log error but don't break session start
        print(
            f"[PACE-MAKER WARNING] Failed to display intent guidance: {e}",
            file=sys.stderr,
        )


def run_subagent_start_hook():
    """
    Handle SubagentStart hook - entering subagent context.

    Increments subagent_counter and sets in_subagent flag based on counter.
    Does NOT reset tool_execution_count (global counter persists).

    Also displays intent validation mandate if feature is enabled.
    """
    # Load config to check if intent validation enabled
    config = load_config(DEFAULT_CONFIG_PATH)

    # Load state
    state = load_state(DEFAULT_STATE_PATH)

    # Increment counter
    state["subagent_counter"] = state.get("subagent_counter", 0) + 1

    # Set flag based on counter
    state["in_subagent"] = state["subagent_counter"] > 0

    # Save state
    save_state(state, DEFAULT_STATE_PATH)

    # Display intent validation mandate if enabled
    try:
        if config.get("intent_validation_enabled", False):
            guidance = display_intent_validation_guidance()
            # Output JSON with additionalContext for subagent injection
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "SubagentStart",
                    "additionalContext": guidance,
                }
            }
            print(json.dumps(output), file=sys.stdout, flush=True)
    except Exception as e:
        # Log error but don't break subagent start
        print(
            f"[PACE-MAKER WARNING] Failed to display intent guidance: {e}",
            file=sys.stderr,
        )


def run_subagent_stop_hook():
    """
    Handle SubagentStop hook - exiting subagent context.

    Decrements subagent_counter and sets in_subagent flag based on counter.
    Does NOT reset tool_execution_count (global counter persists).
    """
    # Load state
    state = load_state(DEFAULT_STATE_PATH)

    # Decrement counter (never go below 0)
    state["subagent_counter"] = max(0, state.get("subagent_counter", 0) - 1)

    # Set flag based on counter
    state["in_subagent"] = state["subagent_counter"] > 0

    # Save state
    save_state(state, DEFAULT_STATE_PATH)


def should_inject_reminder(
    state: dict, config: dict, tool_name: Optional[str] = None
) -> bool:
    """
    Determine if we should inject the subagent reminder.

    Conditions:
    - NOT in subagent (in_subagent == false)
    - Feature enabled in config
    - EITHER:
      a) Write tool used in main context (immediate nudge, bypasses counter)
      b) tool_execution_count is multiple of frequency (every 5 executions)

    Args:
        state: Current session state
        config: Configuration dictionary
        tool_name: Name of the tool that was just executed (optional)

    Returns:
        True if should inject reminder, False otherwise
    """
    # Skip if in subagent
    if state.get("in_subagent", False):
        return False

    # Skip if disabled
    if not config.get("subagent_reminder_enabled", True):
        return False

    # IMMEDIATE NUDGE: Write or Edit tool used in main context
    if tool_name in ("Write", "Edit"):
        return True

    # COUNTER-BASED NUDGE: Check frequency (every 5 executions by default)
    count = state.get("tool_execution_count", 0)
    frequency = config.get("subagent_reminder_frequency", 5)

    # Only inject on multiples of frequency (and not on count 0)
    return count > 0 and count % frequency == 0


def inject_subagent_reminder(config: dict) -> Optional[str]:
    """
    Get subagent reminder message.

    Returns the reminder message that should be shown to Claude.
    Does NOT print to stdout - caller is responsible for output.

    Args:
        config: Configuration dictionary

    Returns:
        Reminder message string, or None if not applicable
    """
    message = config.get(
        "subagent_reminder_message",
        "💡 Consider using the Task tool to delegate work to specialized subagents (per your guidelines)",
    )

    return message


def run_hook():
    """Main hook execution with pacing AND code review.

    Returns:
        bool: True if code review feedback was provided, False otherwise
    """

    # Track pending message (code review takes priority over subagent nudge)
    pending_message = None

    # Track if feedback was provided (for exit code decision)
    feedback_provided = False

    # Load configuration
    config = load_config(DEFAULT_CONFIG_PATH)

    # Check if enabled
    if not config.get("enabled", True):
        return feedback_provided  # Disabled - do nothing

    # Read hook data from stdin to get tool_name
    tool_name = None
    hook_data = None
    try:
        raw_input = sys.stdin.read()
        if raw_input:
            hook_data = json.loads(raw_input)
            tool_name = hook_data.get("tool_name")
    except (json.JSONDecodeError, Exception) as e:
        log_warning("hook", "Failed to parse hook data from stdin", e)

    # Load state
    state = load_state(DEFAULT_STATE_PATH)

    # Increment global tool execution counter
    state["tool_execution_count"] = state.get("tool_execution_count", 0) + 1

    # Ensure database is initialized
    db_path = DEFAULT_DB_PATH
    database.initialize_database(db_path)

    # Run pacing check
    result = pacing_engine.run_pacing_check(
        db_path=db_path,
        session_id=state["session_id"],
        last_poll_time=state.get("last_poll_time"),
        poll_interval=config.get("poll_interval", 60),
        last_cleanup_time=state.get("last_cleanup_time"),
        safety_buffer_pct=config.get("safety_buffer_pct", 95.0),
        preload_hours=config.get("preload_hours", 12.0),
        api_timeout_seconds=config.get("api_timeout_seconds", 10),
        cleanup_interval_hours=config.get("cleanup_interval_hours", 24),
        retention_days=config.get("retention_days", 60),
        weekly_limit_enabled=config.get("weekly_limit_enabled", True),
        five_hour_limit_enabled=config.get("five_hour_limit_enabled", True),
    )

    # Update state if polled or cleaned up
    state_changed = False
    if result.get("polled"):
        state["last_poll_time"] = result.get("poll_time")
        state_changed = True
    if result.get("cleanup_time"):
        state["last_cleanup_time"] = result.get("cleanup_time")
        state_changed = True

    if state_changed:
        save_state(state)

    # Apply throttling if needed
    decision = result.get("decision", {})

    # Show usage status if we polled
    if result.get("polled") and decision:
        five_hour = decision.get("five_hour", {})
        constrained = decision.get("constrained_window")

        if five_hour and constrained:
            util = five_hour.get("utilization", 0)
            target = five_hour.get("target", 0)
            overage = util - target

            print(
                f"[PACING] 5-hour usage: {util}% (target: {target:.1f}%, over by: {overage:.1f}%)",
                file=sys.stderr,
                flush=True,
            )

    if decision.get("should_throttle"):
        # Handle both cached decisions (direct delay_seconds) and fresh decisions (strategy dict)
        strategy = decision.get("strategy", {})

        # Check if this is a cached decision (has delay_seconds directly)
        if "delay_seconds" in decision and not strategy:
            delay = decision.get("delay_seconds", 0)
        else:
            # Fresh decision with strategy
            delay = strategy.get("delay_seconds", 0)

        # Always execute delay if delay > 0
        if delay > 0:
            execute_delay(delay)

    # Capture subagent reminder if conditions met (don't print yet)
    if should_inject_reminder(state, config, tool_name):
        pending_message = inject_subagent_reminder(config)

    # Save state (always save to persist counter)
    state_changed = True
    if state_changed:
        save_state(state, DEFAULT_STATE_PATH)

    # Print final message if any (code review takes priority over subagent nudge)
    if pending_message:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": pending_message,
            }
        }
        print(json.dumps(output), file=sys.stdout, flush=True)
        return False  # Not blocking feedback, just injecting context

    # Return whether feedback was provided
    return feedback_provided


def parse_user_prompt_input(raw_input: str) -> dict:
    """
    Parse user prompt input from Claude Code.

    Handles both JSON format and plain text fallback.

    Args:
        raw_input: Raw stdin input from Claude Code

    Returns:
        Dict with session_id and prompt keys
    """
    try:
        # Try to parse as JSON first
        hook_data = json.loads(raw_input)
        session_id = hook_data.get("session_id", f"sess-{int(time.time())}")
        prompt = hook_data.get("prompt", "")
        return {"session_id": session_id, "prompt": prompt}
    except json.JSONDecodeError:
        # Fallback to plain text - generate session ID
        return {
            "session_id": f"sess-{int(time.time())}",
            "prompt": raw_input.strip(),
        }


def run_user_prompt_submit():
    """Handle user prompt submit hook - pace-maker command interception only."""
    try:
        # Reset subagent counter on every new user prompt
        # This fixes orphaned state from ESC cancellations
        state = load_state(DEFAULT_STATE_PATH)
        state["subagent_counter"] = 0
        state["in_subagent"] = False
        save_state(state, DEFAULT_STATE_PATH)

        # Read user input from stdin
        raw_input = sys.stdin.read().strip()

        # Parse input (JSON or plain text)
        parsed_data = parse_user_prompt_input(raw_input)
        user_input = parsed_data["prompt"]

        # Handle pace-maker commands
        result = user_commands.handle_user_prompt(
            user_input, DEFAULT_CONFIG_PATH, DEFAULT_DB_PATH
        )

        if result["intercepted"]:
            # Command was intercepted - output JSON to block and display output
            response = {"decision": "block", "reason": result["output"]}
            print(json.dumps(response), file=sys.stdout, flush=True)
            sys.exit(0)

        # Pass through original input (no prompt storage needed)
        print(raw_input, file=sys.stdout, flush=True)
        sys.exit(0)

    except Exception as e:
        # Graceful degradation - log error and pass through
        print(f"[PACE-MAKER ERROR] {e}", file=sys.stderr)
        # Re-print original input on error
        try:
            sys.stdin.seek(0)
            print(sys.stdin.read(), file=sys.stdout, flush=True)
        except Exception:
            pass
        sys.exit(0)


def get_last_assistant_message(transcript_path: str) -> str:
    """
    Read JSONL transcript and extract ONLY the last assistant message.

    Args:
        transcript_path: Path to the JSONL transcript file

    Returns:
        Text from the last assistant message only
    """
    try:
        last_assistant_text = ""

        with open(transcript_path, "r") as f:
            for line in f:
                entry = json.loads(line)

                # Check if this is an assistant message
                message = entry.get("message", {})
                role = message.get("role")

                if role == "assistant":
                    # This is an assistant message - extract its text
                    content = message.get("content", [])
                    text_parts = []

                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text_parts.append(block.get("text", ""))
                    elif isinstance(content, str):
                        text_parts.append(content)

                    # Store this as the last assistant message (will be overwritten by next one)
                    if text_parts:
                        last_assistant_text = "\n".join(text_parts)

        return last_assistant_text

    except Exception as e:
        log_warning("hook", "Failed to read last assistant message", e)
        return ""


# Backwards compatibility alias for tests
read_conversation_from_transcript = get_last_assistant_message


def get_last_n_messages(transcript_path: str, n: int = 5) -> list:
    """
    Read JSONL transcript and extract the last N messages (user + assistant).

    Args:
        transcript_path: Path to the JSONL transcript file
        n: Number of messages to extract (default: 5)

    Returns:
        List of message texts (most recent last)
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

                text_parts = []
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                elif isinstance(content, str):
                    text_parts.append(content)

                if text_parts:
                    message_text = "\n".join(text_parts)
                    # Prefix with role for context
                    all_messages.append(f"[{role.upper()}]\n{message_text}")

        # Return last N messages
        return all_messages[-n:] if len(all_messages) >= n else all_messages

    except Exception as e:
        log_warning("hook", "Failed to read messages from transcript", e)
        return []


def should_run_tempo(config: dict, state: dict) -> bool:
    """
    Determine if tempo tracking should run based on global and session settings.

    Precedence logic:
    1. Check if session override exists (tempo_session_enabled in state)
    2. If session override exists, use that value
    3. Otherwise, use global setting (tempo_enabled in config)

    Args:
        config: Configuration dictionary with tempo_enabled
        state: State dictionary with tempo_session_enabled (optional)

    Returns:
        True if tempo should run, False otherwise
    """
    # Check for session override
    tempo_session_enabled = state.get("tempo_session_enabled")

    # If session override exists, use it
    if tempo_session_enabled is not None:
        return tempo_session_enabled

    # Otherwise, use global setting
    return config.get("tempo_enabled", True)


def run_stop_hook():
    """
    Handle Stop hook using intent-based validation.

    Refactored behavior:
    - Extracts first N user messages from transcript (original mission)
    - Extracts last N user messages from transcript (recent context)
    - Extracts last assistant message from transcript (what Claude just said)
    - Calls SDK to validate if Claude completed the user's original request
    - SDK acts as user proxy to judge completion
    - Returns APPROVED (allow exit) or BLOCKED with feedback

    Returns:
        Dictionary with Claude Code Stop hook schema:
        - {"continue": True} - Allow exit
        - {"decision": "block", "reason": "feedback"} - Block with feedback
    """

    try:
        # Load config and state to check if tempo should run
        config = load_config(DEFAULT_CONFIG_PATH)

        # Master switch - all features disabled
        if not config.get("enabled", True):
            return {"continue": True}

        state = load_state(DEFAULT_STATE_PATH)

        if not should_run_tempo(config, state):
            log_debug("hook", "Tempo disabled - allow exit")
            return {"continue": True}  # Tempo disabled - allow exit

        # Read hook data from stdin
        raw_input = sys.stdin.read()
        if not raw_input:
            log_debug("hook", "No raw input from stdin")
            return {"continue": True}

        hook_data = json.loads(raw_input)
        session_id = hook_data.get("session_id")
        transcript_path = hook_data.get("transcript_path")

        # Debug log
        log_debug("hook", "=== INTENT VALIDATION (Refactored) ===")
        log_debug("hook", f"Session ID: {session_id}")
        log_debug("hook", f"Transcript path: {transcript_path}")

        if not transcript_path or not os.path.exists(transcript_path):
            log_debug("hook", "No transcript - allow exit")
            return {"continue": True}

        # Use intent validator to check if work is complete
        from . import intent_validator

        conversation_context_size = config.get("conversation_context_size", 5)

        log_debug(
            "hook",
            f"Calling intent validator (context_size={conversation_context_size})...",
        )

        result = intent_validator.validate_intent(
            session_id=session_id,
            transcript_path=transcript_path,
            conversation_context_size=conversation_context_size,
        )

        log_debug("hook", f"Intent validation result: {result}")

        # Return validation result
        return result

    except Exception as e:
        # Graceful degradation - log error and allow exit
        print(f"[PACE-MAKER ERROR] Stop hook: {e}", file=sys.stderr)
        return {"continue": True}


def run_pre_tool_hook() -> Dict[str, Any]:
    """
    Pre-tool hook: Unified validation (intent + code review).

    Validates BEFORE tool execution:
    1. Intent was declared in last 2 messages
    2. Proposed code matches declared intent exactly
    3. No clean code violations

    Returns:
        {"continue": True} to allow, or
        {"decision": "block", "reason": "..."} to block
    """
    try:
        # 1. Read hook data from stdin
        raw_input = sys.stdin.read()
        if not raw_input:
            return {"continue": True}

        hook_data = json.loads(raw_input)

        # 2. Extract fields
        tool_name = hook_data.get("tool_name")
        tool_input = hook_data.get("tool_input", {})
        file_path = tool_input.get("file_path")
        transcript_path = hook_data.get("transcript_path")

        # 2a. Only validate Write/Edit tools
        if tool_name not in ["Write", "Edit"]:
            return {"continue": True}

        # Skip if no file_path (e.g., some edge cases)
        if not file_path:
            return {"continue": True}

        # 3. Load config
        config = load_config(DEFAULT_CONFIG_PATH)

        # Master switch - all features disabled
        if not config.get("enabled", True):
            return {"continue": True}

        # Check if feature enabled
        if not config.get("intent_validation_enabled", False):
            return {"continue": True}

        # 4. Check if source code file
        from . import extension_registry

        extensions = extension_registry.load_extensions(DEFAULT_EXTENSION_REGISTRY_PATH)
        is_source = extension_registry.is_source_code_file(file_path, extensions)

        if not is_source:
            return {"continue": True}  # Bypass non-source files

        # 5. Extract proposed code from tool_input
        if tool_name == "Write":
            proposed_code = tool_input.get("content", "")
        elif tool_name == "Edit":
            # For Edit, use new_string as the proposed code
            proposed_code = tool_input.get("new_string", "")
        else:
            return {"continue": True}

        # 6. Read last 4 messages for validation (includes user and assistant)
        messages = get_last_n_messages_for_validation(transcript_path, n=4)

        # 7. Call unified validation via SDK
        from . import intent_validator

        result = intent_validator.validate_intent_and_code(
            messages=messages,
            code=proposed_code,
            file_path=file_path,
            tool_name=tool_name,
        )

        # 8. Return result
        if result.get("approved", False):
            return {"continue": True}
        else:
            return {
                "decision": "block",
                "reason": result.get("feedback", "Validation failed"),
            }

    except Exception as e:
        # Graceful degradation - log error and allow
        print(f"[PACE-MAKER ERROR] Pre-tool hook: {e}", file=sys.stderr)
        return {"continue": True}


def main():
    """Entry point for hook script."""
    # Check if this is pre_tool_use hook
    if len(sys.argv) > 1 and sys.argv[1] == "pre_tool_use":
        result = run_pre_tool_hook()
        print(json.dumps(result), file=sys.stdout, flush=True)

        if result.get("decision") == "block":
            sys.exit(2)  # Block tool use
        else:
            sys.exit(0)  # Allow tool use

    # Check if this is stop hook
    if len(sys.argv) > 1 and sys.argv[1] == "stop":
        result = run_stop_hook()
        # Output JSON response
        print(json.dumps(result), file=sys.stdout, flush=True)

        # ========================================================================
        # CRITICAL: Exit code determines Claude Code behavior
        # ========================================================================
        # Exit code 2: Signals blocking error - Claude Code will show the
        #              "reason" message to Claude and force it to continue
        #              responding. Used with {"decision": "block", "reason": "..."}
        #
        # Exit code 0: Signals success - Claude Code allows normal exit.
        #              Used with {"continue": true}
        #
        # DO NOT change these exit codes or the hook will not work correctly.
        # ========================================================================
        if result.get("decision") == "block":
            sys.exit(2)  # Force continuation by signaling blocking error
        else:
            sys.exit(0)  # Allow normal exit

    # Check if this is user-prompt-submit hook
    if len(sys.argv) > 1 and sys.argv[1] == "user_prompt_submit":
        run_user_prompt_submit()
        return

    # Check if this is session-start hook
    if len(sys.argv) > 1 and sys.argv[1] == "session_start":
        try:
            run_session_start_hook()
        except Exception as e:
            print(f"[PACE-MAKER ERROR] SessionStart: {e}", file=sys.stderr)
        return

    # Check if this is subagent-start hook
    if len(sys.argv) > 1 and sys.argv[1] == "subagent_start":
        try:
            run_subagent_start_hook()
        except Exception as e:
            print(f"[PACE-MAKER ERROR] SubagentStart: {e}", file=sys.stderr)
        return

    # Check if this is subagent-stop hook
    if len(sys.argv) > 1 and sys.argv[1] == "subagent_stop":
        try:
            run_subagent_stop_hook()
        except Exception as e:
            print(f"[PACE-MAKER ERROR] SubagentStop: {e}", file=sys.stderr)
        return

    # Check if this is post-tool-use hook (explicit handling for clarity)
    if len(sys.argv) > 1 and sys.argv[1] == "post_tool_use":
        try:
            feedback_provided = run_hook()
            if feedback_provided:
                sys.exit(2)  # Show feedback to Claude
        except Exception as e:
            # Graceful degradation - log error but don't crash
            print(f"[PACE-MAKER ERROR] {e}", file=sys.stderr)
            # Continue execution without throttling
        return

    # Default fallback: treat as post-tool-use hook
    try:
        run_hook()
    except Exception as e:
        # Graceful degradation - log error but don't crash
        print(f"[PACE-MAKER ERROR] {e}", file=sys.stderr)
        # Continue execution without throttling


if __name__ == "__main__":
    main()
