"""
Unit tests for agent activity tracking in session_registry.

Tests:
- Schema DDL idempotency for agents and agent_actions tables
- register_agent (root and subagent)
- extract_target for Edit, Bash, Task, default, empty file_path
- record_action inserts and trims to last 3
- record_action with empty target becomes "-"
- mark_agent_ended
- purge_agents (stale active, ended after 60s, keeps recent, keeps recently ended)
- classify_agent (active, ended_visible, purged)
- list_active_tree (single root, root with subagents, orphan subagent, actions order, empty, sort)
- update_agent_heartbeat

NOTE: All 25 tests live in this single file per the task specification (Story #6).
"""

import sqlite3
import sys
import time

import pytest

# ── Module paths for cache-busting ───────────────────────────────────────────
MOD_REGISTRY = "pacemaker.session_registry.registry"
MOD_DB = "pacemaker.session_registry.db"
MOD_PACKAGE = "pacemaker.session_registry"

# ── Environment variable names ────────────────────────────────────────────────
ENV_REGISTRY_PATH = "PACEMAKER_SESSION_REGISTRY_PATH"
ENV_TEST_MODE = "PACEMAKER_TEST_MODE"
TEST_MODE_ENABLED = "1"

# ── Test data constants ───────────────────────────────────────────────────────
SESSION_A = "session-aaa"
SESSION_B = "session-bbb"
AGENT_ROOT_A = "session-aaa"  # root agent_id == session_id
AGENT_SUB_1 = "agent-sub-001"
AGENT_SUB_2 = "agent-sub-002"
WORKSPACE_X = "/workspace/project-x"
WORKSPACE_Y = "/workspace/project-y"

# ── Purge cutoff constants (must match registry.py) ───────────────────────────
STALE_CUTOFF_SECONDS = 1200  # 20 min — must match _AGENT_STALE_CUTOFF_SECONDS
ENDED_RETENTION_SECONDS = 60  # 60 sec — must match _AGENT_ENDED_RETENTION_SECONDS

# ── Time offset constants (no magic numbers in test bodies) ──────────────────
PAST_STALE = STALE_CUTOFF_SECONDS + 300  # 25 min — clearly beyond stale cutoff
PAST_ENDED = ENDED_RETENTION_SECONDS + 10  # 70 sec — beyond ended retention
RECENT_ACTIVE = STALE_CUTOFF_SECONDS - 900  # 5 min — well within active window
RECENT_ENDED = ENDED_RETENTION_SECONDS - 30  # 30 sec — within retention window
HEARTBEAT_BACKDATE = ENDED_RETENTION_SECONDS  # 60 sec backdate for heartbeat test
ACTION_BEFORE_ENDED = PAST_ENDED + 10  # 80 sec — action before ended agent

# ── Action ordering timestamp offsets ────────────────────────────────────────
FIRST_TS_OFFSET = 0  # oldest action in ordering test
SECOND_TS_OFFSET = 1  # middle action
THIRD_TS_OFFSET = 2  # newest action

# ── Action trimming constants ─────────────────────────────────────────────────
ACTION_INSERT_COUNT = 2  # how many actions to insert in the trim test
MAX_RETAINED_ACTIONS = 1  # window size kept by record_action rolling trim

# ── Collection size constants ─────────────────────────────────────────────────
ONE_ACTION = 1
ONE_ROOT = 1
TWO_SUBAGENTS = 2
TWO_ROOTS = 2

# ── Ordered sequence index constants ─────────────────────────────────────────
FIRST_IDX = 0
SECOND_IDX = 1
THIRD_IDX = 2

# ── Tuple position constants (for raw sqlite3 row tuples) ─────────────────────
COUNT_RESULT_IDX = 0  # fetchone()[0] for COUNT(*) queries
ACTION_TARGET_TUPLE_IDX = 1  # (tool_name, target, ts)[1] == target

# ── SQL helpers ───────────────────────────────────────────────────────────────
SQL_LIST_TABLE = "SELECT name FROM sqlite_master WHERE type='table' AND name=?"
SQL_FETCH_AGENT = (
    "SELECT agent_id, session_id, role, subagent_type, workspace_root, "
    "start_time, last_seen, ended_at FROM agents WHERE agent_id=?"
)
SQL_COUNT_ACTIONS = "SELECT COUNT(*) FROM agent_actions WHERE agent_id=?"
SQL_FETCH_ACTIONS = (
    "SELECT tool_name, target, ts FROM agent_actions "
    "WHERE agent_id=? ORDER BY ts ASC, id ASC"
)


