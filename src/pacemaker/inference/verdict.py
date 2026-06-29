"""
Canonical verdict-normalization primitive for all LLM gate checks.

Story #76 (B1): one shared module used by stop-hook (parse_sdk_response),
Stage 2 (Write/Edit pre-tool gate), and danger-bash Phase 2.

Design: STDLIB-ONLY leaf — imports nothing from other pacemaker modules so
all three gates can import it without creating circular dependencies.

Matching strategy — guarded-lenient / starts-with:
- POSITIVE: some line, stripped + uppercased, STARTS WITH the positive token.
  Accepts "APPROVED.", "APPROVED — ok", "APPROVED\\n\\nnice work".
  Rejects "NOT APPROVED" (that line starts with "NOT", not "APPROVED").
- BLOCKED WINS: has_block_marker supersedes is_positive in verdict_passes.
- FAIL-CLOSED: empty / whitespace-only input → all predicates False.

Stop-hook gets a second positive token: COMPLETE: (but BLOCKED still wins).
"""


def is_positive(text: str, positive_token: str = "APPROVED") -> bool:
    """Return True iff some line of *text*, stripped+uppercased, starts with
    *positive_token* (uppercased).

    Guarded-lenient: accepts trailing commentary ("APPROVED.", "APPROVED — ok").
    Fail-closed: empty / whitespace-only → False.
    Does NOT apply BLOCKED priority — callers that need that use verdict_passes.
    """
    token_upper = positive_token.upper()
    for line in text.splitlines():
        if line.strip().upper().startswith(token_upper):
            return True
    return False


def has_block_marker(text: str) -> bool:
    """Return True iff some line, stripped+uppercased, starts with "BLOCKED:"."""
    for line in text.splitlines():
        if line.strip().upper().startswith("BLOCKED:"):
            return True
    return False


def has_complete_marker(text: str) -> bool:
    """Return True iff some line, stripped+uppercased, starts with "COMPLETE:"."""
    for line in text.splitlines():
        if line.strip().upper().startswith("COMPLETE:"):
            return True
    return False


def verdict_passes(text: str, positive_token: str = "APPROVED") -> bool:
    """Canonical pass/fail check for a single-gate LLM response.

    BLOCKED: wins over any positive token (BLOCKED-priority rule).
    Fail-closed: no positive found → False.
    """
    if has_block_marker(text):
        return False
    return is_positive(text, positive_token)


def verdict_passes_for_context(text: str, call_context: object) -> bool:
    """Context-aware pass/fail.

    Default context: verdict_passes(text, "APPROVED").
    stop_hook context: APPROVED OR COMPLETE: counts as positive, but BLOCKED
    still wins over both.
    """
    if call_context == "stop_hook":
        if has_block_marker(text):
            return False
        return is_positive(text, "APPROVED") or has_complete_marker(text)
    return verdict_passes(text, "APPROVED")
