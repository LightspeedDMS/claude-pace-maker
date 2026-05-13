"""Random selection and sequential failover hook model dispatchers."""

import random as _random
from typing import List, Optional

from ..logger import log_warning, log_debug
from .registry import get_provider, resolve_model_for_call
from .codex_provider import CodexProvider
from .provider import ProviderError
from .model_aliases import KNOWN_MODELS, SHORT_ALIASES
from .competitive import DEFAULT_MAX_THINKING_TOKENS

_REVIEWER_SDK = "anthropic-sdk"


def _parse_expression(
    expression: str,
    operator: str,
    conflicting_ops: tuple,
    mode_label: str,
) -> Optional[List[str]]:
    """Shared parser for random (*) and failover (|) expressions.

    Returns canonical model list, None if expression does not match this type,
    or raises ValueError for malformed expressions belonging to this type.
    """
    if not isinstance(expression, str) or not expression.strip():
        return None
    if operator not in expression:
        return None
    for op in conflicting_ops:
        if op in expression:
            raise ValueError(
                f"Cannot mix '{operator}' with '{op}' in hook-model expression"
            )
    parts = expression.split(operator)
    models = [p.strip() for p in parts if p.strip()]
    if len(models) < 2:
        raise ValueError(f"{mode_label} mode requires at least 2 models")
    models = [SHORT_ALIASES.get(m, m) for m in models]
    for m in models:
        if m not in KNOWN_MODELS:
            raise ValueError(f"Invalid model: {m}")
    if len(models) != len(set(models)):
        raise ValueError("Duplicate models not allowed")
    return models


def parse_random(expression: str) -> Optional[List[str]]:
    """Parse 'm1*m2[*mN]'. Returns model list, None, or raises ValueError."""
    return _parse_expression(expression, "*", ("|", "->", "+"), "Random")


def parse_failover(expression: str) -> Optional[List[str]]:
    """Parse 'm1|m2[|mN]'. Returns model list, None, or raises ValueError."""
    return _parse_expression(expression, "|", ("*", "->", "+"), "Failover")


def _reviewer_name_for(hook_model: str, provider) -> str:
    if isinstance(provider, CodexProvider):
        return "codex-gpt5"
    from .gemini_provider import GeminiProvider

    if isinstance(provider, GeminiProvider):
        return "gem-flash" if hook_model == "gemini-flash" else "gem-pro"
    return "anthropic-sdk"


def _sdk_fallback(prompt, system_prompt, call_context, max_thinking_tokens):
    from .anthropic_provider import AnthropicProvider

    fallback = AnthropicProvider()
    hint = resolve_model_for_call("auto", call_context)
    response = fallback.query(prompt, system_prompt, hint, max_thinking_tokens)
    return response, _REVIEWER_SDK


def run_random(
    models: List[str],
    prompt: str,
    system_prompt: str,
    call_context: str,
    max_thinking_tokens: int = DEFAULT_MAX_THINKING_TOKENS,
) -> tuple:
    """Pick one model uniformly at random; SDK fallback on failure."""
    if not models:
        raise ValueError("run_random requires a non-empty model list")
    chosen = _random.choice(models)
    log_debug("random", f"Picked {chosen} from {models}")
    provider = get_provider(chosen)
    hint = resolve_model_for_call(chosen, call_context)
    try:
        response = provider.query(prompt, system_prompt, hint, max_thinking_tokens)
        return response, _reviewer_name_for(chosen, provider)
    except (ProviderError, TimeoutError, OSError) as e:
        log_warning("random", f"{chosen} failed: {e}; falling back to SDK")
        return _sdk_fallback(prompt, system_prompt, call_context, max_thinking_tokens)


def run_failover(
    models: List[str],
    prompt: str,
    system_prompt: str,
    call_context: str,
    max_thinking_tokens: int = DEFAULT_MAX_THINKING_TOKENS,
) -> tuple:
    """Try models in order; SDK fallback when all fail."""
    if not models:
        raise ValueError("run_failover requires a non-empty model list")
    for model in models:
        provider = get_provider(model)
        hint = resolve_model_for_call(model, call_context)
        try:
            response = provider.query(prompt, system_prompt, hint, max_thinking_tokens)
            log_debug("failover", f"{model} succeeded")
            return response, _reviewer_name_for(model, provider)
        except (ProviderError, TimeoutError, OSError) as e:
            log_warning("failover", f"{model} failed: {e}; trying next")
            continue
    log_warning("failover", f"All {len(models)} models failed; falling back to SDK")
    return _sdk_fallback(prompt, system_prompt, call_context, max_thinking_tokens)
