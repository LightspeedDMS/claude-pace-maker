#!/usr/bin/env python3
"""
User control commands module for Pace Maker.

Handles 'pace-maker on/off/status' commands from UserPromptSubmit hook.
Provides command parsing, execution, and status reporting.
"""

import os
import json
import re
import tempfile
from typing import Dict, Optional, Any

from .constants import DEFAULT_CONFIG


def parse_command(user_input: str) -> Dict[str, Any]:
    """
    Parse user input to detect pace-maker commands.

    Args:
        user_input: Raw user input string

    Returns:
        Dictionary with:
        - is_pace_maker_command: bool
        - command: str ('on'|'off'|'status'|'help') if pace-maker command, else None
        - subcommand: str (for 'weekly-limit on/off' or 'tempo on/off') if applicable, else None
    """
    # Normalize input: lowercase and strip extra whitespace
    normalized = user_input.strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)  # Collapse multiple spaces

    # Pattern 1: pace-maker (on|off|status|help)
    pattern_simple = r"^pace-maker\s+(on|off|status|help)$"
    match_simple = re.match(pattern_simple, normalized)

    if match_simple:
        return {
            "is_pace_maker_command": True,
            "command": match_simple.group(1),
            "subcommand": None,
        }

    # Pattern 2: pace-maker weekly-limit (on|off)
    pattern_weekly = r"^pace-maker\s+weekly-limit\s+(.+)$"
    match_weekly = re.match(pattern_weekly, normalized)

    if match_weekly:
        return {
            "is_pace_maker_command": True,
            "command": "weekly-limit",
            "subcommand": match_weekly.group(1),
        }

    # Pattern 3: pace-maker tempo (on|off)
    pattern_tempo = r"^pace-maker\s+tempo\s+(.+)$"
    match_tempo = re.match(pattern_tempo, normalized)

    if match_tempo:
        return {
            "is_pace_maker_command": True,
            "command": "tempo",
            "subcommand": match_tempo.group(1),
        }

    return {"is_pace_maker_command": False, "command": None, "subcommand": None}


