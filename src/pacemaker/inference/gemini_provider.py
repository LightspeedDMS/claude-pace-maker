"""Gemini CLI provider for Google Gemini models (gemini-flash, gemini-pro)."""

import subprocess

from .provider import InferenceProvider, ProviderError
from ..logger import log_debug

# Map friendly model hints to actual Gemini model identifiers
_MODEL_MAP = {
    "gemini-flash": "gemini-2.5-flash",
    "gemini-pro": "gemini-2.5-pro",
}

_DEFAULT_MODEL = "gemini-2.5-flash"
_CLI_TIMEOUT_SEC = 120
_STDERR_PREVIEW_CHARS = 300
_DEFAULT_MAX_THINKING_TOKENS = 4000


class GeminiProvider(InferenceProvider):
    """Inference provider using Gemini CLI (Google Gemini models)."""

    def query(
        self,
        prompt: str,
        system_prompt: str = "",
        model_hint: str = "",
        max_thinking_tokens: int = _DEFAULT_MAX_THINKING_TOKENS,
    ) -> str:
        """Query Gemini model via Gemini CLI subprocess."""
        model = _MODEL_MAP.get(model_hint, model_hint) or _DEFAULT_MODEL

        # Embed system prompt in the prompt text (gemini CLI uses -p flag for prompt)
        if system_prompt:
            full_prompt = (
                f"SYSTEM INSTRUCTIONS:\n{system_prompt}\n\nUSER REQUEST:\n{prompt}"
            )
        else:
            full_prompt = prompt

        log_debug(
            "gemini_provider",
            f"Calling gemini -m {model}, prompt_len={len(full_prompt)}",
        )

        try:
            result = subprocess.run(
                ["gemini", "-m", model],
                input=full_prompt,
                capture_output=True,
                text=True,
                timeout=_CLI_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            raise ProviderError(f"Gemini CLI timed out after {_CLI_TIMEOUT_SEC}s")
        except FileNotFoundError:
            raise ProviderError("Gemini CLI not found (not installed)")
        except OSError as e:
            raise ProviderError(f"Gemini CLI OS error: {e}")

        if result.returncode != 0:
            stderr_preview = (
                result.stderr[:_STDERR_PREVIEW_CHARS] if result.stderr else "no stderr"
            )
            raise ProviderError(
                f"Gemini CLI failed (exit {result.returncode}): {stderr_preview}"
            )

        response = result.stdout.strip()
        if not response:
            raise ProviderError("Gemini CLI returned empty response")

        log_debug("gemini_provider", f"Gemini response_len={len(response)}")
        return response
