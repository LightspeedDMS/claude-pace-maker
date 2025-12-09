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
from .logger import log_warning
from .prompt_loader import PromptLoader


# Load messages on module import
_prompt_loader = PromptLoader()
try:
    MESSAGES = _prompt_loader.load_json_messages("messages.json", "user_commands")
except FileNotFoundError:
    # Fallback to empty dict if messages not found
    log_warning(
        "user_commands", "messages.json not found, using fallback messages", None
    )
    MESSAGES = {}


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

    # Pattern 1: pace-maker (on|off|status|help|version)
    pattern_simple = r"^pace-maker\s+(on|off|status|help|version)$"
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

    # Pattern 3: pace-maker tempo session (on|off)
    pattern_tempo_session = r"^pace-maker\s+tempo\s+session\s+(on|off)$"
    match_tempo_session = re.match(pattern_tempo_session, normalized)

    if match_tempo_session:
        return {
            "is_pace_maker_command": True,
            "command": "tempo",
            "subcommand": f"session {match_tempo_session.group(1)}",
        }

    # Pattern 4: pace-maker tempo (on|off) - global tempo control
    pattern_tempo = r"^pace-maker\s+tempo\s+(on|off)$"
    match_tempo = re.match(pattern_tempo, normalized)

    if match_tempo:
        return {
            "is_pace_maker_command": True,
            "command": "tempo",
            "subcommand": match_tempo.group(1),
        }

    # Pattern 5: pace-maker reminder (on|off) - subagent reminder control
    pattern_reminder = r"^pace-maker\s+reminder\s+(on|off)$"
    match_reminder = re.match(pattern_reminder, normalized)

    if match_reminder:
        return {
            "is_pace_maker_command": True,
            "command": "reminder",
            "subcommand": match_reminder.group(1),
        }

    # Pattern 6: pace-maker intent-validation (on|off) - intent validation control
    pattern_intent = r"^pace-maker\s+intent-validation\s+(on|off)$"
    match_intent = re.match(pattern_intent, normalized)

    if match_intent:
        return {
            "is_pace_maker_command": True,
            "command": "intent-validation",
            "subcommand": match_intent.group(1),
        }

    # Pattern 7: pace-maker 5-hour-limit (on|off) - 5-hour limit control
    pattern_5hour = r"^pace-maker\s+5-hour-limit\s+(on|off)$"
    match_5hour = re.match(pattern_5hour, normalized)

    if match_5hour:
        return {
            "is_pace_maker_command": True,
            "command": "5-hour-limit",
            "subcommand": match_5hour.group(1),
        }

    # Pattern 8: pace-maker loglevel (0|1|2|3|4) - log level control
    pattern_loglevel = r"^pace-maker\s+loglevel\s+([0-4])$"
    match_loglevel = re.match(pattern_loglevel, normalized)

    if match_loglevel:
        return {
            "is_pace_maker_command": True,
            "command": "loglevel",
            "subcommand": match_loglevel.group(1),
        }

    # Pattern 8.5: pace-maker tdd (on|off) - TDD enforcement control
    pattern_tdd = r"^pace-maker\s+tdd\s+(on|off)$"
    match_tdd = re.match(pattern_tdd, normalized)

    if match_tdd:
        return {
            "is_pace_maker_command": True,
            "command": "tdd",
            "subcommand": match_tdd.group(1),
        }

    # Pattern 9: pace-maker clean-code list - list clean code rules
    pattern_clean_code_list = r"^pace-maker\s+clean-code\s+list$"
    match_clean_code_list = re.match(pattern_clean_code_list, normalized)

    if match_clean_code_list:
        return {
            "is_pace_maker_command": True,
            "command": "clean-code",
            "subcommand": "list",
        }

    # Pattern 10: pace-maker clean-code add --id X --name Y --description Z
    pattern_clean_code_add = r"^pace-maker\s+clean-code\s+add\s+(.+)$"
    match_clean_code_add = re.match(pattern_clean_code_add, normalized)

    if match_clean_code_add:
        return {
            "is_pace_maker_command": True,
            "command": "clean-code",
            "subcommand": f"add {match_clean_code_add.group(1)}",
        }

    # Pattern 11: pace-maker clean-code modify --id X ...
    pattern_clean_code_modify = r"^pace-maker\s+clean-code\s+modify\s+(.+)$"
    match_clean_code_modify = re.match(pattern_clean_code_modify, normalized)

    if match_clean_code_modify:
        return {
            "is_pace_maker_command": True,
            "command": "clean-code",
            "subcommand": f"modify {match_clean_code_modify.group(1)}",
        }

    # Pattern 12: pace-maker clean-code remove --id X
    pattern_clean_code_remove = r"^pace-maker\s+clean-code\s+remove\s+(.+)$"
    match_clean_code_remove = re.match(pattern_clean_code_remove, normalized)

    if match_clean_code_remove:
        return {
            "is_pace_maker_command": True,
            "command": "clean-code",
            "subcommand": f"remove {match_clean_code_remove.group(1)}",
        }

    # Pattern 13: pace-maker core-paths list
    pattern_core_paths_list = r"^pace-maker\s+core-paths\s+list$"
    match_core_paths_list = re.match(pattern_core_paths_list, normalized)

    if match_core_paths_list:
        return {
            "is_pace_maker_command": True,
            "command": "core-paths",
            "subcommand": "list",
        }

    # Pattern 14: pace-maker core-paths add PATH
    pattern_core_paths_add = r"^pace-maker\s+core-paths\s+add\s+(.+)$"
    match_core_paths_add = re.match(pattern_core_paths_add, normalized)

    if match_core_paths_add:
        return {
            "is_pace_maker_command": True,
            "command": "core-paths",
            "subcommand": f"add {match_core_paths_add.group(1)}",
        }

    # Pattern 15: pace-maker core-paths remove PATH
    pattern_core_paths_remove = r"^pace-maker\s+core-paths\s+remove\s+(.+)$"
    match_core_paths_remove = re.match(pattern_core_paths_remove, normalized)

    if match_core_paths_remove:
        return {
            "is_pace_maker_command": True,
            "command": "core-paths",
            "subcommand": f"remove {match_core_paths_remove.group(1)}",
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
        command: Command to execute ('on'|'off'|'status'|'help'|'version'|'weekly-limit'|'tempo')
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
    elif command == "version":
        return _execute_version()
    elif command == "weekly-limit":
        return _execute_weekly_limit(config_path, subcommand)
    elif command == "tempo":
        return _execute_tempo(config_path, subcommand)
    elif command == "reminder":
        return _execute_reminder(config_path, subcommand)
    elif command == "intent-validation":
        return _execute_intent_validation(config_path, subcommand)
    elif command == "5-hour-limit":
        return _execute_5_hour_limit(config_path, subcommand)
    elif command == "loglevel":
        return _execute_loglevel(config_path, subcommand)
    elif command == "tdd":
        return _execute_tdd(config_path, subcommand)
    elif command == "clean-code":
        return _execute_clean_code(subcommand)
    elif command == "core-paths":
        return _execute_core_paths(subcommand)
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

        message = MESSAGES.get("pace_maker", {}).get(
            "enabled",
            "✓ Pace Maker ENABLED\nCredit consumption will be throttled to extend usage windows.",
        )
        return {
            "success": True,
            "message": message,
        }
    except Exception as e:
        error_template = MESSAGES.get("pace_maker", {}).get(
            "error_enabling", "Error enabling pace maker: {error}"
        )
        return {"success": False, "message": error_template.replace("{error}", str(e))}


def _execute_off(config_path: str) -> Dict[str, Any]:
    """Disable pace maker."""
    try:
        # Load existing config or use defaults
        config = _load_config(config_path)

        # Update enabled flag
        config["enabled"] = False

        # Write atomically
        _write_config_atomic(config, config_path)

        message = MESSAGES.get("pace_maker", {}).get(
            "disabled",
            "✓ Pace Maker DISABLED\nClaude will run at full speed without throttling.",
        )
        return {
            "success": True,
            "message": message,
        }
    except Exception as e:
        error_template = MESSAGES.get("pace_maker", {}).get(
            "error_disabling", "Error disabling pace maker: {error}"
        )
        return {"success": False, "message": error_template.replace("{error}", str(e))}


def _execute_status(config_path: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Display current pace maker status."""
    try:
        # Import here to avoid circular dependency
        from . import pacing_engine
        from .constants import DEFAULT_STATE_PATH
        from .hook import load_state

        # Load config
        config = _load_config(config_path)
        enabled = config.get("enabled", False)
        weekly_limit_enabled = config.get("weekly_limit_enabled", True)
        five_hour_limit_enabled = config.get("five_hour_limit_enabled", True)
        tempo_enabled = config.get("tempo_enabled", True)
        subagent_reminder_enabled = config.get("subagent_reminder_enabled", True)
        intent_validation_enabled = config.get("intent_validation_enabled", False)
        tdd_enabled = config.get("tdd_enabled", True)
        log_level = config.get("log_level", 2)
        level_names = {0: "OFF", 1: "ERROR", 2: "WARNING", 3: "INFO", 4: "DEBUG"}

        # Check for tempo session override in state
        tempo_session_override = None
        try:
            state = load_state(DEFAULT_STATE_PATH)
            if "tempo_session_enabled" in state:
                tempo_session_override = state["tempo_session_enabled"]
        except Exception:
            pass

        # Build status message
        status_text = "Pace Maker: ACTIVE" if enabled else "Pace Maker: INACTIVE"
        status_text += (
            f"\nWeekly Limit: {'ENABLED' if weekly_limit_enabled else 'DISABLED'}"
        )
        status_text += (
            f"\n5-Hour Limit: {'ENABLED' if five_hour_limit_enabled else 'DISABLED'}"
        )

        # Show tempo status with override if present
        if tempo_session_override is not None:
            tempo_status = "ENABLED" if tempo_session_override else "DISABLED"
            override_text = "ON" if tempo_session_override else "OFF"
            global_text = "ENABLED" if tempo_enabled else "DISABLED"
            status_text += f"\nTempo Tracking: {tempo_status} (session override: {override_text}, global: {global_text})"
        else:
            status_text += (
                f"\nTempo Tracking: {'ENABLED' if tempo_enabled else 'DISABLED'}"
            )

        status_text += f"\nSubagent Reminder: {'ENABLED' if subagent_reminder_enabled else 'DISABLED'}"
        status_text += f"\nIntent Validation: {'ENABLED' if intent_validation_enabled else 'DISABLED'}"
        status_text += f"\nTDD Enforcement: {'ENABLED' if tdd_enabled else 'DISABLED'}"
        status_text += (
            f"\nLog Level: {log_level} ({level_names.get(log_level, 'UNKNOWN')})"
        )

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


def _execute_version() -> Dict[str, Any]:
    """Display version information."""
    from . import __version__

    message_template = MESSAGES.get("version", {}).get(
        "message", "Claude Pace Maker v{version}"
    )
    return {
        "success": True,
        "message": message_template.replace("{version}", __version__),
    }


def _execute_help(config_path: str) -> Dict[str, Any]:
    """Display help text."""
    help_text = """Pace Maker - Credit-Aware Adaptive Throttling

COMMANDS:
  pace-maker on                   Enable pace maker throttling
  pace-maker off                  Disable pace maker throttling
  pace-maker status               Show current status and usage
  pace-maker version              Show version information
  pace-maker help                 Show this help message
  pace-maker weekly-limit on      Enable weekly (7-day) limit throttling
  pace-maker weekly-limit off     Disable weekly limit throttling
  pace-maker 5-hour-limit on      Enable 5-hour limit throttling
  pace-maker 5-hour-limit off     Disable 5-hour limit throttling
  pace-maker tempo on             Enable session lifecycle tracking (global)
  pace-maker tempo off            Disable session lifecycle tracking (global)
  pace-maker tempo session on     Enable tempo for this session only
  pace-maker tempo session off    Disable tempo for this session only
  pace-maker reminder on          Enable subagent reminder (Write/Edit nudge)
  pace-maker reminder off         Disable subagent reminder
  pace-maker intent-validation on Enable intent validation before code changes
  pace-maker intent-validation off Disable intent validation
  pace-maker tdd on               Enable TDD enforcement for core code changes
  pace-maker tdd off              Disable TDD enforcement
  pace-maker loglevel [0-4]      Set log level (0=OFF to 4=DEBUG)
  pace-maker clean-code list                    List all clean code validation rules
  pace-maker clean-code add --id ID --name NAME --description DESC
                                                Add a new validation rule
  pace-maker clean-code modify --id ID [--name NAME] [--description DESC]
                                                Modify an existing rule
  pace-maker clean-code remove --id ID         Remove a validation rule
  pace-maker core-paths list                   List all TDD-enforced core paths
  pace-maker core-paths add PATH               Add a new core path
  pace-maker core-paths remove PATH            Remove a core path

LOG LEVELS:
  0 = OFF      - No logging
  1 = ERROR    - Errors only
  2 = WARNING  - Warnings + Errors (default)
  3 = INFO     - Info + Warnings + Errors
  4 = DEBUG    - All messages including SDK calls

  Logs: ~/.claude-pace-maker/pace-maker.log

WEEKLY LIMIT:
  The weekly limiter uses weekend-aware throttling to pace your usage
  over 7-day windows. When enabled, it will slow down tool usage on
  weekends if you're ahead of the target pace.

5-HOUR LIMIT:
  The 5-hour limiter paces your usage within the rolling 5-hour window.
  When enabled, it will slow down tool usage if you're ahead of the
  target pace. When disabled, only the 7-day limit applies (if enabled).

TEMPO TRACKING:
  Session lifecycle tracking prevents Claude from prematurely ending
  implementation sessions. When Claude says IMPLEMENTATION_START,
  the Stop hook will require Claude to declare IMPLEMENTATION_COMPLETE
  before allowing the session to end.

  Global Control: 'pace-maker tempo on/off' sets the default for all sessions
  Session Control: 'pace-maker tempo session on/off' overrides the global
                   setting for the current session only

SUBAGENT REMINDER:
  When enabled, using Write or Edit tools in main context will trigger
  a reminder to delegate code changes to subagents (tdd-engineer,
  code-surgeon, etc). Also triggers every N tool executions (default: 5).

INTENT VALIDATION:
  When enabled, Claude must declare intent before modifying source code files.
  The pre-tool hook blocks Write/Edit operations on source files unless Claude
  has clearly stated: (1) what file is being modified, (2) what changes are
  being made, and (3) why/goal of the changes.

  Source code extensions are configured in:
  ~/.claude-pace-maker/source_code_extensions.json

TDD ENFORCEMENT:
  TDD enforcement is a sub-feature of intent validation. When both intent
  validation AND TDD are enabled, Claude must declare test coverage before
  modifying core code files (src/, core/, lib/, etc).

  TDD can be toggled independently from intent validation using:
  - 'pace-maker tdd on' to enable
  - 'pace-maker tdd off' to disable

  Note: TDD enforcement only works when intent validation is also enabled.

CLEAN CODE RULES:
  Manage validation rules that Claude checks before modifying source code.
  Rules are stored in: ~/.claude-pace-maker/clean_code_rules.yaml
  When intent validation is enabled, these rules are checked against all
  code changes to ensure quality standards are met.

CORE PATHS:
  Manage paths that require TDD enforcement. When both intent validation
  and TDD enforcement are enabled, files under these paths require test
  declarations before modification.

  Default paths: src/, lib/, core/, source/, libraries/, kernel/
  Config file: ~/.claude-pace-maker/core_paths.yaml

  Users can customize which paths trigger TDD requirements using:
  - 'pace-maker core-paths list' to see current paths
  - 'pace-maker core-paths add custom/' to add a new path
  - 'pace-maker core-paths remove lib/' to remove a path

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
            message = MESSAGES.get("weekly_limit", {}).get(
                "enabled",
                "✓ Weekly limit ENABLED\n7-day throttling will be applied based on weekday usage pace.",
            )
            return {
                "success": True,
                "message": message,
            }
        except Exception as e:
            error_template = MESSAGES.get("weekly_limit", {}).get(
                "error_enabling", "Error enabling weekly limit: {error}"
            )
            return {
                "success": False,
                "message": error_template.replace("{error}", str(e)),
            }
    elif subcommand == "off":
        try:
            config = _load_config(config_path)
            config["weekly_limit_enabled"] = False
            _write_config_atomic(config, config_path)
            message = MESSAGES.get("weekly_limit", {}).get(
                "disabled",
                "✓ Weekly limit DISABLED\n7-day throttling will be skipped (5-hour limit still applies).",
            )
            return {
                "success": True,
                "message": message,
            }
        except Exception as e:
            error_template = MESSAGES.get("weekly_limit", {}).get(
                "error_disabling", "Error disabling weekly limit: {error}"
            )
            return {
                "success": False,
                "message": error_template.replace("{error}", str(e)),
            }
    else:
        error_template = MESSAGES.get("weekly_limit", {}).get(
            "unknown_subcommand",
            "Unknown subcommand: {subcommand}\nUsage: pace-maker weekly-limit [on|off]",
        )
        return {
            "success": False,
            "message": error_template.replace("{subcommand}", str(subcommand)),
        }


def _execute_5_hour_limit(
    config_path: str, subcommand: Optional[str]
) -> Dict[str, Any]:
    """Enable or disable 5-hour limit throttling."""
    if subcommand == "on":
        try:
            config = _load_config(config_path)
            config["five_hour_limit_enabled"] = True
            _write_config_atomic(config, config_path)
            message = MESSAGES.get("five_hour_limit", {}).get(
                "enabled",
                "✓ 5-Hour limit ENABLED\n5-hour throttling will be applied based on usage pace.",
            )
            return {
                "success": True,
                "message": message,
            }
        except Exception as e:
            error_template = MESSAGES.get("five_hour_limit", {}).get(
                "error_enabling", "Error enabling 5-hour limit: {error}"
            )
            return {
                "success": False,
                "message": error_template.replace("{error}", str(e)),
            }
    elif subcommand == "off":
        try:
            config = _load_config(config_path)
            config["five_hour_limit_enabled"] = False
            _write_config_atomic(config, config_path)
            message = MESSAGES.get("five_hour_limit", {}).get(
                "disabled",
                "✓ 5-Hour limit DISABLED\n5-hour throttling will be skipped.",
            )
            return {
                "success": True,
                "message": message,
            }
        except Exception as e:
            error_template = MESSAGES.get("five_hour_limit", {}).get(
                "error_disabling", "Error disabling 5-hour limit: {error}"
            )
            return {
                "success": False,
                "message": error_template.replace("{error}", str(e)),
            }
    else:
        error_template = MESSAGES.get("five_hour_limit", {}).get(
            "unknown_subcommand",
            "Unknown subcommand: {subcommand}\nUsage: pace-maker 5-hour-limit [on|off]",
        )
        return {
            "success": False,
            "message": error_template.replace("{subcommand}", str(subcommand)),
        }


def _execute_loglevel(config_path: str, subcommand: Optional[str]) -> Dict[str, Any]:
    """Set log level (0-4)."""
    if subcommand is None:
        message = MESSAGES.get("loglevel", {}).get(
            "usage",
            "Usage: pace-maker loglevel [0-4]\n  0=OFF, 1=ERROR, 2=WARNING, 3=INFO, 4=DEBUG",
        )
        return {
            "success": False,
            "message": message,
        }

    try:
        level = int(subcommand)
        if level < 0 or level > 4:
            error_template = MESSAGES.get("loglevel", {}).get(
                "invalid_level", "Invalid log level: {level}. Must be 0-4."
            )
            return {
                "success": False,
                "message": error_template.replace("{level}", str(level)),
            }

        config = _load_config(config_path)
        config["log_level"] = level
        _write_config_atomic(config, config_path)

        level_names = {0: "OFF", 1: "ERROR", 2: "WARNING", 3: "INFO", 4: "DEBUG"}
        message_template = MESSAGES.get("loglevel", {}).get(
            "set_success",
            "✓ Log level set to {level} ({level_name})\nLogs: ~/.claude-pace-maker/pace-maker.log",
        )
        message = message_template.replace("{level}", str(level)).replace(
            "{level_name}", level_names[level]
        )
        return {
            "success": True,
            "message": message,
        }
    except Exception as e:
        error_template = MESSAGES.get("loglevel", {}).get(
            "error", "Error setting log level: {error}"
        )
        return {"success": False, "message": error_template.replace("{error}", str(e))}


def _execute_reminder(config_path: str, subcommand: Optional[str]) -> Dict[str, Any]:
    """Enable or disable subagent reminder (Write/Edit nudge in main context)."""
    if subcommand == "on":
        try:
            config = _load_config(config_path)
            config["subagent_reminder_enabled"] = True
            _write_config_atomic(config, config_path)
            message = MESSAGES.get("reminder", {}).get(
                "enabled",
                "✓ Subagent Reminder ENABLED\nWrite/Edit tool usage in main context will trigger a reminder to use subagents.",
            )
            return {
                "success": True,
                "message": message,
            }
        except Exception as e:
            error_template = MESSAGES.get("reminder", {}).get(
                "error_enabling", "Error enabling reminder: {error}"
            )
            return {
                "success": False,
                "message": error_template.replace("{error}", str(e)),
            }
    elif subcommand == "off":
        try:
            config = _load_config(config_path)
            config["subagent_reminder_enabled"] = False
            _write_config_atomic(config, config_path)
            message = MESSAGES.get("reminder", {}).get(
                "disabled",
                "✓ Subagent Reminder DISABLED\nWrite/Edit tool usage will not trigger reminders.",
            )
            return {
                "success": True,
                "message": message,
            }
        except Exception as e:
            error_template = MESSAGES.get("reminder", {}).get(
                "error_disabling", "Error disabling reminder: {error}"
            )
            return {
                "success": False,
                "message": error_template.replace("{error}", str(e)),
            }
    else:
        error_template = MESSAGES.get("reminder", {}).get(
            "unknown_subcommand",
            "Unknown subcommand: {subcommand}\nUsage: pace-maker reminder [on|off]",
        )
        return {
            "success": False,
            "message": error_template.replace("{subcommand}", str(subcommand)),
        }


def _execute_intent_validation(
    config_path: str, subcommand: Optional[str]
) -> Dict[str, Any]:
    """Enable or disable intent validation before code changes."""
    if subcommand == "on":
        try:
            config = _load_config(config_path)
            config["intent_validation_enabled"] = True
            _write_config_atomic(config, config_path)
            message = MESSAGES.get("intent_validation", {}).get(
                "enabled",
                "✓ Intent Validation ENABLED\nClaude must declare intent before modifying source code files.",
            )
            return {
                "success": True,
                "message": message,
            }
        except Exception as e:
            error_template = MESSAGES.get("intent_validation", {}).get(
                "error_enabling", "Error enabling intent validation: {error}"
            )
            return {
                "success": False,
                "message": error_template.replace("{error}", str(e)),
            }
    elif subcommand == "off":
        try:
            config = _load_config(config_path)
            config["intent_validation_enabled"] = False
            _write_config_atomic(config, config_path)
            message = MESSAGES.get("intent_validation", {}).get(
                "disabled",
                "✓ Intent Validation DISABLED\nCode modifications will not require intent declarations.",
            )
            return {
                "success": True,
                "message": message,
            }
        except Exception as e:
            error_template = MESSAGES.get("intent_validation", {}).get(
                "error_disabling", "Error disabling intent validation: {error}"
            )
            return {
                "success": False,
                "message": error_template.replace("{error}", str(e)),
            }
    else:
        error_template = MESSAGES.get("intent_validation", {}).get(
            "unknown_subcommand",
            "Unknown subcommand: {subcommand}\nUsage: pace-maker intent-validation [on|off]",
        )
        return {
            "success": False,
            "message": error_template.replace("{subcommand}", str(subcommand)),
        }


def _execute_tdd(config_path: str, subcommand: Optional[str]) -> Dict[str, Any]:
    """Enable or disable TDD enforcement."""
    if subcommand == "on":
        try:
            config = _load_config(config_path)
            config["tdd_enabled"] = True
            _write_config_atomic(config, config_path)
            message = MESSAGES.get("tdd", {}).get(
                "enabled",
                "✓ TDD Enforcement ENABLED\nIntent validation will require test declarations for core code changes.",
            )
            return {
                "success": True,
                "message": message,
            }
        except Exception as e:
            error_template = MESSAGES.get("tdd", {}).get(
                "error_enabling", "Error enabling TDD enforcement: {error}"
            )
            return {
                "success": False,
                "message": error_template.replace("{error}", str(e)),
            }
    elif subcommand == "off":
        try:
            config = _load_config(config_path)
            config["tdd_enabled"] = False
            _write_config_atomic(config, config_path)
            message = MESSAGES.get("tdd", {}).get(
                "disabled",
                "✓ TDD Enforcement DISABLED\nIntent validation will not require test declarations.",
            )
            return {
                "success": True,
                "message": message,
            }
        except Exception as e:
            error_template = MESSAGES.get("tdd", {}).get(
                "error_disabling", "Error disabling TDD enforcement: {error}"
            )
            return {
                "success": False,
                "message": error_template.replace("{error}", str(e)),
            }
    else:
        error_template = MESSAGES.get("tdd", {}).get(
            "unknown_subcommand",
            "Unknown subcommand: {subcommand}\nUsage: pace-maker tdd [on|off]",
        )
        return {
            "success": False,
            "message": error_template.replace("{subcommand}", str(subcommand)),
        }


def _execute_tempo(config_path: str, subcommand: Optional[str]) -> Dict[str, Any]:
    """Enable or disable tempo (session lifecycle) tracking."""
    from .constants import DEFAULT_STATE_PATH
    from .hook import load_state, save_state

    # Handle session-level commands
    if subcommand and subcommand.startswith("session "):
        session_cmd = subcommand.split(" ", 1)[1]  # Extract "on" or "off"

        if session_cmd == "on":
            try:
                state = load_state(DEFAULT_STATE_PATH)
                state["tempo_session_enabled"] = True
                save_state(state, DEFAULT_STATE_PATH)
                message = MESSAGES.get("tempo", {}).get(
                    "session_enabled",
                    "✓ Tempo tracking ENABLED for this session\nSession lifecycle tracking will prevent premature exits in this session only.",
                )
                return {
                    "success": True,
                    "message": message,
                }
            except Exception as e:
                error_template = MESSAGES.get("tempo", {}).get(
                    "error_session_enabling",
                    "Error enabling tempo for session: {error}",
                )
                return {
                    "success": False,
                    "message": error_template.replace("{error}", str(e)),
                }
        elif session_cmd == "off":
            try:
                state = load_state(DEFAULT_STATE_PATH)
                state["tempo_session_enabled"] = False
                save_state(state, DEFAULT_STATE_PATH)
                message = MESSAGES.get("tempo", {}).get(
                    "session_disabled",
                    "✓ Tempo tracking DISABLED for this session\nSession lifecycle tracking will not prevent exits in this session.",
                )
                return {
                    "success": True,
                    "message": message,
                }
            except Exception as e:
                error_template = MESSAGES.get("tempo", {}).get(
                    "error_session_disabling",
                    "Error disabling tempo for session: {error}",
                )
                return {
                    "success": False,
                    "message": error_template.replace("{error}", str(e)),
                }
        else:
            error_template = MESSAGES.get("tempo", {}).get(
                "unknown_subcommand",
                "Unknown subcommand: {subcommand}\nUsage: pace-maker tempo [on|off] or pace-maker tempo session [on|off]",
            )
            return {
                "success": False,
                "message": error_template.replace("{subcommand}", str(subcommand)),
            }

    # Handle global tempo commands
    if subcommand == "on":
        try:
            config = _load_config(config_path)
            config["tempo_enabled"] = True
            _write_config_atomic(config, config_path)
            message = MESSAGES.get("tempo", {}).get(
                "enabled",
                "✓ Tempo tracking ENABLED\nSession lifecycle tracking will prevent premature session exits during implementations.",
            )
            return {
                "success": True,
                "message": message,
            }
        except Exception as e:
            error_template = MESSAGES.get("tempo", {}).get(
                "error_enabling", "Error enabling tempo: {error}"
            )
            return {
                "success": False,
                "message": error_template.replace("{error}", str(e)),
            }
    elif subcommand == "off":
        try:
            config = _load_config(config_path)
            config["tempo_enabled"] = False
            _write_config_atomic(config, config_path)
            message = MESSAGES.get("tempo", {}).get(
                "disabled",
                "✓ Tempo tracking DISABLED\nSession lifecycle tracking will not prevent session exits.",
            )
            return {
                "success": True,
                "message": message,
            }
        except Exception as e:
            error_template = MESSAGES.get("tempo", {}).get(
                "error_disabling", "Error disabling tempo: {error}"
            )
            return {
                "success": False,
                "message": error_template.replace("{error}", str(e)),
            }
    else:
        error_template = MESSAGES.get("tempo", {}).get(
            "unknown_subcommand",
            "Unknown subcommand: {subcommand}\nUsage: pace-maker tempo [on|off] or pace-maker tempo session [on|off]",
        )
        return {
            "success": False,
            "message": error_template.replace("{subcommand}", str(subcommand)),
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
    except Exception as e:
        log_warning("user_commands", "Failed to get latest usage from database", e)
        return None


def _execute_clean_code(subcommand: Optional[str]) -> Dict[str, Any]:
    """Execute clean-code subcommands."""
    from .constants import DEFAULT_CLEAN_CODE_RULES_PATH
    from . import clean_code_rules

    if not subcommand:
        return {
            "success": False,
            "message": "clean-code requires a subcommand: list, add, modify, remove",
        }

    # Parse subcommand
    parts = subcommand.split(None, 1)
    action = parts[0]

    if action == "list":
        try:
            rules = clean_code_rules.load_rules(
                DEFAULT_CLEAN_CODE_RULES_PATH, strict=True
            )
            formatted = clean_code_rules.format_rules_for_display(rules)
            return {"success": True, "message": formatted}
        except ValueError as e:
            return {
                "success": False,
                "message": f"Error loading clean code rules config:\n{str(e)}\n\nPlease check: {DEFAULT_CLEAN_CODE_RULES_PATH}",
            }
        except Exception as e:
            return {"success": False, "message": f"Error listing rules: {str(e)}"}

    elif action == "add":
        # Parse --id, --name, --description from command
        if len(parts) < 2:
            return {
                "success": False,
                "message": "Usage: pace-maker clean-code add --id ID --name NAME --description DESC",
            }

        args_str = parts[1]
        rule_data = _parse_rule_args(args_str)

        if "error" in rule_data:
            return {"success": False, "message": rule_data["error"]}

        try:
            clean_code_rules.add_rule(DEFAULT_CLEAN_CODE_RULES_PATH, rule_data)
            return {
                "success": True,
                "message": f"✓ Rule '{rule_data['id']}' added successfully",
            }
        except Exception as e:
            return {"success": False, "message": f"Error adding rule: {str(e)}"}

    elif action == "modify":
        # Parse --id and other fields
        if len(parts) < 2:
            return {
                "success": False,
                "message": "Usage: pace-maker clean-code modify --id ID [--name NAME] [--description DESC]",
            }

        args_str = parts[1]
        parsed = _parse_rule_args(args_str, require_all=False)

        if "error" in parsed:
            return {"success": False, "message": parsed["error"]}

        if "id" not in parsed:
            return {
                "success": False,
                "message": "Error: --id is required for modify command",
            }

        rule_id = parsed.pop("id")
        updates = parsed

        try:
            clean_code_rules.modify_rule(
                DEFAULT_CLEAN_CODE_RULES_PATH, rule_id, updates
            )
            return {
                "success": True,
                "message": f"✓ Rule '{rule_id}' modified successfully",
            }
        except ValueError as e:
            return {"success": False, "message": str(e)}
        except Exception as e:
            return {"success": False, "message": f"Error modifying rule: {str(e)}"}

    elif action == "remove":
        # Parse --id
        if len(parts) < 2:
            return {
                "success": False,
                "message": "Usage: pace-maker clean-code remove --id ID",
            }

        args_str = parts[1]
        parsed = _parse_rule_args(args_str, require_all=False)

        if "error" in parsed:
            return {"success": False, "message": parsed["error"]}

        if "id" not in parsed:
            return {
                "success": False,
                "message": "Error: --id is required for remove command",
            }

        rule_id = parsed["id"]

        try:
            clean_code_rules.remove_rule(DEFAULT_CLEAN_CODE_RULES_PATH, rule_id)
            return {
                "success": True,
                "message": f"✓ Rule '{rule_id}' removed successfully",
            }
        except ValueError as e:
            return {"success": False, "message": str(e)}
        except Exception as e:
            return {"success": False, "message": f"Error removing rule: {str(e)}"}

    else:
        return {"success": False, "message": f"Unknown clean-code subcommand: {action}"}


def _execute_core_paths(subcommand: Optional[str]) -> Dict[str, Any]:
    """Execute core-paths subcommands."""
    from .constants import DEFAULT_CORE_PATHS_PATH
    from . import core_paths

    if not subcommand:
        return {
            "success": False,
            "message": "core-paths requires a subcommand: list, add, remove",
        }

    # Parse subcommand
    parts = subcommand.split(None, 1)
    action = parts[0]

    if action == "list":
        try:
            paths = core_paths.load_paths(DEFAULT_CORE_PATHS_PATH, strict=True)
            formatted = core_paths.format_paths_for_display(paths)
            return {"success": True, "message": formatted}
        except ValueError as e:
            return {
                "success": False,
                "message": f"Error loading core paths config:\n{str(e)}\n\nPlease check: {DEFAULT_CORE_PATHS_PATH}",
            }
        except Exception as e:
            return {"success": False, "message": f"Error listing paths: {str(e)}"}

    elif action == "add":
        # Parse path from command
        if len(parts) < 2:
            return {
                "success": False,
                "message": "Usage: pace-maker core-paths add PATH",
            }

        path = parts[1].strip()

        try:
            core_paths.add_path(DEFAULT_CORE_PATHS_PATH, path)
            normalized = path if path.endswith("/") else path + "/"
            return {
                "success": True,
                "message": f"✓ Core path '{normalized}' added successfully",
            }
        except ValueError as e:
            return {"success": False, "message": str(e)}
        except Exception as e:
            return {"success": False, "message": f"Error adding path: {str(e)}"}

    elif action == "remove":
        # Parse path from command
        if len(parts) < 2:
            return {
                "success": False,
                "message": "Usage: pace-maker core-paths remove PATH",
            }

        path = parts[1].strip()

        try:
            core_paths.remove_path(DEFAULT_CORE_PATHS_PATH, path)
            return {
                "success": True,
                "message": f"✓ Core path '{path}' removed successfully",
            }
        except ValueError as e:
            return {"success": False, "message": str(e)}
        except Exception as e:
            return {"success": False, "message": f"Error removing path: {str(e)}"}

    else:
        return {"success": False, "message": f"Unknown core-paths subcommand: {action}"}


def _parse_rule_args(args_str: str, require_all: bool = True) -> Dict[str, str]:
    """
    Parse --id, --name, --description from argument string.

    Args:
        args_str: Argument string like "--id test --name Test --description Desc"
        require_all: If True, require all three fields; if False, accept partial

    Returns:
        Dictionary with parsed fields or {"error": "message"}
    """
    result = {}

    # Simple regex-based parsing
    import re

    # Extract --id VALUE
    id_match = re.search(r'--id\s+"([^"]+)"', args_str) or re.search(
        r"--id\s+(\S+)", args_str
    )
    if id_match:
        result["id"] = id_match.group(1)

    # Extract --name VALUE
    name_match = re.search(r'--name\s+"([^"]+)"', args_str) or re.search(
        r"--name\s+([^-]+)", args_str
    )
    if name_match:
        result["name"] = name_match.group(1).strip()

    # Extract --description VALUE
    desc_match = re.search(r'--description\s+"([^"]+)"', args_str) or re.search(
        r"--description\s+(.+)$", args_str
    )
    if desc_match:
        result["description"] = desc_match.group(1).strip()

    if require_all:
        if "id" not in result or "name" not in result or "description" not in result:
            return {"error": "Error: --id, --name, and --description are all required"}

    return result


def main():
    """CLI entry point for pace-maker command."""
    import sys
    import argparse

    parser = argparse.ArgumentParser(
        prog="pace-maker",
        description="Claude Pace Maker - Credit-Aware Adaptive Throttling",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  pace-maker on                      Enable pace maker throttling
  pace-maker off                     Disable pace maker throttling
  pace-maker status                  Show current status and usage
  pace-maker weekly-limit on         Enable weekly (7-day) limit throttling
  pace-maker tempo session on        Enable tempo for current session only
  pace-maker reminder off            Disable subagent reminder
  pace-maker intent-validation on    Enable intent validation

For more information, run: pace-maker help
        """,
    )

    # Main commands
    parser.add_argument(
        "command",
        choices=[
            "on",
            "off",
            "status",
            "help",
            "version",
            "weekly-limit",
            "5-hour-limit",
            "tempo",
            "reminder",
            "intent-validation",
            "tdd",
            "loglevel",
            "clean-code",
            "core-paths",
        ],
        help="Command to execute",
    )

    # Optional subcommand (for weekly-limit, tempo, reminder, intent-validation, loglevel)
    parser.add_argument(
        "subcommand",
        nargs="*",
        help="Subcommand (on|off|session) for weekly-limit, tempo, reminder, intent-validation or log level (0-4) for loglevel",
    )

    # Parse arguments
    args = parser.parse_args()

    # Import constants for default paths
    from .constants import DEFAULT_CONFIG_PATH, DEFAULT_DB_PATH

    # Join subcommand args into a single string (for "tempo session on")
    subcommand_str = " ".join(args.subcommand) if args.subcommand else None

    # Execute command
    result = execute_command(
        command=args.command,
        config_path=DEFAULT_CONFIG_PATH,
        db_path=DEFAULT_DB_PATH,
        subcommand=subcommand_str,
    )

    # Print message
    print(result["message"])

    # Exit with appropriate code
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
