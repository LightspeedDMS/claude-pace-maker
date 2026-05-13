"""Tests for agent activity wiring in _csa.py hooks.

Tests:
- test_session_start_registers_root_agent: on_session_start registers root agent row in agents table
- test_subagent_start_registers_subagent: on_subagent_start registers subagent row with subagent_type
- test_session_end_marks_agent_ended: on_session_end sets ended_at for root agent
- test_subagent_stop_marks_agent_ended: on_subagent_stop sets ended_at for subagent
- test_subagent_start_without_subagent_type: subagent_type=None yields NULL in agents table
"""

import sys
import pytest

# ── Module paths ──────────────────────────────────────────────────────────────
MOD_CSA = "pacemaker.session_registry._csa"
MOD_REGISTRY = "pacemaker.session_registry.registry"
MOD_DB = "pacemaker.session_registry.db"
MOD_PACKAGE = "pacemaker.session_registry"

# ── Env vars ──────────────────────────────────────────────────────────────────
ENV_REGISTRY_PATH = "PACEMAKER_SESSION_REGISTRY_PATH"
ENV_TEST_MODE = "PACEMAKER_TEST_MODE"
TEST_MODE_ENABLED = "1"

# ── Data constants ────────────────────────────────────────────────────────────
SESSION_A = "session-aaa"
AGENT_SUB = "subagent-xyz"
AGENT_ROOT = "root"
PID_A = 5001
SUBAGENT_TYPE = "tdd-engineer"


def _fresh_modules(monkeypatch, db_path):
    monkeypatch.setenv(ENV_REGISTRY_PATH, db_path)
    monkeypatch.setenv(ENV_TEST_MODE, TEST_MODE_ENABLED)
    for mod in (MOD_CSA, MOD_REGISTRY, MOD_DB, MOD_PACKAGE):
        sys.modules.pop(mod, None)
    import pacemaker.session_registry._csa as csa
    import pacemaker.session_registry.registry as registry
    import pacemaker.session_registry.db as db

    return csa, registry, db


def _make_config(enabled=True, csa_enabled=True):
    return {"enabled": enabled, "cross_session_awareness_enabled": csa_enabled}


def _make_state_with_session(workspace_root, session_id=SESSION_A):
    """Return state with a pre-populated CSA namespace for the given session."""
    return {
        "cross_session_awareness": {
            session_id: {
                "workspace_root": workspace_root,
                "seen_agent_ids": [AGENT_ROOT],
                "tool_use_counter": {AGENT_ROOT: 0},
            }
        }
    }


