#!/usr/bin/env python3
"""
Unit tests for hook module.

Tests that hooks load prompts from externalized files.
"""


def test_display_intent_validation_guidance_uses_external_file():
    """Test that intent validation guidance is loaded from external file."""
    from pacemaker.hook import display_intent_validation_guidance

    # Execute: Get guidance
    guidance = display_intent_validation_guidance()

    # Assert: Guidance loaded successfully
    assert guidance is not None
    assert isinstance(guidance, str)
    assert len(guidance) > 0
    # Verify expected content from external file
    assert "INTENT VALIDATION ENABLED" in guidance
    assert "TDD ENFORCEMENT" in guidance
    assert "Declare EXACTLY these 3 components" in guidance
