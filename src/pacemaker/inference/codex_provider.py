"""Codex CLI provider for OpenAI models."""

import subprocess

from .provider import InferenceProvider, ProviderError
from .model_aliases import SHORT_ALIASES
from ..logger import log_debug


class CodexProvider(InferenceProvider):
    """Inference provider using Codex CLI (OpenAI models)."""

    def query(
        self,
        prompt: str,
        system_prompt: str = "",
        model_hint: str = "",
        max_thinking_tokens: int = 4000,
    ) -> str:
        """Query OpenAI model via Codex CLI subprocess."""
        # Normalize legacy aliases (e.g. gpt-5 → gpt-5.4); fall back to o3 default
        model = SHORT_ALIASES.get(model_hint, model_hint) or "o3"

        # Embed system prompt in the prompt text (codex has no --system-prompt flag)
        if system_prompt:
            full_prompt = (
                f"SYSTEM INSTRUCTIONS:\n{system_prompt}\n\nUSER REQUEST:\n{prompt}"
            )
        else:
            full_prompt = prompt

        log_debug(
            "codex_provider",
            f"Calling codex exec -m {model}, prompt_len={len(full_prompt)}",
        )

        try:
            result = subprocess.run(
                ["codex", "exec", "-", "-m", model, "-s", "read-only"],
                input=full_prompt,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            raise ProviderError("Codex CLI timed out after 120s")
        except FileNotFoundError:
            raise ProviderError("Codex CLI not found (not installed)")
        except OSError as e:
            raise ProviderError(f"Codex CLI OS error: {e}")

        if result.returncode != 0:
            stderr_preview = result.stderr[:300] if result.stderr else "no stderr"
            raise ProviderError(
                f"Codex CLI failed (exit {result.returncode}): {stderr_preview}"
            )

        response = result.stdout.strip()
        if not response:
            raise ProviderError("Codex CLI returned empty response")

        log_debug("codex_provider", f"Codex response_len={len(response)}")
        return response