def _query_agents(db_module, db_path, agent_id):
    """Return the agents row for agent_id, or None if not present."""
    conn = db_module.get_connection(db_path)
    try:
        cursor = conn.execute(
            "SELECT agent_id, session_id, role, subagent_type, ended_at "
            "FROM agents WHERE agent_id = ?",
            (agent_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "agent_id": row[0],
            "session_id": row[1],
            "role": row[2],
            "subagent_type": row[3],
            "ended_at": row[4],
        }
    finally:
        conn.close()


# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = str(tmp_path / "registry.db")
    ws = str(tmp_path / "projectX")
    csa, registry, db = _fresh_modules(monkeypatch, db_path)
    db.init_schema(db_path)
    return csa, registry, db, db_path, ws


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_session_start_registers_root_agent(env):
    """on_session_start: agents table has a 'root' row with correct session_id."""
    csa, registry, db, db_path, ws = env
    state = {}
    config = _make_config()
    csa.on_session_start(
        session_id=SESSION_A,
        source="startup",
        cwd=ws,
        pid=PID_A,
        db_path=db_path,
        state=state,
        config=config,
    )
    row = _query_agents(db, db_path, SESSION_A)
    assert row is not None, "Root agent row not found in agents table"
    assert row["session_id"] == SESSION_A
    assert row["role"] == "root"
    assert row["ended_at"] is None


def test_subagent_start_registers_subagent(env):
    """on_subagent_start: agents table has a 'subagent' row with correct subagent_type."""
    csa, registry, db, db_path, ws = env
    # Pre-register session so CSA namespace is populated
    registry.register_session(SESSION_A, ws, PID_A, db_path)
    state = _make_state_with_session(ws)
    config = _make_config()
    csa.on_subagent_start(
        session_id=SESSION_A,
        agent_id=AGENT_SUB,
        pid=PID_A,
        db_path=db_path,
        state=state,
        config=config,
        subagent_type=SUBAGENT_TYPE,
    )
    row = _query_agents(db, db_path, AGENT_SUB)
    assert row is not None, "Subagent row not found in agents table"
    assert row["session_id"] == SESSION_A
    assert row["role"] == "subagent"
    assert row["subagent_type"] == SUBAGENT_TYPE
    assert row["ended_at"] is None


def test_session_end_marks_agent_ended(env):
    """on_session_end: root agent row gets ended_at set (not None)."""
    csa, registry, db, db_path, ws = env
    # First start the session to register the root agent
    state = {}
    config = _make_config()
    csa.on_session_start(
        session_id=SESSION_A,
        source="startup",
        cwd=ws,
        pid=PID_A,
        db_path=db_path,
        state=state,
        config=config,
    )
    # Verify root agent was registered
    row_before = _query_agents(db, db_path, SESSION_A)
    assert row_before is not None, "Root agent must exist before session_end"
    assert row_before["ended_at"] is None

    # Now end the session
    csa.on_session_end(
        session_id=SESSION_A,
        pid=PID_A,
        db_path=db_path,
        state=state,
        config=config,
    )
    row_after = _query_agents(db, db_path, SESSION_A)
    assert row_after is not None, "Root agent row should still exist after session_end"
    assert row_after["ended_at"] is not None, "ended_at should be set after session_end"


def test_subagent_stop_marks_agent_ended(env):
    """on_subagent_stop: subagent row gets ended_at set (not None)."""
    csa, registry, db, db_path, ws = env
    # Register session and start subagent
    registry.register_session(SESSION_A, ws, PID_A, db_path)
    state = _make_state_with_session(ws)
    config = _make_config()
    csa.on_subagent_start(
        session_id=SESSION_A,
        agent_id=AGENT_SUB,
        pid=PID_A,
        db_path=db_path,
        state=state,
        config=config,
        subagent_type=SUBAGENT_TYPE,
    )
    # Verify subagent was registered
    row_before = _query_agents(db, db_path, AGENT_SUB)
    assert row_before is not None, "Subagent must exist before subagent_stop"
    assert row_before["ended_at"] is None

    # Now stop the subagent
    csa.on_subagent_stop(
        session_id=SESSION_A,
        agent_id=AGENT_SUB,
        db_path=db_path,
        state=state,
        config=config,
    )
    row_after = _query_agents(db, db_path, AGENT_SUB)
    assert row_after is not None, "Subagent row should still exist after subagent_stop"
    assert (
        row_after["ended_at"] is not None
    ), "ended_at should be set after subagent_stop"


def test_subagent_start_without_subagent_type(env):
    """on_subagent_start with subagent_type=None: agents row has NULL subagent_type."""
    csa, registry, db, db_path, ws = env
    registry.register_session(SESSION_A, ws, PID_A, db_path)
    state = _make_state_with_session(ws)
    config = _make_config()
    csa.on_subagent_start(
        session_id=SESSION_A,
        agent_id=AGENT_SUB,
        pid=PID_A,
        db_path=db_path,
        state=state,
        config=config,
        subagent_type=None,
    )
    row = _query_agents(db, db_path, AGENT_SUB)
    assert row is not None, "Subagent row not found"
    assert (
        row["subagent_type"] is None
    ), "subagent_type should be NULL when not provided"
