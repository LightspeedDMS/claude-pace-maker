#!/usr/bin/env python3
"""
Clean Code Rules Management Module.

Provides CRUD operations for managing clean code validation rules:
- Load rules from YAML config or use defaults
- Add, modify, and remove rules
- Format rules for display and validation prompts

Merge Strategy (Story #55):
- Default rules are never stored in config; config stores only overrides/deletions
- load_rules merges defaults with custom config (overrides + custom appended)
- deleted_rules list suppresses defaults without being stored in rules list
- Migration strips old snapshot copies of defaults from existing configs
"""

import os
import yaml
from typing import List, Dict, Optional

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
            "description": "Silently swallowed exceptions (must log or re-raise). Also: unchecked return values from non-void functions. Every error must be explicitly handled — LOG+THROW, LOG+RECOVER, or EXPLICIT DISCARD",
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
            "description": "Introduction of undeclared fallbacks: alternative code paths, 'just in case' logic, legacy support without explicit approval. Golden rule: graceful failure over forced success",
        },
        {
            "id": "over-mocking",
            "name": "Avoid Over-Mocking Tests",
            "description": "When writing tests, the core area being tested must not be mocked. Mocking should only wrap external dependencies, never the system under test",
        },
        {
            "id": "large-files",
            "name": "No Large Files",
            "description": "Large files. Scripts >200 lines, Classes >300 lines, Modules >500 lines",
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
        {
            "id": "mock-in-e2e",
            "name": "No Mocking in E2E/Integration Tests",
            "description": "Mocking in E2E or integration tests where real systems should be used. Production code must never contain mock behaviors, development flags, or simulated execution paths",
        },
        {
            "id": "over-engineering",
            "name": "No Over-Engineering",
            "description": "Over-engineered solutions with >3 moving parts without justification. Question solutions >50 lines when <20 might work. No unnecessary abstraction layers or premature generalization",
        },
        {
            "id": "code-duplication",
            "name": "No Code Duplication",
            "description": "Copy-pasted or near-identical code patterns appearing 3+ times. Three-strike rule: any pattern repeated 3+ times must be abstracted immediately",
        },
        {
            "id": "orphan-code",
            "name": "No Orphan Code",
            "description": "New functions, classes, or handlers with no call site or integration point. Every new capability must be reachable from the main execution path",
        },
        {
            "id": "unbounded-loops",
            "name": "No Unbounded Loops",
            "description": "Loops without provable termination: while(true), while(condition) without max iterations, unbounded recursion, polling without timeout. All loops must have statically verifiable upper bounds",
        },
        {
            "id": "missing-invariants",
            "name": "No Missing Invariants",
            "description": "Critical functions lacking precondition validation or postcondition assertions. Assert at domain boundaries, state transitions, security-critical paths, and data integrity points",
        },
        {
            "id": "excessive-indirection",
            "name": "No Excessive Indirection",
            "description": "More than 3 jumps from call site to actual work. Wrapper pyramids, single-implementation interfaces, and abstraction layers that exist without justification",
        },
        {
            "id": "hidden-magic",
            "name": "No Hidden Magic",
            "description": "Metaprogramming that obscures control flow: eval/exec, >2 stacked decorators, metaclass abuse, monkey patching, convention-based auto-discovery. Every code path must be traceable by reading source",
        },
    ]