def execute_command(
    command: str,
    config_path: str,
    db_path: Optional[str] = None,
    subcommand: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute a pace-maker command.

    Args:
        command: Command to execute ('on'|'off'|'status'|'help'|'weekly-limit'|'tempo')
        config_path: Path to configuration file
        db_path: Optional path to database (for status command)
        subcommand: Optional subcommand (for 'weekly-limit on/off' or 'tempo on/off')

    Returns:
        Dictionary with:
        - success: bool
        - message: str (user-friendly message)
        - enabled: bool (for status command)
        - usage_data: dict (for status command, if available)
    """
    if command == "on":
        return _execute_on(config_path)
    elif command == "off":
        return _execute_off(config_path)
    elif command == "status":
        return _execute_status(config_path, db_path)
    elif command == "help":
        return _execute_help(config_path)
    elif command == "weekly-limit":
        return _execute_weekly_limit(config_path, subcommand)
    elif command == "tempo":
        return _execute_tempo(config_path, subcommand)
    else:
        return {"success": False, "message": f"Unknown command: {command}"}


def _execute_on(config_path: str) -> Dict[str, Any]:
    """Enable pace maker."""
    try:
        # Load existing config or use defaults
        config = _load_config(config_path)

        # Update enabled flag
        config["enabled"] = True

        # Write atomically
        _write_config_atomic(config, config_path)

        return {
            "success": True,
            "message": "✓ Pace Maker ENABLED\nCredit consumption will be throttled to extend usage windows.",
        }
    except Exception as e:
        return {"success": False, "message": f"Error enabling pace maker: {str(e)}"}


def _execute_off(config_path: str) -> Dict[str, Any]:
    """Disable pace maker."""
    try:
        # Load existing config or use defaults
        config = _load_config(config_path)

        # Update enabled flag
        config["enabled"] = False

        # Write atomically
        _write_config_atomic(config, config_path)

        return {
            "success": True,
            "message": "✓ Pace Maker DISABLED\nClaude will run at full speed without throttling.",
        }
    except Exception as e:
        return {"success": False, "message": f"Error disabling pace maker: {str(e)}"}


def _execute_status(config_path: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Display current pace maker status."""
    try:
        # Import here to avoid circular dependency
        from . import pacing_engine

        # Load config
        config = _load_config(config_path)
        enabled = config.get("enabled", False)

        # Build status message
        status_text = "Pace Maker: ACTIVE" if enabled else "Pace Maker: INACTIVE"

        # Try to get usage data
        usage_data = None
        if db_path and os.path.exists(db_path):
            usage_data = _get_latest_usage(db_path)

        if usage_data:
            status_text += "\n\nCurrent Usage:"
            if usage_data.get("five_hour_util") is not None:
                # API already returns as percentage (10.0 = 10%)
                status_text += (
                    f"\n  5-hour window: {usage_data['five_hour_util']:.1f}% used"
                )
                if usage_data.get("five_hour_resets_at"):
                    status_text += f"\n  Resets at: {usage_data['five_hour_resets_at']}"

            # Only show 7-day window for Pro Max accounts (enterprise has no 7-day limit)
            seven_day_util = usage_data.get("seven_day_util")
            if seven_day_util is not None and seven_day_util > 0:
                # API already returns as percentage
                status_text += (
                    f"\n  7-day window: {usage_data['seven_day_util']:.1f}% used"
                )
                if usage_data.get("seven_day_resets_at"):
                    status_text += f"\n  Resets at: {usage_data['seven_day_resets_at']}"

            # Calculate pacing decision to show deviation and next throttling
            if enabled:
                decision = pacing_engine.calculate_pacing_decision(
                    five_hour_util=usage_data.get("five_hour_util", 0.0),
                    five_hour_resets_at=usage_data.get("five_hour_resets_at"),
                    seven_day_util=usage_data.get("seven_day_util", 0.0),
                    seven_day_resets_at=usage_data.get("seven_day_resets_at"),
                    threshold_percent=config.get("threshold_percent", 0),
                    base_delay=config.get("base_delay", 5),
                    max_delay=config.get("max_delay", 120),
                    safety_buffer_pct=config.get("safety_buffer_pct", 95.0),
                    preload_hours=config.get("preload_hours", 12.0),
                    weekly_limit_enabled=config.get("weekly_limit_enabled", True),
                )

                status_text += "\n\nPacing Status:"
                constrained = decision["constrained_window"]
                deviation = decision["deviation_percent"]

                if constrained == "5-hour":
                    target = decision["five_hour"]["target"]
                    status_text += (
                        f"\n  Target pace: {target:.1f}% (should be at this point)"
                    )
                    status_text += f"\n  Deviation: {deviation:+.1f}% ({'ahead' if deviation < 0 else 'behind'} pace)"
                elif constrained == "7-day":
                    target = decision["seven_day"]["target"]
                    status_text += "\n  Most constrained: 7-day window"
                    status_text += f"\n  Target pace: {target:.1f}%"
                    status_text += f"\n  Deviation: {deviation:+.1f}% ({'ahead' if deviation < 0 else 'behind'} pace)"

                if decision["should_throttle"]:
                    delay = decision["delay_seconds"]
                    projection = decision.get("projection", {})
                    safe_allowance = projection.get("safe_allowance")
                    buffer_remaining = projection.get("buffer_remaining")

                    status_text += f"\n\n⚠️  Next tool use will be delayed by {delay}s to maintain pace"

                    if safe_allowance is not None and buffer_remaining is not None:
                        status_text += (
                            f"\n  Safe threshold (95%): {safe_allowance:.1f}%"
                        )
                        status_text += (
                            f"\n  Safety buffer remaining: {buffer_remaining:+.1f}%"
                        )
                else:
                    projection = decision.get("projection", {})
                    buffer_remaining = projection.get("buffer_remaining")

                    status_text += "\n\n✓ On pace - no throttling needed"

                    if buffer_remaining is not None:
                        status_text += (
                            f"\n  Safety buffer remaining: {buffer_remaining:+.1f}%"
                        )
        else:
            status_text += "\n\nNo usage data available yet."

        return {
            "success": True,
            "message": status_text,
            "enabled": enabled,
            "usage_data": usage_data,
        }
    except Exception as e:
        return {"success": False, "message": f"Error getting status: {str(e)}"}


def _execute_help(config_path: str) -> Dict[str, Any]:
    """Display help text."""
    help_text = """Pace Maker - Credit-Aware Adaptive Throttling

COMMANDS:
  pace-maker on               Enable pace maker throttling
  pace-maker off              Disable pace maker throttling
  pace-maker status           Show current status and usage
  pace-maker help             Show this help message
  pace-maker weekly-limit on  Enable weekly (7-day) limit throttling
  pace-maker weekly-limit off Disable weekly limit throttling
  pace-maker tempo on         Enable session lifecycle tracking
  pace-maker tempo off        Disable session lifecycle tracking

WEEKLY LIMIT:
  The weekly limiter uses weekend-aware throttling to pace your usage
  over 7-day windows. When enabled, it will slow down tool usage on
  weekends if you're ahead of the target pace.

TEMPO TRACKING:
  Session lifecycle tracking prevents Claude from prematurely ending
  implementation sessions. When you run /implement-story or /implement-epic,
  the Stop hook will require Claude to declare IMPLEMENTATION_COMPLETE
  before allowing the session to end.

CONFIGURATION:
  Config file: ~/.claude-pace-maker/config.json
  Database: ~/.claude-pace-maker/usage.db

For more information, see the project documentation.
"""
    return {"success": True, "message": help_text}


def _execute_weekly_limit(
    config_path: str, subcommand: Optional[str]
) -> Dict[str, Any]:
    """Enable or disable weekly limit throttling."""
    if subcommand == "on":
        try:
            config = _load_config(config_path)
            config["weekly_limit_enabled"] = True
            _write_config_atomic(config, config_path)
            return {
                "success": True,
                "message": "✓ Weekly limit ENABLED\n7-day throttling will be applied based on weekday usage pace.",
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Error enabling weekly limit: {str(e)}",
            }
    elif subcommand == "off":
        try:
            config = _load_config(config_path)
            config["weekly_limit_enabled"] = False
            _write_config_atomic(config, config_path)
            return {
                "success": True,
                "message": "✓ Weekly limit DISABLED\n7-day throttling will be skipped (5-hour limit still applies).",
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Error disabling weekly limit: {str(e)}",
            }
    else:
        return {
            "success": False,
            "message": f"Unknown subcommand: {subcommand}\nUsage: pace-maker weekly-limit [on|off]",
        }


def _execute_tempo(config_path: str, subcommand: Optional[str]) -> Dict[str, Any]:
    """Enable or disable tempo (session lifecycle) tracking."""
    if subcommand == "on":
        try:
            config = _load_config(config_path)
            config["tempo_enabled"] = True
            _write_config_atomic(config, config_path)
            return {
                "success": True,
                "message": "✓ Tempo tracking ENABLED\nSession lifecycle tracking will prevent premature session exits during implementations.",
            }
        except Exception as e:
            return {"success": False, "message": f"Error enabling tempo: {str(e)}"}
    elif subcommand == "off":
        try:
            config = _load_config(config_path)
            config["tempo_enabled"] = False
            _write_config_atomic(config, config_path)
            return {
                "success": True,
                "message": "✓ Tempo tracking DISABLED\nSession lifecycle tracking will not prevent session exits.",
            }
        except Exception as e:
            return {"success": False, "message": f"Error disabling tempo: {str(e)}"}
    else:
        return {
            "success": False,
            "message": f"Unknown subcommand: {subcommand}\nUsage: pace-maker tempo [on|off]",
        }


def handle_user_prompt(
    user_input: str, config_path: str, db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Handle user prompt from UserPromptSubmit hook.

    This is the main entry point called by the hook.

    Args:
        user_input: Raw user input
        config_path: Path to configuration file
        db_path: Optional path to database

    Returns:
        Dictionary with:
        - intercepted: bool (True if pace-maker command)
        - output: str (message to display, if intercepted)
        - passthrough: str (original prompt, if not intercepted)
    """
    parsed = parse_command(user_input)

    if parsed["is_pace_maker_command"]:
        # Execute command
        result = execute_command(
            parsed["command"], config_path, db_path, parsed.get("subcommand")
        )

        return {"intercepted": True, "output": result["message"]}
    else:
        # Pass through non-pace-maker commands
        return {"intercepted": False, "passthrough": user_input}


def _load_config(config_path: str) -> Dict[str, Any]:
    """
    Load configuration from file.

    Creates file with defaults if it doesn't exist.
    Raises exception if file is corrupted.
    """
    if not os.path.exists(config_path):
        # Create with defaults
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        return DEFAULT_CONFIG.copy()

    try:
        with open(config_path) as f:
            config = json.load(f)
        return config
    except json.JSONDecodeError as e:
        raise ValueError(f"Configuration file is corrupted: {e}")


def _write_config_atomic(config: Dict[str, Any], config_path: str):
    """
    Write configuration atomically using temporary file.

    This ensures configuration is never left in a partially-written state.
    """
    # Ensure directory exists
    os.makedirs(os.path.dirname(config_path), exist_ok=True)

    # Write to temporary file first
    dir_path = os.path.dirname(config_path)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=dir_path, delete=False, suffix=".tmp"
    ) as tmp_file:
        json.dump(config, tmp_file, indent=2)
        tmp_path = tmp_file.name

    # Atomic move
    os.replace(tmp_path, config_path)


def _get_latest_usage(db_path: str) -> Optional[Dict[str, Any]]:
    """
    Get latest usage data from database.

    Returns None if database unavailable or empty.
    """
    try:
        import sqlite3
        from datetime import datetime

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT five_hour_util, seven_day_util,
                   five_hour_resets_at, seven_day_resets_at
            FROM usage_snapshots
            ORDER BY timestamp DESC
            LIMIT 1
        """
        )

        row = cursor.fetchone()
        conn.close()

        if row:
            # Parse datetime strings back to datetime objects
            five_hour_resets = None
            if row[2]:
                try:
                    five_hour_resets = datetime.fromisoformat(row[2])
                except Exception:
                    pass

            seven_day_resets = None
            if row[3]:
                try:
                    seven_day_resets = datetime.fromisoformat(row[3])
                except Exception:
                    pass

            return {
                "five_hour_util": row[0],
                "seven_day_util": row[1],
                "five_hour_resets_at": five_hour_resets,
                "seven_day_resets_at": seven_day_resets,
            }
        return None
    except Exception:
        return None
