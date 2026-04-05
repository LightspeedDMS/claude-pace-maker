#!/usr/bin/env python3
"""
Tests for two-stage validation (Story #17).

Validates that:
1. Stage 1 blocks when intent missing in CURRENT message
2. Stage 1 blocks when TDD declaration missing on core path
3. Stage 2 blocks when clean code violations found
4. Both stages pass when all checks pass
5. All validation bypassed for non-source files
6. No validation runs when disabled
7. Stage 1 is fast and token-efficient
"""

from unittest.mock import patch
from pacemaker import intent_validator

# Performance threshold: Stage 1 is pure regex, must complete well under 1 second
STAGE1_MAX_SECONDS = 1.0


class TestStage1DeclarationCheck:
    """Test Stage 1: Fast lightweight regex declaration check."""

    def test_stage1_blocks_missing_intent(self):
        """AC1: Stage 1 blocks when intent missing in CURRENT message."""
        # Current message has no INTENT: marker — regex returns NO naturally
        current_message = "Let me fix this bug now."
        messages = ["Previous context message", current_message]
        file_path = "/path/to/auth.py"
        tool_name = "Write"

        result = intent_validator.validate_intent_and_code(
            messages=messages,
            code="def foo(): pass",
            file_path=file_path,
            tool_name=tool_name,
        )

        # Stage 1 blocks
        assert not result["approved"]
        assert "intent" in result["feedback"].lower()

    def test_stage1_blocks_missing_tdd_on_core_path(self):
        """AC2: Stage 1 blocks when TDD declaration missing on core path."""
        # Current message has INTENT: and mentions file, but no TDD declaration
        # File is under src/ (core path) — regex returns NO_TDD naturally
        current_message = "INTENT: Modify src/auth.py to add validation"
        messages = [current_message]
        file_path = "src/auth.py"
        tool_name = "Write"

        result = intent_validator.validate_intent_and_code(
            messages=messages,
            code="def foo(): pass",
            file_path=file_path,
            tool_name=tool_name,
        )

        # Stage 1 blocks with TDD feedback
        assert not result["approved"]
        assert (
            "tdd" in result["feedback"].lower() or "test" in result["feedback"].lower()
        )

    def test_stage1_passes_with_intent_and_tdd(self):
        """Stage 1 passes when intent AND TDD declared in CURRENT message."""
        # Current message has INTENT:, mentions file, and has TDD declaration
        # File is under src/ (core path) — regex returns YES
        current_message = (
            "INTENT: Modify src/auth.py to add validate_token() function. "
            "Test coverage: tests/test_auth.py::test_validate_token_rejects_expired"
        )
        messages = [current_message]
        file_path = "src/auth.py"
        tool_name = "Write"
        code = "def validate_token(): pass"

        # Only patch Stage 2 (external LLM call) — Stage 1 runs as real regex
        with patch("pacemaker.intent_validator._call_stage2_validation") as mock_s2:
            mock_s2.return_value = "APPROVED"

            result = intent_validator.validate_intent_and_code(
                messages=messages,
                code=code,
                file_path=file_path,
                tool_name=tool_name,
                hook_model="gpt-5",  # bypass SDK_AVAILABLE gate for Stage 2
            )

            # Both stages pass
            assert result["approved"]


class TestStage2CodeReview:
    """Test Stage 2: Comprehensive code review."""

    def test_stage2_blocks_clean_code_violations(self):
        """AC3: Stage 2 blocks when clean code violations found."""
        # Non-core path (no leading src/) with INTENT: and file mention
        # → Stage 1 returns YES via real regex (no internal mock needed)
        current_message = "INTENT: Modify utils.py to add helper function"
        messages = [current_message]
        file_path = "utils.py"
        tool_name = "Write"

        code = """
def risky_function():
    try:
        dangerous_operation()
    except:  # Bare except - violation!
        pass
"""

        # Only mock the external LLM call (Stage 2)
        with patch("pacemaker.intent_validator._call_stage2_validation") as mock_s2:
            mock_s2.return_value = "Clean code violation: Bare except clause found"

            result = intent_validator.validate_intent_and_code(
                messages=messages,
                code=code,
                file_path=file_path,
                tool_name=tool_name,
                hook_model="gpt-5",  # bypass SDK_AVAILABLE gate
            )

            # Stage 2 blocks
            assert not result["approved"]
            assert "violation" in result["feedback"].lower()

    def test_stage2_passes_clean_code(self):
        """AC4: Both stages pass when all checks pass."""
        # Non-core path with INTENT: and file mention → Stage 1 YES via regex
        current_message = "INTENT: Modify utils.py to add helper function"
        messages = [current_message]
        file_path = "utils.py"
        tool_name = "Write"
        code = "def clean_function():\n    return True"

        # Only mock the external LLM call (Stage 2)
        with patch("pacemaker.intent_validator._call_stage2_validation") as mock_s2:
            mock_s2.return_value = "APPROVED"

            result = intent_validator.validate_intent_and_code(
                messages=messages,
                code=code,
                file_path=file_path,
                tool_name=tool_name,
                hook_model="gpt-5",  # bypass SDK_AVAILABLE gate
            )

            # Both stages pass
            assert result["approved"]


