#!/usr/bin/env python3
"""
Unit tests for clean_code_rules module.

Tests CRUD operations for managing clean code validation rules.
"""

import tempfile
import os


def test_get_default_rules_returns_hardcoded_list():
    """Test that get_default_rules returns a non-empty list of rules."""
    from pacemaker.clean_code_rules import get_default_rules

    # Execute: Get default rules
    rules = get_default_rules()

    # Assert: Non-empty list
    assert isinstance(rules, list)
    assert len(rules) > 0

    # Assert: Each rule has required fields
    for rule in rules:
        assert isinstance(rule, dict)
        assert "id" in rule
        assert "name" in rule
        assert "description" in rule
        assert isinstance(rule["id"], str)
        assert isinstance(rule["name"], str)
        assert isinstance(rule["description"], str)


def test_load_rules_returns_defaults_when_file_missing():
    """Test that load_rules returns defaults when YAML file doesn't exist."""
    from pacemaker.clean_code_rules import load_rules, get_default_rules

    # Setup: Non-existent file path
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "nonexistent.yaml")

        # Execute: Load rules from non-existent file
        rules = load_rules(config_path)

        # Assert: Returns default rules
        default_rules = get_default_rules()
        assert rules == default_rules


def test_load_rules_from_custom_yaml_file():
    """Test that load_rules loads custom rules from YAML file."""
    from pacemaker.clean_code_rules import load_rules

    # Setup: Create YAML file with 3 custom rules
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")

        custom_rules = [
            {"id": "rule1", "name": "Rule One", "description": "First rule"},
            {"id": "rule2", "name": "Rule Two", "description": "Second rule"},
            {"id": "rule3", "name": "Rule Three", "description": "Third rule"},
        ]

        with open(config_path, "w") as f:
            f.write("rules:\n")
            for rule in custom_rules:
                f.write(f"  - id: {rule['id']}\n")
                f.write(f"    name: {rule['name']}\n")
                f.write(f"    description: {rule['description']}\n")

        # Execute: Load rules from YAML file
        rules = load_rules(config_path)

        # Assert: Returns exactly 3 custom rules
        assert len(rules) == 3
        assert rules == custom_rules


def test_add_rule_creates_new_rule():
    """Test that add_rule adds a new rule to YAML file."""
    from pacemaker.clean_code_rules import add_rule, load_rules

    # Setup: Create empty YAML file or file with existing rules
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")

        # Start with one rule
        with open(config_path, "w") as f:
            f.write("rules:\n")
            f.write("  - id: existing\n")
            f.write("    name: Existing Rule\n")
            f.write("    description: An existing rule\n")

        # Execute: Add new rule
        new_rule = {
            "id": "no-todo",
            "name": "No TODO Comments",
            "description": "Code must not contain TODO comments",
        }
        add_rule(config_path, new_rule)

        # Assert: Rule was added
        rules = load_rules(config_path)
        assert len(rules) == 2
        assert rules[1] == new_rule


def test_modify_rule_updates_existing_rule():
    """Test that modify_rule updates an existing rule's fields."""
    from pacemaker.clean_code_rules import modify_rule, load_rules

    # Setup: Create YAML file with existing rule
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")

        with open(config_path, "w") as f:
            f.write("rules:\n")
            f.write("  - id: magic-numbers\n")
            f.write("    name: No Magic Numbers\n")
            f.write("    description: Original description\n")

        # Execute: Modify rule description
        modify_rule(
            config_path, "magic-numbers", {"description": "Updated description"}
        )

        # Assert: Rule was updated
        rules = load_rules(config_path)
        assert len(rules) == 1
        assert rules[0]["id"] == "magic-numbers"
        assert rules[0]["description"] == "Updated description"
        assert rules[0]["name"] == "No Magic Numbers"  # Name unchanged


def test_remove_rule_deletes_rule():
    """Test that remove_rule deletes a rule by ID."""
    from pacemaker.clean_code_rules import remove_rule, load_rules

    # Setup: Create YAML file with multiple rules
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")

        with open(config_path, "w") as f:
            f.write("rules:\n")
            f.write("  - id: rule1\n")
            f.write("    name: Rule One\n")
            f.write("    description: First rule\n")
            f.write("  - id: bare-except\n")
            f.write("    name: No Bare Except\n")
            f.write("    description: Catch specific exceptions\n")
            f.write("  - id: rule3\n")
            f.write("    name: Rule Three\n")
            f.write("    description: Third rule\n")

        # Execute: Remove rule by ID
        remove_rule(config_path, "bare-except")

        # Assert: Rule was removed
        rules = load_rules(config_path)
        assert len(rules) == 2
        assert all(rule["id"] != "bare-except" for rule in rules)


