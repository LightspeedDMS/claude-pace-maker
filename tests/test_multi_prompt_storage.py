#!/usr/bin/env python3
"""
Unit tests for multi-prompt rolling window storage (Enhancement to Story #9).

Tests rolling window behavior:
- Multiple prompts storage
- Rolling window (keeps last 5)
- Backwards compatibility (old format auto-converts)
- Prompt list formatting for SDK
- Validation with multiple prompts
"""

import unittest
import tempfile
import os
import json
from unittest.mock import patch


class TestMultiPromptRollingWindow(unittest.TestCase):
    """Test rolling window behavior for prompt storage."""

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

    def test_first_prompt_creates_single_entry(self):
        """First prompt should create prompts array with one entry."""
        from src.pacemaker.prompt_storage import store_user_prompt

        session_id = "test-session-001"
        prompt1 = "build calculator"

        result = store_user_prompt(
            session_id=session_id, raw_prompt=prompt1, prompts_dir=self.prompts_dir
        )

        self.assertTrue(result)

        # Verify JSON structure
        prompt_file = os.path.join(self.prompts_dir, f"{session_id}.json")
        with open(prompt_file) as f:
            data = json.load(f)

        self.assertEqual(data["session_id"], session_id)
        self.assertIn("prompts", data)
        self.assertEqual(len(data["prompts"]), 1)

        # Check first prompt entry
        prompt_entry = data["prompts"][0]
        self.assertEqual(prompt_entry["raw_prompt"], "build calculator")
        self.assertEqual(prompt_entry["expanded_prompt"], "build calculator")
        self.assertEqual(prompt_entry["sequence"], 1)
        self.assertIn("timestamp", prompt_entry)

    def test_multiple_prompts_append_to_list(self):
        """Multiple prompts should append to prompts array."""
        from src.pacemaker.prompt_storage import store_user_prompt

        session_id = "test-session-002"

        # Store 3 prompts
        store_user_prompt(session_id, "build calculator", self.prompts_dir)
        store_user_prompt(session_id, "add fractions support", self.prompts_dir)
        store_user_prompt(session_id, "add scientific functions", self.prompts_dir)

        # Verify all 3 prompts stored
        prompt_file = os.path.join(self.prompts_dir, f"{session_id}.json")
        with open(prompt_file) as f:
            data = json.load(f)

        self.assertEqual(len(data["prompts"]), 3)
        self.assertEqual(data["prompts"][0]["raw_prompt"], "build calculator")
        self.assertEqual(data["prompts"][1]["raw_prompt"], "add fractions support")
        self.assertEqual(data["prompts"][2]["raw_prompt"], "add scientific functions")
        self.assertEqual(data["prompts"][0]["sequence"], 1)
        self.assertEqual(data["prompts"][1]["sequence"], 2)
        self.assertEqual(data["prompts"][2]["sequence"], 3)

    def test_rolling_window_keeps_last_5_prompts(self):
        """Rolling window should keep only last 5 prompts (default)."""
        from src.pacemaker.prompt_storage import store_user_prompt

        session_id = "test-session-003"

        # Store 7 prompts
        prompts = [
            "prompt 1",
            "prompt 2",
            "prompt 3",
            "prompt 4",
            "prompt 5",
            "prompt 6",
            "prompt 7",
        ]

        for prompt in prompts:
            store_user_prompt(session_id, prompt, self.prompts_dir)

        # Verify only last 5 kept
        prompt_file = os.path.join(self.prompts_dir, f"{session_id}.json")
        with open(prompt_file) as f:
            data = json.load(f)

        self.assertEqual(len(data["prompts"]), 5)
        # Should keep prompts 3-7
        self.assertEqual(data["prompts"][0]["raw_prompt"], "prompt 3")
        self.assertEqual(data["prompts"][1]["raw_prompt"], "prompt 4")
        self.assertEqual(data["prompts"][2]["raw_prompt"], "prompt 5")
        self.assertEqual(data["prompts"][3]["raw_prompt"], "prompt 6")
        self.assertEqual(data["prompts"][4]["raw_prompt"], "prompt 7")

    def test_rolling_window_custom_max_prompts(self):
        """Should support custom max_prompts parameter."""
        from src.pacemaker.prompt_storage import store_user_prompt

        session_id = "test-session-004"

        # Store 5 prompts with max_prompts=3
        prompts = ["prompt 1", "prompt 2", "prompt 3", "prompt 4", "prompt 5"]

        for prompt in prompts:
            store_user_prompt(session_id, prompt, self.prompts_dir, max_prompts=3)

        # Verify only last 3 kept
        prompt_file = os.path.join(self.prompts_dir, f"{session_id}.json")
        with open(prompt_file) as f:
            data = json.load(f)

        self.assertEqual(len(data["prompts"]), 3)
        self.assertEqual(data["prompts"][0]["raw_prompt"], "prompt 3")
        self.assertEqual(data["prompts"][1]["raw_prompt"], "prompt 4")
        self.assertEqual(data["prompts"][2]["raw_prompt"], "prompt 5")

    def test_timestamps_are_unique_for_each_prompt(self):
        """Each prompt should have its own timestamp."""
        from src.pacemaker.prompt_storage import store_user_prompt
        import time

        session_id = "test-session-005"

        # Store 3 prompts with small delays
        store_user_prompt(session_id, "prompt 1", self.prompts_dir)
        time.sleep(0.01)
        store_user_prompt(session_id, "prompt 2", self.prompts_dir)
        time.sleep(0.01)
        store_user_prompt(session_id, "prompt 3", self.prompts_dir)

        # Verify timestamps are different
        prompt_file = os.path.join(self.prompts_dir, f"{session_id}.json")
        with open(prompt_file) as f:
            data = json.load(f)

        timestamps = [p["timestamp"] for p in data["prompts"]]
        self.assertEqual(len(set(timestamps)), 3)  # All unique

    def test_slash_command_expansion_in_multi_prompt(self):
        """Slash commands should expand correctly in multi-prompt storage."""
        from src.pacemaker.prompt_storage import store_user_prompt

        session_id = "test-session-006"

        # Create global command file
        global_commands_dir = os.path.join(self.temp_dir, "commands")
        os.makedirs(global_commands_dir, exist_ok=True)
        command_file = os.path.join(global_commands_dir, "test-cmd.md")
        with open(command_file, "w") as f:
            f.write("EXPANDED COMMAND CONTENT")

        # Store plain text + slash command
        store_user_prompt(session_id, "plain text prompt", self.prompts_dir)
        store_user_prompt(
            session_id,
            "/test-cmd arg",
            self.prompts_dir,
            global_commands_dir=global_commands_dir,
        )

        # Verify both stored correctly
        prompt_file = os.path.join(self.prompts_dir, f"{session_id}.json")
        with open(prompt_file) as f:
            data = json.load(f)

        self.assertEqual(len(data["prompts"]), 2)
        self.assertEqual(data["prompts"][0]["expanded_prompt"], "plain text prompt")
        self.assertEqual(
            data["prompts"][1]["expanded_prompt"], "EXPANDED COMMAND CONTENT"
        )


