"""Canonical model aliases shared across inference providers.

Single source of truth for:
- KNOWN_MODELS: full set of supported model tokens
- SHORT_ALIASES: user-facing short forms that map to canonical tokens
- is_known_model(): validates a token against all accepted shapes

Kept in a leaf module (no imports from sibling providers) to avoid
circular imports between competitive.py and codex_provider.py.
"""

import re

KNOWN_MODELS = {
    "auto",
    "sonnet",
    "opus",
    "haiku",
    "fable",
    "gpt-5.4",
    "gpt-5.5",
    "gemini-flash",
    "gemini-pro",
    # Antigravity CLI (agy) models — Story #72
    "agy",
    "agy-flash",
    "agy-flash-low",
    "agy-flash-medium",
    "agy-flash-high",
    "agy-pro",
    "agy-pro-low",
    "agy-pro-high",
    "agy-gpt-oss",
    "agy-sonnet",
    "agy-opus",
}

SHORT_ALIASES = {
    "gem-flash": "gemini-flash",
    "gem-pro": "gemini-pro",
    "gpt-5": "gpt-5.5",  # user-friendly alias; resolves to latest (gpt-5.5); Codex CLI accepts both gpt-5.4 and gpt-5.5
    "gpt": "gpt-5.5",  # short alias for latest Codex model
    "codex": "gpt-5.5",  # descriptive alias for latest Codex model
}

# Regex for dynamic codex-<profile> tokens (Story #74)
_CODEX_PROFILE_RE = re.compile(r"^codex-[A-Za-z0-9][A-Za-z0-9._-]*$")


def is_known_model(token: str) -> bool:
    """Return True if token is a valid known model or codex-<profile> shape.

    Accepts:
    - Any token in KNOWN_MODELS (the canonical set)
    - Any token in SHORT_ALIASES (e.g. codex, gpt-5, gem-flash, gem-pro — valid
      CLI tokens that resolve to canonical names; NOT in KNOWN_MODELS itself, but
      must be accepted here so that story #75 CLI validation does not regress)
    - Any codex-<profile> token matching ^codex-[A-Za-z0-9][A-Za-z0-9._-]*$
      (profile existence is validated by codex CLI at runtime, not here)
    """
    if token in KNOWN_MODELS:
        return True
    # SHORT_ALIASES keys are valid user-facing tokens but not in KNOWN_MODELS.
    # Accept them here to avoid regressing the story #75 hook-model CLI.
    if token in SHORT_ALIASES:
        return True
    # Dynamic codex-<profile> shape — binds a named ~/.codex profile
    return _CODEX_PROFILE_RE.match(token) is not None
