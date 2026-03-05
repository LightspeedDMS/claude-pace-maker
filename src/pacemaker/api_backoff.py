#!/usr/bin/env python3
"""
Exponential backoff state management for Anthropic API rate limiting.

Persists backoff state across stateless hook invocations via api_backoff.json.

State structure:
{
    "consecutive_429s": 0,
    "backoff_until": null,       # epoch float or null
    "last_success_time": null    # epoch float or null
}

Backoff formula: min(300 * 2^consecutive_429s, 3600) seconds
  - 1st 429 -> consecutive=1, delay=600s  (10 min)
  - 2nd 429 -> consecutive=2, delay=1200s (20 min)
  - 3rd 429 -> consecutive=3, delay=2400s (40 min)
  - 4th 429 -> consecutive=4, delay=3600s (60 min, cap)
"""

import json
import os
import time
from pathlib import Path
from typing import Optional

from .logger import log_warning, log_info


# Default path for backoff state file
DEFAULT_BACKOFF_STATE_PATH = str(
    Path.home() / ".claude-pace-maker" / "api_backoff.json"
)

# Backoff constants — shared formula with claude-usage-reporting
_BASE_DELAY_SECONDS = 300  # 5 minutes base
_MAX_DELAY_SECONDS = 3600  # 60 minutes cap


def _default_state() -> dict:
    """Return fresh default backoff state."""
    return {
        "consecutive_429s": 0,
        "backoff_until": None,
        "last_success_time": None,
    }


def load_backoff_state(state_path: Optional[str] = None) -> dict:
    """
    Load backoff state from file.

    Args:
        state_path: Path to state file (default: ~/.claude-pace-maker/api_backoff.json)

    Returns:
        State dict with consecutive_429s, backoff_until, last_success_time.
        Returns defaults if file missing or corrupt.
    """
    if state_path is None:
        state_path = DEFAULT_BACKOFF_STATE_PATH

    try:
        path = Path(state_path)
        if not path.exists():
            return _default_state()

        text = path.read_text().strip()
        if not text:
            return _default_state()

        data = json.loads(text)
        # Validate expected keys exist, fill missing with defaults
        defaults = _default_state()
        for key in defaults:
            if key not in data:
                data[key] = defaults[key]
        return data

    except Exception as e:
        log_warning("api_backoff", "Failed to load backoff state, using defaults", e)
        return _default_state()


def save_backoff_state(state: dict, state_path: Optional[str] = None) -> None:
    """
    Atomically save backoff state to file.

    Uses write-to-tmp + rename pattern to avoid partial writes.

    Args:
        state: State dict to save
        state_path: Path to state file (default: ~/.claude-pace-maker/api_backoff.json)
    """
    if state_path is None:
        state_path = DEFAULT_BACKOFF_STATE_PATH

    try:
        path = Path(state_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        tmp_path = path.with_suffix(f".json.tmp.{os.getpid()}")
        tmp_path.write_text(json.dumps(state))
        tmp_path.rename(path)

    except Exception as e:
        log_warning("api_backoff", "Failed to save backoff state", e)


def is_in_backoff(state_path: Optional[str] = None) -> bool:
    """
    Check if we are currently in a backoff period.

    Args:
        state_path: Path to state file (default: ~/.claude-pace-maker/api_backoff.json)

    Returns:
        True if backoff_until is set and still in the future, False otherwise.
    """
    state = load_backoff_state(state_path)
    backoff_until = state.get("backoff_until")
    if backoff_until is None:
        return False
    return time.time() < backoff_until


def get_backoff_remaining_seconds(state_path: Optional[str] = None) -> float:
    """
    Get number of seconds remaining in current backoff period.

    Args:
        state_path: Path to state file (default: ~/.claude-pace-maker/api_backoff.json)

    Returns:
        Seconds until backoff expires, or 0.0 if not in backoff.
    """
    state = load_backoff_state(state_path)
    backoff_until = state.get("backoff_until")
    if backoff_until is None:
        return 0.0
    remaining = backoff_until - time.time()
    return max(0.0, remaining)


def record_429(state_path: Optional[str] = None) -> None:
    """
    Record a 429 rate-limit response and update backoff state.

    Increments consecutive_429s and sets backoff_until using exponential backoff:
        delay = min(300 * 2^consecutive_429s, 3600)

    Args:
        state_path: Path to state file (default: ~/.claude-pace-maker/api_backoff.json)
    """
    state = load_backoff_state(state_path)
    state["consecutive_429s"] += 1
    count = state["consecutive_429s"]
    delay = min(_BASE_DELAY_SECONDS * (2**count), _MAX_DELAY_SECONDS)
    state["backoff_until"] = time.time() + delay
    save_backoff_state(state, state_path)
    log_warning(
        "api_backoff",
        f"Rate limited (429). Consecutive count: {count}. "
        f"Backing off for {delay:.0f}s.",
    )


def record_success(state_path: Optional[str] = None) -> None:
    """
    Record a successful API response, resetting backoff state.

    Args:
        state_path: Path to state file (default: ~/.claude-pace-maker/api_backoff.json)
    """
    state = load_backoff_state(state_path)
    had_backoff = state["consecutive_429s"] > 0
    state["consecutive_429s"] = 0
    state["backoff_until"] = None
    state["last_success_time"] = time.time()
    save_backoff_state(state, state_path)
    if had_backoff:
        log_info("api_backoff", "API call succeeded, backoff state reset.")
