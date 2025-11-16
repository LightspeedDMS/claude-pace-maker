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
                return data
    except Exception:
        pass

    # Generate new session ID
    return {
        "session_id": f"session-{int(time.time())}",
        "last_poll_time": None,
        "last_cleanup_time": None,
    }


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
        time.sleep(actual_delay)


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
    if decision.get("should_throttle"):
        strategy = decision.get("strategy", {})

        if strategy.get("method") == "direct":
            # Direct execution - sleep
            execute_delay(strategy["delay_seconds"])
        elif strategy.get("method") == "prompt":
            # Inject prompt
            inject_prompt_delay(strategy["prompt"])


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

        # Check for session start (implementation commands)
        run_session_start_hook(user_input)

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


def run_session_start_hook(user_input: str):
    """
    Handle session start hook - detect /implement-* commands.

    Args:
        user_input: User's input text
    """
    from . import lifecycle

    try:
        # Load config to check if tempo is enabled
        config = load_config(DEFAULT_CONFIG_PATH)
        if not config.get("tempo_enabled", True):
            return  # Tempo disabled - do nothing

        # Check if this is an implementation command
        if lifecycle.should_mark_implementation_start(user_input):
            # Mark implementation started
            lifecycle.mark_implementation_started(DEFAULT_STATE_PATH)
    except Exception as e:
        # Graceful degradation - log but don't crash
        print(f"[PACE-MAKER ERROR] Session start hook: {e}", file=sys.stderr)


def run_stop_hook():
    """
    Handle Stop hook - check for incomplete implementation.

    Returns:
        Dictionary with:
        - decision: "allow" or "block"
        - reason: Optional message if blocking
    """
    from . import lifecycle

    try:
        # Load config to check if tempo is enabled
        config = load_config(DEFAULT_CONFIG_PATH)
        if not config.get("tempo_enabled", True):
            return {"decision": "allow"}  # Tempo disabled - allow exit

        # Check if implementation started
        if not lifecycle.has_implementation_started(DEFAULT_STATE_PATH):
            return {"decision": "allow"}  # No implementation - allow exit

        # Check if implementation completed
        if lifecycle.has_implementation_completed(DEFAULT_STATE_PATH):
            return {"decision": "allow"}  # Implementation complete - allow exit

        # Check prompt count for infinite loop prevention
        prompt_count = lifecycle.get_stop_hook_prompt_count(DEFAULT_STATE_PATH)
        if prompt_count > 0:
            # Already prompted once - allow exit to prevent loop
            return {"decision": "allow"}

        # Increment prompt count
        lifecycle.increment_stop_hook_prompt_count(DEFAULT_STATE_PATH)

        # Implementation incomplete - block and prompt
        prompt = (
            "You started an implementation but haven't declared IMPLEMENTATION_COMPLETE. "
            "If all tasks are done, respond with exactly 'IMPLEMENTATION_COMPLETE' (nothing else). "
            "If not done, continue working."
        )
        print(prompt, file=sys.stdout, flush=True)

        return {"decision": "block", "reason": prompt}

    except Exception as e:
        # Graceful degradation - log error and allow exit
        print(f"[PACE-MAKER ERROR] Stop hook: {e}", file=sys.stderr)
        return {"decision": "allow"}


def main():
    """Entry point for hook script."""
    # Check if this is stop hook
    if len(sys.argv) > 1 and sys.argv[1] == "stop":
        result = run_stop_hook()
        # Output JSON response
        print(json.dumps(result), file=sys.stdout, flush=True)
        sys.exit(0 if result["decision"] == "allow" else 1)

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
