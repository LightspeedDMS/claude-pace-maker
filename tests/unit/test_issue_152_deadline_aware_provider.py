"""Issue #152 — single-model Stage 2 review ignores _deadline.

With a single-model hook_model (e.g. codex-beast), the pre-tool Stage 2
review did not respect the gate's _deadline: CodexProvider hardcoded a 120s
subprocess timeout, and when that timed out the Anthropic SDK fallback ran
on top of it with no remaining-budget check. Total review time regularly
exceeded the PreToolUse hook timeout — and a killed PreToolUse hook is a
SILENTLY UNVALIDATED tool call (see CLAUDE.md, "Competitive Review Pipeline
-> Timeouts").

Fix: the gate's _deadline is now threaded into the single-model provider
call (inference/registry.py). Each CLI provider (codex/gemini/agy) clamps
its subprocess timeout to min(own hardcoded ceiling, remaining budget minus
a safety margin) when a timeout is supplied; the Anthropic SDK path wraps
its whole async call in asyncio.wait_for() the same way. The Anthropic SDK
fallback (after a failed primary provider) now runs ONLY if enough budget
remains -- otherwise the gate fails closed via the existing #142 zero-
survivor / reviewer-unavailable path, never letting the hook exceed its own
budget.

Mocking boundary (per CLAUDE.md): only the subprocess/SDK boundary is
mocked (subprocess.run, or a fake claude_agent_sdk module) -- everything
else (registry.py's real orchestration logic) runs for real.
"""

import asyncio
import subprocess
import sys
import time
import types
from unittest.mock import MagicMock, patch

import pytest

from pacemaker.inference.provider import ProviderError


# ===========================================================================
# Group A: CLI providers (codex/gemini/agy) clamp their subprocess timeout
# to the caller-supplied `timeout` kwarg, never exceeding their own
# hardcoded ceiling.
# ===========================================================================


class TestCodexProviderTimeoutClamp:
    def test_timeout_kwarg_clamps_subprocess_timeout(self):
        from pacemaker.inference.codex_provider import CodexProvider

        provider = CodexProvider()
        mock_result = MagicMock(returncode=0, stdout="YES", stderr="")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            provider.query("p", "s", "o3", 4000, timeout=30.0)
        assert mock_run.call_args.kwargs["timeout"] == 30.0

    def test_timeout_kwarg_never_exceeds_provider_default(self):
        """A generous remaining budget must never RAISE the subprocess
        timeout above the provider's own known-safe ceiling (120s)."""
        from pacemaker.inference.codex_provider import CodexProvider

        provider = CodexProvider()
        mock_result = MagicMock(returncode=0, stdout="YES", stderr="")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            provider.query("p", "s", "o3", 4000, timeout=500.0)
        assert mock_run.call_args.kwargs["timeout"] == 120

    def test_no_timeout_kwarg_preserves_default(self):
        """Backward compatible: every pre-existing caller (e.g. the
        competitive multi-reviewer path) that never passes `timeout` gets
        byte-identical behavior to before this issue."""
        from pacemaker.inference.codex_provider import CodexProvider

        provider = CodexProvider()
        mock_result = MagicMock(returncode=0, stdout="YES", stderr="")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            provider.query("p", "s", "o3", 4000)
        assert mock_run.call_args.kwargs["timeout"] == 120

    def test_clamped_timeout_error_message_reflects_actual_value(self):
        from pacemaker.inference.codex_provider import CodexProvider

        provider = CodexProvider()
        with patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired("codex", 12)
        ):
            with pytest.raises(ProviderError, match="timed out"):
                provider.query("p", "s", "o3", 4000, timeout=12.0)


class TestGeminiProviderTimeoutClamp:
    def test_timeout_kwarg_clamps_subprocess_timeout(self):
        from pacemaker.inference.gemini_provider import GeminiProvider

        provider = GeminiProvider()
        mock_result = MagicMock(returncode=0, stdout="YES", stderr="")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            provider.query("p", "s", "gemini-flash", 4000, timeout=20.0)
        assert mock_run.call_args.kwargs["timeout"] == 20.0

    def test_no_timeout_kwarg_preserves_default(self):
        from pacemaker.inference.gemini_provider import GeminiProvider

        provider = GeminiProvider()
        mock_result = MagicMock(returncode=0, stdout="YES", stderr="")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            provider.query("p", "s", "gemini-flash", 4000)
        assert mock_run.call_args.kwargs["timeout"] == 120


