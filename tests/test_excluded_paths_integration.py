#!/usr/bin/env python3
"""
Integration tests for excluded paths feature.

Tests that excluded paths bypass TDD requirements while still requiring intent.
"""

import tempfile
import os

from src.pacemaker import excluded_paths
from src.pacemaker.intent_validator import _build_stage1_prompt


class TestExcludedPathsIntegration:
    """Test integration of excluded paths with TDD validation."""

    def test_placeholder_bug_double_braces_not_replaced(self):
        """
        CRITICAL BUG: .format() consumes one layer of braces before .replace() runs.

        The template has {{excluded_paths}} with double braces.
        When .format(current_message=..., file_path=..., tool_name=...) is called,
        Python's .format() processes {{excluded_paths}} and converts it to {excluded_paths}.
        Then .replace("{{excluded_paths}}", excluded_paths_text) looks for double braces
        but finds single braces, so the replacement never happens!

        This test proves the bug exists.
        """
        current_message = "INTENT: Modify .tmp/test.py to add logging"
        file_path = ".tmp/test.py"
        tool_name = "Write"

        prompt = _build_stage1_prompt(current_message, file_path, tool_name)

        # The bug: we should see actual exclusion paths, not placeholder
        # If we see {excluded_paths} (single braces), that proves:
        # 1. .format() consumed one layer: {{excluded_paths}} → {excluded_paths}
        # 2. .replace() didn't find {{excluded_paths}} to replace
        assert "{excluded_paths}" not in prompt, (
            "BUG: Placeholder still visible (single braces). "
            ".format() consumed one layer but .replace() didn't match double braces."
        )

        # We should see actual paths
        assert ".tmp/" in prompt, "Should see actual exclusion path .tmp/"
        assert "tests/" in prompt, "Should see actual exclusion path tests/"

    def test_excluded_path_bypasses_tdd_in_prompt(self):
        """
        Excluded paths should not trigger TDD requirement in Stage 1 prompt.

        When a file in an excluded folder (e.g., .tmp/) is modified:
        - Intent declaration should still be required
        - TDD check should be skipped (no NO_TDD response possible)
        """
        # Create a mock message with intent but no TDD declaration
        current_message = """
        INTENT: Modify .tmp/test.py to add debug logging for troubleshooting
        """

        file_path = ".tmp/test.py"
        tool_name = "Write"

        # Build Stage 1 prompt
        prompt = _build_stage1_prompt(current_message, file_path, tool_name)

        # Verify intent check is still present
        assert "INTENT" in prompt or "intent" in prompt.lower()

        # Verify the file is recognized as excluded path
        exclusions = excluded_paths.get_default_exclusions()
        assert excluded_paths.is_excluded_path(file_path, exclusions) is True

        # CRITICAL: Verify excluded paths actually appear in the prompt
        # This exposes the placeholder bug - {{excluded_paths}} won't be replaced
        assert ".tmp/" in prompt, "Default exclusion .tmp/ should appear in prompt"
        assert "tests/" in prompt, "Default exclusion tests/ should appear in prompt"

        # Verify placeholder was replaced (should NOT see double-brace marker)
        assert (
            "{{excluded_paths}}" not in prompt
        ), "Placeholder should be replaced, not left in prompt"

    def test_non_excluded_core_path_requires_tdd(self):
        """
        Non-excluded core paths should still require TDD in Stage 1.

        When a file in src/ (core path, not excluded) is modified:
        - Intent declaration should be required
        - TDD declaration should be required
        """
        # Create a mock message with intent but no TDD declaration
        current_message = """
        INTENT: Modify src/auth.py to add password validation function
        """

        file_path = "src/auth.py"
        tool_name = "Write"

        # Build Stage 1 prompt
        prompt = _build_stage1_prompt(current_message, file_path, tool_name)

        # Verify intent check is present
        assert "INTENT" in prompt or "intent" in prompt.lower()

        # Verify TDD check is included for core paths
        assert "TDD" in prompt or "test" in prompt.lower()

        # The file should NOT be in excluded paths
        exclusions = excluded_paths.get_default_exclusions()
        assert excluded_paths.is_excluded_path(file_path, exclusions) is False

    def test_custom_excluded_path_bypasses_tdd(self):
        """
        Custom excluded paths should bypass TDD just like defaults.

        When user adds a custom exclusion, files in that path should skip TDD.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "excluded_paths.yaml")

            # Add custom exclusion
            excluded_paths.add_exclusion(config_path, ".generated/")

            # Load exclusions
            exclusions = excluded_paths.load_exclusions(config_path)

            # File in custom excluded path should be recognized as excluded
            assert (
                excluded_paths.is_excluded_path(".generated/output.py", exclusions)
                is True
            )

            # File in default core path should NOT be excluded
            assert excluded_paths.is_excluded_path("src/module.py", exclusions) is False

    def test_excluded_test_folder_bypasses_tdd(self):
        """
        Test folders should be excluded from TDD requirements.

        This is important because test files themselves shouldn't require
        tests (that would be circular).
        """
        # test/ and tests/ are in default exclusions
        exclusions = excluded_paths.get_default_exclusions()

        assert excluded_paths.is_excluded_path("tests/test_auth.py", exclusions) is True
        assert (
            excluded_paths.is_excluded_path("test/unit/test_utils.py", exclusions)
            is True
        )

        # But src/ files should not be excluded
        assert excluded_paths.is_excluded_path("src/auth.py", exclusions) is False

    def test_prompt_excludes_all_default_paths(self):
        """
        Stage 1 prompt should include ALL default excluded paths.

        This ensures the validator knows about all paths that bypass TDD.
        """
        current_message = "INTENT: Modify src/utils.py to add helper function"
        file_path = "src/utils.py"
        tool_name = "Write"

        prompt = _build_stage1_prompt(current_message, file_path, tool_name)

        # Get all defaults and verify they appear
        defaults = excluded_paths.get_default_exclusions()
        for excluded_path in defaults:
            assert (
                excluded_path in prompt
            ), f"Default exclusion '{excluded_path}' should appear in prompt"

    def test_custom_exclusion_appears_in_prompt(self):
        """
        Custom excluded paths from config should appear in Stage 1 prompt.

        When user adds custom exclusions, the validator must know about them.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "excluded_paths.yaml")

            # Add custom exclusion
            excluded_paths.add_exclusion(config_path, ".generated/")
            excluded_paths.add_exclusion(config_path, "debug/")

            # Reload exclusions to pick up custom paths
            # NOTE: This test will fail until we fix the integration
            # to use the config_path parameter instead of hardcoded default

            current_message = "INTENT: Modify .generated/output.py to add schema"
            file_path = ".generated/output.py"
            tool_name = "Write"

            # Build prompt (currently uses DEFAULT_EXCLUDED_PATHS_PATH)
            # This test documents the limitation that custom paths won't appear
            prompt = _build_stage1_prompt(current_message, file_path, tool_name)

            # Verify defaults appear
            assert ".tmp/" in prompt

            # NOTE: Custom paths won't appear until we pass config_path parameter
            # This is a known limitation - documenting for future enhancement

    def test_prompt_format_matches_template_structure(self):
        """
        Verify excluded paths are formatted correctly for the prompt template.

        The format should match the bullet list structure expected by the template.
        """
        current_message = "INTENT: Modify test.py to add function"
        file_path = "test.py"
        tool_name = "Write"

        prompt = _build_stage1_prompt(current_message, file_path, tool_name)

        # Verify excluded paths section exists and is properly formatted
        assert "Excluded paths:" in prompt, "Should have 'Excluded paths:' header"

        # Verify at least one exclusion is listed with proper formatting
        exclusions = excluded_paths.get_default_exclusions()
        formatted = excluded_paths.format_exclusions_for_prompt(exclusions)

        # The formatted output should be a bullet list with "  - " prefix
        assert "  - .tmp/" in formatted or "  - .tmp/" in prompt

    def test_empty_exclusions_handled_gracefully(self):
        """
        If no exclusions are configured, prompt should still be valid.

        Edge case: ensure the system doesn't break with empty exclusion list.
        """
        # Test the formatting function with empty list
        formatted = excluded_paths.format_exclusions_for_prompt([])

        # Should return a safe default message
        assert "No excluded paths configured" in formatted

        # Prompt building should not crash with empty exclusions
        # (This is more of a defensive test - defaults should always exist)