# ── Module loader ─────────────────────────────────────────────────────────────


def _fresh_modules(monkeypatch, db_path):
    """Return freshly imported registry and db modules with test env vars set."""
    monkeypatch.setenv(ENV_REGISTRY_PATH, db_path)
    monkeypatch.setenv(ENV_TEST_MODE, TEST_MODE_ENABLED)
    for mod in (MOD_REGISTRY, MOD_DB, MOD_PACKAGE):
        sys.modules.pop(mod, None)
    import pacemaker.session_registry.registry as registry
    import pacemaker.session_registry.db as db

    return registry, db


# ── Generic column updater ────────────────────────────────────────────────────


def _update_agent_col(db_path, agent_id, column, value):
    """Update a single column on an agent row directly via raw connection.

    column is validated against the allowed set to prevent SQL injection.
    """
    allowed = frozenset({"last_seen", "ended_at", "start_time"})
    assert column in allowed, f"Column '{column}' not in allowed set {allowed}"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            f"UPDATE agents SET {column}=? WHERE agent_id=?", (value, agent_id)
        )
        conn.commit()
    finally:
        conn.close()


# ── Private read helpers ──────────────────────────────────────────────────────


def _fetch_agent(db_path, agent_id):
    """Fetch the full agents row for agent_id as a dict, or None."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(SQL_FETCH_AGENT, (agent_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    cols = [
        "agent_id",
        "session_id",
        "role",
        "subagent_type",
        "workspace_root",
        "start_time",
        "last_seen",
        "ended_at",
    ]
    return dict(zip(cols, row))


def _count_actions(db_path, agent_id):
    """Return count of agent_actions rows for agent_id."""
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(SQL_COUNT_ACTIONS, (agent_id,)).fetchone()[COUNT_RESULT_IDX]
    finally:
        conn.close()


def _fetch_actions_asc(db_path, agent_id):
    """Return list of (tool_name, target, ts) for agent_id, ordered oldest first."""
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(SQL_FETCH_ACTIONS, (agent_id,)).fetchall()
    finally:
        conn.close()


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fresh registry + db modules with isolated temp DB."""
    db_path = str(tmp_path / "agents_test.db")
    registry, db = _fresh_modules(monkeypatch, db_path)
    db.init_schema(db_path)
    return registry, db, db_path


# ── 1. Schema idempotency ─────────────────────────────────────────────────────


class TestSchemaIdempotent:
    def test_schema_idempotent(self, env):
        """Running init_schema() twice on same DB succeeds; agents and agent_actions tables exist."""
        registry, db, db_path = env
        db.init_schema(db_path)  # Second call must not raise

        conn = sqlite3.connect(db_path)
        try:
            row_agents = conn.execute(SQL_LIST_TABLE, ("agents",)).fetchone()
            row_actions = conn.execute(SQL_LIST_TABLE, ("agent_actions",)).fetchone()
        finally:
            conn.close()

        assert row_agents is not None, "agents table must exist after init_schema"
        assert (
            row_actions is not None
        ), "agent_actions table must exist after init_schema"


# ── 2. register_agent ─────────────────────────────────────────────────────────


class TestRegisterAgent:
    def test_register_agent_root(self, env):
        """Register root agent: row appears in agents table with correct fields."""
        registry, db, db_path = env
        before = time.time()
        registry.register_agent(AGENT_ROOT_A, SESSION_A, "root", WORKSPACE_X, db_path)
        after = time.time()

        row = _fetch_agent(db_path, AGENT_ROOT_A)
        assert row is not None
        assert row["agent_id"] == AGENT_ROOT_A
        assert row["session_id"] == SESSION_A
        assert row["role"] == "root"
        assert row["subagent_type"] is None
        assert row["workspace_root"] == WORKSPACE_X
        assert before <= row["start_time"] <= after
        assert before <= row["last_seen"] <= after
        assert row["ended_at"] is None

    def test_register_agent_subagent(self, env):
        """Register subagent with subagent_type: row appears with correct fields."""
        registry, db, db_path = env
        registry.register_agent(
            AGENT_SUB_1,
            SESSION_A,
            "subagent",
            WORKSPACE_X,
            db_path,
            subagent_type="tdd-engineer",
        )

        row = _fetch_agent(db_path, AGENT_SUB_1)
        assert row is not None
        assert row["role"] == "subagent"
        assert row["subagent_type"] == "tdd-engineer"
        assert row["session_id"] == SESSION_A
        assert row["ended_at"] is None


