"""
Bug #157 fix 3 -- guaranteed guidance delivery for subagents.

SubagentStart can still be cancelled (hook timeout, machine load); when it is,
the subagent gets NO pace-maker additionalContext. So:

- as the very LAST step of ``run_subagent_start_hook`` (after its output was
  written) the hook records "SubagentStart completed" for (session_id,
  agent_id);
- on a subagent's FIRST PostToolUse (``agent_id`` present) with no completion
  record, ``run_hook`` injects the same subagent guidance + abbreviated
  manifest through PostToolUse ``additionalContext``, exactly once per agent.

The record lives in the #155 declaration store's DB (table
``subagent_guidance``): same self-cleaning TTL purge, WAL/busy-timeout, env
override and test-mode guard. Real hooks, real SQLite; only external services
(pacing API) are stubbed.
"""

import contextlib
import json
import sqlite3
import threading
from unittest.mock import MagicMock, patch

import pytest

from pacemaker.constants import SUBAGENT_GUIDANCE_TTL_SECONDS
from pacemaker.intent_declarations import subagent_guidance
from pacemaker.intent_declarations.store import (
    IntentDeclarationStore,
    resolve_db_path,
)

SESSION = "sess-157"
MANIFEST_MARK = "PACE-MAKER PROVENANCE CONTRACT (subagent)"
GUIDANCE_MARK = "INTENT VALIDATION ENABLED"
DECLARE_MARK = "call the `declare_intent` tool first"


