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
from datetime import datetime, timedelta
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
    "20x": {"coefficient_5h": 0.001875, "coefficient_7d": 0.000275},
}


def parse_api_datetime(s: Optional[str]) -> Optional[datetime]:
    """
    Parse an ISO 8601 datetime string from the Claude API into a naive UTC datetime.

    Handles the common variants returned by the API:
    - "2026-03-06T15:00:00+00:00"  -> strips +00:00 suffix
    - "2026-03-06T15:00:00Z"       -> strips Z suffix
    - "2026-03-06T15:00:00"        -> plain ISO, used as-is

    Args:
        s: ISO datetime string, or None/empty

    Returns:
        Timezone-naive datetime object, or None if input is None/empty/invalid.
    """
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        # Normalise: strip +00:00 and Z suffixes to get a plain naive datetime
        normalised = s.strip().replace("+00:00", "").rstrip("Z")
        return datetime.fromisoformat(normalised)
    except (ValueError, TypeError, AttributeError):
        return None


class FallbackState(Enum):
    """State machine states for fallback mode."""

    NORMAL = "normal"
    FALLBACK = "fallback"


def _default_state() -> Dict[str, Any]:
    """Return fresh default fallback state (NORMAL)."""
    return {
        "state": FallbackState.NORMAL.value,
        "baseline_5h": None,
        "baseline_7d": None,
        "resets_at_5h": None,
        "resets_at_7d": None,
        "accumulated_cost": 0.0,
        "rollover_cost_5h": None,
        "rollover_cost_7d": None,
        "last_rollover_resets_5h": None,
        "last_rollover_resets_7d": None,
        "tier": None,
        "entered_at": None,
        "last_accumulated_usage": None,
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

    # Read baselines and resets_at from usage_cache.json
    baseline_5h = 0.0
    baseline_7d = 0.0
    resets_at_5h = None
    resets_at_7d = None

    try:
        cache_path = Path(usage_cache_path)
        if cache_path.exists():
            text = cache_path.read_text().strip()
            if text:
                cache_data = json.loads(text)

                # Skip synthetic cache — it was written by another fallback
                # session and contains unreliable data (race condition)
                if cache_data.get("is_synthetic"):
                    log_warning(
                        "fallback",
                        "usage_cache contains synthetic data, using 0.0 baselines",
                    )
                else:
                    response = cache_data.get("response", {})

                    five_hour = response.get("five_hour", {}) or {}
                    baseline_5h = float(five_hour.get("utilization", 0.0) or 0.0)

                    seven_day = response.get("seven_day") or {}
                    baseline_7d = float(seven_day.get("utilization", 0.0) or 0.0)

                    resets_at_5h = five_hour.get("resets_at")
                    resets_at_7d = seven_day.get("resets_at")
    except Exception as e:
        log_warning(
            "fallback", "Failed to read usage_cache for baselines, using 0.0", e
        )

    # Detect tier from cached profile (no max_age — use whatever we have)
    try:
        from .profile_cache import load_cached_profile

        cached_profile = load_cached_profile()
        tier = detect_tier(cached_profile)
        log_info("fallback", f"Detected tier from profile cache: {tier}")
    except Exception as e:
        log_warning(
            "fallback", "Could not detect tier from profile cache, defaulting to 5x", e
        )
        tier = "5x"

    # Synthesize resets_at when null so rollover detection has valid timestamps.
    # Without these, calculate_synthetic_with_rollover() skips rollover detection
    # and the monitor shows "No active windows".
    now_utc = datetime.utcnow()
    if not resets_at_5h:
        resets_at_5h = (now_utc + timedelta(hours=5)).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"
        )
        log_info("fallback", f"Synthesized resets_at_5h: {resets_at_5h}")
    if not resets_at_7d:
        resets_at_7d = (now_utc + timedelta(hours=168)).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"
        )
        log_info("fallback", f"Synthesized resets_at_7d: {resets_at_7d}")

    # Start from _default_state() so all keys are present (P4: avoid key drift).
    new_state = _default_state()
    new_state.update(
        {
            "state": FallbackState.FALLBACK.value,
            "baseline_5h": baseline_5h,
            "baseline_7d": baseline_7d,
            "resets_at_5h": resets_at_5h,
            "resets_at_7d": resets_at_7d,
            "accumulated_cost": 0.0,
            "tier": tier,
            "entered_at": time.time(),
        }
    )

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

    # Start from _default_state() so all keys are present and cleared.
    # Override only the fields that differ from defaults.
    new_state = _default_state()
    new_state["state"] = FallbackState.NORMAL.value
    # accumulated_cost is already 0.0 from _default_state(); entered_at is None.

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


def _project_window(
    raw_resets_at: Optional[str],
    window_hours: float,
    now: datetime,
) -> tuple:
    """
    Parse a resets_at string and project it forward past *now* if the window
    has already expired.

    Args:
        raw_resets_at: ISO 8601 resets_at string (or None/empty)
        window_hours: Window length in hours (5 or 168)
        now: Current UTC datetime (naive)

    Returns:
        (projected_datetime_or_None, rolled: bool)
        - projected_datetime_or_None: The next future reset boundary, or None if unparseable
        - rolled: True if at least one window increment was applied
    """
    if not raw_resets_at:
        return None, False

    parsed = parse_api_datetime(raw_resets_at)
    if parsed is None:
        return None, False

    if parsed <= now:
        while parsed <= now:
            parsed += timedelta(hours=window_hours)
        return parsed, True

    return parsed, False