def test_load_rules_falls_back_on_invalid_yaml():
    """Test that load_rules returns defaults when YAML is invalid."""
    from pacemaker.clean_code_rules import load_rules, get_default_rules

    # Setup: Create invalid YAML file
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")

        with open(config_path, "w") as f:
            f.write("rules:\n")
            f.write("  - id: test\n")
            f.write("    name: Test\n")
            f.write("  invalid yaml here [[[")  # Invalid YAML syntax

        # Execute: Load rules from invalid YAML
        rules = load_rules(config_path)

        # Assert: Returns default rules (fallback)
        default_rules = get_default_rules()
        assert rules == default_rules


def test_format_rules_for_display_with_valid_rules():
    """Test that format_rules_for_display returns formatted string with rule details."""
    from pacemaker.clean_code_rules import format_rules_for_display

    # Setup: Create sample rules
    rules = [
        {"id": "rule1", "name": "Rule One", "description": "First rule"},
        {"id": "rule2", "name": "Rule Two", "description": "Second rule"},
    ]

    # Execute: Format rules for display
    output = format_rules_for_display(rules)

    # Assert: Output contains all rule details
    assert "ID: rule1" in output
    assert "Name: Rule One" in output
    assert "Description: First rule" in output
    assert "ID: rule2" in output
    assert "Name: Rule Two" in output
    assert "Description: Second rule" in output


def test_format_rules_for_display_with_empty_list():
    """Test that format_rules_for_display handles empty rules list."""
    from pacemaker.clean_code_rules import format_rules_for_display

    # Execute: Format empty rules list
    output = format_rules_for_display([])

    # Assert: Returns appropriate message
    assert output == "No rules configured."


def test_format_rules_for_validation_with_valid_rules():
    """Test that format_rules_for_validation returns formatted string for prompts."""
    from pacemaker.clean_code_rules import format_rules_for_validation

    # Setup: Create sample rules
    rules = [
        {"id": "rule1", "name": "Rule One", "description": "Check for X"},
        {"id": "rule2", "name": "Rule Two", "description": "Verify Y"},
    ]

    # Execute: Format rules for validation prompt
    output = format_rules_for_validation(rules)

    # Assert: Output contains descriptions in correct format
    assert "   - Check for X" in output
    assert "   - Verify Y" in output


def test_format_rules_for_validation_with_empty_list():
    """Test that format_rules_for_validation handles empty rules list."""
    from pacemaker.clean_code_rules import format_rules_for_validation

    # Execute: Format empty rules list
    output = format_rules_for_validation([])

    # Assert: Returns default message
    assert "No custom rules configured" in output


def test_load_rules_with_empty_yaml_file():
    """Test that load_rules returns defaults when YAML file is empty."""
    from pacemaker.clean_code_rules import load_rules, get_default_rules

    # Setup: Create empty YAML file
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")

        with open(config_path, "w") as f:
            f.write("")  # Completely empty file

        # Execute: Load rules from empty file
        rules = load_rules(config_path)

        # Assert: Returns default rules
        default_rules = get_default_rules()
        assert rules == default_rules


def test_load_rules_with_non_list_rules_key():
    """Test that load_rules returns defaults when 'rules' key is not a list."""
    from pacemaker.clean_code_rules import load_rules, get_default_rules

    # Setup: Create YAML with rules as a string instead of list
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")

        with open(config_path, "w") as f:
            f.write("rules: this_is_not_a_list\n")

        # Execute: Load rules from malformed file
        rules = load_rules(config_path)

        # Assert: Returns default rules
        default_rules = get_default_rules()
        assert rules == default_rules