class FakeClock:
    def __init__(self, start=2_000_000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _config(**overrides):
    cfg = {
        "enabled": True,
        "langfuse_enabled": False,
        "intent_validation_enabled": True,
        "cross_session_awareness_enabled": False,
    }
    cfg.update(overrides)
    return cfg


def _rows():
    conn = sqlite3.connect(resolve_db_path())
    try:
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM subagent_guidance")]
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()


class TestStoreMethods:
    @pytest.fixture
    def clock(self):
        return FakeClock()

    @pytest.fixture
    def store(self, tmp_path, clock):
        return IntentDeclarationStore(str(tmp_path / "d.db"), clock=clock)

    def test_first_claim_wins_and_second_loses(self, store):
        assert store.claim_late_guidance("s", "a1") is True
        assert store.claim_late_guidance("s", "a1") is False

    def test_completed_start_blocks_the_claim(self, store):
        store.mark_subagent_start_completed("s", "a1")
        assert store.claim_late_guidance("s", "a1") is False

    def test_completion_after_a_claim_is_recorded_without_reopening_delivery(
        self, store
    ):
        assert store.claim_late_guidance("s", "a1") is True
        store.mark_subagent_start_completed("s", "a1")
        assert store.claim_late_guidance("s", "a1") is False

    def test_agents_and_sessions_are_independent(self, store):
        store.mark_subagent_start_completed("s", "a1")
        assert store.claim_late_guidance("s", "a2") is True
        assert store.claim_late_guidance("other", "a1") is True

    def test_mark_is_idempotent(self, store):
        store.mark_subagent_start_completed("s", "a1")
        store.mark_subagent_start_completed("s", "a1")
        assert store.claim_late_guidance("s", "a1") is False

    def test_rows_are_purged_after_the_ttl_so_the_table_stays_bounded(
        self, store, clock, tmp_path
    ):
        store.mark_subagent_start_completed("s", "old")
        clock.advance(SUBAGENT_GUIDANCE_TTL_SECONDS + 1)
        store.mark_subagent_start_completed("s", "new")  # any access purges
        conn = sqlite3.connect(str(tmp_path / "d.db"))
        try:
            agents = [
                r[0] for r in conn.execute("SELECT agent_key FROM subagent_guidance")
            ]
        finally:
            conn.close()
        assert agents == ["new"]

    def test_rows_inside_the_ttl_survive(self, store, clock):
        store.mark_subagent_start_completed("s", "a1")
        clock.advance(SUBAGENT_GUIDANCE_TTL_SECONDS - 60)
        assert store.claim_late_guidance("s", "a1") is False

    def test_ttl_is_much_longer_than_the_declaration_ttl(self):
        from pacemaker.constants import INTENT_DECLARATION_TTL_SECONDS

        # A long-lived subagent must not look "never started" after an hour.
        assert SUBAGENT_GUIDANCE_TTL_SECONDS >= 12 * 3600
        assert SUBAGENT_GUIDANCE_TTL_SECONDS > INTENT_DECLARATION_TTL_SECONDS

    def test_concurrent_claims_deliver_exactly_once(self, tmp_path):
        db = str(tmp_path / "d.db")
        IntentDeclarationStore(db).mark_subagent_start_completed("s", "other")
        winners = []

        def take():
            if IntentDeclarationStore(db).claim_late_guidance("s", "a1"):
                winners.append(1)

        threads = [threading.Thread(target=take) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert len(winners) == 1

    def test_declaration_ttl_purge_does_not_touch_guidance_rows(self, store, clock):
        from pacemaker.constants import INTENT_DECLARATION_TTL_SECONDS

        store.mark_subagent_start_completed("s", "a1")
        clock.advance(INTENT_DECLARATION_TTL_SECONDS + 5)
        store.record("s", "main", "/w/a.py", "c", "g", "")
        assert store.claim_late_guidance("s", "a1") is False


class TestHelperFunctions:
    def test_record_start_completed_needs_session_and_agent(self):
        assert subagent_guidance.record_start_completed({}) is False
        assert subagent_guidance.record_start_completed({"session_id": "s"}) is False
        assert (
            subagent_guidance.record_start_completed(
                {"session_id": "s", "agent_id": "a1"}
            )
            is True
        )

    def test_claim_only_for_subagents(self):
        assert subagent_guidance.claim_late_guidance({"session_id": "s"}) is False
        assert subagent_guidance.claim_late_guidance({"agent_id": "a1"}) is False
        assert (
            subagent_guidance.claim_late_guidance({"session_id": "s", "agent_id": "a"})
            is True
        )

    def test_store_failure_never_raises_and_never_claims(self, tmp_path, monkeypatch):
        broken = tmp_path / "is_a_dir"
        broken.mkdir()
        monkeypatch.setenv("PACEMAKER_INTENT_DECLARATIONS_PATH", str(broken))
        data = {"session_id": "s", "agent_id": "a1"}
        assert subagent_guidance.record_start_completed(data) is False
        assert subagent_guidance.claim_late_guidance(data) is False


def _subagent_start_payload(agent_id="agent-1", transcript="/nonexistent.jsonl"):
    return {
        "hook_event_name": "SubagentStart",
        "session_id": SESSION,
        "agent_id": agent_id,
        "agent_type": "tdd-engineer",
        "transcript_path": transcript,
        "cwd": "/tmp",
    }


def _run_subagent_start(tmp_path, payload, config=None, extra_patches=()):
    import pacemaker.hook as hook_mod

    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"in_subagent": False, "subagent_counter": 0}))
    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch("pacemaker.hook.load_config", return_value=config or _config())
        )
        stack.enter_context(patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path)))
        stack.enter_context(
            patch("sys.stdin", MagicMock(read=lambda: json.dumps(payload)))
        )
        for extra in extra_patches:
            stack.enter_context(extra)
        hook_mod.run_subagent_start_hook()


def _run_post_tool_use(tmp_path, payload, config=None):
    import pacemaker.hook as hook_mod

    state = {
        "session_id": SESSION,
        "tool_execution_count": 0,
        "subagent_counter": 0,
        "in_subagent": False,
    }
    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch("pacemaker.hook.load_config", return_value=config or _config())
        )
        stack.enter_context(patch("pacemaker.hook.load_state", return_value=state))
        stack.enter_context(patch("pacemaker.hook.save_state"))
        stack.enter_context(
            patch("pacemaker.hook.pacing_engine.run_pacing_check", return_value={})
        )
        stack.enter_context(
            patch("sys.stdin", MagicMock(read=lambda: json.dumps(payload)))
        )
        return hook_mod.run_hook()


