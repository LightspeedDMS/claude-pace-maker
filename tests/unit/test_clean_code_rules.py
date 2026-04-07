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
    """Test that load_rules merges custom rules with defaults from YAML file."""
    import yaml
    from pacemaker.clean_code_rules import load_rules, get_default_rules

    # Setup: Create YAML file with 3 purely custom rules (non-default IDs)
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")

        custom_rules = [
            {"id": "rule1", "name": "Rule One", "description": "First rule"},
            {"id": "rule2", "name": "Rule Two", "description": "Second rule"},
            {"id": "rule3", "name": "Rule Three", "description": "Third rule"},
        ]

        with open(config_path, "w") as f:
            yaml.safe_dump({"rules": custom_rules, "deleted_rules": []}, f)

        # Execute: Load rules from YAML file
        rules = load_rules(config_path)
        defaults = get_default_rules()

        # Assert: Returns defaults + 3 custom rules appended
        assert len(rules) == len(defaults) + 3
        # Custom rules are appended at the end
        assert rules[-3]["id"] == "rule1"
        assert rules[-2]["id"] == "rule2"
        assert rules[-1]["id"] == "rule3"
        # All defaults are present
        default_ids = {r["id"] for r in defaults}
        rule_ids = {r["id"] for r in rules}
        assert default_ids.issubset(rule_ids)


def test_add_rule_creates_new_rule():
    """Test that add_rule appends custom rules after all defaults in merged result."""
    import yaml
    from pacemaker.clean_code_rules import add_rule, load_rules, get_default_rules

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")

        # Start with one existing custom rule (non-default id)
        existing_rule = {
            "id": "existing",
            "name": "Existing Rule",
            "description": "An existing rule",
        }
        with open(config_path, "w") as f:
            yaml.safe_dump({"rules": [existing_rule], "deleted_rules": []}, f)

        # Execute: Add another new custom rule
        new_rule = {
            "id": "no-todo",
            "name": "No TODO Comments",
            "description": "Code must not contain TODO comments",
        }
        add_rule(config_path, new_rule)

        # Assert: Both custom rules appended after all defaults
        rules = load_rules(config_path)
        defaults = get_default_rules()
        defaults_count = len(defaults)
        assert len(rules) == defaults_count + 2

        rule_ids = [r["id"] for r in rules]
        # Both custom rules must be positioned after all defaults
        assert rule_ids.index("existing") >= defaults_count
        assert rule_ids.index("no-todo") >= defaults_count
        # Custom rules occupy the last two positions in insertion order
        assert rule_ids[-2:] == ["existing", "no-todo"]


def test_modify_rule_updates_existing_rule():
    """Test that modify_rule updates an existing custom rule in place.

    The merged load result includes all defaults plus the updated custom rule.
    """
    import yaml
    from pacemaker.clean_code_rules import modify_rule, load_rules, get_default_rules

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")

        # Setup: Custom rule with a non-default id
        custom_rule = {
            "id": "my-custom-rule",
            "name": "My Custom Rule",
            "description": "Original description",
        }
        with open(config_path, "w") as f:
            yaml.safe_dump({"rules": [custom_rule], "deleted_rules": []}, f)

        # Execute: Modify the custom rule's description
        modify_rule(
            config_path, "my-custom-rule", {"description": "Updated description"}
        )

        # Assert: Merged result has all defaults plus updated custom rule
        rules = load_rules(config_path)
        defaults = get_default_rules()
        assert len(rules) == len(defaults) + 1

        custom = next(r for r in rules if r["id"] == "my-custom-rule")
        assert custom["description"] == "Updated description"
        assert custom["name"] == "My Custom Rule"  # Name unchanged


