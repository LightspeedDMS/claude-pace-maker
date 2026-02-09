#!/usr/bin/env python3
"""
Intel parser for prompt intelligence metadata.

Parses § intel lines from assistant responses containing:
- △ frustration (0.0-1.0)
- ◎ specificity (surg|const|outc|expl)
- ■ task type (bug|feat|refac|research|test|docs|debug|conf|other)
- ◇ quality (0.0-1.0)
- ↻ iteration (1-9)

Example intel line:
§ △0.8 ◎surg ■bug ◇0.7 ↻2
"""

import re
from typing import Optional, Dict, Any


# Intel marker
INTEL_MARKER = "§"

# Valid enum values
VALID_SPECIFICITY = ["surg", "const", "outc", "expl"]
VALID_TASK_TYPES = [
    "bug",
    "feat",
    "refac",
    "research",
    "test",
    "docs",
    "debug",
    "conf",
    "other",
]


def parse_intel_line(response: str) -> Optional[dict]:
    """
    Parse intel line from assistant response.

    Searches for a line starting with § marker and extracts intelligence fields.
    Missing or invalid fields are not included in the result (no defaults).

    Args:
        response: Assistant response text (may contain multiple lines)

    Returns:
        dict with parsed fields, or None if no intel marker found
        Possible keys: frustration, specificity, task_type, quality, iteration
    """
    # Search for intel line
    for line in response.split("\n"):
        line = line.strip()
        if line.startswith(INTEL_MARKER):
            return _parse_intel_fields(line)

    return None


def _parse_intel_fields(line: str) -> Optional[Dict[str, Any]]:
    """
    Parse individual fields from intel line.

    Args:
        line: Intel line starting with §

    Returns:
        dict with parsed fields (only valid fields included), or None if no valid fields
    """
    result: Dict[str, Any] = {}

    # △ frustration (0.0-1.0)
    frustration_match = re.search(r"△(\d+\.?\d*)", line)
    if frustration_match:
        try:
            value = float(frustration_match.group(1))
            if 0.0 <= value <= 1.0:
                result["frustration"] = value
        except ValueError:
            pass  # Skip invalid float

    # ◎ specificity (surg|const|outc|expl)
    specificity_match = re.search(r"◎(surg|const|outc|expl)", line)
    if specificity_match:
        result["specificity"] = specificity_match.group(1)

    # ■ task type (bug|feat|refac|research|test|docs|debug|conf|other)
    task_match = re.search(
        r"■(bug|feat|refac|research|test|docs|debug|conf|other)", line
    )
    if task_match:
        result["task_type"] = task_match.group(1)

    # ◇ quality (0.0-1.0)
    quality_match = re.search(r"◇(\d+\.?\d*)", line)
    if quality_match:
        try:
            value = float(quality_match.group(1))
            if 0.0 <= value <= 1.0:
                result["quality"] = value
        except ValueError:
            pass  # Skip invalid float

    # ↻ iteration (1-9, single digit only)
    # Use word boundary to ensure we don't match first digit of multi-digit numbers
    iteration_match = re.search(r"↻(\d)(?!\d)", line)
    if iteration_match:
        value = int(iteration_match.group(1))
        if 1 <= value <= 9:
            result["iteration"] = value

    return result if result else None


def strip_intel_line(text: str) -> str:
    """
    Remove intel line from output.

    Args:
        text: Text that may contain intel line

    Returns:
        Text with intel line removed
    """
    lines = text.split("\n")
    filtered = [line for line in lines if not line.strip().startswith(INTEL_MARKER)]
    return "\n".join(filtered)
