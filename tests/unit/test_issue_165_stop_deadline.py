"""Issue #165 follow-up -- the Stop-hook review needs a deadline.

With the reviewer ceiling at 240 s, a Stop review had NO deadline:
run_stop_hook -> validate_intent -> call_sdk_validation -> resolve_and_call ->
resolve_and_call_with_reviewer(_deadline=None). Codex got its full 240 s, the
Anthropic fallback then ran with ``timeout=None`` (the
MIN_SDK_FALLBACK_BUDGET_SECONDS check is skipped without a deadline), and
Stop's Langfuse finalize runs first, unbounded. The total could pass the 300 s
Stop hook timeout, and the harness then kills the hook (fail open, but the
verdict is thrown away).

The Stop hook now takes ``hook_started`` at entry, derives
``hook_started + STOP_REVIEW_BUDGET_SECONDS`` and threads it down to the same
#152 machinery the PreToolUse gate uses. An empty result still fails open.

Mocking boundary: only providers (get_provider / AnthropicProvider) and the
Langfuse finalize are mocked; registry and competitive orchestration run for
real.
"""

import inspect
import json
import time
from unittest.mock import MagicMock, patch

from pacemaker.constants import (
    PRE_TOOL_SAFETY_MARGIN_SECONDS,
    REVIEWER_CLI_TIMEOUT_SECONDS,
    STOP_HOOK_SAFETY_MARGIN_SECONDS,
    STOP_HOOK_TIMEOUT_SECONDS,
    STOP_REVIEW_BUDGET_SECONDS,
)
from pacemaker.inference import competitive, registry
from pacemaker.inference.provider import ProviderError
from pacemaker.inference.registry import (
    MIN_SDK_FALLBACK_BUDGET_SECONDS,
    PROVIDER_TIMEOUT_SAFETY_MARGIN_SECONDS,
)


class TestStopBudgetConstants:
    def test_margin_is_a_named_positive_constant(self):
        assert STOP_HOOK_SAFETY_MARGIN_SECONDS > 0
        assert STOP_HOOK_SAFETY_MARGIN_SECONDS == PRE_TOOL_SAFETY_MARGIN_SECONDS

    def test_budget_is_timeout_minus_margin(self):
        assert (
            STOP_REVIEW_BUDGET_SECONDS
            == STOP_HOOK_TIMEOUT_SECONDS - STOP_HOOK_SAFETY_MARGIN_SECONDS
        )

    def test_codex_full_ceiling_and_sdk_fallback_fit_the_stop_budget(self):
        left = (
            STOP_REVIEW_BUDGET_SECONDS
            - REVIEWER_CLI_TIMEOUT_SECONDS
            - PROVIDER_TIMEOUT_SAFETY_MARGIN_SECONDS
        )
        assert left >= MIN_SDK_FALLBACK_BUDGET_SECONDS


class TestDeadlineThreading:
    def test_every_stop_layer_carries_a_deadline(self):
        from pacemaker import intent_validator

        for fn in (
            intent_validator.validate_intent,
            intent_validator.call_sdk_validation,
            registry.resolve_and_call,
        ):
            assert (
                "_deadline" in inspect.signature(fn).parameters
            ), f"{fn.__qualname__} cannot carry the Stop deadline"

    def test_resolve_and_call_forwards_the_deadline(self):
        seen = {}

        def fake_with_reviewer(*args, **kwargs):
            seen.update(kwargs)
            return "APPROVED", "x"

        with patch.object(
            registry, "resolve_and_call_with_reviewer", fake_with_reviewer
        ):
            registry.resolve_and_call("m", "p", "s", "stop_hook", 4000, _deadline=123.5)
        assert seen["_deadline"] == 123.5

    def test_resolve_and_call_without_a_deadline_is_unchanged(self):
        seen = {}

        def fake_with_reviewer(*args, **kwargs):
            seen.update(kwargs)
            return "APPROVED", "x"

        with patch.object(
            registry, "resolve_and_call_with_reviewer", fake_with_reviewer
        ):
            registry.resolve_and_call("m", "p", "s", "stop_hook")
        assert seen.get("_deadline") is None

    def test_call_sdk_validation_forwards_the_deadline(self):
        from pacemaker import intent_validator

        seen = {}

        def fake_resolve(**kwargs):
            seen.update(kwargs)
            return "APPROVED"

        with patch("pacemaker.inference.resolve_and_call", fake_resolve):
            intent_validator.call_sdk_validation(
                "ctx", hook_model="codex", _deadline=9.0
            )
        assert seen["_deadline"] == 9.0

    def test_validate_intent_forwards_the_deadline(self, tmp_path):
        from pacemaker import intent_validator

        transcript = tmp_path / "t.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "do the thing"},
                }
            )
            + "\n"
        )
        seen = {}

        def fake_call(context, hook_model="auto", _deadline=None):
            seen["deadline"] = _deadline
            return "APPROVED"

        with patch.object(intent_validator, "call_sdk_validation", fake_call):
            result = intent_validator.validate_intent(
                "sid", str(transcript), hook_model="codex", _deadline=77.0
            )
        assert seen["deadline"] == 77.0
        assert result == {"continue": True}


