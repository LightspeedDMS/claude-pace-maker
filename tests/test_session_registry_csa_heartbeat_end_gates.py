"""
Unit tests for session_registry._csa.on_heartbeat(), on_session_end(), and feature gates.

Tests:
- on_heartbeat: updates last_seen timestamp in DB
- on_heartbeat: missing cs namespace returns without crash
- on_session_end: removes session from DB
- on_session_end: missing cs namespace returns without crash
- feature gate (parametrized over csa_enabled=False and master enabled=False):
    on_session_start: returns '', does not init namespace (no side effects)
    on_subagent_start: returns '', state unchanged (seen_agent_ids and counter unmodified)
    on_pre_tool_use: returns {}, counter unchanged in state
    on_heartbeat: returns None, last_seen unchanged in DB
    on_session_end: returns None, session still exists in DB after call
"""

import sys
import sqlite3
import time

import pytest

# ── Module paths ──────────────────────────────────────────────────────────────
MOD_CSA = "pacemaker.session_registry._csa"
MOD_REGISTRY = "pacemaker.session_registry.registry"
MOD_DB = "pacemaker.session_registry.db"
MOD_PACKAGE = "pacemaker.session_registry"

ENV_REGISTRY_PATH = "PACEMAKER_SESSION_REGISTRY_PATH"
ENV_TEST_MODE = "PACEMAKER_TEST_MODE"
TEST_MODE_ENABLED = "1"

SESSION_A = "session-aaa"
SESSION_B = "session-bbb"
AGENT_ROOT = "root"
AGENT_SUB = "subagent-xyz"
PID_A = 5001
PID_B = 5002
DANGER_CMD = "git checkout -- src/foo.py"
_INITIAL_COUNTER = 0

# ── Gate config variants ──────────────────────────────────────────────────────
_GATE_CONFIGS = [
    {"enabled": True, "cross_session_awareness_enabled": False},
    {"enabled": False, "cross_session_awareness_enabled": True},
]
_GATE_IDS = ["csa_disabled", "master_disabled"]


def _fresh_modules(monkeypatch, db_path):
    monkeypatch.setenv(ENV_REGISTRY_PATH, db_path)
    monkeypatch.setenv(ENV_TEST_MODE, TEST_MODE_ENABLED)
    for mod in (MOD_CSA, MOD_REGISTRY, MOD_DB, MOD_PACKAGE):
        sys.modules.pop(mod, None)
    import pacemaker.session_registry._csa as csa
    import pacemaker.session_registry.registry as registry
    import pacemaker.session_registry.db as db

    return csa, registry, db


def _active_config():
    return {"enabled": True, "cross_session_awareness_enabled": True}


def _make_state(workspace_root, seen=None, counter=None, session_id=SESSION_A):
    return {
        "cross_session_awareness": {
            session_id: {
                "workspace_root": workspace_root,
                "seen_agent_ids": seen if seen is not None else [AGENT_ROOT],
                "tool_use_counter": (
                    counter if counter is not None else {AGENT_ROOT: _INITIAL_COUNTER}
                ),
            }
        }
    }


def _fetch_last_seen(db_path, session_id):
    """Fetch the last_seen value for session_id directly from SQLite."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT last_seen FROM sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _session_exists(db_path, session_id):
    """Return True if session_id has a row in the sessions table."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _call_heartbeat(csa, db_path, state, config=None, session_id=SESSION_A, pid=PID_A):
    if config is None:
        config = _active_config()
    return csa.on_heartbeat(
        session_id=session_id,
        pid=pid,
        db_path=db_path,
        state=state,
        config=config,
    )


