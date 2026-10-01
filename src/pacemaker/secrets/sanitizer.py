"""
Trace sanitizer module.

Sanitizes Langfuse traces by masking all stored secrets before upload.
"""

from typing import Any

from .database import get_all_secrets
from .masking import build_prefiltered_pattern, collect_strings, mask_structure
from .metrics import increment_secrets_masked


def sanitize_trace(trace: Any, db_path: str) -> tuple:
    """
    Sanitize a trace by masking all stored secrets.

    Creates a deep copy of the trace and masks all occurrences of secrets
    stored in the database. Records metrics for each secret masked.

    Bug #157: only the stored secrets that actually OCCUR in the trace are
    compiled into the pattern (``masking.build_prefiltered_pattern``). The
    previous design compiled ONE regex from the WHOLE store -- ~8 s of CPU on
    a 765-secret / 5 MB store -- in every hook process (the module-level
    cache that tried to amortize it never survived from one hook process to
    the next). The masked output is byte-identical to full-store masking.

    Args:
        trace: The trace structure to sanitize (dict, list, or any nested structure)
        db_path: Path to the secrets database (also used for metrics)

    Returns:
        Tuple of (sanitized, mask_count) where sanitized is the deep copy with
        all secrets masked, and mask_count is the number of masking operations
        performed (0 means no secrets were found in the trace).
    """
    # Get all secrets from database
    secrets = get_all_secrets(db_path)

    # Keep only the secrets present in this trace, then compile just those
    relevant, pattern = build_prefiltered_pattern(secrets, collect_strings(trace))

    # Apply masking to entire trace structure with the prefiltered pattern
    sanitized, mask_count = mask_structure(trace, relevant, pattern)

    # Restore protected fields that must never be masked
    # userId is essential for Langfuse trace identity (contains user email)
    _restore_protected_fields(trace, sanitized)

    # Record metrics if any secrets were masked
    if mask_count > 0:
        increment_secrets_masked(db_path, count=mask_count)

    return sanitized, mask_count


def _restore_protected_fields(original: Any, sanitized: Any) -> None:
    """
    Restore fields that must never be masked in Langfuse traces.

    The sanitizer masks all string values, but certain fields like userId
    are essential for Langfuse trace identity and must be preserved.

    Handles batch format: list of events, each with body.userId.
    Also handles single trace dicts with top-level userId.
    """
    if isinstance(original, list) and isinstance(sanitized, list):
        for orig_item, san_item in zip(original, sanitized):
            _restore_protected_fields(orig_item, san_item)
    elif isinstance(original, dict) and isinstance(sanitized, dict):
        # Restore userId at this level
        if "userId" in original:
            sanitized["userId"] = original["userId"]
        # Recurse into body (batch event format: {id, type, body: {userId, ...}})
        if "body" in original and "body" in sanitized:
            _restore_protected_fields(original["body"], sanitized["body"])
