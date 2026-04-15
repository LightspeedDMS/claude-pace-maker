"""
Unit tests verifying hook.py correctly wires CSA periodic_reminder into responses
across all reachable Write/Edit return paths in run_pre_tool_hook().

Scope
-----
hook.py WIRING only: does the hook propagate whatever CSA returns into its response?
CSA internal behaviour (when the reminder is generated, 5th-call cadence, etc.) is
tested separately in test_session_registry_csa_pre_tool_use.py.

Mocking rationale
-----------------
hook.py is the outermost process-entry-point boundary.  All its collaborators touch
real filesystems, live DBs, or LLM APIs.  Patching them is the correct unit-test
strategy for boundary-wiring verification.

8 return paths exercised in the Write/Edit branch
--------------------------------------------------
  no_file_path     no file_path in tool_input
  master_disabled  config["enabled"] == False
  feature_disabled config["intent_validation_enabled"] == False
  non_source       file not recognised as source code
  approved_write   Write approved by intent_validator
  approved_edit    Edit approved by intent_validator
  blocked          intent_validator returns approved=False
  exception        outer try/except catches unhandled exception
"""

import contextlib
import json
from unittest.mock import MagicMock, patch

import pytest

# ── Constants ──────────────────────────────────────────────────────────────────
SESSION_ID = "session-csa-wiring-001"
AGENT_ID = "agent-csa-wiring-001"

_PERIODIC_REMINDER = "CSA REMINDER: sibling session-sibling-001 is active."

_BASE_CONFIG = {
    "enabled": True,
    "intent_validation_enabled": True,
    "tdd_enabled": True,
    "danger_bash_enabled": False,
    "cross_session_awareness_enabled": True,
    "hook_model": "auto",
}

_CSA_WITH_REMINDER = {
    "periodic_reminder": _PERIODIC_REMINDER,
    "danger_bash_warning": "",
}
_CSA_EMPTY = {"periodic_reminder": "", "danger_bash_warning": ""}

_APPROVED = {
    "approved": True,
    "feedback": "",
    "reviewer": "test",
    "tdd_failure": False,
    "clean_code_failure": False,
}
_BLOCKED = {
    "approved": False,
    "feedback": "TDD failure: no test declared.",
    "reviewer": "test",
    "tdd_failure": True,
    "clean_code_failure": False,
}


# ── Payload factories ──────────────────────────────────────────────────────────


def _write_payload(file_path="/repo/src/foo.py"):
    return {
        "session_id": SESSION_ID,
        "agent_id": AGENT_ID,
        "tool_name": "Write",
        "tool_input": {"file_path": file_path, "content": "x=1"},
        "transcript_path": "/tmp/fake.jsonl",
    }


def _edit_payload(file_path="/repo/src/foo.py"):
    return {
        "session_id": SESSION_ID,
        "agent_id": AGENT_ID,
        "tool_name": "Edit",
        "tool_input": {
            "file_path": file_path,
            "old_string": "x=1",
            "new_string": "x=2",
        },
        "transcript_path": "/tmp/fake.jsonl",
    }


def _no_file_payload():
    return {
        "session_id": SESSION_ID,
        "agent_id": AGENT_ID,
        "tool_name": "Write",
        "tool_input": {},
        "transcript_path": "/tmp/fake.jsonl",
    }


# ── Core runner ────────────────────────────────────────────────────────────────


