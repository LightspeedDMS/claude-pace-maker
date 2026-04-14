"""Provider registry and orchestrator with cross-vendor fallback."""

from .provider import ProviderError
from ..logger import log_warning
from ..codex_usage import (
    get_latest_codex_usage,
    write_codex_usage,
    migrate_codex_usage_schema,
)
from ..constants import DEFAULT_DB_PATH


# Call context → default model hint when hook_model is "auto"
# These match the CURRENT hardcoded defaults in intent_validator.py and code_reviewer.py
_AUTO_DEFAULTS = {
    "stop_hook": "sonnet",  # call_sdk_validation_async: tries sonnet first
    "intent_validation": "sonnet",  # _call_sdk_intent_validation_async
    "stage2_unified": "opus",  # _call_unified_validation_async: tries opus first
    "code_review": "sonnet",  # _call_sdk_review_async: tries sonnet first
}

# Reviewer name constants — identify which provider actually served the request
_REVIEWER_CODEX = "codex-gpt5"
_REVIEWER_GEMINI_FLASH = "gem-flash"
_REVIEWER_GEMINI_PRO = "gem-pro"
_REVIEWER_SDK = "anthropic-sdk"
_REVIEWER_UNKNOWN = "unknown"


def _refresh_codex_usage() -> None:
    """Refresh codex usage DB from session files. Logs and continues on errors."""
    try:
        migrate_codex_usage_schema(DEFAULT_DB_PATH)
        usage = get_latest_codex_usage()
        if usage:
            write_codex_usage(DEFAULT_DB_PATH, usage)
    except Exception as e:
        log_warning("registry", f"Codex usage refresh failed: {e}")


def get_provider(hook_model: str):
    """Get provider instance for the given hook_model config value.

    Args:
        hook_model: Config value - "auto", "sonnet", "opus", "haiku", "gpt-5.4"
                    (legacy alias: "gpt-5"), "gemini-flash", "gemini-pro"

    Returns:
        InferenceProvider instance
    """
    if hook_model in ("auto", "sonnet", "opus", "haiku"):
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider()
    elif hook_model in ("gpt-5", "gpt-5.4"):
        from .codex_provider import CodexProvider

        return CodexProvider()
    elif hook_model in ("gemini-flash", "gemini-pro"):
        from .gemini_provider import GeminiProvider

        return GeminiProvider()
    else:
        log_warning(
            "registry", f"Unknown hook_model '{hook_model}', falling back to Anthropic"
        )
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider()


def resolve_model_for_call(hook_model: str, call_context: str) -> str:
    """Resolve the model hint for a specific call context.

    When hook_model is "auto", returns the per-call-site default that matches
    current hardcoded behavior. Otherwise passes through the hook_model value.

    Args:
        hook_model: Config value - "auto", "sonnet", "opus", "gpt-5.4"
                    (legacy alias: "gpt-5"), "gemini-flash", "gemini-pro"
        call_context: Identifies the call site - "stop_hook", "intent_validation",
                      "stage2_unified", "code_review"

    Returns:
        Model hint string for the provider
    """
    if hook_model == "auto":
        return _AUTO_DEFAULTS.get(call_context, "sonnet")
    return hook_model


def resolve_and_call_with_reviewer(
    hook_model: str,
    prompt: str,
    system_prompt: str,
    call_context: str,
    max_thinking_tokens: int = 4000,
) -> tuple:
    """Top-level orchestrator returning (response, reviewer_name) with fallback.

    Fallback chain: selected provider → auto (Anthropic) → fail-open (empty string)
    When the actual failing provider is CodexProvider, refreshes codex usage DB
    from session files before invoking the fallback provider.

    Reviewer identity is determined from the concrete provider instance actually
    used (not from the hook_model string) to ensure correctness when unknown
    hook_model values fall back to Anthropic.

    Args:
        hook_model: Config value for hook inference model
        prompt: The validation/review prompt
        system_prompt: System instructions for the model
        call_context: Call site identifier for model resolution
        max_thinking_tokens: Max thinking tokens for the model

    Returns:
        Tuple of (response_text, reviewer_name) where reviewer_name identifies
        the provider that actually served the request:
        - "codex-gpt5" for Codex CLI
        - "gem-flash" for Gemini Flash CLI
        - "gem-pro" for Gemini Pro CLI
        - "anthropic-sdk" for Anthropic SDK
        - "unknown" on complete failure (fail-open)
    """
    # Competitive mode detection — must be checked before single-model path
    if "+" in hook_model:
        from .competitive import parse_competitive, run_competitive

        try:
            parsed = parse_competitive(hook_model)
        except ValueError as e:
            log_warning(
                "registry",
                f"Invalid competitive expression '{hook_model}': {e}, falling back to Anthropic auto",
            )
            parsed = None
        if parsed is None:
            log_warning(
                "registry",
                f"hook_model '{hook_model}' contains '+' but is not valid competitive expression, "
                "falling back to Anthropic auto",
            )
            hook_model = "auto"
        else:
            reviewers, synthesizer = parsed
            return run_competitive(
                reviewers,
                synthesizer,
                prompt,
                system_prompt,
                call_context,
                max_thinking_tokens,
            )

    from .codex_provider import CodexProvider
    from .gemini_provider import GeminiProvider

    provider = get_provider(hook_model)
    model_hint = resolve_model_for_call(hook_model, call_context)
    is_codex_provider = isinstance(provider, CodexProvider)
    is_gemini_provider = isinstance(provider, GeminiProvider)

    try:
        response = provider.query(
            prompt, system_prompt, model_hint, max_thinking_tokens
        )
        if is_codex_provider:
            reviewer = _REVIEWER_CODEX
        elif is_gemini_provider:
            reviewer = (
                _REVIEWER_GEMINI_FLASH
                if hook_model == "gemini-flash"
                else _REVIEWER_GEMINI_PRO
            )
        else:
            reviewer = _REVIEWER_SDK
        return response, reviewer
    except ProviderError as e:
        if hook_model != "auto":
            log_warning(
                "registry",
                f"Primary provider failed for hook_model='{hook_model}' ({e}), "
                "falling back to auto (Anthropic)",
            )

            # Refresh codex usage from session files when the actual Codex provider fails
            if is_codex_provider:
                _refresh_codex_usage()

            from .anthropic_provider import AnthropicProvider

            fallback_provider = AnthropicProvider()
            fallback_hint = resolve_model_for_call("auto", call_context)
            try:
                response = fallback_provider.query(
                    prompt, system_prompt, fallback_hint, max_thinking_tokens
                )
                return response, _REVIEWER_SDK
            except ProviderError as e2:
                log_warning("registry", f"Fallback also failed ({e2}), fail-open")
                return "", _REVIEWER_UNKNOWN
        else:
            log_warning("registry", f"Anthropic failed ({e}), fail-open")
            return "", _REVIEWER_UNKNOWN


def resolve_and_call(
    hook_model: str,
    prompt: str,
    system_prompt: str,
    call_context: str,
    max_thinking_tokens: int = 4000,
) -> str:
    """Top-level orchestrator: call provider with cross-vendor fallback.

    Fallback chain: selected provider → auto (Anthropic) → fail-open (empty string)

    Args:
        hook_model: Config value for hook inference model
        prompt: The validation/review prompt
        system_prompt: System instructions for the model
        call_context: Call site identifier for model resolution
        max_thinking_tokens: Max thinking tokens for the model

    Returns:
        Model response text, or empty string on complete failure (fail-open)
    """
    response, _ = resolve_and_call_with_reviewer(
        hook_model, prompt, system_prompt, call_context, max_thinking_tokens
    )
    return response
