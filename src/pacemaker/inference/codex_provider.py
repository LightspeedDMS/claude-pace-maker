"""Codex CLI provider for OpenAI models."""

import subprocess

from .provider import InferenceProvider, ProviderError
from .model_aliases import SHORT_ALIASES
from ..logger import log_debug


def _parse_codex_target(model_hint: str) -> tuple:
    """Parse a model hint into (profile, model) for codex invocation.

    Returns:
        (profile, None) when model_hint is a codex-<profile> token — the profile
            name (substring after "codex-") is passed to codex via --profile; no
            -m flag is used so the profile's own model config applies.
        (None, model)   for all other tokens — aliases are resolved and "o3" is
            the fallback when model_hint is empty or unknown.

    Profile existence is NOT validated here; codex CLI rejects unknown profiles
    at runtime with a non-zero exit code → ProviderError → Anthropic fallback.
    """
    if model_hint.startswith("codex-"):
        profile = model_hint[len("codex-") :]
        return (profile, None)
    model = SHORT_ALIASES.get(model_hint, model_hint) or "o3"
    return (None, model)


class CodexProvider(InferenceProvider):
    """Inference provider using Codex CLI (OpenAI models)."""

    def query(
        self,
        prompt: str,
        system_prompt: str = "",
        model_hint: str = "",
        max_thinking_tokens: int = 4000,
    ) -> str:
        """Query OpenAI model via Codex CLI subprocess.

        When model_hint is a codex-<profile> token (e.g. "codex-beast"), the
        CLI is invoked as:
            codex exec - --profile <name> -s read-only
        so the profile's own model/base_url/wire_api config applies.

        For all other tokens, the historical invocation is used:
            codex exec - -m <model> -s read-only
        """
        profile, model = _parse_codex_target(model_hint)

        # Embed system prompt in the prompt text (codex has no --system-prompt flag)
        if system_prompt:
            full_prompt = (
                f"SYSTEM INSTRUCTIONS:\n{system_prompt}\n\nUSER REQUEST:\n{prompt}"
            )
        else:
            full_prompt = prompt

        if profile is not None:
            log_debug(
                "codex_provider",
                f"Calling codex exec --profile {profile}, prompt_len={len(full_prompt)}",
            )
            argv = [
                "codex",
                "exec",
                "--skip-git-repo-check",
                "-",
                "--profile",
                profile,
                "-s",
                "read-only",
            ]
        else:
            log_debug(
                "codex_provider",
                f"Calling codex exec -m {model}, prompt_len={len(full_prompt)}",
            )
            argv = [
                "codex",
                "exec",
                "--skip-git-repo-check",
                "-",
                "-m",
                model,
                "-s",
                "read-only",
            ]

        try:
            result = subprocess.run(
                argv,
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
