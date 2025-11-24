#!/usr/bin/env python3
"""
Unit tests for UserPromptSubmit hook - Intent-based validation (Story #9).

Tests AC1, AC3, AC4, AC5:
- Plain text prompt capture and storage
- Slash command expansion and resolution
- Project-level command precedence
- Missing command fallback to plain text
"""

import unittest
import tempfile
import os
import json


class TestUserPromptSubmitPlainText(unittest.TestCase):
    """Test AC1: Plain text prompt completion validation."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.prompts_dir = os.path.join(self.temp_dir, "prompts")
        os.makedirs(self.prompts_dir, exist_ok=True)

    def tearDown(self):
        """Clean up."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_plain_text_prompt_storage(self):
        """Should store plain text prompt in JSON file with session ID."""
        from src.pacemaker.prompt_storage import store_user_prompt

        session_id = "test-session-123"
        prompt = "implement a calculator"

        # Call the function
        result = store_user_prompt(
            session_id=session_id, raw_prompt=prompt, prompts_dir=self.prompts_dir
        )

        # Verify JSON file was created
        expected_path = os.path.join(self.prompts_dir, f"{session_id}.json")
        self.assertTrue(os.path.exists(expected_path))

        # Verify content (NEW FORMAT)
        with open(expected_path) as f:
            data = json.load(f)

        self.assertEqual(data["session_id"], session_id)
        self.assertIn("prompts", data)
        self.assertEqual(len(data["prompts"]), 1)

        prompt_entry = data["prompts"][0]
        self.assertEqual(prompt_entry["raw_prompt"], prompt)
        self.assertEqual(
            prompt_entry["expanded_prompt"], prompt
        )  # Plain text: no expansion
        self.assertIn("timestamp", prompt_entry)
        self.assertEqual(prompt_entry["sequence"], 1)
        self.assertTrue(result)

    def test_plain_text_prompt_no_expansion(self):
        """Plain text prompts should have expanded_prompt equal to raw_prompt."""
        from src.pacemaker.prompt_storage import store_user_prompt

        session_id = "test-session-456"
        prompt = "add authentication system"

        store_user_prompt(
            session_id=session_id, raw_prompt=prompt, prompts_dir=self.prompts_dir
        )

        # Read file (NEW FORMAT)
        prompt_file = os.path.join(self.prompts_dir, f"{session_id}.json")
        with open(prompt_file) as f:
            data = json.load(f)

        # Both should be identical for plain text
        prompt_entry = data["prompts"][0]
        self.assertEqual(prompt_entry["raw_prompt"], prompt_entry["expanded_prompt"])
        self.assertEqual(prompt_entry["expanded_prompt"], "add authentication system")


class TestUserPromptSubmitSlashCommands(unittest.TestCase):
    """Test AC3, AC4, AC5: Slash command expansion and resolution."""

    def setUp(self):
        """Set up temp environment with command files."""
        self.temp_dir = tempfile.mkdtemp()
        self.prompts_dir = os.path.join(self.temp_dir, "prompts")
        os.makedirs(self.prompts_dir, exist_ok=True)

        # Create mock command directories
        self.project_commands_dir = os.path.join(
            self.temp_dir, "project", ".claude", "commands"
        )
        self.global_commands_dir = os.path.join(
            self.temp_dir, "global", ".claude", "commands"
        )
        os.makedirs(self.project_commands_dir, exist_ok=True)
        os.makedirs(self.global_commands_dir, exist_ok=True)

    def tearDown(self):
        """Clean up."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_slash_command_detection(self):
        """Should detect slash commands starting with '/'."""
        from src.pacemaker.prompt_storage import is_slash_command

        # Slash commands
        self.assertTrue(is_slash_command("/implement-epic user-auth"))
        self.assertTrue(is_slash_command("/test"))
        self.assertTrue(is_slash_command("/foo-bar-baz"))

        # Not slash commands
        self.assertFalse(is_slash_command("implement a feature"))
        self.assertFalse(is_slash_command("// this is a comment"))
        self.assertFalse(is_slash_command(""))

    def test_slash_command_expansion_from_global(self):
        """AC3: Should expand slash command by reading command file."""
        from src.pacemaker.prompt_storage import store_user_prompt

        # Create global command file
        command_file = os.path.join(self.global_commands_dir, "implement-epic.md")
        command_content = """# Implement Epic Command

This command implements an epic using story-by-story TDD workflow.