class TestBackwardsCompatibility(unittest.TestCase):
    """Test backwards compatibility with old single-prompt format."""

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

    def create_old_format_file(self, session_id):
        """Create a prompt file in old format (single prompt)."""
        prompt_file = os.path.join(self.prompts_dir, f"{session_id}.json")
        data = {
            "session_id": session_id,
            "raw_prompt": "old format prompt",
            "expanded_prompt": "old format prompt",
            "timestamp": "2025-11-23T12:00:00",
        }
        with open(prompt_file, "w") as f:
            json.dump(data, f, indent=2)

    def test_read_old_format_converts_to_new_format(self):
        """Reading old format should auto-convert to new format."""
        from src.pacemaker.intent_validator import read_stored_prompt

        session_id = "test-old-001"
        self.create_old_format_file(session_id)

        # Read using new code
        data = read_stored_prompt(session_id, self.prompts_dir)

        # Should convert to new format
        self.assertIn("prompts", data)
        self.assertIsInstance(data["prompts"], list)
        self.assertEqual(len(data["prompts"]), 1)

        # Check converted prompt entry
        prompt_entry = data["prompts"][0]
        self.assertEqual(prompt_entry["raw_prompt"], "old format prompt")
        self.assertEqual(prompt_entry["expanded_prompt"], "old format prompt")
        self.assertEqual(prompt_entry["timestamp"], "2025-11-23T12:00:00")
        self.assertEqual(prompt_entry["sequence"], 1)

    def test_store_after_old_format_appends_correctly(self):
        """Storing after old format should append to converted list."""
        from src.pacemaker.prompt_storage import store_user_prompt

        session_id = "test-old-002"
        self.create_old_format_file(session_id)

        # Store new prompt
        store_user_prompt(session_id, "new prompt", self.prompts_dir)

        # Verify both prompts exist
        prompt_file = os.path.join(self.prompts_dir, f"{session_id}.json")
        with open(prompt_file) as f:
            data = json.load(f)

        self.assertEqual(len(data["prompts"]), 2)
        self.assertEqual(data["prompts"][0]["raw_prompt"], "old format prompt")
        self.assertEqual(data["prompts"][1]["raw_prompt"], "new prompt")


