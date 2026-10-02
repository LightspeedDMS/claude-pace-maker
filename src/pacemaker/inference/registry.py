"""Provider registry and orchestrator with cross-vendor fallback."""

import time
from typing import Optional

from .provider import ProviderError
from ..logger import log_warning
from ..codex_usage import (
    get_latest_codex_usage,
    write_codex_usage,
    migrate_codex_usage_schema,
)
from ..constants import DEFAULT_DB_PATH

# Issue #152: named constants for the single-model path's deadline-aware
# clamp -- mirrors competitive.py's own budget derivation, but for the
# NON-competitive (single hook_model) path, which previously ignored the
# gate's _deadline entirely. A live replay against real codex-beast
# traffic showed the 120s hardcoded codex subprocess timeout, plus an
# unbounded Anthropic SDK fallback on top of it, regularly exceeding the
# PreToolUse hook's own timeout -- and a killed PreToolUse hook is a
# SILENTLY UNVALIDATED tool call (see CLAUDE.md, "Competitive Review
# Pipeline -> Timeouts").
#
# Reserved off the remaining budget before it's handed to a provider as
# its subprocess/SDK timeout -- leaves slack for the provider's own
# process-teardown overhead and this function's own bookkeeping.
PROVIDER_TIMEOUT_SAFETY_MARGIN_SECONDS = 5.0

# Minimum remaining budget (after the safety margin above) required to
# even ATTEMPT the Anthropic SDK fallback after the primary provider
# fails. Below this floor, the fallback is skipped entirely and the gate
# fails CLOSED via the existing #142 zero-survivor / reviewer-unavailable
# path -- an unbounded fallback call is exactly the failure mode this
# issue exists to close.
MIN_SDK_FALLBACK_BUDGET_SECONDS = 15.0


def _remaining_budget(_deadline: Optional[float], margin: float) -> Optional[float]:
    """Issue #152: wall-clock seconds left before `_deadline`, minus
    `margin`, floored at 0.0 -- or None when there's no deadline at all.

    None is a DELIBERATE no-op, not a bug: callers that have no hook
    deadline (code_reviewer, the intent-declaration check) get
    `timeout=None` and behave exactly as they did before #152. Since #165
    the Stop hook DOES supply a deadline (STOP_REVIEW_BUDGET_SECONDS from
    hook entry), so its provider calls are clamped and an SDK fallback with
    too little time left is skipped. Stop still fails OPEN: an empty result
    from this path never blocks.
    """
    if _deadline is None:
        return None
    return max(0.0, _deadline - time.monotonic() - margin)


# Call context → default model hint when hook_model is "auto"
# These are the historical defaults for each validation context.
_AUTO_DEFAULTS = {
    "stop_hook": "sonnet",  # historical default for the stop-hook validator
    "intent_validation": "sonnet",  # historical default for intent declaration check
    "stage2_unified": "opus",  # historical default for unified stage-2 validation
    "code_review": "sonnet",  # historical default for the code-review validator
}

# Reviewer name constants — identify which provider actually served the request
_REVIEWER_CODEX = "codex-gpt5"
_REVIEWER_GEMINI_FLASH = "gem-flash"
_REVIEWER_GEMINI_PRO = "gem-pro"
_REVIEWER_SDK = "anthropic-sdk"
_REVIEWER_UNKNOWN = "unknown"


def _refresh_codex_usage() -> None:
    """Refresh codex usage DB from session files. Logs and continues on errors."""
    try:
        migrate_codex_usage_schema(DEFAULT_DB_PATH)
        usage = get_latest_codex_usage()
        if usage:
            write_codex_usage(DEFAULT_DB_PATH, usage)
    except Exception as e:
        log_warning("registry", f"Codex usage refresh failed: {e}")


