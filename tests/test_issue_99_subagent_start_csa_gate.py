"""
Unit tests verifying hook.run_subagent_start_hook()'s "Early CSA agent
registration" block correctly gates its session_registry.db write behind the
MASTER `enabled` switch, not just `cross_session_awareness_enabled` (issue
#99).

The system under test is hook.py's SubagentStart "Early CSA agent
registration" block (immediately before the Langfuse call). As of this test
file's creation, that block calls
pacemaker.session_registry.registry.register_agent() DIRECTLY, gated ONLY on
`config.get("cross_session_awareness_enabled", True)` — it never checks the
master `enabled` flag at all. That is exactly the issue #99 bug (mirrors
issue #97, one hook over): with `enabled: false`, a subagent's `agents` row
is still written. These tests assert the desired gated behavior (routing
through a config-checked _csa entry point that uses `_is_enabled()`, which
checks BOTH flags) and are expected to be RED for the enabled=False case
until hook.py is fixed to call a gated session_registry._csa entry point
instead of the registry module directly.

Every session_registry.db read/write these tests exercise is 100% real
SQLite — nothing in the CSA/registry code path is mocked anywhere in this
file, matching the existing sibling precedent in
test_issue_97_post_tool_use_csa_gate.py.

Only genuine external dependencies of run_subagent_start_hook() are stubbed:
- load_config/load_state/save_state: config/state source, not under test.
- _handle_langfuse_subagent_start: makes real external Langfuse API calls,
  entirely unrelated to the CSA registry code path under test.
- record_activity_event: writes to usage.db, unrelated to this DB.
- sys.stdin/sys.stdout: no real terminal is attached under pytest.
- pacemaker.session_registry.db.resolve_db_path: points the real registry
  code at this test's real (tmp_path) SQLite file instead of the production
  path — the DB itself is untouched/unmocked.

Tests:
- test_master_disabled_writes_zero_rows: regression test for the bug —
  enabled=False (cross_session_awareness_enabled defaulting True) must
  produce ZERO agents rows for the subagent's agent_id.
- test_csa_flag_disabled_writes_zero_rows: enabled=True,
  cross_session_awareness_enabled=False must ALSO produce zero rows (this
  should already pass pre-fix — the old inline check covered this one flag).
- test_both_enabled_writes_row: enabled=True AND
  cross_session_awareness_enabled=True must still write exactly one row
  (regression guard — the working case must not break).
"""

import contextlib
import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

SESSION_ID = "session-issue99-001"
AGENT_ID = "agent-issue99-001"


def _base_config(**overrides):
    cfg = {"enabled": True, "cross_session_awareness_enabled": True}
    cfg.update(overrides)
    return cfg


def _make_state(workspace_root: str):
    return {
        "subagent_counter": 0,
        "in_subagent": False,
        "cross_session_awareness": {
            SESSION_ID: {
                "workspace_root": workspace_root,
                "seen_agent_ids": ["root"],
                "tool_use_counter": {"root": 0},
            }
        },
    }


def _payload():
    return {
        "session_id": SESSION_ID,
        "agent_id": AGENT_ID,
        "agent_type": "tdd-engineer",
    }


def _run_subagent_start(config: dict, tmp_path, db_path: str):
    """Invoke the real hook.run_subagent_start_hook() with config."""
    import pacemaker.hook as hook_mod

    hook_data_json = json.dumps(_payload())
    workspace_root = str(tmp_path / "repo")
    state = _make_state(workspace_root)

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch("pacemaker.hook.load_config", return_value=config))
        stack.enter_context(patch("pacemaker.hook.load_state", return_value=state))
        stack.enter_context(patch("pacemaker.hook.save_state"))
        stack.enter_context(patch("pacemaker.hook.record_activity_event"))
        stack.enter_context(
            patch("pacemaker.hook._handle_langfuse_subagent_start", return_value=None)
        )
        stack.enter_context(
            patch(
                "pacemaker.session_registry.db.resolve_db_path",
                return_value=db_path,
            )
        )
        stack.enter_context(
            patch("sys.stdin", MagicMock(read=MagicMock(return_value=hook_data_json)))
        )
        stack.enter_context(patch("sys.stdout", MagicMock()))

        hook_mod.run_subagent_start_hook()


def _query_agents_count(db_path, agent_id):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        return row[0] if row is not None else 0
    finally:
        conn.close()


@pytest.fixture
def registry_env(tmp_path, monkeypatch):
    """Real session_registry.db, schema initialized, no pre-existing rows."""
    import pacemaker.session_registry.db as db_mod

    db_path = str(tmp_path / "session_registry.db")
    monkeypatch.setenv("PACEMAKER_SESSION_REGISTRY_PATH", db_path)
    db_mod.init_schema(db_path)
    return db_path


def test_master_disabled_writes_zero_rows(registry_env, tmp_path):
    """enabled=False must produce ZERO agents rows regardless of
    cross_session_awareness_enabled (issue #99 regression)."""
    db_path = registry_env
    _run_subagent_start(
        _base_config(enabled=False, cross_session_awareness_enabled=True),
        tmp_path,
        db_path,
    )

    assert _query_agents_count(db_path, AGENT_ID) == 0, (
        "SubagentStart must write ZERO agents rows when the master 'enabled' "
        "switch is False (issue #99 regression)"
    )


def test_csa_flag_disabled_writes_zero_rows(registry_env, tmp_path):
    """enabled=True, cross_session_awareness_enabled=False must also produce
    zero rows."""
    db_path = registry_env
    _run_subagent_start(
        _base_config(enabled=True, cross_session_awareness_enabled=False),
        tmp_path,
        db_path,
    )

    assert _query_agents_count(db_path, AGENT_ID) == 0, (
        "SubagentStart must write ZERO agents rows when "
        "cross_session_awareness_enabled is False"
    )


def test_both_enabled_writes_row(registry_env, tmp_path):
    """enabled=True AND cross_session_awareness_enabled=True must still
    write exactly one agents row for the subagent (regression guard)."""
    db_path = registry_env
    _run_subagent_start(
        _base_config(enabled=True, cross_session_awareness_enabled=True),
        tmp_path,
        db_path,
    )

    assert _query_agents_count(db_path, AGENT_ID) == 1, (
        "SubagentStart must write exactly one agents row when both gates " "are enabled"
    )
