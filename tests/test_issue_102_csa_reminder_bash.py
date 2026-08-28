"""
Issue #102 regression tests: the CSA periodic sibling-session reminder never
reached Claude for Bash tool calls.

Root cause: in ``run_pre_tool_hook()``'s danger-bash branch (``hook.py``,
``if tool_name == "Bash":``), the branch's terminal ``return {"continue":
True}`` bypassed ``_merge_csa_reminder()`` -- the same helper every other
return path in the function uses to inject ``_csa_result["periodic_reminder"]``
into ``hookSpecificOutput.additionalContext``. For any other tool (e.g. Read),
that injection point (further down, the ``tool_name not in ["Write",
"Edit"]`` branch) was reached normally.

This covers the non-matching-command path (the common case: most Bash
commands never match a danger rule at all, so they fall through the
``if matched:`` block straight to the terminal return). The matched-but-
Phase-2-approved path shares the exact same terminal return and is not
separately re-tested here -- it is the identical code path.

MOCKING RATIONALE (mirrors tests/test_session_registry_hook_pre_tool_csa_wiring.py)
=====================================================================================
hook.py is the outermost process-entry-point boundary. Its collaborators
(CSA registry DB, danger-bash rules loader) touch real filesystems / DBs;
patching them at the module boundary is the correct unit-test strategy for
verifying HOOK WIRING (does the hook propagate what CSA returns?), as
opposed to CSA's own internal cadence logic (tested elsewhere).
"""

import json
from unittest.mock import MagicMock, patch

SESSION_ID = "session-issue-102"
AGENT_ID = "agent-issue-102"

_PERIODIC_REMINDER = "CSA REMINDER: sibling session-sibling-102 is active."

_CSA_WITH_REMINDER = {
    "periodic_reminder": _PERIODIC_REMINDER,
    "danger_bash_warning": "",
}
_CSA_EMPTY = {"periodic_reminder": "", "danger_bash_warning": ""}

_BENIGN_COMMAND = "ls -la /tmp"


def _bash_payload(command: str = _BENIGN_COMMAND) -> dict:
    return {
        "session_id": SESSION_ID,
        "agent_id": AGENT_ID,
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "transcript_path": "/tmp/fake-issue-102.jsonl",
    }


def _config(danger_bash_enabled: bool = True) -> dict:
    return {
        "enabled": True,
        "intent_validation_enabled": True,
        "danger_bash_enabled": danger_bash_enabled,
        "cross_session_awareness_enabled": True,
        "hook_model": "auto",
    }


def _run_pre_tool(
    payload: dict,
    csa_result: dict,
    matched_rules=None,
    danger_bash_enabled: bool = True,
    current_message_override=None,
) -> dict:
    """Invoke run_pre_tool_hook() with collaborators patched; return its
    result dict.

    ``matched_rules``: rules ``match_command`` reports as matched (default
    none -> falls through the ``if matched:`` block to the terminal return).
    ``current_message_override``: when given, patches
    ``get_current_turn_message_for_validation`` so a matched-rule Phase 1/2
    path can be exercised without a real transcript file.
    """
    if matched_rules is None:
        matched_rules = []

    with (
        patch(
            "pacemaker.hook.load_config",
            return_value=_config(danger_bash_enabled=danger_bash_enabled),
        ),
        patch("pacemaker.hook.load_state", return_value={}),
        patch("pacemaker.hook.save_state"),
        patch(
            "pacemaker.session_registry._csa.on_pre_tool_use",
            return_value=csa_result,
        ),
        patch(
            "pacemaker.session_registry.db.resolve_db_path",
            return_value="/tmp/fake-issue-102.db",
        ),
        patch("pacemaker.danger_bash_rules.load_rules", return_value=[]),
        patch("pacemaker.danger_bash_rules.match_command", return_value=matched_rules),
        patch(
            "pacemaker.hook.get_current_turn_message_for_validation",
            return_value=current_message_override,
        ),
        patch(
            "sys.stdin",
            MagicMock(read=MagicMock(return_value=json.dumps(payload))),
        ),
    ):
        import pacemaker.hook as hook_mod

        return hook_mod.run_pre_tool_hook()


def _has_reminder(result: dict) -> bool:
    ctx = result.get("hookSpecificOutput", {}).get("additionalContext", "")
    return _PERIODIC_REMINDER in ctx


class TestCsaReminderReachesBashNonDangerousPath:
    """The periodic reminder must be wired into the Bash gate's terminal
    return path (no danger rule matched -> falls through to the early
    ``return {"continue": True}`` that previously bypassed
    ``_merge_csa_reminder``)."""

    def test_reminder_wired_when_command_does_not_match_any_danger_rule(self):
        result = _run_pre_tool(_bash_payload(), csa_result=_CSA_WITH_REMINDER)
        assert _has_reminder(result), (
            "CSA periodic_reminder must reach Claude for a benign Bash "
            f"command. Got: {result}"
        )
        assert result.get("hookSpecificOutput", {}).get("hookEventName") == "PreToolUse"

    def test_reminder_wired_when_danger_bash_disabled_in_config(self):
        """Even with danger_bash_enabled False (gate preconditions unmet,
        the whole danger-bash block is skipped), the reminder must still
        reach Claude via the same terminal return."""
        result = _run_pre_tool(
            _bash_payload(),
            csa_result=_CSA_WITH_REMINDER,
            danger_bash_enabled=False,
        )
        assert _has_reminder(result), (
            "CSA periodic_reminder must reach Claude for Bash even when "
            f"danger_bash_enabled is False. Got: {result}"
        )


class TestNoSpuriousReminderWhenCsaEmpty:
    """Regression guard: when CSA has no reminder to offer (e.g. no
    siblings, or not yet the 5th call), the Bash gate must return the
    exact plain {'continue': True} it always did -- no spurious
    hookSpecificOutput / additionalContext."""

    def test_plain_continue_when_csa_reminder_empty(self):
        result = _run_pre_tool(_bash_payload(), csa_result=_CSA_EMPTY)
        assert result == {"continue": True}, (
            "Expected plain continue when CSA has no reminder to offer, "
            f"got: {result}"
        )


class TestDangerBashBlockingUnaffectedByFix:
    """The fix touches ONLY the falls-through-to-allow terminal return.
    A command that DOES match a danger rule and fails Phase 1 (no INTENT
    declared) must still block exactly as before -- the CSA merge must
    never leak into (or suppress) a block decision."""

    def test_matched_command_without_intent_still_blocks(self):
        """Phase 1 regex gate: matched rule + no INTENT in the anchored
        message -> block, regardless of CSA reminder availability."""
        matched = [{"id": "SD-99", "description": "rm -rf test rule"}]
        result = _run_pre_tool(
            _bash_payload("rm -rf /tmp/whatever"),
            csa_result=_CSA_WITH_REMINDER,
            matched_rules=matched,
            current_message_override="",  # turn found, no INTENT: marker
        )
        assert (
            result.get("decision") == "block"
        ), f"Matched danger command without INTENT must still block. Got: {result}"