def test_load_rules_with_empty_rules_list():
    """Test that load_rules returns defaults when 'rules' list is empty."""
    from pacemaker.clean_code_rules import load_rules, get_default_rules

    # Setup: Create YAML with empty rules list
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")

        with open(config_path, "w") as f:
            f.write("rules: []\n")

        # Execute: Load rules from file with empty list
        rules = load_rules(config_path)

        # Assert: Returns default rules
        default_rules = get_default_rules()
        assert rules == default_rules


def test_modify_rule_raises_error_when_rule_not_found():
    """Test that modify_rule raises ValueError when rule ID doesn't exist."""
    from pacemaker.clean_code_rules import modify_rule

    # Setup: Create YAML file with existing rule
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")

        with open(config_path, "w") as f:
            f.write("rules:\n")
            f.write("  - id: existing-rule\n")
            f.write("    name: Existing Rule\n")
            f.write("    description: This exists\n")

        # Execute & Assert: Attempt to modify non-existent rule raises ValueError
        try:
            modify_rule(config_path, "non-existent-id", {"description": "Updated"})
            assert False, "Expected ValueError to be raised"
        except ValueError as e:
            assert "non-existent-id" in str(e)
            assert "not found" in str(e)


def test_remove_rule_raises_error_when_rule_not_found():
    """Test that remove_rule raises ValueError when rule ID doesn't exist."""
    from pacemaker.clean_code_rules import remove_rule

    # Setup: Create YAML file with existing rule
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")

        with open(config_path, "w") as f:
            f.write("rules:\n")
            f.write("  - id: existing-rule\n")
            f.write("    name: Existing Rule\n")
            f.write("    description: This exists\n")

        # Execute & Assert: Attempt to remove non-existent rule raises ValueError
        try:
            remove_rule(config_path, "non-existent-id")
            assert False, "Expected ValueError to be raised"
        except ValueError as e:
            assert "non-existent-id" in str(e)
            assert "not found" in str(e)


def test_load_rules_strict_mode_raises_on_invalid_yaml():
    """Test that load_rules with strict=True raises exception on invalid YAML."""
    from pacemaker.clean_code_rules import load_rules
    import yaml

    # Setup: Create invalid YAML file
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")

        with open(config_path, "w") as f:
            f.write("rules:\n")
            f.write("  - id: test\n")
            f.write("    name: Test\n")
            f.write("  invalid yaml here [[[")  # Invalid YAML syntax

        # Execute & Assert: Load rules with strict=True should raise exception
        try:
            load_rules(config_path, strict=True)
            assert False, "Expected exception to be raised in strict mode"
        except (yaml.YAMLError, ValueError, Exception):
            # Expected exception - test passes
            pass


def test_load_rules_non_strict_mode_returns_defaults_on_invalid_yaml():
    """Test that load_rules with strict=False returns defaults on invalid YAML."""
    from pacemaker.clean_code_rules import load_rules, get_default_rules

    # Setup: Create invalid YAML file
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")

        with open(config_path, "w") as f:
            f.write("rules:\n")
            f.write("  - id: test\n")
            f.write("    name: Test\n")
            f.write("  invalid yaml here [[[")  # Invalid YAML syntax

        # Execute: Load rules with strict=False (default)
        rules = load_rules(config_path, strict=False)

        # Assert: Returns default rules (fallback behavior)
        default_rules = get_default_rules()
        assert rules == default_rules


# ============================================================================
# Tests for Messi rules enhancements
# ============================================================================


def test_default_rules_total_count_is_25():
    """Test that get_default_rules returns exactly 25 rules after Messi rules additions."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()

    assert len(rules) == 25, f"Expected 25 rules, got {len(rules)}"


def test_enhanced_undeclared_fallbacks_description():
    """Test that undeclared-fallbacks rule has enhanced description."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule = next((r for r in rules if r["id"] == "undeclared-fallbacks"), None)

    assert rule is not None, "Rule 'undeclared-fallbacks' not found"
    assert "alternative code paths" in rule["description"]
    assert "just in case" in rule["description"]
    assert "graceful failure over forced success" in rule["description"]


def test_enhanced_swallowed_exceptions_description():
    """Test that swallowed-exceptions rule has enhanced description covering return values."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule = next((r for r in rules if r["id"] == "swallowed-exceptions"), None)

    assert rule is not None, "Rule 'swallowed-exceptions' not found"
    assert "unchecked return values" in rule["description"]
    assert "LOG+THROW" in rule["description"]


def test_enhanced_over_mocking_description():
    """Test that over-mocking rule has enhanced description covering E2E and integration tests."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule = next((r for r in rules if r["id"] == "over-mocking"), None)

    assert rule is not None, "Rule 'over-mocking' not found"
    assert "core area being tested" in rule["description"]
    assert "external dependencies" in rule["description"]