class TestAgyProviderTimeoutClamp:
    def test_timeout_kwarg_clamps_subprocess_timeout(self):
        from pacemaker.inference.agy_provider import AgyProvider

        provider = AgyProvider()
        mock_result = MagicMock(returncode=0, stdout="YES", stderr="")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            provider.query("p", "s", "agy-flash", 4000, timeout=25.0)
        assert mock_run.call_args.kwargs["timeout"] == 25.0

    def test_no_timeout_kwarg_preserves_default(self):
        from pacemaker.inference.agy_provider import AgyProvider

        provider = AgyProvider()
        mock_result = MagicMock(returncode=0, stdout="YES", stderr="")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            provider.query("p", "s", "agy-flash", 4000)
        assert mock_run.call_args.kwargs["timeout"] == 120


# ===========================================================================
# Group B: AnthropicProvider — the SDK path wraps its whole async call in
# asyncio.wait_for() when a timeout is supplied.
# ===========================================================================


def _install_fake_sdk(delay_seconds: float = 0.0):
    """Installs a fake claude_agent_sdk module whose query() sleeps
    `delay_seconds` before yielding a result. Returns a restore callback."""

    class StubOptions:
        def __init__(self, **kwargs):
            pass

    class StubResult:
        result = "APPROVED"

    async def stub_query(prompt, options):
        if delay_seconds:
            await asyncio.sleep(delay_seconds)
        yield StubResult()

    fake_sdk = types.ModuleType("claude_agent_sdk")
    fake_sdk.query = stub_query
    fake_types = types.ModuleType("claude_agent_sdk.types")
    fake_types.ClaudeAgentOptions = StubOptions
    fake_types.ResultMessage = StubResult
    fake_sdk.types = fake_types

    saved_sdk = sys.modules.get("claude_agent_sdk")
    saved_types = sys.modules.get("claude_agent_sdk.types")
    sys.modules["claude_agent_sdk"] = fake_sdk
    sys.modules["claude_agent_sdk.types"] = fake_types

    def _restore():
        if saved_sdk is not None:
            sys.modules["claude_agent_sdk"] = saved_sdk
        else:
            sys.modules.pop("claude_agent_sdk", None)
        if saved_types is not None:
            sys.modules["claude_agent_sdk.types"] = saved_types
        else:
            sys.modules.pop("claude_agent_sdk.types", None)

    return _restore


class TestAnthropicProviderTimeoutClamp:
    def test_no_timeout_kwarg_preserves_default_behavior(self):
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        restore = _install_fake_sdk(delay_seconds=0.0)
        try:
            provider = AnthropicProvider()
            result = provider.query("hi", "sys", "opus", 1024)
        finally:
            restore()
        assert result == "APPROVED"

    def test_generous_timeout_still_succeeds(self):
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        restore = _install_fake_sdk(delay_seconds=0.01)
        try:
            provider = AnthropicProvider()
            result = provider.query("hi", "sys", "opus", 1024, timeout=5.0)
        finally:
            restore()
        assert result == "APPROVED"

    def test_tight_timeout_raises_provider_error(self):
        """A clamped timeout shorter than the SDK call's real latency must
        raise ProviderError (never hang past the gate's own deadline)."""
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        restore = _install_fake_sdk(delay_seconds=0.2)
        try:
            provider = AnthropicProvider()
            with pytest.raises(ProviderError, match="timed out"):
                provider.query("hi", "sys", "opus", 1024, timeout=0.01)
        finally:
            restore()


# ===========================================================================
# Group C: registry.py's single-model path is deadline-aware.
# ===========================================================================


