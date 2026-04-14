"""Abstract base class for inference providers."""

from abc import ABC, abstractmethod


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
    ) -> str:
        """Query the model and return response text.

        Args:
            prompt: The user/validation prompt
            system_prompt: System instructions for the model
            model_hint: Model identifier (e.g., "sonnet", "opus", "gpt-5.4")
            max_thinking_tokens: Max thinking/reasoning tokens

        Returns:
            Response text from the model

        Raises:
            ProviderError: If the provider fails to get a response
        """
        pass
