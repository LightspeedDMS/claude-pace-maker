"""
Story #155 -- declaration store (SQLite, temporary, self-cleaning).

Real SQLite files in tmp_path, no mocks. The store keeps two tables:
``declarations`` (unconsumed declare_intent calls) and ``chains`` (the one
declaration an agent may currently reuse), both keyed by session_id +
agent_key, both purged after 60 minutes on EVERY store access.
"""

import os
import sqlite3

import pytest

from pacemaker.constants import (
    DECLARE_INTENT_TOOL_NAMES,
    INTENT_DECLARATION_TTL_SECONDS,
)
from pacemaker.intent_declarations.fields import (
    REQUIRED_FIELDS,
    missing_required_fields,
    normalize_file_path,
)
from pacemaker.intent_declarations.store import (
    IntentDeclarationStore,
    StoredIntent,
    resolve_db_path,
)


class FakeClock:
    """Controllable clock injected into the store (a real double we own)."""

    def __init__(self, start: float = 1_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "intent_declarations.db")


@pytest.fixture
def store(db_path, clock):
    return IntentDeclarationStore(db_path, clock=clock)


def _rows(db_path, table):
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
    finally:
        conn.close()


class TestConstants:
    def test_ttl_is_sixty_minutes(self):
        assert INTENT_DECLARATION_TTL_SECONDS == 60 * 60

    def test_both_tool_names_recognized_from_one_constant_set(self):
        assert DECLARE_INTENT_TOOL_NAMES == frozenset(
            {
                "mcp__pace-maker__declare_intent",
                "mcp__plugin_claude-pace-maker_pace-maker__declare_intent",
            }
        )


