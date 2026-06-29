"""
Tests for the canonical verdict-normalization primitive.

Story #76 (B1): guarded-lenient fail-closed verdict primitive + gate convergence.

src/pacemaker/inference/verdict.py is a STDLIB-ONLY leaf module imported by all
three LLM gates (stop-hook parse_sdk_response, Stage 2, danger-bash Phase 2).

Truth table contract:
  - APPROVED           → pass
  - APPROVED.          → pass  (guarded-lenient: starts-with, not equality)
  - APPROVED\\n\\nnice   → pass  (trailing commentary OK)
  - NOT APPROVED       → FAIL  (starts with NOT, not APPROVED)
  - (empty)            → FAIL  (fail-closed)
  - BLOCKED: x         → FAIL  (block marker wins)
  - APPROVED\\nBLOCKED:x→ FAIL  (BLOCKED priority even when APPROVED also present)
  - COMPLETE: done     → pass under stop_hook, FAIL under default
  - BLOCKED:x\\nCOMPLETE→ FAIL  under stop_hook (BLOCKED wins over COMPLETE)
"""

import pytest

from pacemaker.inference.verdict import (
    has_block_marker,
    has_complete_marker,
    is_positive,
    verdict_passes,
    verdict_passes_for_context,
)


# ---------------------------------------------------------------------------
# is_positive — starts-with matching, any line, case-insensitive
# ---------------------------------------------------------------------------


class TestIsPositive:
    def test_clean_approved(self):
        assert is_positive("APPROVED") is True

    def test_approved_with_period(self):
        assert is_positive("APPROVED.") is True

    def test_approved_with_trailing_commentary(self):
        assert is_positive("APPROVED\n\nnice work") is True

    def test_approved_with_dash_commentary(self):
        assert is_positive("APPROVED — looks good") is True

    def test_not_approved_fails(self):
        """NOT APPROVED must not pass — it does NOT start with APPROVED."""
        assert is_positive("NOT APPROVED") is False

    def test_empty_fails(self):
        assert is_positive("") is False

    def test_whitespace_only_fails(self):
        assert is_positive("   \n  ") is False

    def test_blocked_only_fails(self):
        assert is_positive("BLOCKED: reason") is False

    def test_approved_after_blocked_line(self):
        """APPROVED on a later line should still be positive (is_positive only)."""
        assert is_positive("BLOCKED: x\nAPPROVED") is True

    def test_lowercase_approved(self):
        assert is_positive("approved") is True

    def test_mixed_case_approved(self):
        assert is_positive("Approved.") is True

    def test_positive_token_custom(self):
        """Support custom positive_token."""
        assert is_positive("COMPLETE: done", positive_token="COMPLETE:") is True

    def test_approved_multiline_second_line(self):
        """APPROVED on a non-first line must still be positive."""
        assert is_positive("§ intel\nAPPROVED") is True

    def test_not_approved_multiline_not_positive(self):
        """NOT APPROVED anywhere → still fails (doesn't start with APPROVED)."""
        assert is_positive("NOT APPROVED\nSome reason") is False


# ---------------------------------------------------------------------------
# has_block_marker
# ---------------------------------------------------------------------------


class TestHasBlockMarker:
    def test_clean_blocked(self):
        assert has_block_marker("BLOCKED: reason") is True

    def test_blocked_lowercase(self):
        assert has_block_marker("blocked: reason") is True

    def test_blocked_mixed_case(self):
        assert has_block_marker("Blocked: reason") is True

    def test_not_blocked(self):
        assert has_block_marker("APPROVED") is False

    def test_empty(self):
        assert has_block_marker("") is False

    def test_blocked_after_approved_line(self):
        assert has_block_marker("APPROVED\nBLOCKED: x") is True

    def test_blocked_without_colon_not_detected(self):
        """BLOCKED without colon should NOT match (spec requires BLOCKED:)."""
        assert has_block_marker("BLOCKED") is False


# ---------------------------------------------------------------------------
# has_complete_marker
# ---------------------------------------------------------------------------


class TestHasCompleteMarker:
    def test_clean_complete(self):
        assert has_complete_marker("COMPLETE: done") is True

    def test_complete_lowercase(self):
        assert has_complete_marker("complete: done") is True

    def test_not_complete(self):
        assert has_complete_marker("APPROVED") is False

    def test_empty(self):
        assert has_complete_marker("") is False

    def test_complete_after_other_line(self):
        assert has_complete_marker("some text\nCOMPLETE: done") is True


# ---------------------------------------------------------------------------
# verdict_passes — BLOCKED priority over APPROVED; fail-closed
# ---------------------------------------------------------------------------


