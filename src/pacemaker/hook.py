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
from .transcript_reader import get_last_n_assistant_messages


def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    """Load configuration from file."""
    try:
        if os.path.exists(config_path):
            with open(config_path) as f:
                return json.load(f)
    except Exception:
        pass

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
    except Exception:
        pass

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
    except Exception:
        pass  # Graceful degradation


def execute_delay(delay_seconds: int):
    """Execute direct delay (sleep)."""
    if delay_seconds > 0:
        # Cap at MAX_DELAY_SECONDS (360s timeout - 10s safety margin)
        actual_delay = min(delay_seconds, MAX_DELAY_SECONDS)
        print(
            f"[PACING] Sleeping for {actual_delay} seconds...",
            file=sys.stderr,
            flush=True,
        )
        time.sleep(actual_delay)
        print("[PACING] Sleep complete", file=sys.stderr, flush=True)


def inject_prompt_delay(prompt: str):
    """Inject prompt for Claude to wait."""
    # Print to stdout so Claude sees it
    print(prompt, file=sys.stdout, flush=True)


def run_session_start_hook():
    """
    Handle SessionStart hook - beginning of new session.

    Resets subagent_counter to 0 and in_subagent to false to ensure clean state.
    This prevents state corruption from cancelled subagents.

    Also displays intent validation mandate if feature is enabled.
    """
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
        config = load_config(DEFAULT_CONFIG_PATH)
        if config.get("intent_validation_enabled", False):
            print("\n" + "=" * 70)
            print("⚠️  INTENT VALIDATION ENABLED")
            print("=" * 70)
            print(
                "\nBefore modifying code files, you MUST declare your intent explicitly:"
            )
            print("\nDeclare EXACTLY these 3 components:")
            print("  1. FILE: Which file you're modifying")
            print("  2. CHANGES: What specific changes you're making")
            print("  3. GOAL: Why you're making these changes")
            print("\nGOOD Example:")
            print("  'I will modify src/auth.py to add a validate_token() function")
            print("   that checks JWT expiration, to fix the security vulnerability.'")
            print("\nBAD Examples:")
            print("  ✗ 'Fixing auth bug' - Missing file and specifics")
            print("  ✗ 'Updating code' - Too vague")
            print("\nDeclare intent in the SAME message as the Write/Edit tool call.")
            print("=" * 70 + "\n")
    except Exception:
        # Fail silently - don't break session start
        pass


def run_subagent_start_hook():
    """
    Handle SubagentStart hook - entering subagent context.

    Increments subagent_counter and sets in_subagent flag based on counter.
    Does NOT reset tool_execution_count (global counter persists).
    """
    # Load state
    state = load_state(DEFAULT_STATE_PATH)

    # Increment counter
    state["subagent_counter"] = state.get("subagent_counter", 0) + 1

    # Set flag based on counter
    state["in_subagent"] = state["subagent_counter"] > 0

    # Save state
    save_state(state, DEFAULT_STATE_PATH)


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


def inject_subagent_reminder(config: dict):
    """
    Inject reminder as JSON to stdout with block decision.

    This ensures Claude sees the reminder by using Claude Code's
    hook response format. The "block" decision doesn't prevent tool
    execution (tool already ran), but makes the reminder visible.

    Args:
        config: Configuration dictionary
    """
    message = config.get(
        "subagent_reminder_message",
        "💡 Consider using the Task tool to delegate work to specialized subagents (per your guidelines)",
    )

    # Output JSON format that Claude Code will show to Claude
    reminder_output = {"decision": "block", "reason": message}

    print(json.dumps(reminder_output), file=sys.stdout, flush=True)


