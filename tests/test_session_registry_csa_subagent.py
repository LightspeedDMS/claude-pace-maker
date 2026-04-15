"""
Unit tests for session_registry._csa.on_subagent_start().

Tests:
- new agent_id: tool_use_counter initialized to 0
- new agent_id: added to seen_agent_ids list
- no siblings: returns empty string
- with siblings: returns non-empty banner containing sibling session_id
- already-seen agent_id: returns empty banner even when siblings present
- missing cs namespace: returns "" without crash
"""

import sys
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


def _fresh_modules(monkeypatch, db_path):
    monkeypatch.setenv(ENV_REGISTRY_PATH, db_path)
    monkeypatch.setenv(ENV_TEST_MODE, TEST_MODE_ENABLED)
    for mod in (MOD_CSA, MOD_REGISTRY, MOD_DB, MOD_PACKAGE):
        sys.modules.pop(mod, None)
    import pacemaker.session_registry._csa as csa
    import pacemaker.session_registry.registry as registry
    import pacemaker.session_registry.db as db

    return csa, registry, db


def _make_config():
    return {"enabled": True, "cross_session_awareness_enabled": True}


def _make_state(workspace_root, seen=None, counter=None, session_id=SESSION_A):
    return {
        "cross_session_awareness": {
            session_id: {
                "workspace_root": workspace_root,
                "seen_agent_ids": seen if seen is not None else [AGENT_ROOT],
                "tool_use_counter": counter if counter is not None else {AGENT_ROOT: 0},
            }
        }
    }


def _call_subagent_start(
    csa, db_path, ws, state, agent_id=AGENT_SUB, session_id=SESSION_A, pid=PID_A
):
    return csa.on_subagent_start(
        session_id=session_id,
        agent_id=agent_id,
        pid=pid,
        db_path=db_path,
        state=state,
        config=_make_config(),
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = str(tmp_path / "registry.db")
    ws = str(tmp_path / "projectX")
    csa, registry, db = _fresh_modules(monkeypatch, db_path)
    db.init_schema(db_path)
    return csa, registry, db, db_path, ws


def test_new_agent_counter_initialized(env):
    """on_subagent_start initializes tool_use_counter to 0 for new agent_id."""
    csa, registry, db, db_path, ws = env
    state = _make_state(ws)
    registry.register_session(SESSION_A, ws, PID_A, db_path)
    _call_subagent_start(csa, db_path, ws, state)
    assert (
        state["cross_session_awareness"][SESSION_A]["tool_use_counter"][AGENT_SUB] == 0
    )


def test_new_agent_added_to_seen(env):
    """on_subagent_start adds the new agent_id to seen_agent_ids."""
    csa, registry, db, db_path, ws = env
    state = _make_state(ws)
    registry.register_session(SESSION_A, ws, PID_A, db_path)
    _call_subagent_start(csa, db_path, ws, state)
    assert AGENT_SUB in state["cross_session_awareness"][SESSION_A]["seen_agent_ids"]


def test_no_siblings_empty_banner(env):
    """on_subagent_start returns '' when no siblings are present."""
    csa, registry, db, db_path, ws = env
    state = _make_state(ws)
    registry.register_session(SESSION_A, ws, PID_A, db_path)
    banner = _call_subagent_start(csa, db_path, ws, state)
    assert banner == ""


def test_with_sibling_banner_contains_id(env):
    """on_subagent_start returns banner naming sibling session_id when siblings present."""
    csa, registry, db, db_path, ws = env
    registry.register_session(SESSION_B, ws, PID_B, db_path)
    state = _make_state(ws)
    registry.register_session(SESSION_A, ws, PID_A, db_path)
    banner = _call_subagent_start(csa, db_path, ws, state)
    assert SESSION_B in banner


def test_already_seen_agent_no_banner(env):
    """on_subagent_start returns '' when agent_id is already in seen_agent_ids."""
    csa, registry, db, db_path, ws = env
    registry.register_session(SESSION_B, ws, PID_B, db_path)
    state = _make_state(ws, seen=[AGENT_ROOT, AGENT_SUB])
    registry.register_session(SESSION_A, ws, PID_A, db_path)
    banner = _call_subagent_start(csa, db_path, ws, state)
    assert banner == ""


def test_missing_namespace_no_crash(env):
    """on_subagent_start returns '' without crash when cs namespace is absent."""
    csa, registry, db, db_path, ws = env
    result = _call_subagent_start(csa, db_path, ws, {})
    assert result == ""