def _ptu_payload(agent_id=None, tool="Read"):
    payload = {
        "session_id": SESSION,
        "cwd": "/tmp",
        "tool_name": tool,
        "tool_input": {"file_path": "/tmp/x.py"},
        "tool_response": "ok",
        "transcript_path": "/nonexistent.jsonl",
    }
    if agent_id:
        payload["agent_id"] = agent_id
    return payload


def _contexts(capsys):
    """additionalContext strings from every JSON line printed to stdout."""
    found = []
    for line in capsys.readouterr().out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            found.append(json.loads(line)["hookSpecificOutput"]["additionalContext"])
        except (ValueError, KeyError):
            continue
    return found


class TestSubagentStartRecordsCompletion:
    def test_completion_is_recorded_for_session_and_agent(self, tmp_path):
        _run_subagent_start(tmp_path, _subagent_start_payload("agent-7"))
        (row,) = _rows()
        assert row["session_id"] == SESSION
        assert row["agent_key"] == "agent-7"
        assert row["start_completed_at"] is not None

    def test_it_is_recorded_AFTER_the_output_is_written(self, tmp_path, capsys):
        order = []
        real_record = subagent_guidance.record_start_completed

        def spy_record(hook_data):
            order.append(("record", capsys.readouterr().out))
            return real_record(hook_data)

        _run_subagent_start(
            tmp_path,
            _subagent_start_payload(),
            extra_patches=[
                patch(
                    "pacemaker.hook.subagent_guidance.record_start_completed",
                    spy_record,
                )
            ],
        )
        (_, printed_before_record) = order[0]
        assert "additionalContext" in printed_before_record
        assert MANIFEST_MARK in printed_before_record

    def test_a_start_that_dies_before_finishing_records_nothing(self, tmp_path):
        def boom(*args, **kwargs):
            raise SystemExit("cancelled by the harness")

        with pytest.raises(SystemExit):
            _run_subagent_start(
                tmp_path,
                _subagent_start_payload("agent-dead"),
                extra_patches=[
                    patch("pacemaker.hook._handle_langfuse_subagent_start", boom)
                ],
            )
        assert _rows() == []

    def test_no_hook_data_records_nothing(self, tmp_path):
        import pacemaker.hook as hook_mod

        state_path = tmp_path / "state.json"
        state_path.write_text("{}")
        with (
            patch("pacemaker.hook.load_config", return_value=_config()),
            patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path)),
            patch("sys.stdin", MagicMock(read=lambda: "")),
        ):
            hook_mod.run_subagent_start_hook()
        assert _rows() == []

    def test_a_failing_record_never_breaks_subagent_start(self, tmp_path, capsys):
        def boom(hook_data):
            raise RuntimeError("store exploded")

        _run_subagent_start(
            tmp_path,
            _subagent_start_payload(),
            extra_patches=[
                patch("pacemaker.hook.subagent_guidance.record_start_completed", boom)
            ],
        )
        assert any(MANIFEST_MARK in c for c in _contexts(capsys))