def run_hook():
    """Main hook execution with pacing AND code review."""

    # Load configuration
    config = load_config(DEFAULT_CONFIG_PATH)

    # Check if enabled
    if not config.get("enabled", True):
        return  # Disabled - do nothing

    # Read hook data from stdin to get tool_name
    tool_name = None
    hook_data = None
    try:
        raw_input = sys.stdin.read()
        if raw_input:
            hook_data = json.loads(raw_input)
            tool_name = hook_data.get("tool_name")
    except (json.JSONDecodeError, Exception):
        # Graceful degradation - continue without tool_name
        pass

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
            method = "direct"  # Cached decisions always use direct sleep
        else:
            # Fresh decision with strategy
            delay = strategy.get("delay_seconds", 0)
            method = strategy.get("method", "direct")

        print(
            f"[PACING] Throttling for {delay}s (method={method}, cached={result.get('cached', False)})",
            file=sys.stderr,
            flush=True,
        )

        # Always execute delay if delay > 0
        if delay > 0:
            execute_delay(delay)
    else:
        print("[PACING] No throttling needed", file=sys.stderr, flush=True)

    # Inject subagent reminder if conditions met
    if should_inject_reminder(state, config, tool_name):
        # Debug logging
        reason = (
            "Write tool used"
            if tool_name == "Write"
            else f"count {state['tool_execution_count']}"
        )
        print(
            f"[PACING] DEBUG: Injecting reminder ({reason})",
            file=sys.stderr,
            flush=True,
        )
        inject_subagent_reminder(config)
        print("[PACING] DEBUG: Reminder injected", file=sys.stderr, flush=True)

    # =========================================================================
    # NEW: Post-tool code review validation (Phase 5)
    # =========================================================================
    debug_log_path = os.path.join(
        os.path.dirname(DEFAULT_CONFIG_PATH), "post_tool_debug.log"
    )
    try:
        with open(debug_log_path, "a") as debug_f:
            debug_f.write(f"\n[{datetime.now()}] Post-tool hook called\n")
            debug_f.write(
                f"  intent_validation_enabled: {config.get('intent_validation_enabled', False)}\n"
            )
            debug_f.write(f"  hook_data exists: {hook_data is not None}\n")
            debug_f.write(f"  tool_name: {tool_name}\n")

        if config.get("intent_validation_enabled", False) and hook_data:
            if tool_name in ["Write", "Edit"]:
                tool_input = hook_data.get("tool_input", {})
                file_path = tool_input.get("file_path")
                transcript_path = hook_data.get("transcript_path")

                with open(debug_log_path, "a") as debug_f:
                    debug_f.write(f"  file_path: {file_path}\n")
                    debug_f.write(f"  transcript_path: {transcript_path}\n")

                if file_path and transcript_path:
                    # Check if source code file
                    from . import extension_registry, code_reviewer

                    extensions = extension_registry.load_extensions(
                        DEFAULT_EXTENSION_REGISTRY_PATH
                    )

                    is_source = extension_registry.is_source_code_file(
                        file_path, extensions
                    )
                    with open(debug_log_path, "a") as debug_f:
                        debug_f.write(f"  is_source_code_file: {is_source}\n")

                    if is_source:
                        # Validate code against intent
                        messages = get_last_n_assistant_messages(transcript_path, n=10)

                        with open(debug_log_path, "a") as debug_f:
                            debug_f.write(f"  Retrieved {len(messages)} messages\n")
                            debug_f.write(
                                "  Calling code_reviewer.validate_code_against_intent()...\n"
                            )

                        feedback = code_reviewer.validate_code_against_intent(
                            file_path, messages
                        )

                        with open(debug_log_path, "a") as debug_f:
                            debug_f.write(f"  Feedback: {feedback}\n")

                        if feedback:
                            # Print feedback to stdout so Claude sees it
                            print(feedback, file=sys.stdout, flush=True)
                            with open(debug_log_path, "a") as debug_f:
                                debug_f.write("  Feedback printed to stdout\n")
    except Exception as e:
        # Log error but don't break pacing functionality
        with open(debug_log_path, "a") as debug_f:
            debug_f.write(f"  ERROR: {e}\n")
            import traceback

            debug_f.write(f"  Traceback: {traceback.format_exc()}\n")

    # Save state (always save to persist counter)
    state_changed = True
    if state_changed:
        save_state(state, DEFAULT_STATE_PATH)


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

    except Exception:
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

    except Exception:
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

    # Debug log path
    debug_log = os.path.join(
        os.path.dirname(DEFAULT_CONFIG_PATH), "stop_hook_debug.log"
    )

    try:
        # Load config and state to check if tempo should run
        config = load_config(DEFAULT_CONFIG_PATH)
        state = load_state(DEFAULT_STATE_PATH)

        if not should_run_tempo(config, state):
            with open(debug_log, "a") as f:
                f.write(f"\n[{datetime.now()}] Tempo disabled - allow exit\n")
            return {"continue": True}  # Tempo disabled - allow exit

        # Read hook data from stdin
        raw_input = sys.stdin.read()
        if not raw_input:
            with open(debug_log, "a") as f:
                f.write(f"\n[{datetime.now()}] No raw input from stdin\n")
            return {"continue": True}

        hook_data = json.loads(raw_input)
        session_id = hook_data.get("session_id")
        transcript_path = hook_data.get("transcript_path")

        # Debug log
        with open(debug_log, "a") as f:
            f.write(f"\n[{datetime.now()}] === INTENT VALIDATION (Refactored) ===\n")
            f.write(f"Session ID: {session_id}\n")
            f.write(f"Transcript path: {transcript_path}\n")

        if not transcript_path or not os.path.exists(transcript_path):
            with open(debug_log, "a") as f:
                f.write("No transcript - allow exit\n")
            return {"continue": True}

        # Use intent validator to check if work is complete
        from . import intent_validator

        conversation_context_size = config.get("conversation_context_size", 5)

        with open(debug_log, "a") as f:
            f.write(
                f"Calling intent validator (context_size={conversation_context_size})...\n"
            )

        result = intent_validator.validate_intent(
            session_id=session_id,
            transcript_path=transcript_path,
            conversation_context_size=conversation_context_size,
        )

        with open(debug_log, "a") as f:
            f.write(f"Intent validation result: {result}\n")

        # Return validation result
        return result

    except Exception as e:
        # Graceful degradation - log error and allow exit
        print(f"[PACE-MAKER ERROR] Stop hook: {e}", file=sys.stderr)
        return {"continue": True}


