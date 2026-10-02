#!/usr/bin/env python3
"""
Bug #161 review follow-up (M1, L2, L7, L8): ONE place decides which subagent
ids may become a state-file name, and it is the StateManager itself.

`agent_id` comes from the hook's stdin payload. Before this fix only the
SubagentStop read path validated it; SubagentStart and PostToolUse built
`subagent-<agent_id>` paths unchecked, so "x/../../planted" reached files
outside `langfuse_state/`. Real files in tmp dirs; nothing is mocked except a
recorder standing in for the logger (to assert a warning was emitted).
"""

import json
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from pacemaker.langfuse.state import (
    StateManager,
    is_safe_agent_id,
    is_safe_state_id,
    read_subagent_trace_info,
)

TRAVERSAL_IDS = [
    "x/../../planted",
    "../planted",
    "a/b",
    "a\\b",
    "..",
    "a b",
    "a\x00b",
    "",
    "x" * 129,
]


@pytest.fixture
def state_dir(tmp_path):
    return tmp_path / "pm" / "langfuse_state"


@pytest.fixture
def manager(state_dir):
    return StateManager(str(state_dir))


class TestIsSafeAgentId:
    @pytest.mark.parametrize(
        "agent_id", ["a1000001", "general-purpose-7a0bf911", "A_b-9", "x" * 128]
    )
    def test_plain_identifiers_are_safe(self, agent_id):
        assert is_safe_agent_id(agent_id) is True

    @pytest.mark.parametrize("agent_id", TRAVERSAL_IDS + [None, 5, ["a"], b"abc"])
    def test_everything_else_is_unsafe(self, agent_id):
        assert is_safe_agent_id(agent_id) is False


class TestStateManagerRejectsUnsafeIds:
    def test_read_refuses_traversal_even_when_the_target_file_exists(
        self, manager, state_dir
    ):
        (state_dir / "subagent-x").mkdir()
        planted = state_dir.parent / "planted.json"
        planted.write_text(json.dumps({"trace_id": "planted-trace"}))
        # Premise: the naive path really resolves to the planted file.
        naive = state_dir / "subagent-x/../../planted.json"
        assert json.loads(naive.read_text())["trace_id"] == "planted-trace"

        assert manager.read("subagent-x/../../planted") is None

    def test_create_or_update_refuses_traversal_and_writes_nothing(
        self, manager, state_dir
    ):
        (state_dir / "subagent-x").mkdir()
        before = sorted(p for p in state_dir.parent.rglob("*"))

        ok = manager.create_or_update(
            session_id="subagent-x/../../planted",
            trace_id="t",
            last_pushed_line=0,
        )

        assert ok is False
        assert sorted(p for p in state_dir.parent.rglob("*")) == before
        assert not (state_dir.parent / "planted.json").exists()

    @pytest.mark.parametrize("bad", TRAVERSAL_IDS)
    def test_unsafe_subagent_ids_are_refused_on_both_paths(self, manager, bad):
        session_id = f"subagent-{bad}"
        assert (
            manager.create_or_update(
                session_id=session_id, trace_id="t", last_pushed_line=0
            )
            is False
        )
        assert manager.read(session_id) is None

    def test_a_refusal_is_logged_as_a_warning(self, manager):
        with patch("pacemaker.langfuse.state.log_warning") as warn:
            manager.read("subagent-../x")
            manager.create_or_update("subagent-../x", "t", 0)
        assert warn.call_count == 2
        # log_warning(component, message, exc=None): args[0] is the component
        # ("state"), args[1] is the message text.
        assert all("unsafe" in c.args[1].lower() for c in warn.call_args_list)

    def test_safe_subagent_and_plain_session_ids_still_round_trip(self, manager):
        assert manager.create_or_update("subagent-a1000001", "t1", 3) is True
        assert manager.create_or_update("sess-one-aaaa", "t2", 4) is True
        assert manager.read("subagent-a1000001")["trace_id"] == "t1"
        assert manager.read("sess-one-aaaa")["last_pushed_line"] == 4


UNSAFE_PLAIN_IDS = [
    "../config",
    "../../etc/passwd",
    "/etc/passwd",
    "/",
    "..",
    ".",
    "a/b",
    "a\\b",
    "a b",
    "a\x00b",
    "",
    "x" * 129,
]