class TestLateDeliveryOnTheFirstSubagentToolCall:
    def test_delivered_once_when_start_did_not_complete(self, tmp_path, capsys):
        _run_post_tool_use(tmp_path, _ptu_payload("agent-1"))
        first = [c for c in _contexts(capsys) if MANIFEST_MARK in c]
        assert len(first) == 1
        assert GUIDANCE_MARK in first[0]
        assert DECLARE_MARK in first[0]
        # Second and third tool calls of the same agent: nothing more.
        _run_post_tool_use(tmp_path, _ptu_payload("agent-1"))
        _run_post_tool_use(tmp_path, _ptu_payload("agent-1", tool="Write"))
        assert [c for c in _contexts(capsys) if MANIFEST_MARK in c] == []

    def test_not_delivered_when_start_completed(self, tmp_path, capsys):
        _run_subagent_start(tmp_path, _subagent_start_payload("agent-1"))
        capsys.readouterr()
        _run_post_tool_use(tmp_path, _ptu_payload("agent-1"))
        assert [c for c in _contexts(capsys) if MANIFEST_MARK in c] == []

    def test_each_agent_gets_its_own_single_delivery(self, tmp_path, capsys):
        _run_post_tool_use(tmp_path, _ptu_payload("agent-1"))
        _run_post_tool_use(tmp_path, _ptu_payload("agent-2"))
        delivered = [c for c in _contexts(capsys) if MANIFEST_MARK in c]
        assert len(delivered) == 2

    def test_completed_agent_does_not_suppress_another_agents_delivery(
        self, tmp_path, capsys
    ):
        _run_subagent_start(tmp_path, _subagent_start_payload("agent-done"))
        capsys.readouterr()
        _run_post_tool_use(tmp_path, _ptu_payload("agent-late"))
        assert len([c for c in _contexts(capsys) if MANIFEST_MARK in c]) == 1

    def test_main_thread_is_never_affected(self, tmp_path, capsys):
        _run_post_tool_use(tmp_path, _ptu_payload(None))
        _run_post_tool_use(tmp_path, _ptu_payload(None))
        assert [c for c in _contexts(capsys) if MANIFEST_MARK in c] == []
        assert _rows() == []

    def test_the_text_is_exactly_what_subagent_start_delivers(self, tmp_path, capsys):
        _run_subagent_start(tmp_path, _subagent_start_payload("agent-s"))
        start_context = next(c for c in _contexts(capsys) if MANIFEST_MARK in c)
        _run_post_tool_use(tmp_path, _ptu_payload("agent-late"))
        late_context = next(c for c in _contexts(capsys) if MANIFEST_MARK in c)
        assert late_context.startswith(start_context.split("\n\n")[0])
        assert GUIDANCE_MARK in late_context and GUIDANCE_MARK in start_context

    def test_config_gates_decide_what_text_exists(self, tmp_path, capsys):
        cfg = _config(intent_validation_enabled=False)
        _run_post_tool_use(tmp_path, _ptu_payload("agent-1"), config=cfg)
        (context,) = [c for c in _contexts(capsys) if MANIFEST_MARK in c]
        assert GUIDANCE_MARK not in context
        assert DECLARE_MARK not in context

    def test_kill_switch_changes_the_guidance_like_subagent_start_does(
        self, tmp_path, capsys
    ):
        cfg = _config(intent_declaration_tool_enabled=False)
        _run_post_tool_use(tmp_path, _ptu_payload("agent-1"), config=cfg)
        (context,) = [c for c in _contexts(capsys) if MANIFEST_MARK in c]
        assert GUIDANCE_MARK in context
        assert DECLARE_MARK not in context  # the #155 tool paragraph is gone

    def test_pace_maker_disabled_delivers_nothing(self, tmp_path, capsys):
        _run_post_tool_use(
            tmp_path, _ptu_payload("agent-1"), config=_config(enabled=False)
        )
        assert _contexts(capsys) == []
        assert _rows() == []

    def test_store_failure_means_no_injection_and_no_crash(
        self, tmp_path, capsys, monkeypatch
    ):
        broken = tmp_path / "is_a_dir"
        broken.mkdir()
        monkeypatch.setenv("PACEMAKER_INTENT_DECLARATIONS_PATH", str(broken))
        _run_post_tool_use(tmp_path, _ptu_payload("agent-1"))
        assert [c for c in _contexts(capsys) if MANIFEST_MARK in c] == []

    def test_delivery_is_combined_with_other_post_tool_use_context(
        self, tmp_path, capsys
    ):
        """The secrets nudge / reminders still go out in the same output."""
        _run_post_tool_use(tmp_path, _ptu_payload("agent-1", tool="Bash"))
        contexts = _contexts(capsys)
        late = [c for c in contexts if MANIFEST_MARK in c]
        assert len(late) == 1
        assert len(contexts) == 1  # ONE JSON object per hook run
        assert "secrets_nudge" in late[0] or "SECRET" in late[0]

    def test_delivery_is_recorded_when_the_output_is_emitted(self, tmp_path, capsys):
        _run_post_tool_use(tmp_path, _ptu_payload("agent-1"))
        (row,) = _rows()
        assert row["delivered_at"] is not None
        assert row["start_completed_at"] is None