def test_remove_rule_deletes_rule():
    """Test that remove_rule removes a custom rule and merged result excludes it."""
    import yaml
    from pacemaker.clean_code_rules import remove_rule, load_rules, get_default_rules

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")

        # Setup: 3 purely custom rules (non-default ids)
        custom_rules = [
            {"id": "rule1", "name": "Rule One", "description": "First rule"},
            {
                "id": "custom-no-bare-except",
                "name": "No Bare Except",
                "description": "Catch specific exceptions",
            },
            {"id": "rule3", "name": "Rule Three", "description": "Third rule"},
        ]
        with open(config_path, "w") as f:
            yaml.safe_dump({"rules": custom_rules, "deleted_rules": []}, f)

        # Execute: Remove one custom rule by ID
        remove_rule(config_path, "custom-no-bare-except")

        # Assert: Merged result has all defaults plus the 2 remaining custom rules
        rules = load_rules(config_path)
        defaults = get_default_rules()
        assert len(rules) == len(defaults) + 2
        assert all(rule["id"] != "custom-no-bare-except" for rule in rules)


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
    """Test that get_default_rules returns exactly 25 rules after language-agnostic expansion."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()

    assert len(rules) == 25, f"Expected 25 rules, got {len(rules)}"


def test_merged_rule_silent_degradation_present():
    """Test that silent-degradation rule (merged from undeclared-fallbacks) is present."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule = next((r for r in rules if r["id"] == "silent-degradation"), None)

    assert rule is not None, "Rule 'silent-degradation' not found"
    assert "alternative code paths" in rule["description"]
    assert "just in case" in rule["description"]
    assert "graceful failure over forced success" in rule["description"]


def test_merged_rule_exception_handling_present():
    """Test that exception-handling rule (merged from bare-except + swallowed-exceptions) is present."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule = next((r for r in rules if r["id"] == "exception-handling"), None)

    assert rule is not None, "Rule 'exception-handling' not found"
    assert "unchecked return values" in rule["description"]
    assert "LOG+THROW" in rule["description"]


def test_merged_rule_mock_abuse_present():
    """Test that mock-abuse rule (merged from over-mocking + mock-in-e2e) is present."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule = next((r for r in rules if r["id"] == "mock-abuse"), None)

    assert rule is not None, "Rule 'mock-abuse' not found"
    assert rule["name"] == "No Mock Abuse"
    assert "E2E/integration tests" in rule["description"]
    assert "external dependencies" in rule["description"]


def test_merged_rule_blob_size_present():
    """Test that blob-size rule (merged from large-files + large-blobs + large-methods + too-many-units) is present."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule = next((r for r in rules if r["id"] == "blob-size"), None)

    assert rule is not None, "Rule 'blob-size' not found"
    assert "300" in rule["description"]
    assert "500" in rule["description"]
    assert "50" in rule["description"]


def test_new_rule_credential_construction_present():
    """Test that credential-construction rule is present with correct id and name."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule = next((r for r in rules if r["id"] == "credential-construction"), None)

    assert rule is not None, "Rule 'credential-construction' not found"
    assert rule["name"] == "No Dynamic Credential Assembly"
    assert "concatenation" in rule["description"]


def test_new_rule_path_traversal_present():
    """Test that path-traversal rule is present with correct id and name."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule = next((r for r in rules if r["id"] == "path-traversal"), None)

    assert rule is not None, "Rule 'path-traversal' not found"
    assert rule["name"] == "No Path Traversal"
    assert "sanitization" in rule["description"]


def test_new_rule_resource_leak_present():
    """Test that resource-leak rule is present with correct id and name."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule = next((r for r in rules if r["id"] == "resource-leak"), None)

    assert rule is not None, "Rule 'resource-leak' not found"
    assert rule["name"] == "No Resource Leaks"
    assert "context manager" in rule["description"]


def test_new_rule_concurrency_hazard_present():
    """Test that concurrency-hazard rule is present with correct id and name."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule = next((r for r in rules if r["id"] == "concurrency-hazard"), None)

    assert rule is not None, "Rule 'concurrency-hazard' not found"
    assert rule["name"] == "No Concurrency Hazards"
    assert "synchronization" in rule["description"]


def test_new_rule_over_engineering_present():
    """Test that over-engineering rule is present with correct id and name."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule = next((r for r in rules if r["id"] == "over-engineering"), None)

    assert rule is not None, "Rule 'over-engineering' not found"
    assert rule["name"] == "No Over-Engineering"
    assert "single-implementation interfaces" in rule["description"]


