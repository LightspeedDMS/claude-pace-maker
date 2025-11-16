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
IMPLEMENTATION_REMINDER_TEXT = """
IMPLEMENTATION LIFECYCLE PROTOCOL:

When doing implementation work:
1. Before starting ANY implementation work, say exactly: "IMPLEMENTATION_START"
2. When ALL tasks are 100% complete (code + tests + manual validation all passed), say exactly: "IMPLEMENTATION_COMPLETE"
3. Never say IMPLEMENTATION_COMPLETE unless everything is truly done.

These markers prevent premature session termination.
""".strip()


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