class TestMultiPromptValidation(unittest.TestCase):
    """Test intent validation with multiple prompts."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.prompts_dir = os.path.join(self.temp_dir, "prompts")
        self.transcript_path = os.path.join(self.temp_dir, "transcript.jsonl")
        os.makedirs(self.prompts_dir, exist_ok=True)

    def tearDown(self):
        """Clean up."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def create_multi_prompt_file(self, session_id, prompts_data):
        """Create a prompt file with multiple prompts."""
        prompt_file = os.path.join(self.prompts_dir, f"{session_id}.json")
        data = {
            "session_id": session_id,
            "prompts": [
                {
                    "raw_prompt": p["raw"],
                    "expanded_prompt": p["expanded"],
                    "timestamp": p["timestamp"],
                    "sequence": i + 1,
                }
                for i, p in enumerate(prompts_data)
            ],
        }
        with open(prompt_file, "w") as f:
            json.dump(data, f, indent=2)

    def create_transcript(self):
        """Create a basic transcript."""
        with open(self.transcript_path, "w") as f:
            messages = [
                {
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "build calculator"}],
                    }
                },
                {
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "I will build it"}],
                    }
                },
                {
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "add fractions"}],
                    }
                },
                {
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Adding fractions"}],
                    }
                },
            ]
            for msg in messages:
                f.write(json.dumps(msg) + "\n")

    def test_build_validation_prompt_with_multiple_prompts(self):
        """Should format multiple prompts chronologically for SDK."""
        from src.pacemaker.intent_validator import build_validation_prompt

        user_prompts = [
            {
                "raw_prompt": "build calculator",
                "expanded_prompt": "build calculator",
                "timestamp": "2025-11-23T12:00:00",
                "sequence": 1,
            },
            {
                "raw_prompt": "add fractions",
                "expanded_prompt": "add fractions support",
                "timestamp": "2025-11-23T12:01:30",
                "sequence": 2,
            },
            {
                "raw_prompt": "add complex numbers",
                "expanded_prompt": "add complex numbers support",
                "timestamp": "2025-11-23T12:03:00",
                "sequence": 3,
            },
        ]

        messages = ["[USER]\nbuild calculator", "[ASSISTANT]\nImplementing..."]

        prompt = build_validation_prompt(user_prompts, messages)

        # Should contain all prompts chronologically
        self.assertIn("Prompt 1 (2025-11-23T12:00:00)", prompt)
        self.assertIn("build calculator", prompt)
        self.assertIn("Prompt 2 (2025-11-23T12:01:30)", prompt)
        self.assertIn("add fractions support", prompt)
        self.assertIn("Prompt 3 (2025-11-23T12:03:00)", prompt)
        self.assertIn("add complex numbers support", prompt)

        # Should mention evolution of request
        self.assertIn("last 5 user prompts", prompt)
        self.assertIn("evolution of your request", prompt)
        self.assertIn("COMPLETE intent", prompt)

    def test_build_validation_prompt_with_single_prompt(self):
        """Should work with single prompt (backwards compatible)."""
        from src.pacemaker.intent_validator import build_validation_prompt

        user_prompts = [
            {
                "raw_prompt": "build calculator",
                "expanded_prompt": "build calculator",
                "timestamp": "2025-11-23T12:00:00",
                "sequence": 1,
            }
        ]

        messages = ["[USER]\nbuild calculator"]

        prompt = build_validation_prompt(user_prompts, messages)

        # Should contain single prompt
        self.assertIn("Prompt 1", prompt)
        self.assertIn("build calculator", prompt)

    @patch("src.pacemaker.intent_validator.call_sdk_validation")
    def test_validate_intent_passes_all_prompts_to_sdk(self, mock_sdk):
        """Should pass all stored prompts to SDK for validation."""
        from src.pacemaker.intent_validator import validate_intent

        session_id = "test-multi-001"

        # Create multi-prompt file
        prompts_data = [
            {
                "raw": "build calculator",
                "expanded": "build calculator",
                "timestamp": "2025-11-23T12:00:00",
            },
            {
                "raw": "add fractions",
                "expanded": "add fractions support",
                "timestamp": "2025-11-23T12:01:30",
            },
        ]
        self.create_multi_prompt_file(session_id, prompts_data)
        self.create_transcript()

        mock_sdk.return_value = "APPROVED"

        result = validate_intent(session_id, self.transcript_path, self.prompts_dir)

        # Verify SDK was called
        self.assertTrue(mock_sdk.called)

        # Get call arguments
        call_args = mock_sdk.call_args[0]

        # First arg should be list of prompts
        user_prompts = call_args[0]
        self.assertIsInstance(user_prompts, list)
        self.assertEqual(len(user_prompts), 2)

        # Second arg should be conversation messages
        messages = call_args[1]
        self.assertIsInstance(messages, list)

        # Should return parsed result
        self.assertEqual(result, {"continue": True})

    @patch("src.pacemaker.intent_validator.call_sdk_validation_async")
    def test_call_sdk_validation_formats_prompts_correctly(self, mock_sdk_async):
        """SDK call should format all prompts for validation."""
        from src.pacemaker.intent_validator import call_sdk_validation

        user_prompts = [
            {
                "raw_prompt": "build calculator",
                "expanded_prompt": "build calculator",
                "timestamp": "2025-11-23T12:00:00",
                "sequence": 1,
            },
            {
                "raw_prompt": "add fractions",
                "expanded_prompt": "add fractions support",
                "timestamp": "2025-11-23T12:01:30",
                "sequence": 2,
            },
        ]

        messages = ["[USER]\nbuild calculator"]

        # Mock async function to return immediately
        import asyncio

        async def mock_return():
            return "APPROVED"

        mock_sdk_async.return_value = asyncio.Future()
        mock_sdk_async.return_value.set_result("APPROVED")

        call_sdk_validation(user_prompts, messages)

        # Verify SDK was called with formatted prompt
        self.assertTrue(mock_sdk_async.called)