## Instructions
1. Read epic specification
2. Implement each story with TDD
3. Run all tests
"""
        with open(command_file, "w") as f:
            f.write(command_content)

        session_id = "test-session-789"
        raw_prompt = "/implement-epic user-auth"

        # Store with global commands directory
        store_user_prompt(
            session_id=session_id,
            raw_prompt=raw_prompt,
            prompts_dir=self.prompts_dir,
            project_commands_dir=None,  # No project commands
            global_commands_dir=self.global_commands_dir,
        )

        # Verify expansion (NEW FORMAT)
        prompt_file = os.path.join(self.prompts_dir, f"{session_id}.json")
        with open(prompt_file) as f:
            data = json.load(f)

        prompt_entry = data["prompts"][0]
        self.assertEqual(prompt_entry["raw_prompt"], "/implement-epic user-auth")
        self.assertEqual(prompt_entry["expanded_prompt"], command_content)

    def test_project_command_precedence(self):
        """AC4: Project-level command should take precedence over global."""
        from src.pacemaker.prompt_storage import store_user_prompt

        # Create both project and global command files with different content
        project_command = os.path.join(self.project_commands_dir, "foo.md")
        global_command = os.path.join(self.global_commands_dir, "foo.md")

        with open(project_command, "w") as f:
            f.write("PROJECT LEVEL COMMAND")

        with open(global_command, "w") as f:
            f.write("GLOBAL LEVEL COMMAND")

        session_id = "test-precedence"
        raw_prompt = "/foo"

        # Store with both directories available
        store_user_prompt(
            session_id=session_id,
            raw_prompt=raw_prompt,
            prompts_dir=self.prompts_dir,
            project_commands_dir=self.project_commands_dir,
            global_commands_dir=self.global_commands_dir,
        )

        # Verify project command was used (NEW FORMAT)
        prompt_file = os.path.join(self.prompts_dir, f"{session_id}.json")
        with open(prompt_file) as f:
            data = json.load(f)

        prompt_entry = data["prompts"][0]
        self.assertEqual(prompt_entry["expanded_prompt"], "PROJECT LEVEL COMMAND")

    def test_missing_command_fallback_to_plain_text(self):
        """AC5: Missing command should be treated as plain text."""
        from src.pacemaker.prompt_storage import store_user_prompt

        session_id = "test-missing"
        raw_prompt = "/nonexistent-command do something"

        # Store with no matching command file
        store_user_prompt(
            session_id=session_id,
            raw_prompt=raw_prompt,
            prompts_dir=self.prompts_dir,
            project_commands_dir=self.project_commands_dir,
            global_commands_dir=self.global_commands_dir,
        )

        # Verify fallback to plain text (no expansion) (NEW FORMAT)
        prompt_file = os.path.join(self.prompts_dir, f"{session_id}.json")
        with open(prompt_file) as f:
            data = json.load(f)

        prompt_entry = data["prompts"][0]
        self.assertEqual(
            prompt_entry["raw_prompt"], "/nonexistent-command do something"
        )
        self.assertEqual(
            prompt_entry["expanded_prompt"], "/nonexistent-command do something"
        )

    def test_slash_command_name_extraction(self):
        """Should correctly extract command name from slash command."""
        from src.pacemaker.prompt_storage import extract_command_name

        self.assertEqual(
            extract_command_name("/implement-epic user-auth"), "implement-epic"
        )
        self.assertEqual(extract_command_name("/test"), "test")
        self.assertEqual(extract_command_name("/foo-bar-baz arg1 arg2"), "foo-bar-baz")
        self.assertEqual(extract_command_name("/single"), "single")


class TestUserPromptSubmitHookIntegration(unittest.TestCase):
    """Test UserPromptSubmit hook integration with hook.py."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.prompts_dir = os.path.join(self.temp_dir, "prompts")
        os.makedirs(self.prompts_dir, exist_ok=True)

        # Patch HOME to use temp directory
        self.original_home = os.environ.get("HOME")
        os.environ["HOME"] = self.temp_dir

    def tearDown(self):
        """Clean up."""
        import shutil

        if self.original_home:
            os.environ["HOME"] = self.original_home
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_user_prompt_submit_hook_parses_json_input(self):
        """Hook should parse JSON input from Claude Code stdin."""
        from src.pacemaker.hook import parse_user_prompt_input

        # Simulate Claude Code JSON input
        stdin_json = json.dumps(
            {"session_id": "sess-12345", "prompt": "implement a calculator"}
        )

        result = parse_user_prompt_input(stdin_json)

        self.assertEqual(result["session_id"], "sess-12345")
        self.assertEqual(result["prompt"], "implement a calculator")

    def test_user_prompt_submit_hook_handles_plain_text_fallback(self):
        """Hook should handle non-JSON input as plain text."""
        from src.pacemaker.hook import parse_user_prompt_input

        plain_text = "just some plain text prompt"

        result = parse_user_prompt_input(plain_text)

        # Should generate session ID and use text as prompt
        self.assertIn("session_id", result)
        self.assertEqual(result["prompt"], plain_text)


if __name__ == "__main__":
    unittest.main()