def _call_session_end(
    csa, db_path, state, config=None, session_id=SESSION_A, pid=PID_A
):
    if config is None:
        config = _active_config()
    return csa.on_session_end(
        session_id=session_id,
        pid=pid,
        db_path=db_path,
        state=state,
        config=config,
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = str(tmp_path / "registry.db")
    ws = str(tmp_path / "projectX")
    csa, registry, db = _fresh_modules(monkeypatch, db_path)
    db.init_schema(db_path)
    return csa, registry, db, db_path, ws


# ── on_heartbeat ──────────────────────────────────────────────────────────────


def test_heartbeat_updates_last_seen(env):
    """on_heartbeat updates last_seen for the session in the DB."""
    csa, registry, db, db_path, ws = env
    registry.register_session(SESSION_A, ws, PID_A, db_path)
    state = _make_state(ws)
    before = time.time()
    _call_heartbeat(csa, db_path, state)
    after = time.time()
    registry.register_session(SESSION_B, ws, PID_B, db_path)
    siblings = registry.list_siblings(ws, SESSION_B, db_path)
    row = next((s for s in siblings if s["session_id"] == SESSION_A), None)
    assert row is not None
    assert before <= row["last_seen"] <= after


def test_heartbeat_missing_namespace_no_crash(env):
    """on_heartbeat returns without crash when cs namespace is absent."""
    csa, registry, db, db_path, ws = env
    _call_heartbeat(csa, db_path, {})  # must not raise


# ── on_session_end ────────────────────────────────────────────────────────────


def test_session_end_unregisters_session(env):
    """on_session_end removes the session from the registry DB."""
    csa, registry, db, db_path, ws = env
    registry.register_session(SESSION_A, ws, PID_A, db_path)
    _call_session_end(csa, db_path, _make_state(ws))
    registry.register_session(SESSION_B, ws, PID_B, db_path)
    siblings = registry.list_siblings(ws, SESSION_B, db_path)
    assert SESSION_A not in [s["session_id"] for s in siblings]


def test_session_end_missing_namespace_no_crash(env):
    """on_session_end returns without crash when cs namespace is absent."""
    csa, registry, db, db_path, ws = env
    registry.register_session(SESSION_A, ws, PID_A, db_path)
    _call_session_end(csa, db_path, {})  # must not raise


# ── Feature gate: on_session_start ────────────────────────────────────────────


@pytest.mark.parametrize("gate_config", _GATE_CONFIGS, ids=_GATE_IDS)
def test_gate_skips_session_start(env, gate_config):
    """Disabled feature: on_session_start returns '' and does not init namespace."""
    csa, registry, db, db_path, ws = env
    state = {}
    result = csa.on_session_start(
        session_id=SESSION_A,
        source="startup",
        cwd=ws,
        pid=PID_A,
        db_path=db_path,
        state=state,
        config=gate_config,
    )
    assert result == ""
    assert "cross_session_awareness" not in state


# ── Feature gate: on_subagent_start ──────────────────────────────────────────


@pytest.mark.parametrize("gate_config", _GATE_CONFIGS, ids=_GATE_IDS)
def test_gate_skips_subagent_start(env, gate_config):
    """Disabled feature: on_subagent_start returns '' and leaves state unchanged."""
    csa, registry, db, db_path, ws = env
    state = _make_state(ws, seen=[AGENT_ROOT], counter={AGENT_ROOT: _INITIAL_COUNTER})
    result = csa.on_subagent_start(
        session_id=SESSION_A,
        agent_id=AGENT_SUB,
        pid=PID_A,
        db_path=db_path,
        state=state,
        config=gate_config,
    )
    assert result == ""
    cs = state["cross_session_awareness"][SESSION_A]
    assert AGENT_SUB not in cs["seen_agent_ids"]
    assert AGENT_SUB not in cs["tool_use_counter"]


# ── Feature gate: on_pre_tool_use ─────────────────────────────────────────────


@pytest.mark.parametrize("gate_config", _GATE_CONFIGS, ids=_GATE_IDS)
def test_gate_skips_pre_tool_use(env, gate_config):
    """Disabled feature: on_pre_tool_use returns {} and does not increment counter."""
    csa, registry, db, db_path, ws = env
    state = _make_state(ws, counter={AGENT_ROOT: _INITIAL_COUNTER})
    result = csa.on_pre_tool_use(
        session_id=SESSION_A,
        agent_id=AGENT_ROOT,
        pid=PID_A,
        tool_name="Bash",
        command=DANGER_CMD,
        db_path=db_path,
        state=state,
        config=gate_config,
    )
    assert result == {}
    assert (
        state["cross_session_awareness"][SESSION_A]["tool_use_counter"][AGENT_ROOT]
        == _INITIAL_COUNTER
    )


# ── Feature gate: on_heartbeat ────────────────────────────────────────────────


@pytest.mark.parametrize("gate_config", _GATE_CONFIGS, ids=_GATE_IDS)
def test_gate_skips_heartbeat(env, gate_config):
    """Disabled feature: on_heartbeat returns None and does not update last_seen."""
    csa, registry, db, db_path, ws = env
    registry.register_session(SESSION_A, ws, PID_A, db_path)
    last_seen_before = _fetch_last_seen(db_path, SESSION_A)
    result = _call_heartbeat(csa, db_path, _make_state(ws), config=gate_config)
    assert result is None
    last_seen_after = _fetch_last_seen(db_path, SESSION_A)
    assert last_seen_after == last_seen_before


# ── Feature gate: on_session_end ──────────────────────────────────────────────


@pytest.mark.parametrize("gate_config", _GATE_CONFIGS, ids=_GATE_IDS)
def test_gate_skips_session_end(env, gate_config):
    """Disabled feature: on_session_end returns None and session remains in DB."""
    csa, registry, db, db_path, ws = env
    registry.register_session(SESSION_A, ws, PID_A, db_path)
    result = _call_session_end(csa, db_path, _make_state(ws), config=gate_config)
    assert result is None
    assert _session_exists(db_path, SESSION_A)
