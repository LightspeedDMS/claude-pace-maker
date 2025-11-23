#!/usr/bin/env python3
"""
Tests for COMPLETELY_BLOCKED escape hatch feature.

Tests both the blockage legitimacy validation and the stop hook integration
for the COMPLETELY_BLOCKED escape hatch that allows Claude to exit validation
loops when genuinely blocked.
"""

import unittest
import tempfile
import os
import json
from unittest.mock import patch
import io


class TestBlockageLegitimacyValidation(unittest.TestCase):
    """Test blockage legitimacy validation functions."""

    def test_validate_blockage_legitimacy_legitimate_missing_info(self):
        """Should accept legitimate blockage due to missing critical information."""
        from src.pacemaker.completion_validator import validate_blockage_legitimacy

        messages = [
            "[USER]\nImplement user authentication with OAuth",
            "[ASSISTANT]\nI need to implement OAuth authentication. IMPLEMENTATION_COMPLETE",
            "[SYSTEM]\nCHALLENGE: You mentioned OAuth but didn't implement any OAuth flow...",
            "[ASSISTANT]\nCOMPLETELY_BLOCKED: I need the OAuth provider details (Google/GitHub/etc), client ID, client secret, and redirect URLs. These cannot be inferred and must come from the user.",
        ]

        result = validate_blockage_legitimacy(messages)

        self.assertTrue(result.get("legitimate"))
        self.assertIsNone(result.get("challenge_message"))

    def test_validate_blockage_legitimacy_legitimate_ambiguous_requirements(self):
        """Should accept legitimate blockage due to ambiguous requirements."""
        from src.pacemaker.completion_validator import validate_blockage_legitimacy

        messages = [
            "[USER]\nFix the performance issue",
            "[ASSISTANT]\nOptimized the database queries. IMPLEMENTATION_COMPLETE",
            "[SYSTEM]\nCHALLENGE: You didn't identify what the performance issue was...",
            "[ASSISTANT]\nCOMPLETELY_BLOCKED: The user said 'fix the performance issue' but there are multiple potential issues: slow database queries, inefficient rendering, memory leaks, and network latency. I need clarification on which specific performance problem to address.",
        ]

        result = validate_blockage_legitimacy(messages)

        self.assertTrue(result.get("legitimate"))
        self.assertIsNone(result.get("challenge_message"))

    def test_validate_blockage_legitimacy_legitimate_user_asked_to_stop(self):
        """Should accept legitimate blockage when user explicitly asked to stop."""
        from src.pacemaker.completion_validator import validate_blockage_legitimacy

        messages = [
            "[USER]\nStart implementing the payment system",
            "[ASSISTANT]\nImplementing payment gateway integration...",
            "[USER]\nActually, wait. Don't implement this yet.",
            "[ASSISTANT]\nUnderstood, stopping work. IMPLEMENTATION_COMPLETE",
            "[SYSTEM]\nCHALLENGE: You didn't complete the payment system...",
            "[ASSISTANT]\nCOMPLETELY_BLOCKED: The user explicitly told me to stop and not implement the payment system yet.",
        ]

        result = validate_blockage_legitimacy(messages)

        self.assertTrue(result.get("legitimate"))
        self.assertIsNone(result.get("challenge_message"))

    def test_validate_blockage_legitimacy_legitimate_contradictory_requirements(self):
        """Should accept legitimate blockage due to contradictory requirements."""
        from src.pacemaker.completion_validator import validate_blockage_legitimacy

        messages = [
            "[USER]\nMake it work without external dependencies and use the requests library",
            "[ASSISTANT]\nImplemented HTTP client. IMPLEMENTATION_COMPLETE",
            "[SYSTEM]\nCHALLENGE: You used urllib but user asked for requests library...",
            "[ASSISTANT]\nCOMPLETELY_BLOCKED: The requirements are contradictory - the user wants no external dependencies but also wants to use 'requests' which IS an external dependency. I need clarification.",
        ]

        result = validate_blockage_legitimacy(messages)

        self.assertTrue(result.get("legitimate"))
        self.assertIsNone(result.get("challenge_message"))

    def test_validate_blockage_legitimacy_rejected_token_budget_excuse(self):
        """Should reject blockage claim due to token budget concerns."""
        from src.pacemaker import completion_validator

        messages = [
            "[USER]\nImplement the shopping cart feature",
            "[ASSISTANT]\nCreated basic structure. IMPLEMENTATION_COMPLETE",
            "[SYSTEM]\nCHALLENGE: You only created stubs, no actual implementation...",
            "[ASSISTANT]\nCOMPLETELY_BLOCKED: I'm concerned about running out of tokens and need to conserve context.",
        ]

        # Mock the SDK response to reject this excuse
        mock_validation = {
            "legitimate": False,
            "challenge_message": "BLOCKAGE_REJECTED: Token budget concerns are not valid blockers. Continue implementing the feature.",
        }

        with patch.object(
            completion_validator,
            "validate_blockage_legitimacy_async",
            return_value=mock_validation,
        ):
            result = completion_validator.validate_blockage_legitimacy(messages)

        self.assertFalse(result.get("legitimate"))
        self.assertIsNotNone(result.get("challenge_message"))
        self.assertIn("token budget", result["challenge_message"].lower())

    def test_validate_blockage_legitimacy_rejected_too_complex_excuse(self):
        """Should reject blockage claim of 'too complex'."""
        from src.pacemaker import completion_validator

        messages = [
            "[USER]\nImplement binary search tree",
            "[ASSISTANT]\nStarted implementation. IMPLEMENTATION_COMPLETE",
            "[SYSTEM]\nCHALLENGE: BST has no insert, delete, or search methods...",
            "[ASSISTANT]\nCOMPLETELY_BLOCKED: This data structure is too complex for me to implement fully.",
        ]

        # Mock the SDK response to reject this excuse
        mock_validation = {
            "legitimate": False,
            "challenge_message": "BLOCKAGE_REJECTED: Complexity is not a valid blocker. Research and implement the BST methods step by step.",
        }

        with patch.object(
            completion_validator,
            "validate_blockage_legitimacy_async",
            return_value=mock_validation,
        ):
            result = completion_validator.validate_blockage_legitimacy(messages)

        self.assertFalse(result.get("legitimate"))
        self.assertIsNotNone(result.get("challenge_message"))
        self.assertIn("complex", result["challenge_message"].lower())

    def test_validate_blockage_legitimacy_rejected_user_should_do_it(self):
        """Should reject blockage claim of 'user should do this'."""
        from src.pacemaker import completion_validator

        messages = [
            "[USER]\nAdd error handling to the API",
            "[ASSISTANT]\nAdded basic try-catch. IMPLEMENTATION_COMPLETE",
            "[SYSTEM]\nCHALLENGE: No specific error handling for different error types...",
            "[ASSISTANT]\nCOMPLETELY_BLOCKED: Error handling strategy should be decided by the user, not me.",
        ]

        # Mock the SDK response to reject this excuse
        mock_validation = {
            "legitimate": False,
            "challenge_message": "BLOCKAGE_REJECTED: Implement comprehensive error handling. The user requested you do this work.",
        }

        with patch.object(
            completion_validator,
            "validate_blockage_legitimacy_async",
            return_value=mock_validation,
        ):
            result = completion_validator.validate_blockage_legitimacy(messages)

        self.assertFalse(result.get("legitimate"))
        self.assertIsNotNone(result.get("challenge_message"))

    def test_validate_blockage_legitimacy_rejected_generic_excuse(self):
        """Should reject blockage claim with generic non-specific excuse."""
        from src.pacemaker import completion_validator

        messages = [
            "[USER]\nComplete the feature",
            "[ASSISTANT]\nWorking on it. IMPLEMENTATION_COMPLETE",
            "[SYSTEM]\nCHALLENGE: Feature is incomplete...",
            "[ASSISTANT]\nCOMPLETELY_BLOCKED: I can't continue with this.",
        ]

        # Mock the SDK response to reject this generic excuse
        mock_validation = {
            "legitimate": False,
            "challenge_message": "BLOCKAGE_REJECTED: Generic excuse without specific blocker. Identify what you need and continue working.",
        }

        with patch.object(
            completion_validator,
            "validate_blockage_legitimacy_async",
            return_value=mock_validation,
        ):
            result = completion_validator.validate_blockage_legitimacy(messages)

        self.assertFalse(result.get("legitimate"))
        self.assertIsNotNone(result.get("challenge_message"))

    def test_validate_blockage_legitimacy_sdk_unavailable_allows_exit(self):
        """Should allow exit gracefully when SDK is not available."""
        from src.pacemaker import completion_validator

        messages = ["[USER]\nTest", "[ASSISTANT]\nCOMPLETELY_BLOCKED: Blocked"]

        # Temporarily disable SDK
        original_sdk = completion_validator.SDK_AVAILABLE
        try:
            completion_validator.SDK_AVAILABLE = False
            result = completion_validator.validate_blockage_legitimacy(messages)
            self.assertTrue(result.get("legitimate"))
            self.assertIsNone(result.get("challenge_message"))
        finally:
            completion_validator.SDK_AVAILABLE = original_sdk


