"""
Secrets masking engine.

Provides functions to mask secret values in text and nested data structures.
"""

import copy
import re
from typing import Any, List, Tuple, Optional


def _build_secrets_pattern(secrets: List[str]) -> Optional[re.Pattern]:
    """
    Build a single compiled regex pattern for all secrets.

    Uses re.escape() to safely handle regex special characters in secrets.

    Args:
        secrets: List of secret values to create pattern from

    Returns:
        Compiled regex pattern, or None if no valid secrets
    """
    if not secrets:
        return None

    # Escape special regex chars and filter empty strings
    escaped = [re.escape(s) for s in secrets if s]
    if not escaped:
        return None

    # Join with | (OR) operator for single-pass matching
    pattern_str = "|".join(escaped)
    return re.compile(pattern_str)


def mask_text(
    content: str, secrets: List[str], pattern: Optional[re.Pattern] = None
) -> Tuple[str, int]:
    """
    Replace all occurrences of secrets in text with mask placeholder.

    Performs case-sensitive exact string replacement using compiled regex
    for high performance (O(n) instead of O(n*m)).

    Args:
        content: The text content to mask
        secrets: List of secret values to replace
        pattern: Optional pre-compiled pattern (if None, builds from secrets)

    Returns:
        Tuple of (masked text, count of secrets masked)
    """
    # Use provided pattern or build new one
    if pattern is None:
        pattern = _build_secrets_pattern(secrets)

    if pattern is None:
        return content, 0

    # Count occurrences before replacement
    matches = pattern.findall(content)
    mask_count = len(matches)

    # Replace all matches with mask placeholder
    masked = pattern.sub("*** MASKED ***", content)

    return masked, mask_count


def mask_structure(
    data: Any, secrets: List[str], pattern: Optional[re.Pattern] = None
) -> Tuple[Any, int]:
    """
    Recursively mask secrets in nested data structures.

    Creates a deep copy of the input and replaces all string values
    containing secrets with the mask placeholder.

    Supports:
    - Dictionaries (recursively traversed)
    - Lists (recursively traversed)
    - Tuples (recursively traversed, returned as tuples)
    - Strings (masked if containing secrets)
    - Other types (returned unchanged)

    Args:
        data: The data structure to mask
        secrets: List of secret values to replace
        pattern: Optional pre-compiled pattern (if None, builds from secrets)

    Returns:
        Tuple of (deep copy of data with all secrets masked, count of secrets masked)
    """
    # Build pattern once if not provided (for top-level call)
    if pattern is None:
        pattern = _build_secrets_pattern(secrets)

    # Handle None
    if data is None:
        return None, 0

    # Handle strings - apply text masking with pattern
    if isinstance(data, str):
        return mask_text(data, secrets, pattern)

    # Handle dictionaries - recurse on values with pattern
    if isinstance(data, dict):
        result = {}
        total_count = 0
        for key, value in data.items():
            masked_value, count = mask_structure(value, secrets, pattern)
            result[key] = masked_value
            total_count += count
        return result, total_count

    # Handle lists - recurse on elements with pattern
    if isinstance(data, list):
        result_list = []
        total_count = 0
        for item in data:
            masked_item, count = mask_structure(item, secrets, pattern)
            result_list.append(masked_item)
            total_count += count
        return result_list, total_count

    # Handle tuples - recurse on elements with pattern, return as tuple
    if isinstance(data, tuple):
        result_items = []
        total_count = 0
        for item in data:
            masked_item, count = mask_structure(item, secrets, pattern)
            result_items.append(masked_item)
            total_count += count
        return tuple(result_items), total_count

    # For all other types (int, bool, float, etc.), return a copy
    # Use copy.deepcopy to handle any complex objects
    return copy.deepcopy(data), 0
