"""
Unit tests verifying that CSA state is scoped per session_id in state.json.

Bug context
-----------
state.json is a SINGLE GLOBAL FILE shared across all concurrent Claude Code
sessions.  The old flat shape:

    state["cross_session_awareness"] = {
        "workspace_root": "...",
        "seen_agent_ids": [...],
        "tool_use_counter": {...},
    }

meant that every on_session_start() call overwrote workspace_root for ALL
sessions.  Session A could see session B's workspace and list siblings from
the wrong project.

Fix
---
CSA state is now keyed by session_id:

    state["cross_session_awareness"] = {
        "<session_id_A>": {
            "workspace_root": "...",
            "seen_agent_ids": [...],
            "tool_use_counter": {...},
        },
        "<session_id_B>": {...},
    }

Tests
-----
1. test_two_sessions_do_not_share_workspace_root
2. test_two_sessions_counters_are_independent
3. test_session_end_removes_only_its_own_key
4. test_concurrent_heartbeat_does_not_touch_other_sessions_workspace_root
5. test_legacy_flat_format_is_safely_migrated
6. test_subagent_start_only_mutates_its_own_session
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

# ── Session / agent identifiers ───────────────────────────────────────────────
SESSION_A = "session-scope-aaa"
SESSION_B = "session-scope-bbb"
AGENT_ROOT = "root"
AGENT_SUB = "subagent-xyz"
PID_A = 6001
PID_B = 6002

# ── State namespace key ───────────────────────────────────────────────────────
NS = "cross_session_awareness"


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


def _session_start(csa, db_path, ws, state, session_id, pid, source="startup"):
    return csa.on_session_start(
        session_id=session_id,
        source=source,
        cwd=ws,
        pid=pid,
        db_path=db_path,
        state=state,
        config=_make_config(),
    )


# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = str(tmp_path / "registry.db")
    ws_a = str(tmp_path / "workspace_A")
    ws_b = str(tmp_path / "workspace_B")
    csa, registry, db = _fresh_modules(monkeypatch, db_path)
    db.init_schema(db_path)
    return csa, registry, db, db_path, ws_a, ws_b


# ── Test 1: two sessions store separate workspace_roots ───────────────────────


def test_two_sessions_do_not_share_workspace_root(env):
    """on_session_start for B must NOT overwrite A's workspace_root in shared state."""
    csa, registry, db, db_path, ws_a, ws_b = env
    state = {}

    _session_start(csa, db_path, ws_a, state, SESSION_A, PID_A)
    # Session A's workspace must be stored under its session_id key.
    assert state[NS][SESSION_A]["workspace_root"] == ws_a, (
        f"Session A workspace_root should be {ws_a!r}; "
        f"got {state[NS].get(SESSION_A, {}).get('workspace_root')!r}"
    )

    _session_start(csa, db_path, ws_b, state, SESSION_B, PID_B)

    # Session A's workspace_root must be unchanged after session B starts.
    assert state[NS][SESSION_A]["workspace_root"] == ws_a, (
        "Session B's on_session_start overwrote Session A's workspace_root — "
        "cross-session state pollution detected."
    )
    # Session B must store its own workspace_root.
    assert state[NS][SESSION_B]["workspace_root"] == ws_b, (
        f"Session B workspace_root should be {ws_b!r}; "
        f"got {state[NS].get(SESSION_B, {}).get('workspace_root')!r}"
    )
    # The two workspaces must differ (guard against test setup error).
    assert (
        state[NS][SESSION_A]["workspace_root"] != state[NS][SESSION_B]["workspace_root"]
    )


# ── Test 2: counters are strictly per-session ─────────────────────────────────


def test_two_sessions_counters_are_independent(env):
    """tool_use_counter for session A and B must not accumulate into each other."""
    csa, registry, db, db_path, ws_a, ws_b = env
    state = {}

    _session_start(csa, db_path, ws_a, state, SESSION_A, PID_A)
    _session_start(csa, db_path, ws_b, state, SESSION_B, PID_B)

    registry.register_session(SESSION_A, ws_a, PID_A, db_path)
    registry.register_session(SESSION_B, ws_b, PID_B, db_path)

    CALLS_A = 5
    CALLS_B = 3

    for _ in range(CALLS_A):
        csa.on_pre_tool_use(
            session_id=SESSION_A,
            agent_id=AGENT_ROOT,
            pid=PID_A,
            tool_name="Write",
            command=None,
            db_path=db_path,
            state=state,
            config=_make_config(),
        )

    for _ in range(CALLS_B):
        csa.on_pre_tool_use(
            session_id=SESSION_B,
            agent_id=AGENT_ROOT,
            pid=PID_B,
            tool_name="Write",
            command=None,
            db_path=db_path,
            state=state,
            config=_make_config(),
        )

    counter_a = state[NS][SESSION_A]["tool_use_counter"][AGENT_ROOT]
    counter_b = state[NS][SESSION_B]["tool_use_counter"][AGENT_ROOT]

    assert (
        counter_a == CALLS_A
    ), f"Session A counter should be {CALLS_A}, got {counter_a}"
    assert (
        counter_b == CALLS_B
    ), f"Session B counter should be {CALLS_B}, got {counter_b}"


