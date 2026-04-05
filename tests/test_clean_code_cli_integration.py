#!/usr/bin/env python3
"""
CLI integration tests for clean-code command.

Tests CLI execution with subprocess, verifying actual command-line interface behavior.
Zero mocking - tests real CLI and file I/O.
"""

import subprocess
import os


def test_cli_clean_code_list_command():
    """
    Test that 'pace-maker clean-code list' executes successfully and displays rules.

    Tests CLI execution via subprocess with real file I/O.
    """
    # Execute: Run pace-maker clean-code list
    result = subprocess.run(
        ["pace-maker", "clean-code", "list"],
        capture_output=True,
        text=True,
    )

    # Assert: Command succeeds
    assert result.returncode == 0

    # Assert: Output contains rule information
    output = result.stdout
    # Default rules should be present
    assert "ID:" in output
    assert "Name:" in output
    assert "Description:" in output


def test_cli_clean_code_add_command_with_custom_config():
    """
    Test that 'pace-maker clean-code add' adds a rule to config file.

    Tests the add command logic and verifies file modification.
    Uses Python function directly because subprocess runs with real HOME
    while conftest overrides HOME to a fake path.
    """
    from pacemaker.constants import DEFAULT_CLEAN_CODE_RULES_PATH
    from pacemaker.clean_code_rules import load_rules, _write_config
    from pacemaker.user_commands import _execute_clean_code
    import shutil

    # Setup: Backup original config and create test config
    backup_path = None
    try:
        if os.path.exists(DEFAULT_CLEAN_CODE_RULES_PATH):
            backup_path = DEFAULT_CLEAN_CODE_RULES_PATH + ".backup"
            shutil.copy2(DEFAULT_CLEAN_CODE_RULES_PATH, backup_path)

        # Initialize with a custom test rule (new merge-strategy format)
        _write_config(
            DEFAULT_CLEAN_CODE_RULES_PATH,
            {
                "rules": [
                    {
                        "id": "test-init",
                        "name": "Test Init",
                        "description": "Initial test rule",
                    }
                ],
                "deleted_rules": [],
            },
        )

        # Execute: Add rule via Python function
        result = _execute_clean_code(
            'add --id cli-test-rule --name "CLI Test Rule" --description "Rule added via CLI test"'
        )

        # Assert: Command succeeds
        assert result["success"] is True
        assert (
            "added successfully" in result["message"].lower()
            or "\u2713" in result["message"]
        )

        # Assert: Rule was actually added to file (merged with defaults)
        rules = load_rules(DEFAULT_CLEAN_CODE_RULES_PATH)
        assert any(r["id"] == "cli-test-rule" for r in rules)
        added_rule = next(r for r in rules if r["id"] == "cli-test-rule")
        assert added_rule["name"] == "CLI Test Rule"
        assert added_rule["description"] == "Rule added via CLI test"

    finally:
        # Cleanup: Restore original config
        if backup_path and os.path.exists(backup_path):
            shutil.move(backup_path, DEFAULT_CLEAN_CODE_RULES_PATH)
        elif os.path.exists(DEFAULT_CLEAN_CODE_RULES_PATH):
            os.remove(DEFAULT_CLEAN_CODE_RULES_PATH)


def test_cli_clean_code_add_missing_arguments():
    """
    Test that 'pace-maker clean-code add' with missing arguments shows error.

    Tests error handling for incomplete CLI commands.
    """
    # Execute: Run add command without required arguments
    result = subprocess.run(
        ["pace-maker", "clean-code", "add"],
        capture_output=True,
        text=True,
    )

    # Assert: Command fails with non-zero exit code
    assert (
        result.returncode != 0
        or "error" in result.stdout.lower()
        or "usage" in result.stdout.lower()
    )


def test_cli_clean_code_modify_command():
    """
    Test that 'pace-maker clean-code modify' updates an existing rule.

    Tests the modify command logic with real file I/O.
    Uses Python function directly because subprocess runs with real HOME
    while conftest overrides HOME to a fake path.
    """
    from pacemaker.constants import DEFAULT_CLEAN_CODE_RULES_PATH
    from pacemaker.clean_code_rules import load_rules, _write_config
    from pacemaker.user_commands import _execute_clean_code
    import shutil

    # Setup: Backup original config and create test config
    backup_path = None
    try:
        if os.path.exists(DEFAULT_CLEAN_CODE_RULES_PATH):
            backup_path = DEFAULT_CLEAN_CODE_RULES_PATH + ".backup"
            shutil.copy2(DEFAULT_CLEAN_CODE_RULES_PATH, backup_path)

        # Initialize with a custom test rule (new merge-strategy format)
        _write_config(
            DEFAULT_CLEAN_CODE_RULES_PATH,
            {
                "rules": [
                    {
                        "id": "modify-test",
                        "name": "Original Name",
                        "description": "Original description",
                    }
                ],
                "deleted_rules": [],
            },
        )

        # Execute: Modify rule via Python function
        result = _execute_clean_code(
            'modify --id modify-test --description "Updated via CLI test"'
        )

        # Assert: Command succeeds
        assert result["success"] is True
        assert (
            "modified successfully" in result["message"].lower()
            or "\u2713" in result["message"]
        )

        # Assert: Rule was actually modified in file (merged with defaults)
        rules = load_rules(DEFAULT_CLEAN_CODE_RULES_PATH)
        modified_rule = next(r for r in rules if r["id"] == "modify-test")
        assert modified_rule["description"] == "Updated via CLI test"
        assert modified_rule["name"] == "Original Name"  # Name unchanged

    finally:
        # Cleanup: Restore original config
        if backup_path and os.path.exists(backup_path):
            shutil.move(backup_path, DEFAULT_CLEAN_CODE_RULES_PATH)
        elif os.path.exists(DEFAULT_CLEAN_CODE_RULES_PATH):
            os.remove(DEFAULT_CLEAN_CODE_RULES_PATH)


