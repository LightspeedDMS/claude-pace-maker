#!/usr/bin/env python3
"""
Manual test to demonstrate flexible IMPLEMENTATION_COMPLETE matching.

This script shows that the updated is_implementation_complete_response()
function now accepts IMPLEMENTATION_COMPLETE anywhere in the text, not just
as an exact standalone message.
"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pacemaker.lifecycle import is_implementation_complete_response


def test_flexible_matching():
    """Demonstrate various formats that now work."""

    print("Testing flexible IMPLEMENTATION_COMPLETE matching\n")
    print("=" * 70)

    test_cases = [
        # Should match
        ("IMPLEMENTATION_COMPLETE", True, "Exact match"),
        ("  IMPLEMENTATION_COMPLETE  ", True, "With whitespace"),
        ("All done. IMPLEMENTATION_COMPLETE", True, "Text before"),
        ("IMPLEMENTATION_COMPLETE. Moving on.", True, "Text after"),
        (
            "All tests pass.\n\nIMPLEMENTATION_COMPLETE\n\nReady for deployment.",
            True,
            "Text before and after",
        ),
        (
            """All tasks completed:
- Unit tests: PASS
- Integration tests: PASS

IMPLEMENTATION_COMPLETE

Ready for deployment.""",
            True,
            "Multiline",
        ),
        # Should NOT match
        ("implementation_complete", False, "Lowercase"),
        ("MY_IMPLEMENTATION_COMPLETE_THING", False, "Part of variable name"),
        ("IMPLEMENTATION_COMPLETE_VAR", False, "Prefix to variable"),
        ("All tasks complete", False, "Missing marker"),
        ("", False, "Empty string"),
    ]

    passed = 0
    failed = 0

    for text, expected, description in test_cases:
        result = is_implementation_complete_response(text)
        status = "PASS" if result == expected else "FAIL"

        if result == expected:
            passed += 1
        else:
            failed += 1

        print(f"\n[{status}] {description}")
        print(f"  Expected: {expected}")
        print(f"  Got: {result}")

        # Show first 60 chars of text
        display_text = text.replace("\n", "\\n")
        if len(display_text) > 60:
            display_text = display_text[:60] + "..."
        print(f'  Text: "{display_text}"')

    print("\n" + "=" * 70)
    print(f"\nResults: {passed} passed, {failed} failed")

    if failed > 0:
        print("\nFAILURE: Some tests did not pass!")
        return False
    else:
        print("\nSUCCESS: All tests passed!")
        return True


if __name__ == "__main__":
    success = test_flexible_matching()
    sys.exit(0 if success else 1)
