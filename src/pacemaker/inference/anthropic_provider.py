"""Anthropic provider using Claude Agent SDK."""

import os
import re
import asyncio
import contextlib
from typing import Optional

from .provider import InferenceProvider, ProviderError
from ..logger import log_debug, log_warning


# Known short aliases — passed straight through to the SDK so it resolves each
# family to its latest model version.  No pinned IDs here; pinning is the SDK's job.
_KNOWN_ALIASES = ("sonnet", "opus", "haiku", "fable")
_DEFAULT_MODEL = "sonnet"

# Fallback pairs (alias keys): if one family hits a usage limit, try the other.
_FALLBACK_MAP = {
    "sonnet": "opus",
    "opus": "sonnet",
    "fable": "opus",
}

# Thinking-effort level applied to every Anthropic SDK call.
# SDK supports "low" | "medium" | "high" | "max"; "high" per project requirement.
_EFFORT_LEVEL = "high"


@contextlib.contextmanager
def _clean_sdk_env():
    """Temporarily remove CLAUDECODE env var to prevent nested session error."""
    removed = {}
    for key in ("CLAUDECODE",):
        if key in os.environ:
            removed[key] = os.environ.pop(key)
    try:
        yield
    finally:
        os.environ.update(removed)


def _is_limit_error(response: str) -> bool:
    """Check if response indicates usage limit error."""
    if not response:
        return False
    lower = response.lower()
    return "usage limit" in lower or "limit reached" in lower or "resets" in lower


# Issue #165: the SDK error detail that goes into a ProviderError reason is
# capped, and credential-looking text is redacted first, because the reason
# flows into blockage details (usage.db) and governance feedback shown to
# Claude. It cannot know the user's stored secrets (that machinery needs a DB
# path); it only removes what looks like a credential. An SDK error is a
# CLI/process/API message and does not carry the prompt.
_SDK_ERROR_DETAIL_MAX_CHARS = 300
_CREDENTIAL_PAIR_RE = re.compile(
    r"\b(?:api[_-]?key|token|secret|password|passwd|authorization|bearer)\b"
    r"\s*[:=]?\s*(?:bearer\s+)?[^\s,;]+",
    re.IGNORECASE,
)
_KEY_LIKE_TOKEN_RE = re.compile(r"\b(?=[A-Za-z_\-]*\d)[A-Za-z0-9_\-]{24,}\b")


def _describe_sdk_error(exc: Exception) -> str:
    """'TypeName: message' for an SDK exception: credential-looking text
    redacted, whitespace collapsed to one line, message capped."""
    text = _CREDENTIAL_PAIR_RE.sub("[redacted]", str(exc))
    text = _KEY_LIKE_TOKEN_RE.sub("[redacted]", text)
    text = " ".join(text.split())[:_SDK_ERROR_DETAIL_MAX_CHARS]
    return f"{type(exc).__name__}: {text}"


def _build_options(
    options_cls,
    model: str,
    system_prompt: str,
    max_thinking_tokens: int,
):
    """Build ClaudeAgentOptions for a reviewer call, fully isolated from the user's
    own Claude Code session (issue #147).

    Without isolation, the nested `claude` process spawned by the SDK loads the
    user's own settings — hooks (including pace-maker's own), plugins, and MCP
    servers (including unreachable ones) — turning a ~3-5s reviewer call into a
    20-49s one and causing reviewer timeouts. `setting_sources=[]` skips loading
    user/project/local settings (which is also what keeps plugins from loading —
    plugins are configured via settings, and `plugins` itself already defaults to
    an empty list on ClaudeAgentOptions, so no separate flag is needed for that).
    `mcp_servers={}` + `strict_mcp_config=True` together guarantee no MCP server
    is loaded from any other source either.

    Used for BOTH the primary call and the limit-error fallback call so the two
    constructions cannot diverge (single source of truth for isolation).
    """
    return options_cls(
        max_turns=1,
        model=model,
        effort=_EFFORT_LEVEL,
        max_thinking_tokens=max(max_thinking_tokens, 1024),
        system_prompt=system_prompt or "You are a helpful assistant.",
        disallowed_tools=[
            "Write",
            "Edit",
            "Bash",
            "TodoWrite",
            "Read",
            "Grep",
            "Glob",
        ],
        setting_sources=[],
        strict_mcp_config=True,
        mcp_servers={},
    )