def _load_custom_config(config_path: str, strict: bool = False) -> Dict:
    """
    Load custom config from YAML file.

    Returns a dict with 'rules' (list of custom/override rule dicts)
    and 'deleted_rules' (list of default rule IDs to suppress).
    Never returns defaults — only what is stored in the config file.

    Args:
        config_path: Path to YAML config file
        strict: If True, raise ValueError on YAML syntax errors

    Returns:
        Dict with keys 'rules' and 'deleted_rules'

    Raises:
        ValueError: If strict=True and YAML has syntax errors
    """
    if not os.path.exists(config_path):
        return {"rules": [], "deleted_rules": []}

    try:
        with open(config_path, "r") as f:
            config_data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        if strict:
            raise ValueError(f"Invalid YAML syntax in config file:\n{e}") from e
        log_warning(
            "clean_code_rules", "Failed to parse YAML config, using defaults", e
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

    # Filter: deleted must be strings, rules must be dicts with id
    deleted = [d for d in deleted if isinstance(d, str)]
    rules = [r for r in rules if isinstance(r, dict) and "id" in r]

    return {"rules": rules, "deleted_rules": deleted}


def _write_config(config_path: str, custom_config: Dict) -> None:
    """
    Write custom config to YAML file atomically.

    Writes only the custom overrides and deleted_rules markers —
    never writes default rule copies.

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


def _migrate_snapshot(config_path: str) -> None:
    """
    Migrate old snapshot-format configs to the new merge-strategy format.

    Old format stored all default rules as copies in 'rules' list.
    New format stores only genuine overrides (rules with changed fields).
    This strips exact copies of defaults, preserving only genuine overrides.

    INVARIANT: calls _load_custom_config directly, NEVER load_rules (avoids recursion).

    Args:
        config_path: Path to YAML config file
    """
    custom_config = _load_custom_config(config_path)
    if not custom_config["rules"]:
        return

    defaults_by_id = {r["id"]: r for r in get_default_rules()}
    genuinely_custom = []

    for rule in custom_config["rules"]:
        rid = rule.get("id")
        if rid in defaults_by_id:
            default = defaults_by_id[rid]
            if rule.get("name") == default.get("name") and rule.get(
                "description"
            ) == default.get("description"):
                continue  # Strip exact snapshot copy
            else:
                genuinely_custom.append(rule)  # Keep genuine override
        else:
            genuinely_custom.append(rule)  # Keep custom rule

    stripped_count = len(custom_config["rules"]) - len(genuinely_custom)
    if stripped_count > 0:
        custom_config["rules"] = genuinely_custom
        _write_config(config_path, custom_config)
        log_warning(
            "clean_code_rules",
            f"Migrated clean_code_rules.yaml: stripped {stripped_count} snapshot copies of default rules",
        )


def load_rules(config_path: str, strict: bool = False) -> List[Dict[str, str]]:
    """
    Load clean code rules using merge strategy.

    Merges hardcoded defaults with custom config:
    - Defaults appear at their natural positions
    - Overrides (same id as default) replace defaults at same position
    - Deleted defaults are suppressed
    - Custom rules (new ids) are appended after defaults

    Migration: strips old snapshot copies of defaults if detected.

    Args:
        config_path: Path to YAML config file
        strict: If True, raise exception on YAML syntax errors

    Returns:
        Merged list of rule dictionaries

    Raises:
        ValueError: If strict=True and YAML file has syntax errors
    """
    defaults = get_default_rules()

    if os.path.exists(config_path):
        _migrate_snapshot(config_path)

    custom_config = _load_custom_config(config_path, strict)
    deleted_ids = set(custom_config["deleted_rules"])

    # Start with defaults, excluding deleted ones
    merged = [r for r in defaults if r["id"] not in deleted_ids]

    # Replace defaults with overrides at same position
    custom_by_id = {r["id"]: r for r in custom_config["rules"]}
    for i, rule in enumerate(merged):
        if rule["id"] in custom_by_id:
            merged[i] = custom_by_id.pop(rule["id"])

    # Append remaining custom rules (not in defaults)
    for rule in custom_by_id.values():
        merged.append(rule)

    return merged


def get_rules_metadata(config_path: str) -> List[Dict[str, str]]:
    """
    Get metadata about each rule's source (default/override/custom).

    Returns a list of dicts with 'id' and 'source' fields.
    Deleted rules are excluded from the result.

    Source values:
    - 'default': rule comes from hardcoded defaults unmodified
    - 'override': default rule with custom modifications
    - 'custom': rule not in defaults, added by user

    Args:
        config_path: Path to YAML config file

    Returns:
        List of dicts with 'id' and 'source' keys
    """
    defaults = get_default_rules()
    default_ids = {r["id"] for r in defaults}
    custom_config = _load_custom_config(config_path)
    custom_ids = {r["id"] for r in custom_config["rules"]}
    deleted_ids = set(custom_config["deleted_rules"])

    result = []
    for r in defaults:
        if r["id"] in deleted_ids:
            continue
        if r["id"] in custom_ids:
            result.append({"id": r["id"], "source": "override"})
        else:
            result.append({"id": r["id"], "source": "default"})

    for r in custom_config["rules"]:
        if r["id"] not in default_ids and r["id"] not in deleted_ids:
            result.append({"id": r["id"], "source": "custom"})

    return result


def add_rule(config_path: str, rule: Dict[str, str]) -> None:
    """
    Add a rule to the custom config.

    If the rule ID matches a deleted default, it removes the deletion marker
    and stores the rule as an override (restoring it at original position).

    Args:
        config_path: Path to YAML config file
        rule: Dictionary with id, name, and description fields

    Raises:
        ValueError: If a rule with the same ID already exists in custom config
    """
    custom_config = _load_custom_config(config_path)

    if any(r["id"] == rule["id"] for r in custom_config["rules"]):
        raise ValueError(f"Rule with id '{rule['id']}' already exists in custom config")

    if rule["id"] in custom_config["deleted_rules"]:
        custom_config["deleted_rules"].remove(rule["id"])

    custom_config["rules"].append(rule)
    _write_config(config_path, custom_config)


def remove_rule(config_path: str, rule_id: str) -> None:
    """
    Remove a rule by ID.

    For default rules: adds a deletion marker to deleted_rules.
    For custom rules: removes from rules list without a marker.
    For overrides (default id with custom content): removes override AND adds deletion marker.

    Args:
        config_path: Path to YAML config file
        rule_id: ID of the rule to remove

    Raises:
        ValueError: If rule is already deleted or not found
    """
    custom_config = _load_custom_config(config_path)
    default_ids = {r["id"] for r in get_default_rules()}
    custom_ids = {r["id"] for r in custom_config["rules"]}

    if rule_id in set(custom_config["deleted_rules"]):
        raise ValueError(f"Rule '{rule_id}' is already deleted")

    if rule_id not in default_ids and rule_id not in custom_ids:
        raise ValueError(f"Rule with id '{rule_id}' not found")

    # Remove from custom rules (handles both overrides and pure custom rules)
    custom_config["rules"] = [r for r in custom_config["rules"] if r["id"] != rule_id]

    # Add deletion marker only for default rules (or overrides of defaults)
    if rule_id in default_ids:
        custom_config["deleted_rules"].append(rule_id)

    _write_config(config_path, custom_config)


def modify_rule(config_path: str, rule_id: str, updates: Dict[str, str]) -> None:
    """
    Modify an existing rule by ID.

    For default rules: creates an override in the custom config.
    For overrides/custom rules: updates in place.
    If rule was deleted: removes deletion marker and creates override.

    The 'id' key in updates is always ignored to prevent ID changes.

    Args:
        config_path: Path to YAML config file
        rule_id: ID of the rule to modify
        updates: Dictionary of fields to update (id key is ignored)

    Raises:
        ValueError: If rule with given ID is not found in defaults or custom config
    """
    custom_config = _load_custom_config(config_path)

    # Strip id from updates to prevent changing rule identity
    updates = {k: v for k, v in updates.items() if k != "id"}

    # If rule was deleted, remove deletion marker before modifying
    if rule_id in custom_config["deleted_rules"]:
        custom_config["deleted_rules"].remove(rule_id)

    # Update existing custom/override entry if present
    found_custom = False
    for rule in custom_config["rules"]:
        if rule["id"] == rule_id:
            rule.update(updates)
            found_custom = True
            break

    if not found_custom:
        # Check if it's a default rule — create override
        default_rule = next(
            (r for r in get_default_rules() if r["id"] == rule_id), None
        )
        if not default_rule:
            raise ValueError(f"Rule with id '{rule_id}' not found")
        override = {**default_rule, **updates}
        custom_config["rules"].append(override)

    _write_config(config_path, custom_config)


def format_rules_for_display(
    rules: List[Dict[str, str]], config_path: Optional[str] = None
) -> str:
    """
    Format rules for CLI display output.

    When config_path is provided, each rule is tagged with its source:
    [default], [override], or [custom].

    Args:
        rules: List of rule dictionaries
        config_path: Optional path to config file for source tagging

    Returns:
        Formatted string for display
    """
    if not rules:
        return "No rules configured."

    metadata = {}
    if config_path:
        for m in get_rules_metadata(config_path):
            metadata[m["id"]] = m["source"]

    output = []
    for rule in rules:
        rid = rule.get("id", "N/A")
        source_tag = f" [{metadata.get(rid, 'default')}]" if metadata else ""
        output.append(f"ID: {rid}{source_tag}")
        output.append(f"  Name: {rule.get('name', 'N/A')}")
        output.append(f"  Description: {rule.get('description', 'N/A')}")
        output.append("")

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