def test_new_rule_code_duplication_present():
    """Test that code-duplication rule is present with correct id and name."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule = next((r for r in rules if r["id"] == "code-duplication"), None)

    assert rule is not None, "Rule 'code-duplication' not found"
    assert rule["name"] == "No Code Duplication"
    assert "2+" in rule["description"]


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


def test_new_rule_hidden_magic_present():
    """Test that hidden-magic rule is present with correct id and name."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule = next((r for r in rules if r["id"] == "hidden-magic"), None)

    assert rule is not None, "Rule 'hidden-magic' not found"
    assert rule["name"] == "No Hidden Magic"
    assert "eval" in rule["description"]
    assert "Metaprogramming" in rule["description"]


def test_mutable_defaults_rule_replaced_by_unsafe_defaults():
    """Test that mutable-defaults id is gone and unsafe-defaults is present."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule_ids = {r["id"] for r in rules}

    assert (
        "mutable-defaults" not in rule_ids
    ), "Old rule 'mutable-defaults' must be removed"
    rule = next((r for r in rules if r["id"] == "unsafe-defaults"), None)
    assert rule is not None, "New rule 'unsafe-defaults' must be present"
    assert rule["name"] == "No Unsafe Default Values"
    assert "contamination" in rule["description"]


def test_new_rules_present():
    """Test that all 5 new rule IDs added in the language-agnostic expansion are present."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule_ids = {r["id"] for r in rules}

    new_ids = [
        "type-safety-erosion",
        "ignored-error-return",
        "unhandled-async",
        "hardcoded-config",
        "unsafe-string-interpolation",
    ]
    for rid in new_ids:
        assert rid in rule_ids, f"New rule '{rid}' must be present in default rules"


def test_patched_rules_are_language_agnostic():
    """Test that the 5 patched rules contain at least one non-Python language reference."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rules_by_id = {r["id"]: r for r in rules}

    non_python_markers = ["Go", "Java", "Kotlin", "C#", "JS", "TypeScript"]
    patched_ids = [
        "exception-handling",
        "resource-leak",
        "path-traversal",
        "concurrency-hazard",
        "hidden-magic",
    ]
    for rid in patched_ids:
        rule = rules_by_id.get(rid)
        assert rule is not None, f"Patched rule '{rid}' must be present"
        desc = rule["description"]
        has_non_python = any(marker in desc for marker in non_python_markers)
        assert has_non_python, (
            f"Rule '{rid}' description must reference at least one non-Python language "
            f"(one of {non_python_markers}), got: {desc!r}"
        )


def test_all_new_rule_ids_present():
    """Test that exactly the 20 Codex-refactored rule IDs are present in default rules (no missing, no extras)."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    rule_ids = {r["id"] for r in rules}

    expected_rule_ids = {
        "hardcoded-secrets",
        "credential-construction",
        "sql-injection",
        "path-traversal",
        "exception-handling",
        "resource-leak",
        "concurrency-hazard",
        "boundary-checks",
        "logic-bugs",
        "magic-numbers",
        "unsafe-defaults",
        "deep-nesting",
        "blob-size",
        "mock-abuse",
        "silent-degradation",
        "over-engineering",
        "code-duplication",
        "orphan-code",
        "unbounded-loops",
        "hidden-magic",
        "type-safety-erosion",
        "ignored-error-return",
        "unhandled-async",
        "hardcoded-config",
        "unsafe-string-interpolation",
    }

    missing = expected_rule_ids - rule_ids
    assert not missing, f"Missing rule IDs: {missing}"
    extra = rule_ids - expected_rule_ids
    assert not extra, f"Unexpected rule IDs: {extra}"


def test_default_rule_ids_are_unique():
    """Test that all rule IDs in default rules are unique."""
    from pacemaker.clean_code_rules import get_default_rules

    rules = get_default_rules()
    ids = [r["id"] for r in rules]

    assert len(ids) == len(
        set(ids)
    ), f"Duplicate rule IDs: {[id for id in ids if ids.count(id) > 1]}"


# ============================================================================
# Acceptance Criteria Tests for Merge Strategy (Story #55)
# os and tempfile are already imported at top of file (lines 7-9)
# ============================================================================


