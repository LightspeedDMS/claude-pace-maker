#!/usr/bin/env python3
"""
Danger Bash Rules Management Module.

Provides load and match operations for dangerous bash command rules:
- Load rules from bundled YAML defaults
- Merge with user customizations from ~/.claude-pace-maker/danger_bash_rules.yaml
- Pre-compile regex patterns at load time for performance
- Match commands against compiled rules

Merge Strategy (same as clean_code_rules):
- Default rules are loaded from the bundled YAML, never from user config
- User config stores only additions and deletion markers
- Deleted defaults are suppressed; custom rules (new IDs) are appended after defaults
"""

import os
import re
import yaml
from typing import Any, Dict, List

from .logger import log_warning

# Path to bundled default rules YAML, co-located with this module
_DEFAULT_YAML = os.path.join(
    os.path.dirname(__file__), "danger_bash_rules_default.yaml"
)


def load_default_rules() -> List[Dict[str, str]]:
    """
    Load default danger bash rules from the bundled YAML file.

    Returns raw dicts with string patterns (not compiled).
    Each rule has: id, pattern (str), category, description.

    Returns:
        List of rule dicts with string fields

    Raises:
        RuntimeError: If the bundled YAML is missing or malformed
    """
    try:
        with open(_DEFAULT_YAML, "r") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as e:
        raise RuntimeError(
            f"Failed to load bundled danger_bash_rules_default.yaml: {e}"
        ) from e

    rules = data.get("rules", []) if data else []
    return [r for r in rules if isinstance(r, dict) and "id" in r]


def _load_custom_config(config_path: str) -> Dict[str, Any]:
    """
    Load user customizations from YAML file.

    Returns a dict with:
      'rules'         — list of custom/addition rule dicts (string patterns)
      'deleted_rules' — list of default rule IDs to suppress

    Args:
        config_path: Path to user YAML config file

    Returns:
        Dict with keys 'rules' and 'deleted_rules'
    """
    if not os.path.exists(config_path):
        return {"rules": [], "deleted_rules": []}

    try:
        with open(config_path, "r") as f:
            config_data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        log_warning(
            "danger_bash_rules", "Failed to parse YAML config, using defaults", e
        )
        return {"rules": [], "deleted_rules": []}

    if config_data is None:
        return {"rules": [], "deleted_rules": []}

    rules = config_data.get("rules", [])
    if not isinstance(rules, list):
        rules = []

    deleted = config_data.get("deleted_rules", [])
    if not isinstance(deleted, list):
        deleted = []

    return {
        "rules": [r for r in rules if isinstance(r, dict) and "id" in r],
        "deleted_rules": [d for d in deleted if isinstance(d, str)],
    }


def _compile_rule(raw: Dict[str, str], source: str) -> Dict[str, Any]:
    """
    Compile a raw rule dict into a runtime rule dict.

    Replaces the string 'pattern' field with a compiled regex object
    and adds a 'source' field.

    Args:
        raw:    Raw rule dict with string fields
        source: Source label ('default' or 'custom')

    Returns:
        Runtime rule dict with compiled pattern and source field
    """
    return {
        "id": raw["id"],
        "pattern": re.compile(raw["pattern"]),
        "category": raw.get("category", ""),
        "description": raw.get("description", ""),
        "source": source,
    }


def load_rules(config_path: str) -> List[Dict[str, Any]]:
    """
    Load danger bash rules using merge strategy.

    Merges bundled defaults with user customizations:
    - Defaults appear first
    - Deleted defaults are suppressed
    - Custom rules (new IDs) are appended after defaults
    - All patterns are pre-compiled at load time

    Args:
        config_path: Path to user YAML config file (need not exist)

    Returns:
        List of runtime rule dicts with compiled patterns and source fields

    Raises:
        ValueError: If config_path is not a non-empty string
    """
    if not isinstance(config_path, str) or not config_path:
        raise ValueError("config_path must be a non-empty string")

    defaults = load_default_rules()
    default_ids = {r["id"] for r in defaults}
    custom_config = _load_custom_config(config_path)
    deleted_ids = set(custom_config["deleted_rules"])

    merged = [
        _compile_rule(raw, "default")
        for raw in defaults
        if raw["id"] not in deleted_ids
    ]

    for raw in custom_config["rules"]:
        if raw["id"] not in default_ids:
            merged.append(_compile_rule(raw, "custom"))

    return merged


def get_rules_metadata(config_path: str) -> List[Dict[str, str]]:
    """
    Get metadata about each active rule's source (default/custom).

    Deleted defaults are excluded. Custom rules added by the user
    are included with source='custom'.

    Source values:
    - 'default': rule comes from bundled YAML unmodified
    - 'custom':  rule not in defaults, added by user

    Args:
        config_path: Path to user YAML config file (need not exist)

    Returns:
        List of dicts with 'id' and 'source' keys

    Raises:
        ValueError: If config_path is not a non-empty string
    """
    if not isinstance(config_path, str) or not config_path:
        raise ValueError("config_path must be a non-empty string")

    defaults = load_default_rules()
    default_ids = {r["id"] for r in defaults}
    custom_config = _load_custom_config(config_path)
    deleted_ids = set(custom_config["deleted_rules"])

    result = [
        {"id": r["id"], "source": "default"}
        for r in defaults
        if r["id"] not in deleted_ids
    ]

    for r in custom_config["rules"]:
        if r["id"] not in default_ids:
            result.append({"id": r["id"], "source": "custom"})

    return result


def match_command(command: str, rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Check a command string against all compiled rules.

    Args:
        command: The bash command string to check
        rules:   List of runtime rule dicts from load_rules()

    Returns:
        List of rule dicts (with id, category, description, source) that matched.
        Returns empty list if no rules matched.

    Raises:
        ValueError: If command is not a string or rules is not a list
    """
    if not isinstance(command, str):
        raise ValueError("command must be a string")
    if not isinstance(rules, list):
        raise ValueError("rules must be a list")

    return [
        {
            "id": rule["id"],
            "category": rule["category"],
            "description": rule["description"],
            "source": rule["source"],
        }
        for rule in rules
        if rule["pattern"].search(command)
    ]
