"""Issue #135 — an unparseable stop-gate response is not a negative verdict.

`run_mechanical()` scored a survivor with no verdict marker as a failing vote,
stamped "BLOCKED: " on it, and returned that to `parse_sdk_response()` — which
therefore never reached its documented fail-open branch. The user saw the
model's own narration presented as a governance decision.
"""

from unittest.mock import patch

import pytest

from pacemaker.inference.verdict import has_verdict_marker

# Verbatim from blockage_events — the verifier's ENTIRE response.
NARRATION = (
    "I'm going to check whether there's any development work in progress "
    "that needs E2E evidence, and whether Claude is waiting on async work."
)


class TestHasVerdictMarker:
    @pytest.mark.parametrize(
        "text",
        [
            "APPROVED",
            "**APPROVED**",
            "BLOCKED: intent mismatch",
            "**BLOCKED:** intent mismatch",
            "COMPLETE: done",
            "## COMPLETE: done",
            "Some preamble\nAPPROVED",
        ],
    )
    def test_recognises_markers(self, text):
        assert has_verdict_marker(text) is True

    @pytest.mark.parametrize("text", [NARRATION, "", "   ", "Let me analyze this."])
    def test_rejects_non_verdicts(self, text):
        assert has_verdict_marker(text) is False


def _run(survivor_responses, call_context):
    """Drive run_mechanical with canned reviewer responses.

    run_mechanical returns (message, expression). Only the message is asserted
    on here; the second element is the hook_model expression string, which is
    echoed back unchanged and is irrelevant to these tests.
    """
    from pacemaker.inference import competitive

    survivors = [(resp, f"model{i}") for i, resp in enumerate(survivor_responses)]
    with patch.object(competitive, "_dispatch_reviewers", return_value=(survivors, [])):
        message, _expression = competitive.run_mechanical(
            verifiers=[f"model{i}" for i in range(len(survivor_responses))],
            synthesizer="synth",
            prompt="p",
            system_prompt="s",
            call_context=call_context,
        )
    return message


class TestStopGate:
    def test_narration_only_survivor_does_not_block(self):
        """The incident: one verifier returned prose, and it blocked the turn."""
        message = _run([NARRATION], "stop_hook")
        assert not message.startswith("BLOCKED:")
        assert NARRATION not in message

    def test_all_unparseable_returns_empty_for_fail_open(self):
        assert _run([NARRATION, "Let me check."], "stop_hook") == ""

    def test_real_block_still_blocks(self):
        """Leniency must not weaken a genuine rejection."""
        message = _run(["BLOCKED: work is unfinished"], "stop_hook")
        assert message.startswith("BLOCKED:")
        assert "unfinished" in message

    def test_unparseable_ignored_alongside_a_real_approval(self):
        assert _run([NARRATION, "APPROVED"], "stop_hook") == "APPROVED"

    def test_unparseable_ignored_alongside_a_real_block(self):
        message = _run([NARRATION, "BLOCKED: not done"], "stop_hook")
        assert message.startswith("BLOCKED:")
        assert "not done" in message


class TestPreToolGateUnchanged:
    """Leniency is stop-gate only — unparseable output must never approve here."""

    def test_pre_tool_unparseable_still_fails_closed(self):
        assert _run([NARRATION], "intent_validation").startswith("BLOCKED:")

    def test_pre_tool_approval_still_passes(self):
        assert _run(["APPROVED"], "intent_validation") == "APPROVED"
