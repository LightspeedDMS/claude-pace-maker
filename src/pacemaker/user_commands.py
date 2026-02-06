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


# Pre-compiled regex for log error parsing
_ERROR_LOG_PATTERN = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[ERROR\]")

# ANSI color codes for terminal output
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_RED = "\033[31m"
ANSI_RESET = "\033[0m"


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

    # Pattern 4: pace-maker tempo (on|off|auto) - global tempo control
    pattern_tempo = r"^pace-maker\s+tempo\s+(on|off|auto)$"
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

    # Pattern 16: pace-maker excluded-paths list
    pattern_excluded_paths_list = r"^pace-maker\s+excluded-paths\s+list$"
    match_excluded_paths_list = re.match(pattern_excluded_paths_list, normalized)

    if match_excluded_paths_list:
        return {
            "is_pace_maker_command": True,
            "command": "excluded-paths",
            "subcommand": "list",
        }

    # Pattern 17: pace-maker excluded-paths add PATH
    pattern_excluded_paths_add = r"^pace-maker\s+excluded-paths\s+add\s+(.+)$"
    match_excluded_paths_add = re.match(pattern_excluded_paths_add, normalized)

    if match_excluded_paths_add:
        return {
            "is_pace_maker_command": True,
            "command": "excluded-paths",
            "subcommand": f"add {match_excluded_paths_add.group(1)}",
        }

    # Pattern 18: pace-maker excluded-paths remove PATH
    pattern_excluded_paths_remove = r"^pace-maker\s+excluded-paths\s+remove\s+(.+)$"
    match_excluded_paths_remove = re.match(pattern_excluded_paths_remove, normalized)

    if match_excluded_paths_remove:
        return {
            "is_pace_maker_command": True,
            "command": "excluded-paths",
            "subcommand": f"remove {match_excluded_paths_remove.group(1)}",
        }

    # Pattern 19: pace-maker prefer-model (opus|sonnet|haiku|auto)
    pattern_prefer_model = r"^pace-maker\s+prefer-model\s+(opus|sonnet|haiku|auto)$"
    match_prefer_model = re.match(pattern_prefer_model, normalized)

    if match_prefer_model:
        return {
            "is_pace_maker_command": True,
            "command": "prefer-model",
            "subcommand": match_prefer_model.group(1),
        }

    # Pattern 20: pace-maker langfuse (config|on|off|status) [args...]
    pattern_langfuse = r"^pace-maker\s+langfuse\s+(.+)$"
    match_langfuse = re.match(pattern_langfuse, normalized)

    if match_langfuse:
        return {
            "is_pace_maker_command": True,
            "command": "langfuse",
            "subcommand": match_langfuse.group(1),
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
    elif command == "excluded-paths":
        return _execute_excluded_paths(subcommand)
    elif command == "prefer-model":
        return _execute_prefer_model(config_path, subcommand)
    elif command == "langfuse":
        return _execute_langfuse(config_path, subcommand)
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


def _count_recent_errors(log_path: str, hours: int = 24) -> int:
    """
    Count ERROR-level log entries from the last N hours.

    Scans rotated log files (today's and yesterday's) for errors.

    Args:
        log_path: Base log path (ignored, uses rotated files)
        hours: Number of hours to look back (default: 24)

    Returns:
        Count of ERROR entries within the time window
    """
    from datetime import datetime, timedelta
    from .logger import get_recent_log_paths

    try:
        cutoff_time = datetime.now() - timedelta(hours=hours)
        error_count = 0

        # Get log files for the last 2 days (covers 24-hour window)
        log_files = get_recent_log_paths(days=2)

        if not log_files:
            return 0

        for log_file in log_files:
            try:
                with open(log_file, "r") as f:
                    for line in f:
                        match = _ERROR_LOG_PATTERN.match(line)
                        if match:
                            try:
                                timestamp_str = match.group(1)
                                timestamp = datetime.strptime(
                                    timestamp_str, "%Y-%m-%d %H:%M:%S"
                                )
                                if timestamp >= cutoff_time:
                                    error_count += 1
                            except ValueError:
                                continue
            except (OSError, IOError):
                continue  # Skip files that can't be read

        return error_count
    except Exception as e:
        from .logger import log_warning

        log_warning("_count_recent_errors", "Error counting errors", e)
        return 0


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
        subagent_reminder_enabled = config.get("subagent_reminder_enabled", True)
        intent_validation_enabled = config.get("intent_validation_enabled", False)
        tdd_enabled = config.get("tdd_enabled", True)
        preferred_model = config.get("preferred_subagent_model", "auto")
        log_level = config.get("log_level", 2)
        level_names = {0: "OFF", 1: "ERROR", 2: "WARNING", 3: "INFO", 4: "DEBUG"}

        # Get tempo_mode with backward compatibility
        from .hook import format_elapsed_time

        tempo_mode = config.get("tempo_mode")
        if tempo_mode is None:
            # Backward compat: check old tempo_enabled
            tempo_enabled = config.get("tempo_enabled")
            if tempo_enabled is not None:
                tempo_mode = "on" if tempo_enabled else "off"
            else:
                tempo_mode = "auto"  # Default

        # Check for tempo session override and last interaction in state
        tempo_session_override = None
        last_user_interaction = None
        try:
            state = load_state(DEFAULT_STATE_PATH)
            if "tempo_session_enabled" in state:
                tempo_session_override = state["tempo_session_enabled"]
            last_user_interaction = state.get("last_user_interaction_time")
        except Exception:
            # State file may not exist on fresh install - use defaults (None values)
            pass

        # Build status message
        status_text = "Pace Maker: ACTIVE" if enabled else "Pace Maker: INACTIVE"
        status_text += (
            f"\nWeekly Limit: {'ENABLED' if weekly_limit_enabled else 'DISABLED'}"
        )
        status_text += (
            f"\n5-Hour Limit: {'ENABLED' if five_hour_limit_enabled else 'DISABLED'}"
        )

        # Show tempo status with mode details
        # Only show session override if it's actually overriding (set to True)
        # When session override is False or None, show mode-specific details
        if tempo_session_override is True:
            # Session override forcing tempo ON regardless of mode
            status_text += f"\nTempo Tracking: ENABLED (session override: ON, global: {tempo_mode.upper()})"
        elif tempo_mode == "auto":
            # Auto mode - show detailed status
            threshold = config.get("auto_tempo_threshold_minutes", 10)
            elapsed_str = format_elapsed_time(last_user_interaction)

            # Show auto mode header
            if tempo_session_override is False:
                # Session explicitly disabled, but show auto mode would do
                status_text += (
                    "\nTempo Tracking: DISABLED (session override: OFF, global: AUTO)"
                )
                status_text += "\nAuto Mode Details (if session override removed):"
                status_text += f"\n  Threshold: {threshold} min"
                status_text += f"\n  Last User Interaction: {elapsed_str}"
            else:
                # No session override, show active auto mode
                status_text += f"\nTempo Mode: AUTO (threshold: {threshold} min)"
                status_text += f"\nLast User Interaction: {elapsed_str}"

            # Determine if tempo would be engaged (if not overridden)
            if last_user_interaction is None:
                engagement_status = "ENGAGED (no interaction recorded)"
            else:
                from datetime import datetime

                elapsed_minutes = (
                    datetime.now() - last_user_interaction
                ).total_seconds() / 60
                if elapsed_minutes >= threshold:
                    engagement_status = "ENGAGED (user inactive)"
                else:
                    engagement_status = "PAUSED (user active)"

            if tempo_session_override is False:
                status_text += f"\n  Would Be: {engagement_status}"
            else:
                status_text += f"\nTempo Status: {engagement_status}"
        else:
            # On or off mode
            if tempo_session_override is False:
                status_text += f"\nTempo Tracking: DISABLED (session override: OFF, global: {tempo_mode.upper()})"
            else:
                status_text += f"\nTempo Mode: {tempo_mode.upper()}"
                if tempo_mode == "on":
                    status_text += " (always engaged)"
                elif tempo_mode == "off":
                    status_text += " (disabled)"

        status_text += f"\nSubagent Reminder: {'ENABLED' if subagent_reminder_enabled else 'DISABLED'}"
        status_text += f"\nIntent Validation: {'ENABLED' if intent_validation_enabled else 'DISABLED'}"
        status_text += f"\nTDD Enforcement: {'ENABLED' if tdd_enabled else 'DISABLED'}"
        status_text += f"\nModel Preference: {preferred_model.upper()}"
        status_text += (
            f"\nLog Level: {log_level} ({level_names.get(log_level, 'UNKNOWN')})"
        )

        # Add version information
        from . import __version__ as pacemaker_version

        status_text += f"\nPace Maker: v{pacemaker_version}"

        # Try to get Usage Console version
        try:
            from claude_usage import __version__ as usage_version

            status_text += f"\nUsage Console: v{usage_version}"
        except ImportError:
            status_text += "\nUsage Console: not installed"

        # Add Langfuse status
        langfuse_enabled = config.get("langfuse_enabled", False)
        status_text += f"\nLangfuse: {'ENABLED' if langfuse_enabled else 'DISABLED'}"

        # If Langfuse is enabled, show connectivity status
        if langfuse_enabled:
            connection_result = _langfuse_test_connection(config)
            if connection_result["connected"]:
                status_text += (
                    f"\n  {ANSI_GREEN}✓ {connection_result['message']}{ANSI_RESET}"
                )
            else:
                status_text += (
                    f"\n  {ANSI_RED}✗ {connection_result['message']}{ANSI_RESET}"
                )

        # Add 24-hour error count from logs
        from .constants import DEFAULT_LOG_PATH

        error_count = _count_recent_errors(DEFAULT_LOG_PATH, hours=24)

        # Handle different error count scenarios
        if error_count == -1:
            # Log file too large to scan
            status_text += (
                f"\n24-Hour Errors: {ANSI_YELLOW}(log too large to scan){ANSI_RESET}"
            )
        elif error_count == 0:
            status_text += (
                f"\n24-Hour Errors: {ANSI_GREEN}{error_count} errors{ANSI_RESET}"
            )
        elif error_count <= 10:
            status_text += (
                f"\n24-Hour Errors: {ANSI_YELLOW}{error_count} errors{ANSI_RESET}"
            )
        else:
            status_text += (
                f"\n24-Hour Errors: {ANSI_RED}{error_count} errors{ANSI_RESET}"
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

        # Add blockage statistics section
        status_text += _format_blockage_stats(db_path)

        return {
            "success": True,
            "message": status_text,
            "enabled": enabled,
            "usage_data": usage_data,
        }
    except Exception as e:
        return {"success": False, "message": f"Error getting status: {str(e)}"}


def _format_blockage_stats(db_path: Optional[str]) -> str:
    """
    Format blockage statistics for status display.

    Args:
        db_path: Path to the database file

    Returns:
        Formatted string with blockage statistics section
    """
    from .constants import BLOCKAGE_CATEGORY_LABELS
    from . import database

    result = "\n\nBlockages (last hour):"

    try:
        stats = database.get_hourly_blockage_stats(db_path)
        total = 0

        # Display each category with human-readable label (except 'other')
        for category in [
            "intent_validation",
            "intent_validation_tdd",
            "intent_validation_cleancode",
            "pacing_tempo",
            "pacing_quota",
        ]:
            label = BLOCKAGE_CATEGORY_LABELS.get(category, category)
            count = stats.get(category, 0)
            total += count
            result += f"\n  {label}:{count:>10}"

        # Add separator and total
        result += "\n  " + "-" * 25
        result += f"\n  {'Total:':<18}{total:>7}"

    except Exception as e:
        result += "\n  (unavailable)"
        log_warning("user_commands", "Failed to get blockage stats", e)

    return result


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
  pace-maker tempo on             Enable session lifecycle tracking (global, always on)
  pace-maker tempo off            Disable session lifecycle tracking (global, always off)
  pace-maker tempo auto           Enable auto mode (engages after inactivity threshold)
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
  pace-maker excluded-paths list               List all folders excluded from TDD
  pace-maker excluded-paths add PATH           Add a new excluded path
  pace-maker excluded-paths remove PATH        Remove an excluded path
  pace-maker prefer-model opus                 Prefer Opus model for subagents
  pace-maker prefer-model sonnet               Prefer Sonnet model for subagents
  pace-maker prefer-model haiku                Prefer Haiku model for subagents
  pace-maker prefer-model auto                 Use default model selection (no preference)
  pace-maker langfuse config <url> <public_key> <secret_key>
                                               Configure Langfuse credentials
  pace-maker langfuse on                       Enable Langfuse telemetry collection
  pace-maker langfuse off                      Disable Langfuse telemetry collection
  pace-maker langfuse status                   Show Langfuse configuration and connection status

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

  Modes:
  - ON: Tempo always engaged (validates every session exit)
  - OFF: Tempo disabled (allows all exits without validation)
  - AUTO: Tempo engages only after user inactivity (DEFAULT)
    * Tracks last user interaction via UserPromptSubmit hook
    * Engages after configurable threshold (default: 10 minutes)
    * Active users get uninterrupted conversations
    * Unattended operations get automatic protection

  Global Control: 'pace-maker tempo on/off/auto' sets mode for all sessions
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

EXCLUDED PATHS:
  Manage folders excluded from TDD enforcement. Files in excluded paths
  skip TDD requirements but still require intent declaration.

  Default exclusions: .tmp/, test/, tests/, fixtures/, __pycache__/,
                      node_modules/, vendor/, dist/, build/, .git/
  Config file: ~/.claude-pace-maker/excluded_paths.yaml

  Users can customize excluded paths using:
  - 'pace-maker excluded-paths list' to see current exclusions
  - 'pace-maker excluded-paths add .generated/' to add a new exclusion
  - 'pace-maker excluded-paths remove .tmp/' to remove an exclusion

  Use cases: Temporary files, generated code, test fixtures, build artifacts

MODEL PREFERENCE (Quota Balancing):
  Control which model Claude prefers for subagent Task tool calls.
  Use this to balance quota usage between Opus and Sonnet.

  Example scenario:
  - 7-day usage at 82%, Sonnet usage at 96%
  - Set 'pace-maker prefer-model opus' to use more Opus tokens
  - This helps sync quota consumption across models

  Modes:
  - opus: Nudge to use model: "opus" in Task tool calls
  - sonnet: Nudge to use model: "sonnet" in Task tool calls
  - haiku: Nudge to use model: "haiku" in Task tool calls
  - auto: No preference, use default behavior (DEFAULT)

  Nudges appear in:
  - Session start message (with usage stats)
  - Post-tool subagent reminders

  Note: Main conversation model cannot be changed mid-session.
  To change main model, restart with: claude --model opus

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


def _execute_prefer_model(
    config_path: str, subcommand: Optional[str]
) -> Dict[str, Any]:
    """Set preferred subagent model for quota balancing."""
    valid_models = ["opus", "sonnet", "haiku", "auto"]

    if subcommand not in valid_models:
        return {
            "success": False,
            "message": f"Invalid model: {subcommand}\nUsage: pace-maker prefer-model [opus|sonnet|haiku|auto]",
        }

    try:
        config = _load_config(config_path)
        config["preferred_subagent_model"] = subcommand
        _write_config_atomic(config, config_path)

        if subcommand == "auto":
            message = (
                "✓ Model preference set to AUTO\n"
                "Subagents will use default model selection (inherit from parent)."
            )
        else:
            message = (
                f"✓ Preferred subagent model set to {subcommand.upper()}\n"
                f"Session start and post-tool reminders will nudge to use model: '{subcommand}' in Task tool calls.\n"
                f"Note: Main session model cannot be changed mid-conversation - restart with `claude --model {subcommand}` if needed."
            )
        return {"success": True, "message": message}
    except Exception as e:
        return {
            "success": False,
            "message": f"Error setting model preference: {str(e)}",
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
            config["tempo_mode"] = "on"
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
            config["tempo_mode"] = "off"
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
    elif subcommand == "auto":
        try:
            config = _load_config(config_path)
            config["tempo_mode"] = "auto"
            _write_config_atomic(config, config_path)
            threshold = config.get("auto_tempo_threshold_minutes", 10)
            message = MESSAGES.get("tempo", {}).get(
                "auto_enabled",
                f"✓ Tempo tracking set to AUTO mode\nTempo will engage after {threshold} minutes of user inactivity.",
            )
            return {
                "success": True,
                "message": message,
            }
        except Exception as e:
            error_template = MESSAGES.get("tempo", {}).get(
                "error_auto", "Error setting tempo to auto: {error}"
            )
            return {
                "success": False,
                "message": error_template.replace("{error}", str(e)),
            }
    else:
        error_template = MESSAGES.get("tempo", {}).get(
            "unknown_subcommand",
            "Unknown subcommand: {subcommand}\nUsage: pace-maker tempo [on|off|auto] or pace-maker tempo session [on|off]",
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


def _execute_excluded_paths(subcommand: Optional[str]) -> Dict[str, Any]:
    """Execute excluded-paths subcommands."""
    from .constants import DEFAULT_EXCLUDED_PATHS_PATH
    from . import excluded_paths

    if not subcommand:
        return {
            "success": False,
            "message": "excluded-paths requires a subcommand: list, add, remove",
        }

    # Parse subcommand
    parts = subcommand.split(None, 1)
    action = parts[0]

    if action == "list":
        try:
            exclusions = excluded_paths.load_exclusions(DEFAULT_EXCLUDED_PATHS_PATH)
            formatted = excluded_paths.format_exclusions_for_display(exclusions)
            return {"success": True, "message": formatted}
        except Exception as e:
            return {"success": False, "message": f"Error listing exclusions: {str(e)}"}

    elif action == "add":
        # Parse path from command
        if len(parts) < 2:
            return {
                "success": False,
                "message": "Usage: pace-maker excluded-paths add PATH",
            }

        path = parts[1].strip()

        try:
            excluded_paths.add_exclusion(DEFAULT_EXCLUDED_PATHS_PATH, path)
            normalized = path if path.endswith("/") else path + "/"
            return {
                "success": True,
                "message": f"✓ Excluded path '{normalized}' added successfully",
            }
        except ValueError as e:
            return {"success": False, "message": str(e)}
        except Exception as e:
            return {"success": False, "message": f"Error adding exclusion: {str(e)}"}

    elif action == "remove":
        # Parse path from command
        if len(parts) < 2:
            return {
                "success": False,
                "message": "Usage: pace-maker excluded-paths remove PATH",
            }

        path = parts[1].strip()

        try:
            excluded_paths.remove_exclusion(DEFAULT_EXCLUDED_PATHS_PATH, path)
            return {
                "success": True,
                "message": f"✓ Excluded path '{path}' removed successfully",
            }
        except ValueError as e:
            return {"success": False, "message": str(e)}
        except Exception as e:
            return {"success": False, "message": f"Error removing exclusion: {str(e)}"}

    else:
        return {
            "success": False,
            "message": f"Unknown excluded-paths subcommand: {action}",
        }


def _execute_langfuse(config_path: str, subcommand: Optional[str]) -> Dict[str, Any]:
    """
    Execute langfuse subcommands (dispatcher).

    Routes to appropriate handler based on subcommand.
    """
    if not subcommand:
        return {
            "success": False,
            "message": "langfuse requires a subcommand: config, on, off, status",
        }

    # Parse subcommand
    parts = subcommand.split(None, 3)  # Split into max 4 parts for config command
    action = parts[0]

    # Route to appropriate handler
    if action == "config":
        return _langfuse_config(config_path, parts)
    elif action == "on":
        return _langfuse_on(config_path)
    elif action == "off":
        return _langfuse_off(config_path)
    elif action == "status":
        return _langfuse_status(config_path)
    elif action == "filter":
        return _langfuse_filter(config_path, parts)
    elif action == "backfill":
        return _langfuse_backfill(config_path, parts)
    elif action == "stats":
        return _langfuse_stats(config_path, parts)
    else:
        return {
            "success": False,
            "message": f"Unknown langfuse subcommand: {action}\nUsage: pace-maker langfuse [config|on|off|status|filter|backfill|stats]",
        }


def _langfuse_filter(config_path: str, parts: list) -> Dict[str, Any]:
    """Handle 'langfuse filter [--max-result-size N] [--redact on|off]' command."""
    try:
        config = _load_config(config_path)

        # If no arguments, show current settings
        if len(parts) == 1:
            max_size = config.get("langfuse_max_result_size", 10240)
            redact = config.get("langfuse_redact_secrets", True)

            status_text = "Langfuse Filter Settings:"
            status_text += f"\n  Max Result Size: {max_size} bytes"
            status_text += f"\n  Secret Redaction: {'ON' if redact else 'OFF'}"

            return {
                "success": True,
                "message": status_text,
            }

        # Parse arguments
        args_str = " ".join(parts[1:])

        # Extract --max-result-size
        max_size_match = re.search(r"--max-result-size\s+(\d+)", args_str)
        if max_size_match:
            config["langfuse_max_result_size"] = int(max_size_match.group(1))

        # Extract --redact on|off
        redact_match = re.search(r"--redact\s+(on|off)", args_str)
        if redact_match:
            config["langfuse_redact_secrets"] = redact_match.group(1) == "on"

        # Write config
        _write_config_atomic(config, config_path)

        # Build confirmation message
        message = "✓ Langfuse filter settings updated:"
        if max_size_match:
            message += (
                f"\n  Max Result Size: {config['langfuse_max_result_size']} bytes"
            )
        if redact_match:
            message += f"\n  Secret Redaction: {'ON' if config['langfuse_redact_secrets'] else 'OFF'}"

        return {
            "success": True,
            "message": message,
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Error configuring filter: {str(e)}",
        }


def _parse_backfill_date(parts: list) -> tuple:
    """Parse --since date from command parts. Returns (date, error_dict or None)."""
    from datetime import datetime

    args_str = " ".join(parts[1:]) if len(parts) > 1 else ""
    since_match = re.search(r"--since\s+(\d{4}-\d{2}-\d{2})", args_str)

    if not since_match:
        return None, {
            "success": False,
            "message": "Usage: pace-maker langfuse backfill --since YYYY-MM-DD",
        }

    try:
        return datetime.strptime(since_match.group(1), "%Y-%m-%d"), None
    except ValueError:
        return None, {
            "success": False,
            "message": "Invalid date format. Use YYYY-MM-DD",
        }


def _langfuse_backfill(config_path: str, parts: list) -> Dict[str, Any]:
    """Handle 'langfuse backfill --since YYYY-MM-DD' command."""
    from pathlib import Path
    from .langfuse.backfill import backfill_sessions

    since_date, error = _parse_backfill_date(parts)
    if error:
        return error

    config = _load_config(config_path)
    base_url = config.get("langfuse_base_url")
    public_key = config.get("langfuse_public_key")
    secret_key = config.get("langfuse_secret_key")

    if not all([base_url, public_key, secret_key]):
        return {
            "success": False,
            "message": "Langfuse not configured. Run: pace-maker langfuse config",
        }

    if not config.get("langfuse_enabled", False):
        return {
            "success": False,
            "message": "Langfuse disabled. Run: pace-maker langfuse on",
        }

    claude_projects = Path.home() / ".claude" / "projects"
    if not claude_projects.exists():
        return {"success": False, "message": f"No Claude projects at {claude_projects}"}

    totals = {"total": 0, "success": 0, "failed": 0, "skipped": 0}
    print(f"Scanning projects since {since_date.strftime('%Y-%m-%d')}...")

    for project_dir in claude_projects.iterdir():
        if project_dir.is_dir():
            result = backfill_sessions(
                str(project_dir),
                since_date,
                base_url,
                public_key,
                secret_key,
                progress=True,
            )
            for key in totals:
                totals[key] += result[key]

    summary = f"✓ Backfill: {totals['total']} processed, {totals['success']} new, {totals['skipped']} skipped"
    if totals["failed"] > 0:
        summary += f", {totals['failed']} failed"
    return {"success": True, "message": summary}


def _langfuse_config(config_path: str, parts: list) -> Dict[str, Any]:
    """Handle 'langfuse config <base_url> <public_key> <secret_key>' command."""
    if len(parts) < 4:
        return {
            "success": False,
            "message": "Usage: pace-maker langfuse config <base_url> <public_key> <secret_key>",
        }

    base_url = parts[1]
    public_key = parts[2]
    secret_key = parts[3]

    try:
        # Load existing config
        config = _load_config(config_path)

        # Store credentials (CRITICAL: Never log secret_key)
        config["langfuse_base_url"] = base_url
        config["langfuse_public_key"] = public_key
        config["langfuse_secret_key"] = secret_key

        # Write atomically
        _write_config_atomic(config, config_path)

        return {
            "success": True,
            "message": "✓ Langfuse configuration saved successfully\nCredentials stored securely in config.",
        }
    except Exception as e:
        # CRITICAL: Never log the secret_key in error messages
        return {
            "success": False,
            "message": f"Error saving Langfuse configuration: {str(e)}",
        }


def _langfuse_on(config_path: str) -> Dict[str, Any]:
    """Handle 'langfuse on' command."""
    try:
        config = _load_config(config_path)
        config["langfuse_enabled"] = True
        _write_config_atomic(config, config_path)
        return {
            "success": True,
            "message": "✓ Langfuse telemetry ENABLED\nSession data will be pushed to Langfuse at session end.",
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Error enabling Langfuse: {str(e)}",
        }


def _langfuse_off(config_path: str) -> Dict[str, Any]:
    """Handle 'langfuse off' command."""
    try:
        config = _load_config(config_path)
        config["langfuse_enabled"] = False
        _write_config_atomic(config, config_path)
        return {
            "success": True,
            "message": "✓ Langfuse telemetry DISABLED\nSession data will not be pushed to Langfuse.",
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Error disabling Langfuse: {str(e)}",
        }


def _langfuse_test_connection(config: dict) -> Dict[str, Any]:
    """Test Langfuse API connection."""
    from .langfuse import client

    base_url = config.get("langfuse_base_url")
    public_key = config.get("langfuse_public_key")
    secret_key = config.get("langfuse_secret_key")

    # Validate credentials configured
    if not base_url or not public_key or not secret_key:
        return {"connected": False, "message": "Credentials not configured"}

    # Test connection with 5-second timeout
    return client.test_connection(base_url, public_key, secret_key, timeout=5)


def _langfuse_status(config_path: str) -> Dict[str, Any]:
    """Handle 'langfuse status' command."""
    try:
        config = _load_config(config_path)
        enabled = config.get("langfuse_enabled", False)
        base_url = config.get("langfuse_base_url", "Not configured")
        public_key = config.get("langfuse_public_key", "Not configured")
        # CRITICAL: Never display secret_key in status output

        status_text = f"Langfuse: {'ENABLED' if enabled else 'DISABLED'}"
        status_text += f"\nBase URL: {base_url}"
        status_text += f"\nPublic Key: {public_key}"

        # AC3: Test connection
        connection_result = _langfuse_test_connection(config)
        if connection_result["connected"]:
            status_text += f"\nConnection: ✓ {connection_result['message']}"
        else:
            status_text += f"\nConnection: ✗ {connection_result['message']}"

        return {
            "success": True,
            "message": status_text,
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Error getting Langfuse status: {str(e)}",
        }


def _langfuse_stats(config_path: str, parts: list) -> Dict[str, Any]:
    """
    Handle 'langfuse stats [--week]' command (Story #33).

    Args:
        config_path: Path to config file
        parts: Command parts (parts[0] = 'stats', parts[1:] = flags)

    Returns:
        Dict with success and message
    """
    from .langfuse import stats

    try:
        # Load config
        config = _load_config(config_path)

        # Check if Langfuse is configured
        base_url = config.get("langfuse_base_url")
        public_key = config.get("langfuse_public_key")
        secret_key = config.get("langfuse_secret_key")

        if not all([base_url, public_key, secret_key]):
            return {
                "success": False,
                "message": "Langfuse not configured. Run: pace-maker langfuse config <url> <public_key> <secret_key>",
            }

        # Check if Langfuse is enabled
        if not config.get("langfuse_enabled", False):
            return {
                "success": False,
                "message": "Langfuse disabled. Run: pace-maker langfuse on",
            }

        # Check for --week flag
        is_weekly = len(parts) > 1 and "--week" in " ".join(parts[1:])

        if is_weekly:
            # AC2: Weekly breakdown
            output = stats.get_weekly_breakdown(base_url, public_key, secret_key)
        else:
            # AC1: Daily summary
            output = stats.get_daily_summary(base_url, public_key, secret_key)

        # AC4: Always return success=True even if API failed (graceful fallback)
        return {"success": True, "message": output}

    except Exception as e:
        # AC4: Even unexpected errors should return success=True with error message
        return {"success": True, "message": f"Error fetching stats: {str(e)}"}


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
            "excluded-paths",
            "prefer-model",
            "langfuse",
        ],
        help="Command to execute",
    )

    # Optional subcommand (for weekly-limit, tempo, reminder, intent-validation, loglevel)
    parser.add_argument(
        "subcommand",
        nargs="*",
        help="Subcommand (on|off|session) for weekly-limit, tempo, reminder, intent-validation or log level (0-4) for loglevel",
    )

    # Parse arguments - use parse_known_args to allow -- flags to pass through
    args, unknown = parser.parse_known_args()

    # Import constants for default paths
    from .constants import DEFAULT_CONFIG_PATH, DEFAULT_DB_PATH

    # Combine known subcommand args with unknown args (for flags like --since, --max-result-size)
    all_parts = (args.subcommand or []) + unknown
    subcommand_str = " ".join(all_parts) if all_parts else None

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
