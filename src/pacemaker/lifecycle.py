#!/usr/bin/env python3
"""
Session lifecycle tracking module for Pace Maker.

Manages IMPLEMENTATION_START and IMPLEMENTATION_COMPLETE markers
to prevent Claude from prematurely ending implementation sessions.
"""

import os
import json
from pathlib import Path


# Reminder text injected to Claude at session start
SESSION_COMPLETION_PROTOCOL = """
SESSION COMPLETION PROTOCOL:

Before ending ANY session, you must declare completion:

1. For implementation work:
   - Say exactly: IMPLEMENTATION_COMPLETE
   - Only after all code is working and tested

2. For conversations/discussions/research:
   - Say exactly: EXCHANGE_COMPLETE
   - Only after all questions answered and objectives met

These markers prevent premature session termination.
""".strip()

# Backwards compatibility alias
IMPLEMENTATION_REMINDER_TEXT = SESSION_COMPLETION_PROTOCOL


def get_stop_hook_prompt_count(state_path: str) -> int:
    """
    Get the number of times Stop hook has prompted in this session.

    Args:
        state_path: Path to state.json file

    Returns:
        Number of prompts (0 if not tracked)
    """
    try:
        if not os.path.exists(state_path):
            return 0

        with open(state_path) as f:
            state = json.load(f)

        return state.get("stop_hook_prompt_count", 0)
    except Exception:
        return 0


def increment_stop_hook_prompt_count(state_path: str):
    """
    Increment the Stop hook prompt counter.

    Args:
        state_path: Path to state.json file
    """
    try:
        # Load existing state
        state = {}
        if os.path.exists(state_path):
            with open(state_path) as f:
                state = json.load(f)

        # Increment counter
        current_count = state.get("stop_hook_prompt_count", 0)
        state["stop_hook_prompt_count"] = current_count + 1

        # Ensure directory exists
        Path(state_path).parent.mkdir(parents=True, exist_ok=True)

        # Write state
        with open(state_path, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        # Graceful degradation - log but don't crash
        print(f"[PACE-MAKER] Error incrementing stop hook prompt count: {e}")
