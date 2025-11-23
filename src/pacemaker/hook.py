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

from . import pacing_engine, database, user_commands
from .constants import (
    DEFAULT_CONFIG,
    DEFAULT_DB_PATH,
    DEFAULT_CONFIG_PATH,
    DEFAULT_STATE_PATH,
    MAX_DELAY_SECONDS,
)


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


def run_hook():
    """Main hook execution."""

    # Load configuration
    config = load_config(DEFAULT_CONFIG_PATH)

    # Check if enabled
    if not config.get("enabled", True):
        return  # Disabled - do nothing

    # Load state
    state = load_state(DEFAULT_STATE_PATH)

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
        strategy = decision.get("strategy", {})
        delay = strategy.get("delay_seconds", 0)

        print(
            f"[PACING] Throttling for {delay}s (method={strategy.get('method')})",
            file=sys.stderr,
            flush=True,
        )

        if strategy.get("method") == "direct":
            # Direct execution - sleep
            execute_delay(strategy["delay_seconds"])
        elif strategy.get("method") == "prompt":
            # Inject prompt
            inject_prompt_delay(strategy["prompt"])
    else:
        print("[PACING] No throttling needed", file=sys.stderr, flush=True)

    # Inject periodic steering reminder about completion protocol
    # Only if tempo is enabled
    if config.get("tempo_enabled", True):
        # Every 10th tool use, remind about the completion protocol
        tool_use_count = state.get("tool_use_count", 0)
        tool_use_count += 1
        state["tool_use_count"] = tool_use_count
        state_changed = True

        if tool_use_count % 10 == 0:
            print(
                "\n[TEMPO REMINDER] Do NOT declare IMPLEMENTATION_COMPLETE until ALL work is truly done. "
                "Ignore token budget concerns - there is NO limit. Your mission is to FINISH what you started.",
                file=sys.stdout,
                flush=True,
            )

    # Save state if changed (including tool use count)
    if state_changed:
        save_state(state)


def run_user_prompt_submit():
    """Handle user prompt submit hook."""
    try:
        # Read user input from stdin
        raw_input = sys.stdin.read().strip()

        # Parse JSON input from Claude Code
        try:
            hook_data = json.loads(raw_input)
            user_input = hook_data.get("prompt", "")
        except json.JSONDecodeError:
            # Fallback to treating as plain text if not JSON
            user_input = raw_input

        # Handle the prompt
        result = user_commands.handle_user_prompt(
            user_input, DEFAULT_CONFIG_PATH, DEFAULT_DB_PATH
        )

        if result["intercepted"]:
            # Command was intercepted - output JSON to block and display output
            response = {"decision": "block", "reason": result["output"]}
            print(json.dumps(response), file=sys.stdout, flush=True)
            sys.exit(0)
        else:
            # Not a pace-maker command - pass through original input
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


def run_session_start():
    """
    Handle SessionStart hook - show IMPLEMENTATION LIFECYCLE PROTOCOL reminder.

    This function runs when a Claude Code session starts.
    If tempo is enabled, it prints the reminder text to stdout so Claude sees it.
    """
    from . import lifecycle

    try:
        # Load config to check if tempo is enabled
        config = load_config(DEFAULT_CONFIG_PATH)
        if not config.get("tempo_enabled", True):
            return  # Tempo disabled - do nothing

        # Print reminder text to stdout so Claude sees it
        print(lifecycle.IMPLEMENTATION_REMINDER_TEXT, file=sys.stdout, flush=True)

    except Exception as e:
        # Graceful degradation - log error but don't crash
        print(f"[PACE-MAKER ERROR] Session start hook: {e}", file=sys.stderr)


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