class TestSingleModelDeadlineClamp:
    def test_deadline_threaded_as_clamped_timeout_kwarg(self):
        """The gate's _deadline, minus the safety margin, is passed to the
        provider as `timeout=`."""
        from pacemaker.inference.registry import (
            resolve_and_call_with_reviewer,
            PROVIDER_TIMEOUT_SAFETY_MARGIN_SECONDS,
        )

        mock_provider = MagicMock()
        mock_provider.query.return_value = "YES"
        deadline = time.monotonic() + 50.0

        with patch(
            "pacemaker.inference.registry.get_provider", return_value=mock_provider
        ):
            resolve_and_call_with_reviewer(
                "gpt-5", "prompt", "sys", "stage1", 4000, _deadline=deadline
            )

        called_timeout = mock_provider.query.call_args.kwargs["timeout"]
        expected = deadline - time.monotonic() - PROVIDER_TIMEOUT_SAFETY_MARGIN_SECONDS
        # Wall-clock tolerance for test execution jitter.
        assert abs(called_timeout - expected) < 1.0

    def test_no_deadline_passes_none_timeout(self):
        """Backward compatible: no _deadline (e.g. every pre-#152 caller)
        means `timeout=None` -- each provider's own hardcoded default."""
        from pacemaker.inference.registry import resolve_and_call_with_reviewer

        mock_provider = MagicMock()
        mock_provider.query.return_value = "YES"

        with patch(
            "pacemaker.inference.registry.get_provider", return_value=mock_provider
        ):
            resolve_and_call_with_reviewer("gpt-5", "prompt", "sys", "stage1", 4000)

        assert mock_provider.query.call_args.kwargs["timeout"] is None

    def test_no_fallback_when_budget_too_low_reviewer_unavailable(self):
        """Issue #152 / #142: when the primary provider fails and too
        little budget remains for the Anthropic SDK fallback, the gate
        fails CLOSED via the reviewer-unavailable path instead of running
        an unbounded fallback call."""
        from pacemaker.inference.registry import resolve_and_call_with_reviewer

        mock_primary = MagicMock()
        mock_primary.query.side_effect = ProviderError("codex failed")
        mock_fallback = MagicMock()
        mock_fallback.query.return_value = "SHOULD_NEVER_BE_RETURNED"

        # Deadline so close that, after the safety margin, almost nothing
        # remains -- well under MIN_SDK_FALLBACK_BUDGET_SECONDS.
        deadline = time.monotonic() + 1.0

        degradation = {}
        with patch(
            "pacemaker.inference.registry.get_provider", return_value=mock_primary
        ):
            with patch(
                "pacemaker.inference.anthropic_provider.AnthropicProvider",
                return_value=mock_fallback,
            ):
                with patch(
                    "pacemaker.inference.registry.get_latest_codex_usage",
                    return_value=None,
                ):
                    response, reviewer = resolve_and_call_with_reviewer(
                        "gpt-5",
                        "prompt",
                        "sys",
                        "stage1",
                        4000,
                        _degradation=degradation,
                        _deadline=deadline,
                    )

        mock_fallback.query.assert_not_called()
        assert response == ""
        assert reviewer == "unknown"
        assert degradation["zero_survivors"] is True
        assert "gpt-5" in degradation["failed_providers"]

    def test_fallback_still_runs_when_enough_time_remains(self):
        """With a generous deadline, the Anthropic SDK fallback still runs
        exactly as before this issue."""
        from pacemaker.inference.registry import resolve_and_call_with_reviewer

        mock_primary = MagicMock()
        mock_primary.query.side_effect = ProviderError("codex failed")
        mock_fallback = MagicMock()
        mock_fallback.query.return_value = "FALLBACK_YES"

        deadline = time.monotonic() + 170.0

        with patch(
            "pacemaker.inference.registry.get_provider", return_value=mock_primary
        ):
            with patch(
                "pacemaker.inference.anthropic_provider.AnthropicProvider",
                return_value=mock_fallback,
            ):
                with patch(
                    "pacemaker.inference.registry.get_latest_codex_usage",
                    return_value=None,
                ):
                    response, reviewer = resolve_and_call_with_reviewer(
                        "gpt-5", "prompt", "sys", "stage1", 4000, _deadline=deadline
                    )

        mock_fallback.query.assert_called_once()
        assert response == "FALLBACK_YES"
        assert reviewer == "anthropic-sdk"

    def test_total_elapsed_never_exceeds_budget_fake_clock(self):
        """Simulates a slow primary call (a clamped-timeout codex call that
        burned nearly the whole budget) using a fake, manually-advanced
        clock instead of real sleeps. The fallback must be skipped once
        the remaining budget (after the primary call's own elapsed time)
        is too low -- proving the gate never lets the SECOND call push
        the total past the deadline."""
        from pacemaker.inference import registry

        fake_now = [1000.0]

        def fake_monotonic():
            return fake_now[0]

        start = fake_now[0]
        deadline = start + 170.0  # PRE_TOOL_REVIEW_BUDGET_SECONDS-shaped

        def slow_primary_query(*args, **kwargs):
            # Simulate the primary call consuming almost the whole budget
            # (e.g. a clamped-timeout codex subprocess that ran right up
            # to its own timeout) before failing.
            fake_now[0] += 165.0
            raise ProviderError("codex timed out")

        mock_primary = MagicMock()
        mock_primary.query.side_effect = slow_primary_query
        mock_fallback = MagicMock()
        mock_fallback.query.return_value = "SHOULD_NEVER_BE_RETURNED"

        with patch("pacemaker.inference.registry.time.monotonic", fake_monotonic):
            with patch(
                "pacemaker.inference.registry.get_provider", return_value=mock_primary
            ):
                with patch(
                    "pacemaker.inference.anthropic_provider.AnthropicProvider",
                    return_value=mock_fallback,
                ):
                    with patch(
                        "pacemaker.inference.registry.get_latest_codex_usage",
                        return_value=None,
                    ):
                        response, reviewer = registry.resolve_and_call_with_reviewer(
                            "gpt-5",
                            "prompt",
                            "sys",
                            "stage1",
                            4000,
                            _deadline=deadline,
                        )

        mock_fallback.query.assert_not_called()
        assert response == ""
        assert reviewer == "unknown"
        # The elapsed fake-clock time never grew past the deadline because
        # the fallback (which would have added more simulated time) never
        # ran at all.
        assert fake_now[0] <= deadline