class TestStopHookBlockageEscapeHatch(unittest.TestCase):
    """Test Stop hook integration with COMPLETELY_BLOCKED escape hatch."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.json")
        self.state_path = os.path.join(self.temp_dir, "state.json")
        self.transcript_path = os.path.join(self.temp_dir, "transcript.jsonl")

    def tearDown(self):
        """Clean up."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def create_transcript_with_blockage(
        self, marker_type="IMPLEMENTATION_COMPLETE", blockage_reason=None
    ):
        """Create transcript with challenge and COMPLETELY_BLOCKED response."""
        messages = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Implement OAuth authentication"}],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": f"Created basic structure. {marker_type}",
                    }
                ],
            },
        ]

        if blockage_reason:
            # Add challenge from system (simulated as assistant seeing the challenge)
            messages.append(
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": f"COMPLETELY_BLOCKED: {blockage_reason}",
                        }
                    ],
                }
            )

        # Write transcript
        with open(self.transcript_path, "w") as f:
            for msg in messages:
                entry = {"message": msg, "type": msg["role"]}
                f.write(json.dumps(entry) + "\n")

    def test_stop_hook_implementation_complete_then_legitimate_blockage_allows_exit(
        self,
    ):
        """Should allow exit when COMPLETELY_BLOCKED with legitimate reason after IMPLEMENTATION_COMPLETE challenge."""
        from src.pacemaker.hook import run_stop_hook
        from src.pacemaker import completion_validator

        # Create transcript: IMPLEMENTATION_COMPLETE -> challenged -> COMPLETELY_BLOCKED
        self.create_transcript_with_blockage(
            marker_type="IMPLEMENTATION_COMPLETE",
            blockage_reason="I need the OAuth provider (Google/GitHub), client credentials, and redirect URL which cannot be inferred.",
        )

        # Create enabled config
        config = {"tempo_enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Mock the validation to return legitimate blockage
        mock_validation = {
            "legitimate": True,
            "challenge_message": None,
        }

        hook_data = {"transcript_path": self.transcript_path}
        mock_stdin = io.StringIO(json.dumps(hook_data))

        with patch("src.pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("src.pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                with patch("sys.stdin", mock_stdin):
                    with patch.object(
                        completion_validator,
                        "validate_blockage_legitimacy",
                        return_value=mock_validation,
                    ):
                        result = run_stop_hook()

        # Should allow exit (legitimate blockage)
        self.assertTrue(result.get("continue"))

    def test_stop_hook_implementation_complete_then_rejected_blockage_blocks_exit(self):
        """Should block exit when COMPLETELY_BLOCKED with invalid excuse after IMPLEMENTATION_COMPLETE challenge."""
        from src.pacemaker.hook import run_stop_hook
        from src.pacemaker import completion_validator

        # Create transcript: IMPLEMENTATION_COMPLETE -> challenged -> COMPLETELY_BLOCKED with bad excuse
        self.create_transcript_with_blockage(
            marker_type="IMPLEMENTATION_COMPLETE",
            blockage_reason="This is too complex and taking too long.",
        )

        # Create enabled config
        config = {"tempo_enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Mock the validation to reject blockage
        mock_validation = {
            "legitimate": False,
            "challenge_message": "BLOCKAGE_REJECTED: Complexity is not a valid blocker. You should research and implement the OAuth flow step by step.",
        }

        hook_data = {"transcript_path": self.transcript_path}
        mock_stdin = io.StringIO(json.dumps(hook_data))

        with patch("src.pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("src.pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                with patch("sys.stdin", mock_stdin):
                    with patch.object(
                        completion_validator,
                        "validate_blockage_legitimacy",
                        return_value=mock_validation,
                    ):
                        result = run_stop_hook()

        # Should block exit with challenge message
        self.assertEqual(result.get("decision"), "block")
        self.assertIn("BLOCKAGE_REJECTED", result.get("reason", ""))

    def test_stop_hook_exchange_complete_then_legitimate_blockage_allows_exit(self):
        """Should allow exit when COMPLETELY_BLOCKED with legitimate reason after EXCHANGE_COMPLETE challenge."""
        from src.pacemaker.hook import run_stop_hook
        from src.pacemaker import completion_validator

        # Create transcript: EXCHANGE_COMPLETE -> challenged -> COMPLETELY_BLOCKED
        self.create_transcript_with_blockage(
            marker_type="EXCHANGE_COMPLETE",
            blockage_reason="The user asked me to wait before implementing, so I cannot proceed with code changes.",
        )

        # Create enabled config
        config = {"tempo_enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Mock the validation to return legitimate blockage
        mock_validation = {
            "legitimate": True,
            "challenge_message": None,
        }

        hook_data = {"transcript_path": self.transcript_path}
        mock_stdin = io.StringIO(json.dumps(hook_data))

        with patch("src.pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("src.pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                with patch("sys.stdin", mock_stdin):
                    with patch.object(
                        completion_validator,
                        "validate_blockage_legitimacy",
                        return_value=mock_validation,
                    ):
                        result = run_stop_hook()

        # Should allow exit (legitimate blockage)
        self.assertTrue(result.get("continue"))

    def test_stop_hook_exchange_complete_then_rejected_blockage_blocks_exit(self):
        """Should block exit when COMPLETELY_BLOCKED with invalid excuse after EXCHANGE_COMPLETE challenge."""
        from src.pacemaker.hook import run_stop_hook
        from src.pacemaker import completion_validator

        # Create transcript: EXCHANGE_COMPLETE -> challenged -> COMPLETELY_BLOCKED with bad excuse
        self.create_transcript_with_blockage(
            marker_type="EXCHANGE_COMPLETE",
            blockage_reason="I don't want to implement this feature.",
        )

        # Create enabled config
        config = {"tempo_enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        # Mock the validation to reject blockage
        mock_validation = {
            "legitimate": False,
            "challenge_message": "BLOCKAGE_REJECTED: The user requested implementation. Proceed with implementing the feature.",
        }

        hook_data = {"transcript_path": self.transcript_path}
        mock_stdin = io.StringIO(json.dumps(hook_data))

        with patch("src.pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("src.pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                with patch("sys.stdin", mock_stdin):
                    with patch.object(
                        completion_validator,
                        "validate_blockage_legitimacy",
                        return_value=mock_validation,
                    ):
                        result = run_stop_hook()

        # Should block exit with challenge message
        self.assertEqual(result.get("decision"), "block")
        self.assertIn("BLOCKAGE_REJECTED", result.get("reason", ""))

    def test_stop_hook_completely_blocked_without_context_allows_exit(self):
        """Should allow exit gracefully when COMPLETELY_BLOCKED detected but context unavailable."""
        from src.pacemaker.hook import run_stop_hook

        # Create minimal transcript with just COMPLETELY_BLOCKED
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "COMPLETELY_BLOCKED: Missing critical information",
                    }
                ],
            }
        ]

        with open(self.transcript_path, "w") as f:
            for msg in messages:
                entry = {"message": msg, "type": msg["role"]}
                f.write(json.dumps(entry) + "\n")

        # Create enabled config
        config = {"tempo_enabled": True}
        with open(self.config_path, "w") as f:
            json.dump(config, f)

        hook_data = {"transcript_path": self.transcript_path}
        mock_stdin = io.StringIO(json.dumps(hook_data))

        with patch("src.pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path):
            with patch("src.pacemaker.hook.DEFAULT_STATE_PATH", self.state_path):
                with patch("sys.stdin", mock_stdin):
                    result = run_stop_hook()

        # Should allow exit (graceful degradation when context missing)
        self.assertTrue(result.get("continue"))


if __name__ == "__main__":
    unittest.main()
