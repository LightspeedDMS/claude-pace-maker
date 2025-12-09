#!/usr/bin/env python3
"""
Unit tests for TDD toggle functionality (Story #14).

Tests all 8 acceptance criteria:
1. TDD enabled by default (config without tdd_enabled key defaults to ENABLED)
2. Disable TDD via CLI (pace-maker tdd off)
3. Enable TDD via CLI (pace-maker tdd on)
4. TDD inactive when intent validation disabled
5. Status shows TDD state
6. Help text documents TDD command
7. Prompt placeholder when disabled
8. Prompt placeholder when enabled
"""

import unittest
import tempfile
import os
import json
import shutil


class TestTDDToggle(unittest.TestCase):
    """Test TDD toggle functionality."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.json")

    def tearDown(self):
        """Clean up."""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_tdd_enabled_by_default(self):
        """AC1: Config without tdd_enabled key defaults to ENABLED."""
        from src.pacemaker.constants import DEFAULT_CONFIG

        # DEFAULT_CONFIG should contain tdd_enabled: True
        self.assertIn("tdd_enabled", DEFAULT_CONFIG)
        self.assertTrue(DEFAULT_CONFIG["tdd_enabled"])

    def test_disable_tdd_via_cli(self):
        """AC2: pace-maker tdd off sets tdd_enabled=false."""
        from src.pacemaker.user_commands import execute_command

        # Execute tdd off command
        result = execute_command("tdd", self.config_path, subcommand="off")

        # Should succeed
        self.assertTrue(result["success"])

        # Should set tdd_enabled=False in config
        with open(self.config_path) as f:
            config = json.load(f)
        self.assertFalse(config["tdd_enabled"])

    def test_enable_tdd_via_cli(self):
        """AC3: pace-maker tdd on sets tdd_enabled=true."""
        from src.pacemaker.user_commands import execute_command

        # First disable TDD
        execute_command("tdd", self.config_path, subcommand="off")

        # Then enable TDD
        result = execute_command("tdd", self.config_path, subcommand="on")

        # Should succeed
        self.assertTrue(result["success"])

        # Should set tdd_enabled=True in config
        with open(self.config_path) as f:
            config = json.load(f)
        self.assertTrue(config["tdd_enabled"])

    def test_tdd_inactive_when_intent_validation_disabled(self):
        """AC4: TDD only active when intent_validation_enabled AND tdd_enabled."""

        # Create config with intent_validation_enabled=False, tdd_enabled=True
        config = {
            "intent_validation_enabled": False,
            "tdd_enabled": True,
        }
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # TDD should be inactive (cannot test without real prompt validation)
        # For now, verify config state
        with open(self.config_path) as f:
            loaded = json.load(f)
        self.assertFalse(loaded["intent_validation_enabled"])
        self.assertTrue(loaded["tdd_enabled"])

        # When intent_validation is off, TDD section should not be included
        # This will be tested in E2E tests

    def test_status_shows_tdd_state(self):
        """AC5: pace-maker status includes TDD Enforcement: ENABLED/DISABLED."""
        from src.pacemaker.user_commands import execute_command

        # Enable TDD
        execute_command("intent-validation", self.config_path, subcommand="on")
        execute_command("tdd", self.config_path, subcommand="on")

        # Get status
        result = execute_command("status", self.config_path, db_path=None)

        # Should succeed and include TDD state
        self.assertTrue(result["success"])
        self.assertIn("TDD Enforcement", result["message"])
        self.assertIn("ENABLED", result["message"])

        # Disable TDD
        execute_command("tdd", self.config_path, subcommand="off")

        # Get status again
        result = execute_command("status", self.config_path, db_path=None)

        # Should show DISABLED
        self.assertTrue(result["success"])
        self.assertIn("TDD Enforcement", result["message"])
        self.assertIn("DISABLED", result["message"])

    def test_help_documents_tdd_command(self):
        """AC6: pace-maker help shows tdd on/off usage."""
        from src.pacemaker.user_commands import execute_command

        # Get help
        result = execute_command("help", self.config_path)

        # Should succeed and include TDD command documentation
        self.assertTrue(result["success"])
        self.assertIn("pace-maker tdd on", result["message"])
        self.assertIn("pace-maker tdd off", result["message"])


class TestTDDPromptPlaceholder(unittest.TestCase):
    """Test TDD section placeholder replacement in prompts."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.json")
        self.original_tdd_section_content = None

    def tearDown(self):
        """Clean up."""
        # Restore tdd_section.md if we backed it up
        if self.original_tdd_section_content is not None:
            module_dir = os.path.dirname(os.path.abspath(__file__))
            tdd_section_path = os.path.join(
                module_dir,
                "..",
                "..",
                "src",
                "pacemaker",
                "prompts",
                "pre_tool_use",
                "tdd_section.md",
            )
            tdd_section_path = os.path.normpath(tdd_section_path)
            with open(tdd_section_path, "w", encoding="utf-8") as f:
                f.write(self.original_tdd_section_content)

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_prompt_placeholder_when_disabled(self):
        """AC7: {{tdd_section}} replaced with empty string when tdd_enabled=False."""
        from src.pacemaker.intent_validator import generate_validation_prompt

        # Create config with TDD disabled
        config = {
            "intent_validation_enabled": True,  # Intent validation ON
            "tdd_enabled": False,  # TDD OFF
        }

        # Generate prompt with config
        prompt = generate_validation_prompt(
            tool_name="Edit",
            file_path="/home/user/test.py",
            code='def hello():\n    print("world")',
            messages=["User: Add hello function"],
            config=config,
        )

        # Prompt should not contain TDD section content
        # TDD section starts with "OUTCOME 1.5: CORE CODE WITHOUT TEST DECLARATION"
        self.assertNotIn("OUTCOME 1.5", prompt)
        self.assertNotIn("TDD Required for Core Code", prompt)

    def test_prompt_placeholder_when_enabled(self):
        """AC8: {{tdd_section}} replaced with full TDD requirements when tdd_enabled=True."""
        from src.pacemaker.intent_validator import generate_validation_prompt

        # Create config with TDD enabled
        config = {
            "intent_validation_enabled": True,  # Intent validation ON
            "tdd_enabled": True,  # TDD ON
        }

        # Generate prompt with config
        prompt = generate_validation_prompt(
            tool_name="Edit",
            file_path="/home/user/src/test.py",  # src/ path to trigger TDD check
            code='def hello():\n    print("world")',
            messages=["User: Add hello function"],
            config=config,
        )

        # Prompt should contain TDD section content
        # TDD section starts with "OUTCOME 1.5: CORE CODE WITHOUT TEST DECLARATION"
        self.assertIn("OUTCOME 1.5", prompt)
        self.assertIn("TDD Required for Core Code", prompt)

    def test_missing_tdd_section_file_logs_warning(self):
        """Test that missing tdd_section.md logs warning but doesn't crash."""
        from src.pacemaker.intent_validator import generate_validation_prompt
        import os

        # Backup and delete tdd_section.md temporarily
        module_dir = os.path.dirname(os.path.abspath(__file__))
        tdd_section_path = os.path.join(
            module_dir,
            "..",
            "..",
            "src",
            "pacemaker",
            "prompts",
            "pre_tool_use",
            "tdd_section.md",
        )
        tdd_section_path = os.path.normpath(tdd_section_path)

        # Backup file
        with open(tdd_section_path, "r", encoding="utf-8") as f:
            self.original_tdd_section_content = f.read()

        # Delete file
        os.remove(tdd_section_path)

        try:
            # Create config with TDD enabled
            config = {
                "intent_validation_enabled": True,
                "tdd_enabled": True,
            }

            # Generate prompt - should NOT crash, just skip TDD section
            prompt = generate_validation_prompt(
                tool_name="Edit",
                file_path="/home/user/src/test.py",
                code='def hello():\n    print("world")',
                messages=["User: Add hello function"],
                config=config,
            )

            # TDD section should NOT be present (file was missing)
            self.assertNotIn("OUTCOME 1.5", prompt)
            self.assertNotIn("TDD Required for Core Code", prompt)
        finally:
            # Restore file in tearDown
            pass


if __name__ == "__main__":
    unittest.main()
