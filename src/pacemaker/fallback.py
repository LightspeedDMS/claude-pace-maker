#!/usr/bin/env python3
"""
Fallback mode state machine for Resilient Pacing.

Story #38: When the Claude API returns 429 errors, pace-maker enters fallback
mode and synthesizes utilization estimates from accumulated token costs.

State machine transitions:
  NORMAL -> FALLBACK (on 429 / backoff entry)
  FALLBACK -> NORMAL (on API recovery / backoff expiry)

State is persisted in fallback_state.json for cross-invocation durability.
"""

import json
import os
import time
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any

from .logger import log_warning, log_info


# Default path for fallback state file
DEFAULT_FALLBACK_STATE_PATH = str(
    Path.home() / ".claude-pace-maker" / "fallback_state.json"
)

# Default path for token costs config
DEFAULT_TOKEN_COSTS_PATH = str(
    Path(__file__).parent.parent.parent / "config" / "token_costs.json"
)

# API-equivalent pricing per 1M tokens (mirrors claude-usage-reporting constants)
# These are used to compute accumulated_cost from raw token counts.
API_PRICING: Dict[str, Dict[str, float]] = {
    "opus": {
        "input": 15.0,
        "output": 75.0,
        "cache_read": 1.50,
        "cache_create": 18.75,
    },
    "sonnet": {
        "input": 3.0,
        "output": 15.0,
        "cache_read": 0.30,
        "cache_create": 3.75,
    },
    "haiku": {
        "input": 0.80,
        "output": 4.0,
        "cache_read": 0.08,
        "cache_create": 1.00,
    },
}

# Default coefficients if token_costs.json is unavailable
_DEFAULT_TOKEN_COSTS: Dict[str, Dict[str, float]] = {
    "5x": {"coefficient_5h": 0.0075, "coefficient_7d": 0.0011},
    "20x": {"coefficient_5h": 0.0014, "coefficient_7d": 0.0002},
}


class FallbackState(Enum):
    """State machine states for fallback mode."""

    NORMAL = "normal"
    FALLBACK = "fallback"
    # TODO: Add TRUEUP intermediate state when hook.py integration wires enter/exit_fallback


def _default_state() -> Dict[str, Any]:
    """Return fresh default fallback state (NORMAL)."""
    return {
        "state": FallbackState.NORMAL.value,
        "baseline_5h": None,
        "baseline_7d": None,
        "accumulated_cost": 0.0,
        "entered_at": None,
    }


