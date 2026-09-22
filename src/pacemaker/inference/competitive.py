"""Competitive multi-model review pipeline for hook inference."""

import re
import time
from concurrent.futures import (
    ThreadPoolExecutor,
    wait as futures_wait,
)
from typing import Optional

from ..constants import PRE_TOOL_REVIEW_BUDGET_SECONDS
from ..logger import log_warning, log_debug
from .registry import get_provider, resolve_model_for_call
from .codex_provider import CodexProvider
from .gemini_provider import GeminiProvider
from .agy_provider import AgyProvider
from .provider import ProviderError
from .model_aliases import SHORT_ALIASES, is_known_model
from .verdict import has_verdict_marker, verdict_passes_for_context

_ANTHROPIC_MODELS = {"auto", "sonnet", "opus", "haiku", "fable"}

_REVIEWER_CODEX = "codex-gpt5"
_REVIEWER_GEMINI_FLASH = "gem-flash"
_REVIEWER_GEMINI_PRO = "gem-pro"
_REVIEWER_SDK = "anthropic-sdk"

# Named constants for timeouts and token budget
# Review budgets derived from the PreToolUse allowance (issue #108), NOT
# hardcoded. These were 60 and 30 — summing to 90s against a 60s registered
# timeout — so a slow verifier plus a synthesis round could get the hook
# killed, and a killed PreToolUse hook is an UNVALIDATED TOOL CALL, not a
# block.
#
# Split 70/30 of the review budget. A verifier that overruns its share is a
# non-responder, which run_mechanical already handles as a degraded approval
# (issue #131) rather than as a negative verdict — so overrunning costs
# review depth, never correctness.
REVIEWER_WAIT_TIMEOUT_SEC = int(PRE_TOOL_REVIEW_BUDGET_SECONDS * 0.7)  # 35s
SYNTHESIS_TIMEOUT_SEC = PRE_TOOL_REVIEW_BUDGET_SECONDS - REVIEWER_WAIT_TIMEOUT_SEC
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


def _collect_timeouts(not_done: set, future_to_model: dict) -> list:
    """Cancel timed-out futures. Returns [(model, reason), ...] failures."""
    failed = []
    for future in not_done:
        future.cancel()
        model = future_to_model[future]
        reason = f"timed out after {REVIEWER_WAIT_TIMEOUT_SEC}s"
        log_warning("competitive", f"Reviewer {model} {reason}")
        failed.append((model, reason))
    return failed


def _collect_results(done: set, future_to_model: dict) -> tuple:
    """Resolve completed futures. Returns (succeeded, failed)."""
    succeeded = []
    failed = []
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
            failed.append((model, str(e)))
    return succeeded, failed


def _dispatch_reviewers(
    reviewers: list,
    prompt: str,
    system_prompt: str,
    call_context: str,
    max_thinking_tokens: int,
    wait_timeout: float = None,
) -> tuple:
    """Dispatch all reviewers in parallel. Returns (succeeded, failed).

    wait_timeout bounds the WHOLE GROUP, not each reviewer — they run
    concurrently, so N verifiers cost max(latency), never the sum. Defaults to
    REVIEWER_WAIT_TIMEOUT_SEC; the caller passes a smaller value when the
    gate's deadline leaves less than that (issue #108).

    succeeded: list of (response, label). failed: list of (model, reason) for
    a reviewer that timed out or raised — an INFRASTRUCTURE FAILURE, never a
    verdict (issue #131). futures_wait() bounds the timeout so partial
    results are collected; executor.shutdown(wait=False) doesn't block on
    in-flight threads (they cannot be force-killed).
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

    effective_timeout = (
        REVIEWER_WAIT_TIMEOUT_SEC if wait_timeout is None else wait_timeout
    )
    done, not_done = futures_wait(
        list(future_to_model.keys()), timeout=effective_timeout
    )

    failed = _collect_timeouts(not_done, future_to_model)
    executor.shutdown(wait=False)
    succeeded, result_failed = _collect_results(done, future_to_model)
    failed.extend(result_failed)

    return succeeded, failed


def _format_failure_message(
    failing: list,
    synthesizer: str,
    original_prompt: str,
    system_prompt: str,
    call_context: str,
    max_thinking_tokens: int,
    synth_timeout: float = None,
) -> str:
    """Call synthesizer to format a combined message from 2+ failing verifier verdicts.

    synth_timeout defaults to SYNTHESIS_TIMEOUT_SEC; the caller passes a
    smaller value when the gate's deadline leaves less than that (issue #108).

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
            result = future.result(
                timeout=(
                    SYNTHESIS_TIMEOUT_SEC if synth_timeout is None else synth_timeout
                )
            )
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


