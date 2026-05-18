"""
Tests for subagent registration ordering and last_seen refresh.

Bug context
-----------
run_subagent_start_hook() performed CSA agent registration AFTER Langfuse
trace creation (a network call). The SubagentStart hook has a 10-second
timeout. When Langfuse was slow, Claude Code killed the hook before
register_agent() was reached, so subagents silently disappeared from the
activity display.

Fix: CSA registration now runs BEFORE Langfuse operations.

Additionally, record_action() did not update the agent's last_seen
timestamp, so subagents that ran longer than 20 minutes went stale even
though they were actively using tools.

Fix: record_action() now executes _SQL_HEARTBEAT_AGENT in the same
transaction as the action insert.
"""

import json
import sqlite3
import sys
import time
from io import StringIO

import pytest

ENV_REGISTRY_PATH = "PACEMAKER_SESSION_REGISTRY_PATH"
ENV_TEST_MODE = "PACEMAKER_TEST_MODE"
TEST_MODE_ENABLED = "1"

MOD_CSA = "pacemaker.session_registry._csa"
MOD_REGISTRY = "pacemaker.session_registry.registry"
MOD_DB = "pacemaker.session_registry.db"
MOD_PACKAGE = "pacemaker.session_registry"

SESSION_ID = "test-session-ordering"
AGENT_ID = "subagent-ordering-abc"
WORKSPACE = "/tmp/test-workspace"


def _fresh_modules(monkeypatch, db_path):
    monkeypatch.setenv(ENV_REGISTRY_PATH, db_path)
    monkeypatch.setenv(ENV_TEST_MODE, TEST_MODE_ENABLED)
    for mod in (MOD_CSA, MOD_REGISTRY, MOD_DB, MOD_PACKAGE):
        sys.modules.pop(mod, None)
    import pacemaker.session_registry._csa as csa
    import pacemaker.session_registry.registry as registry
    import pacemaker.session_registry.db as db

    return csa, registry, db


def _read_agent(db_path, agent_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def test_csa_registration_survives_langfuse_failure(tmp_path, monkeypatch):
    """Agent must be registered in session_registry.db even when Langfuse
    raises an exception. Patches _handle_langfuse_subagent_start to raise
    TimeoutError, then verifies the agent row exists after
    run_subagent_start_hook() completes."""
    db_path = str(tmp_path / "sessions.db")
    csa, registry, db = _fresh_modules(monkeypatch, db_path)

    state = {
        "cross_session_awareness": {
            SESSION_ID: {
                "workspace_root": WORKSPACE,
                "seen_agent_ids": ["root"],
                "tool_use_counter": {"root": 0},
            }
        }
    }
    config = {
        "cross_session_awareness_enabled": True,
        "enabled": True,
        "langfuse_enabled": True,
        "intent_validation_enabled": False,
    }

    db.init_schema(db.resolve_db_path())
    registry.register_agent(SESSION_ID, SESSION_ID, "root", WORKSPACE, db_path)

    hook_data = {
        "session_id": SESSION_ID,
        "agent_id": AGENT_ID,
        "agent_type": "fact-checker",
        "transcript_path": "/tmp/fake-transcript.jsonl",
    }

    import pacemaker.hook as hook

    fake_state_path = str(tmp_path / "state.json")
    fake_config_path = str(tmp_path / "config.json")

    with open(fake_state_path, "w") as f:
        json.dump(state, f)
    with open(fake_config_path, "w") as f:
        json.dump(config, f)

    monkeypatch.setattr(hook, "DEFAULT_STATE_PATH", fake_state_path)
    monkeypatch.setattr(hook, "DEFAULT_CONFIG_PATH", fake_config_path)
    monkeypatch.setattr(hook, "DEFAULT_DB_PATH", str(tmp_path / "usage.db"))

    monkeypatch.setattr("sys.stdin", StringIO(json.dumps(hook_data)))
    monkeypatch.setattr("sys.stdout", StringIO())

    def langfuse_raises(*args, **kwargs):
        raise TimeoutError("Langfuse network timeout")

    monkeypatch.setattr(hook, "_handle_langfuse_subagent_start", langfuse_raises)

    with pytest.raises(TimeoutError):
        hook.run_subagent_start_hook()

    agent = _read_agent(db_path, AGENT_ID)
    assert agent is not None, (
        "Subagent must be registered even when Langfuse fails — "
        "CSA registration must run before Langfuse"
    )
    assert agent["subagent_type"] == "fact-checker"
    assert agent["session_id"] == SESSION_ID


def test_record_action_updates_last_seen(tmp_path, monkeypatch):
    """record_action() must refresh the agent's last_seen timestamp,
    preventing subagents from going stale between heartbeats."""
    db_path = str(tmp_path / "sessions.db")
    _, registry, db = _fresh_modules(monkeypatch, db_path)

    db.init_schema(db.resolve_db_path())
    registry.register_agent(AGENT_ID, SESSION_ID, "subagent", WORKSPACE, db_path)

    agent_before = _read_agent(db_path, AGENT_ID)
    assert agent_before is not None
    initial_last_seen = agent_before["last_seen"]

    future_ts = initial_last_seen + 100.0
    registry.record_action(
        agent_id=AGENT_ID,
        tool_name="Bash",
        tool_input={"command": "echo hello"},
        ts=future_ts,
        db_path=db_path,
    )

    agent_after = _read_agent(db_path, AGENT_ID)
    assert agent_after is not None
    assert agent_after["last_seen"] == future_ts, (
        f"last_seen should be updated to {future_ts}, "
        f"got {agent_after['last_seen']}"
    )


def test_record_action_clears_ended_at(tmp_path, monkeypatch):
    """record_action() heartbeat must clear ended_at (same behavior as
    update_agent_heartbeat), keeping active agents from appearing ended."""
    db_path = str(tmp_path / "sessions.db")
    _, registry, db = _fresh_modules(monkeypatch, db_path)

    db.init_schema(db.resolve_db_path())
    registry.register_agent(AGENT_ID, SESSION_ID, "subagent", WORKSPACE, db_path)

    registry.mark_agent_ended(AGENT_ID, db_path)
    agent_ended = _read_agent(db_path, AGENT_ID)
    assert agent_ended["ended_at"] is not None

    registry.record_action(
        agent_id=AGENT_ID,
        tool_name="Read",
        tool_input={"file_path": "/tmp/test.py"},
        ts=time.time() + 200,
        db_path=db_path,
    )

    agent_revived = _read_agent(db_path, AGENT_ID)
    assert (
        agent_revived["ended_at"] is None
    ), "record_action should clear ended_at via heartbeat"


def test_record_action_no_agent_row_no_crash(tmp_path, monkeypatch):
    """record_action() on a non-existent agent_id must not crash.
    The heartbeat UPDATE is a no-op when no matching row exists."""
    db_path = str(tmp_path / "sessions.db")
    _, registry, db = _fresh_modules(monkeypatch, db_path)

    db.init_schema(db.resolve_db_path())

    registry.record_action(
        agent_id="nonexistent-agent",
        tool_name="Bash",
        tool_input={"command": "ls"},
        ts=time.time(),
        db_path=db_path,
    )