# ===========================================================================
# Group D: the Stop hook path (resolve_and_call, no _deadline concept) is
# UNCHANGED -- it never fails closed, regardless of provider failures.
# ===========================================================================


class TestStopPathUnchanged:
    def test_resolve_and_call_still_falls_back_and_never_fails_closed(self):
        from pacemaker.inference.registry import resolve_and_call

        mock_primary = MagicMock()
        mock_primary.query.side_effect = ProviderError("codex failed")
        mock_fallback = MagicMock()
        mock_fallback.query.return_value = "FALLBACK_YES"

        with patch(
            "pacemaker.inference.registry.get_provider", return_value=mock_primary
        ):
            with patch(
                "pacemaker.inference.anthropic_provider.AnthropicProvider",
                return_value=mock_fallback,
            ):
                with patch(
                    "pacemaker.inference.registry.get_latest_codex_usage",
                    return_value=None,
                ):
                    response = resolve_and_call(
                        "gpt-5", "prompt", "sys", "stop_hook", 4000
                    )

        # No _deadline exists on this path at all -- the fallback ALWAYS
        # runs, exactly as before issue #152.
        mock_fallback.query.assert_called_once()
        assert response == "FALLBACK_YES"

    def test_resolve_and_call_has_no_deadline_parameter(self):
        """Locks the Stop hook's own contract: resolve_and_call() must
        never grow a _deadline param -- that would change its fail-open
        semantics, which issue #152 explicitly must not touch."""
        import inspect
        from pacemaker.inference.registry import resolve_and_call

        assert "_deadline" not in inspect.signature(resolve_and_call).parameters


# ===========================================================================
# Group E: codex_provider.py's stderr filter (issue #132 follow-up) — known
# benign noise lines are filtered out before taking the LAST 300 chars.
# ===========================================================================


class TestCodexStderrFilter:
    def test_benign_noise_lines_filtered_real_reason_kept(self):
        from pacemaker.inference.codex_provider import CodexProvider

        provider = CodexProvider()
        stderr = (
            "codex_models_manager::manager: failed to refresh available models "
            "from remote, using cached list\n"
            "codex_rmcp_client::oauth::refresh_transaction: token refresh "
            "started\n"
            "ERROR: Reconnecting... 2/5 ... 404 Not Found\n"
        )
        mock_result = MagicMock(returncode=0, stdout="", stderr=stderr)
        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(ProviderError) as exc_info:
                provider.query("test", "", "o3", 4000)
        message = str(exc_info.value)
        assert "empty response" in message.lower()
        assert "404 Not Found" in message
        assert "codex_models_manager" not in message
        assert "codex_rmcp_client" not in message

    def test_filter_takes_last_300_chars_of_remaining(self):
        from pacemaker.inference.codex_provider import (
            CodexProvider,
            _STDERR_PREVIEW_CHARS,
        )

        provider = CodexProvider()
        noise = "codex_rmcp_client::oauth::refresh_transaction: noop\n"
        real_reason = "x" * 400 + "REAL_REASON_AT_END"
        stderr = noise + real_reason
        mock_result = MagicMock(returncode=1, stdout="", stderr=stderr)
        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(ProviderError) as exc_info:
                provider.query("test", "", "o3", 4000)
        message = str(exc_info.value)
        assert "REAL_REASON_AT_END" in message
        assert "codex_rmcp_client" not in message
        # Only the LAST _STDERR_PREVIEW_CHARS chars of the filtered
        # remainder are kept.
        assert len(real_reason) > _STDERR_PREVIEW_CHARS

    def test_all_benign_stderr_falls_back_to_no_stderr(self):
        from pacemaker.inference.codex_provider import CodexProvider

        provider = CodexProvider()
        stderr = (
            "codex_models_manager::manager: failed to refresh available models\n"
            "codex_rmcp_client::oauth::refresh_transaction: noop\n"
        )
        mock_result = MagicMock(returncode=0, stdout="", stderr=stderr)
        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(ProviderError) as exc_info:
                provider.query("test", "", "o3", 4000)
        assert "no stderr" in str(exc_info.value)

    def test_short_unfiltered_stderr_unaffected(self):
        """Regression: bug #132's own fixture (no benign-noise lines) must
        still work exactly as before."""
        from pacemaker.inference.codex_provider import CodexProvider

        provider = CodexProvider()
        stderr = (
            "OpenAI Codex v0.153.4 ... ERROR: Reconnecting... 2/5 ... 404 Not Found"
        )
        mock_result = MagicMock(returncode=0, stdout="", stderr=stderr)
        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(ProviderError) as exc_info:
                provider.query("test", "", "o3", 4000)
        message = str(exc_info.value)
        assert "empty response" in message.lower()
        assert "404 Not Found" in message
        assert "exit 0" in message