def get_provider(hook_model: str):
    """Get provider instance for the given hook_model config value.

    Args:
        hook_model: Config value - "auto", "sonnet", "opus", "haiku", "gpt-5.4",
                    "gpt-5.4-mini", "gpt-5.5", "gpt-5.6-sol", "gpt-5.6-terra",
                    "gpt-5.6-luna", "gpt-6-astra", "gpt-6-sol"
                    (legacy aliases: "gpt-5", "gpt", "codex"),
                    "gemini-flash", "gemini-pro"

    Returns:
        InferenceProvider instance
    """
    if hook_model in ("auto", "sonnet", "opus", "haiku", "fable"):
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider()
    elif hook_model in (
        "gpt-5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.5",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-6-astra",
        "gpt-6-sol",
        "gpt",
        "codex",
    ):
        from .codex_provider import CodexProvider

        return CodexProvider()
    elif hook_model.startswith("codex-"):
        # codex-<profile> tokens (Story #74): bind a named ~/.codex profile
        from .codex_provider import CodexProvider

        return CodexProvider()
    elif hook_model in ("gemini-flash", "gemini-pro"):
        from .gemini_provider import GeminiProvider

        return GeminiProvider()
    elif hook_model == "agy" or hook_model.startswith("agy-"):
        from .agy_provider import AgyProvider

        return AgyProvider()
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
        hook_model: Config value - any token accepted by
                    model_aliases.is_known_model(), which is the single source
                    of truth: the KNOWN_MODELS set (Anthropic, Codex, Gemini
                    and Antigravity models), the short aliases, or a
                    "codex-<profile>" token. Not re-listed here on purpose —
                    this docstring previously hardcoded the set and silently
                    went stale as tokens were added.
        call_context: Identifies the call site - "stop_hook", "intent_validation",
                      "stage2_unified", "code_review"

    Returns:
        Model hint string for the provider
    """
    if hook_model == "auto":
        return _AUTO_DEFAULTS.get(call_context, "sonnet")
    return hook_model


def resolve_and_call_with_reviewer(
    hook_model: str,
    prompt: str,
    system_prompt: str,
    call_context: str,
    max_thinking_tokens: int = 4000,
    _degradation: Optional[dict] = None,
    _deadline: Optional[float] = None,
) -> tuple:
    """Top-level orchestrator returning (response, reviewer_name) with fallback.

    Fallback chain: selected provider → auto (Anthropic) → fail-open (empty string)
    When the actual failing provider is CodexProvider, refreshes codex usage DB
    from session files before invoking the fallback provider.

    Reviewer identity is determined from the concrete provider instance actually
    used (not from the hook_model string) to ensure correctness when unknown
    hook_model values fall back to Anthropic.

    Args:
        hook_model: Config value for hook inference model
        prompt: The validation/review prompt
        system_prompt: System instructions for the model
        call_context: Call site identifier for model resolution
        max_thinking_tokens: Max thinking tokens for the model
        _degradation: optional out-param dict (same idiom as _diagnostics/
            _outcome in transcript_reader.py). When provided, populated with
            {"degraded": False} normally, or {"degraded": True,
            "failed_providers": {model: reason}, "context": "..."} when the
            review actually served was degraded — either a competitive
            verifier failed to respond (context="competitive", forwarded
            from run_mechanical()) or the single configured provider failed
            and the Anthropic SDK fallback served instead
            (context="single_model_fallback"). Issue #131. Never raises;
            None (default) is a no-op for existing callers. Issue #142:
            when the response is empty because NOBODY answered at all
            (competitive zero survivors, forwarded from run_mechanical();
            or the single provider AND its Anthropic fallback both failed;
            or hook_model="auto" failed outright), the dict additionally
            carries {"zero_survivors": True} — callers use this to build a
            real explanation instead of relaying a blank response as if it
            were reviewer feedback.

    Returns:
        Tuple of (response_text, reviewer_name) where reviewer_name identifies
        the provider that actually served the request:
        - "codex-gpt5" for Codex CLI
        - "gem-flash" for Gemini Flash CLI
        - "gem-pro" for Gemini Pro CLI
        - "anthropic-sdk" for Anthropic SDK
        - "unknown" on complete failure (fail-open)
    """
    if _degradation is not None:
        _degradation.clear()
        _degradation["degraded"] = False

    # Competitive mode detection — must be checked before single-model path
    if "+" in hook_model:
        from .competitive import parse_competitive, run_mechanical

        try:
            parsed = parse_competitive(hook_model)
        except ValueError as e:
            log_warning(
                "registry",
                f"Invalid competitive expression '{hook_model}': {e}, falling back to Anthropic auto",
            )
            parsed = None
        if parsed is None:
            log_warning(
                "registry",
                f"hook_model '{hook_model}' contains '+' but is not valid competitive expression, "
                "falling back to Anthropic auto",
            )
            hook_model = "auto"
        else:
            verifiers, synthesizer = parsed
            return run_mechanical(
                verifiers,
                synthesizer,
                prompt,
                system_prompt,
                call_context,
                max_thinking_tokens,
                _degradation=_degradation,
                _deadline=_deadline,
            )

    from .codex_provider import CodexProvider
    from .gemini_provider import GeminiProvider
    from .agy_provider import AgyProvider

    provider = get_provider(hook_model)
    model_hint = resolve_model_for_call(hook_model, call_context)
    is_codex_provider = isinstance(provider, CodexProvider)
    is_gemini_provider = isinstance(provider, GeminiProvider)
    is_agy_provider = isinstance(provider, AgyProvider)

    _provider_timeout = _remaining_budget(
        _deadline, PROVIDER_TIMEOUT_SAFETY_MARGIN_SECONDS
    )
    try:
        response = provider.query(
            prompt,
            system_prompt,
            model_hint,
            max_thinking_tokens,
            timeout=_provider_timeout,
        )
        if is_codex_provider:
            # codex-<profile> tokens return the verbatim hook_model as reviewer
            # (e.g. "codex-beast"); plain codex/gpt-5.5 map to "codex-gpt5"
            if hook_model.startswith("codex-"):
                reviewer = hook_model
            else:
                reviewer = _REVIEWER_CODEX
        elif is_gemini_provider:
            reviewer = (
                _REVIEWER_GEMINI_FLASH
                if hook_model == "gemini-flash"
                else _REVIEWER_GEMINI_PRO
            )
        elif is_agy_provider:
            reviewer = hook_model  # preserve the full alias (e.g. "agy-flash-high")
        else:
            reviewer = _REVIEWER_SDK
        return response, reviewer
    except ProviderError as e:
        if hook_model != "auto":
            log_warning(
                "registry",
                f"Primary provider failed for hook_model='{hook_model}' ({e}), "
                "falling back to auto (Anthropic)",
            )

            # Refresh codex usage from session files when the actual Codex provider fails
            if is_codex_provider:
                _refresh_codex_usage()

            from .anthropic_provider import AnthropicProvider

            fallback_provider = AnthropicProvider()
            fallback_hint = resolve_model_for_call("auto", call_context)

            _fallback_remaining = _remaining_budget(
                _deadline, PROVIDER_TIMEOUT_SAFETY_MARGIN_SECONDS
            )
            if (
                _fallback_remaining is not None
                and _fallback_remaining < MIN_SDK_FALLBACK_BUDGET_SECONDS
            ):
                # Issue #152: not enough budget left to even attempt the
                # Anthropic SDK fallback -- an unbounded fallback call after
                # a primary-provider timeout is exactly the failure mode
                # this issue exists to close (a live replay showed a
                # codex-beast timeout + fallback taking 133.6s against a
                # 120s hook budget). Fail CLOSED via the existing #142
                # zero-survivors / reviewer-unavailable path instead of
                # attempting a call that can't finish in time.
                log_warning(
                    "registry",
                    f"Insufficient remaining budget ({_fallback_remaining:.1f}s) "
                    f"to attempt Anthropic SDK fallback after hook_model="
                    f"'{hook_model}' failed ({e}); failing closed",
                )
                if _degradation is not None:
                    _degradation["degraded"] = True
                    _degradation["zero_survivors"] = True
                    _degradation["failed_providers"] = {hook_model: str(e)}
                    _degradation["context"] = "single_model_fallback"
                return "", _REVIEWER_UNKNOWN

            try:
                response = fallback_provider.query(
                    prompt,
                    system_prompt,
                    fallback_hint,
                    max_thinking_tokens,
                    timeout=_fallback_remaining,
                )
                # Issue #131: the fallback succeeded, so the request WAS
                # served — but by anthropic-sdk instead of the configured
                # provider. That is a degraded review and must be visible.
                if _degradation is not None:
                    _degradation["degraded"] = True
                    _degradation["failed_providers"] = {hook_model: str(e)}
                    _degradation["context"] = "single_model_fallback"
                return response, _REVIEWER_SDK
            except ProviderError as e2:
                log_warning("registry", f"Fallback also failed ({e2}), fail-open")
                # Issue #142: BOTH the configured provider and the
                # anthropic-sdk fallback failed — nobody answered at all.
                # Surface why, same idiom as the degraded-approval branch
                # above, so callers never have to relay a blank response
                # without an explanation.
                if _degradation is not None:
                    _degradation["degraded"] = True
                    _degradation["zero_survivors"] = True
                    # Issue #142 code-review follow-up (item 6): key by the
                    # provider identity that actually failed
                    # (_REVIEWER_SDK, "anthropic-sdk") — "auto" is a
                    # model-resolution alias, not a reviewer identity, and
                    # would be misleading next to hook_model's real
                    # provider-token key.
                    _degradation["failed_providers"] = {
                        hook_model: str(e),
                        _REVIEWER_SDK: str(e2),
                    }
                    _degradation["context"] = "single_model_fallback"
                return "", _REVIEWER_UNKNOWN
        else:
            log_warning("registry", f"Anthropic failed ({e}), fail-open")
            # Issue #142: hook_model == "auto" (no fallback attempted) and
            # the single Anthropic call itself failed — same "nobody
            # answered" signal as the competitive zero-survivors path.
            if _degradation is not None:
                _degradation["degraded"] = True
                _degradation["zero_survivors"] = True
                _degradation["failed_providers"] = {hook_model: str(e)}
                _degradation["context"] = "single_model_fallback"
            return "", _REVIEWER_UNKNOWN


def resolve_and_call(
    hook_model: str,
    prompt: str,
    system_prompt: str,
    call_context: str,
    max_thinking_tokens: int = 4000,
    _deadline: Optional[float] = None,
) -> str:
    """Top-level orchestrator: call provider with cross-vendor fallback.

    Fallback chain: selected provider → auto (Anthropic) → fail-open (empty string)

    Args:
        hook_model: Config value for hook inference model
        prompt: The validation/review prompt
        system_prompt: System instructions for the model
        call_context: Call site identifier for model resolution
        max_thinking_tokens: Max thinking tokens for the model
        _deadline: Issue #165. Absolute ``time.monotonic()`` deadline. The Stop
            hook supplies one (its review must finish before the harness kills
            the hook); it clamps the provider calls and skips an SDK fallback
            with too little time left, exactly as for the PreToolUse gate.
            An empty result is still a fail-open for the caller. ``None`` (the
            other callers) means no deadline.

    Returns:
        Model response text, or empty string on complete failure (fail-open)
    """
    response, _ = resolve_and_call_with_reviewer(
        hook_model,
        prompt,
        system_prompt,
        call_context,
        max_thinking_tokens,
        _deadline=_deadline,
    )
    return response
