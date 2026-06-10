"""
Tests for LLM response noise stripping and APPROVED/BLOCKED parsing.

Covers the case where LLM reviewers (especially Fable) prepend § intel/sentiment
lines or append narrative explanations after their verdict.
"""

from pacemaker.intent_validator import (
    _strip_llm_noise,
    parse_sdk_response,
)


# ---------------------------------------------------------------------------
# _strip_llm_noise helper
# ---------------------------------------------------------------------------


class TestStripLlmNoise:
    def test_clean_text_unchanged(self):
        assert _strip_llm_noise("APPROVED") == "APPROVED"

    def test_strips_single_section_line(self):
        result = _strip_llm_noise("§ △0.1 ◎surg ■test ◇0.9 ↻1\nAPPROVED")
        assert result.strip() == "APPROVED"

    def test_strips_multiple_section_lines(self):
        result = _strip_llm_noise("§ line1\n§ line2\nAPPROVED")
        assert result.strip() == "APPROVED"

    def test_does_not_strip_non_section_lines(self):
        result = _strip_llm_noise("APPROVED\nsome narrative")
        assert "APPROVED" in result
        assert "some narrative" in result

    def test_empty_string_unchanged(self):
        assert _strip_llm_noise("") == ""

    def test_only_section_lines_returns_empty_after_strip(self):
        result = _strip_llm_noise("§ line1\n§ line2")
        assert result.strip() == ""

    def test_section_line_in_middle_also_stripped(self):
        result = _strip_llm_noise("APPROVED\n§ mid line\nmore text")
        assert "APPROVED" in result
        assert "§ mid line" not in result
        assert "more text" in result

    def test_section_line_with_no_space_after_section_sign(self):
        # § immediately followed by content (no space) still matches
        result = _strip_llm_noise("§line\nAPPROVED")
        assert result.strip() == "APPROVED"

    def test_whitespace_only_lines_between_section_and_verdict(self):
        result = _strip_llm_noise("§ intel\n\nAPPROVED")
        assert "APPROVED" in result


# ---------------------------------------------------------------------------
# parse_sdk_response — existing behaviours preserved
# ---------------------------------------------------------------------------


class TestParseSdkResponseExisting:
    def test_clean_approved_returns_continue(self):
        result = parse_sdk_response("APPROVED")
        assert result == {"continue": True}

    def test_clean_blocked_returns_block(self):
        result = parse_sdk_response("BLOCKED: some reason")
        assert result["decision"] == "block"
        assert "some reason" in result["reason"]

    def test_empty_string_fail_open(self):
        result = parse_sdk_response("")
        assert result == {"continue": True}

    def test_whitespace_only_fail_open(self):
        result = parse_sdk_response("   \n  ")
        assert result == {"continue": True}

    def test_unexpected_text_fail_open(self):
        result = parse_sdk_response("some random text")
        assert result == {"continue": True}


# ---------------------------------------------------------------------------
# parse_sdk_response — new § stripping behaviours
# ---------------------------------------------------------------------------


class TestParseSdkResponseWithNoise:
    def test_section_line_then_approved(self):
        """§ intel line before APPROVED should still be approved."""
        result = parse_sdk_response("§ △0.1 ◎surg ■test ◇0.9 ↻1\nAPPROVED")
        assert result == {"continue": True}

    def test_section_line_then_blocked(self):
        """§ intel line before BLOCKED should still be blocked."""
        result = parse_sdk_response("§ intel\nBLOCKED: bad intent")
        assert result["decision"] == "block"
        assert "bad intent" in result["reason"]

    def test_multiple_section_lines_then_approved(self):
        result = parse_sdk_response("§ line1\n§ line2\nAPPROVED")
        assert result == {"continue": True}

    def test_multiple_section_lines_then_blocked(self):
        result = parse_sdk_response("§ line1\n§ line2\nBLOCKED: reason")
        assert result["decision"] == "block"


# ---------------------------------------------------------------------------
# parse_sdk_response — APPROVED with trailing narrative
# ---------------------------------------------------------------------------


class TestParseSdkResponseApprovedWithNarrative:
    def test_approved_with_narrative(self):
        """APPROVED followed by explanatory text should still be approved."""
        text = (
            "APPROVED\n"
            "The SD-014 danger-rule match is a false positive: the word "
            '"shutdown" appears only in test file names...'
        )
        result = parse_sdk_response(text)
        assert result == {"continue": True}

    def test_section_line_approved_then_narrative(self):
        text = "§ intel\nAPPROVED\nSome long explanation here."
        result = parse_sdk_response(text)
        assert result == {"continue": True}

    def test_approved_lowercase_with_narrative(self):
        """Case-insensitive first-line check."""
        result = parse_sdk_response("approved\nSome explanation")
        assert result == {"continue": True}


# ---------------------------------------------------------------------------
# parse_sdk_response — APPROVED in narrative of BLOCKED must NOT be approved
# ---------------------------------------------------------------------------


class TestParseSdkResponseApprovedInNarrativeIsBlocked:
    def test_blocked_mentioning_approved_in_explanation(self):
        """BLOCKED response that mentions 'APPROVED' in its reason must remain blocked."""
        text = (
            "BLOCKED: The code does not match the declared intent.\n"
            "The intent said it would only update tests, but it also modified\n"
            "production code. Had the intent matched, it would have been APPROVED."
        )
        result = parse_sdk_response(text)
        assert result["decision"] == "block"

    def test_section_line_blocked_with_approved_in_body(self):
        text = "§ intel\nBLOCKED: reason mentioning APPROVED word\nMore text."
        result = parse_sdk_response(text)
        assert result["decision"] == "block"


# ---------------------------------------------------------------------------
# Stage 2 APPROVED check — via a thin integration path
# ---------------------------------------------------------------------------


class TestStage2ApprovedParsing:
    """
    Stage 2 uses `stage2_feedback.strip().upper() == "APPROVED"`.
    We verify _strip_llm_noise can be composed with that check correctly —
    callers of the helper need: _first_non_noise_line(text).upper() == "APPROVED".

    We expose this indirectly by testing _strip_llm_noise + first-line logic.
    """

    def _is_approved_stage2(self, text: str) -> bool:
        """Replicate the fixed Stage 2 logic for testing."""
        cleaned = _strip_llm_noise(text)
        lines = [line for line in cleaned.splitlines() if line.strip()]
        if not lines:
            return False
        return lines[0].strip().upper() == "APPROVED"

    def test_clean_approved(self):
        assert self._is_approved_stage2("APPROVED") is True

    def test_section_then_approved(self):
        assert self._is_approved_stage2("§ intel\nAPPROVED") is True

    def test_approved_then_narrative(self):
        assert self._is_approved_stage2("APPROVED\nnarrative here") is True

    def test_section_approved_narrative(self):
        assert self._is_approved_stage2("§ intel\nAPPROVED\nnarrative") is True

    def test_blocked_not_approved(self):
        assert self._is_approved_stage2("BLOCKED: reason") is False

    def test_blocked_with_approved_in_body_not_approved(self):
        text = "BLOCKED: reason\nwould have been APPROVED otherwise"
        assert self._is_approved_stage2(text) is False

    def test_empty_not_approved(self):
        assert self._is_approved_stage2("") is False