def run_pre_tool_hook() -> Dict[str, Any]:
    """
    Pre-tool hook: Validate intent before Write/Edit on source code files.

    Reads stdin for hook data:
    {
      "session_id": "...",
      "transcript_path": "/path/to/conversation.jsonl",
      "tool_name": "Write",
      "tool_input": {
        "file_path": "/path/to/file.py",
        "content": "..."
      }
    }

    Returns:
      {"continue": True} - Allow tool use
      {"decision": "block", "reason": "..."} - Block tool use

    Exit code 0: Allow, Exit code 2: Block
    """
    try:
        # 1. Read JSON from stdin
        raw_input = sys.stdin.read()
        hook_data = json.loads(raw_input)

        # 2. Extract fields
        tool_name = hook_data.get("tool_name")
        tool_input = hook_data.get("tool_input", {})
        file_path = tool_input.get("file_path")
        transcript_path = hook_data.get("transcript_path")

        # 2a. Only validate Write/Edit tools (defense against query filter bugs)
        if tool_name not in ["Write", "Edit"]:
            return {"continue": True}

        # 3. Load config
        config = load_config(DEFAULT_CONFIG_PATH)

        # 4. Check if feature enabled
        if not config.get("intent_validation_enabled", False):
            return {"continue": True}

        # 5. Check if source code file
        from . import extension_registry

        # Skip if no file_path (e.g., Bash tool)
        if not file_path:
            return {"continue": True}

        extensions = extension_registry.load_extensions(DEFAULT_EXTENSION_REGISTRY_PATH)
        if not extension_registry.is_source_code_file(file_path, extensions):
            return {"continue": True}

        # 6. Read last 10 assistant messages (include context + message with tool usage)
        messages = get_last_n_assistant_messages(transcript_path, n=10)

        # 7. Validate intent declared
        from .intent_validator import validate_intent_declared

        result = validate_intent_declared(messages, file_path, tool_name)

        # 8. Block if no intent
        if not result["intent_found"]:
            return {
                "decision": "block",
                "reason": """⛔ Intent declaration required

You must declare your intent IN THIS SAME MESSAGE before using Write/Edit tools.

Required format - include ALL 3 components:
  1. FILE: Which file you're modifying
  2. CHANGES: What specific changes you're making
  3. GOAL: Why you're making these changes

Example:
  "I will modify src/database.py to add a connect_to_db() function
   that handles connection pooling, to improve performance."

Then use your Write/Edit tool in the same message.""",
            }

        return {"continue": True}

    except Exception as e:
        # Fail open on any error - graceful degradation
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
            run_hook()
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
