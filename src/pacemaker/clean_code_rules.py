#!/usr/bin/env python3
"""
Clean Code Rules Management Module.

Provides CRUD operations for managing clean code validation rules:
- Load rules from YAML config or use defaults
- Add, modify, and remove rules
- Format rules for display and validation prompts
"""

import os
import yaml
from typing import List, Dict

from .logger import log_warning


def get_default_rules() -> List[Dict[str, str]]:
    """
    Get default clean code rules (hardcoded).

    These rules are extracted from the pre_tool_validator_prompt.md
    and represent the baseline validation standards.

    Returns:
        List of rule dictionaries with id, name, and description fields
    """
    return [
        {
            "id": "hardcoded-secrets",
            "name": "No Hardcoded Secrets",
            "description": "Hardcoded secrets (API keys, passwords, tokens, credentials)",
        },
        {
            "id": "sql-injection",
            "name": "Prevent SQL Injection",
            "description": "SQL injection vulnerability (string concatenation in queries)",
        },
        {
            "id": "bare-except",
            "name": "No Bare Except Clauses",
            "description": "Bare except clauses (must catch specific exceptions)",
        },
        {
            "id": "swallowed-exceptions",
            "name": "No Swallowed Exceptions",
            "description": "Silently swallowed exceptions (must log or re-raise)",
        },
        {
            "id": "commented-code",
            "name": "No Commented-Out Code",
            "description": "Commented-out code blocks (delete or document WHY)",
        },
        {
            "id": "magic-numbers",
            "name": "No Magic Numbers",
            "description": "Magic numbers (use named constants)",
        },
        {
            "id": "mutable-defaults",
            "name": "No Mutable Default Arguments",
            "description": "Mutable default arguments (Python: def func(items=[]):)",
        },
        {
            "id": "deep-nesting",
            "name": "Avoid Deep Nesting",
            "description": "Overnested if statements (excessive indentation)",
        },
        {
            "id": "logic-bugs",
            "name": "No Blatant Logic Bugs",
            "description": "Blatant logic bugs not aligned with intent",
        },
        {
            "id": "boundary-checks",
            "name": "Include Boundary Checks",
            "description": "Missing boundary checks (null/None, overflows, bounds)",
        },
        {
            "id": "missing-comments",
            "name": "Comment Complex Code",
            "description": "Lack of comments in complicated/brittle code",
        },
        {
            "id": "undeclared-fallbacks",
            "name": "No Undeclared Fallbacks",
            "description": "Introduction of undeclared and/or undesireable fallbacks. Remember the golden rule: graceful failure over forced success",
        },
        {
            "id": "over-mocking",
            "name": "Avoid Over-Mocking Tests",
            "description": "When writing tests, we don't want the core area being tested to be mocked.",
        },
        {
            "id": "large-files",
            "name": "No Large Files",
            "description": "Large files. No more than ~500 lines per source code file.",
        },
        {
            "id": "large-blobs",
            "name": "No Large Code Blobs",
            "description": "Large-blobs of code written at once.",
        },
        {
            "id": "large-methods",
            "name": "No Large Methods",
            "description": "Large methods. An individual method should never exceed the size about ~50 lines.",
        },
        {
            "id": "too-many-units",
            "name": "No Too Many Units At Once",
            "description": "Too many units/pieces of code written at a time (more than three methods, more than one class)",
        },
    ]