class TestClaimHappensAtEmission:
    """Code-review L1: the text is prepared up front, but the one-time claim is
    made IMMEDIATELY BEFORE the output is printed -- a pacing delay (up to
    350 s) or an unbounded Langfuse push between the two must never be able to
    spend the delivery without the output going out ("guaranteed" delivery
    prefers a rare duplicate over a lost delivery)."""

    def _events_run(self, tmp_path, payload):
        events = []
        from pacemaker.langfuse import orchestrator

        real_claim = subagent_guidance.claim_late_guidance

        def spy_claim(hook_data):
            events.append(("claim", len(_rows())))
            return real_claim(hook_data)

        def fake_langfuse(**kwargs):
            events.append(("langfuse", len(_rows())))
            return True

        with (
            patch("pacemaker.hook.subagent_guidance.claim_late_guidance", spy_claim),
            patch.object(orchestrator, "handle_post_tool_use", fake_langfuse),
        ):
            _run_post_tool_use(tmp_path, payload)
        return events

    def test_nothing_is_claimed_before_the_slow_post_tool_use_steps_ran(self, tmp_path):
        events = self._events_run(tmp_path, _ptu_payload("agent-1"))
        names = [name for name, _ in events]
        assert names == ["langfuse", "claim"]
        # No delivery record existed while the slow Langfuse step ran.
        assert events[0] == ("langfuse", 0)

    def test_a_hook_that_dies_before_emitting_leaves_the_delivery_unclaimed(
        self, tmp_path, capsys
    ):
        from pacemaker.langfuse import orchestrator

        def die(**kwargs):
            raise SystemExit("killed by the harness timeout")

        with patch.object(orchestrator, "handle_post_tool_use", die):
            with pytest.raises(SystemExit):
                _run_post_tool_use(tmp_path, _ptu_payload("agent-1"))
        assert _rows() == []
        capsys.readouterr()
        # The retry (the agent's next tool call) still delivers.
        _run_post_tool_use(tmp_path, _ptu_payload("agent-1"))
        assert len([c for c in _contexts(capsys) if MANIFEST_MARK in c]) == 1

    def test_losing_the_claim_race_means_no_duplicate_output(self, tmp_path, capsys):
        """Another PostToolUse of the same agent claimed between our up-front
        check and our emit: we must not print a second copy."""
        with patch(
            "pacemaker.hook.subagent_guidance.claim_late_guidance",
            lambda hook_data: False,
        ):
            _run_post_tool_use(tmp_path, _ptu_payload("agent-1"))
        assert [c for c in _contexts(capsys) if MANIFEST_MARK in c] == []


