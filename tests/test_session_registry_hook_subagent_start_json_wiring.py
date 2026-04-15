"""
Unit tests verifying run_subagent_start_hook() emits exactly ONE JSON object to
stdout when both intent-validation guidance and a CSA sibling banner are present.

Before the BLOCKING #2 fix, _emit_subagent_additional_context() was called twice
in a single SubagentStart execution — once for intent guidance and once for the CSA
banner — producing two concatenated JSON objects on stdout, which Claude Code's hook
protocol cannot parse correctly (it expects a single JSON response per invocation).

Scope
-----
SubagentStart JSON-emission WIRING only: how many JSON objects does the hook emit,
and do they contain the right merged text?

Mocking rationale
-----------------
run_subagent_start_hook() touches config files, state files, Langfuse APIs, and
the CSA DB.  Patching these collaborators is required for a hermetic unit test.

Cases covered
-------------
  both_contexts   intent guidance + CSA banner → exactly 1 JSON, contains both texts
  only_guidance   intent guidance only (no CSA banner) → exactly 1 JSON
  only_banner     CSA banner only (intent validation off) → exactly 1 JSON
  neither         intent off, no banner → 0 JSON objects emitted
"""

import contextlib
import json
import sys
from unittest.mock import MagicMock, patch

# ── Constants ──────────────────────────────────────────────────────────────────
SESSION_ID = "session-subagent-json-001"
AGENT_ID = "agent-subagent-json-001"

_INTENT_GUIDANCE = "INTENT GUIDANCE: declare INTENT: before every write."
_CSA_BANNER = "CSA: sibling session active in this workspace."


# ── Runner ─────────────────────────────────────────────────────────────────────


def _run_subagent_start(
    tmp_path,
    intent_enabled: bool,
    csa_banner: str,
) -> list:
    """Invoke run_subagent_start_hook() and return list of JSON strings emitted to stdout."""
    hook_data_json = json.dumps({"session_id": SESSION_ID, "agent_id": AGENT_ID})
    config = {
        "enabled": True,
        "intent_validation_enabled": intent_enabled,
        "cross_session_awareness_enabled": True,
    }
    captured: list = []

    def _fake_safe_print(text, file=None):
        if file is sys.stdout or file is None:
            captured.append(text)

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch("pacemaker.hook.load_config", return_value=config))
        stack.enter_context(patch("pacemaker.hook.load_state", return_value={}))
        stack.enter_context(patch("pacemaker.hook.save_state"))
        stack.enter_context(patch("pacemaker.hook.record_activity_event"))
        stack.enter_context(
            patch(
                "pacemaker.hook.display_intent_validation_guidance",
                return_value=_INTENT_GUIDANCE,
            )
        )
        stack.enter_context(
            patch(
                "pacemaker.session_registry._csa.on_subagent_start",
                return_value=csa_banner,
            )
        )
        stack.enter_context(
            patch(
                "pacemaker.session_registry.db.resolve_db_path",
                return_value=str(tmp_path / "registry.db"),
            )
        )
        stack.enter_context(
            patch("pacemaker.hook._handle_langfuse_subagent_start", return_value=None)
        )
        stack.enter_context(
            patch("sys.stdin", MagicMock(read=MagicMock(return_value=hook_data_json)))
        )
        stack.enter_context(
            patch("pacemaker.hook.safe_print", side_effect=_fake_safe_print)
        )

        import pacemaker.hook as hook_mod

        hook_mod.run_subagent_start_hook()

    return [o for o in captured if o.strip().startswith("{")]


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_emits_one_json_when_both_contexts_present(tmp_path):
    """When intent guidance AND CSA banner both fire, exactly ONE JSON object emitted."""
    outputs = _run_subagent_start(tmp_path, intent_enabled=True, csa_banner=_CSA_BANNER)
    assert len(outputs) == 1, (
        f"Expected exactly 1 JSON object when both contexts fire, "
        f"got {len(outputs)}: {outputs}"
    )


def test_single_json_contains_both_guidance_and_banner(tmp_path):
    """The one JSON object must carry both intent guidance and CSA banner in additionalContext."""
    outputs = _run_subagent_start(tmp_path, intent_enabled=True, csa_banner=_CSA_BANNER)
    assert len(outputs) == 1, f"Expected 1 JSON, got {len(outputs)}: {outputs}"

    ctx = (
        json.loads(outputs[0])
        .get("hookSpecificOutput", {})
        .get("additionalContext", "")
    )
    assert (
        _INTENT_GUIDANCE in ctx
    ), f"Intent guidance missing from additionalContext. Got: {ctx}"
    assert _CSA_BANNER in ctx, f"CSA banner missing from additionalContext. Got: {ctx}"


def test_emits_one_json_when_only_intent_guidance(tmp_path):
    """Exactly ONE JSON object when only intent guidance fires (CSA banner empty)."""
    outputs = _run_subagent_start(tmp_path, intent_enabled=True, csa_banner="")
    assert (
        len(outputs) == 1
    ), f"Expected 1 JSON when only intent guidance fires, got {len(outputs)}: {outputs}"


def test_emits_one_json_when_only_csa_banner(tmp_path):
    """Exactly ONE JSON object when only CSA banner fires (intent validation off)."""
    outputs = _run_subagent_start(
        tmp_path, intent_enabled=False, csa_banner=_CSA_BANNER
    )
    assert (
        len(outputs) == 1
    ), f"Expected 1 JSON when only CSA banner fires, got {len(outputs)}: {outputs}"


def test_emits_no_json_when_neither_fires(tmp_path):
    """No JSON emitted when intent validation is off and CSA banner is empty."""
    outputs = _run_subagent_start(tmp_path, intent_enabled=False, csa_banner="")
    assert (
        len(outputs) == 0
    ), f"Expected no JSON output when nothing fires, got: {outputs}"