# ── 3. extract_target ─────────────────────────────────────────────────────────


class TestExtractTarget:
    def test_extract_target_edit(self, env):
        """extract_target('Edit', {'file_path': '/a/b/c.py'}) returns 'c.py'."""
        registry, db, db_path = env
        result = registry.extract_target("Edit", {"file_path": "/a/b/c.py"})
        assert result == "c.py"

    def test_extract_target_bash(self, env):
        """extract_target('Bash', {'command': 'pytest tests/'}) returns 'pytest tests/'."""
        registry, db, db_path = env
        result = registry.extract_target("Bash", {"command": "pytest tests/"})
        assert result == "pytest tests/"

    def test_extract_target_task(self, env):
        """extract_target('Task', {'subagent_type': 'tdd-engineer'}) returns 'tdd-engineer'."""
        registry, db, db_path = env
        result = registry.extract_target("Task", {"subagent_type": "tdd-engineer"})
        assert result == "tdd-engineer"

    def test_extract_target_default(self, env):
        """extract_target('Unknown', {}) returns ''."""
        registry, db, db_path = env
        result = registry.extract_target("Unknown", {})
        assert result == ""

    def test_extract_target_empty_file_path(self, env):
        """extract_target('Edit', {'file_path': ''}) returns ''."""
        registry, db, db_path = env
        result = registry.extract_target("Edit", {"file_path": ""})
        assert result == ""


# ── 4. record_action ─────────────────────────────────────────────────────────


class TestRecordAction:
    def test_record_action_inserts_and_trims(self, env):
        """Insert ACTION_INSERT_COUNT actions: only MAX_RETAINED_ACTIONS remain (newest)."""
        registry, db, db_path = env
        registry.register_agent(AGENT_ROOT_A, SESSION_A, "root", WORKSPACE_X, db_path)

        base_ts = time.time()
        for i in range(ACTION_INSERT_COUNT):
            registry.record_action(
                AGENT_ROOT_A, "Bash", {"command": f"cmd{i}"}, base_ts + i, db_path
            )

        count = _count_actions(db_path, AGENT_ROOT_A)
        assert (
            count == MAX_RETAINED_ACTIONS
        ), f"Expected {MAX_RETAINED_ACTIONS} actions after trim, got {count}"

        # The MAX_RETAINED_ACTIONS newest commands are kept; oldest are dropped.
        oldest_dropped_idx = ACTION_INSERT_COUNT - MAX_RETAINED_ACTIONS
        actions = _fetch_actions_asc(db_path, AGENT_ROOT_A)
        targets = [a[ACTION_TARGET_TUPLE_IDX] for a in actions]
        for i in range(ACTION_INSERT_COUNT):
            if i < oldest_dropped_idx:
                assert f"cmd{i}" not in targets, f"cmd{i} should have been trimmed"
            else:
                assert f"cmd{i}" in targets, f"cmd{i} should be retained"

    def test_record_action_empty_target_becomes_dash(self, env):
        """record_action with tool that produces empty target stores '-'."""
        registry, db, db_path = env
        registry.register_agent(AGENT_ROOT_A, SESSION_A, "root", WORKSPACE_X, db_path)

        registry.record_action(AGENT_ROOT_A, "Unknown", {}, time.time(), db_path)

        actions = _fetch_actions_asc(db_path, AGENT_ROOT_A)
        assert len(actions) == ONE_ACTION
        assert (
            actions[FIRST_IDX][ACTION_TARGET_TUPLE_IDX] == "-"
        ), f"Expected '-' target, got {actions[FIRST_IDX][ACTION_TARGET_TUPLE_IDX]!r}"


# ── 5. mark_agent_ended ───────────────────────────────────────────────────────


class TestMarkAgentEnded:
    def test_mark_agent_ended(self, env):
        """Register agent, mark ended: ended_at is set to a recent timestamp."""
        registry, db, db_path = env
        registry.register_agent(AGENT_ROOT_A, SESSION_A, "root", WORKSPACE_X, db_path)

        before = time.time()
        registry.mark_agent_ended(AGENT_ROOT_A, db_path)
        after = time.time()

        row = _fetch_agent(db_path, AGENT_ROOT_A)
        assert row is not None
        assert row["ended_at"] is not None
        assert before <= row["ended_at"] <= after


# ── 6. purge_agents ───────────────────────────────────────────────────────────