def _resolve_model(model_hint: str) -> str:
    """Resolve model hint to the string passed to the Claude Agent SDK.

    Known short aliases (sonnet/opus/haiku) are passed through unchanged so the
    SDK resolves each family to its current latest model — no version pinning here.
    Explicit claude-* strings are also returned as-is (escape hatch for callers that
    need a specific version).  Empty or unrecognised hints default to _DEFAULT_MODEL.
    """
    if model_hint in _KNOWN_ALIASES:
        return model_hint
    if model_hint.startswith("claude-"):
        return model_hint
    return _DEFAULT_MODEL


class AnthropicProvider(InferenceProvider):
    """Inference provider using Claude Agent SDK."""

    def query(
        self,
        prompt: str,
        system_prompt: str = "",
        model_hint: str = "",
        max_thinking_tokens: int = 4000,
        timeout: Optional[float] = None,
    ) -> str:
        """Query Anthropic model via Claude Agent SDK.

        Issue #152: `timeout`, when supplied, wraps the WHOLE async call
        (including any internal limit-error fallback-model retry -- see
        _query_async) in ``asyncio.wait_for``, so the caller's remaining
        deadline budget bounds the SDK path the same way it bounds the
        CLI providers' subprocess timeout. ``None`` (the default)
        preserves the pre-#152 unbounded-wait behavior exactly.
        """
        loop = asyncio.new_event_loop()
        try:
            coro = self._query_async(
                prompt, system_prompt, model_hint, max_thinking_tokens
            )
            if timeout is not None:
                coro = asyncio.wait_for(coro, timeout=timeout)
            try:
                return loop.run_until_complete(coro)
            except asyncio.TimeoutError:
                raise ProviderError(f"Anthropic SDK query timed out after {timeout}s")
        finally:
            loop.close()

    async def _query_async(
        self, prompt: str, system_prompt: str, model_hint: str, max_thinking_tokens: int
    ) -> str:
        """Async implementation of query."""
        try:
            from claude_agent_sdk import query as fresh_query
            from claude_agent_sdk.types import (
                ClaudeAgentOptions as FreshOptions,
                ResultMessage as FreshResult,
            )
        except ImportError:
            raise ProviderError("Claude Agent SDK not available")

        model = _resolve_model(model_hint)
        log_debug(
            "anthropic_provider", f"Querying model={model}, prompt_len={len(prompt)}"
        )

        options = _build_options(
            FreshOptions, model, system_prompt, max_thinking_tokens
        )

        response_text = ""
        last_error: Optional[Exception] = None
        try:
            with _clean_sdk_env():
                async for message in fresh_query(prompt=prompt, options=options):
                    if isinstance(message, FreshResult):
                        if hasattr(message, "result") and message.result:
                            response_text = message.result.strip()
        except Exception as e:
            last_error = e
            log_warning(
                "anthropic_provider",
                f"SDK call exception (model={model}): {_describe_sdk_error(e)}",
            )

        # Check for limit error and try fallback
        if _is_limit_error(response_text):
            fallback_model = _FALLBACK_MAP.get(model)
            if fallback_model:
                log_debug(
                    "anthropic_provider",
                    f"Limit error, trying fallback model={fallback_model}",
                )
                options_fb = _build_options(
                    FreshOptions, fallback_model, system_prompt, max_thinking_tokens
                )
                response_text = ""
                last_error = None
                try:
                    with _clean_sdk_env():
                        async for message in fresh_query(
                            prompt=prompt, options=options_fb
                        ):
                            if isinstance(message, FreshResult):
                                if hasattr(message, "result") and message.result:
                                    response_text = message.result.strip()
                except Exception as e:
                    last_error = e
                    log_warning(
                        "anthropic_provider",
                        f"Fallback SDK call exception (model={fallback_model}): "
                        f"{_describe_sdk_error(e)}",
                    )

                if _is_limit_error(response_text):
                    raise ProviderError(
                        f"Both {model} and {fallback_model} hit usage limits"
                    )

        if not response_text:
            if last_error is not None:
                raise ProviderError(
                    f"Empty response from {model} "
                    f"(SDK error: {_describe_sdk_error(last_error)})"
                )
            raise ProviderError(f"Empty response from {model}")

        return response_text
