#!/usr/bin/env python3
"""
Integration tests for multi-prompt rolling window feature.

Demonstrates complete workflow from storage to validation.
"""

import unittest
import tempfile
import os
import json
from unittest.mock import patch


class TestMultiPromptIntegration(unittest.TestCase):
    """Integration test for multi-prompt feature end-to-end."""

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

    def create_transcript(self):
        """Create a mock transcript."""
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
                        "content": [
                            {
                                "type": "text",
                                "text": "Creating calculator with basic operations",
                            }
                        ],
                    }
                },
                {
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "add fractions support"}],
                    }
                },
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "Adding fractions support"}
                        ],
                    }
                },
                {
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "add complex numbers"}],
                    }
                },
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "Implementing complex numbers"}
                        ],
                    }
                },
            ]
            for msg in messages:
                f.write(json.dumps(msg) + "\n")

    def test_complete_multi_prompt_workflow(self):
        """
        Test complete workflow:
        1. Store multiple prompts
        2. Read and validate
        3. Verify SDK receives all prompts
        """
        from src.pacemaker.prompt_storage import store_user_prompt
        from src.pacemaker.intent_validator import validate_intent

        session_id = "test-workflow-001"

        # Step 1: User submits initial request
        store_user_prompt(session_id, "build calculator", self.prompts_dir)

        # Verify first prompt stored
        prompt_file = os.path.join(self.prompts_dir, f"{session_id}.json")
        with open(prompt_file) as f:
            data = json.load(f)
        self.assertEqual(len(data["prompts"]), 1)
        self.assertEqual(data["prompts"][0]["raw_prompt"], "build calculator")

        # Step 2: User steers the work with additional prompts
        store_user_prompt(session_id, "add fractions support", self.prompts_dir)
        store_user_prompt(session_id, "add complex numbers", self.prompts_dir)

        # Verify all 3 prompts stored
        with open(prompt_file) as f:
            data = json.load(f)
        self.assertEqual(len(data["prompts"]), 3)
        self.assertEqual(data["prompts"][0]["raw_prompt"], "build calculator")
        self.assertEqual(data["prompts"][1]["raw_prompt"], "add fractions support")
        self.assertEqual(data["prompts"][2]["raw_prompt"], "add complex numbers")

        # Step 3: Validation calls SDK with all prompts
        self.create_transcript()

        with patch("src.pacemaker.intent_validator.call_sdk_validation") as mock_sdk:
            mock_sdk.return_value = "APPROVED"

            result = validate_intent(session_id, self.transcript_path, self.prompts_dir)

            # Verify SDK was called
            self.assertTrue(mock_sdk.called)

            # Get call arguments
            call_args = mock_sdk.call_args[0]
            user_prompts = call_args[0]

            # Verify all 3 prompts were passed to SDK
            self.assertEqual(len(user_prompts), 3)
            self.assertEqual(user_prompts[0]["expanded_prompt"], "build calculator")
            self.assertEqual(
                user_prompts[1]["expanded_prompt"], "add fractions support"
            )
            self.assertEqual(user_prompts[2]["expanded_prompt"], "add complex numbers")

            # Verify result is APPROVED
            self.assertEqual(result, {"continue": True})

    def test_rolling_window_in_workflow(self):
        """Test that rolling window works correctly in complete workflow."""
        from src.pacemaker.prompt_storage import store_user_prompt

        session_id = "test-rolling-001"

        # Store 7 prompts
        for i in range(1, 8):
            store_user_prompt(session_id, f"request {i}", self.prompts_dir)

        # Verify only last 5 kept (default)
        prompt_file = os.path.join(self.prompts_dir, f"{session_id}.json")
        with open(prompt_file) as f:
            data = json.load(f)

        self.assertEqual(len(data["prompts"]), 5)
        self.assertEqual(data["prompts"][0]["raw_prompt"], "request 3")
        self.assertEqual(data["prompts"][4]["raw_prompt"], "request 7")

    def test_backwards_compatibility_in_workflow(self):
        """Test that old format files work seamlessly in workflow."""
        from src.pacemaker.prompt_storage import store_user_prompt
        from src.pacemaker.intent_validator import read_stored_prompt

        session_id = "test-compat-001"

        # Create old format file
        prompt_file = os.path.join(self.prompts_dir, f"{session_id}.json")
        old_data = {
            "session_id": session_id,
            "raw_prompt": "old request",
            "expanded_prompt": "old request",
            "timestamp": "2025-11-23T10:00:00",
        }
        with open(prompt_file, "w") as f:
            json.dump(old_data, f)

        # Read with new code - should auto-convert
        prompt_data = read_stored_prompt(session_id, self.prompts_dir)
        self.assertEqual(len(prompt_data["prompts"]), 1)
        self.assertEqual(prompt_data["prompts"][0]["raw_prompt"], "old request")

        # Store new prompt - should append to converted format
        store_user_prompt(session_id, "new request", self.prompts_dir)

        # Verify both prompts exist
        with open(prompt_file) as f:
            data = json.load(f)
        self.assertEqual(len(data["prompts"]), 2)
        self.assertEqual(data["prompts"][0]["raw_prompt"], "old request")
        self.assertEqual(data["prompts"][1]["raw_prompt"], "new request")


if __name__ == "__main__":
    unittest.main()