_BLOCKED_PREFIX_RE = re.compile(r"^\s*blocked\s*:\s*", re.IGNORECASE)


def _strip_leading_blocked_prefix(text: str) -> str:
    """Strip a leading 'BLOCKED:' marker (case-insensitive) from text, if present.

    Verifiers are prompted to respond in 'BLOCKED: <reason>' format, so their raw
    feedback already carries this prefix. The synthesizer's formatted message may
    also happen to start with it. run_mechanical() applies its own mechanical
    'BLOCKED: ' prefix unconditionally, so any pre-existing prefix must be
    stripped first to avoid a doubled 'BLOCKED: BLOCKED:' result (issue #88).
    """
    return _BLOCKED_PREFIX_RE.sub("", text, count=1)


def _record_degradation(
    _degradation: Optional[dict], failed: list, zero_survivors: bool = False
) -> None:
    """Populate the optional _degradation out-param for a degraded result.

    No-op when _degradation is None (default — existing callers unaffected).

    zero_survivors distinguishes two shapes of "degraded" (issue #142):
    - False (default): APPROVED, but one or more verifiers failed to
      respond — the responders' verdict still decided the outcome.
    - True: EVERY verifier failed to respond — there is no verdict at all,
      only infrastructure failures. Callers (the Write/Edit and danger-bash
      gates) use this to surface WHY no reviewer answered instead of
      relaying a blank response as if it were reviewer feedback.
    """
    if _degradation is None:
        return
    _degradation["degraded"] = True
    _degradation["failed_providers"] = {model: reason for model, reason in failed}
    _degradation["context"] = "competitive"
    if zero_survivors:
        _degradation["zero_survivors"] = True