class TestValidationPromptTemplate(unittest.TestCase):
    """Test updated validation prompt template."""

    def test_template_mentions_multiple_prompts(self):
        """Template should mention handling multiple prompts."""
        from src.pacemaker.intent_validator import VALIDATION_PROMPT_TEMPLATE

        # Should mention multiple prompts
        self.assertIn("last 5 user prompts", VALIDATION_PROMPT_TEMPLATE)
        self.assertIn("evolution of your request", VALIDATION_PROMPT_TEMPLATE)

        # Should explain prompt structure
        self.assertIn("first prompt is your core request", VALIDATION_PROMPT_TEMPLATE)
        self.assertIn(
            "Subsequent prompts", VALIDATION_PROMPT_TEMPLATE
        )  # steering/refinements

        # Should emphasize ALL prompts
        self.assertIn("ALL your prompts", VALIDATION_PROMPT_TEMPLATE)
        self.assertIn("COMPLETE intent", VALIDATION_PROMPT_TEMPLATE)

    def test_template_maintains_response_format(self):
        """Template should maintain APPROVED/BLOCKED response format."""
        from src.pacemaker.intent_validator import VALIDATION_PROMPT_TEMPLATE

        self.assertIn("APPROVED", VALIDATION_PROMPT_TEMPLATE)
        self.assertIn("BLOCKED:", VALIDATION_PROMPT_TEMPLATE)
        self.assertIn("RESPONSE FORMAT", VALIDATION_PROMPT_TEMPLATE)


class TestEdgeCases(unittest.TestCase):
    """Test edge cases for multi-prompt storage."""

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

    def test_empty_prompts_list_fails_open(self):
        """Empty prompts list should fail open."""
        from src.pacemaker.intent_validator import validate_intent

        session_id = "test-empty-001"

        # Create file with empty prompts list
        prompt_file = os.path.join(self.prompts_dir, f"{session_id}.json")
        data = {"session_id": session_id, "prompts": []}
        with open(prompt_file, "w") as f:
            json.dump(data, f)

        # Create empty transcript
        transcript_path = os.path.join(self.temp_dir, "transcript.jsonl")
        with open(transcript_path, "w") as f:
            pass

        result = validate_intent(session_id, transcript_path, self.prompts_dir)

        # Should fail open
        self.assertEqual(result, {"continue": True})

    def test_missing_fields_in_prompt_entry(self):
        """Should handle missing fields gracefully."""
        from src.pacemaker.intent_validator import build_validation_prompt

        user_prompts = [
            {
                "raw_prompt": "test",
                "expanded_prompt": "test prompt",
                # Missing timestamp and sequence
            }
        ]

        messages = ["[USER]\ntest"]

        # Should not crash
        prompt = build_validation_prompt(user_prompts, messages)
        self.assertIn("test prompt", prompt)

    def test_max_prompts_zero_keeps_all(self):
        """max_prompts=0 should keep all prompts (unlimited)."""
        from src.pacemaker.prompt_storage import store_user_prompt

        session_id = "test-unlimited-001"

        # Store 10 prompts with max_prompts=0
        for i in range(10):
            store_user_prompt(
                session_id, f"prompt {i+1}", self.prompts_dir, max_prompts=0
            )

        # Verify all 10 kept
        prompt_file = os.path.join(self.prompts_dir, f"{session_id}.json")
        with open(prompt_file) as f:
            data = json.load(f)

        self.assertEqual(len(data["prompts"]), 10)


if __name__ == "__main__":
    unittest.main()
