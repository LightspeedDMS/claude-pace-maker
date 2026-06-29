"""Competitive multi-model review pipeline for hook inference."""

from concurrent.futures import (
    ThreadPoolExecutor,
    wait as futures_wait,
)

from ..logger import log_warning, log_debug
from .registry import get_provider, resolve_model_for_call
from .codex_provider import CodexProvider
from .gemini_provider import GeminiProvider
from .agy_provider import AgyProvider
from .provider import ProviderError
from .model_aliases import SHORT_ALIASES, is_known_model
from .verdict import verdict_passes_for_context

_ANTHROPIC_MODELS = {"auto", "sonnet", "opus", "haiku", "fable"}

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
        if not is_known_model(token):
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
        # codex-<profile> tokens use the verbatim token as reviewer label;
        # plain codex model tokens (gpt-5.5, gpt-5.4, etc.) keep the legacy label.
        label = model if model.startswith("codex-") else _REVIEWER_CODEX
    elif isinstance(provider, GeminiProvider):
        label = (
            _REVIEWER_GEMINI_FLASH if model == "gemini-flash" else _REVIEWER_GEMINI_PRO
        )
    elif isinstance(provider, AgyProvider):
        # AgyProvider uses the verbatim model alias as label (e.g. "agy-flash-high")
        label = model
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


def _format_failure_message(
    failing: list,
    synthesizer: str,
    original_prompt: str,
    system_prompt: str,
    call_context: str,
    max_thinking_tokens: int,
) -> str:
    """Call synthesizer to format a combined message from 2+ failing verifier verdicts.

    Synthesizer is a MESSAGE-ONLY formatter — the caller applies the 'BLOCKED:' prefix.
    On synthesizer error/timeout/empty: falls back to concatenated raw feedbacks.
    The synthesizer can NEVER influence the pass/fail decision — only the message body.
    """
    # Load externalized synthesis prompt (Messi Rule 11 — externalize prompts)
    try:
        from ..prompt_loader import PromptLoader

        loader = PromptLoader()
        template = loader.load_prompt(
            "mechanical_failure_synthesis.md", subfolder="common"
        )
    except Exception as e:
        log_warning(
            "competitive",
            f"Failed to load synthesis prompt template: {e} — using inline fallback",
        )
        template = (
            "Merge these failing reviews into ONE concise user-facing message. "
            "You are a FORMATTER, not a judge. Do NOT decide. "
            "Do NOT output APPROVED, BLOCKED, or COMPLETE. Just merge the concerns."
        )

    failing_block = "\n\n".join(f"[{label}]: {resp}" for resp, label in failing)
    synthesis_prompt = (
        f"{template}\n\n"
        f"Failing reviewer verdicts to merge:\n{failing_block}\n\n"
        f"Original submission under review:\n{original_prompt}"
    )

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        synth_provider = get_provider(synthesizer)
        synth_hint = resolve_model_for_call(synthesizer, call_context)
        future = executor.submit(
            synth_provider.query,
            synthesis_prompt,
            system_prompt,
            synth_hint,
            max_thinking_tokens,
        )
        try:
            result = future.result(timeout=SYNTHESIS_TIMEOUT_SEC)
            if not result:
                raise ValueError("Synthesizer returned empty response")
            log_debug("competitive", f"Synthesis complete, len={len(result)}")
            return result
        except Exception as e:
            log_warning(
                "competitive",
                f"Synthesizer '{synthesizer}' failed: {e} — concatenating raw feedbacks",
            )
            return "\n".join(f"[{label}]: {resp}" for resp, label in failing)
    finally:
        executor.shutdown(wait=False)


def run_mechanical(
    verifiers: list,
    synthesizer: str,
    prompt: str,
    system_prompt: str,
    call_context: str,
    max_thinking_tokens: int = DEFAULT_MAX_THINKING_TOKENS,
) -> tuple:
    """N-verifier mechanical decision engine with message-only synthesizer.

    Decision is computed in code (PASS iff all verifiers PASS for the gate type).
    Synthesizer is demoted to message-only formatter — it can NEVER flip the verdict.
    'BLOCKED:' prefix is applied mechanically by this function regardless of what
    the synthesizer returns, closing the false-allow-via-encoded-message trap.

    Pre-tool gate (call_context != 'stop_hook'):
    - ALL verifiers must respond AND pass → APPROVED (fail-closed)
    - Missing verifier (infra failure) → BLOCKED
    - Zero survivors → '' (verdict_passes('') = False → gate blocks)

    Stop gate (call_context == 'stop_hook'):
    - All present survivors must pass → APPROVED (missing verifiers ignored)
    - Any non-passing survivor → BLOCKED
    - Zero survivors → '' (parse_sdk_response('') → fail-open, avoids infinite stop loop)

    Returns (response, reviewer_label) tuple.
    """
    expression = "+".join(verifiers) + "->" + synthesizer
    log_debug(
        "competitive",
        f"Dispatching {len(verifiers)} verifiers in parallel: {verifiers}",
    )

    survivors = _dispatch_reviewers(
        verifiers, prompt, system_prompt, call_context, max_thinking_tokens
    )

    # Zero survivors: return empty string and let gate semantics handle it.
    # Stop gate: parse_sdk_response('') → {"continue": True} — fail-open (avoids infinite loop)
    # Pre-tool gate: verdict_passes('') → False → block — fail-closed
    if len(survivors) == 0:
        log_debug(
            "competitive",
            "Zero survivors — returning '' (stop: fail-open, pre-tool: fail-closed)",
        )
        return "", expression

    # Evaluate each survivor with context-aware positive predicate
    passed = [
        verdict_passes_for_context(resp, call_context) for resp, _label in survivors
    ]

    # Mechanical decision — computed in code, NOT delegated to the LLM
    is_stop = call_context == "stop_hook"
    if is_stop:
        # Stop gate: missing verifiers are ignored; all present survivors must pass
        overall_pass = all(passed)
    else:
        # Pre-tool gate: every verifier must respond AND pass (fail-closed)
        overall_pass = (len(survivors) == len(verifiers)) and all(passed)

    if overall_pass:
        log_debug(
            "competitive",
            f"Mechanical APPROVED ({len(survivors)}/{len(verifiers)} verifiers passed)",
        )
        return "APPROVED", expression

    # Build failure message from failing survivors only
    failing = [(resp, label) for (resp, label), p in zip(survivors, passed) if not p]

    if not failing:
        # All present survivors passed but a verifier was missing (pre-tool fail-closed edge case)
        message = "a required verifier did not respond (fail-closed)"
        log_debug(
            "competitive",
            "BLOCKED: missing verifier, all present passed — fail-closed",
        )
    elif len(failing) == 1:
        # Single failing verifier: raw response used; synthesizer NOT called
        message = failing[0][0]
        log_debug(
            "competitive",
            "Single failing verifier — raw message used, synthesizer skipped",
        )
    else:
        # 2+ failing verifiers: synthesizer formats the combined message
        log_debug(
            "competitive",
            f"{len(failing)} failing verifiers — calling synthesizer '{synthesizer}' for message",
        )
        message = _format_failure_message(
            failing,
            synthesizer,
            prompt,
            system_prompt,
            call_context,
            max_thinking_tokens,
        )

    # BLOCKED: prefix applied mechanically — synthesizer output cannot override FAIL decision
    return "BLOCKED: " + message, expression
