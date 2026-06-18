"""Antigravity CLI (agy) provider for multi-model inference (Story #72).

Supports Gemini Flash/Pro thinking modes, GPT-OSS, and Claude via agy CLI.
"""

import subprocess

from .provider import InferenceProvider, ProviderError
from ..logger import log_debug

# Map pace-maker model aliases to agy --model argument strings.
# None means bare 'agy' with no --model flag (agy's own default).
_MODEL_MAP = {
    "agy": None,  # no --model flag
    "agy-flash": "Gemini 3.5 Flash (Medium)",
    "agy-flash-low": "Gemini 3.5 Flash (Low)",
    "agy-flash-medium": "Gemini 3.5 Flash (Medium)",
    "agy-flash-high": "Gemini 3.5 Flash (High)",
    "agy-pro": "Gemini 3.1 Pro (High)",
    "agy-pro-low": "Gemini 3.1 Pro (Low)",
    "agy-pro-high": "Gemini 3.1 Pro (High)",
    "agy-gpt-oss": "GPT-OSS 120B (Medium)",
    "agy-sonnet": "Claude Sonnet 4.6 (Thinking)",
    "agy-opus": "Claude Opus 4.6 (Thinking)",
}

_DEFAULT_MODEL_ARG = "Gemini 3.5 Flash (Medium)"
_CLI_TIMEOUT_SEC = 120
_STDERR_PREVIEW_CHARS = 300


class AgyProvider(InferenceProvider):
    """Inference provider using Antigravity CLI (agy)."""

    def query(
        self,
        prompt: str,
        system_prompt: str = "",
        model_hint: str = "",
        max_thinking_tokens: int = 4000,
    ) -> str:
        """Query agy CLI with the given prompt and model hint.

        Args:
            prompt: The validation/review prompt text.
            system_prompt: Optional system instructions to embed in the prompt.
            model_hint: pace-maker alias (e.g. "agy-flash-high"). Falls back
                        to _DEFAULT_MODEL_ARG for unknown hints.
            max_thinking_tokens: Unused by agy CLI (kept for interface parity).

        Returns:
            Stripped response text from agy CLI stdout.

        Raises:
            ProviderError: On timeout, CLI not found, OS error, non-zero exit,
                           or empty response.
        """
        # Resolve model arg: None for bare "agy", fallback for unknown hints
        if model_hint in _MODEL_MAP:
            model_arg = _MODEL_MAP[model_hint]
        else:
            model_arg = _DEFAULT_MODEL_ARG

        # Embed system prompt in the prompt text (agy uses --print flag for prompt)
        if system_prompt:
            full_prompt = (
                f"SYSTEM INSTRUCTIONS:\n{system_prompt}\n\nUSER REQUEST:\n{prompt}"
            )
        else:
            full_prompt = prompt

        if model_arg is None:
            cmd = ["agy", "--print", full_prompt]
        else:
            cmd = ["agy", "--print", full_prompt, "--model", model_arg]

        log_debug(
            "agy_provider",
            f"Calling agy model_hint={model_hint!r} model_arg={model_arg!r} "
            f"prompt_len={len(full_prompt)}",
        )

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_CLI_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            raise ProviderError(f"agy CLI timed out after {_CLI_TIMEOUT_SEC}s")
        except FileNotFoundError:
            raise ProviderError("agy CLI not found (not installed)")
        except OSError as e:
            raise ProviderError(f"agy CLI OS error: {e}")

        if result.returncode != 0:
            stderr_preview = (
                result.stderr[:_STDERR_PREVIEW_CHARS] if result.stderr else "no stderr"
            )
            raise ProviderError(
                f"agy CLI failed (exit {result.returncode}): {stderr_preview}"
            )

        response = result.stdout.strip()
        if not response:
            raise ProviderError("agy CLI returned empty response")

        log_debug("agy_provider", f"agy response_len={len(response)}")
        return response
