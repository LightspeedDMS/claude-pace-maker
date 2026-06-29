"""
Tests for LLM response noise stripping and APPROVED/BLOCKED parsing.

Covers the case where LLM reviewers (especially Fable) prepend § intel/sentiment
lines or append narrative explanations after their verdict.
"""

from pacemaker.intent_validator import (
    _strip_llm_noise,
    parse_sdk_response,
)
from pacemaker.inference.verdict import verdict_passes


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
    Stage 2 now uses verdict_passes(stage2_feedback) — guarded-lenient starts-with
    matching via the canonical verdict primitive (story #76 B1).

    "APPROVED.", "APPROVED — ok", "APPROVED\\n\\nnarrative" all pass.
    "NOT APPROVED" fails. BLOCKED: wins over APPROVED.
    """

    def test_clean_approved(self):
        assert verdict_passes("APPROVED") is True

    def test_section_then_approved(self):
        # § noise is stripped by _find_verdict before reaching verdict_passes,
        # but verdict_passes also handles this correctly because it scans all lines.
        assert verdict_passes("§ intel\nAPPROVED") is True

    def test_approved_then_narrative(self):
        assert verdict_passes("APPROVED\nnarrative here") is True

    def test_section_approved_narrative(self):
        assert verdict_passes("§ intel\nAPPROVED\nnarrative") is True

    def test_blocked_not_approved(self):
        assert verdict_passes("BLOCKED: reason") is False

    def test_blocked_with_approved_in_body_not_approved(self):
        text = "BLOCKED: reason\nwould have been APPROVED otherwise"
        assert verdict_passes(text) is False

    def test_empty_not_approved(self):
        assert verdict_passes("") is False

    # --- NEW guarded-lenient cases (story #76 deliberate leniency change) ---

    def test_approved_with_period_passes(self):
        """APPROVED. → passes (old strict equality would have blocked this)."""
        assert verdict_passes("APPROVED.") is True

    def test_approved_with_dash_comment_passes(self):
        """APPROVED — ok → passes (old strict equality would have blocked this)."""
        assert verdict_passes("APPROVED — ok") is True

    def test_not_approved_fails(self):
        """NOT APPROVED → fails (line starts with NOT, not APPROVED)."""
        assert verdict_passes("NOT APPROVED") is False