def test_ac1_default_rules_returned_when_no_config_file():
    """AC-1: Default rules returned when no config file exists."""
    import yaml  # noqa: F401 - yaml used below
    from pacemaker.clean_code_rules import load_rules, get_default_rules

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "nonexistent.yaml")
        rules = load_rules(config_path)
        assert rules == get_default_rules()


def test_ac2_custom_rule_appended_to_defaults():
    """AC-2: Custom rule is appended to defaults."""
    import yaml
    from pacemaker.clean_code_rules import load_rules, get_default_rules

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")
        custom_rule = {"id": "my-custom", "name": "My Custom", "description": "Custom"}
        with open(config_path, "w") as f:
            yaml.safe_dump({"rules": [custom_rule], "deleted_rules": []}, f)

        rules = load_rules(config_path)
        defaults = get_default_rules()
        default_ids = {r["id"] for r in defaults}
        rule_ids = [r["id"] for r in rules]
        for did in default_ids:
            assert did in rule_ids
        assert rules[-1]["id"] == "my-custom"
        assert len(rules) == len(defaults) + 1


def test_ac3_override_replaces_default_at_same_position():
    """AC-3: Override replaces matching default at same position."""
    import yaml
    from pacemaker.clean_code_rules import load_rules, get_default_rules

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")
        defaults = get_default_rules()
        first_id = defaults[0]["id"]
        override = {
            "id": first_id,
            "name": "Override Name",
            "description": "Override Desc",
        }
        with open(config_path, "w") as f:
            yaml.safe_dump({"rules": [override], "deleted_rules": []}, f)

        rules = load_rules(config_path)
        assert len(rules) == len(defaults)
        assert rules[0]["id"] == first_id
        assert rules[0]["name"] == "Override Name"
        assert rules[0]["description"] == "Override Desc"


def test_ac4_deleted_default_rule_excluded():
    """AC-4: Deleted default rule is excluded from load_rules result."""
    import yaml
    from pacemaker.clean_code_rules import load_rules, get_default_rules

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")
        defaults = get_default_rules()
        deleted_id = defaults[0]["id"]
        with open(config_path, "w") as f:
            yaml.safe_dump({"rules": [], "deleted_rules": [deleted_id]}, f)

        rules = load_rules(config_path)
        assert len(rules) == len(defaults) - 1
        assert all(r["id"] != deleted_id for r in rules)


