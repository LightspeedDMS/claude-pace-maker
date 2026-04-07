"""Competitive multi-model review pipeline for hook inference."""

from concurrent.futures import (
    ThreadPoolExecutor,
    wait as futures_wait,
    TimeoutError as FuturesTimeoutError,
)

from ..logger import log_warning, log_debug
from .registry import get_provider, resolve_model_for_call
from .codex_provider import CodexProvider
from .gemini_provider import GeminiProvider
from .anthropic_provider import AnthropicProvider
from .provider import ProviderError

KNOWN_MODELS = {
    "auto",
    "sonnet",
    "opus",
    "haiku",
    "gpt-5",
    "gemini-flash",
    "gemini-pro",
}
SHORT_ALIASES = {"gem-flash": "gemini-flash", "gem-pro": "gemini-pro"}
_ANTHROPIC_MODELS = {"auto", "sonnet", "opus", "haiku"}

_REVIEWER_CODEX = "codex-gpt5"
_REVIEWER_GEMINI_FLASH = "gem-flash"
_REVIEWER_GEMINI_PRO = "gem-pro"
_REVIEWER_SDK = "anthropic-sdk"

# Named constants for timeouts and token budget
REVIEWER_WAIT_TIMEOUT_SEC = 60  # individual per-reviewer timeout
SYNTHESIS_TIMEOUT_SEC = 30  # synthesis phase timeout
DEFAULT_MAX_THINKING_TOKENS = 4000
MIN_REVIEWERS = 2
MAX_REVIEWERS = 3
MAX_REVIEW_LOG_CHARS = 300  # max chars of reviewer response to log at DEBUG level


def parse_competitive(hook_model: str):
    """Parse competitive expression 'A+B[+C]->synthesizer'.

    Returns (reviewers, synthesizer) tuple or None if not a competitive expression.
    Raises ValueError for malformed expressions.
    """
    if "+" not in hook_model or "->" not in hook_model:
        return None
    parts = hook_model.split("->")
    if len(parts) != 2:
        raise ValueError(
            f"Invalid competitive expression: multiple '->' found in '{hook_model}'"
        )
    lhs, synthesizer = parts[0].strip(), parts[1].strip()
    reviewers = [r.strip() for r in lhs.split("+")]
    reviewers = [SHORT_ALIASES.get(r, r) for r in reviewers]
    synthesizer = SHORT_ALIASES.get(synthesizer, synthesizer)
    for token in reviewers + [synthesizer]:
        if token not in KNOWN_MODELS:
            raise ValueError(f"Invalid model: {token}")
    if len(reviewers) != len(set(reviewers)):
        raise ValueError("Duplicate models in reviewer list not allowed")
    if len(reviewers) < MIN_REVIEWERS:
        raise ValueError(
            f"Competitive mode requires at least {MIN_REVIEWERS} reviewers"
        )
    if len(reviewers) > MAX_REVIEWERS:
        raise ValueError(f"Competitive mode supports at most {MAX_REVIEWERS} reviewers")
    return reviewers, synthesizer


def _build_synthesis_prompt(succeeded: list, original_prompt: str) -> str:
    """Build synthesis prompt from surviving reviewer verdicts."""
    verdicts_block = "\n\n".join(
        f"[{label}]: {response}" for response, label in succeeded
    )
    return (
        "You are a synthesis formatter. Multiple AI reviewers have independently reviewed "
        "the same code/command. Your job is to consolidate their verdicts — not re-judge.\n\n"
        f"Original submission under review:\n{original_prompt}\n\n"
        f"Independent reviewer verdicts:\n{verdicts_block}\n\n"
        "Rules (apply mechanically, do not override):\n"
        "- If ALL reviewers output APPROVED -> output exactly: APPROVED\n"
        "- If ANY reviewer outputs BLOCKED -> output: BLOCKED: [combined reasons from all blocking reviewers, de-duplicated]\n"
        "- Do not add new concerns not raised by reviewers\n"
        "- Do not remove or downgrade concerns raised by reviewers\n"
        '- Output ONLY "APPROVED" or "BLOCKED: [reason]" -- no other text, no preamble'
    )


def _call_single_reviewer(
    model: str,
    prompt: str,
    system_prompt: str,
    call_context: str,
    max_thinking_tokens: int,
) -> tuple:
    """Call one reviewer, return (response, label). Raises ProviderError on failure."""
    provider = get_provider(model)
    model_hint = resolve_model_for_call(model, call_context)
    try:
        response = provider.query(
            prompt, system_prompt, model_hint, max_thinking_tokens
        )
    except ProviderError:
        if isinstance(provider, CodexProvider):
            from .registry import (
                _refresh_codex_usage,
            )  # private helper; local import only

            _refresh_codex_usage()
        raise

    if isinstance(provider, CodexProvider):
        label = _REVIEWER_CODEX
    elif isinstance(provider, GeminiProvider):
        label = (
            _REVIEWER_GEMINI_FLASH if model == "gemini-flash" else _REVIEWER_GEMINI_PRO
        )
    else:
        label = _REVIEWER_SDK
    return response, label


