#!/usr/bin/env python3
"""
Tests for issue #152 code-review follow-up — danger-bash Phase 2 was
missing `_deadline` on its resolve_and_call_with_reviewer() call.

Problem (coordinator review, "CHANGES REQUIRED", HIGH): the Write/Edit
gate's Stage 2 call already threads `_deadline=_gate_deadline`, but the
danger-bash Phase 2 call at hook.py (~3364) did not. Without a deadline,
`_remaining_budget()` (inference/registry.py) returns None, so the codex
subprocess keeps its unclamped 120s timeout and the Anthropic SDK fallback
runs with no timeout and no #152 minimum-budget check at all -- the
combined worst case can exceed the 180s PreToolUse hook budget, meaning a
dangerous Bash command gets silently unvalidated when the harness kills
the hook.

Fix: pass `_deadline=_gate_deadline` at the danger-bash Phase 2 call site,
matching the Write/Edit gate. This reuses the *existing* #152 single-model
deadline-aware machinery in inference/registry.py (already unit-tested in
tests/unit/test_issue_152_deadline_aware_provider.py) and the *existing*
#142 zero-survivor / reviewer-unavailable fail-closed path in hook.py's
danger-bash gate (already covered by
tests/test_issue_142_zero_survivor_message.py's
TestDangerBashPhase2ZeroSurvivor) -- no new hook.py branch logic was
needed, only the missing kwarg.

This file adds HOOK-LEVEL coverage (per the coordinator's explicit ask)
proving two things end-to-end through run_pre_tool_hook():
1. The danger-bash Phase 2 provider call actually receives a `_deadline`
   (previously always None -- the exact defect being fixed).
2. When the remaining budget is forced below
   inference.registry.MIN_SDK_FALLBACK_BUDGET_SECONDS (15.0s), the real
   (unmocked) registry code skips the Anthropic SDK fallback and the gate
   fails closed with the "intent_validation_reviewer_unavailable"
   category -- never attempting the fallback provider at all.

Mocking constraint (per tests/conftest.py's autouse guard): all
codex/gemini/claude CLI/SDK calls are mocked at the namespace the code
imports from. Test 1 mocks pacemaker.inference.resolve_and_call_with_reviewer
directly (same pattern as test_issue_142_zero_survivor_message.py). Test 2
deliberately does NOT mock resolve_and_call_with_reviewer -- it needs the
REAL registry.py deadline-skip logic to run -- so it mocks one layer
deeper, pacemaker.inference.registry.get_provider (the primary provider
factory) and pacemaker.inference.anthropic_provider.AnthropicProvider (the
fallback provider class), so no subprocess/SDK call ever actually fires.
"""

import json
import sqlite3
import time
from unittest.mock import MagicMock, patch

from pacemaker import database
from pacemaker.hook import run_pre_tool_hook
from pacemaker.inference.provider import ProviderError


def _base_hook_data(session_id):
    return {
        "session_id": session_id,
        "transcript_path": "/tmp/nonexistent-transcript.jsonl",
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /tmp/x", "description": "cleanup"},
    }