class TestEveryStateIdIsAllowlisted:
    """Reviewer's gap: only `subagent-` ids were validated, so a parent id such
    as "../config" still escaped langfuse_state/ and overwrote config.json. The
    SAME allowlist now applies to EVERY id."""

    REAL_IDS = [
        "8db566bc-3863-4ded-b091-eac96858c253",  # Claude Code session UUID
        "sess-one-aaaa",
        "subagent-aabb8c9f1234567ab",  # subagent-<17 hex>
        "subagent-" + "a" * 128,
    ]

    @pytest.mark.parametrize("bad", UNSAFE_PLAIN_IDS + [None, 5, b"x", ["a"]])
    def test_is_safe_state_id_refuses_everything_outside_the_allowlist(self, bad):
        assert is_safe_state_id(bad) is False

    @pytest.mark.parametrize("bad", ["subagent-", "subagent-../x", "subagent-a/b"])
    def test_a_subagent_prefix_does_not_relax_the_rule(self, bad):
        assert is_safe_state_id(bad) is False

    @pytest.mark.parametrize("good", REAL_IDS)
    def test_real_session_and_subagent_ids_pass(self, good):
        assert is_safe_state_id(good) is True

    def test_parent_id_traversal_cannot_overwrite_a_sibling_config_file(
        self, manager, state_dir
    ):
        config = state_dir.parent / "config.json"
        config.write_text('{"langfuse_secret_key": "keep-me"}')

        ok = manager.create_or_update(
            session_id="../config", trace_id="t", last_pushed_line=0
        )

        assert ok is False
        assert config.read_text() == '{"langfuse_secret_key": "keep-me"}'
        assert manager.read("../config") is None

    @pytest.mark.parametrize("bad", UNSAFE_PLAIN_IDS + [None])
    def test_read_and_create_or_update_refuse_every_unsafe_id(self, manager, bad):
        assert manager.read(bad) is None
        assert manager.create_or_update(bad, "t", 0) is False

    def test_a_refusal_writes_nothing_anywhere_under_the_state_parent(
        self, manager, state_dir
    ):
        before = sorted(state_dir.parent.rglob("*"))
        for bad in UNSAFE_PLAIN_IDS + [None]:
            manager.create_or_update(bad, "t", 0)
        assert sorted(state_dir.parent.rglob("*")) == before

    @pytest.mark.parametrize("good", REAL_IDS)
    def test_real_ids_still_round_trip(self, manager, good):
        assert manager.create_or_update(good, "t1", 7) is True
        assert manager.read(good)["last_pushed_line"] == 7


class TestCreateOrUpdateIsAtomic:
    """L7: create_or_update shares atomic_file.atomic_write_text (unique temp
    names per call, mode of an existing file preserved, no leftovers)."""

    def test_existing_file_mode_is_preserved(self, manager, state_dir):
        manager.create_or_update("sess-a", "t1", 1)
        target = state_dir / "sess-a.json"
        os.chmod(target, 0o600)

        manager.create_or_update("sess-a", "t2", 2)

        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert manager.read("sess-a")["trace_id"] == "t2"

    def test_unserializable_metadata_leaves_old_file_and_no_temp_files(
        self, manager, state_dir
    ):
        manager.create_or_update("sess-a", "t1", 1)

        with pytest.raises(TypeError):
            manager.create_or_update("sess-a", "t2", 2, metadata={"x": object()})

        assert manager.read("sess-a")["trace_id"] == "t1"
        assert [p.name for p in state_dir.iterdir()] == ["sess-a.json"]

    def test_write_failure_returns_false_and_leaves_no_temp_file(
        self, manager, state_dir
    ):
        manager.create_or_update("sess-a", "t1", 1)
        with patch("os.replace", side_effect=OSError("disk gone")):
            assert manager.create_or_update("sess-a", "t2", 2) is False
        assert [p.name for p in state_dir.iterdir()] == ["sess-a.json"]
        assert manager.read("sess-a")["trace_id"] == "t1"


def _register(manager: StateManager, agent_id: str, trace_id, parent_path="/p.jsonl"):
    metadata = {"current_trace_id": trace_id}
    if parent_path is not None:
        metadata["parent_transcript_path"] = parent_path
    assert manager.create_or_update(f"subagent-{agent_id}", trace_id, 0, metadata)