def _dispatch_reviewers(
    reviewers: list,
    prompt: str,
    system_prompt: str,
    call_context: str,
    max_thinking_tokens: int,
) -> list:
    """Dispatch all reviewers in parallel. Returns list of (response, label) for survivors.

    Uses futures_wait() with a bounded timeout so partial results are collected
    even when some reviewers time out. Threads that are already running cannot be
    force-killed; executor.shutdown(wait=False) releases the executor without
    blocking on in-flight threads.
    """
    executor = ThreadPoolExecutor(max_workers=len(reviewers))
    future_to_model = {
        executor.submit(
            _call_single_reviewer,
            model,
            prompt,
            system_prompt,
            call_context,
            max_thinking_tokens,
        ): model
        for model in reviewers
    }

    done, not_done = futures_wait(
        list(future_to_model.keys()), timeout=REVIEWER_WAIT_TIMEOUT_SEC
    )

    for future in not_done:
        future.cancel()
        model = future_to_model[future]
        log_warning(
            "competitive",
            f"Reviewer {model} timed out after {REVIEWER_WAIT_TIMEOUT_SEC}s",
        )

    executor.shutdown(wait=False)

    succeeded = []
    for future in done:
        model = future_to_model[future]
        try:
            result = future.result()
            succeeded.append(result)
            log_debug("competitive", f"Reviewer {model} succeeded")
            response_text, _label = result
            log_debug(
                "competitive",
                f"Reviewer {model} verdict: {response_text[:MAX_REVIEW_LOG_CHARS]!r}",
            )
        except Exception as e:
            log_warning("competitive", f"Reviewer {model} failed: {e}")

    return succeeded


def _sdk_fallback(
    prompt: str,
    system_prompt: str,
    call_context: str,
    max_thinking_tokens: int,
    expression: str,
) -> tuple:
    """Attempt Anthropic SDK solo fallback. Returns (response, label)."""
    log_warning(
        "competitive",
        "All reviewers failed, no Anthropic in competitors — SDK solo fallback",
    )
    try:
        sdk = AnthropicProvider()
        fallback_hint = resolve_model_for_call("auto", call_context)
        resp = sdk.query(prompt, system_prompt, fallback_hint, max_thinking_tokens)
        return resp, "sdk-fallback"
    except Exception as e:
        log_warning("competitive", f"SDK fallback also failed: {e}")
        return "", expression


def _synthesize(
    succeeded: list,
    synthesizer: str,
    prompt: str,
    system_prompt: str,
    call_context: str,
    max_thinking_tokens: int,
    expression: str,
) -> tuple:
    """Call synthesizer with all surviving verdicts. Returns (response, label)."""
    log_debug(
        "competitive",
        f"{len(succeeded)} survivors — calling synthesizer '{synthesizer}'",
    )
    synthesis_prompt = _build_synthesis_prompt(succeeded, prompt)
    synth_executor = ThreadPoolExecutor(max_workers=1)
    try:
        synth_provider = get_provider(synthesizer)
        synth_hint = resolve_model_for_call(synthesizer, call_context)
        future = synth_executor.submit(
            synth_provider.query,
            synthesis_prompt,
            system_prompt,
            synth_hint,
            max_thinking_tokens,
        )
        try:
            synth_response = future.result(timeout=SYNTHESIS_TIMEOUT_SEC)
            log_debug("competitive", f"Synthesis complete, len={len(synth_response)}")
            return synth_response, expression
        except FuturesTimeoutError:
            log_warning(
                "competitive",
                f"Synthesizer '{synthesizer}' timed out after {SYNTHESIS_TIMEOUT_SEC}s"
                " — first survivor wins",
            )
            return succeeded[0]
        except Exception as e:
            log_warning(
                "competitive",
                f"Synthesizer '{synthesizer}' failed: {e} — first survivor wins",
            )
            return succeeded[0]
    finally:
        synth_executor.shutdown(wait=False)


def run_competitive(
    reviewers: list,
    synthesizer: str,
    prompt: str,
    system_prompt: str,
    call_context: str,
    max_thinking_tokens: int = DEFAULT_MAX_THINKING_TOKENS,
) -> tuple:
    """Dispatch reviewers in parallel, synthesize results.

    Returns (response, reviewer_label) tuple.
    """
    expression = "+".join(reviewers) + "->" + synthesizer
    log_debug(
        "competitive",
        f"Dispatching {len(reviewers)} reviewers in parallel: {reviewers}",
    )

    succeeded = _dispatch_reviewers(
        reviewers, prompt, system_prompt, call_context, max_thinking_tokens
    )

    if len(succeeded) == 0:
        if not (set(reviewers) & _ANTHROPIC_MODELS):
            return _sdk_fallback(
                prompt, system_prompt, call_context, max_thinking_tokens, expression
            )
        log_warning(
            "competitive",
            "All reviewers failed (SDK was a competitor) — fail per caller semantics",
        )
        return "", expression

    if len(succeeded) == 1:
        log_debug("competitive", "Single survivor — passing through without synthesis")
        return succeeded[0]

    return _synthesize(
        succeeded,
        synthesizer,
        prompt,
        system_prompt,
        call_context,
        max_thinking_tokens,
        expression,
    )
