"""Issue #165 -- the reviewer timeout was doubled (120 s -> 240 s).

Requests to the beast queue up, and some time out at 120 s. For the doubled
timeout to take effect, four places must agree: the reviewer CLI ceiling, the
PreToolUse hook timeout (constants.py, install.sh, hooks/hooks.json, and the
live settings.json deployed by install.sh), and the Stop hook timeout. Without
the larger hook timeouts the #152 deadline clamp would cut the reviewer short,
and a killed Stop hook would lose the verdict.
"""

import asyncio
import json
import pathlib
import re
import sys
import time
import types

import pytest

from pacemaker.constants import (
    PRE_TOOL_ANCHOR_CAP_SECONDS,
    PRE_TOOL_HOOK_TIMEOUT_SECONDS,
    PRE_TOOL_REVIEW_BUDGET_SECONDS,
    PRE_TOOL_SAFETY_MARGIN_SECONDS,
    REVIEWER_CLI_TIMEOUT_SECONDS,
    STOP_HOOK_TIMEOUT_SECONDS,
)
from pacemaker.inference import agy_provider, codex_provider, gemini_provider
from pacemaker.inference.competitive import (
    REVIEWER_WAIT_TIMEOUT_SEC,
    SYNTHESIS_TIMEOUT_SEC,
)
from pacemaker.inference.registry import (
    MIN_SDK_FALLBACK_BUDGET_SECONDS,
    PROVIDER_TIMEOUT_SAFETY_MARGIN_SECONDS,
    _remaining_budget,
)

REPO = pathlib.Path(__file__).resolve().parents[2]


class TestReviewerCliTimeout:
    def test_shared_constant_is_240(self):
        assert REVIEWER_CLI_TIMEOUT_SECONDS == 240

    def test_all_three_providers_use_the_shared_ceiling(self):
        assert codex_provider._DEFAULT_CLI_TIMEOUT_SEC == REVIEWER_CLI_TIMEOUT_SECONDS
        assert agy_provider._CLI_TIMEOUT_SEC == REVIEWER_CLI_TIMEOUT_SECONDS
        assert gemini_provider._CLI_TIMEOUT_SEC == REVIEWER_CLI_TIMEOUT_SECONDS


class TestHookTimeoutParity:
    def test_pre_tool_timeout_is_300(self):
        assert PRE_TOOL_HOOK_TIMEOUT_SECONDS == 300

    def test_stop_timeout_is_300(self):
        assert STOP_HOOK_TIMEOUT_SECONDS == 300

    def test_install_sh_stop_timeout_matches_constant(self):
        text = (REPO / "install.sh").read_text()
        match = re.search(
            r"\.hooks\.Stop\s*\+=.*?\$stop_hook.*?\"timeout\"\s*:\s*(\d+)",
            text,
            re.DOTALL,
        )
        assert match, "could not locate the Stop timeout in install.sh"
        assert int(match.group(1)) == STOP_HOOK_TIMEOUT_SECONDS

    def test_hooks_json_stop_timeout_matches_constant(self):
        manifest = json.loads((REPO / "hooks" / "hooks.json").read_text())
        entries = manifest["hooks"]["Stop"]
        assert len(entries) == 1
        timeouts = [h["timeout"] for h in entries[0]["hooks"]]
        assert timeouts == [STOP_HOOK_TIMEOUT_SECONDS]