class TestSettledDeliveryIsCheap:
    """Code-review L2: the common path (every subagent tool call after the
    first) must not build the guidance text nor take a write transaction."""

    def _settle_by_delivery(self, tmp_path, capsys):
        _run_post_tool_use(tmp_path, _ptu_payload("agent-1"))
        capsys.readouterr()

    def test_text_is_not_built_once_delivery_is_settled(self, tmp_path, capsys):
        self._settle_by_delivery(tmp_path, capsys)

        def boom(config):
            raise AssertionError("guidance text must not be built")

        with patch("pacemaker.hook._subagent_guidance_parts", boom):
            _run_post_tool_use(tmp_path, _ptu_payload("agent-1"))
            _run_post_tool_use(tmp_path, _ptu_payload("agent-1", tool="Write"))
        assert [c for c in _contexts(capsys) if MANIFEST_MARK in c] == []

    def test_text_is_not_built_when_subagent_start_completed(self, tmp_path, capsys):
        _run_subagent_start(tmp_path, _subagent_start_payload("agent-1"))
        capsys.readouterr()

        def boom(config):
            raise AssertionError("guidance text must not be built")

        with patch("pacemaker.hook._subagent_guidance_parts", boom):
            _run_post_tool_use(tmp_path, _ptu_payload("agent-1"))

    def test_the_claim_transaction_is_not_attempted_once_settled(
        self, tmp_path, capsys
    ):
        self._settle_by_delivery(tmp_path, capsys)
        claims = []
        with patch(
            "pacemaker.hook.subagent_guidance.claim_late_guidance",
            lambda hook_data: claims.append(1) or False,
        ):
            _run_post_tool_use(tmp_path, _ptu_payload("agent-1"))
        assert claims == []

    def test_main_thread_never_reaches_the_store_at_all(self, tmp_path, capsys):
        def boom(hook_data):
            raise AssertionError("main thread must not touch the guidance store")

        with patch("pacemaker.hook.subagent_guidance.needs_late_guidance", boom):
            _run_post_tool_use(tmp_path, _ptu_payload(None))

    def test_unsettled_agent_builds_the_text_exactly_once(self, tmp_path, capsys):
        import pacemaker.hook as hook_mod

        calls = []
        real = hook_mod._subagent_guidance_parts

        def spy(config):
            calls.append(1)
            return real(config)

        with patch("pacemaker.hook._subagent_guidance_parts", spy):
            _run_post_tool_use(tmp_path, _ptu_payload("agent-1"))
        assert calls == [1]


class TestReadOnlySettledCheck:
    def test_unknown_agent_is_not_settled(self, tmp_path):
        store = IntentDeclarationStore(str(tmp_path / "d.db"))
        assert store.is_late_guidance_settled("s", "a1") is False

    def test_settled_after_a_claim_and_after_a_completion(self, tmp_path):
        store = IntentDeclarationStore(str(tmp_path / "d.db"))
        store.claim_late_guidance("s", "claimed")
        store.mark_subagent_start_completed("s", "completed")
        assert store.is_late_guidance_settled("s", "claimed") is True
        assert store.is_late_guidance_settled("s", "completed") is True
        assert store.is_late_guidance_settled("s", "other") is False

    def test_missing_database_is_unsettled_and_is_not_created(self, tmp_path):
        db = tmp_path / "never.db"
        assert (
            IntentDeclarationStore(str(db)).is_late_guidance_settled("s", "a") is False
        )
        assert not db.exists()

    def test_it_takes_no_write_lock(self, tmp_path):
        """Another connection holds BEGIN IMMEDIATE (the write lock): a write
        transaction would wait out the 2 s busy timeout and fail; the settled
        check is a plain read and answers at once."""
        db = str(tmp_path / "d.db")
        store = IntentDeclarationStore(db)
        store.mark_subagent_start_completed("s", "a1")
        holder = sqlite3.connect(db, isolation_level=None)
        try:
            holder.execute("BEGIN IMMEDIATE")
            start = __import__("time").monotonic()
            assert store.is_late_guidance_settled("s", "a1") is True
            assert __import__("time").monotonic() - start < 1.0
        finally:
            holder.execute("ROLLBACK")
            holder.close()

    def test_needs_late_guidance_helper(self, tmp_path, monkeypatch):
        data = {"session_id": "s", "agent_id": "a1"}
        assert subagent_guidance.needs_late_guidance({"session_id": "s"}) is False
        assert subagent_guidance.needs_late_guidance(data) is True
        subagent_guidance.record_start_completed(data)
        assert subagent_guidance.needs_late_guidance(data) is False

    def test_needs_late_guidance_store_failure_means_no(self, tmp_path, monkeypatch):
        broken = tmp_path / "is_a_dir"
        broken.mkdir()
        monkeypatch.setenv("PACEMAKER_INTENT_DECLARATIONS_PATH", str(broken))
        assert (
            subagent_guidance.needs_late_guidance({"session_id": "s", "agent_id": "a"})
            is False
        )
