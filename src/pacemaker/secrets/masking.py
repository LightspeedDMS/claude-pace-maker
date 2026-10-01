"""
Secrets masking engine.

Provides functions to mask secret values in text and nested data structures.
"""

import copy
import re
from typing import Any, Iterable, List, Tuple, Optional

# The placeholder that replaces every masked secret.
MASK_MARKER = "*** MASKED ***"

# Bug #160 item 2: stored secrets SHORTER than this are masked only as
# standalone tokens (a word-character edge must not touch another
# [A-Za-z0-9_]), so a short secret like "value" no longer mangles
# "raises_value_error". Secrets of this length or more keep match-anywhere
# behaviour. (The word class is ASCII-only: "é" counts as a boundary, which can
# only over-mask -- the safe direction.)
SHORT_SECRET_TOKEN_BOUNDARY_BELOW = 8
_WORD_BEFORE_NOT = r"(?<![A-Za-z0-9_])"
_WORD_AFTER_NOT = r"(?![A-Za-z0-9_])"


def is_degenerate_secret(value: str) -> bool:
    """Bug #160: True for values that must never be stored or used as secrets.

    A masked string pasted into a SECRET_TEXT declaration stores a fragment of
    ``MASK_MARKER`` as a "secret"; every masked value then contains a secret
    and each stored fragment keeps re-masking ordinary text. Degenerate means:
    empty/whitespace-only, a substring of the marker (case-sensitive), or text
    made only of marker repetitions and whitespace. Real content that merely
    surrounds a marker is NOT degenerate.
    """
    stripped = value.strip()
    if not stripped:
        return True
    if stripped in MASK_MARKER:
        return True
    return not stripped.replace(MASK_MARKER, "").strip()


_WORD_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
)


class SecretsMasker:
    """Compiled two-pass masker for one set of secrets (bug #160).

    Pass 1 masks every secret of SHORT_SECRET_TOKEN_BOUNDARY_BELOW chars or more,
    anywhere, longest first. Pass 2 masks the shorter secrets only as standalone
    tokens, and only inside the text BETWEEN pass-1 matches: a masked span is an
    impenetrable, non-word separator, so a short secret can neither start in
    front of it and run into it, nor be re-masked inside it. The two passes are
    what guarantee that a short secret failing its boundary can never let
    another short secret match across (and thereby expose) the start of a long
    secret -- which a single combined alternation could not.
    """

    def __init__(
        self, long_pattern: Optional[re.Pattern], short_pattern: Optional[re.Pattern]
    ):
        self._long = long_pattern
        self._short = short_pattern

    def mask(self, content: str) -> Tuple[str, int]:
        """Return ``(masked content, number of secrets masked)``."""
        count = 0
        if self._long is not None:
            content, count = self._long.subn(MASK_MARKER, content)
        if self._short is not None:
            masked_parts = []
            for part in content.split(MASK_MARKER):
                masked_part, part_count = self._short.subn(MASK_MARKER, part)
                masked_parts.append(masked_part)
                count += part_count
            content = MASK_MARKER.join(masked_parts)
        return content, count


def _short_secret_alternative(secret: str) -> str:
    """Escaped short secret plus word-edge guards. A guard is only needed on an
    edge that is itself a word character (``\\b``-style): ``-abc`` is masked in
    ``x-abc``, while ``abc`` is protected inside ``xabc``."""
    before = _WORD_BEFORE_NOT if secret[0] in _WORD_CHARS else ""
    after = _WORD_AFTER_NOT if secret[-1] in _WORD_CHARS else ""
    return f"{before}{re.escape(secret)}{after}"


def _build_secrets_pattern(secrets: List[str]) -> Optional[SecretsMasker]:
    """
    Build the masker for all secrets (see ``SecretsMasker``).

    Uses re.escape() to safely handle regex special characters in secrets.
    Each pass is a single alternation, longest secret first (by the secret's own
    length; sorted() is stable, so equal lengths keep store order).

    Args:
        secrets: List of secret values to create pattern from

    Returns:
        A ``SecretsMasker``, or None if no valid secrets
    """
    ordered = sorted((s for s in secrets or [] if s), key=len, reverse=True)
    if not ordered:
        return None

    long_secrets = [s for s in ordered if len(s) >= SHORT_SECRET_TOKEN_BOUNDARY_BELOW]
    short_secrets = [s for s in ordered if len(s) < SHORT_SECRET_TOKEN_BOUNDARY_BELOW]
    long_pattern = (
        re.compile("|".join(re.escape(s) for s in long_secrets))
        if long_secrets
        else None
    )
    short_pattern = (
        re.compile("|".join(_short_secret_alternative(s) for s in short_secrets))
        if short_secrets
        else None
    )
    return SecretsMasker(long_pattern, short_pattern)


def collect_strings(data: Any) -> List[str]:
    """Every string ``mask_structure`` would mask in ``data``: string VALUES
    reached through dicts, lists and tuples (dict KEYS are never masked, so
    they are never collected; other container/scalar types are copied
    unmasked, so they hold nothing to collect). Iterative -- no recursion
    limit on deep payloads."""
    found: List[str] = []
    stack = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            found.append(item)
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return found


def build_prefiltered_pattern(
    secrets: Iterable[str], texts: Iterable[str]
) -> Tuple[List[str], Optional[SecretsMasker]]:
    """Bug #157: keep only the stored secrets that actually OCCUR in the
    payload, then build and compile the pattern from that subset.

    Compiling one regex from the whole store (hundreds of secrets, MBs of
    ``SECRET_FILE`` content) cost ~8 s of CPU per hook process -- more than
    SubagentStart's 10 s budget -- while at most a handful of secrets ever
    occur in a given payload. A secret that occurs in no payload string can
    never match, and deleting an alternative that can never match changes
    neither WHICH alternative wins at any position (the survivors keep their
    relative longest-first order) nor any count -- so the masked output is
    byte-identical to full-store masking, by construction.

    Shared by ``secrets.sanitizer.sanitize_trace`` (every Langfuse push) and
    ``intent_validator._mask_reviewer_prompt``. Applies NO minimum secret
    length: that is the reviewer-prompt caller's own rule, applied before it
    calls this.

    Args:
        secrets: stored secret values (empty values are ignored).
        texts: the payload strings (see ``collect_strings``).

    Returns:
        ``(relevant, pattern)`` -- the occurring secrets in their original
        order, and the compiled pattern (``None`` when none occur).
    """
    payload = [text for text in texts if text]
    if not payload:
        return [], None
    longest = max(len(text) for text in payload)
    relevant: List[str] = []
    for secret in secrets:
        # A secret longer than every payload string cannot occur in any.
        if not secret or len(secret) > longest:
            continue
        if any(secret in text for text in payload):
            relevant.append(secret)
    if not relevant:
        return [], None
    return relevant, _build_secrets_pattern(relevant)


def mask_text(
    content: str, secrets: List[str], pattern: Optional[SecretsMasker] = None
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

    # Replace all matches and count in single pass
    masked, mask_count = pattern.mask(content)

    return masked, mask_count


def mask_structure(
    data: Any, secrets: List[str], pattern: Optional[SecretsMasker] = None
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
