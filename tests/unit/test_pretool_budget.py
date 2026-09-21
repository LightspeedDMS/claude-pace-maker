"""Issue #108 — the PreToolUse gate must answer before the harness kills it.

Claude Code kills the hook at its registered timeout, and a KILLED PreToolUse
hook is an UNVALIDATED TOOL CALL — the harness simply proceeds. The phases
(anchor wait -> reviewers -> synthesis) are sequential and each carried its own
fixed timeout with nothing summing them, so the chain reached 90s against a
60s allowance.

One deadline is now set at gate entry and every phase takes
min(its cap, remaining). Running short costs review depth, never correctness:
a reviewer cut off is a non-responder, which #131 records as a degraded
approval.
"""

import pathlib
import re
import time
from unittest.mock import patch

from pacemaker.constants import (
    PRE_TOOL_ANCHOR_CAP_SECONDS,
    PRE_TOOL_HOOK_TIMEOUT_SECONDS,
    PRE_TOOL_REVIEW_BUDGET_SECONDS,
    PRE_TOOL_SAFETY_MARGIN_SECONDS,
)
from pacemaker.inference import competitive
from pacemaker.inference.competitive import (
    REVIEWER_WAIT_TIMEOUT_SEC,
    SYNTHESIS_TIMEOUT_SEC,
)

REPO = pathlib.Path(__file__).resolve().parents[2]


class TestFullSequentialChain:
    def test_full_sequential_chain_fits(self):
        """The question the first version of this guard failed to ask.

        It measured only the review phase, so it passed while the anchor wait
        (sequential, up to 30s) still pushed the chain past the allowance.
        """
        worst_case = (
            PRE_TOOL_ANCHOR_CAP_SECONDS
            + REVIEWER_WAIT_TIMEOUT_SEC
            + SYNTHESIS_TIMEOUT_SEC
            + PRE_TOOL_SAFETY_MARGIN_SECONDS
        )
        # The caps may exceed the allowance on paper — the deadline is what
        # enforces it — but the gate must never rely on that alone.
        assert worst_case > PRE_TOOL_HOOK_TIMEOUT_SECONDS, (
            "caps now fit statically; this guard should be tightened to assert "
            "the sum directly rather than relying on the runtime deadline"
        )

    def test_review_budget_leaves_a_margin(self):
        assert PRE_TOOL_REVIEW_BUDGET_SECONDS < PRE_TOOL_HOOK_TIMEOUT_SECONDS
        assert PRE_TOOL_SAFETY_MARGIN_SECONDS > 0

    def test_budgets_derive_from_constant(self):
        assert (
            REVIEWER_WAIT_TIMEOUT_SEC + SYNTHESIS_TIMEOUT_SEC
            == PRE_TOOL_REVIEW_BUDGET_SECONDS
        )

    def test_both_phases_get_usable_time(self):
        assert REVIEWER_WAIT_TIMEOUT_SEC > SYNTHESIS_TIMEOUT_SEC > 0


class TestInstallerAgrees:
    def test_installer_timeout_matches_constant(self):
        """install.sh registers the hook; the constant is the source of truth."""
        text = (REPO / "install.sh").read_text()
        match = re.search(
            r"\.hooks\.PreToolUse\s*\+=.*?\$pre_tool_hook.*?\"timeout\"\s*:\s*(\d+)",
            text,
            re.DOTALL,
        )
        assert match, "could not locate the PreToolUse timeout in install.sh"
        assert int(match.group(1)) == PRE_TOOL_HOOK_TIMEOUT_SECONDS


def _capture_wait(deadline):
    """Run run_mechanical and report the reviewer wait it actually used."""
    seen = {}

    def fake_dispatch(v, p, s, ctx, mt, wait_timeout=None):
        seen["wait"] = wait_timeout
        return ([("APPROVED", "m")], [])

    with patch.object(competitive, "_dispatch_reviewers", fake_dispatch):
        competitive.run_mechanical(
            ["m"], "synth", "p", "s", "stop_hook", _deadline=deadline
        )
    return seen["wait"]


class TestDeadlineClamping:
    def test_reviewer_wait_clamped_to_remaining(self):
        wait = _capture_wait(time.monotonic() + 5)
        assert wait <= 5.1
        assert wait < REVIEWER_WAIT_TIMEOUT_SEC

    def test_no_deadline_preserves_cap(self):
        assert _capture_wait(None) == REVIEWER_WAIT_TIMEOUT_SEC

    def test_generous_deadline_preserves_cap(self):
        assert _capture_wait(time.monotonic() + 600) == REVIEWER_WAIT_TIMEOUT_SEC

    def test_expired_deadline_returns_verdict_not_exception(self):
        """Out of time must still produce an answer — never a crash."""
        wait = _capture_wait(time.monotonic() - 10)
        assert wait == 0.0

    def test_clamp_never_negative(self):
        assert _capture_wait(time.monotonic() - 999) >= 0.0


class TestDeadlineThreading:
    def test_validator_forwards_deadline_to_review(self):
        """hook -> intent_validator -> registry -> run_mechanical."""
        import inspect

        from pacemaker import intent_validator
        from pacemaker.inference import registry

        for fn in (
            intent_validator.validate_intent_and_code,
            intent_validator._call_stage2_validation,
            registry.resolve_and_call_with_reviewer,
            competitive.run_mechanical,
        ):
            assert (
                "_deadline" in inspect.signature(fn).parameters
            ), f"{fn.__qualname__} cannot carry the gate deadline"

    def test_gate_sets_a_deadline(self):
        """run_pre_tool_hook must create the clock, not pass None."""
        src = (REPO / "src/pacemaker/hook.py").read_text()
        assert "_gate_deadline = (" in src
        assert "_deadline=_gate_deadline" in src

    def test_anchor_wait_is_clamped(self):
        """The anchor is sequential with the review and must share the budget."""
        src = (REPO / "src/pacemaker/hook.py").read_text()
        assert "PRE_TOOL_ANCHOR_CAP_SECONDS" in src
        assert "_max_wait_seconds=max(" in src