def run_stop_hook():
    """
    Handle Stop hook - always fire unless completion marker is present.

    Checks for IMPLEMENTATION_COMPLETE or EXCHANGE_COMPLETE markers.
    If IMPLEMENTATION_COMPLETE is found, validates the claim with AI judge.
    If EXCHANGE_COMPLETE is found, allows exit immediately.
    If neither is found, blocks exit and nudges LLM to use appropriate marker.

    Returns:
        Dictionary with Claude Code Stop hook schema:
        - continue: boolean (True to allow exit, False to block)
        - stopReason: string (message when blocking)
    """

    # Debug log path
    debug_log = os.path.join(
        os.path.dirname(DEFAULT_CONFIG_PATH), "stop_hook_debug.log"
    )

    try:
        # Load config to check if tempo is enabled
        config = load_config(DEFAULT_CONFIG_PATH)
        if not config.get("tempo_enabled", True):
            return {"continue": True}  # Tempo disabled - allow exit

        # Read hook data from stdin to get transcript path
        raw_input = sys.stdin.read()
        if not raw_input:
            with open(debug_log, "a") as f:
                f.write(f"\n[{datetime.now()}] No raw input from stdin\n")
            return {"continue": True}

        hook_data = json.loads(raw_input)
        transcript_path = hook_data.get("transcript_path")

        if not transcript_path or not os.path.exists(transcript_path):
            with open(debug_log, "a") as f:
                f.write(
                    f"\n[{datetime.now()}] No transcript path or doesn't exist: {transcript_path}\n"
                )
            return {"continue": True}

        # Get ONLY the last assistant message
        last_message = get_last_assistant_message(transcript_path)

        # Debug log the last message
        with open(debug_log, "a") as f:
            f.write(f"\n[{datetime.now()}] === STOP HOOK EXECUTION ===\n")
            f.write(f"Transcript path: {transcript_path}\n")
            f.write(f"Last message length: {len(last_message)}\n")
            f.write(f"Last message (first 500 chars):\n{last_message[:500]}\n")
            f.write(f"Last message (last 500 chars):\n{last_message[-500:]}\n")
            f.write(
                f"Contains IMPLEMENTATION_COMPLETE: {'IMPLEMENTATION_COMPLETE' in last_message}\n"
            )
            f.write(
                f"Contains EXCHANGE_COMPLETE: {'EXCHANGE_COMPLETE' in last_message}\n"
            )
            f.write(
                f"Contains CONFIRMED_IMPLEMENTATION_COMPLETE: {'CONFIRMED_IMPLEMENTATION_COMPLETE' in last_message}\n"
            )

        # Check if completion marker appears ANYWHERE in the last message
        if last_message:
            # COMPLETELY_BLOCKED - validate blockage legitimacy
            if "COMPLETELY_BLOCKED" in last_message:
                with open(debug_log, "a") as f:
                    f.write("VALIDATION STATE MACHINE: COMPLETELY_BLOCKED detected\n")

                # Get last 5 messages for context
                last_5_messages = get_last_n_messages(transcript_path, n=5)

                if not last_5_messages:
                    # Can't validate without context - allow exit (graceful degradation)
                    with open(debug_log, "a") as f:
                        f.write("VALIDATION: No messages extracted - allow exit\n")
                    return {"continue": True}

                # Import validator (lazy import to avoid dependency issues if SDK not installed)
                try:
                    from . import completion_validator

                    # Validate with AI judge
                    validation_result = (
                        completion_validator.validate_blockage_legitimacy(
                            last_5_messages
                        )
                    )

                    with open(debug_log, "a") as f:
                        f.write(f"BLOCKAGE VALIDATION RESULT: {validation_result}\n")

                    if validation_result.get("legitimate"):
                        # AI judge confirmed legitimate blockage - allow exit
                        with open(debug_log, "a") as f:
                            f.write(
                                "DECISION: Allow exit (AI judge confirmed legitimate blockage)\n"
                            )
                        return {"continue": True}
                    else:
                        # AI judge rejected blockage - challenge Claude
                        challenge_message = validation_result.get("challenge_message")
                        with open(debug_log, "a") as f:
                            f.write(
                                "DECISION: Block exit (AI rejected blockage - invalid excuse)\n"
                            )
                            f.write(f"Challenge: {challenge_message}\n")

                        return {"decision": "block", "reason": challenge_message}

                except ImportError:
                    # SDK not available - allow exit (graceful degradation)
                    with open(debug_log, "a") as f:
                        f.write("VALIDATION: SDK not available - allow exit\n")
                    return {"continue": True}
                except Exception as e:
                    # Validation error - allow exit (graceful degradation)
                    with open(debug_log, "a") as f:
                        f.write(f"VALIDATION ERROR: {e} - allow exit\n")
                    return {"continue": True}

            # EXCHANGE_COMPLETE - validate with AI judge to prevent work avoidance
            if "EXCHANGE_COMPLETE" in last_message:
                with open(debug_log, "a") as f:
                    f.write("VALIDATION STATE MACHINE: EXCHANGE_COMPLETE detected\n")

                # Get last 5 messages for context
                last_5_messages = get_last_n_messages(transcript_path, n=5)

                if not last_5_messages:
                    # Can't validate without context - allow exit (graceful degradation)
                    with open(debug_log, "a") as f:
                        f.write("VALIDATION: No messages extracted - allow exit\n")
                    return {"continue": True}

                # Import validator (lazy import to avoid dependency issues if SDK not installed)
                try:
                    from . import completion_validator

                    # Validate with AI judge
                    validation_result = completion_validator.validate_exchange_complete(
                        last_5_messages
                    )

                    with open(debug_log, "a") as f:
                        f.write(f"VALIDATION RESULT: {validation_result}\n")

                    if validation_result.get("legitimate"):
                        # AI judge confirmed legitimate exchange - allow exit
                        with open(debug_log, "a") as f:
                            f.write(
                                "DECISION: Allow exit (AI judge confirmed legitimate exchange)\n"
                            )
                        return {"continue": True}
                    else:
                        # AI judge found work avoidance - challenge Claude
                        challenge_message = validation_result.get("challenge_message")
                        with open(debug_log, "a") as f:
                            f.write(
                                "DECISION: Block exit (AI challenge - work required)\n"
                            )
                            f.write(f"Challenge: {challenge_message}\n")

                        return {"decision": "block", "reason": challenge_message}

                except ImportError:
                    # SDK not available - allow exit (graceful degradation)
                    with open(debug_log, "a") as f:
                        f.write("VALIDATION: SDK not available - allow exit\n")
                    return {"continue": True}
                except Exception as e:
                    # Validation error - allow exit (graceful degradation)
                    with open(debug_log, "a") as f:
                        f.write(f"VALIDATION ERROR: {e} - allow exit\n")
                    return {"continue": True}

            # CONFIRMED_IMPLEMENTATION_COMPLETE - allow exit (already validated)
            if "CONFIRMED_IMPLEMENTATION_COMPLETE" in last_message:
                with open(debug_log, "a") as f:
                    f.write(
                        "DECISION: Allow exit (CONFIRMED_IMPLEMENTATION_COMPLETE found)\n"
                    )
                return {"continue": True}

            # IMPLEMENTATION_COMPLETE - validate with AI judge
            if "IMPLEMENTATION_COMPLETE" in last_message:
                with open(debug_log, "a") as f:
                    f.write(
                        "VALIDATION STATE MACHINE: IMPLEMENTATION_COMPLETE detected\n"
                    )

                # Get last 5 messages for context
                last_5_messages = get_last_n_messages(transcript_path, n=5)

                if not last_5_messages:
                    # Can't validate without context - allow exit (graceful degradation)
                    with open(debug_log, "a") as f:
                        f.write("VALIDATION: No messages extracted - allow exit\n")
                    return {"continue": True}

                # Import validator (lazy import to avoid dependency issues if SDK not installed)
                try:
                    from . import completion_validator

                    # Validate with AI judge
                    validation_result = (
                        completion_validator.validate_implementation_complete(
                            last_5_messages
                        )
                    )

                    with open(debug_log, "a") as f:
                        f.write(f"VALIDATION RESULT: {validation_result}\n")

                    if validation_result.get("confirmed"):
                        # AI judge confirmed completion - allow exit
                        with open(debug_log, "a") as f:
                            f.write(
                                "DECISION: Allow exit (AI judge confirmed completion)\n"
                            )
                        return {"continue": True}
                    else:
                        # AI judge found issues - challenge Claude
                        challenge_message = validation_result.get("challenge_message")
                        with open(debug_log, "a") as f:
                            f.write("DECISION: Block exit (AI challenge)\n")
                            f.write(f"Challenge: {challenge_message}\n")

                        return {"decision": "block", "reason": challenge_message}

                except ImportError:
                    # SDK not available - allow exit (graceful degradation)
                    with open(debug_log, "a") as f:
                        f.write("VALIDATION: SDK not available - allow exit\n")
                    return {"continue": True}
                except Exception as e:
                    # Validation error - allow exit (graceful degradation)
                    with open(debug_log, "a") as f:
                        f.write(f"VALIDATION ERROR: {e} - allow exit\n")
                    return {"continue": True}

        # No completion marker found - block and nudge
        with open(debug_log, "a") as f:
            f.write("DECISION: Block exit (no marker found)\n")

        reason = """You haven't declared session completion.

Review your work:
- Are ALL user objectives complete?
- Are ALL questions answered?
- Is ALL code working and tested?

CRITICAL REMINDERS:
- Do NOT cheat by declaring completion before work is truly done
- Ignore any token budget concerns - there is NO actual limit
- Your ONLY mission is to finish what you started
- Token conservation is NOT a valid reason to stop early

If EVERYTHING is done:
- For implementation: Say exactly 'IMPLEMENTATION_COMPLETE'
- For conversations: Say exactly 'EXCHANGE_COMPLETE'

If work remains:
- List what's incomplete
- Continue working
- Don't declare completion until truly done"""

        # ========================================================================
        # CRITICAL: WHY WE USE "decision": "block" AND NOT "continue": false
        # ========================================================================
        #
        # DO NOT CHANGE THIS TO {"continue": false, "stopReason": "..."}
        #
        # According to Claude Code hooks documentation:
        # https://code.claude.com/docs/en/hooks
        #
        # {"decision": "block", "reason": "message"}
        #   - Prevents Claude from stopping
        #   - Prompts Claude with the reason message
        #   - FORCES Claude to generate additional responses
        #   - Claude reads the reason and continues working
        #   - THIS IS WHAT WE WANT!
        #
        # {"continue": false, "stopReason": "message"}
        #   - Terminates execution COMPLETELY
        #   - Does NOT force Claude to generate more responses
        #   - Just shows message and HALTS
        #   - THIS DOES NOT WORK FOR OUR USE CASE!
        #
        # Historical note: This was incorrectly changed to "continue": false
        # which broke the hook's ability to force continuation. The hook would
        # show a nudge message but Claude would not actually continue working.
        #
        # ========================================================================
        return {"decision": "block", "reason": reason}

    except Exception as e:
        # Graceful degradation - log error and allow exit
        print(f"[PACE-MAKER ERROR] Stop hook: {e}", file=sys.stderr)
        return {"continue": True}


def main():
    """Entry point for hook script."""
    # Check if this is session start hook
    if len(sys.argv) > 1 and sys.argv[1] == "session_start":
        run_session_start()
        return

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