def test_ac5_removing_default_creates_deletion_marker():
    """AC-5: Removing a default creates deletion marker in YAML."""
    from pacemaker.clean_code_rules import (
        remove_rule,
        get_default_rules,
        _load_custom_config,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")
        defaults = get_default_rules()
        default_id = defaults[0]["id"]

        remove_rule(config_path, default_id)

        custom = _load_custom_config(config_path)
        assert default_id in custom["deleted_rules"]
        assert all(r["id"] != default_id for r in custom["rules"])


def test_ac6_removing_custom_rule_does_not_create_deletion_marker():
    """AC-6: Removing a custom rule does not create deletion marker."""
    import yaml
    from pacemaker.clean_code_rules import remove_rule, _load_custom_config

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")
        custom_rule = {"id": "my-custom", "name": "My Custom", "description": "Custom"}
        with open(config_path, "w") as f:
            yaml.safe_dump({"rules": [custom_rule], "deleted_rules": []}, f)

        remove_rule(config_path, "my-custom")

        custom = _load_custom_config(config_path)
        assert "my-custom" not in custom["deleted_rules"]
        assert all(r["id"] != "my-custom" for r in custom["rules"])


def test_ac7_adding_previously_deleted_default_restores_at_original_position():
    """AC-7: Adding a previously deleted default restores it at original position."""
    import yaml
    from pacemaker.clean_code_rules import add_rule, load_rules, get_default_rules

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")
        defaults = get_default_rules()
        target = defaults[2]
        with open(config_path, "w") as f:
            yaml.safe_dump({"rules": [], "deleted_rules": [target["id"]]}, f)

        add_rule(config_path, target)

        rules = load_rules(config_path)
        assert len(rules) == len(defaults)
        assert rules[2]["id"] == target["id"]


def test_ac8_modifying_default_creates_override():
    """AC-8: Modifying a default creates an override stored in custom config."""
    from pacemaker.clean_code_rules import (
        modify_rule,
        load_rules,
        get_default_rules,
        _load_custom_config,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")
        defaults = get_default_rules()
        target_id = defaults[0]["id"]

        modify_rule(config_path, target_id, {"description": "New desc"})

        rules = load_rules(config_path)
        rule = next(r for r in rules if r["id"] == target_id)
        assert rule["description"] == "New desc"

        custom = _load_custom_config(config_path)
        assert any(r["id"] == target_id for r in custom["rules"])


def test_get_rules_metadata_no_config_returns_all_defaults():
    """get_rules_metadata with no config returns all defaults tagged as 'default'."""
    from pacemaker.clean_code_rules import get_rules_metadata, get_default_rules

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "nonexistent.yaml")
        metadata = get_rules_metadata(config_path)
        defaults = get_default_rules()

        assert len(metadata) == len(defaults)
        for m in metadata:
            assert m["source"] == "default"
            assert "id" in m


def test_get_rules_metadata_shows_override_custom_deleted():
    """get_rules_metadata shows override/custom sources and excludes deleted."""
    import yaml
    from pacemaker.clean_code_rules import get_rules_metadata, get_default_rules

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")
        defaults = get_default_rules()
        override_id = defaults[0]["id"]
        deleted_id = defaults[1]["id"]
        override = {"id": override_id, "name": "Ov Name", "description": "Ov Desc"}
        custom_rule = {"id": "my-custom", "name": "My Custom", "description": "Custom"}
        with open(config_path, "w") as f:
            yaml.safe_dump(
                {"rules": [override, custom_rule], "deleted_rules": [deleted_id]}, f
            )

        metadata = get_rules_metadata(config_path)
        meta_by_id = {m["id"]: m["source"] for m in metadata}

        assert meta_by_id[override_id] == "override"
        assert deleted_id not in meta_by_id
        assert meta_by_id["my-custom"] == "custom"
        for d in defaults[2:]:
            assert meta_by_id[d["id"]] == "default"


# ============================================================================
# AC-9 through AC-18, AC-21 through AC-24
# ============================================================================


def test_ac9_new_defaults_appear_after_upgrade():
    """AC-9: New defaults that don't exist in config appear in load_rules result."""
    import yaml
    from pacemaker.clean_code_rules import load_rules, get_default_rules

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")
        defaults = get_default_rules()
        # Config has only first rule as override, no deleted_rules
        first = defaults[0]
        override = {
            "id": first["id"],
            "name": first["name"],
            "description": first["description"],
        }
        with open(config_path, "w") as f:
            yaml.safe_dump({"rules": [override], "deleted_rules": []}, f)

        rules = load_rules(config_path)
        rule_ids = {r["id"] for r in rules}
        # All defaults must appear
        for d in defaults:
            assert d["id"] in rule_ids, f"Default rule '{d['id']}' missing after load"


def test_ac10_add_rule_with_default_id_creates_position_preserving_override():
    """AC-10: add_rule with a deleted default ID removes marker and adds override."""
    import yaml
    from pacemaker.clean_code_rules import (
        add_rule,
        load_rules,
        get_default_rules,
        _load_custom_config,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")
        defaults = get_default_rules()
        target = defaults[2]
        # Config has a deleted marker for target
        with open(config_path, "w") as f:
            yaml.safe_dump({"rules": [], "deleted_rules": [target["id"]]}, f)

        # add_rule with default id should remove from deleted and add as override
        add_rule(config_path, target)

        rules = load_rules(config_path)
        assert len(rules) == len(defaults)
        assert rules[2]["id"] == target["id"]

        custom = _load_custom_config(config_path)
        assert target["id"] not in custom["deleted_rules"]


def test_ac11_add_rule_duplicate_custom_raises_value_error():
    """AC-11: add_rule with existing custom rule ID raises ValueError."""
    import yaml
    from pacemaker.clean_code_rules import add_rule

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")
        custom_rule = {"id": "my-custom", "name": "My Custom", "description": "Custom"}
        with open(config_path, "w") as f:
            yaml.safe_dump({"rules": [custom_rule], "deleted_rules": []}, f)

        try:
            add_rule(
                config_path, {"id": "my-custom", "name": "Dup", "description": "Dup"}
            )
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "my-custom" in str(e)


def test_ac12_modify_rule_on_deleted_removes_marker_and_overrides():
    """AC-12: modify_rule on a deleted default removes deletion marker and creates override."""
    import yaml
    from pacemaker.clean_code_rules import (
        modify_rule,
        load_rules,
        get_default_rules,
        _load_custom_config,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")
        defaults = get_default_rules()
        target_id = defaults[0]["id"]
        with open(config_path, "w") as f:
            yaml.safe_dump({"rules": [], "deleted_rules": [target_id]}, f)

        modify_rule(config_path, target_id, {"description": "Restored and modified"})

        custom = _load_custom_config(config_path)
        assert target_id not in custom["deleted_rules"]
        assert any(r["id"] == target_id for r in custom["rules"])

        rules = load_rules(config_path)
        rule = next(r for r in rules if r["id"] == target_id)
        assert rule["description"] == "Restored and modified"


def test_ac13_remove_rule_already_deleted_raises_value_error():
    """AC-13: remove_rule on already-deleted rule raises ValueError."""
    import yaml
    from pacemaker.clean_code_rules import remove_rule, get_default_rules

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")
        defaults = get_default_rules()
        target_id = defaults[0]["id"]
        with open(config_path, "w") as f:
            yaml.safe_dump({"rules": [], "deleted_rules": [target_id]}, f)

        try:
            remove_rule(config_path, target_id)
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "already deleted" in str(e)


def test_ac14_remove_rule_override_removes_and_suppresses_default():
    """AC-14: remove_rule on an override removes the override AND suppresses the default."""
    import yaml
    from pacemaker.clean_code_rules import (
        remove_rule,
        load_rules,
        get_default_rules,
        _load_custom_config,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")
        defaults = get_default_rules()
        target_id = defaults[0]["id"]
        override = {
            "id": target_id,
            "name": "Override Name",
            "description": "Override Desc",
        }
        with open(config_path, "w") as f:
            yaml.safe_dump({"rules": [override], "deleted_rules": []}, f)

        remove_rule(config_path, target_id)

        custom = _load_custom_config(config_path)
        assert target_id in custom["deleted_rules"]
        assert all(r["id"] != target_id for r in custom["rules"])

        rules = load_rules(config_path)
        assert all(r["id"] != target_id for r in rules)


def test_ac15_invalid_deleted_rules_type_handled_gracefully():
    """AC-15: Invalid deleted_rules type (not a list) is treated as empty."""
    import yaml
    from pacemaker.clean_code_rules import load_rules, get_default_rules

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")
        defaults = get_default_rules()
        with open(config_path, "w") as f:
            yaml.safe_dump({"rules": [], "deleted_rules": "not-a-list"}, f)

        # Should not raise; deleted_rules ignored, returns all defaults
        rules = load_rules(config_path)
        assert len(rules) == len(defaults)


def test_ac16_add_rule_with_existing_override_raises_value_error():
    """AC-16: add_rule with an ID that already has an override raises ValueError."""
    import yaml
    from pacemaker.clean_code_rules import add_rule, get_default_rules

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")
        defaults = get_default_rules()
        target_id = defaults[0]["id"]
        override = {"id": target_id, "name": "Override", "description": "Override"}
        with open(config_path, "w") as f:
            yaml.safe_dump({"rules": [override], "deleted_rules": []}, f)

        try:
            add_rule(
                config_path,
                {"id": target_id, "name": "Another", "description": "Another"},
            )
            assert False, "Expected ValueError"
        except ValueError as e:
            assert target_id in str(e)


def test_ac17_add_rule_with_default_id_twice_raises_value_error():
    """AC-17: add_rule with a default ID after it's already been re-added raises ValueError."""
    import yaml
    from pacemaker.clean_code_rules import add_rule, get_default_rules

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")
        defaults = get_default_rules()
        target = defaults[0]
        # First add: removes from deleted, adds to rules
        with open(config_path, "w") as f:
            yaml.safe_dump({"rules": [], "deleted_rules": [target["id"]]}, f)
        add_rule(config_path, target)

        # Second add: already in custom rules, should raise
        try:
            add_rule(config_path, target)
            assert False, "Expected ValueError on second add"
        except ValueError as e:
            assert target["id"] in str(e)


def test_ac18_migration_strips_snapshotted_defaults():
    """AC-18: Migration strips snapshot copies of default rules from config."""
    import yaml
    from pacemaker.clean_code_rules import (
        load_rules,
        get_default_rules,
        _load_custom_config,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")
        defaults = get_default_rules()
        # Simulate old snapshot format: all defaults stored as rules
        with open(config_path, "w") as f:
            yaml.safe_dump({"rules": list(defaults), "deleted_rules": []}, f)

        # load_rules should trigger migration
        rules = load_rules(config_path)

        # After migration: config should have no snapshot copies
        custom = _load_custom_config(config_path)
        assert (
            len(custom["rules"]) == 0
        ), f"Expected 0 custom rules after migration, got {len(custom['rules'])}"
        # Result should still be all defaults
        assert len(rules) == len(defaults)


def test_ac21_format_rules_for_display_shows_source_tags():
    """AC-21: format_rules_for_display with config_path shows [default]/[override]/[custom] tags."""
    import yaml
    from pacemaker.clean_code_rules import (
        format_rules_for_display,
        load_rules,
        get_default_rules,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")
        defaults = get_default_rules()
        override_id = defaults[0]["id"]
        override = {
            "id": override_id,
            "name": "Override Name",
            "description": "Override Desc",
        }
        custom_rule = {"id": "my-custom", "name": "My Custom", "description": "Custom"}
        with open(config_path, "w") as f:
            yaml.safe_dump({"rules": [override, custom_rule], "deleted_rules": []}, f)

        rules = load_rules(config_path)
        output = format_rules_for_display(rules, config_path=config_path)

        assert "[override]" in output
        assert "[custom]" in output
        assert "[default]" in output


def test_ac22_modify_rule_strips_id_from_updates():
    """AC-22: modify_rule ignores 'id' key in updates dict."""
    from pacemaker.clean_code_rules import modify_rule, load_rules, get_default_rules

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")
        defaults = get_default_rules()
        target_id = defaults[0]["id"]

        # Pass id in updates — it should be stripped and NOT change the rule id
        modify_rule(
            config_path, target_id, {"id": "different-id", "description": "New desc"}
        )

        rules = load_rules(config_path)
        rule = next((r for r in rules if r["id"] == target_id), None)
        assert rule is not None, f"Original rule '{target_id}' must still exist"
        assert rule["description"] == "New desc"
        # Verify 'different-id' was NOT applied
        no_different = all(r["id"] != "different-id" for r in rules)
        assert no_different, "id field must not be changed by modify_rule"


def test_ac23_load_custom_config_skips_malformed_entries():
    """AC-23: _load_custom_config skips entries without 'id' field."""
    import yaml
    from pacemaker.clean_code_rules import _load_custom_config

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "rules.yaml")
        # Mix of valid and malformed entries
        with open(config_path, "w") as f:
            yaml.safe_dump(
                {
                    "rules": [
                        {"id": "valid-rule", "name": "Valid", "description": "Valid"},
                        {"name": "No ID", "description": "Missing id field"},
                        "not-a-dict",
                    ],
                    "deleted_rules": [],
                },
                f,
            )

        custom = _load_custom_config(config_path)
        assert len(custom["rules"]) == 1
        assert custom["rules"][0]["id"] == "valid-rule"


def test_ac24_write_config_propagates_oserror():
    """AC-24: _write_config raises OSError when write fails (e.g., read-only dir)."""
    import stat
    from pacemaker.clean_code_rules import _write_config

    with tempfile.TemporaryDirectory() as tmpdir:
        # Make tmpdir read-only
        os.chmod(tmpdir, stat.S_IRUSR | stat.S_IXUSR)
        config_path = os.path.join(tmpdir, "rules.yaml")

        try:
            _write_config(config_path, {"rules": [], "deleted_rules": []})
            assert False, "Expected OSError"
        except OSError:
            pass
        finally:
            # Restore permissions to allow cleanup
            os.chmod(tmpdir, stat.S_IRWXU)