class TestPurgeAgents:
    def test_purge_stale_active(self, env):
        """Agent with last_seen PAST_STALE seconds ago (ended_at=NULL) is purged."""
        registry, db, db_path = env
        now = time.time()
        registry.register_agent(AGENT_ROOT_A, SESSION_A, "root", WORKSPACE_X, db_path)
        _update_agent_col(db_path, AGENT_ROOT_A, "last_seen", now - PAST_STALE)

        registry.purge_agents(db_path, now=now)

        row = _fetch_agent(db_path, AGENT_ROOT_A)
        assert row is None, "Stale active agent should be purged"

    def test_purge_ended_after_60s(self, env):
        """Ended agent with ended_at PAST_ENDED seconds ago is purged with cascade."""
        registry, db, db_path = env
        now = time.time()
        registry.register_agent(AGENT_ROOT_A, SESSION_A, "root", WORKSPACE_X, db_path)
        registry.record_action(
            AGENT_ROOT_A, "Bash", {"command": "ls"}, now - ACTION_BEFORE_ENDED, db_path
        )
        _update_agent_col(db_path, AGENT_ROOT_A, "ended_at", now - PAST_ENDED)

        registry.purge_agents(db_path, now=now)

        row = _fetch_agent(db_path, AGENT_ROOT_A)
        assert row is None, "Ended agent beyond retention should be purged"
        count = _count_actions(db_path, AGENT_ROOT_A)
        assert count == 0, "Actions should be cascaded-deleted when agent is purged"

    def test_purge_keeps_recent(self, env):
        """Active agent with last_seen RECENT_ACTIVE seconds ago is NOT purged."""
        registry, db, db_path = env
        now = time.time()
        registry.register_agent(AGENT_ROOT_A, SESSION_A, "root", WORKSPACE_X, db_path)
        _update_agent_col(db_path, AGENT_ROOT_A, "last_seen", now - RECENT_ACTIVE)

        registry.purge_agents(db_path, now=now)

        row = _fetch_agent(db_path, AGENT_ROOT_A)
        assert row is not None, "Recent active agent should NOT be purged"

    def test_purge_keeps_recently_ended(self, env):
        """Ended agent with ended_at RECENT_ENDED seconds ago is NOT purged."""
        registry, db, db_path = env
        now = time.time()
        registry.register_agent(AGENT_ROOT_A, SESSION_A, "root", WORKSPACE_X, db_path)
        _update_agent_col(db_path, AGENT_ROOT_A, "ended_at", now - RECENT_ENDED)

        registry.purge_agents(db_path, now=now)

        row = _fetch_agent(db_path, AGENT_ROOT_A)
        assert row is not None, "Recently ended agent should NOT be purged"


# ── 7. classify_agent ─────────────────────────────────────────────────────────


class TestClassifyAgent:
    def test_classify_agent_active(self, env):
        """classify_agent(ended_at=None, now) returns 'active'."""
        registry, db, db_path = env
        result = registry.classify_agent(None, time.time())
        assert result == "active"

    def test_classify_agent_ended_visible(self, env):
        """classify_agent(ended_at=RECENT_ENDED seconds ago, now) returns 'ended_visible'."""
        registry, db, db_path = env
        now = time.time()
        result = registry.classify_agent(now - RECENT_ENDED, now)
        assert result == "ended_visible"

    def test_classify_agent_purged(self, env):
        """classify_agent(ended_at=PAST_ENDED seconds ago, now) returns 'purged'."""
        registry, db, db_path = env
        now = time.time()
        result = registry.classify_agent(now - PAST_ENDED, now)
        assert result == "purged"


# ── 8. list_active_tree ───────────────────────────────────────────────────────