class TestDbPathResolution:
    def test_env_override_wins(self, monkeypatch, tmp_path):
        target = str(tmp_path / "custom.db")
        monkeypatch.setenv("PACEMAKER_INTENT_DECLARATIONS_PATH", target)
        assert resolve_db_path() == target

    def test_test_mode_without_override_raises(self, monkeypatch):
        monkeypatch.delenv("PACEMAKER_INTENT_DECLARATIONS_PATH", raising=False)
        monkeypatch.setenv("PACEMAKER_TEST_MODE", "1")
        with pytest.raises(RuntimeError, match="PACEMAKER_INTENT_DECLARATIONS_PATH"):
            resolve_db_path()

    def test_production_default_under_home(self, monkeypatch, tmp_path):
        monkeypatch.delenv("PACEMAKER_INTENT_DECLARATIONS_PATH", raising=False)
        monkeypatch.delenv("PACEMAKER_TEST_MODE", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        assert resolve_db_path() == str(
            tmp_path / ".claude-pace-maker" / "intent_declarations.db"
        )

    def test_conftest_guard_sets_override_for_every_test(self):
        """AC12: the autouse guard keeps every test off the real DB."""
        path = resolve_db_path()
        assert path
        assert os.path.expanduser("~/.claude-pace-maker/") not in path or (
            "fake_home" in path
        )
        assert not path.startswith("/home/jsbattig/.claude-pace-maker")


class TestFields:
    def test_required_fields(self):
        assert REQUIRED_FIELDS == ("file_path", "change", "goal")

    def test_missing_none_and_blank_and_nonstring(self):
        assert missing_required_fields(
            {"file_path": None, "change": "  ", "goal": 5}
        ) == ["file_path", "change", "goal"]

    def test_nothing_missing(self):
        assert (
            missing_required_fields({"file_path": "a.py", "change": "c", "goal": "g"})
            == []
        )

    def test_non_dict_reports_everything_missing(self):
        assert missing_required_fields(None) == list(REQUIRED_FIELDS)


class TestNormalizeFilePath:
    def test_absolute_path_is_realpathed(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        got = normalize_file_path(str(link / "a.py"), cwd=None)
        assert got == str(real / "a.py")

    def test_relative_path_resolved_against_payload_cwd(self, tmp_path):
        got = normalize_file_path("src/a.py", cwd=str(tmp_path))
        assert got == str(tmp_path / "src" / "a.py")

    def test_dotdot_collapsed(self, tmp_path):
        got = normalize_file_path("src/../a.py", cwd=str(tmp_path))
        assert got == str(tmp_path / "a.py")

    def test_relative_without_cwd_uses_process_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        got = normalize_file_path("a.py", cwd=None)
        assert got == os.path.realpath(str(tmp_path / "a.py"))

    def test_empty_path_rejected(self):
        with pytest.raises(ValueError):
            normalize_file_path("", cwd=None)


class TestRecordAndConsume:
    def test_record_then_resolve_returns_declaration_and_consumes_it(
        self, store, db_path
    ):
        store.record("s1", "main", "/w/a.py", "add f", "fix bug", "t.py - test_f")
        resolution = store.resolve("s1", "main", "/w/a.py")
        assert resolution is not None
        assert resolution.source == "declare_intent"
        assert resolution.intent == StoredIntent(
            file_path="/w/a.py",
            change="add f",
            goal="fix bug",
            test_coverage="t.py - test_f",
        )
        assert _rows(db_path, "declarations") == []

    def test_second_resolve_without_new_declaration_is_none(self, store):
        store.record("s1", "main", "/w/a.py", "c", "g", "")
        assert store.resolve("s1", "main", "/w/a.py") is not None
        assert store.resolve("s1", "main", "/w/a.py") is None

    def test_newest_declaration_wins_and_every_duplicate_is_consumed(
        self, store, db_path
    ):
        """L1: all rows for session+agent+file go, the newest is the intent."""
        store.record("s1", "main", "/w/a.py", "old change", "g", "")
        store.record("s1", "main", "/w/a.py", "new change", "g", "")
        resolution = store.resolve("s1", "main", "/w/a.py")
        assert resolution.intent.change == "new change"
        assert _rows(db_path, "declarations") == []

    def test_consuming_a_file_leaves_other_files_and_agents_rows(self, store, db_path):
        store.record("s1", "main", "/w/a.py", "a1", "g", "")
        store.record("s1", "main", "/w/a.py", "a2", "g", "")
        store.record("s1", "main", "/w/b.py", "b", "g", "")
        store.record("s1", "agent-1", "/w/a.py", "other agent", "g", "")
        store.record("s2", "main", "/w/a.py", "other session", "g", "")
        store.resolve("s1", "main", "/w/a.py")
        assert sorted(r["change"] for r in _rows(db_path, "declarations")) == [
            "b",
            "other agent",
            "other session",
        ]

    def test_returning_to_a_file_cannot_reuse_an_older_duplicate(self, store):
        """The reviewer's repro: declare A twice -> edit A -> edit B -> return
        to A must NOT be satisfied by the older A row (AC5)."""
        store.record("s1", "main", "/w/a.py", "first", "g", "")
        store.record("s1", "main", "/w/a.py", "second", "g", "")
        consumed = store.resolve("s1", "main", "/w/a.py")
        store.approve("s1", "main", consumed.intent)
        assert store.resolve("s1", "main", "/w/b.py") is None  # chain dropped
        assert store.resolve("s1", "main", "/w/a.py") is None  # needs a new one

    def test_declaration_for_other_file_does_not_match(self, store, db_path):
        store.record("s1", "main", "/w/a.py", "c", "g", "")
        assert store.resolve("s1", "main", "/w/b.py") is None
        assert len(_rows(db_path, "declarations")) == 1

    def test_main_and_subagent_are_kept_separate(self, store):
        store.record("s1", "main", "/w/a.py", "c", "g", "")
        assert store.resolve("s1", "agent-7", "/w/a.py") is None
        assert store.resolve("s1", "main", "/w/a.py") is not None

    def test_two_subagents_are_kept_separate(self, store):
        store.record("s1", "agent-1", "/w/a.py", "c1", "g", "")
        store.record("s1", "agent-2", "/w/a.py", "c2", "g", "")
        assert store.resolve("s1", "agent-2", "/w/a.py").intent.change == "c2"
        assert store.resolve("s1", "agent-1", "/w/a.py").intent.change == "c1"

    def test_sessions_are_kept_separate(self, store):
        store.record("s1", "main", "/w/a.py", "c", "g", "")
        assert store.resolve("s2", "main", "/w/a.py") is None

    def test_row_carries_session_agent_and_created_at(self, store, db_path, clock):
        store.record("s1", "agent-9", "/w/a.py", "c", "g", "tc")
        (row,) = _rows(db_path, "declarations")
        assert row["session_id"] == "s1"
        assert row["agent_key"] == "agent-9"
        assert row["file_path"] == "/w/a.py"
        assert row["test_coverage"] == "tc"
        assert row["created_at"] == clock.now


class TestChains:
    def test_approve_creates_chain_reusable_for_same_file(self, store):
        intent = StoredIntent("/w/a.py", "c", "g", "tc")
        store.approve("s1", "main", intent)
        resolution = store.resolve("s1", "main", "/w/a.py")
        assert resolution.source == "declare_intent_chain"
        assert resolution.intent == intent

    def test_chain_is_reusable_repeatedly(self, store):
        store.approve("s1", "main", StoredIntent("/w/a.py", "c", "g", ""))
        for _ in range(3):
            assert store.resolve("s1", "main", "/w/a.py").source == (
                "declare_intent_chain"
            )

    def test_different_file_deletes_chain_and_returns_none(self, store, db_path):
        store.approve("s1", "main", StoredIntent("/w/a.py", "c", "g", ""))
        assert store.resolve("s1", "main", "/w/b.py") is None
        assert _rows(db_path, "chains") == []

    def test_returning_to_first_file_needs_new_declaration(self, store):
        store.approve("s1", "main", StoredIntent("/w/a.py", "c", "g", ""))
        assert store.resolve("s1", "main", "/w/b.py") is None
        assert store.resolve("s1", "main", "/w/a.py") is None

    def test_declaration_takes_priority_over_chain_for_same_file(self, store):
        store.approve("s1", "main", StoredIntent("/w/a.py", "chain", "g", ""))
        store.record("s1", "main", "/w/a.py", "fresh", "g", "")
        resolution = store.resolve("s1", "main", "/w/a.py")
        assert resolution.source == "declare_intent"
        assert resolution.intent.change == "fresh"

    def test_declaration_for_new_file_with_stale_chain_other_file(self, store):
        store.approve("s1", "main", StoredIntent("/w/a.py", "chain", "g", ""))
        store.record("s1", "main", "/w/b.py", "fresh", "g", "")
        resolution = store.resolve("s1", "main", "/w/b.py")
        assert resolution.source == "declare_intent"
        store.approve("s1", "main", resolution.intent)
        # Chain now follows b.py; a.py no longer chained.
        assert store.resolve("s1", "main", "/w/a.py") is None

    def test_approve_upserts_single_chain_per_agent(self, store, db_path):
        store.approve("s1", "main", StoredIntent("/w/a.py", "c1", "g", ""))
        store.approve("s1", "main", StoredIntent("/w/b.py", "c2", "g", ""))
        rows = _rows(db_path, "chains")
        assert len(rows) == 1
        assert rows[0]["file_path"] == "/w/b.py"
        assert rows[0]["change"] == "c2"

    def test_reject_deletes_chain(self, store, db_path):
        store.approve("s1", "main", StoredIntent("/w/a.py", "c", "g", ""))
        store.reject("s1", "main")
        assert _rows(db_path, "chains") == []
        assert store.resolve("s1", "main", "/w/a.py") is None

    def test_reject_only_touches_that_agents_chain(self, store):
        store.approve("s1", "main", StoredIntent("/w/a.py", "c", "g", ""))
        store.approve("s1", "agent-1", StoredIntent("/w/a.py", "c", "g", ""))
        store.reject("s1", "agent-1")
        assert store.resolve("s1", "main", "/w/a.py") is not None

    def test_reject_without_chain_is_a_noop(self, store):
        store.reject("s1", "main")

    def test_chains_are_per_agent(self, store):
        store.approve("s1", "agent-1", StoredIntent("/w/a.py", "c", "g", ""))
        assert store.resolve("s1", "main", "/w/a.py") is None
        assert store.resolve("s1", "agent-1", "/w/a.py") is not None


class TestTtlPurge:
    def test_declaration_older_than_sixty_minutes_is_purged_on_resolve(
        self, store, db_path, clock
    ):
        store.record("s1", "main", "/w/a.py", "c", "g", "")
        clock.advance(INTENT_DECLARATION_TTL_SECONDS + 1)
        assert store.resolve("s1", "main", "/w/a.py") is None
        assert _rows(db_path, "declarations") == []

    def test_declaration_just_inside_ttl_survives(self, store, clock):
        store.record("s1", "main", "/w/a.py", "c", "g", "")
        clock.advance(INTENT_DECLARATION_TTL_SECONDS - 1)
        assert store.resolve("s1", "main", "/w/a.py") is not None

    def test_chain_older_than_sixty_minutes_is_purged(self, store, db_path, clock):
        store.approve("s1", "main", StoredIntent("/w/a.py", "c", "g", ""))
        clock.advance(INTENT_DECLARATION_TTL_SECONDS + 1)
        assert store.resolve("s1", "main", "/w/a.py") is None
        assert _rows(db_path, "chains") == []

    def test_purge_runs_on_record(self, store, db_path, clock):
        store.record("s1", "main", "/w/old.py", "c", "g", "")
        clock.advance(INTENT_DECLARATION_TTL_SECONDS + 1)
        store.record("s1", "main", "/w/new.py", "c", "g", "")
        assert [r["file_path"] for r in _rows(db_path, "declarations")] == ["/w/new.py"]

    def test_purge_runs_on_approve_and_reject(self, store, db_path, clock):
        store.record("s1", "main", "/w/old.py", "c", "g", "")
        store.approve("s2", "main", StoredIntent("/w/x.py", "c", "g", ""))
        clock.advance(INTENT_DECLARATION_TTL_SECONDS + 1)
        store.reject("s3", "main")
        assert _rows(db_path, "declarations") == []
        assert _rows(db_path, "chains") == []

    def test_approve_refreshes_chain_timestamp(self, store, clock):
        intent = StoredIntent("/w/a.py", "c", "g", "")
        store.approve("s1", "main", intent)
        clock.advance(INTENT_DECLARATION_TTL_SECONDS - 10)
        store.approve("s1", "main", intent)
        clock.advance(INTENT_DECLARATION_TTL_SECONDS - 10)
        assert store.resolve("s1", "main", "/w/a.py") is not None


class TestSchemaAndConnection:
    def test_database_uses_wal(self, store, db_path):
        store.record("s1", "main", "/w/a.py", "c", "g", "")
        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        finally:
            conn.close()

    def test_chains_primary_key_is_session_and_agent(self, store, db_path):
        store.approve("s1", "main", StoredIntent("/w/a.py", "c", "g", ""))
        conn = sqlite3.connect(db_path)
        try:
            info = conn.execute("PRAGMA table_info(chains)").fetchall()
        finally:
            conn.close()
        pk_cols = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5] > 0]
        assert pk_cols == ["session_id", "agent_key"]

    def test_empty_db_path_rejected(self, clock):
        with pytest.raises(ValueError):
            IntentDeclarationStore("", clock=clock)

    def test_parent_directory_is_created(self, tmp_path, clock):
        nested = tmp_path / "a" / "b" / "decl.db"
        IntentDeclarationStore(str(nested), clock=clock).record(
            "s", "main", "/w/a.py", "c", "g", ""
        )
        assert nested.exists()
