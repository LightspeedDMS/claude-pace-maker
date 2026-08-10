"""
Unit tests verifying hook.run_hook() (PostToolUse) correctly gates its
"record tool action for activity trail" block behind
cross_session_awareness_enabled (issue #97).

The system under test is hook.py's PostToolUse "record tool action for
activity trail" block. As of this test file's creation, that block calls
pacemaker.session_registry.registry.record_action() and
update_agent_heartbeat() DIRECTLY, with no config gate at all — that is
exactly the issue #97 bug (see hook.py around line 1046). These tests assert
the desired gated behavior (routing through a config-checked entry point) and
are expected to be RED until hook.py is fixed to call a gated
session_registry._csa entry point instead of the registry module directly.

Every session_registry.db read/write these tests exercise is 100% real
SQLite — nothing in the CSA/registry code path is mocked anywhere in this
file, matching the existing sibling precedent in
test_session_registry_hook_heartbeat_wiring.py.

Only genuine external dependencies of run_hook() are stubbed, per the
"unit tests must not mock the system under test — only external
dependencies" clean-code rule:
- load_config/load_state/save_state: config/state source, not under test.
- pacing_engine.run_pacing_check: makes real external usage-API calls,
  entirely unrelated to the CSA registry code path under test — the same
  boundary already mocked in test_session_registry_hook_heartbeat_wiring.py.
- sys.stdin/sys.stdout: no real terminal is attached under pytest.

record_activity_event, record_governance_event, cleanup_old_activity,
cleanup_old_governance_events and database.initialize_database are NOT
mocked — they run for real against the fake usage.db that
tests/conftest.py's autouse `_guard_production_db` fixture already redirects
hook.DEFAULT_DB_PATH to and initializes.

Tests:
- test_gate_off_writes_zero_rows_and_does_not_advance_last_seen: regression
  test for the bug — cross_session_awareness_enabled=False must produce ZERO
  agent_actions rows and an unchanged agents.last_seen.
- test_gate_on_writes_row_and_advances_last_seen: proves the fix doesn't
  break the feature when the gate is on.
- test_gate_absent_matches_is_enabled_default: config missing the key
  entirely must behave exactly like _is_enabled()'s own actual default.
- test_registry_failure_is_non_fatal: run_hook() completes normally even
  when the registry DB genuinely cannot be opened (a real failure — the
  session-registry path is pointed at a directory, which sqlite3 cannot
  open as a database — not a mocked exception).
"""

import contextlib
import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

SESSION_ID = "session-issue97-001"
PINNED_LAST_SEEN = 1000.0

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


def _base_config(**overrides):
    cfg = {"enabled": True, "cross_session_awareness_enabled": True}
    cfg.update(overrides)
    return cfg


def _make_state():
    return {
        "session_id": SESSION_ID,
        "tool_execution_count": 0,
        "subagent_counter": 0,
        "in_subagent": False,
    }


def _payload(tmp_path):
    return {
        "session_id": SESSION_ID,
        "tool_name": "Write",
        "tool_input": {"file_path": "/tmp/x.py"},
        "tool_response": "ok",
        "transcript_path": str(tmp_path / "transcript.jsonl"),
    }


def _run_post_tool_use(config: dict, tmp_path):
    """Invoke the real hook.run_hook() PostToolUse handler with config."""
    import pacemaker.hook as hook_mod

    hook_data_json = json.dumps(_payload(tmp_path))
    state = _make_state()

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch("pacemaker.hook.load_config", return_value=config))
        stack.enter_context(patch("pacemaker.hook.load_state", return_value=state))
        stack.enter_context(patch("pacemaker.hook.save_state"))
        stack.enter_context(
            patch(
                "pacemaker.hook.pacing_engine.run_pacing_check",
                return_value=_MOCK_PACING,
            )
        )
        stack.enter_context(
            patch("sys.stdin", MagicMock(read=MagicMock(return_value=hook_data_json)))
        )
        stack.enter_context(patch("sys.stdout", MagicMock()))

        # run_hook() (PostToolUse) never calls sys.exit() in its own body —
        # it returns a bool — so no SystemExit handling is needed here.
        feedback_provided = hook_mod.run_hook()
        # Its concrete True/False value is orthogonal to the CSA gate under
        # test in this file, but the return value is checked, not discarded.
        assert isinstance(feedback_provided, bool)