class TestDangerBashPhase2DeadlineAware:
    @patch("pacemaker.hook.load_config")
    @patch("pacemaker.danger_bash_rules.load_rules")
    @patch("pacemaker.danger_bash_rules.match_command")
    @patch("pacemaker.hook.get_current_turn_message_for_validation")
    @patch("pacemaker.inference.resolve_and_call_with_reviewer")
    @patch("sys.stdin")
    def test_deadline_threaded_into_resolve_and_call(
        self,
        mock_stdin,
        mock_resolve,
        mock_get_override,
        mock_match_command,
        mock_load_rules,
        mock_load_config,
        tmp_path,
    ):
        """The exact defect: before this fix, the danger-bash Phase 2 call
        never passed `_deadline` at all, so `resolve_and_call_with_reviewer`
        always received `_deadline=None` for this gate -- silently
        disabling the whole #152 single-model deadline-aware path for
        every Bash command. Post-fix, it must receive the gate's own
        `_gate_deadline`, a real time.monotonic()-based float."""
        db_path = str(tmp_path / "usage.db")
        database.initialize_database(db_path)

        hook_data = _base_hook_data("test-152-bash-deadline")
        mock_stdin.read.return_value = json.dumps(hook_data)
        mock_load_config.return_value = {
            "enabled": True,
            "intent_validation_enabled": True,
            "danger_bash_enabled": True,
        }
        mock_load_rules.return_value = [{"id": "SD-01", "description": "rm -rf"}]
        mock_match_command.return_value = [{"id": "SD-01", "description": "rm -rf"}]
        mock_get_override.return_value = "INTENT: delete a temp directory"
        mock_resolve.return_value = ("APPROVED", "codex-gpt5")

        before = time.monotonic()
        with patch("pacemaker.hook.DEFAULT_DB_PATH", db_path):
            run_pre_tool_hook()
        after = time.monotonic()

        mock_resolve.assert_called_once()
        _, kwargs = mock_resolve.call_args
        assert "_deadline" in kwargs
        deadline = kwargs["_deadline"]
        assert deadline is not None, (
            "danger-bash Phase 2 must pass a real _deadline, not None -- "
            "this is the exact regression issue #152's review flagged"
        )
        # _gate_deadline = time.monotonic() + 180 - 10 at gate entry, before
        # this call fired -- generous bounds account for real wall-clock
        # elapsed between the two time.monotonic() samples above.
        assert before + 150 <= deadline <= after + 190

    @patch("pacemaker.hook.load_config")
    @patch("pacemaker.danger_bash_rules.load_rules")
    @patch("pacemaker.danger_bash_rules.match_command")
    @patch("pacemaker.hook.get_current_turn_message_for_validation")
    @patch("pacemaker.inference.anthropic_provider.AnthropicProvider")
    @patch("pacemaker.inference.registry.get_provider")
    @patch("pacemaker.hook.PRE_TOOL_SAFETY_MARGIN_SECONDS", 0)
    @patch("pacemaker.hook.PRE_TOOL_HOOK_TIMEOUT_SECONDS", -100)
    @patch("sys.stdin")
    def test_fails_closed_reviewer_unavailable_when_budget_too_low(
        self,
        mock_stdin,
        mock_get_provider,
        MockAnthropicProvider,
        mock_get_override,
        mock_match_command,
        mock_load_rules,
        mock_load_config,
        tmp_path,
    ):
        """Forces _gate_deadline into the past (PRE_TOOL_HOOK_TIMEOUT_SECONDS
        patched to -100, margin to 0, so _gate_deadline ~= now - 100). Runs
        the REAL inference/registry.py resolve_and_call_with_reviewer (not
        mocked) so its own #152 fail-closed skip-fallback logic executes:
        remaining budget after PROVIDER_TIMEOUT_SAFETY_MARGIN_SECONDS is
        floored at 0.0, which is below MIN_SDK_FALLBACK_BUDGET_SECONDS
        (15.0), so the Anthropic SDK fallback must be skipped entirely --
        proven by asserting the fallback provider's query() was NEVER
        called -- and the gate must fail closed via the existing #142
        reviewer-unavailable path."""
        db_path = str(tmp_path / "usage.db")
        database.initialize_database(db_path)

        hook_data = _base_hook_data("test-152-bash-failclosed")
        mock_stdin.read.return_value = json.dumps(hook_data)
        mock_load_config.return_value = {
            "enabled": True,
            "intent_validation_enabled": True,
            "danger_bash_enabled": True,
            "hook_model": "gpt-5",
        }
        mock_load_rules.return_value = [{"id": "SD-01", "description": "rm -rf"}]
        mock_match_command.return_value = [{"id": "SD-01", "description": "rm -rf"}]
        mock_get_override.return_value = "INTENT: delete a temp directory"

        # Primary provider (codex, via hook_model="gpt-5") fails fast --
        # no real subprocess ever runs.
        mock_primary_provider = MagicMock()
        mock_primary_provider.query.side_effect = ProviderError(
            "simulated primary provider failure"
        )
        mock_get_provider.return_value = mock_primary_provider

        mock_fallback_instance = MagicMock()
        mock_fallback_instance.query.return_value = "APPROVED"
        MockAnthropicProvider.return_value = mock_fallback_instance

        with patch("pacemaker.hook.DEFAULT_DB_PATH", db_path):
            result = run_pre_tool_hook()

        # The fallback must never even be attempted -- this is the whole
        # point of the #152 fail-closed skip-fallback rule.
        mock_fallback_instance.query.assert_not_called()

        assert result.get("decision") == "block"
        reason = result["reason"]
        assert "No reviewer responded" in reason

        conn = sqlite3.connect(db_path)
        try:
            blockage_rows = conn.execute(
                "SELECT category FROM blockage_events"
            ).fetchall()
        finally:
            conn.close()
        assert len(blockage_rows) == 1, blockage_rows
        assert blockage_rows[0][0] == "intent_validation_reviewer_unavailable"
        assert blockage_rows[0][0] != "intent_validation_dangerbash"
