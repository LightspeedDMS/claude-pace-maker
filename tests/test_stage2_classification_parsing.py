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
    """Test Stage 2 structured CLASSIFICATION line parsing.

    Stage 1 is driven via real regex logic by crafting messages that satisfy
    INTENT: + file mention on a non-core path (no TDD declaration required).
    Only the external LLM call (_call_stage2_validation) is mocked.
    """

    def _make_stage2_result(self, stage2_response: str) -> dict:
        """Helper: run validate_intent_and_code with a mocked stage 2 LLM response.

        Stage 1 passes naturally: non-core path (helpers/utils.py has no
        src/lib/core prefix), INTENT: marker present, basename mentioned.
        Only Stage 2 (external LLM call) is mocked.
        """
        # Non-core path: no src/lib/core/source/libraries/kernel prefix
        # Stage 1 regex: INTENT: present + "utils.py" mentioned → YES
        current_message = "INTENT: Modify helpers/utils.py to add helper function."
        messages = [current_message]
        file_path = "helpers/utils.py"
        tool_name = "Write"
        code = "def helper(): return True"

        # _call_stage2_validation returns (response_text, reviewer_name) tuple
        with patch("pacemaker.intent_validator._call_stage2_validation") as mock_s2:
            mock_s2.return_value = (stage2_response, "test-reviewer")

            return intent_validator.validate_intent_and_code(
                messages=messages,
                code=code,
                file_path=file_path,
                tool_name=tool_name,
                hook_model="gpt-5",  # bypass SDK_AVAILABLE gate for Stage 2
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


class TestStage2RejectionCategorization:
    """Unit tests for end-to-end rejection categorization via Stage 2 response parsing.

    Stage 1 is driven via real regex: non-core paths with INTENT: + file mention,
    or core paths with full TDD declaration. Only Stage 2 (external LLM) is mocked.
    """

    def test_missing_functionality_categorized_as_cleancode(self):
        """Missing functionality rejection maps to clean_code_failure=True.

        This was the original bug: 'MISSING FUNCTIONALITY' didn't match keyword list
        so it was miscategorized. Now CLASSIFICATION line drives the result.
        """
        # Core path (src/) with TDD declaration → Stage 1 returns YES via real regex
        current_message = (
            "INTENT: Modify src/config.py to add OntapConfig dataclass, "
            "add it as Optional field on ServerConfig, and add dict-to-dataclass conversion. "
            "Test coverage: tests/test_config.py - test_ontap_config()"
        )
        messages = [current_message]
        file_path = "src/config.py"
        tool_name = "Edit"
        code = """
@dataclass
class OntapConfig:
    host: str
    port: int
"""
        stage2_response = """⛔ Code Review Violations Found

CHECK 1: MISSING FUNCTIONALITY
The intent declares three changes but only one is present in the proposed code:
1. ✓ Add OntapConfig dataclass — DONE
2. ✗ Add it as Optional field on ServerConfig — MISSING
3. ✗ Add dict-to-dataclass conversion in load_config() — NOT IN THIS EDIT

CLASSIFICATION: CLEAN_CODE"""

        # _call_stage2_validation returns (response_text, reviewer_name) tuple
        with patch("pacemaker.intent_validator._call_stage2_validation") as mock_s2:
            mock_s2.return_value = (stage2_response, "test-reviewer")

            result = intent_validator.validate_intent_and_code(
                messages=messages,
                code=code,
                file_path=file_path,
                tool_name=tool_name,
                hook_model="gpt-5",  # bypass SDK_AVAILABLE gate
            )

        assert result["approved"] is False
        assert result.get("clean_code_failure") is True, (
            "Missing functionality rejection must set clean_code_failure=True "
            "so hook.py categorizes it as intent_validation_cleancode"
        )

    def test_approved_has_no_clean_code_failure_flag(self):
        """Approved result does not have clean_code_failure key."""
        # Non-core path with INTENT: + file mention → Stage 1 YES via real regex
        current_message = "INTENT: Modify helpers/utils.py to add helper."
        messages = [current_message]
        file_path = "helpers/utils.py"

        # _call_stage2_validation returns (response_text, reviewer_name) tuple
        with patch("pacemaker.intent_validator._call_stage2_validation") as mock_s2:
            mock_s2.return_value = ("APPROVED", "test-reviewer")

            result = intent_validator.validate_intent_and_code(
                messages=messages,
                code="def helper(): return True",
                file_path=file_path,
                tool_name="Write",
                hook_model="gpt-5",  # bypass SDK_AVAILABLE gate
            )

        assert result["approved"] is True
        # clean_code_failure should not be in approved results
        assert "clean_code_failure" not in result
