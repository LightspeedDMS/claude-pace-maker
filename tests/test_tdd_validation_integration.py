#!/usr/bin/env python3
"""
Integration tests for TDD validation prompt generation (Story #14).

Tests the actual prompt generation logic with all 4 combinations of
intent_validation_enabled and tdd_enabled flags.
"""

import unittest
import tempfile
import os
import shutil


class TestTDDValidationIntegration(unittest.TestCase):
    """Test TDD section injection into validation prompts."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.json")

    def tearDown(self):
        """Clean up."""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
        # Clean up env var
        if "CLAUDE_PACE_MAKER_CONFIG_PATH" in os.environ:
            del os.environ["CLAUDE_PACE_MAKER_CONFIG_PATH"]

    def _generate_validation_prompt(
        self, intent_enabled: bool, tdd_enabled: bool
    ) -> str:
        """
        Helper to generate validation prompt with specific config.

        Args:
            intent_enabled: Value for intent_validation_enabled
            tdd_enabled: Value for tdd_enabled

        Returns:
            Generated prompt string
        """
        from src.pacemaker.intent_validator import generate_validation_prompt

        # Create config dict (no file I/O needed - pass directly)
        config = {
            "intent_validation_enabled": intent_enabled,
            "tdd_enabled": tdd_enabled,
        }

        # Use the actual production function with injected config
        prompt = generate_validation_prompt(
            messages=["User: Add hello function"],
            code='def hello():\n    print("world")',
            file_path="/home/user/src/test.py",
            tool_name="Edit",
            config=config,
        )

        return prompt

    def test_tdd_section_included_when_both_enabled(self):
        """
        AC4 + AC8: TDD section included when intent_validation=ON and tdd=ON.
        """
        prompt = self._generate_validation_prompt(intent_enabled=True, tdd_enabled=True)

        # Prompt should contain TDD section content
        self.assertIn("OUTCOME 1.5", prompt)
        self.assertIn("TDD Required for Core Code", prompt)

    def test_tdd_section_excluded_when_intent_disabled(self):
        """
        AC4: TDD section excluded when intent_validation=OFF (even if tdd=ON).
        """
        prompt = self._generate_validation_prompt(
            intent_enabled=False, tdd_enabled=True
        )

        # Prompt should NOT contain TDD section content
        self.assertNotIn("OUTCOME 1.5", prompt)
        self.assertNotIn("TDD Required for Core Code", prompt)

    def test_tdd_section_excluded_when_tdd_disabled(self):
        """
        AC7: TDD section excluded when tdd=OFF (even if intent_validation=ON).
        """
        prompt = self._generate_validation_prompt(
            intent_enabled=True, tdd_enabled=False
        )

        # Prompt should NOT contain TDD section content
        self.assertNotIn("OUTCOME 1.5", prompt)
        self.assertNotIn("TDD Required for Core Code", prompt)

    def test_tdd_section_excluded_when_both_disabled(self):
        """
        AC4 + AC7: TDD section excluded when both intent_validation=OFF and tdd=OFF.
        """
        prompt = self._generate_validation_prompt(
            intent_enabled=False, tdd_enabled=False
        )

        # Prompt should NOT contain TDD section content
        self.assertNotIn("OUTCOME 1.5", prompt)
        self.assertNotIn("TDD Required for Core Code", prompt)


if __name__ == "__main__":
    unittest.main()
