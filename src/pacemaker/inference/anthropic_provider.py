"""Anthropic provider using Claude Agent SDK."""

import os
import asyncio
import contextlib

from .provider import InferenceProvider, ProviderError
from ..logger import log_debug


# Model name mapping
_MODEL_MAP = {
    "sonnet": "claude-sonnet-4-5",
    "opus": "claude-opus-4-5",
    "haiku": "claude-haiku-4-5-20251001",
}

# Fallback pairs: if one hits limit, try the other
_FALLBACK_MAP = {
    "claude-sonnet-4-5": "claude-opus-4-5",
    "claude-opus-4-5": "claude-sonnet-4-5",
}


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


def _resolve_model(model_hint: str) -> str:
    """Resolve model hint to full Anthropic model name."""
    if model_hint in _MODEL_MAP:
        return _MODEL_MAP[model_hint]
    if model_hint.startswith("claude-"):
        return model_hint
    return _MODEL_MAP.get("sonnet", "claude-sonnet-4-5")


class AnthropicProvider(InferenceProvider):
    """Inference provider using Claude Agent SDK."""

    def query(
        self,
        prompt: str,
        system_prompt: str = "",
        model_hint: str = "",
        max_thinking_tokens: int = 4000,
    ) -> str:
        """Query Anthropic model via Claude Agent SDK."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                self._query_async(
                    prompt, system_prompt, model_hint, max_thinking_tokens
                )
            )
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

        options = FreshOptions(
            max_turns=1,
            model=model,
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
        )

        response_text = ""
        try:
            with _clean_sdk_env():
                async for message in fresh_query(prompt=prompt, options=options):
                    if isinstance(message, FreshResult):
                        if hasattr(message, "result") and message.result:
                            response_text = message.result.strip()
        except Exception as e:
            log_debug("anthropic_provider", f"SDK call exception: {e}")

        # Check for limit error and try fallback
        if _is_limit_error(response_text):
            fallback_model = _FALLBACK_MAP.get(model)
            if fallback_model:
                log_debug(
                    "anthropic_provider",
                    f"Limit error, trying fallback model={fallback_model}",
                )
                options_fb = FreshOptions(
                    max_turns=1,
                    model=fallback_model,
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
                )
                response_text = ""
                try:
                    with _clean_sdk_env():
                        async for message in fresh_query(
                            prompt=prompt, options=options_fb
                        ):
                            if isinstance(message, FreshResult):
                                if hasattr(message, "result") and message.result:
                                    response_text = message.result.strip()
                except Exception as e:
                    log_debug("anthropic_provider", f"Fallback SDK call exception: {e}")

                if _is_limit_error(response_text):
                    raise ProviderError(
                        f"Both {model} and {fallback_model} hit usage limits"
                    )

        if not response_text:
            raise ProviderError(f"Empty response from {model}")

        return response_text
