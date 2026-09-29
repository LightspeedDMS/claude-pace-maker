"""Abstract base class for inference providers."""

from abc import ABC, abstractmethod
from typing import Optional


class ProviderError(Exception):
    """Raised when a provider fails to get a response."""

    pass


class InferenceProvider(ABC):
    """Abstract interface for model inference providers."""

    @abstractmethod
    def query(
        self,
        prompt: str,
        system_prompt: str = "",
        model_hint: str = "",
        max_thinking_tokens: int = 4000,
        timeout: Optional[float] = None,
    ) -> str:
        """Query the model and return response text.

        Args:
            prompt: The user/validation prompt
            system_prompt: System instructions for the model
            model_hint: Model identifier (e.g., "sonnet", "opus", "gpt-5.4", "gpt-5.5")
            max_thinking_tokens: Max thinking/reasoning tokens
            timeout: Issue #152. Optional deadline-aware override for the
                provider's own subprocess/SDK timeout. When supplied, the
                provider clamps to ``min(its own hardcoded ceiling,
                timeout)`` -- never RAISES the timeout above its known-safe
                default, only ever shrinks it toward the caller's
                remaining budget. ``None`` (the default) preserves each
                provider's pre-#152 hardcoded behavior byte-identically
                for every existing caller (e.g. the competitive multi-
                reviewer path, which never passes this).

        Returns:
            Response text from the model

        Raises:
            ProviderError: If the provider fails to get a response
        """
        pass
