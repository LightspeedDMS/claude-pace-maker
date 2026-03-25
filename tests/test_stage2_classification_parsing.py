#!/usr/bin/env python3
"""
Tests for Stage 2 structured CLASSIFICATION parsing (Bug #51 fix).

Validates that:
1. Stage 2 APPROVED response is approved (no CLASSIFICATION line needed)
2. CLASSIFICATION: CLEAN_CODE sets clean_code_failure = True
3. CLASSIFICATION: INTENT_MISMATCH sets clean_code_failure = False
4. Missing CLASSIFICATION line in a non-APPROVED response defaults to clean_code_failure = True
5. Keyword matching logic is entirely removed (not present in codebase)
6. The CLASSIFICATION line can appear anywhere in the response
7. Classification is case-insensitive
"""

from unittest.mock import patch
from pacemaker import intent_validator


class TestStage2ClassificationParsing:
    """Test Stage 2 structured CLASSIFICATION line parsing."""

    def _make_stage2_result(self, stage2_response: str) -> dict:
        """Helper: run validate_intent_and_code with a mocked stage 2 response."""
        current_message = "INTENT: Modify utils.py to add helper function. Test coverage: tests/test_utils.py - test_helper()"
        messages = [current_message]
        file_path = "/home/project/utils.py"
        tool_name = "Write"
        code = "def helper(): return True"

        with (
            patch("pacemaker.intent_validator.SDK_AVAILABLE", True),
            patch("pacemaker.intent_validator._call_stage1_validation") as mock_s1,
            patch(
                "pacemaker.intent_validator._call_unified_validation_async"
            ) as mock_s2,
        ):
            mock_s1.return_value = "YES"
            mock_s2.return_value = stage2_response

            return intent_validator.validate_intent_and_code(
                messages=messages,
                code=code,
                file_path=file_path,
                tool_name=tool_name,
            )

    def test_approved_response_is_approved(self):
        """APPROVED response (exact) yields approved=True."""
        result = self._make_stage2_result("APPROVED")
        assert result["approved"] is True

    def test_approved_response_case_insensitive(self):
        """Lowercase 'approved' also yields approved=True."""
        result = self._make_stage2_result("approved")
        assert result["approved"] is True

    def test_approved_response_with_whitespace(self):
        """APPROVED with surrounding whitespace yields approved=True."""
        result = self._make_stage2_result("  APPROVED  ")
        assert result["approved"] is True

    def test_classification_clean_code_sets_flag(self):
        """CLASSIFICATION: CLEAN_CODE sets clean_code_failure=True."""
        response = """⛔ Code Review Violations Found

CHECK 2: CLEAN CODE VIOLATION
Using bare except clause on line 3.

CLASSIFICATION: CLEAN_CODE"""
        result = self._make_stage2_result(response)
        assert result["approved"] is False
        assert result.get("clean_code_failure") is True

    def test_classification_intent_mismatch_clears_flag(self):
        """CLASSIFICATION: INTENT_MISMATCH sets clean_code_failure=False."""
        response = """⛔ Code Review Violations Found

CHECK 1: MISSING FUNCTIONALITY
The intent declares three changes but only one is present.

CLASSIFICATION: INTENT_MISMATCH"""
        result = self._make_stage2_result(response)
        assert result["approved"] is False
        assert result.get("clean_code_failure") is False

    def test_missing_classification_defaults_to_clean_code(self):
        """Non-APPROVED response without CLASSIFICATION line defaults to clean_code_failure=True.

        Rationale: Stage 2 IS the code review stage. Any stage 2 rejection is
        a code review issue, so we default to clean_code_failure=True.
        """
        response = """⛔ Code Review Violations Found

MISSING FUNCTIONALITY: The intent declares adding field X but it is absent.
NOT IN THIS EDIT: load_config() conversion not present."""
        result = self._make_stage2_result(response)
        assert result["approved"] is False
        assert result.get("clean_code_failure") is True

    def test_classification_line_in_middle_of_response(self):
        """CLASSIFICATION line is parsed regardless of position in response."""
        response = """⛔ Code Review Violations Found

CLASSIFICATION: CLEAN_CODE

CHECK 2: Magic number 42 used on line 5. Use a named constant."""
        result = self._make_stage2_result(response)
        assert result["approved"] is False
        assert result.get("clean_code_failure") is True

    def test_classification_case_insensitive(self):
        """CLASSIFICATION value matching is case-insensitive."""
        response = """⛔ Code Review Violations Found

CLASSIFICATION: clean_code

Bare except clause on line 3."""
        result = self._make_stage2_result(response)
        assert result["approved"] is False
        assert result.get("clean_code_failure") is True

    def test_classification_intent_mismatch_lowercase(self):
        """CLASSIFICATION: intent_mismatch (lowercase) works correctly."""
        response = """⛔ Code Review Violations Found

CLASSIFICATION: intent_mismatch

Missing functionality declared in intent."""
        result = self._make_stage2_result(response)
        assert result["approved"] is False
        assert result.get("clean_code_failure") is False

    def test_feedback_preserved_in_rejection(self):
        """Rejection result preserves the feedback text."""
        response = """⛔ Code Review Violations Found

Bare except clause on line 3.

CLASSIFICATION: CLEAN_CODE"""
        result = self._make_stage2_result(response)
        assert result["approved"] is False
        assert "feedback" in result
        assert len(result["feedback"]) > 0

    def test_no_keyword_matching_logic_present(self):
        """Verify keyword matching list is not present in the source code.

        The clean_code_keywords list must be removed as part of this bug fix.
        This test reads the source and asserts the list does not exist.
        """
        import os

        source_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "src",
            "pacemaker",
            "intent_validator.py",
        )
        with open(os.path.realpath(source_path)) as f:
            source_text = f.read()

        # The old keyword list variable must be gone
        assert (
            "clean_code_keywords" not in source_text
        ), "clean_code_keywords variable still present — keyword matching not removed"

    def test_unknown_classification_value_defaults_to_clean_code(self):
        """Unknown CLASSIFICATION value defaults to clean_code_failure=True (safe default)."""
        response = """⛔ Code Review Violations Found

Some violation description.

CLASSIFICATION: UNKNOWN_FUTURE_VALUE"""
        result = self._make_stage2_result(response)
        assert result["approved"] is False
        # Unknown values should default to clean_code (safe default for stage 2)
        assert result.get("clean_code_failure") is True