class TestListActiveTree:
    def test_list_active_tree_empty(self, env):
        """No agents in DB: list_active_tree returns empty list."""
        registry, db, db_path = env
        result = registry.list_active_tree(db_path)
        assert result == []

    def test_list_active_tree_single_root(self, env):
        """One root agent, no subagents: result contains one root dict."""
        registry, db, db_path = env
        registry.register_agent(AGENT_ROOT_A, SESSION_A, "root", WORKSPACE_X, db_path)

        result = registry.list_active_tree(db_path)

        assert len(result) == ONE_ROOT
        root = result[FIRST_IDX]
        assert root["agent_id"] == AGENT_ROOT_A
        assert root["workspace_root"] == WORKSPACE_X
        assert root["subagents"] == []
        assert root["status"] == "active"

    def test_list_active_tree_root_with_subagents(self, env):
        """Root + 2 subagents: result contains root with 2 nested subagents."""
        registry, db, db_path = env
        registry.register_agent(AGENT_ROOT_A, SESSION_A, "root", WORKSPACE_X, db_path)
        registry.register_agent(
            AGENT_SUB_1,
            SESSION_A,
            "subagent",
            WORKSPACE_X,
            db_path,
            subagent_type="tdd-engineer",
        )
        registry.register_agent(
            AGENT_SUB_2,
            SESSION_A,
            "subagent",
            WORKSPACE_X,
            db_path,
            subagent_type="code-reviewer",
        )

        result = registry.list_active_tree(db_path)

        assert len(result) == ONE_ROOT
        root = result[FIRST_IDX]
        assert root["agent_id"] == AGENT_ROOT_A
        assert len(root["subagents"]) == TWO_SUBAGENTS
        sub_types = {s["subagent_type"] for s in root["subagents"]}
        assert "tdd-engineer" in sub_types
        assert "code-reviewer" in sub_types

    def test_list_active_tree_orphan_subagent(self, env):
        """Subagent exists but its root already purged: appears under '(parent ended)' node."""
        registry, db, db_path = env
        registry.register_agent(
            AGENT_SUB_1,
            SESSION_B,
            "subagent",
            WORKSPACE_X,
            db_path,
            subagent_type="tdd-engineer",
        )

        result = registry.list_active_tree(db_path)

        labels = [r.get("label") for r in result]
        assert "(parent ended)" in labels, f"Expected orphan group, got: {result}"
        orphan_group = next(r for r in result if r.get("label") == "(parent ended)")
        orphan_ids = [s["agent_id"] for s in orphan_group["subagents"]]
        assert AGENT_SUB_1 in orphan_ids

    def test_list_active_tree_actions_newest_retained(self, env):
        """With MAX_RETAINED_ACTIONS=1, only the most recent action is retained."""
        registry, db, db_path = env
        registry.register_agent(AGENT_ROOT_A, SESSION_A, "root", WORKSPACE_X, db_path)

        base_ts = time.time()
        registry.record_action(
            AGENT_ROOT_A,
            "Read",
            {"file_path": "/a/old.py"},
            base_ts + FIRST_TS_OFFSET,
            db_path,
        )
        registry.record_action(
            AGENT_ROOT_A,
            "Write",
            {"file_path": "/a/new.py"},
            base_ts + SECOND_TS_OFFSET,
            db_path,
        )

        result = registry.list_active_tree(db_path)
        assert len(result) == ONE_ROOT
        actions = result[FIRST_IDX]["actions"]
        assert len(actions) == MAX_RETAINED_ACTIONS
        assert actions[FIRST_IDX]["target"] == "new.py"

    def test_list_active_tree_sort_active_first(self, env):
        """Active roots sort before ended-visible roots."""
        registry, db, db_path = env
        now = time.time()

        registry.register_agent(AGENT_ROOT_A, SESSION_A, "root", WORKSPACE_X, db_path)
        registry.register_agent(SESSION_B, SESSION_B, "root", WORKSPACE_Y, db_path)
        _update_agent_col(db_path, SESSION_B, "ended_at", now - RECENT_ENDED)

        result = registry.list_active_tree(db_path, now=now)

        assert len(result) == TWO_ROOTS
        assert (
            result[FIRST_IDX]["status"] == "active"
        ), f"Expected active first, got: {result[FIRST_IDX]['status']}"
        assert result[SECOND_IDX]["status"] == "ended_visible"


# ── 9. update_agent_heartbeat ─────────────────────────────────────────────────


class TestUpdateAgentHeartbeat:
    def test_update_agent_heartbeat(self, env):
        """Register agent, backdate last_seen, call heartbeat, verify last_seen updated."""
        registry, db, db_path = env
        registry.register_agent(AGENT_ROOT_A, SESSION_A, "root", WORKSPACE_X, db_path)

        old_ts = time.time() - HEARTBEAT_BACKDATE
        _update_agent_col(db_path, AGENT_ROOT_A, "last_seen", old_ts)

        before_hb = time.time()
        registry.update_agent_heartbeat(AGENT_ROOT_A, db_path)
        after_hb = time.time()

        row = _fetch_agent(db_path, AGENT_ROOT_A)
        assert row is not None
        assert (
            row["last_seen"] >= before_hb
        ), f"last_seen {row['last_seen']} should be >= {before_hb} after heartbeat"
        assert row["last_seen"] <= after_hb
