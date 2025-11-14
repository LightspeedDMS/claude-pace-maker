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


# Configuration
DEFAULT_DB_PATH = str(Path.home() / ".claude-pace-maker" / "usage.db")
DEFAULT_CONFIG_PATH = str(Path.home() / ".claude-pace-maker" / "config.json")
DEFAULT_STATE_PATH = str(Path.home() / ".claude-pace-maker" / "state.json")

DEFAULT_CONFIG = {
    "enabled": True,
    "base_delay": 5,
    "max_delay": 120,
    "threshold_percent": 10,
    "poll_interval": 60
}


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
                if data.get('last_poll_time'):
                    data['last_poll_time'] = datetime.fromisoformat(data['last_poll_time'])
                if data.get('last_cleanup_time'):
                    data['last_cleanup_time'] = datetime.fromisoformat(data['last_cleanup_time'])
                return data
    except Exception:
        pass

    # Generate new session ID
    return {
        'session_id': f"session-{int(time.time())}",
        'last_poll_time': None,
        'last_cleanup_time': None
    }


def save_state(state: dict, state_path: str = DEFAULT_STATE_PATH):
    """Save hook state for next invocation."""
    try:
        # Ensure directory exists
        Path(state_path).parent.mkdir(parents=True, exist_ok=True)

        # Convert datetime to string for JSON serialization
        state_copy = state.copy()
        if isinstance(state_copy.get('last_poll_time'), datetime):
            state_copy['last_poll_time'] = state_copy['last_poll_time'].isoformat()
        if isinstance(state_copy.get('last_cleanup_time'), datetime):
            state_copy['last_cleanup_time'] = state_copy['last_cleanup_time'].isoformat()

        with open(state_path, 'w') as f:
            json.dump(state_copy, f)
    except Exception:
        pass  # Graceful degradation


def execute_delay(delay_seconds: int):
    """Execute direct delay (sleep)."""
    if delay_seconds > 0:
        time.sleep(delay_seconds)


def inject_prompt_delay(prompt: str):
    """Inject prompt for Claude to wait."""
    # Print to stdout so Claude sees it
    print(prompt, file=sys.stdout, flush=True)


def run_hook():
    """Main hook execution."""
    # Load configuration
    config = load_config()

    # Check if enabled
    if not config.get('enabled', True):
        return  # Disabled - do nothing

    # Load state
    state = load_state()

    # Ensure database is initialized
    db_path = DEFAULT_DB_PATH
    database.initialize_database(db_path)

    # Run pacing check
    result = pacing_engine.run_pacing_check(
        db_path=db_path,
        session_id=state['session_id'],
        last_poll_time=state.get('last_poll_time'),
        poll_interval=config.get('poll_interval', 60),
        last_cleanup_time=state.get('last_cleanup_time')
    )

    # Update state if polled or cleaned up
    state_changed = False
    if result.get('polled'):
        state['last_poll_time'] = result.get('poll_time')
        state_changed = True
    if result.get('cleanup_time'):
        state['last_cleanup_time'] = result.get('cleanup_time')
        state_changed = True

    if state_changed:
        save_state(state)

    # Apply throttling if needed
    decision = result.get('decision', {})
    if decision.get('should_throttle'):
        strategy = decision.get('strategy', {})

        if strategy.get('method') == 'direct':
            # Direct execution - sleep
            execute_delay(strategy['delay_seconds'])
        elif strategy.get('method') == 'prompt':
            # Inject prompt
            inject_prompt_delay(strategy['prompt'])


def run_user_prompt_submit():
    """Handle user prompt submit hook."""
    try:
        # Read user input from stdin
        raw_input = sys.stdin.read().strip()

        # Parse JSON input from Claude Code
        try:
            hook_data = json.loads(raw_input)
            user_input = hook_data.get('prompt', '')
        except json.JSONDecodeError:
            # Fallback to treating as plain text if not JSON
            user_input = raw_input

        # Handle the prompt
        result = user_commands.handle_user_prompt(
            user_input,
            DEFAULT_CONFIG_PATH,
            DEFAULT_DB_PATH
        )

        if result['intercepted']:
            # Command was intercepted - output JSON to block and display output
            response = {
                "decision": "block",
                "reason": result['output']
            }
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
        except:
            pass
        sys.exit(0)


def main():
    """Entry point for hook script."""
    # Check if this is user-prompt-submit hook
    if len(sys.argv) > 1 and sys.argv[1] == 'user_prompt_submit':
        run_user_prompt_submit()
        return

    # Otherwise, run post-tool-use hook
    try:
        run_hook()
    except Exception as e:
        # Graceful degradation - log error but don't crash
        print(f"[PACE-MAKER ERROR] {e}", file=sys.stderr)
        # Continue execution without throttling


if __name__ == '__main__':
    main()