def _run_pre_tool(
    payload: dict,
    config_override: dict = None,
    csa_result: dict = None,
    intent_result: dict = None,
    intent_raises: Exception = None,
    is_source: bool = True,
) -> dict:
    """Invoke run_pre_tool_hook() with collaborators patched; return its result dict."""
    if csa_result is None:
        csa_result = _CSA_WITH_REMINDER
    if intent_result is None:
        intent_result = _APPROVED

    config = {**_BASE_CONFIG, **(config_override or {})}

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch("pacemaker.hook.load_config", return_value=config))
        stack.enter_context(patch("pacemaker.hook.load_state", return_value={}))
        stack.enter_context(patch("pacemaker.hook.save_state"))
        stack.enter_context(
            patch(
                "pacemaker.session_registry._csa.on_pre_tool_use",
                return_value=csa_result,
            )
        )
        stack.enter_context(
            patch(
                "pacemaker.session_registry.db.resolve_db_path",
                return_value="/tmp/fake.db",
            )
        )
        stack.enter_context(
            patch(
                "pacemaker.extension_registry.is_source_code_file",
                return_value=is_source,
            )
        )
        stack.enter_context(
            patch("pacemaker.extension_registry.load_extensions", return_value={".py"})
        )
        stack.enter_context(
            patch("pacemaker.hook.get_last_n_messages_for_validation", return_value=[])
        )
        if intent_raises is not None:
            stack.enter_context(
                patch(
                    "pacemaker.intent_validator.validate_intent_and_code",
                    side_effect=intent_raises,
                )
            )
        else:
            stack.enter_context(
                patch(
                    "pacemaker.intent_validator.validate_intent_and_code",
                    return_value=intent_result,
                )
            )
        stack.enter_context(patch("pacemaker.hook.record_activity_event"))
        stack.enter_context(patch("pacemaker.hook.record_governance_event"))
        stack.enter_context(patch("pacemaker.hook.record_blockage"))
        stack.enter_context(
            patch(
                "sys.stdin", MagicMock(read=MagicMock(return_value=json.dumps(payload)))
            )
        )

        import pacemaker.hook as hook_mod

        return hook_mod.run_pre_tool_hook()


def _has_reminder(result: dict) -> bool:
    ctx = result.get("hookSpecificOutput", {}).get("additionalContext", "")
    return _PERIODIC_REMINDER in ctx


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "label,payload_fn,kwargs",
    [
        ("no_file_path", _no_file_payload, {}),
        ("master_disabled", _write_payload, {"config_override": {"enabled": False}}),
        (
            "feature_disabled",
            _write_payload,
            {"config_override": {"intent_validation_enabled": False}},
        ),
        ("non_source", lambda: _write_payload("/repo/README.md"), {"is_source": False}),
        ("approved_write", _write_payload, {"intent_result": _APPROVED}),
        ("approved_edit", _edit_payload, {"intent_result": _APPROVED}),
        ("blocked", _write_payload, {"intent_result": _BLOCKED}),
        ("exception", _write_payload, {"intent_raises": RuntimeError("boom")}),
    ],
)
def test_reminder_wired_on_write_edit_path(label, payload_fn, kwargs):
    """CSA periodic_reminder must appear in hookSpecificOutput.additionalContext on each path."""
    result = _run_pre_tool(payload_fn(), **kwargs)
    assert _has_reminder(result), (
        f"[{label}] periodic_reminder dropped — expected it in "
        f"hookSpecificOutput.additionalContext, got: {result}"
    )


def test_no_reminder_injected_when_csa_returns_empty():
    """When CSA returns no reminder, hook returns plain {'continue': True}."""
    result = _run_pre_tool(
        _write_payload(), csa_result=_CSA_EMPTY, intent_result=_APPROVED
    )
    assert result == {
        "continue": True
    }, f"Expected plain continue when CSA reminder is empty, got: {result}"


def test_reminder_wired_regardless_of_text_content():
    """Hook propagates whatever non-empty text CSA puts in periodic_reminder."""
    custom_reminder = "You share this workspace with agent-xyz; coordinate carefully."
    result = _run_pre_tool(
        _write_payload(),
        csa_result={"periodic_reminder": custom_reminder, "danger_bash_warning": ""},
        intent_result=_APPROVED,
    )
    ctx = result.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert (
        custom_reminder in ctx
    ), f"hook.py must propagate any non-empty periodic_reminder verbatim. Got: {result}"