def _run_resolve(deadline, primary_burns, fake_now):
    """resolve_and_call with a codex primary that burns `primary_burns` fake
    seconds then fails, and a recording Anthropic fallback."""

    def slow_primary_query(*args, **kwargs):
        fake_now[0] += primary_burns
        raise ProviderError("codex timed out")

    primary = MagicMock()
    primary.query.side_effect = slow_primary_query
    fallback = MagicMock()
    fallback.query.return_value = "APPROVED"

    with patch("pacemaker.inference.registry.time.monotonic", lambda: fake_now[0]):
        with patch("pacemaker.inference.registry.get_provider", return_value=primary):
            with patch(
                "pacemaker.inference.anthropic_provider.AnthropicProvider",
                return_value=fallback,
            ):
                with patch(
                    "pacemaker.inference.registry.get_latest_codex_usage",
                    return_value=None,
                ):
                    response = registry.resolve_and_call(
                        "gpt-5",
                        "prompt",
                        "sys",
                        "stop_hook",
                        4000,
                        _deadline=deadline,
                    )
    return response, primary, fallback


class TestSingleModelStopPathIsBounded:
    def test_fallback_skipped_and_hook_returns_in_budget_when_codex_burns_it(self):
        fake_now = [1000.0]
        deadline = fake_now[0] + STOP_REVIEW_BUDGET_SECONDS
        # Leaves 290 - 275 - 5 = 10 s < MIN_SDK_FALLBACK_BUDGET_SECONDS.
        response, _primary, fallback = _run_resolve(deadline, 275.0, fake_now)
        fallback.query.assert_not_called()
        assert response == ""  # empty result: Stop fails open
        assert fake_now[0] <= deadline

    def test_fallback_gets_a_clamped_timeout_when_budget_remains(self):
        fake_now = [1000.0]
        deadline = fake_now[0] + STOP_REVIEW_BUDGET_SECONDS
        response, _primary, fallback = _run_resolve(
            deadline, REVIEWER_CLI_TIMEOUT_SECONDS, fake_now
        )
        fallback.query.assert_called_once()
        timeout = fallback.query.call_args.kwargs["timeout"]
        assert timeout == (
            STOP_REVIEW_BUDGET_SECONDS
            - REVIEWER_CLI_TIMEOUT_SECONDS
            - PROVIDER_TIMEOUT_SAFETY_MARGIN_SECONDS
        )
        assert response == "APPROVED"

    def test_primary_is_clamped_by_the_deadline(self):
        """A codex call may not be handed more than the time left."""
        fake_now = [1000.0]
        deadline = fake_now[0] + 60.0
        _response, primary, _fallback = _run_resolve(deadline, 0.0, fake_now)
        assert primary.query.call_args.kwargs["timeout"] == 55.0


class TestCompetitiveStopPathIsBounded:
    def test_reviewer_wait_is_clamped_to_the_remaining_budget(self):
        seen = {}

        def fake_dispatch(v, p, s, ctx, mt, wait_timeout=None):
            seen["wait"] = wait_timeout
            return ([("APPROVED", "m")], [])

        with patch.object(competitive, "_dispatch_reviewers", fake_dispatch):
            registry.resolve_and_call(
                "sonnet+opus->sonnet",
                "p",
                "s",
                "stop_hook",
                4000,
                _deadline=time.monotonic() + 5,
            )
        assert seen["wait"] <= 5.1
        assert seen["wait"] < competitive.REVIEWER_WAIT_TIMEOUT_SEC


class TestRunStopHookSetsTheDeadline:
    def _run(self, tmp_path, finalize_sleep):
        from pacemaker import hook

        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("")
        state = {"consecutive_stop_blocks": 0, "session_id": "s-165"}
        config = {
            "enabled": True,
            "tempo_mode": "on",
            "hook_model": "auto",
            "conversation_context_size": 5,
        }
        validator = MagicMock(return_value={"continue": True})

        def slow_finalize(*args, **kwargs):
            time.sleep(finalize_sleep)

        with (
            patch("pacemaker.hook.load_config", return_value=config),
            patch("pacemaker.hook.load_state", return_value=state),
            patch("pacemaker.hook.save_state"),
            patch("pacemaker.hook.is_context_exhaustion_detected", return_value=False),
            patch(
                "pacemaker.transcript_reader.detect_silent_tool_stop",
                return_value=False,
            ),
            patch("pacemaker.hook.should_run_tempo", return_value=True),
            patch(
                "pacemaker.langfuse.orchestrator.handle_stop_finalize",
                side_effect=slow_finalize,
            ),
            patch("pacemaker.intent_validator.validate_intent", validator),
            patch("pacemaker.hook.record_blockage"),
            patch("pacemaker.hook.record_activity_event"),
            patch("pacemaker.hook.DEFAULT_STATE_PATH", str(tmp_path / "state.json")),
            patch("pacemaker.hook.DEFAULT_DB_PATH", str(tmp_path / "t.db")),
            patch("sys.stdin") as stdin,
        ):
            stdin.read.return_value = json.dumps(
                {"session_id": "s-165", "transcript_path": str(transcript)}
            )
            before = time.monotonic()
            hook.run_stop_hook()
        return validator, before

    def test_validate_intent_receives_a_deadline_from_hook_entry(self, tmp_path):
        validator, before = self._run(tmp_path, finalize_sleep=0.0)
        validator.assert_called_once()
        deadline = validator.call_args.kwargs["_deadline"]
        assert deadline is not None
        assert (
            before + STOP_REVIEW_BUDGET_SECONDS - 0.5
            <= deadline
            <= time.monotonic() + STOP_REVIEW_BUDGET_SECONDS
        )

    def test_the_langfuse_finalize_counts_against_the_budget(self, tmp_path):
        """The clock starts at hook entry, so a slow finalize shortens the
        review instead of pushing the hook past its timeout."""
        validator, before = self._run(tmp_path, finalize_sleep=0.6)
        deadline = validator.call_args.kwargs["_deadline"]
        assert deadline <= before + STOP_REVIEW_BUDGET_SECONDS + 0.3
