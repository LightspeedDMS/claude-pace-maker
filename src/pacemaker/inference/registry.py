"""Provider registry and orchestrator with cross-vendor fallback."""

from .provider import ProviderError
from ..logger import log_warning


# Call context → default model hint when hook_model is "auto"
# These match the CURRENT hardcoded defaults in intent_validator.py and code_reviewer.py
_AUTO_DEFAULTS = {
    "stop_hook": "sonnet",  # call_sdk_validation_async: tries sonnet first
    "intent_validation": "sonnet",  # _call_sdk_intent_validation_async
    "stage1": "sonnet",  # _call_stage1_validation_async
    "stage2_unified": "opus",  # _call_unified_validation_async: tries opus first
    "code_review": "sonnet",  # _call_sdk_review_async: tries sonnet first
}


def get_provider(hook_model: str):
    """Get provider instance for the given hook_model config value.

    Args:
        hook_model: Config value - "auto", "sonnet", "opus", "haiku", "gpt-5"

    Returns:
        InferenceProvider instance
    """
    if hook_model in ("auto", "sonnet", "opus", "haiku"):
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider()
    elif hook_model == "gpt-5":
        from .codex_provider import CodexProvider

        return CodexProvider()
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
        hook_model: Config value - "auto", "sonnet", "opus", "gpt-5"
        call_context: Identifies the call site - "stop_hook", "intent_validation",
                      "stage1", "stage2_unified", "code_review"

    Returns:
        Model hint string for the provider
    """
    if hook_model == "auto":
        return _AUTO_DEFAULTS.get(call_context, "sonnet")
    return hook_model


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
    provider = get_provider(hook_model)
    model_hint = resolve_model_for_call(hook_model, call_context)

    try:
        return provider.query(prompt, system_prompt, model_hint, max_thinking_tokens)
    except ProviderError as e:
        if hook_model != "auto":
            log_warning(
                "registry",
                f"Primary provider failed for hook_model='{hook_model}' ({e}), "
                "falling back to auto (Anthropic)",
            )
            from .anthropic_provider import AnthropicProvider

            fallback_provider = AnthropicProvider()
            fallback_hint = resolve_model_for_call("auto", call_context)
            try:
                return fallback_provider.query(
                    prompt, system_prompt, fallback_hint, max_thinking_tokens
                )
            except ProviderError as e2:
                log_warning("registry", f"Fallback also failed ({e2}), fail-open")
                return ""
        else:
            log_warning("registry", f"Anthropic failed ({e}), fail-open")
            return ""