class TestValidationBypass:
    """Test validation bypass scenarios."""

    def test_non_source_files_bypass_validation(self):
        """AC5: Non-source files bypass all validation."""
        # This test verifies that hook.py bypasses validation for non-source files
        # The validation functions are never called for .md, .txt, etc.
        # Testing at hook level rather than validator level
        pass  # Covered by test_pre_tool_hook.py

    def test_validation_disabled_no_validation_runs(self):
        """AC6: When disabled, no validation runs."""
        # This test verifies that hook.py doesn't call validator when disabled
        # Testing at hook level rather than validator level
        pass  # Covered by test_pre_tool_hook.py


class TestPerformanceAndTokenEfficiency:
    """Test Stage 1 performance and token efficiency."""

    def test_stage1_is_fast(self):
        """AC7a: Stage 1 completes under STAGE1_MAX_SECONDS (pure regex, no LLM)."""
        import time

        # Message without INTENT: — Stage 1 regex rejects immediately
        current_message = "I will modify test.py to add function"
        messages = [current_message]
        file_path = "/home/project/test.py"
        tool_name = "Write"
        code = "def foo(): pass"

        # Stage 1 is pure regex — no external call, no mock needed
        # Stage 2 is never reached (Stage 1 returns NO), so no patching required
        start = time.time()

        intent_validator.validate_intent_and_code(
            messages=messages,
            code=code,
            file_path=file_path,
            tool_name=tool_name,
        )

        elapsed = time.time() - start

        # Pure regex stage must complete well under STAGE1_MAX_SECONDS
        assert elapsed < STAGE1_MAX_SECONDS

    def test_stage1_uses_only_current_message(self):
        """AC7b: Stage 1 uses ONLY CURRENT message, not full history.

        Proof: INTENT: is present only in old messages but absent in the current
        message. Stage 1 must block (NO), demonstrating it does NOT look at
        history — only the current message.
        """
        # INTENT: is in old messages only, NOT in the current (last) message
        old_message_with_intent = (
            "INTENT: Modify src/auth.py to add validate_token(). "
            "Test coverage: tests/test_auth.py - test_validate_token()"
        )
        current_message = "Now writing the code."  # No INTENT: here
        messages = [old_message_with_intent, current_message]
        file_path = "src/auth.py"
        tool_name = "Write"

        result = intent_validator.validate_intent_and_code(
            messages=messages,
            code="def validate_token(): pass",
            file_path=file_path,
            tool_name=tool_name,
        )

        # Stage 1 must block: INTENT: was only in old message, not current
        # This proves Stage 1 only inspects the current message
        assert not result["approved"]
        assert "intent" in result["feedback"].lower()


class TestShortCircuitBehavior:
    """Test that Stage 2 is skipped when Stage 1 fails."""

    def test_stage2_not_called_when_stage1_fails(self):
        """Stage 2 should be skipped when Stage 1 fails (NO result)."""
        # Message with no INTENT: — regex naturally returns NO
        current_message = "No intent here"
        messages = [current_message]
        file_path = "/home/project/test.py"
        tool_name = "Write"

        with patch("pacemaker.intent_validator._call_stage2_validation") as mock_s2:
            intent_validator.validate_intent_and_code(
                messages=messages,
                code="def foo(): pass",
                file_path=file_path,
                tool_name=tool_name,
            )

            # Stage 2 was NOT called (short-circuit after Stage 1 NO)
            assert not mock_s2.called
