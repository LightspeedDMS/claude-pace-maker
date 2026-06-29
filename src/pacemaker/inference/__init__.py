"""Multi-model inference provider abstraction."""

from .provider import InferenceProvider, ProviderError
from .anthropic_provider import AnthropicProvider
from .codex_provider import CodexProvider
from .gemini_provider import GeminiProvider
from .agy_provider import AgyProvider
from .registry import (
    get_provider,
    resolve_and_call,
    resolve_and_call_with_reviewer,
    resolve_model_for_call,
)
from .competitive import parse_competitive, run_mechanical

__all__ = [
    "InferenceProvider",
    "ProviderError",
    "AnthropicProvider",
    "CodexProvider",
    "GeminiProvider",
    "AgyProvider",
    "get_provider",
    "resolve_and_call",
    "resolve_and_call_with_reviewer",
    "resolve_model_for_call",
    "parse_competitive",
    "run_mechanical",
]
