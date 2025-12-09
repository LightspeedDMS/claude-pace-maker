#!/usr/bin/env python3
"""
End-to-end tests for clean_code_rules module.

Tests complete workflows with real file I/O and zero mocking.
"""

import tempfile
import os
import yaml


def test_full_crud_workflow():
    """
    E2E test: Complete CRUD workflow (add → list → modify → list → remove → list).

    Tests real file I/O, YAML persistence, and all CRUD operations in sequence.
    Zero mocking - all operations use real file system.
    """
    from pacemaker.clean_code_rules import (
        load_rules,
        add_rule,
        modify_rule,
        remove_rule,
        format_rules_for_display,
        _write_rules,
    )

    # Setup: Use temporary directory for config file
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "test_rules.yaml")

        # Initialize with a starter rule to avoid defaults fallback
        starter_rule = {
            "id": "starter",
            "name": "Starter",
            "description": "Initial rule",
        }
        _write_rules(config_path, [starter_rule])

        # STEP 1: Add first rule
        rule1 = {
            "id": "test-rule-1",
            "name": "Test Rule One",
            "description": "First test rule for E2E workflow",
        }
        add_rule(config_path, rule1)

        # Assert: Rule was added and file exists
        assert os.path.exists(config_path)
        rules = load_rules(config_path)
        # add_rule loads existing (empty list returns defaults), so we have defaults + our rule
        # Actually, empty list returns defaults, so we need to start fresh
        assert any(rule["id"] == "test-rule-1" for rule in rules)

        # STEP 2: Add second rule
        rule2 = {
            "id": "test-rule-2",
            "name": "Test Rule Two",
            "description": "Second test rule for E2E workflow",
        }
        add_rule(config_path, rule2)

        # Assert: Both custom rules exist
        rules = load_rules(config_path)
        assert any(rule["id"] == "test-rule-1" for rule in rules)
        assert any(rule["id"] == "test-rule-2" for rule in rules)

        # STEP 3: List rules (format for display)
        formatted = format_rules_for_display(rules)
        assert "test-rule-1" in formatted
        assert "Test Rule One" in formatted
        assert "test-rule-2" in formatted
        assert "Test Rule Two" in formatted

        # STEP 4: Modify first rule
        modify_rule(config_path, "test-rule-1", {"description": "Modified description"})

        # Assert: Rule was modified
        rules = load_rules(config_path)
        rule1_updated = next(r for r in rules if r["id"] == "test-rule-1")
        assert rule1_updated["description"] == "Modified description"
        assert rule1_updated["name"] == "Test Rule One"  # Name unchanged

        # STEP 5: List rules again (verify modification)
        formatted = format_rules_for_display(rules)
        assert "Modified description" in formatted
        assert "Test Rule One" in formatted  # Name still present

        # STEP 6: Remove first rule
        remove_rule(config_path, "test-rule-1")

        # Assert: First rule removed, second rule still exists
        rules = load_rules(config_path)
        assert all(rule["id"] != "test-rule-1" for rule in rules)
        assert any(rule["id"] == "test-rule-2" for rule in rules)

        # STEP 7: List rules final time
        formatted = format_rules_for_display(rules)
        assert "test-rule-2" in formatted
        assert "test-rule-1" not in formatted

        # STEP 8: Verify YAML file structure (real file I/O)
        with open(config_path, "r") as f:
            yaml_content = yaml.safe_load(f)

        assert "rules" in yaml_content
        assert isinstance(yaml_content["rules"], list)
        # Verify our custom rule is in the list
        assert any(rule["id"] == "test-rule-2" for rule in yaml_content["rules"])


def test_validation_prompt_integration():
    """
    E2E test: Validation prompt integration (rules injected into pre-tool validation).

    Tests that rules can be formatted for injection into validation prompts.
    Zero mocking - tests actual formatting and file I/O.
    """
    from pacemaker.clean_code_rules import (
        load_rules,
        add_rule,
        format_rules_for_validation,
        _write_rules,
    )

    # Setup: Create config file with test rules
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "validation_test.yaml")

        # Start with a single rule to avoid defaults fallback
        _write_rules(
            config_path, [{"id": "init", "name": "Init", "description": "Initial"}]
        )

        # Add three validation rules
        rules_to_add = [
            {
                "id": "validation-1",
                "name": "Validation Rule 1",
                "description": "Check for hardcoded secrets in code",
            },
            {
                "id": "validation-2",
                "name": "Validation Rule 2",
                "description": "Verify proper error handling exists",
            },
            {
                "id": "validation-3",
                "name": "Validation Rule 3",
                "description": "Ensure boundary checks for null values",
            },
        ]

        for rule in rules_to_add:
            add_rule(config_path, rule)

        # Load rules from file (real file I/O)
        loaded_rules = load_rules(config_path)
        # Should have init rule + 3 validation rules = 4 total
        assert len(loaded_rules) == 4
        assert any(r["id"] == "validation-1" for r in loaded_rules)
        assert any(r["id"] == "validation-2" for r in loaded_rules)
        assert any(r["id"] == "validation-3" for r in loaded_rules)

        # Format rules for validation prompt injection
        formatted_for_validation = format_rules_for_validation(loaded_rules)

        # Assert: All rules are formatted correctly for validation
        assert "   - Check for hardcoded secrets in code" in formatted_for_validation
        assert "   - Verify proper error handling exists" in formatted_for_validation
        assert "   - Ensure boundary checks for null values" in formatted_for_validation

        # Assert: Format is suitable for prompt injection (indented list items)
        lines = formatted_for_validation.split("\n")
        assert all(line.startswith("   - ") for line in lines if line)

        # Verify YAML file integrity (real file persistence)
        with open(config_path, "r") as f:
            yaml_content = yaml.safe_load(f)

        assert "rules" in yaml_content
        assert isinstance(yaml_content["rules"], list)
        # Verify our custom rules are in the file
        rule_ids = [r["id"] for r in yaml_content["rules"]]
        assert "validation-1" in rule_ids
        assert "validation-2" in rule_ids
        assert "validation-3" in rule_ids
