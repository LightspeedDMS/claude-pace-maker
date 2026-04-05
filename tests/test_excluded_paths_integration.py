#!/usr/bin/env python3
"""
Integration tests for excluded paths feature.

Tests that excluded paths bypass TDD requirements while still requiring intent.
Stage 1 is now regex-based (_regex_stage1_check), so these tests call it directly
to verify excluded path behaviour without needing an LLM.
"""

import tempfile
import os

from pacemaker import excluded_paths
from pacemaker.intent_validator import _regex_stage1_check


class TestExcludedPathsIntegration:
    """Test integration of excluded paths with TDD validation."""

    def test_excluded_path_bypasses_tdd(self):
        """Excluded path with INTENT: declared returns YES (no TDD required).

        A file under .tmp/ is excluded — even without a TDD declaration the
        regex check must return YES, because excluded paths skip the TDD gate.
        """
        current_message = (
            "INTENT: Modify .tmp/test.py to add debug logging for troubleshooting"
        )
        file_path = ".tmp/test.py"
        exclusions = excluded_paths.get_default_exclusions()

        result = _regex_stage1_check(current_message, file_path, exclusions)

        assert result == "YES", (
            f"Expected YES for excluded path but got '{result}'. "
            "Excluded paths must bypass TDD requirement."
        )

    def test_excluded_path_still_requires_intent(self):
        """Excluded path without INTENT: returns NO (intent always required).

        Even though .tmp/ is excluded from TDD, an INTENT: marker is still
        required in the current message.
        """
        current_message = "Writing some debug code."  # No INTENT:
        file_path = ".tmp/test.py"
        exclusions = excluded_paths.get_default_exclusions()

        result = _regex_stage1_check(current_message, file_path, exclusions)

        assert result == "NO", (
            f"Expected NO (missing intent) but got '{result}'. "
            "Even excluded paths require an INTENT: declaration."
        )

    def test_non_excluded_core_path_requires_tdd(self):
        """Non-excluded core path without TDD declaration returns NO_TDD.

        src/ is a core path and not excluded — INTENT: + file mention present
        but no TDD declaration, so the check must return NO_TDD.
        """
        current_message = (
            "INTENT: Modify src/auth.py to add password validation function"
        )
        file_path = "src/auth.py"
        exclusions = excluded_paths.get_default_exclusions()

        result = _regex_stage1_check(current_message, file_path, exclusions)

        assert (
            result == "NO_TDD"
        ), f"Expected NO_TDD for core path without TDD but got '{result}'."

    def test_non_excluded_core_path_with_tdd_returns_yes(self):
        """Non-excluded core path WITH TDD declaration returns YES."""
        current_message = (
            "INTENT: Modify src/auth.py to add password validation function. "
            "Test coverage: tests/test_auth.py - test_password_validation()"
        )
        file_path = "src/auth.py"
        exclusions = excluded_paths.get_default_exclusions()

        result = _regex_stage1_check(current_message, file_path, exclusions)

        assert (
            result == "YES"
        ), f"Expected YES for core path with TDD declaration but got '{result}'."

    def test_custom_excluded_path_bypasses_tdd(self):
        """Custom excluded paths bypass TDD just like defaults.

        When user adds a custom exclusion, files in that path must skip TDD.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "excluded_paths.yaml")

            # Add custom exclusion
            excluded_paths.add_exclusion(config_path, ".generated/")

            # Load exclusions (includes custom path)
            exclusions = excluded_paths.load_exclusions(config_path)

            current_message = "INTENT: Modify .generated/output.py to update schema"
            file_path = ".generated/output.py"

            result = _regex_stage1_check(current_message, file_path, exclusions)

            assert (
                result == "YES"
            ), f"Expected YES for custom excluded path but got '{result}'."

    def test_excluded_test_folder_bypasses_tdd(self):
        """Test folders (tests/, test/) are excluded — TDD not required for test files."""
        exclusions = excluded_paths.get_default_exclusions()

        for test_file in ("tests/test_auth.py", "test/unit/test_utils.py"):
            current_message = (
                f"INTENT: Modify {os.path.basename(test_file)} to add test case"
            )
            result = _regex_stage1_check(current_message, test_file, exclusions)
            assert result == "YES", (
                f"Expected YES for test file '{test_file}' but got '{result}'. "
                "Test files are in excluded paths and must bypass TDD."
            )

    def test_src_file_not_excluded(self):
        """src/ files are NOT excluded — they remain subject to TDD rules."""
        exclusions = excluded_paths.get_default_exclusions()
        assert excluded_paths.is_excluded_path("src/auth.py", exclusions) is False

    def test_default_exclusion_paths_recognised(self):
        """All default exclusions are correctly identified as excluded."""
        exclusions = excluded_paths.get_default_exclusions()

        for excl in exclusions:
            # Build a plausible file path inside this exclusion
            file_path = os.path.join(excl.rstrip("/"), "sample.py")
            assert excluded_paths.is_excluded_path(
                file_path, exclusions
            ), f"Default exclusion '{excl}' not recognised by is_excluded_path."

    def test_empty_exclusions_handled_gracefully(self):
        """If no exclusions are configured, prompt formatting returns safe default."""
        formatted = excluded_paths.format_exclusions_for_prompt([])
        assert "No excluded paths configured" in formatted

    def test_custom_excluded_path_not_recognised_without_loading(self):
        """Custom path added to config is NOT recognised when using get_default_exclusions()."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "excluded_paths.yaml")
            excluded_paths.add_exclusion(config_path, ".generated/")

            # Using only defaults (no custom config loaded)
            default_exclusions = excluded_paths.get_default_exclusions()
            assert not excluded_paths.is_excluded_path(
                ".generated/output.py", default_exclusions
            ), "Custom path should not appear in default exclusions."
