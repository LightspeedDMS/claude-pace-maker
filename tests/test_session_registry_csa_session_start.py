"""
Unit tests for session_registry._csa.on_session_start().

Tests (parametrized across source variants):
- startup/resume: namespace initialized with required keys
- startup/resume: empty banner when no siblings
- startup/resume: banner contains sibling session_id when sibling present
- startup/resume: session registered in DB after call
- clear/compact: tool_use_counter reset to 0 for all agents
- clear/compact: seen_agent_ids preserved
- clear/compact: empty banner even when sibling present
- clear/compact: missing cs namespace returns "" without crash
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
SESSION_B = "session-bbb"
AGENT_ROOT = "root"
AGENT_SUB = "subagent-xyz"
PID_A = 5001
PID_B = 5002

# ── Source variant groups ─────────────────────────────────────────────────────
FRESH_SOURCES = ["startup", "resume"]
RESET_SOURCES = ["clear", "compact"]


# ── Module-level helpers ──────────────────────────────────────────────────────


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


def _start_session(
    csa, db_path, ws, state, source, session_id=SESSION_A, pid=PID_A, config=None
):
    """Invoke csa.on_session_start with standard parameters."""
    if config is None:
        config = _make_config()
    return csa.on_session_start(
        session_id=session_id,
        source=source,
        cwd=ws,
        pid=pid,
        db_path=db_path,
        state=state,
        config=config,
    )


def _assert_registers_session(registry, db_path, ws, registered_id):
    """Assert registered_id appears as sibling when queried by SESSION_B."""
    registry.register_session(SESSION_B, ws, PID_B, db_path)
    siblings = registry.list_siblings(ws, SESSION_B, db_path)
    assert registered_id in [s["session_id"] for s in siblings]


# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = str(tmp_path / "registry.db")
    ws = str(tmp_path / "projectX")
    csa, registry, db = _fresh_modules(monkeypatch, db_path)
    db.init_schema(db_path)
    return csa, registry, db, db_path, ws


# ── startup / resume parametrized tests ──────────────────────────────────────


@pytest.mark.parametrize("source", FRESH_SOURCES)
def test_fresh_source_initializes_namespace(env, source):
    """startup/resume: cross_session_awareness namespace with required keys is created."""
    csa, registry, db, db_path, ws = env
    state = {}
    _start_session(csa, db_path, ws, state, source)
    assert "cross_session_awareness" in state
    ns = state["cross_session_awareness"]
    assert (
        SESSION_A in ns
    ), f"Expected session key {SESSION_A!r} in cross_session_awareness, got: {list(ns.keys())}"
    cs = ns[SESSION_A]
    assert "workspace_root" in cs
    assert "seen_agent_ids" in cs
    assert "tool_use_counter" in cs


@pytest.mark.parametrize("source", FRESH_SOURCES)
def test_fresh_source_no_siblings_empty_banner(env, source):
    """startup/resume: returns '' when no siblings registered."""
    csa, registry, db, db_path, ws = env
    banner = _start_session(csa, db_path, ws, {}, source)
    assert banner == ""


@pytest.mark.parametrize("source", FRESH_SOURCES)
def test_fresh_source_sibling_banner_contains_id(env, source):
    """startup/resume: banner contains sibling session_id when sibling present."""
    csa, registry, db, db_path, ws = env
    registry.register_session(SESSION_B, ws, PID_B, db_path)
    banner = _start_session(csa, db_path, ws, {}, source)
    assert SESSION_B in banner


@pytest.mark.parametrize("source", FRESH_SOURCES)
def test_fresh_source_registers_in_db(env, source):
    """startup/resume: calling session is registered in the DB."""
    csa, registry, db, db_path, ws = env
    _start_session(csa, db_path, ws, {}, source)
    _assert_registers_session(registry, db_path, ws, SESSION_A)


# ── clear / compact parametrized tests ───────────────────────────────────────


@pytest.mark.parametrize("source", RESET_SOURCES)
def test_reset_source_resets_counters(env, source):
    """clear/compact: all tool_use_counter values reset to 0."""
    csa, registry, db, db_path, ws = env
    state = _make_state(ws, counter={AGENT_ROOT: 7, AGENT_SUB: 3})
    registry.register_session(SESSION_A, ws, PID_A, db_path)
    _start_session(csa, db_path, ws, state, source)
    for val in state["cross_session_awareness"][SESSION_A]["tool_use_counter"].values():
        assert val == 0


@pytest.mark.parametrize("source", RESET_SOURCES)
def test_reset_source_preserves_seen_agent_ids(env, source):
    """clear/compact: seen_agent_ids list is preserved unchanged."""
    csa, registry, db, db_path, ws = env
    state = _make_state(ws, seen=[AGENT_ROOT, AGENT_SUB])
    registry.register_session(SESSION_A, ws, PID_A, db_path)
    _start_session(csa, db_path, ws, state, source)
    seen = state["cross_session_awareness"][SESSION_A]["seen_agent_ids"]
    assert AGENT_ROOT in seen
    assert AGENT_SUB in seen


@pytest.mark.parametrize("source", RESET_SOURCES)
def test_reset_source_no_banner_with_sibling(env, source):
    """clear/compact: no banner emitted even when siblings are present."""
    csa, registry, db, db_path, ws = env
    registry.register_session(SESSION_B, ws, PID_B, db_path)
    registry.register_session(SESSION_A, ws, PID_A, db_path)
    state = _make_state(ws)
    banner = _start_session(csa, db_path, ws, state, source)
    assert banner == ""


@pytest.mark.parametrize("source", RESET_SOURCES)
def test_reset_source_missing_namespace_no_crash(env, source):
    """clear/compact: returns '' without crash when cs namespace absent."""
    csa, registry, db, db_path, ws = env
    result = _start_session(csa, db_path, ws, {}, source)
    assert result == ""
