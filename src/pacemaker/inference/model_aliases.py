"""Canonical model aliases shared across inference providers.

Single source of truth for:
- KNOWN_MODELS: full set of supported model tokens
- SHORT_ALIASES: user-facing short forms that map to canonical tokens

Kept in a leaf module (no imports from sibling providers) to avoid
circular imports between competitive.py and codex_provider.py.
"""

KNOWN_MODELS = {
    "auto",
    "sonnet",
    "opus",
    "haiku",
    "gpt-5.4",
    "gemini-flash",
    "gemini-pro",
}

SHORT_ALIASES = {
    "gem-flash": "gemini-flash",
    "gem-pro": "gemini-pro",
    "gpt-5": "gpt-5.4",  # backward-compat alias; Codex CLI requires gpt-5.4
}