class TestVerdictPasses:
    @pytest.mark.parametrize(
        "text,expected",
        [
            # truth table from story #76
            ("APPROVED", True),
            ("APPROVED.", True),
            ("APPROVED\n\nnice work", True),
            ("NOT APPROVED", False),
            ("", False),
            ("BLOCKED: x", False),
            ("APPROVED\nBLOCKED: x", False),  # BLOCKED priority
        ],
        ids=[
            "clean_approved",
            "approved_with_period",
            "approved_with_trailing_commentary",
            "not_approved",
            "empty",
            "blocked_only",
            "approved_then_blocked_priority",
        ],
    )
    def test_truth_table(self, text: str, expected: bool):
        assert verdict_passes(text) is expected

    def test_whitespace_only_fails(self):
        assert verdict_passes("   \n  ") is False

    def test_narrative_preamble_then_approved(self):
        """Preamble before APPROVED on its own line → passes."""
        assert verdict_passes("§ intel\nAPPROVED") is True

    def test_approved_lowercase_passes(self):
        assert verdict_passes("approved") is True

    def test_blocked_wins_over_complete(self):
        """BLOCKED priority holds even against COMPLETE:."""
        assert verdict_passes("BLOCKED: x\nCOMPLETE: y") is False


# ---------------------------------------------------------------------------
# verdict_passes_for_context — stop_hook gets COMPLETE: as second positive
# ---------------------------------------------------------------------------


class TestVerdictPassesForContext:
    # --- default context (no call_context or anything other than stop_hook) ---

    def test_default_approved(self):
        assert verdict_passes_for_context("APPROVED", "intent_validation") is True

    def test_default_approved_with_period(self):
        assert verdict_passes_for_context("APPROVED.", "intent_validation") is True

    def test_default_complete_fails(self):
        """COMPLETE: is NOT a positive under default context."""
        assert (
            verdict_passes_for_context("COMPLETE: done", "intent_validation") is False
        )

    def test_default_blocked_fails(self):
        assert verdict_passes_for_context("BLOCKED: x", "intent_validation") is False

    def test_default_empty_fails(self):
        assert verdict_passes_for_context("", "intent_validation") is False

    def test_default_not_approved_fails(self):
        assert verdict_passes_for_context("NOT APPROVED", "intent_validation") is False

    # --- stop_hook context ---

    def test_stop_hook_approved_passes(self):
        assert verdict_passes_for_context("APPROVED", "stop_hook") is True

    def test_stop_hook_complete_passes(self):
        """COMPLETE: is a second positive under stop_hook."""
        assert verdict_passes_for_context("COMPLETE: done", "stop_hook") is True

    def test_stop_hook_blocked_wins_over_complete(self):
        """BLOCKED: STILL WINS over COMPLETE: even under stop_hook."""
        assert (
            verdict_passes_for_context("BLOCKED: x\nCOMPLETE: y", "stop_hook") is False
        )

    def test_stop_hook_empty_fails(self):
        assert verdict_passes_for_context("", "stop_hook") is False

    def test_stop_hook_not_approved_fails(self):
        assert verdict_passes_for_context("NOT APPROVED", "stop_hook") is False

    def test_stop_hook_blocked_fails(self):
        assert verdict_passes_for_context("BLOCKED: x", "stop_hook") is False

    def test_stop_hook_approved_with_trailing_passes(self):
        assert verdict_passes_for_context("APPROVED\n\nnice work", "stop_hook") is True

    # --- None / unknown context falls back to default ---

    def test_none_context_approved_passes(self):
        assert verdict_passes_for_context("APPROVED", None) is True

    def test_none_context_complete_fails(self):
        assert verdict_passes_for_context("COMPLETE: done", None) is False


# ---------------------------------------------------------------------------
# Gate convergence: lenient flip at Stage 2 and danger-bash
# (these tests exercise parse_sdk_response and verdict_passes directly,
#  proving the DELIBERATE behavior change documented in story #76)
# ---------------------------------------------------------------------------


class TestGateLenientFlip:
    """Deliberately lenient inputs that OLD strict equality would have blocked."""

    def test_approved_dot_passes_verdict_passes(self):
        """APPROVED. → verdict_passes True (Stage 2 and danger-bash now accept this)."""
        assert verdict_passes("APPROVED.") is True

    def test_approved_dash_commentary_passes(self):
        """APPROVED — ok → verdict_passes True."""
        assert verdict_passes("APPROVED — ok") is True

    def test_approved_trailing_reasoning(self):
        """APPROVED\\n(the command is safe) → verdict_passes True (danger-bash pattern)."""
        assert verdict_passes("APPROVED\n(the command is safe)") is True

    def test_not_approved_still_blocks(self):
        """NOT APPROVED → verdict_passes False at every gate."""
        assert verdict_passes("NOT APPROVED") is False

    def test_blocked_still_blocks(self):
        """BLOCKED: ... → verdict_passes False at every gate."""
        assert verdict_passes("BLOCKED: intent mismatch") is False
