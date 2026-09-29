"""Codex CLI provider for OpenAI models."""

import subprocess
from typing import Optional

from .provider import InferenceProvider, ProviderError
from .model_aliases import SHORT_ALIASES
from ..logger import log_debug

# Max chars of stderr to include in a ProviderError message (both failure
# branches below use this — keeps them symmetric, see bug #132).
_STDERR_PREVIEW_CHARS = 300

# Issue #152: the provider's own known-safe subprocess timeout ceiling. A
# caller-supplied `timeout` (issue #152's deadline-aware clamp) can only
# SHRINK this, never raise it.
_DEFAULT_CLI_TIMEOUT_SEC = 120

# Issue #132 follow-up: codex-beast prints harmless diagnostic lines on
# EVERY run before any real error, e.g. a models-cache refresh notice and
# an oauth token-refresh log line. These are benign noise, but the
# original stderr_preview took only the FIRST 300 chars, so on a real live
# empty-response failure these two lines alone consumed the entire preview
# budget and hid the actual reason. Filtered out by substring match before
# taking the LAST 300 chars of what remains (a real error is more likely to
# be the last thing printed).
_BENIGN_STDERR_LINE_PATTERNS = (
    "codex_models_manager::manager: failed to refresh available models",
    "codex_rmcp_client::oauth::refresh_transaction",
)


def _filter_benign_stderr_lines(stderr: str) -> str:
    """Issue #132 follow-up: drop lines matching a known-benign pattern,
    preserving line order and everything else verbatim."""
    lines = stderr.splitlines()
    kept = [
        line
        for line in lines
        if not any(pattern in line for pattern in _BENIGN_STDERR_LINE_PATTERNS)
    ]
    return "\n".join(kept)


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
        timeout: Optional[float] = None,
    ) -> str:
        """Query OpenAI model via Codex CLI subprocess.

        When model_hint is a codex-<profile> token (e.g. "codex-beast"), the
        CLI is invoked as:
            codex exec - --profile <name> -s read-only
        so the profile's own model/base_url/wire_api config applies.

        For all other tokens, the historical invocation is used:
            codex exec - -m <model> -s read-only

        Issue #152: `timeout`, when supplied, clamps the subprocess timeout
        to ``max(0.0, min(_DEFAULT_CLI_TIMEOUT_SEC, timeout))`` -- never
        raises it above the provider's own known-safe ceiling (only ever
        shrinks it toward the caller's remaining deadline budget), and
        never lets a negative value reach ``subprocess.run`` (defensive
        floor). ``None`` (the default) preserves the pre-#152 hardcoded
        120s timeout exactly.
        """
        profile, model = _parse_codex_target(model_hint)
        argv, full_prompt = self._build_argv(profile, model, system_prompt, prompt)
        effective_timeout = self._clamp_timeout(timeout)

        try:
            result = subprocess.run(
                argv,
                input=full_prompt,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )
        except subprocess.TimeoutExpired:
            raise ProviderError(f"Codex CLI timed out after {effective_timeout}s")
        except FileNotFoundError:
            raise ProviderError("Codex CLI not found (not installed)")
        except OSError as e:
            raise ProviderError(f"Codex CLI OS error: {e}")

        return self._parse_result(result)

    @staticmethod
    def _clamp_timeout(timeout: Optional[float]) -> float:
        """Issue #152: shrink-only clamp, floored at 0.0."""
        if timeout is None:
            return _DEFAULT_CLI_TIMEOUT_SEC
        return max(0.0, min(_DEFAULT_CLI_TIMEOUT_SEC, timeout))

    @staticmethod
    def _build_argv(
        profile: Optional[str], model: Optional[str], system_prompt: str, prompt: str
    ) -> "tuple[list, str]":
        """Build the codex CLI argv and the (system-prompt-embedded) input text."""
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
        return argv, full_prompt

    @staticmethod
    def _parse_result(result) -> str:
        """Raise ProviderError on non-zero exit or empty stdout; otherwise
        return the stripped response. Issue #132 follow-up: benign noise
        lines (models-cache refresh, oauth token refresh -- printed on
        EVERY run) are filtered out FIRST, then the LAST
        _STDERR_PREVIEW_CHARS of what remains is kept -- a real error is
        more likely to be the last thing printed, and the old FIRST-300-
        chars slice let two benign lines alone consume the whole preview
        budget and hide the actual reason (bug #132)."""
        _filtered_stderr = (
            _filter_benign_stderr_lines(result.stderr) if result.stderr else ""
        )
        stderr_preview = (
            _filtered_stderr[-_STDERR_PREVIEW_CHARS:]
            if _filtered_stderr
            else "no stderr"
        )

        if result.returncode != 0:
            raise ProviderError(
                f"Codex CLI failed (exit {result.returncode}): {stderr_preview}"
            )

        response = result.stdout.strip()
        if not response:
            # Bug #132: symmetric with the non-zero-exit branch above.
            # Includes returncode too, so an exit-0 empty response (the
            # observed failure mode) is distinguishable from other causes.
            raise ProviderError(
                f"Codex CLI returned empty response (exit {result.returncode}): "
                f"{stderr_preview}"
            )

        log_debug("codex_provider", f"Codex response_len={len(response)}")
        return response