class TestBudgetArithmetic:
    def test_codex_can_use_its_full_ceiling_inside_the_gate_budget(self):
        """Single-model path: with the transcript anchor resolving instantly,
        the clamp handed to codex is at least the CLI ceiling, so codex really
        gets the full 240 s."""
        deadline = time.monotonic() + PRE_TOOL_REVIEW_BUDGET_SECONDS
        handed = _remaining_budget(deadline, PROVIDER_TIMEOUT_SAFETY_MARGIN_SECONDS)
        effective = codex_provider.CodexProvider._clamp_timeout(handed)
        assert effective == REVIEWER_CLI_TIMEOUT_SECONDS

    def test_sdk_fallback_still_fits_after_a_full_codex_timeout(self):
        """After codex burns its whole ceiling, what is left must clear the
        Anthropic SDK fallback floor."""
        left = (
            PRE_TOOL_REVIEW_BUDGET_SECONDS
            - REVIEWER_CLI_TIMEOUT_SECONDS
            - PROVIDER_TIMEOUT_SAFETY_MARGIN_SECONDS
        )
        assert left >= MIN_SDK_FALLBACK_BUDGET_SECONDS

    def test_sdk_fallback_fits_with_a_half_slow_anchor(self):
        """The anchor wait shares the deadline; a 15 s lag (half its cap)
        must still leave the fallback its floor."""
        lag = PRE_TOOL_ANCHOR_CAP_SECONDS / 2
        left = (
            PRE_TOOL_REVIEW_BUDGET_SECONDS
            - lag
            - REVIEWER_CLI_TIMEOUT_SECONDS
            - PROVIDER_TIMEOUT_SAFETY_MARGIN_SECONDS
        )
        assert left >= MIN_SDK_FALLBACK_BUDGET_SECONDS

    def test_pre_tool_review_budget_follows_the_hook_timeout(self):
        assert (
            PRE_TOOL_REVIEW_BUDGET_SECONDS
            == PRE_TOOL_HOOK_TIMEOUT_SECONDS - PRE_TOOL_SAFETY_MARGIN_SECONDS
        )
        assert REVIEWER_WAIT_TIMEOUT_SEC + SYNTHESIS_TIMEOUT_SEC == (
            PRE_TOOL_REVIEW_BUDGET_SECONDS
        )

    def test_stop_hook_fits_competitive_chain(self):
        """Stop runs the competitive chain with no deadline: reviewer wait +
        synthesis must finish before the harness kills the Stop hook."""
        assert (
            REVIEWER_WAIT_TIMEOUT_SEC
            + SYNTHESIS_TIMEOUT_SEC
            + PRE_TOOL_SAFETY_MARGIN_SECONDS
            <= STOP_HOOK_TIMEOUT_SECONDS
        )

    def test_stop_hook_fits_a_full_single_model_cli_timeout(self):
        """A 240 s codex call must not get the Stop hook killed, with room for
        the surrounding work."""
        assert (
            REVIEWER_CLI_TIMEOUT_SECONDS + PRE_TOOL_SAFETY_MARGIN_SECONDS
            <= STOP_HOOK_TIMEOUT_SECONDS
        )


class TestDeadlineCutIsLabelledAsTimeout:
    """Issue #165 investigation: 'Empty response from opus' after a codex
    timeout. A fallback cut off by its deadline must read as a timeout, never
    as 'empty'. (Live evidence, blockage_events 57514/57522: the fallbacks died
    after 29 s / 23 s of a ~45 s budget, so those were genuine empty results,
    not deadline cuts.)"""

    @staticmethod
    def _install_fake_sdk(monkeypatch, query):
        sdk = types.ModuleType("claude_agent_sdk")
        sdk.query = query
        sdk_types = types.ModuleType("claude_agent_sdk.types")

        class _Options:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class _Result:
            pass

        sdk_types.ClaudeAgentOptions = _Options
        sdk_types.ResultMessage = _Result
        monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
        monkeypatch.setitem(sys.modules, "claude_agent_sdk.types", sdk_types)

    def test_slow_sdk_cut_by_deadline_says_timed_out(self, monkeypatch):
        from pacemaker.inference.anthropic_provider import AnthropicProvider
        from pacemaker.inference.provider import ProviderError

        async def hang(prompt, options):
            await asyncio.sleep(30)
            yield None

        self._install_fake_sdk(monkeypatch, hang)
        with pytest.raises(ProviderError) as exc:
            AnthropicProvider().query("p", "s", "opus", timeout=0.2)
        assert "timed out after 0.2s" in str(exc.value)
        assert "Empty response" not in str(exc.value)


