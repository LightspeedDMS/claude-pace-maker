"""
Trace sanitizer module.

Sanitizes Langfuse traces by masking all stored secrets before upload.
"""

import re
from typing import Any, List, Optional

from .database import get_all_secrets
from .masking import mask_structure, _build_secrets_pattern
from .metrics import increment_secrets_masked

# Global cache for compiled regex pattern
_cached_pattern: Optional[re.Pattern] = None
_cached_secrets_hash: Optional[int] = None


def _get_cached_pattern(secrets: List[str]) -> Optional[re.Pattern]:
    """
    Get cached compiled pattern or build new one if secrets changed.

    Uses hash of sorted secrets list to detect changes. This optimization
    avoids recompiling regex on every sanitize_trace() call when secrets
    haven't changed (common case during a session).

    Args:
        secrets: List of secret values

    Returns:
        Compiled regex pattern, or None if no secrets
    """
    global _cached_pattern, _cached_secrets_hash

    # Compute hash of current secrets
    secrets_hash = hash(tuple(sorted(secrets))) if secrets else None

    # Check if cache is valid
    if _cached_secrets_hash != secrets_hash:
        # Cache miss - rebuild pattern
        _cached_pattern = _build_secrets_pattern(secrets)
        _cached_secrets_hash = secrets_hash

    return _cached_pattern


def sanitize_trace(trace: Any, db_path: str) -> Any:
    """
    Sanitize a trace by masking all stored secrets.

    Creates a deep copy of the trace and masks all occurrences of secrets
    stored in the database. Records metrics for each secret masked.

    Uses pattern caching to optimize performance for repeated calls with
    the same set of secrets.

    Args:
        trace: The trace structure to sanitize (dict, list, or any nested structure)
        db_path: Path to the secrets database (also used for metrics)

    Returns:
        Sanitized deep copy of the trace with all secrets masked
    """
    # Get all secrets from database
    secrets = get_all_secrets(db_path)

    # Get cached pattern (or build new one if secrets changed)
    pattern = _get_cached_pattern(secrets)

    # Apply masking to entire trace structure with cached pattern
    sanitized, mask_count = mask_structure(trace, secrets, pattern)

    # Record metrics if any secrets were masked
    if mask_count > 0:
        increment_secrets_masked(db_path, count=mask_count)

    return sanitized