def _query_scalar(db_path, sql, params):
    """Open db_path, run sql with params, return fetchone()[0] (or None)."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row is not None else None
    finally:
        conn.close()


def _query_agent_actions_count(db_path, agent_id):
    return _query_scalar(
        db_path,
        "SELECT COUNT(*) FROM agent_actions WHERE agent_id = ?",
        (agent_id,),
    )


def _query_last_seen(db_path, agent_id):
    return _query_scalar(
        db_path, "SELECT last_seen FROM agents WHERE agent_id = ?", (agent_id,)
    )


@pytest.fixture
def registry_env(tmp_path, monkeypatch):
    """Real session_registry.db with a pre-registered root agent row.

    Pre-registering the agent avoids the agent_actions FK-to-agents
    constraint masking gate behaviour as a foreign-key failure — without
    an existing agents row, record_action's INSERT would fail regardless
    of the CSA gate, making the gate-ON test a false positive.
    """
    import pacemaker.session_registry.db as db_mod
    import pacemaker.session_registry.registry as registry_mod

    db_path = str(tmp_path / "session_registry.db")
    monkeypatch.setenv("PACEMAKER_SESSION_REGISTRY_PATH", db_path)
    db_mod.init_schema(db_path)
    ws = str(tmp_path / "repo")
    registry_mod.register_agent(SESSION_ID, SESSION_ID, "root", ws, db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE agents SET last_seen = ? WHERE agent_id = ?",
            (PINNED_LAST_SEEN, SESSION_ID),
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_gate_off_writes_zero_rows_and_does_not_advance_last_seen(
    registry_env, tmp_path
):
    db_path = registry_env
    _run_post_tool_use(_base_config(cross_session_awareness_enabled=False), tmp_path)

    assert _query_agent_actions_count(db_path, SESSION_ID) == 0, (
        "PostToolUse must write ZERO agent_actions rows when "
        "cross_session_awareness_enabled is False (issue #97 regression)"
    )
    assert _query_last_seen(db_path, SESSION_ID) == PINNED_LAST_SEEN, (
        "agents.last_seen must NOT advance when "
        "cross_session_awareness_enabled is False (issue #97 regression)"
    )


def test_gate_on_writes_row_and_advances_last_seen(registry_env, tmp_path):
    db_path = registry_env
    _run_post_tool_use(_base_config(cross_session_awareness_enabled=True), tmp_path)

    assert (
        _query_agent_actions_count(db_path, SESSION_ID) == 1
    ), "PostToolUse must write an agent_actions row when the CSA gate is ON"
    assert (
        _query_last_seen(db_path, SESSION_ID) > PINNED_LAST_SEEN
    ), "agents.last_seen must advance when the CSA gate is ON"


def test_gate_absent_matches_is_enabled_default(registry_env, tmp_path):
    """config missing 'cross_session_awareness_enabled' entirely must match
    _is_enabled()'s own actual default — verified directly, not assumed."""
    from pacemaker.session_registry._csa import _is_enabled

    config_without_key = {"enabled": True}
    default_is_enabled = _is_enabled(config_without_key)
    assert default_is_enabled is True, (
        "_is_enabled() default changed from what this test assumes — update "
        "this test's expectations to match the real default, do not silently "
        "assume True"
    )

    db_path = registry_env
    _run_post_tool_use(config_without_key, tmp_path)

    assert _query_agent_actions_count(db_path, SESSION_ID) == 1, (
        "Missing cross_session_awareness_enabled key must default to enabled, "
        "matching _is_enabled()'s own default"
    )
    assert _query_last_seen(db_path, SESSION_ID) > PINNED_LAST_SEEN


def test_registry_failure_is_non_fatal(registry_env, tmp_path, monkeypatch):
    """A real registry-open failure during the record-action block must not
    crash PostToolUse. Uses a genuine failure condition — the session-registry
    path is pointed at a directory, which sqlite3 cannot open as a database —
    rather than mocking the registry call, so the existing try/except +
    log_warning fail-open behaviour is proven against a real error, not a
    simulated one."""
    good_db_path = registry_env
    broken_path = tmp_path / "not_a_real_db_file"
    broken_path.mkdir()
    monkeypatch.setenv("PACEMAKER_SESSION_REGISTRY_PATH", str(broken_path))

    # Must not raise despite the real DB-open failure inside record_action.
    _run_post_tool_use(_base_config(cross_session_awareness_enabled=True), tmp_path)

    # The original, valid DB (from registry_env) must remain untouched.
    assert _query_agent_actions_count(good_db_path, SESSION_ID) == 0