class TestStage2ClassificationIntegration:
    """Integration tests for end-to-end rejection categorization."""

    def test_missing_functionality_categorized_as_cleancode(self):
        """End-to-end: missing functionality rejection maps to clean_code_failure=True.

        This was the original bug: 'MISSING FUNCTIONALITY' didn't match keyword list
        so it was miscategorized as intent_validation instead of intent_validation_cleancode.
        """
        current_message = (
            "INTENT: Modify src/config.py to add OntapConfig dataclass, "
            "add it as Optional field on ServerConfig, and add dict-to-dataclass conversion. "
            "Test coverage: tests/test_config.py - test_ontap_config()"
        )
        messages = [current_message]
        file_path = "/home/project/src/config.py"
        tool_name = "Edit"
        code = """
@dataclass
class OntapConfig:
    host: str
    port: int
"""
        # Only 1 of 3 declared changes present — stage 2 correctly rejects
        stage2_response = """⛔ Code Review Violations Found

CHECK 1: MISSING FUNCTIONALITY
The intent declares three changes but only one is present in the proposed code:
1. ✓ Add OntapConfig dataclass — DONE
2. ✗ Add it as Optional field on ServerConfig — MISSING
3. ✗ Add dict-to-dataclass conversion in load_config() — NOT IN THIS EDIT

CLASSIFICATION: CLEAN_CODE"""

        with (
            patch("pacemaker.intent_validator.SDK_AVAILABLE", True),
            patch("pacemaker.intent_validator._call_stage1_validation") as mock_s1,
            patch(
                "pacemaker.intent_validator._call_unified_validation_async"
            ) as mock_s2,
        ):
            mock_s1.return_value = "YES"
            mock_s2.return_value = stage2_response

            result = intent_validator.validate_intent_and_code(
                messages=messages,
                code=code,
                file_path=file_path,
                tool_name=tool_name,
            )

        assert result["approved"] is False
        assert result.get("clean_code_failure") is True, (
            "Missing functionality rejection must set clean_code_failure=True "
            "so hook.py categorizes it as intent_validation_cleancode"
        )

    def test_approved_has_no_clean_code_failure_flag(self):
        """Approved result does not have clean_code_failure key."""
        current_message = (
            "INTENT: Modify utils.py to add helper. "
            "Test coverage: tests/test_utils.py - test_helper()"
        )
        messages = [current_message]
        file_path = "/home/project/utils.py"

        with (
            patch("pacemaker.intent_validator.SDK_AVAILABLE", True),
            patch("pacemaker.intent_validator._call_stage1_validation") as mock_s1,
            patch(
                "pacemaker.intent_validator._call_unified_validation_async"
            ) as mock_s2,
        ):
            mock_s1.return_value = "YES"
            mock_s2.return_value = "APPROVED"

            result = intent_validator.validate_intent_and_code(
                messages=messages,
                code="def helper(): return True",
                file_path=file_path,
                tool_name="Write",
            )

        assert result["approved"] is True
        # clean_code_failure should not be in approved results
        assert "clean_code_failure" not in result
