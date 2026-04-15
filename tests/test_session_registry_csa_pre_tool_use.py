"""
Unit tests for session_registry._csa.on_pre_tool_use().

Tests:
- counter increment: tool_use_counter incremented by 1 per call
- no reminder: calls 1-4 return periodic_reminder=""
- reminder with siblings: 5th call returns non-empty periodic_reminder
- no reminder without siblings: 5th call returns periodic_reminder="" when alone
- danger_bash_warning with siblings: Bash danger command returns non-empty warning containing sibling id
- no danger_bash_warning without siblings: returns danger_bash_warning=""
- missing cs namespace: returns {} without crash
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
PID_A = 5001
PID_B = 5002
PERIODIC_INTERVAL = 5
DANGER_CMD = "git checkout -- src/foo.py"


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


def _make_state(workspace_root, session_id=SESSION_A):
    return {
        "cross_session_awareness": {
            session_id: {
                "workspace_root": workspace_root,
                "seen_agent_ids": [AGENT_ROOT],
                "tool_use_counter": {AGENT_ROOT: 0},
            }
        }
    }


def _call_pre_tool(
    csa,
    db_path,
    state,
    tool_name="Write",
    command=None,
    session_id=SESSION_A,
    agent_id=AGENT_ROOT,
    pid=PID_A,
):
    return csa.on_pre_tool_use(
        session_id=session_id,
        agent_id=agent_id,
        pid=pid,
        tool_name=tool_name,
        command=command,
        db_path=db_path,
        state=state,
        config=_make_config(),
    )


def _call_pre_tool_n_times(csa, db_path, state, n, tool_name="Write", command=None):
    """Call on_pre_tool_use n times and return the last result."""
    result = {}
    for _ in range(n):
        result = _call_pre_tool(
            csa, db_path, state, tool_name=tool_name, command=command
        )
    return result


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = str(tmp_path / "registry.db")
    ws = str(tmp_path / "projectX")
    csa, registry, db = _fresh_modules(monkeypatch, db_path)
    db.init_schema(db_path)
    return csa, registry, db, db_path, ws


def test_increments_tool_use_counter(env):
    """on_pre_tool_use increments tool_use_counter for the agent by 1."""
    csa, registry, db, db_path, ws = env
    state = _make_state(ws)
    registry.register_session(SESSION_A, ws, PID_A, db_path)
    _call_pre_tool(csa, db_path, state)
    assert (
        state["cross_session_awareness"][SESSION_A]["tool_use_counter"][AGENT_ROOT] == 1
    )


def test_no_reminder_before_fifth_call(env):
    """on_pre_tool_use returns periodic_reminder='' for calls 1 through 4."""
    csa, registry, db, db_path, ws = env
    registry.register_session(SESSION_B, ws, PID_B, db_path)
    state = _make_state(ws)
    registry.register_session(SESSION_A, ws, PID_A, db_path)
    result = _call_pre_tool_n_times(csa, db_path, state, n=4)
    assert result.get("periodic_reminder", "") == ""


def test_reminder_on_fifth_call_with_sibling(env):
    """on_pre_tool_use returns non-empty periodic_reminder on 5th call when siblings present."""
    csa, registry, db, db_path, ws = env
    registry.register_session(SESSION_B, ws, PID_B, db_path)
    state = _make_state(ws)
    registry.register_session(SESSION_A, ws, PID_A, db_path)
    result = _call_pre_tool_n_times(csa, db_path, state, n=PERIODIC_INTERVAL)
    assert result.get("periodic_reminder", "") != ""


def test_no_reminder_on_fifth_call_without_sibling(env):
    """on_pre_tool_use returns periodic_reminder='' on 5th call when no siblings."""
    csa, registry, db, db_path, ws = env
    state = _make_state(ws)
    registry.register_session(SESSION_A, ws, PID_A, db_path)
    result = _call_pre_tool_n_times(csa, db_path, state, n=PERIODIC_INTERVAL)
    assert result.get("periodic_reminder", "") == ""


def test_danger_bash_warning_with_sibling(env):
    """on_pre_tool_use returns danger_bash_warning containing sibling id for danger Bash command."""
    csa, registry, db, db_path, ws = env
    registry.register_session(SESSION_B, ws, PID_B, db_path)
    state = _make_state(ws)
    registry.register_session(SESSION_A, ws, PID_A, db_path)
    result = _call_pre_tool(csa, db_path, state, tool_name="Bash", command=DANGER_CMD)
    assert result.get("danger_bash_warning", "") != ""
    assert SESSION_B in result.get("danger_bash_warning", "")


def test_no_danger_bash_warning_without_sibling(env):
    """on_pre_tool_use returns danger_bash_warning='' when no siblings present."""
    csa, registry, db, db_path, ws = env
    state = _make_state(ws)
    registry.register_session(SESSION_A, ws, PID_A, db_path)
    result = _call_pre_tool(csa, db_path, state, tool_name="Bash", command=DANGER_CMD)
    assert result.get("danger_bash_warning", "") == ""


def test_missing_namespace_no_crash(env):
    """on_pre_tool_use returns {} without crash when cs namespace is absent."""
    csa, registry, db, db_path, ws = env
    result = _call_pre_tool(csa, db_path, {})
    assert result == {}
