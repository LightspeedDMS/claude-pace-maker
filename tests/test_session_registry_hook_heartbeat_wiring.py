"""
Unit tests verifying CSA on_heartbeat() is called from PostToolUse and
UserPromptSubmit hook handlers.

Spec AC7: every hook invocation must update last_seen in the registry so the
session remains visible to siblings.  These tests assert that both handlers
call on_heartbeat() by spying on the function in its source module
(pacemaker.session_registry._csa), which is the correct interception point
because hook.py imports it lazily with
  'from .session_registry._csa import on_heartbeat as csa_on_heartbeat'
inside each handler's try-block.

Tests:
- test_post_tool_use_calls_csa_heartbeat: run_hook() calls on_heartbeat
- test_user_prompt_submit_calls_csa_heartbeat: run_user_prompt_submit() calls on_heartbeat
"""

import contextlib
import json
from unittest.mock import MagicMock, patch

# ── Constants ─────────────────────────────────────────────────────────────────
SESSION_ID = "session-heartbeat-test-001"
_BASE_CONFIG = {
    "enabled": True,
    "cross_session_awareness_enabled": True,
}
_MOCK_PACING = MagicMock(
    should_delay=False,
    delay_seconds=0,
    feedback_message=None,
    tokens_used=0,
    weekly_budget=1000,
    five_hour_usage=0,
    is_limited=False,
    limit_type=None,
)


# ── Shared helpers ─────────────────────────────────────────────────────────────


def _make_csa_state(tmp_path) -> dict:
    """Build a minimal hook state dict with cross_session_awareness namespace.

    Derives workspace from tmp_path so no environment-specific path is embedded.
    """
    return {
        "session_id": SESSION_ID,
        "tool_execution_count": 0,
        "subagent_counter": 0,
        "in_subagent": False,
        "cross_session_awareness": {
            SESSION_ID: {
                "workspace_root": str(tmp_path / "repo"),
                "seen_agent_ids": ["root"],
                "tool_use_counter": {"root": 0},
            }
        },
    }


def _call_heartbeat_spy():
    """Return (calls_list, spy_fn).

    spy_fn matches on_heartbeat() signature and records each call in calls_list.
    """
    calls = []

    def _spy(session_id, pid, db_path, state, config):
        calls.append({"session_id": session_id, "pid": pid})

    return calls, _spy


def _run_under_patches(hook_fn, payload: dict, state: dict, heartbeat_spy) -> None:
    """Invoke hook_fn with all dependencies patched.

    Uses contextlib.ExitStack for clean lifecycle management.
    Patches handle_user_prompt in all calls (no-op for PostToolUse; required for
    UserPromptSubmit) so both tests share identical setup.
    Heartbeat is spied via the source module since hook.py imports lazily.
    """
    hook_data_json = json.dumps(payload)

    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch("pacemaker.hook.load_config", return_value=dict(_BASE_CONFIG))
        )
        stack.enter_context(patch("pacemaker.hook.load_state", return_value=state))
        stack.enter_context(patch("pacemaker.hook.save_state"))
        stack.enter_context(patch("pacemaker.hook.database.initialize_database"))
        stack.enter_context(
            patch(
                "pacemaker.hook.pacing_engine.run_pacing_check",
                return_value=_MOCK_PACING,
            )
        )
        stack.enter_context(patch("pacemaker.hook.record_activity_event"))
        stack.enter_context(patch("pacemaker.hook.record_governance_event"))
        stack.enter_context(patch("pacemaker.hook.cleanup_old_activity"))
        stack.enter_context(patch("pacemaker.hook.cleanup_old_governance_events"))
        stack.enter_context(
            patch(
                "pacemaker.hook.user_commands.handle_user_prompt",
                return_value={"intercepted": False, "output": ""},
            )
        )
        stack.enter_context(
            patch("sys.stdin", MagicMock(read=MagicMock(return_value=hook_data_json)))
        )
        stack.enter_context(patch("sys.stdout", MagicMock()))
        stack.enter_context(
            patch(
                "pacemaker.session_registry._csa.on_heartbeat",
                side_effect=heartbeat_spy,
            )
        )

        try:
            hook_fn()
        except SystemExit:
            pass


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_post_tool_use_calls_csa_heartbeat(monkeypatch, tmp_path):
    """run_hook() (PostToolUse) must call on_heartbeat() when CSA state is present."""
    monkeypatch.setenv("PACEMAKER_SESSION_REGISTRY_PATH", str(tmp_path / "registry.db"))

    state = _make_csa_state(tmp_path)
    heartbeat_calls, heartbeat_spy = _call_heartbeat_spy()

    payload = {
        "session_id": SESSION_ID,
        "tool_name": "Write",
        "tool_input": {},
        "tool_response": "ok",
        "transcript_path": str(tmp_path / "transcript.jsonl"),
    }

    import pacemaker.hook as hook_mod

    _run_under_patches(hook_mod.run_hook, payload, state, heartbeat_spy)

    assert heartbeat_calls, (
        "run_hook() (PostToolUse) did not call on_heartbeat(). "
        "CSA heartbeat must fire on every PostToolUse to keep the session visible to siblings."
    )


def test_user_prompt_submit_calls_csa_heartbeat(monkeypatch, tmp_path):
    """run_user_prompt_submit() (UserPromptSubmit) must call on_heartbeat() when CSA state is present."""
    monkeypatch.setenv("PACEMAKER_SESSION_REGISTRY_PATH", str(tmp_path / "registry.db"))

    state = _make_csa_state(tmp_path)
    heartbeat_calls, heartbeat_spy = _call_heartbeat_spy()

    payload = {
        "session_id": SESSION_ID,
        "prompt": "Hello Claude",
        "transcript_path": str(tmp_path / "transcript.jsonl"),
    }

    import pacemaker.hook as hook_mod

    _run_under_patches(hook_mod.run_user_prompt_submit, payload, state, heartbeat_spy)

    assert heartbeat_calls, (
        "run_user_prompt_submit() (UserPromptSubmit) did not call on_heartbeat(). "
        "CSA heartbeat must fire on every UserPromptSubmit to keep the session visible to siblings."
    )