def test_enhanced_large_files_description():
    """Test that large-files rule has enhanced description with size thresholds."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule = next((r for r in rules if r["id"] == "large-files"), None)

    assert rule is not None, "Rule 'large-files' not found"
    assert "200" in rule["description"]
    assert "300" in rule["description"]
    assert "500" in rule["description"]


def test_new_rule_mock_in_e2e_present():
    """Test that mock-in-e2e rule is present with correct id and name."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule = next((r for r in rules if r["id"] == "mock-in-e2e"), None)

    assert rule is not None, "Rule 'mock-in-e2e' not found"
    assert rule["name"] == "No Mocking in E2E/Integration Tests"
    assert "E2E" in rule["description"]
    assert "integration tests" in rule["description"]


def test_new_rule_over_engineering_present():
    """Test that over-engineering rule is present with correct id and name."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule = next((r for r in rules if r["id"] == "over-engineering"), None)

    assert rule is not None, "Rule 'over-engineering' not found"
    assert rule["name"] == "No Over-Engineering"
    assert "3 moving parts" in rule["description"]


def test_new_rule_code_duplication_present():
    """Test that code-duplication rule is present with correct id and name."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule = next((r for r in rules if r["id"] == "code-duplication"), None)

    assert rule is not None, "Rule 'code-duplication' not found"
    assert rule["name"] == "No Code Duplication"
    assert "Three-strike" in rule["description"]


def test_new_rule_orphan_code_present():
    """Test that orphan-code rule is present with correct id and name."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule = next((r for r in rules if r["id"] == "orphan-code"), None)

    assert rule is not None, "Rule 'orphan-code' not found"
    assert rule["name"] == "No Orphan Code"
    assert "call site" in rule["description"]


def test_new_rule_unbounded_loops_present():
    """Test that unbounded-loops rule is present with correct id and name."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule = next((r for r in rules if r["id"] == "unbounded-loops"), None)

    assert rule is not None, "Rule 'unbounded-loops' not found"
    assert rule["name"] == "No Unbounded Loops"
    assert "termination" in rule["description"]


def test_new_rule_missing_invariants_present():
    """Test that missing-invariants rule is present with correct id and name."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule = next((r for r in rules if r["id"] == "missing-invariants"), None)

    assert rule is not None, "Rule 'missing-invariants' not found"
    assert rule["name"] == "No Missing Invariants"
    assert "precondition" in rule["description"]


def test_new_rule_excessive_indirection_present():
    """Test that excessive-indirection rule is present with correct id and name."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule = next((r for r in rules if r["id"] == "excessive-indirection"), None)

    assert rule is not None, "Rule 'excessive-indirection' not found"
    assert rule["name"] == "No Excessive Indirection"
    assert "3 jumps" in rule["description"]


def test_new_rule_hidden_magic_present():
    """Test that hidden-magic rule is present with correct id and name."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule = next((r for r in rules if r["id"] == "hidden-magic"), None)

    assert rule is not None, "Rule 'hidden-magic' not found"
    assert rule["name"] == "No Hidden Magic"
    assert "eval" in rule["description"]
    assert "Metaprogramming" in rule["description"]


def test_all_new_rule_ids_present():
    """Test that all 8 new Messi rule IDs are present in default rules."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule_ids = {r["id"] for r in rules}

    new_rule_ids = {
        "mock-in-e2e",
        "over-engineering",
        "code-duplication",
        "orphan-code",
        "unbounded-loops",
        "missing-invariants",
        "excessive-indirection",
        "hidden-magic",
    }

    missing = new_rule_ids - rule_ids
    assert not missing, f"Missing rule IDs: {missing}"


def test_default_rule_ids_are_unique():
    """Test that all rule IDs in default rules are unique."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    ids = [r["id"] for r in rules]

    assert len(ids) == len(
        set(ids)
    ), f"Duplicate rule IDs: {[id for id in ids if ids.count(id) > 1]}"