def load_fallback_state(state_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Load fallback state from file.

    Args:
        state_path: Path to state file (default: ~/.claude-pace-maker/fallback_state.json)

    Returns:
        State dict with state, baseline_5h, baseline_7d, accumulated_cost, entered_at.
        Returns NORMAL defaults if file missing or corrupt.
    """
    if state_path is None:
        state_path = DEFAULT_FALLBACK_STATE_PATH

    try:
        path = Path(state_path)
        if not path.exists():
            return _default_state()

        text = path.read_text().strip()
        if not text:
            return _default_state()

        data = json.loads(text)
        # Fill missing keys with defaults
        defaults = _default_state()
        for key in defaults:
            if key not in data:
                data[key] = defaults[key]
        return data

    except Exception as e:
        log_warning("fallback", "Failed to load fallback state, using defaults", e)
        return _default_state()


def save_fallback_state(
    state: Dict[str, Any], state_path: Optional[str] = None
) -> None:
    """
    Atomically save fallback state to file.

    Uses write-to-tmp + rename pattern to avoid partial writes.

    Args:
        state: State dict to save
        state_path: Path to state file (default: ~/.claude-pace-maker/fallback_state.json)
    """
    if state_path is None:
        state_path = DEFAULT_FALLBACK_STATE_PATH

    try:
        path = Path(state_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        tmp_path = path.with_suffix(f".json.tmp.{os.getpid()}")
        tmp_path.write_text(json.dumps(state))
        tmp_path.rename(path)

    except Exception as e:
        log_warning("fallback", "Failed to save fallback state", e)


def enter_fallback(
    usage_cache_path: str,
    state_path: Optional[str] = None,
) -> None:
    """
    Transition to FALLBACK mode, capturing baselines from usage_cache.json.

    Called when API returns 429 and backoff begins. Captures current utilization
    values as baselines for synthetic calculation. Idempotent: if already in
    FALLBACK, does not reset accumulated_cost.

    Args:
        usage_cache_path: Path to usage_cache.json (written by api_client)
        state_path: Path to fallback_state.json
    """
    if state_path is None:
        state_path = DEFAULT_FALLBACK_STATE_PATH

    current_state = load_fallback_state(state_path)

    # Idempotent: already in fallback, do not reset cost
    if current_state["state"] == FallbackState.FALLBACK.value:
        log_info("fallback", "Already in fallback mode, not resetting accumulated_cost")
        return

    # Read baselines from usage_cache.json
    baseline_5h = 0.0
    baseline_7d = 0.0

    try:
        cache_path = Path(usage_cache_path)
        if cache_path.exists():
            text = cache_path.read_text().strip()
            if text:
                cache_data = json.loads(text)
                response = cache_data.get("response", {})

                five_hour = response.get("five_hour", {}) or {}
                baseline_5h = float(five_hour.get("utilization", 0.0) or 0.0)

                seven_day = response.get("seven_day") or {}
                baseline_7d = float(seven_day.get("utilization", 0.0) or 0.0)
    except Exception as e:
        log_warning(
            "fallback", "Failed to read usage_cache for baselines, using 0.0", e
        )

    new_state = {
        "state": FallbackState.FALLBACK.value,
        "baseline_5h": baseline_5h,
        "baseline_7d": baseline_7d,
        "accumulated_cost": 0.0,
        "entered_at": time.time(),
    }

    save_fallback_state(new_state, state_path)
    log_info(
        "fallback",
        f"Entered fallback mode. Baselines: 5h={baseline_5h:.1f}%, 7d={baseline_7d:.1f}%",
    )


def exit_fallback(
    real_5h: float,
    real_7d: float,
    state_path: Optional[str] = None,
) -> None:
    """
    Transition from FALLBACK back to NORMAL after API recovery.

    Clears accumulated_cost and baselines. Real values replace synthetic ones.

    Args:
        real_5h: Real 5-hour utilization from recovered API
        real_7d: Real 7-day utilization from recovered API
        state_path: Path to fallback_state.json
    """
    if state_path is None:
        state_path = DEFAULT_FALLBACK_STATE_PATH

    current_state = load_fallback_state(state_path)

    if current_state["state"] == FallbackState.NORMAL.value:
        # Already normal, nothing to do
        return

    new_state = {
        "state": FallbackState.NORMAL.value,
        "baseline_5h": None,
        "baseline_7d": None,
        "accumulated_cost": 0.0,
        "entered_at": None,
    }

    save_fallback_state(new_state, state_path)
    log_info(
        "fallback",
        f"Exited fallback mode (true-up). Real: 5h={real_5h:.1f}%, 7d={real_7d:.1f}%",
    )


def load_token_costs(costs_path: Optional[str] = None) -> Dict[str, Dict[str, float]]:
    """
    Load token cost coefficients from config file.

    Args:
        costs_path: Path to token_costs.json (default: config/token_costs.json)

    Returns:
        Dict with tier keys ("5x", "20x") each containing coefficient_5h and coefficient_7d.
        Returns hardcoded defaults if file missing or corrupt.
    """
    if costs_path is None:
        costs_path = DEFAULT_TOKEN_COSTS_PATH

    try:
        path = Path(costs_path)
        if not path.exists():
            return _DEFAULT_TOKEN_COSTS.copy()

        text = path.read_text().strip()
        if not text:
            return _DEFAULT_TOKEN_COSTS.copy()

        data = json.loads(text)

        # Validate expected keys exist
        if "5x" not in data or "20x" not in data:
            log_warning(
                "fallback",
                "token_costs.json missing required tier keys, using defaults",
            )
            return _DEFAULT_TOKEN_COSTS.copy()

        return {
            "5x": {
                "coefficient_5h": float(data["5x"]["coefficient_5h"]),
                "coefficient_7d": float(data["5x"]["coefficient_7d"]),
            },
            "20x": {
                "coefficient_5h": float(data["20x"]["coefficient_5h"]),
                "coefficient_7d": float(data["20x"]["coefficient_7d"]),
            },
        }

    except Exception as e:
        log_warning("fallback", "Failed to load token_costs.json, using defaults", e)
        return _DEFAULT_TOKEN_COSTS.copy()


def detect_tier(profile: Optional[Dict[str, Any]]) -> str:
    """
    Detect subscription tier from profile data.

    Args:
        profile: Profile dict from Claude OAuth API (or None)

    Returns:
        "20x" if Claude Max, "5x" otherwise (default)
    """
    if not profile:
        return "5x"

    try:
        account = profile.get("account", {}) or {}
        has_max = account.get("has_claude_max", False)
        if has_max:
            return "20x"
    except Exception:
        pass

    return "5x"


def calculate_synthetic(
    state: Dict[str, Any],
    tier: str,
    token_costs: Dict[str, Dict[str, float]],
) -> Dict[str, Any]:
    """
    Calculate synthetic utilization values during fallback mode.

    Formula:
        synthetic_5h = baseline_5h + (accumulated_cost * coefficient_5h * 100)
        synthetic_7d = baseline_7d + (accumulated_cost * coefficient_7d * 100)

    Values are capped at 100.0.

    Args:
        state: Current fallback state dict (from load_fallback_state)
        tier: Subscription tier ("5x" or "20x")
        token_costs: Tier coefficients (from load_token_costs)

    Returns:
        Dict with synthetic_5h, synthetic_7d, is_synthetic=True, fallback_mode=True
    """
    baseline_5h = float(state.get("baseline_5h") or 0.0)
    baseline_7d = float(state.get("baseline_7d") or 0.0)
    accumulated_cost = float(state.get("accumulated_cost", 0.0))

    # Use tier-specific coefficients, fall back to 5x defaults if tier unknown
    tier_costs = (
        token_costs.get(tier) or token_costs.get("5x") or _DEFAULT_TOKEN_COSTS["5x"]
    )
    coeff_5h = float(tier_costs.get("coefficient_5h", 0.0075))
    coeff_7d = float(tier_costs.get("coefficient_7d", 0.0011))

    synthetic_5h = baseline_5h + (accumulated_cost * coeff_5h * 100.0)
    synthetic_7d = baseline_7d + (accumulated_cost * coeff_7d * 100.0)

    # Cap at 100%
    synthetic_5h = min(synthetic_5h, 100.0)
    synthetic_7d = min(synthetic_7d, 100.0)

    return {
        "synthetic_5h": synthetic_5h,
        "synthetic_7d": synthetic_7d,
        "is_synthetic": True,
        "fallback_mode": True,
        "accumulated_cost": accumulated_cost,
        "tier": tier,
    }


def accumulate_cost(
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    model_family: str,
    state_path: Optional[str] = None,
) -> None:
    """
    Add API-equivalent cost to accumulated_cost during fallback mode.

    Only updates state when currently in FALLBACK mode. No-op in NORMAL state.

    Uses API_PRICING constants to convert token counts to dollar cost.

    Args:
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        cache_read_tokens: Number of cache read tokens
        cache_creation_tokens: Number of cache creation tokens
        model_family: Model family ("opus", "sonnet", "haiku")
        state_path: Path to fallback_state.json
    """
    if state_path is None:
        state_path = DEFAULT_FALLBACK_STATE_PATH

    try:
        current_state = load_fallback_state(state_path)

        if current_state["state"] != FallbackState.FALLBACK.value:
            return  # No-op when not in fallback

        pricing = API_PRICING.get(model_family.lower()) or API_PRICING["sonnet"]

        cost = (
            input_tokens * pricing["input"] / 1_000_000
            + output_tokens * pricing["output"] / 1_000_000
            + cache_read_tokens * pricing["cache_read"] / 1_000_000
            + cache_creation_tokens * pricing["cache_create"] / 1_000_000
        )

        current_state["accumulated_cost"] = (
            float(current_state.get("accumulated_cost", 0.0)) + cost
        )
        save_fallback_state(current_state, state_path)

    except Exception as e:
        log_warning("fallback", "Failed to accumulate cost", e)


def is_fallback_active(state_path: Optional[str] = None) -> bool:
    """
    Check if pace-maker is currently in fallback mode.

    Args:
        state_path: Path to fallback_state.json

    Returns:
        True if state is FALLBACK or TRUEUP, False otherwise
    """
    state = load_fallback_state(state_path)
    return state["state"] == FallbackState.FALLBACK.value


def get_fallback_display_label() -> str:
    """
    Get the display label suffix for synthetic utilization values.

    Returns:
        String like "[est]" to append to utilization display values
    """
    return "[est]"


def get_fallback_status_message() -> str:
    """
    Get human-readable status message shown when in fallback mode.

    Returns:
        String message indicating API is unavailable and estimated pacing is used
    """
    return "API unavailable - using estimated pacing"
