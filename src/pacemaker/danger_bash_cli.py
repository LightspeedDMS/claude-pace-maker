"""CLI helper for danger bash rule management commands.

Dispatches `pace-maker danger-bash <subcommand>` to the appropriate
CRUD function in danger_bash_rules.py. Keeps user_commands.py thin.
"""

import json
import os
import re
from typing import Dict

from .danger_bash_rules import (
    add_rule,
    format_rules_for_display,
    load_rules,
    modify_rule,
    remove_rule,
    restore_rule,
)

# Config path for danger bash rules YAML
DEFAULT_DANGER_BASH_CONFIG = os.path.expanduser(
    "~/.claude-pace-maker/danger_bash_rules.yaml"
)

# Config path for main pace-maker config
DEFAULT_CONFIG_PATH = os.path.expanduser("~/.claude-pace-maker/config.json")


def _parse_named_args(args_str: str) -> Dict[str, str]:
    """Parse --key value pairs from a CLI argument string.

    Supports both `--key value` and `--key 'value with spaces'` forms.
    Single-quoted values preserve backslashes (important for regex patterns).

    Returns:
        Dict mapping key names (without --) to values.
    """
    result = {}
    # Match --key followed by either a quoted string or a non-whitespace token
    pattern = re.compile(r"--(\w[\w-]*)\s+(?:'([^']*)'|\"([^\"]*)\"|(\S+))")
    for match in pattern.finditer(args_str):
        key = match.group(1)
        value = match.group(2) or match.group(3) or match.group(4)
        result[key] = value
    return result


def _load_main_config() -> dict:
    """Load the main pace-maker config.json."""
    try:
        with open(DEFAULT_CONFIG_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_main_config(config: dict) -> None:
    """Save the main pace-maker config.json atomically."""
    dirname = os.path.dirname(DEFAULT_CONFIG_PATH)
    os.makedirs(dirname, exist_ok=True)
    temp_path = DEFAULT_CONFIG_PATH + ".tmp"
    try:
        with open(temp_path, "w") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
        os.replace(temp_path, DEFAULT_CONFIG_PATH)
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def execute(subcommand: str, args_str: str = "") -> Dict:
    """Execute a danger-bash CLI subcommand.

    Args:
        subcommand: One of list, add, remove, restore, modify, on, off
        args_str: Remaining CLI arguments as a string

    Returns:
        Dict with "success" bool and "message" str
    """
    config_path = DEFAULT_DANGER_BASH_CONFIG

    try:
        if subcommand == "list":
            return _cmd_list(config_path)
        elif subcommand == "add":
            return _cmd_add(config_path, args_str)
        elif subcommand == "remove":
            return _cmd_remove(config_path, args_str)
        elif subcommand == "restore":
            return _cmd_restore(config_path, args_str)
        elif subcommand == "modify":
            return _cmd_modify(config_path, args_str)
        elif subcommand == "on":
            return _cmd_toggle(True)
        elif subcommand == "off":
            return _cmd_toggle(False)
        else:
            return {
                "success": False,
                "message": (
                    "danger-bash requires a subcommand: "
                    "list, add, remove, restore, modify, on, off"
                ),
            }
    except ValueError as e:
        return {"success": False, "message": str(e)}
    except Exception as e:
        return {"success": False, "message": f"Unexpected error: {e}"}


def _cmd_list(config_path: str) -> Dict:
    """List all danger bash rules."""
    rules = load_rules(config_path)
    output = format_rules_for_display(rules, config_path=config_path)
    wd = sum(1 for r in rules if r["category"] == "work_destruction")
    sd = sum(1 for r in rules if r["category"] == "system_destruction")
    return {
        "success": True,
        "message": f"{output}\nTotal: {len(rules)} rules ({wd} WD, {sd} SD)",
    }


def _cmd_add(config_path: str, args_str: str) -> Dict:
    """Add a custom danger bash rule."""
    parsed = _parse_named_args(args_str)
    required = {"id", "pattern", "category", "description"}
    missing = required - set(parsed.keys())
    if missing:
        return {
            "success": False,
            "message": (
                f"Missing required arguments: {', '.join(f'--{k}' for k in sorted(missing))}\n"
                f"Usage: pace-maker danger-bash add "
                f"--id ID --pattern 'REGEX' --category CATEGORY --description 'DESC'"
            ),
        }
    rule = {
        "id": parsed["id"],
        "pattern": parsed["pattern"],
        "category": parsed["category"],
        "description": parsed["description"],
    }
    add_rule(config_path, rule)
    return {"success": True, "message": f"Added rule {parsed['id']}"}


def _cmd_remove(config_path: str, args_str: str) -> Dict:
    """Remove a danger bash rule."""
    parsed = _parse_named_args(args_str)
    if "id" not in parsed:
        return {
            "success": False,
            "message": "Missing required argument: --id\n"
            "Usage: pace-maker danger-bash remove --id RULE_ID",
        }
    remove_rule(config_path, parsed["id"])
    return {"success": True, "message": f"Removed rule {parsed['id']}"}


def _cmd_restore(config_path: str, args_str: str) -> Dict:
    """Restore a deleted default rule."""
    parsed = _parse_named_args(args_str)
    if "id" not in parsed:
        return {
            "success": False,
            "message": "Missing required argument: --id\n"
            "Usage: pace-maker danger-bash restore --id RULE_ID",
        }
    restore_rule(config_path, parsed["id"])
    return {"success": True, "message": f"Restored default rule {parsed['id']}"}


def _cmd_modify(config_path: str, args_str: str) -> Dict:
    """Modify a custom danger bash rule."""
    parsed = _parse_named_args(args_str)
    if "id" not in parsed:
        return {
            "success": False,
            "message": "Missing required argument: --id\n"
            "Usage: pace-maker danger-bash modify --id RULE_ID "
            "[--description 'DESC'] [--category CATEGORY]",
        }
    rule_id = parsed.pop("id")
    if not parsed:
        return {
            "success": False,
            "message": "Nothing to modify. Provide --description and/or --category.",
        }
    modify_rule(config_path, rule_id, parsed)
    return {"success": True, "message": f"Modified rule {rule_id}"}


def _cmd_toggle(enabled: bool) -> Dict:
    """Toggle danger_bash_enabled in config.json."""
    config = _load_main_config()
    config["danger_bash_enabled"] = enabled
    _save_main_config(config)
    state = "enabled" if enabled else "disabled"
    return {"success": True, "message": f"Danger bash validation {state}"}