class TestSdkErrorIsVisible:
    """Finding 3: _query_async used to swallow every SDK exception (debug log
    only) and raise a bare 'Empty response from <model>', hiding the cause."""

    @staticmethod
    def _query_raising(exc):
        async def boom(prompt, options):
            raise exc
            yield  # pragma: no cover - makes this an async generator

        return boom

    @staticmethod
    def _call():
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        return AnthropicProvider().query("p", "s", "opus")

    def test_exception_is_named_in_the_reason(self, monkeypatch):
        from pacemaker.inference.provider import ProviderError

        TestDeadlineCutIsLabelledAsTimeout._install_fake_sdk(
            monkeypatch, self._query_raising(RuntimeError("cli exited 1"))
        )
        with pytest.raises(ProviderError) as exc:
            self._call()
        assert (
            str(exc.value)
            == "Empty response from opus (SDK error: RuntimeError: cli exited 1)"
        )

    def test_exception_is_logged_as_a_warning(self, monkeypatch):
        from unittest.mock import patch

        from pacemaker.inference.provider import ProviderError

        TestDeadlineCutIsLabelledAsTimeout._install_fake_sdk(
            monkeypatch, self._query_raising(RuntimeError("cli exited 1"))
        )
        with patch("pacemaker.inference.anthropic_provider.log_warning") as warn:
            with pytest.raises(ProviderError):
                self._call()
        logged = " ".join(str(a) for call in warn.call_args_list for a in call.args)
        assert "RuntimeError" in logged and "cli exited 1" in logged

    def test_reason_is_capped_and_single_line(self, monkeypatch):
        from pacemaker.inference.provider import ProviderError

        long_text = "line one\nline two " + ("x " * 400)
        TestDeadlineCutIsLabelledAsTimeout._install_fake_sdk(
            monkeypatch, self._query_raising(ValueError(long_text))
        )
        with pytest.raises(ProviderError) as exc:
            self._call()
        message = str(exc.value)
        detail = message.split("(SDK error: ", 1)[1].rstrip(")")
        assert len(detail) <= len("ValueError: ") + 300
        assert "\n" not in message

    def test_key_and_token_looking_strings_are_redacted(self, monkeypatch):
        from pacemaker.inference.provider import ProviderError

        err = RuntimeError(
            "401 for key sk-ant-api03-AbCdEf0123456789AbCdEf0123456789 "
            "Authorization: Bearer abc123def456 api_key=hunter2hunter2"
        )
        TestDeadlineCutIsLabelledAsTimeout._install_fake_sdk(
            monkeypatch, self._query_raising(err)
        )
        with pytest.raises(ProviderError) as exc:
            self._call()
        message = str(exc.value)
        assert "sk-ant" not in message
        assert "abc123def456" not in message
        assert "hunter2" not in message
        assert "[redacted]" in message

    def test_truly_empty_result_keeps_the_plain_message(self, monkeypatch):
        from pacemaker.inference.provider import ProviderError

        async def nothing(prompt, options):
            return
            yield  # pragma: no cover - makes this an async generator

        TestDeadlineCutIsLabelledAsTimeout._install_fake_sdk(monkeypatch, nothing)
        with pytest.raises(ProviderError) as exc:
            self._call()
        assert str(exc.value) == "Empty response from opus"

    def test_exception_in_the_limit_fallback_branch_is_named_too(self, monkeypatch):
        from pacemaker.inference.provider import ProviderError

        calls = {"n": 0}

        async def first_limit_then_boom(prompt, options):
            calls["n"] += 1
            if calls["n"] == 1:
                result = sys.modules["claude_agent_sdk.types"].ResultMessage()
                result.result = "Claude usage limit reached"
                yield result
                return
            raise RuntimeError("fallback model unreachable")

        TestDeadlineCutIsLabelledAsTimeout._install_fake_sdk(
            monkeypatch, first_limit_then_boom
        )
        with pytest.raises(ProviderError) as exc:
            self._call()
        assert "SDK error: RuntimeError: fallback model unreachable" in str(exc.value)
        assert calls["n"] == 2


class TestSdkErrorRedaction:
    """_describe_sdk_error: credentials are redacted whole, ordinary text is
    left alone."""

    @staticmethod
    def _describe(text):
        from pacemaker.inference.anthropic_provider import _describe_sdk_error

        return _describe_sdk_error(RuntimeError(text))

    def test_dotted_token_value_is_fully_redacted(self):
        token = "ya29.a0AfH6SMBx-very-long-opaque-value-0123456789"
        out = self._describe('{"access_token":"%s"}' % token)
        assert "ya29" not in out
        assert "a0AfH6SMBx" not in out
        assert "very-long" not in out
        assert "0123456789" not in out
        assert "[redacted]" in out

    def test_underscore_prefixed_credential_names_are_redacted(self):
        out = self._describe(
            "client_secret=s3cr3tvalue1 refresh_token: r3fr3shvalue2 "
            "db_password=pa55word3"
        )
        for leaked in ("s3cr3tvalue1", "r3fr3shvalue2", "pa55word3"):
            assert leaked not in out
        assert out.count("[redacted]") == 3

    @pytest.mark.parametrize(
        "text",
        [
            "input token count exceeds",
            "exit code 1",
            "exit code 143",
            "250123 tokens > 200000",
            "Invalid API key · Please run /login",
            "overloaded_error",
        ],
    )
    def test_ordinary_text_is_kept(self, text):
        assert self._describe(text) == f"RuntimeError: {text}"

    def test_model_and_request_ids_are_kept(self):
        out = self._describe(
            "model: claude-sonnet-4-5-20250929 not found; "
            "request_id=req_011CTabcdefghijklmnopqrstuvwxyz "
            "Authorization: Bearer abc123def456"
        )
        assert "claude-sonnet-4-5-20250929" in out
        assert "req_011CTabcdefghijklmnopqrstuvwxyz" in out
        assert "abc123def456" not in out
