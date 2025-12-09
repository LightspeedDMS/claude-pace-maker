#!/usr/bin/env python3
"""
Unit tests for user command message externalization.

Tests that user command messages are loaded from external JSON file.
"""

import unittest
import tempfile
import os
import json
import shutil


class TestUserCommandMessages(unittest.TestCase):
    """Test externalized user command messages."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()

    def tearDown(self):
        """Clean up."""
        os.chdir(self.original_cwd)
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_load_json_messages_success(self):
        """Should load messages from JSON file."""
        from src.pacemaker.prompt_loader import PromptLoader

        # Create test messages file
        messages = {
            "pace_maker": {
                "enabled": "Pace Maker enabled",
                "disabled": "Pace Maker disabled",
            },
            "version": {"message": "Version {version}"},
        }

        # Create prompts structure
        prompts_dir = os.path.join(self.temp_dir, "prompts")
        user_commands_dir = os.path.join(prompts_dir, "user_commands")
        os.makedirs(user_commands_dir)

        messages_file = os.path.join(user_commands_dir, "messages.json")
        with open(messages_file, "w") as f:
            json.dump(messages, f)

        # Load messages
        loader = PromptLoader(prompts_dir)
        loaded = loader.load_json_messages("messages.json", "user_commands")

        # Should return parsed JSON
        self.assertEqual(loaded, messages)
        self.assertIn("pace_maker", loaded)
        self.assertIn("version", loaded)
        self.assertEqual(loaded["pace_maker"]["enabled"], "Pace Maker enabled")

    def test_load_json_messages_file_not_found(self):
        """Should raise FileNotFoundError if messages file doesn't exist."""
        from src.pacemaker.prompt_loader import PromptLoader

        prompts_dir = os.path.join(self.temp_dir, "prompts")
        os.makedirs(prompts_dir)

        loader = PromptLoader(prompts_dir)

        with self.assertRaises(FileNotFoundError):
            loader.load_json_messages("nonexistent.json", "user_commands")

    def test_messages_json_exists_in_default_location(self):
        """Should have messages.json at default location."""
        from src.pacemaker.prompt_loader import PromptLoader

        loader = PromptLoader()

        # Should not raise - file should exist at default location
        messages = loader.load_json_messages("messages.json", "user_commands")

        # Should be a dict
        self.assertIsInstance(messages, dict)

        # Should contain expected message categories
        self.assertIn("pace_maker", messages)
        self.assertIn("version", messages)
        self.assertIn("weekly_limit", messages)
        self.assertIn("five_hour_limit", messages)
        self.assertIn("loglevel", messages)
        self.assertIn("reminder", messages)
        self.assertIn("intent_validation", messages)
        self.assertIn("tempo", messages)

    def test_messages_have_required_fields(self):
        """Messages should have all required fields for each command."""
        from src.pacemaker.prompt_loader import PromptLoader

        loader = PromptLoader()
        messages = loader.load_json_messages("messages.json", "user_commands")

        # Pace maker messages
        self.assertIn("enabled", messages["pace_maker"])
        self.assertIn("disabled", messages["pace_maker"])
        self.assertIn("error_enabling", messages["pace_maker"])
        self.assertIn("error_disabling", messages["pace_maker"])

        # Version message
        self.assertIn("message", messages["version"])

        # Weekly limit messages
        self.assertIn("enabled", messages["weekly_limit"])
        self.assertIn("disabled", messages["weekly_limit"])

        # Tempo messages
        self.assertIn("enabled", messages["tempo"])
        self.assertIn("disabled", messages["tempo"])
        self.assertIn("session_enabled", messages["tempo"])
        self.assertIn("session_disabled", messages["tempo"])

    def test_get_message_with_variable_replacement(self):
        """Should support variable replacement in messages."""
        from src.pacemaker.prompt_loader import PromptLoader

        loader = PromptLoader()
        messages = loader.load_json_messages("messages.json", "user_commands")

        # Version message has placeholder
        version_msg = messages["version"]["message"]
        self.assertIn("{version}", version_msg)

        # Can replace variable
        replaced = version_msg.replace("{version}", "1.0.0")
        self.assertIn("1.0.0", replaced)
        self.assertNotIn("{version}", replaced)

        # Error messages have placeholders
        error_msg = messages["pace_maker"]["error_enabling"]
        self.assertIn("{error}", error_msg)


if __name__ == "__main__":
    unittest.main()