def test_cli_clean_code_remove_command():
    """
    Test that 'pace-maker clean-code remove' deletes a rule.

    Tests the remove command logic with real file I/O.
    Uses Python function directly because subprocess runs with real HOME
    while conftest overrides HOME to a fake path.
    """
    from pacemaker.constants import DEFAULT_CLEAN_CODE_RULES_PATH
    from pacemaker.clean_code_rules import load_rules, _write_config
    from pacemaker.user_commands import _execute_clean_code
    import shutil

    # Setup: Backup original config and create test config
    backup_path = None
    try:
        if os.path.exists(DEFAULT_CLEAN_CODE_RULES_PATH):
            backup_path = DEFAULT_CLEAN_CODE_RULES_PATH + ".backup"
            shutil.copy2(DEFAULT_CLEAN_CODE_RULES_PATH, backup_path)

        # Initialize with two custom test rules (new merge-strategy format)
        _write_config(
            DEFAULT_CLEAN_CODE_RULES_PATH,
            {
                "rules": [
                    {
                        "id": "keep-rule",
                        "name": "Keep This",
                        "description": "Rule to keep",
                    },
                    {
                        "id": "remove-rule",
                        "name": "Remove This",
                        "description": "Rule to remove",
                    },
                ],
                "deleted_rules": [],
            },
        )

        # Execute: Remove rule via Python function
        result = _execute_clean_code("remove --id remove-rule")

        # Assert: Command succeeds
        assert result["success"] is True
        assert (
            "removed successfully" in result["message"].lower()
            or "\u2713" in result["message"]
        )

        # Assert: Rule was actually removed (merged with defaults, so check absence)
        rules = load_rules(DEFAULT_CLEAN_CODE_RULES_PATH)
        assert any(r["id"] == "keep-rule" for r in rules)
        assert all(r["id"] != "remove-rule" for r in rules)

    finally:
        # Cleanup: Restore original config
        if backup_path and os.path.exists(backup_path):
            shutil.move(backup_path, DEFAULT_CLEAN_CODE_RULES_PATH)
        elif os.path.exists(DEFAULT_CLEAN_CODE_RULES_PATH):
            os.remove(DEFAULT_CLEAN_CODE_RULES_PATH)


def test_cli_clean_code_list_shows_error_on_invalid_yaml():
    """
    Test that clean-code list shows explicit error on invalid YAML.

    Tests that CLI does NOT silently fall back to defaults when YAML is invalid.
    Acceptance Criteria: Show explicit error, do NOT use defaults silently.

    Note: Uses the Python function directly rather than subprocess because
    subprocess runs with real HOME while conftest overrides HOME to a fake path,
    making it impossible to write test fixtures that the subprocess can read.
    """
    from pacemaker.user_commands import _execute_clean_code
    from pacemaker.constants import DEFAULT_CLEAN_CODE_RULES_PATH
    import shutil

    # Setup: Backup original config and create invalid YAML
    backup_path = None
    try:
        if os.path.exists(DEFAULT_CLEAN_CODE_RULES_PATH):
            backup_path = DEFAULT_CLEAN_CODE_RULES_PATH + ".backup"
            shutil.copy2(DEFAULT_CLEAN_CODE_RULES_PATH, backup_path)

        # Create invalid YAML file
        os.makedirs(os.path.dirname(DEFAULT_CLEAN_CODE_RULES_PATH), exist_ok=True)
        with open(DEFAULT_CLEAN_CODE_RULES_PATH, "w") as f:
            f.write("rules:\n")
            f.write("  - id: test\n")
            f.write("    name: Test\n")
            f.write("  invalid yaml here [[[")  # Invalid YAML syntax

        # Execute: Call Python function directly
        result = _execute_clean_code("list")

        # Assert: Command fails with explicit error message
        assert result["success"] is False
        assert "error" in result["message"].lower()
        # Should NOT show default rules (no silent fallback)
        assert "hardcoded-secrets" not in result["message"].lower()

    finally:
        # Cleanup: Restore original config
        if backup_path and os.path.exists(backup_path):
            shutil.move(backup_path, DEFAULT_CLEAN_CODE_RULES_PATH)
        elif os.path.exists(DEFAULT_CLEAN_CODE_RULES_PATH):
            os.remove(DEFAULT_CLEAN_CODE_RULES_PATH)


def test_cli_help_includes_clean_code_commands():
    """
    Test that 'pace-maker help' includes clean-code commands in output.

    Tests Definition of Done requirement: "CLI help text includes clean-code commands"
    """
    # Execute: Run pace-maker help
    result = subprocess.run(
        ["pace-maker", "help"],
        capture_output=True,
        text=True,
    )

    # Assert: Command succeeds
    assert result.returncode == 0

    # Assert: Output includes clean-code commands
    output = result.stdout
    assert "clean-code list" in output
    assert "clean-code add" in output
    assert "clean-code modify" in output
    assert "clean-code remove" in output

    # Assert: Output includes CLEAN CODE RULES section
    assert "CLEAN CODE RULES:" in output
    assert "clean_code_rules.yaml" in output