# ── Test 3: session_end removes only its own key ──────────────────────────────


def test_session_end_removes_only_its_own_key(env):
    """on_session_end for session A must leave session B's sub-dict intact."""
    csa, registry, db, db_path, ws_a, ws_b = env
    state = {}

    _session_start(csa, db_path, ws_a, state, SESSION_A, PID_A)
    _session_start(csa, db_path, ws_b, state, SESSION_B, PID_B)

    # Both keys must exist before we end session A.
    assert SESSION_A in state[NS], "Session A key should exist after session_start"
    assert SESSION_B in state[NS], "Session B key should exist after session_start"

    csa.on_session_end(
        session_id=SESSION_A,
        pid=PID_A,
        db_path=db_path,
        state=state,
        config=_make_config(),
    )

    assert (
        SESSION_A not in state[NS]
    ), "Session A key must be removed from state after on_session_end"
    assert (
        SESSION_B in state[NS]
    ), "Session B key must remain in state after Session A ends"


# ── Test 4: heartbeat does not touch other session's workspace_root ───────────


def test_concurrent_heartbeat_does_not_touch_other_sessions_workspace_root(env):
    """on_heartbeat for session A must not mutate session B's sub-dict."""
    csa, registry, db, db_path, ws_a, ws_b = env
    state = {}

    _session_start(csa, db_path, ws_a, state, SESSION_A, PID_A)
    _session_start(csa, db_path, ws_b, state, SESSION_B, PID_B)

    registry.register_session(SESSION_A, ws_a, PID_A, db_path)

    ws_b_before = state[NS][SESSION_B]["workspace_root"]
    ws_a_before = state[NS][SESSION_A]["workspace_root"]

    csa.on_heartbeat(
        session_id=SESSION_A,
        pid=PID_A,
        db_path=db_path,
        state=state,
        config=_make_config(),
    )

    assert (
        state[NS][SESSION_B]["workspace_root"] == ws_b_before
    ), "Session B's workspace_root was mutated by Session A's on_heartbeat"
    assert (
        state[NS][SESSION_A]["workspace_root"] == ws_a_before
    ), "Session A's workspace_root was mutated by its own on_heartbeat — it must remain unchanged"


# ── Test 5: legacy flat format is migrated safely ─────────────────────────────


def test_legacy_flat_format_is_safely_migrated(env):
    """Pre-existing flat state shape is replaced by session-keyed shape on on_session_start."""
    csa, registry, db, db_path, ws_a, ws_b = env

    # Pre-populate with the OLD broken flat shape.
    state = {
        NS: {
            "workspace_root": "stale-workspace",
            "seen_agent_ids": [AGENT_ROOT],
            "tool_use_counter": {AGENT_ROOT: 99},
        }
    }

    _session_start(csa, db_path, ws_a, state, SESSION_A, PID_A)

    # The new shape must be keyed by session_id.
    assert (
        SESSION_A in state[NS]
    ), "After migration, state[NS] must contain a sub-dict keyed by session_id"
    # Stale flat keys must be gone.
    assert (
        "workspace_root" not in state[NS]
    ), "Legacy flat 'workspace_root' key must not remain at top level after migration"
    assert (
        "seen_agent_ids" not in state[NS]
    ), "Legacy flat 'seen_agent_ids' key must not remain at top level after migration"
    assert (
        "tool_use_counter" not in state[NS]
    ), "Legacy flat 'tool_use_counter' key must not remain at top level after migration"
    # The new sub-dict must contain the correct workspace.
    assert (
        state[NS][SESSION_A]["workspace_root"] == ws_a
    ), f"New session sub-dict workspace_root should be {ws_a!r}"


# ── Test 6: subagent_start only mutates its own session ───────────────────────


def test_subagent_start_only_mutates_its_own_session(env):
    """on_subagent_start for session A must not grow session B's seen_agent_ids."""
    csa, registry, db, db_path, ws_a, ws_b = env
    state = {}

    _session_start(csa, db_path, ws_a, state, SESSION_A, PID_A)
    _session_start(csa, db_path, ws_b, state, SESSION_B, PID_B)

    registry.register_session(SESSION_A, ws_a, PID_A, db_path)

    seen_b_before = list(state[NS][SESSION_B]["seen_agent_ids"])

    csa.on_subagent_start(
        session_id=SESSION_A,
        agent_id=AGENT_SUB,
        pid=PID_A,
        db_path=db_path,
        state=state,
        config=_make_config(),
    )

    # Session A must have the new subagent in its seen list.
    assert (
        AGENT_SUB in state[NS][SESSION_A]["seen_agent_ids"]
    ), "Session A must track the new subagent in its own seen_agent_ids"
    # Session B's seen_agent_ids must be unchanged.
    assert (
        state[NS][SESSION_B]["seen_agent_ids"] == seen_b_before
    ), "Session A's on_subagent_start must not mutate Session B's seen_agent_ids"
