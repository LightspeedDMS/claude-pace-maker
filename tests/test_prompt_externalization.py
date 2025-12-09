#!/usr/bin/env python3
"""
Unit tests for externalized prompt template loading.

Tests that the validation prompt template can be loaded from an external
markdown file and properly used in the intent validation system.
"""

import unittest
import tempfile
import os
import shutil
from unittest.mock import patch


class TestPromptExternalization(unittest.TestCase):
    """Test externalized prompt template loading."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()

    def tearDown(self):
        """Clean up."""
        os.chdir(self.original_cwd)
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_load_prompt_template_from_file(self):
        """Should load validation prompt template from external markdown file."""
        from src.pacemaker.intent_validator import load_prompt_template

        # Create test prompt file
        prompt_content = """Test prompt template with variables:
{all_user_messages}
{last_assistant_messages}
{last_assistant}
{n}
"""
        prompt_file = os.path.join(self.temp_dir, "test_prompt.md")
        with open(prompt_file, "w") as f:
            f.write(prompt_content)

        # Load template
        template = load_prompt_template(prompt_file)

        # Should return template content
        self.assertEqual(template, prompt_content)
        self.assertIn("{all_user_messages}", template)
        self.assertIn("{last_assistant_messages}", template)
        self.assertIn("{last_assistant}", template)
        self.assertIn("{n}", template)

    def test_load_prompt_template_file_not_found(self):
        """Should raise FileNotFoundError if prompt file doesn't exist."""
        from src.pacemaker.intent_validator import load_prompt_template

        nonexistent_file = os.path.join(self.temp_dir, "nonexistent.md")

        with self.assertRaises(FileNotFoundError):
            load_prompt_template(nonexistent_file)

    def test_load_prompt_template_with_utf8_encoding(self):
        """Should correctly load prompt with UTF-8 characters."""
        from src.pacemaker.intent_validator import load_prompt_template

        # Create prompt with special characters
        prompt_content = """⚠️ CRITICAL RULES ⚠️
>>> CLAUDE'S RESPONSE <<<
👍 APPROVED or 👎 BLOCKED
Template vars: {all_user_messages} {n}
"""
        prompt_file = os.path.join(self.temp_dir, "utf8_prompt.md")
        with open(prompt_file, "w", encoding="utf-8") as f:
            f.write(prompt_content)

        # Load template
        template = load_prompt_template(prompt_file)

        # Should preserve UTF-8 characters
        self.assertIn("⚠️", template)
        self.assertIn(">>>", template)
        self.assertIn("👍", template)
        self.assertIn("👎", template)

    def test_get_prompt_template_uses_module_default_location(self):
        """Should use default prompt location relative to module."""
        from src.pacemaker.intent_validator import get_prompt_template

        # Should not raise - file should exist at default location
        template = get_prompt_template()

        # Should be a non-empty string
        self.assertIsInstance(template, str)
        self.assertGreater(len(template), 0)

        # Should contain expected template variable
        self.assertIn("{conversation_context}", template)

        # Should contain key validation instructions
        self.assertIn("APPROVED", template)
        self.assertIn("BLOCKED:", template)

    def test_get_prompt_template_raises_on_missing_file(self):
        """Should raise FileNotFoundError if external file is missing."""
        from src.pacemaker.intent_validator import get_prompt_template

        # Mock os.path.exists to return False
        with patch("src.pacemaker.intent_validator.os.path.exists") as mock_exists:
            mock_exists.return_value = False

            # Should raise FileNotFoundError with helpful message
            with self.assertRaises(FileNotFoundError) as ctx:
                get_prompt_template()

            # Error message should mention broken installation
            self.assertIn("broken installation", str(ctx.exception))
            self.assertIn("./install.sh", str(ctx.exception))

    def test_build_validation_prompt_uses_externalized_template(self):
        """Should use externalized template in build_validation_prompt."""
        from src.pacemaker.intent_validator import build_validation_prompt

        # Build prompt with formatted conversation context
        conversation_context = "User: Test message\nAssistant: Test response"
        prompt = build_validation_prompt(conversation_context)

        # Should contain conversation context
        self.assertIn("Test message", prompt)
        self.assertIn("Test response", prompt)

        # Should contain template structure from external file
        self.assertIn("APPROVED", prompt)
        self.assertIn("BLOCKED:", prompt)

    def test_externalized_prompt_contains_required_sections(self):
        """External prompt file should contain all required validation sections."""
        from src.pacemaker.intent_validator import get_prompt_template

        # Load external template
        external_template = get_prompt_template()

        # Should contain critical sections
        self.assertIn("APPROVED", external_template)
        self.assertIn("BLOCKED:", external_template)
        # Template uses {conversation_context} placeholder now
        self.assertIn("{conversation_context}", external_template)

    def test_prompt_file_location_is_correct(self):
        """Prompt file should be at src/pacemaker/prompts/stop/stop_hook_validator_prompt.md"""
        import src.pacemaker.intent_validator as validator_module

        # Calculate expected path (now in stop subfolder)
        module_dir = os.path.dirname(validator_module.__file__)
        expected_path = os.path.join(
            module_dir, "prompts", "stop", "stop_hook_validator_prompt.md"
        )

        # File should exist
        self.assertTrue(
            os.path.exists(expected_path), f"Prompt file not found at: {expected_path}"
        )

        # Should be readable
        with open(expected_path, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertGreater(len(content), 0)


class TestPromptTemplateVariables(unittest.TestCase):
    """Test that all required template variables are present."""

    def test_externalized_prompt_has_all_variables(self):
        """External prompt should have all required template variables."""
        from src.pacemaker.intent_validator import get_prompt_template

        template = get_prompt_template()

        # Required variable (now just conversation_context)
        required_var = "{conversation_context}"

        self.assertIn(
            required_var,
            template,
            f"Required variable {required_var} not found in template",
        )

    def test_externalized_prompt_has_response_format(self):
        """External prompt should specify APPROVED/BLOCKED response format."""
        from src.pacemaker.intent_validator import get_prompt_template

        template = get_prompt_template()

        # Should have response format instructions
        self.assertIn("APPROVED", template)
        self.assertIn("BLOCKED:", template)

    def test_externalized_prompt_has_tempo_liveliness_instructions(self):
        """External prompt should have tempo liveliness check instructions."""
        from src.pacemaker.intent_validator import get_prompt_template

        template = get_prompt_template()

        # Should contain tempo liveliness detection
        self.assertIn("TEMPO", template.upper())
        self.assertIn("LIVELINESS", template.upper())


if __name__ == "__main__":
    unittest.main()