class TestReadSubagentTraceInfo:
    SESSION = "sess-one-aaaa"
    # Real trace-id format (orchestrator.handle_subagent_start):
    #   "<parent_session_id>-subagent-<agent_TYPE>-<uuid8>"
    # It embeds the agent type ("general-purpose"), NOT the agent_id, so the
    # only thing verifiable from the payload is the "<session>-subagent-" prefix.
    TRACE = f"{SESSION}-subagent-general-purpose-1a2b3c4d"

    def test_returns_trace_and_parent_path_for_this_sessions_agent(
        self, manager, state_dir
    ):
        _register(manager, "a1000001", self.TRACE)
        info = read_subagent_trace_info(str(state_dir), "a1000001", self.SESSION)
        assert info == {"trace_id": self.TRACE, "parent_transcript_path": "/p.jsonl"}

    def test_parent_path_is_none_for_a_file_that_predates_it(self, manager, state_dir):
        _register(manager, "a1000001", self.TRACE, parent_path=None)
        info = read_subagent_trace_info(str(state_dir), "a1000001", self.SESSION)
        assert info["parent_transcript_path"] is None

    @pytest.mark.parametrize("bad_metadata", ["x", ["a"], 5])
    def test_malformed_metadata_does_not_raise_and_yields_no_parent_path(
        self, manager, state_dir, bad_metadata
    ):
        (state_dir / "subagent-a1000001.json").write_text(
            json.dumps({"trace_id": self.TRACE, "metadata": bad_metadata})
        )
        info = read_subagent_trace_info(str(state_dir), "a1000001", self.SESSION)
        assert info == {"trace_id": self.TRACE, "parent_transcript_path": None}

    def test_unknown_agent_is_none(self, state_dir, manager):
        assert (
            read_subagent_trace_info(str(state_dir), "nope0001", self.SESSION) is None
        )

    def test_malformed_json_is_none_not_an_exception(self, manager, state_dir):
        (state_dir / "subagent-a1000001.json").write_text('{"trace_id": "x", ')
        assert (
            read_subagent_trace_info(str(state_dir), "a1000001", self.SESSION) is None
        )

    def test_non_object_json_is_none(self, manager, state_dir):
        (state_dir / "subagent-a1000001.json").write_text("[1, 2]")
        assert (
            read_subagent_trace_info(str(state_dir), "a1000001", self.SESSION) is None
        )

    @pytest.mark.parametrize("empty", ["", None])
    def test_empty_trace_id_is_none(self, manager, state_dir, empty):
        (state_dir / "subagent-a1000001.json").write_text(
            json.dumps({"session_id": "subagent-a1000001", "trace_id": empty})
        )
        assert (
            read_subagent_trace_info(str(state_dir), "a1000001", self.SESSION) is None
        )

    def test_another_sessions_trace_is_never_returned(self, manager, state_dir):
        _register(
            manager, "a1000001", "sess-two-bbbb-subagent-general-purpose-ffff0000"
        )
        assert (
            read_subagent_trace_info(str(state_dir), "a1000001", self.SESSION) is None
        )

    def test_prefix_must_include_the_subagent_marker(self, manager, state_dir):
        # Same session prefix but not the "<session>-subagent-" shape.
        _register(manager, "a1000001", f"{self.SESSION}-main-trace")
        assert (
            read_subagent_trace_info(str(state_dir), "a1000001", self.SESSION) is None
        )

    @pytest.mark.parametrize("session", [None, ""])
    def test_missing_payload_session_cannot_be_verified_so_it_is_refused(
        self, manager, state_dir, session
    ):
        _register(manager, "a1000001", self.TRACE)
        assert read_subagent_trace_info(str(state_dir), "a1000001", session) is None

    @pytest.mark.parametrize("bad", TRAVERSAL_IDS)
    def test_unsafe_agent_id_is_none(self, manager, state_dir, bad):
        (state_dir / "subagent-x").mkdir(exist_ok=True)
        Path(state_dir.parent / "planted.json").write_text(
            json.dumps({"trace_id": f"{self.SESSION}-subagent-planted"})
        )
        assert read_subagent_trace_info(str(state_dir), bad, self.SESSION) is None
