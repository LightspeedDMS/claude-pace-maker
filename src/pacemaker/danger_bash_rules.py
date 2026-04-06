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
from typing import Any, Dict, List, Optional

from .logger import log_warning

VALID_CATEGORIES: frozenset = frozenset({"work_destruction", "system_destruction"})
MAX_PATTERN_DISPLAY_LEN: int = 80

# Path to bundled default rules YAML, co-located with this module
_DEFAULT_YAML = os.path.join(
    os.path.dirname(__file__), "danger_bash_rules_default.yaml"
)


def _validate_rule_id(rule_id: str) -> None:
    """
    Validate a rule ID string.

    Args:
        rule_id: The rule ID to validate

    Raises:
        ValueError: If rule_id is empty, whitespace-only, or contains whitespace
    """
    if not rule_id or not rule_id.strip():
        raise ValueError("rule_id must not be empty or whitespace-only")
    if any(c.isspace() for c in rule_id):
        raise ValueError(f"rule_id must not contain whitespace: {rule_id!r}")


def _write_config(config_path: str, custom_config: Dict[str, Any]) -> None:
    """
    Write custom config to YAML file atomically.

    Uses a temp file (config_path + '.tmp') then os.replace() for atomicity.
    Cleans up the temp file if the replace fails.

    Args:
        config_path: Path to YAML config file
        custom_config: Dict with 'rules' and 'deleted_rules' keys

    Raises:
        OSError: If the file cannot be written
    """
    dirname = os.path.dirname(config_path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)

    tmp_path = config_path + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            yaml.safe_dump(
                {
                    "rules": custom_config["rules"],
                    "deleted_rules": custom_config["deleted_rules"],
                },
                f,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )
        os.replace(tmp_path, config_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


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
        if raw["id"] in default_ids:
            log_warning(
                "danger_bash_rules",
                f"Custom rule '{raw['id']}' has the same ID as a default rule — skipping",
            )
        else:
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


_ALLOWED_MODIFY_FIELDS = frozenset({"description", "category"})
_REQUIRED_RULE_KEYS = frozenset({"id", "pattern", "category", "description"})


def add_rule(config_path: str, rule: Dict[str, Any]) -> None:
    """
    Add a custom rule to the config.

    Args:
        config_path: Path to user YAML config file
        rule: Dict with id (str), pattern (str), category (str), description (str)

    Raises:
        ValueError: If required keys are missing or not strings, rule_id is invalid,
                    pattern is invalid regex, category is not in VALID_CATEGORIES,
                    rule_id matches a default rule, or a custom rule with the same
                    ID already exists
    """
    missing = _REQUIRED_RULE_KEYS - set(rule.keys())
    if missing:
        raise ValueError(f"Rule is missing required keys: {sorted(missing)}")

    for key in _REQUIRED_RULE_KEYS:
        if not isinstance(rule[key], str):
            raise ValueError(
                f"Rule field '{key}' must be a string, got {type(rule[key]).__name__}"
            )

    _validate_rule_id(rule["id"])

    try:
        re.compile(rule["pattern"])
    except re.error as e:
        raise ValueError(f"Invalid pattern regex: {e}") from e

    if rule["category"] not in VALID_CATEGORIES:
        raise ValueError(
            f"Invalid category {rule['category']!r}. Must be one of: {sorted(VALID_CATEGORIES)}"
        )

    default_ids = {r["id"] for r in load_default_rules()}
    if rule["id"] in default_ids:
        raise ValueError(
            f"Rule id '{rule['id']}' matches a default rule. "
            "Default rules cannot be replaced; choose a different id or suppress the default first."
        )

    custom_config = _load_custom_config(config_path)
    if any(r["id"] == rule["id"] for r in custom_config["rules"]):
        raise ValueError(
            f"Duplicate rule id '{rule['id']}': already exists in custom config"
        )

    custom_config["rules"].append(rule)
    _write_config(config_path, custom_config)


def restore_rule(config_path: str, rule_id: str) -> None:
    """
    Restore a deleted default rule.

    Args:
        config_path: Path to user YAML config file
        rule_id: ID of the default rule to restore

    Raises:
        ValueError: If rule_id is not a default rule, or is not currently deleted
    """
    default_ids = {r["id"] for r in load_default_rules()}
    if rule_id not in default_ids:
        raise ValueError(f"'{rule_id}' is not a default rule and cannot be restored")

    custom_config = _load_custom_config(config_path)
    if rule_id not in custom_config["deleted_rules"]:
        raise ValueError(f"'{rule_id}' is not in deleted_rules — nothing to restore")

    custom_config["deleted_rules"].remove(rule_id)
    _write_config(config_path, custom_config)


def remove_rule(config_path: str, rule_id: str) -> None:
    """
    Remove a rule by ID.

    For default rules: adds rule_id to deleted_rules (suppression).
    For custom rules: removes from rules list (no suppression marker).

    Args:
        config_path: Path to user YAML config file
        rule_id: ID of the rule to remove

    Raises:
        ValueError: If rule is already deleted or not found
    """
    custom_config = _load_custom_config(config_path)
    default_ids = {r["id"] for r in load_default_rules()}
    custom_ids = {r["id"] for r in custom_config["rules"]}

    if rule_id in set(custom_config["deleted_rules"]):
        raise ValueError(f"Rule '{rule_id}' is already deleted")

    if rule_id not in default_ids and rule_id not in custom_ids:
        raise ValueError(f"Rule '{rule_id}' not found")

    custom_config["rules"] = [r for r in custom_config["rules"] if r["id"] != rule_id]

    if rule_id in default_ids:
        custom_config["deleted_rules"].append(rule_id)

    _write_config(config_path, custom_config)


def modify_rule(config_path: str, rule_id: str, updates: Dict[str, Any]) -> None:
    """
    Modify a custom rule's description and/or category.

    Only 'description' and 'category' fields may be updated.
    The 'id' key in updates is silently ignored.
    The 'pattern' key in updates raises ValueError.
    Only custom rules (not default rules) may be modified.

    Args:
        config_path: Path to user YAML config file
        rule_id: ID of the custom rule to modify
        updates: Dict of fields to update (only 'description' and 'category' allowed)

    Raises:
        ValueError: If rule_id is a default rule, updates contain 'pattern',
                    category value is invalid, or rule_id is not found
    """
    if "pattern" in updates:
        raise ValueError(
            "Field 'pattern' cannot be modified. Remove and re-add the rule to change its pattern."
        )

    # Silently drop 'id' from updates — cannot change rule identity
    safe_updates = {k: v for k, v in updates.items() if k != "id"}

    disallowed = set(safe_updates.keys()) - _ALLOWED_MODIFY_FIELDS
    if disallowed:
        raise ValueError(f"Fields not allowed in updates: {sorted(disallowed)}")

    if "category" in safe_updates and safe_updates["category"] not in VALID_CATEGORIES:
        raise ValueError(
            f"Invalid category {safe_updates['category']!r}. Must be one of: {sorted(VALID_CATEGORIES)}"
        )

    default_ids = {r["id"] for r in load_default_rules()}
    if rule_id in default_ids:
        raise ValueError(
            f"Rule '{rule_id}' is a default rule. Only custom rules can be modified."
        )

    custom_config = _load_custom_config(config_path)
    for rule in custom_config["rules"]:
        if rule["id"] == rule_id:
            rule.update(safe_updates)
            _write_config(config_path, custom_config)
            return

    raise ValueError(f"Rule '{rule_id}' not found in custom config")


def format_rules_for_display(
    rules: List[Dict[str, Any]], config_path: Optional[str] = None
) -> str:
    """
    Format rules for CLI display output.

    Rules are sorted stably by ID. When config_path is provided, each rule
    is tagged with its source: [default] or [custom]. Pattern strings are
    truncated at MAX_PATTERN_DISPLAY_LEN characters with '...' appended.

    Args:
        rules: List of runtime rule dicts (with compiled pattern objects)
        config_path: Optional path to config file for source tagging

    Returns:
        Formatted string for display, or "No rules configured." if empty
    """
    if not rules:
        return "No rules configured."

    metadata: Dict[str, str] = {}
    if config_path:
        for m in get_rules_metadata(config_path):
            metadata[m["id"]] = m["source"]

    sorted_rules = sorted(rules, key=lambda r: r.get("id", ""))

    output = []
    for rule in sorted_rules:
        rid = rule.get("id", "N/A")
        source_tag = f" [{metadata.get(rid, 'default')}]" if metadata else ""
        pattern_obj = rule.get("pattern")
        pattern_str = (
            pattern_obj.pattern if hasattr(pattern_obj, "pattern") else str(pattern_obj)
        )
        if len(pattern_str) > MAX_PATTERN_DISPLAY_LEN:
            pattern_str = pattern_str[:MAX_PATTERN_DISPLAY_LEN] + "..."
        output.append(f"ID: {rid}{source_tag}")
        output.append(f"  Pattern: {pattern_str}")
        output.append(f"  Category: {rule.get('category', 'N/A')}")
        output.append(f"  Description: {rule.get('description', 'N/A')}")
        output.append("")

    return "\n".join(output).rstrip()
