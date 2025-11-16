#!/usr/bin/env python3
"""
Session lifecycle tracking module for Pace Maker.

Manages IMPLEMENTATION_START and IMPLEMENTATION_COMPLETE markers
to prevent Claude from prematurely ending implementation sessions.
"""

import os
import json
import re
from pathlib import Path


def has_implementation_started(state_path: str) -> bool:
    """
    Check if IMPLEMENTATION_START marker exists in state.

    Args:
        state_path: Path to state.json file

    Returns:
        True if implementation has started, False otherwise
    """
    try:
        if not os.path.exists(state_path):
            return False

        with open(state_path) as f:
            state = json.load(f)

        return state.get("implementation_started", False)
    except Exception:
        return False


def has_implementation_completed(state_path: str) -> bool:
    """
    Check if IMPLEMENTATION_COMPLETE marker exists in state.

    Args:
        state_path: Path to state.json file

    Returns:
        True if implementation is complete, False otherwise
    """
    try:
        if not os.path.exists(state_path):
            return False

        with open(state_path) as f:
            state = json.load(f)

        return state.get("implementation_completed", False)
    except Exception:
        return False


def mark_implementation_started(state_path: str):
    """
    Set IMPLEMENTATION_START marker in state.

    Args:
        state_path: Path to state.json file
    """
    try:
        # Load existing state or create new
        state = {}
        if os.path.exists(state_path):
            with open(state_path) as f:
                state = json.load(f)

        # Set marker
        state["implementation_started"] = True
        state["implementation_completed"] = False
        state["stop_hook_prompt_count"] = 0  # Reset prompt counter

        # Ensure directory exists
        Path(state_path).parent.mkdir(parents=True, exist_ok=True)

        # Write state
        with open(state_path, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        # Graceful degradation - log but don't crash
        print(f"[PACE-MAKER] Error marking implementation started: {e}")


def mark_implementation_completed(state_path: str):
    """
    Set IMPLEMENTATION_COMPLETE marker in state.

    Args:
        state_path: Path to state.json file
    """
    try:
        # Load existing state or create new
        state = {}
        if os.path.exists(state_path):
            with open(state_path) as f:
                state = json.load(f)

        # Set marker
        state["implementation_completed"] = True

        # Ensure directory exists
        Path(state_path).parent.mkdir(parents=True, exist_ok=True)

        # Write state
        with open(state_path, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        # Graceful degradation - log but don't crash
        print(f"[PACE-MAKER] Error marking implementation completed: {e}")


def clear_lifecycle_markers(state_path: str):
    """
    Clear both lifecycle markers from state.

    Args:
        state_path: Path to state.json file
    """
    try:
        # Load existing state
        if not os.path.exists(state_path):
            return

        with open(state_path) as f:
            state = json.load(f)

        # Clear markers
        state["implementation_started"] = False
        state["implementation_completed"] = False
        state["stop_hook_prompt_count"] = 0

        # Write state
        with open(state_path, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        # Graceful degradation - log but don't crash
        print(f"[PACE-MAKER] Error clearing lifecycle markers: {e}")


def should_mark_implementation_start(user_input: str) -> bool:
    """
    Detect if user input is an /implement-* command.

    Args:
        user_input: Raw user input string

    Returns:
        True if implementation command detected, False otherwise
    """
    # Normalize input
    normalized = user_input.strip().lower()

    # Check for /implement-story or /implement-epic commands
    pattern = r"^/implement-(story|epic)\s+"
    return re.match(pattern, normalized) is not None


def is_implementation_complete_response(response: str) -> bool:
    """
    Check if response is exactly 'IMPLEMENTATION_COMPLETE'.

    Args:
        response: Claude's response text

    Returns:
        True if exact match (case-sensitive), False otherwise
    """
    # Strip whitespace but require exact match (case-sensitive)
    return response.strip() == "IMPLEMENTATION_COMPLETE"


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