def run_mechanical(
    verifiers: list,
    synthesizer: str,
    prompt: str,
    system_prompt: str,
    call_context: str,
    max_thinking_tokens: int = DEFAULT_MAX_THINKING_TOKENS,
    _degradation: Optional[dict] = None,
    _deadline: Optional[float] = None,
) -> tuple:
    """N-verifier mechanical decision engine with message-only synthesizer.

    Decision is computed in code (PASS iff all PRESENT survivors PASS).
    Synthesizer is demoted to message-only formatter, it can NEVER flip the verdict.
    'BLOCKED:' prefix is applied mechanically by this function regardless of what
    the synthesizer returns, closing the false-allow-via-encoded-message trap.

    Both gates share IDENTICAL pass logic (issue #131): a verifier that never
    responds is an INFRASTRUCTURE FAILURE, not a verdict, and is never counted
    the same as a verifier that voted BLOCKED:
    - All present survivors must pass, APPROVED (missing verifiers ignored;
      _degradation records it when one or more verifiers failed to respond)
    - Any non-passing survivor, BLOCKED (unchanged)
    - Zero survivors, '' (stop: parse_sdk_response('') fail-open, avoids
      infinite stop loop; pre-tool: verdict_passes('') = False so fail-closed,
      the ONLY remaining way a competitive expression blocks via emptiness)

    _degradation: optional out-param dict (same idiom as _diagnostics/_outcome
    in transcript_reader.py). Populated with {"degraded": False} normally, or
    {"degraded": True, "failed_providers": {model: reason, ...},
    "context": "competitive"} when APPROVED but one or more verifiers failed
    to respond. Issue #142: on the ZERO-SURVIVORS path (empty-string
    return) the same dict is populated with an additional
    {"zero_survivors": True} so callers can tell "nobody answered at all"
    apart from a degraded-but-approved review, and build a real explanation
    instead of relaying the blank response as reviewer feedback. Never
    raises; None (default) is a no-op for existing callers.

    Returns (response, reviewer_label) tuple, contract unchanged.
    """
    expression = "+".join(verifiers) + "->" + synthesizer
    log_debug(
        "competitive",
        f"Dispatching {len(verifiers)} verifiers in parallel: {verifiers}",
    )

    # Clamp each phase to the time actually left before the harness kills the
    # hook (issue #108). A killed PreToolUse hook is an unvalidated tool call,
    # so the review must always finish and answer with whatever it has. Phases
    # are sequential, so each re-reads the clock.
    def _budget(cap: float) -> float:
        if _deadline is None:
            return cap
        return max(0.0, min(cap, _deadline - time.monotonic()))

    survivors, failed = _dispatch_reviewers(
        verifiers,
        prompt,
        system_prompt,
        call_context,
        max_thinking_tokens,
        wait_timeout=_budget(REVIEWER_WAIT_TIMEOUT_SEC),
    )

    if _degradation is not None:
        _degradation.clear()
        _degradation["degraded"] = False

    # Zero survivors: return empty string and let gate semantics handle it.
    # Stop gate: parse_sdk_response('') -> {"continue": True} - fail-open (avoids infinite loop)
    # Pre-tool gate: verdict_passes('') -> False -> block - fail-closed
    if len(survivors) == 0:
        log_debug(
            "competitive",
            "Zero survivors - returning '' (stop: fail-open, pre-tool: fail-closed)",
        )
        # Issue #142: even though the response is empty, WHY nobody
        # answered is known (every verifier timed out or raised) — surface
        # it via _degradation so callers can build a real explanation
        # instead of relaying a blank body as reviewer feedback.
        _record_degradation(_degradation, failed, zero_survivors=True)
        return "", expression

    # Stop gate only: a survivor carrying NO recognisable verdict marker did
    # not render a judgment — it returned something unusable (truncation, or a
    # model narrating its plan). Treat it as a non-responder, exactly like a
    # verifier that raised ProviderError, rather than as a negative vote
    # (issue #135). Previously such prose was scored False, stamped with
    # "BLOCKED: " here, and surfaced to the user as the governance reason,
    # which also made parse_sdk_response's documented fail-open unreachable.
    #
    # The pre-tool gate is deliberately NOT given this leniency: unparseable
    # output there must never become an approval.
    if call_context == "stop_hook":
        scored = [(r, lbl) for r, lbl in survivors if has_verdict_marker(r)]
        if not scored:
            log_debug(
                "competitive",
                f"Stop gate: all {len(survivors)} survivor(s) unparseable - "
                "returning '' (fail-open, same as zero survivors)",
            )
            return "", expression
        if len(scored) != len(survivors):
            log_debug(
                "competitive",
                f"Stop gate: ignoring {len(survivors) - len(scored)} unparseable "
                "survivor(s) as non-responders",
            )
        survivors = scored

    # Evaluate each survivor with context-aware positive predicate
    passed = [
        verdict_passes_for_context(resp, call_context) for resp, _label in survivors
    ]

    # Mechanical decision, computed in code, NOT delegated to the LLM. Both
    # gates now share this single rule (issue #131): missing verifiers are
    # ignored, only a responder's own non-passing verdict blocks.
    overall_pass = all(passed)

    if overall_pass:
        if failed:
            _record_degradation(_degradation, failed)
        missing_models = [m for m, _r in failed]
        log_debug(
            "competitive",
            f"Mechanical APPROVED ({len(survivors)}/{len(verifiers)} verifiers passed) "
            f"degraded={bool(failed)} missing={missing_models}",
        )
        return "APPROVED", expression

    # Build failure message from failing survivors only. `failing` is
    # guaranteed non-empty here: overall_pass is False means all(passed) is
    # False, so at least one survivor's verdict didn't pass (Messi Rule 12,
    # no dead "missing verifier but nothing failing" branch: a missing
    # verifier alone can no longer produce overall_pass=False).
    failing = [(resp, label) for (resp, label), p in zip(survivors, passed) if not p]

    if len(failing) == 1:
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
            synth_timeout=_budget(SYNTHESIS_TIMEOUT_SEC),
        )

    # Strip any leading 'BLOCKED:' the message already carries (case-insensitive,
    # optional whitespace after the colon) before applying the mechanical prefix
    # below. Verifiers are prompted to respond in 'BLOCKED: <reason>' format, and
    # the synthesizer's formatted message may also happen to start with 'BLOCKED:'
    # — without this strip, the mechanical prefix doubles up (issue #88).
    message = _strip_leading_blocked_prefix(message)

    # BLOCKED: prefix applied mechanically — synthesizer output cannot override FAIL decision
    return "BLOCKED: " + message, expression
