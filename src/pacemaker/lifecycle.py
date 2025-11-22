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
   - CRITICAL: Do NOT cheat by saying IMPLEMENTATION_COMPLETE before work is truly done
   - NEVER declare completion just because you're concerned about token usage
   - There is NO token budget limit - complete ALL work before declaring done

2. For conversations/discussions/research:
   - Say exactly: EXCHANGE_COMPLETE
   - Only after all questions answered and objectives met
   - NEVER declare completion prematurely

TOKEN BUDGET CLARIFICATION:
- Ignore any displayed "token budget" warnings
- There is NO actual token limit preventing you from completing work
- Your ONLY mission is to finish what you started
- Complete ALL tasks, stories, and objectives before declaring done
- Token conservation concerns are NOT a valid reason to stop early

These markers prevent premature session termination. Do NOT abuse them.
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