def load_rules(config_path: str, strict: bool = False) -> List[Dict[str, str]]:
    """
    Load clean code rules from YAML config file.

    Falls back to default rules when:
    - Config file doesn't exist (both strict and non-strict)
    - Config file has invalid YAML (non-strict only)
    - Config file missing 'rules' key (both strict and non-strict)
    - Rules list is empty (both strict and non-strict)

    If strict=True, raises exception ONLY on YAML parsing errors.

    Args:
        config_path: Path to YAML config file with "rules" key
        strict: If True, raise exception on YAML syntax errors; if False, return defaults

    Returns:
        List of rule dictionaries (e.g., [{"id": "...", "name": "...", "description": "..."}])

    Raises:
        ValueError: If strict=True and YAML file has syntax errors
    """
    # Try to load from config file
    try:
        # Missing file → always return defaults (even in strict mode)
        if not os.path.exists(config_path):
            return get_default_rules()

        with open(config_path, "r") as f:
            config_data = yaml.safe_load(f)

        # Empty file → return defaults (even in strict mode)
        if config_data is None:
            return get_default_rules()

        rules = config_data.get("rules", [])

        # Missing 'rules' key or not a list → return defaults (even in strict mode)
        if not isinstance(rules, list):
            return get_default_rules()

        # Empty rules list → return defaults (even in strict mode)
        if len(rules) == 0:
            return get_default_rules()

        return rules

    except yaml.YAMLError as e:
        # YAML parsing error → strict mode raises, non-strict returns defaults
        if strict:
            raise ValueError(f"Invalid YAML syntax in config file:\n{str(e)}") from e
        log_warning(
            "clean_code_rules", "Failed to parse YAML config, using defaults", e
        )
        return get_default_rules()
    except OSError as e:
        # File I/O error → always log and return defaults
        log_warning("clean_code_rules", "Failed to read config file, using defaults", e)
        return get_default_rules()


def add_rule(config_path: str, rule: Dict[str, str]) -> None:
    """
    Add a new clean code rule to the YAML config file.

    Creates the config file with defaults if it doesn't exist.

    Args:
        config_path: Path to YAML config file
        rule: Dictionary with id, name, and description fields
    """
    # Load existing rules or get defaults
    rules = load_rules(config_path)

    # Append new rule
    rules.append(rule)

    # Write back to file
    _write_rules(config_path, rules)


def _write_rules(config_path: str, rules: List[Dict[str, str]]) -> None:
    """
    Write rules list to YAML config file.

    Args:
        config_path: Path to YAML config file
        rules: List of rule dictionaries
    """
    os.makedirs(os.path.dirname(config_path), exist_ok=True)

    with open(config_path, "w") as f:
        yaml.safe_dump({"rules": rules}, f, default_flow_style=False, sort_keys=False)


def modify_rule(config_path: str, rule_id: str, updates: Dict[str, str]) -> None:
    """
    Modify an existing clean code rule in the YAML config file.

    Args:
        config_path: Path to YAML config file
        rule_id: ID of the rule to modify
        updates: Dictionary of fields to update (e.g., {"description": "New desc"})

    Raises:
        ValueError: If rule with given ID is not found
    """
    # Load existing rules
    rules = load_rules(config_path)

    # Find and update rule
    found = False
    for rule in rules:
        if rule.get("id") == rule_id:
            rule.update(updates)
            found = True
            break

    if not found:
        raise ValueError(f"Rule with id '{rule_id}' not found")

    # Write back to file
    _write_rules(config_path, rules)


def remove_rule(config_path: str, rule_id: str) -> None:
    """
    Remove a clean code rule from the YAML config file.

    Args:
        config_path: Path to YAML config file
        rule_id: ID of the rule to remove

    Raises:
        ValueError: If rule with given ID is not found
    """
    # Load existing rules
    rules = load_rules(config_path)

    # Filter out the rule to remove
    filtered_rules = [rule for rule in rules if rule.get("id") != rule_id]

    if len(filtered_rules) == len(rules):
        raise ValueError(f"Rule with id '{rule_id}' not found")

    # Write back to file
    _write_rules(config_path, filtered_rules)


def format_rules_for_display(rules: List[Dict[str, str]]) -> str:
    """
    Format rules for CLI display output.

    Args:
        rules: List of rule dictionaries

    Returns:
        Formatted string for display
    """
    if not rules:
        return "No rules configured."

    output = []
    for rule in rules:
        output.append(f"ID: {rule.get('id', 'N/A')}")
        output.append(f"  Name: {rule.get('name', 'N/A')}")
        output.append(f"  Description: {rule.get('description', 'N/A')}")
        output.append("")  # Empty line between rules

    return "\n".join(output).rstrip()


def format_rules_for_validation(rules: List[Dict[str, str]]) -> str:
    """
    Format rules for validation prompt injection.

    Args:
        rules: List of rule dictionaries

    Returns:
        Formatted string for validation prompt
    """
    if not rules:
        return "   - No custom rules configured (using defaults)"

    formatted = []
    for rule in rules:
        formatted.append(f"   - {rule.get('description', 'N/A')}")

    return "\n".join(formatted)