def calculate_synthetic_with_rollover(
    state: Dict[str, Any],
    tier: str,
    token_costs: Dict[str, Dict[str, float]],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Calculate synthetic utilization with window rollover detection.

    Extends calculate_synthetic() by detecting expired windows and projecting
    them forward. When a window rolls over, cost accumulated before the rollover
    is snapshotted so only tokens from the NEW window count toward utilization.

    Formula for rolled windows:
        cost_in_window = max(0.0, accumulated_cost - rollover_cost)
        synthetic_util = min(cost_in_window * coefficient * 100.0, 100.0)

    For non-rolled windows the standard formula is used (same as calculate_synthetic).

    Args:
        state: Current fallback state dict — mutated in-place if rollover occurs
        tier: Subscription tier ("5x" or "20x")
        token_costs: Tier coefficients (from load_token_costs)
        now: Current UTC datetime (default: datetime.utcnow())

    Returns:
        Dict with:
          synthetic_5h: float
          synthetic_7d: float
          five_resets: Optional[datetime]  — projected 5h reset boundary
          seven_resets: Optional[datetime] — projected 7d reset boundary
          is_synthetic: True
          state_changed: bool  — True if state was mutated (rollover occurred)
    """
    if now is None:
        now = datetime.utcnow()

    tier_costs = (
        token_costs.get(tier) or token_costs.get("5x") or _DEFAULT_TOKEN_COSTS["5x"]
    )
    coeff_5h = float(tier_costs.get("coefficient_5h", 0.0075))
    coeff_7d = float(tier_costs.get("coefficient_7d", 0.0011))

    accumulated_cost = float(state.get("accumulated_cost", 0.0))

    # --- Project 5-hour window ---
    five_resets, five_rolled = _project_window(
        state.get("resets_at_5h"), window_hours=5.0, now=now
    )

    # --- Project 7-day window ---
    seven_resets, seven_rolled = _project_window(
        state.get("resets_at_7d"), window_hours=168.0, now=now
    )

    state_changed = False

    # --- Handle 5h rollover ---
    if five_rolled and five_resets is not None:
        projected_str = five_resets.isoformat()
        if state.get("last_rollover_resets_5h") != projected_str:
            state["rollover_cost_5h"] = accumulated_cost
            state["last_rollover_resets_5h"] = projected_str
            state_changed = True
        rollover_cost_5h = float(state.get("rollover_cost_5h") or 0.0)
        cost_in_window_5h = max(0.0, accumulated_cost - rollover_cost_5h)
        synthetic_5h = min(cost_in_window_5h * coeff_5h * 100.0, 100.0)
    else:
        baseline_5h = float(state.get("baseline_5h") or 0.0)
        synthetic_5h = min(baseline_5h + (accumulated_cost * coeff_5h * 100.0), 100.0)

    # --- Handle 7d rollover ---
    if seven_rolled and seven_resets is not None:
        projected_str = seven_resets.isoformat()
        if state.get("last_rollover_resets_7d") != projected_str:
            state["rollover_cost_7d"] = accumulated_cost
            state["last_rollover_resets_7d"] = projected_str
            state_changed = True
        rollover_cost_7d = float(state.get("rollover_cost_7d") or 0.0)
        cost_in_window_7d = max(0.0, accumulated_cost - rollover_cost_7d)
        synthetic_7d = min(cost_in_window_7d * coeff_7d * 100.0, 100.0)
    else:
        baseline_7d = float(state.get("baseline_7d") or 0.0)
        synthetic_7d = min(baseline_7d + (accumulated_cost * coeff_7d * 100.0), 100.0)

    return {
        "synthetic_5h": synthetic_5h,
        "synthetic_7d": synthetic_7d,
        "five_resets": five_resets,
        "seven_resets": seven_resets,
        "is_synthetic": True,
        "state_changed": state_changed,
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

        # Build usage fingerprint for deduplication
        current_usage = {
            "input": input_tokens,
            "output": output_tokens,
            "cache_read": cache_read_tokens,
            "cache_create": cache_creation_tokens,
            "model": model_family.lower(),
        }

        # Skip if this exact usage was already accumulated (parallel tool calls)
        last_usage = current_state.get("last_accumulated_usage")
        if last_usage == current_usage:
            log_info("fallback", "Skipping duplicate usage accumulation")
            return

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
        current_state["last_accumulated_usage"] = current_usage
        save_fallback_state(current_state, state_path)

    except Exception as e:
        log_warning("fallback", "Failed to accumulate cost", e)


def is_fallback_active(state_path: Optional[str] = None) -> bool:
    """
    Check if pace-maker is currently in fallback mode.

    Args:
        state_path: Path to fallback_state.json

    Returns:
        True if state is FALLBACK, False otherwise
    """
    state = load_fallback_state(state_path)
    return state["state"] == FallbackState.FALLBACK.value
