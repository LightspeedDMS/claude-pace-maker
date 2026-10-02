#!/usr/bin/env python3
"""
Bug #161 (part 2): SubagentStop must finalize a subagent's Langfuse trace from
the subagent's OWN state file, never from the machine-wide state.json.

Root cause (live evidence: traces ...-subagent-general-purpose-7a0bf911 and
...-4d7a0111 had spans but no generation and no output; state.json held ONE
`subagent_traces` entry after the run, none for the two lost agents while their
`langfuse_state/subagent-<agent_id>.json` files still had the right trace ids):
finalization looked the trace up in `state["subagent_traces"][agent_id]` inside
state.json, a single file every concurrent hook loads, edits and rewrites
(non-atomically, and with a stale in-memory copy held across multi-second
Langfuse calls). Whatever another hook clobbered was a trace never finalized.

Fix contract: SubagentStop resolves the trace from
`langfuse_state/subagent-<agent_id>.json` keyed by the payload's agent_id (as
#158 did for spans). The global state.json is at most a legacy fallback for
payloads with no agent_id (or for the slot's own agent).

Real hooks, real StateManager files in a tmp HOME, real orchestrator. Only the
Langfuse HTTP push (and the OAuth e-mail lookup, an unrelated network call)
are patched, as the neighbouring tests do.
"""

import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from pacemaker.hook import run_subagent_start_hook, run_subagent_stop_hook
from pacemaker.langfuse import orchestrator
from pacemaker.langfuse.state import StateManager, is_safe_agent_id

SESSION_1 = "sess-one-aaaa"
SESSION_2 = "sess-two-bbbb"
# (agent_id, parent session id): two subagents of session 1, one of session 2.
AGENTS = [("a1000001", SESSION_1), ("a2000002", SESSION_1), ("b1000003", SESSION_2)]
USER_EMAIL = "tester@example.com"
GLOBAL_STATE_CLOBBERS = {
    "empty": "",  # the "JSONDecodeError ... char 0" a reader hits mid-write
    "partial": '{"subagent_counter": 3, "subagent_trac',
    "reset": "{}",  # valid JSON, every entry lost
    "missing": None,  # file removed
}


def _agent_output(agent_id: str) -> str:
    return f"final report of {agent_id}"


def _write_agent_transcript(path: Path, agent_id: str) -> str:
    entry = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "model": "claude-sonnet-4-5",
            "content": [{"type": "text", "text": _agent_output(agent_id)}],
            "usage": {"input_tokens": 11, "output_tokens": 7},
        },
    }
    path.write_text(json.dumps(entry) + "\n")
    return str(path)


class Harness:
    """Drives the real hooks for several sessions/subagents and records every
    event that would have been pushed to Langfuse."""

    def __init__(self, tmp_path: Path, config: dict):
        self.tmp_path = tmp_path
        self.config = config
        self.state_file = tmp_path / "state.json"
        self.state_dir = Path.home() / ".claude-pace-maker" / "langfuse_state"
        self.events: list = []
        self.parent_transcript = tmp_path / "parent.jsonl"
        self.parent_transcript.write_text(
            json.dumps({"type": "user", "message": {"role": "user", "content": "go"}})
            + "\n"
        )
        manager = StateManager(str(self.state_dir))
        for session in (SESSION_1, SESSION_2):
            main_trace = f"main-{session}"
            manager.create_or_update(
                session_id=session,
                trace_id=main_trace,
                last_pushed_line=0,
                metadata={"current_trace_id": main_trace, "trace_start_line": 0},
            )

    def push(self, base_url, public_key, secret_key, batch, **kwargs):
        self.events.extend(batch)
        return True, len(batch)

    def _stdin(self, payload: dict):
        return patch("sys.stdin.read", return_value=json.dumps(payload))

    def start(self, agent_id: str, session_id: str) -> None:
        payload = {
            "session_id": session_id,
            "agent_id": agent_id,
            "agent_type": "general-purpose",
            "transcript_path": str(self.parent_transcript),
        }
        with self._stdin(payload):
            run_subagent_start_hook()

    def post_tool(self, agent_id: str, session_id: str) -> None:
        orchestrator.handle_post_tool_use(
            config=self.config,
            session_id=session_id,
            transcript_path=str(self.parent_transcript),
            state_dir=str(self.state_dir),
            tool_response="ok",
            tool_name="Bash",
            tool_input={"command": "x"},
            agent_id=agent_id,
        )

    def stop(self, agent_id: str, session_id: str) -> None:
        file_stem = agent_id.replace("/", "_")  # ids may be hostile in tests
        payload = {
            "session_id": session_id,
            "agent_id": agent_id,
            "agent_transcript_path": _write_agent_transcript(
                self.tmp_path / f"agent-{file_stem}.jsonl", agent_id
            ),
        }
        with self._stdin(payload):
            run_subagent_stop_hook()

    def clobber_global_state(self, kind: str) -> None:
        content = GLOBAL_STATE_CLOBBERS[kind]
        if content is None:
            self.state_file.unlink(missing_ok=True)
        else:
            self.state_file.write_text(content)

    def trace_of(self, agent_id: str) -> str:
        state = StateManager(str(self.state_dir)).read(f"subagent-{agent_id}")
        assert state and state.get("trace_id"), f"no per-agent state for {agent_id}"
        return state["trace_id"]

    def events_for(self, event_type: str, trace_id: str) -> list:
        key = "id" if event_type == "trace-create" else "traceId"
        return [
            e
            for e in self.events
            if e["type"] == event_type and e["body"].get(key) == trace_id
        ]


@pytest.fixture
def config(tmp_path):
    return {
        "enabled": True,
        "langfuse_enabled": True,
        "langfuse_base_url": "https://langfuse.example.com",
        "langfuse_public_key": "pk-test",
        "langfuse_secret_key": "sk-test",
        "db_path": str(tmp_path / "usage.db"),
    }


@pytest.fixture
def harness(tmp_path, config):
    h = Harness(tmp_path, config)
    with ExitStack() as stack:
        stack.enter_context(
            patch("pacemaker.hook.DEFAULT_STATE_PATH", str(h.state_file))
        )
        stack.enter_context(patch("pacemaker.hook.load_config", return_value=config))
        stack.enter_context(
            patch("pacemaker.hook.get_transcript_path", return_value=None)
        )
        stack.enter_context(
            patch("pacemaker.langfuse.push.push_batch_events", side_effect=h.push)
        )
        stack.enter_context(patch("pacemaker.langfuse.metrics.increment_metric"))
        stack.enter_context(
            patch(
                "pacemaker.langfuse.orchestrator.jsonl_parser.get_user_email",
                return_value=USER_EMAIL,
            )
        )
        yield h


def _assert_every_agent_finalized(h: Harness) -> None:
    traces = {agent_id: h.trace_of(agent_id) for agent_id, _ in AGENTS}
    assert len(set(traces.values())) == len(AGENTS), "trace ids must be distinct"
    for agent_id, trace_id in traces.items():
        finals = [
            e for e in h.events_for("trace-create", trace_id) if "output" in e["body"]
        ]
        assert len(finals) == 1, f"{agent_id}: trace never finalized with output"
        assert finals[0]["body"]["output"] == _agent_output(agent_id)
        generations = h.events_for("generation-create", trace_id)
        assert len(generations) == 1, f"{agent_id}: no generation observation"
        spans = h.events_for("span-create", trace_id)
        assert spans, f"{agent_id}: its spans did not land in its own trace"


class TestInterleavedFinalizationWithClobberedGlobalState:
    def test_control_same_flow_without_clobbering_finalizes_everything(self, harness):
        h = harness
        for agent_id, session_id in AGENTS:
            h.start(agent_id, session_id)
            h.post_tool(agent_id, session_id)
        for agent_id, session_id in reversed(AGENTS):
            h.stop(agent_id, session_id)

        _assert_every_agent_finalized(h)

    def test_every_subagent_trace_is_finalized_whatever_state_json_looks_like(
        self, harness
    ):
        h = harness
        h.start("a1000001", SESSION_1)
        h.clobber_global_state("empty")
        h.start("b1000003", SESSION_2)
        h.clobber_global_state("partial")
        h.start("a2000002", SESSION_1)
        for agent_id, session_id in AGENTS:
            h.post_tool(agent_id, session_id)

        # SubagentStop in a different order than start, with the shared
        # state.json destroyed in a different way before each one.
        h.clobber_global_state("reset")
        h.stop("b1000003", SESSION_2)
        h.clobber_global_state("missing")
        h.stop("a1000001", SESSION_1)
        h.clobber_global_state("partial")
        h.stop("a2000002", SESSION_1)

        _assert_every_agent_finalized(h)


def _write_during(path: Path, key: str, value: str):
    """Returns a function standing in for 'another hook process rewrote
    state.json while this hook was busy with its (slow) Langfuse step'."""

    def write() -> None:
        data = json.loads(path.read_text()) if path.exists() else {}
        data[key] = value
        path.write_text(json.dumps(data))

    return write


class TestSubagentStartRegistration:
    def test_agent_state_carries_parent_path_and_global_map_is_retired(self, harness):
        harness.start("a1000001", SESSION_1)

        agent_state = StateManager(str(harness.state_dir)).read("subagent-a1000001")
        assert agent_state["metadata"]["parent_transcript_path"] == str(
            harness.parent_transcript
        )
        global_state = json.loads(harness.state_file.read_text())
        assert "subagent_traces" not in global_state
        # legacy single slot is still written (counter/reminder/no-agent_id compat)
        assert global_state["current_subagent_trace_id"] == harness.trace_of("a1000001")

    def test_start_does_not_overwrite_changes_made_during_its_langfuse_step(
        self, harness
    ):
        real_start = orchestrator.handle_subagent_start
        write_marker = _write_during(harness.state_file, "marker", "from-another-hook")

        def slow_langfuse_step(*args, **kwargs):
            write_marker()
            return real_start(*args, **kwargs)

        with patch(
            "pacemaker.langfuse.orchestrator.handle_subagent_start",
            side_effect=slow_langfuse_step,
        ):
            harness.start("a1000001", SESSION_1)

        global_state = json.loads(harness.state_file.read_text())
        assert global_state.get("marker") == "from-another-hook"
        assert global_state["current_subagent_agent_id"] == "a1000001"


TRAVERSAL_ID = "x/../../planted"


def _snapshot(root: Path) -> dict:
    """Every JSON state file (and any leftover temp file of one) under root.
    usage.db and the log files are legitimately written by the hooks."""
    return {
        p: p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".json" in p.name
    }


class TestUnsafeAgentIdAtSubagentStart:
    """M1: SubagentStart built `subagent-<agent_id>` from the raw payload."""

    def test_traversal_agent_id_writes_nothing_outside_langfuse_state(self, harness):
        (harness.state_dir / "subagent-x").mkdir()  # makes the traversal resolvable
        pm_dir = harness.state_dir.parent
        before = _snapshot(pm_dir)

        harness.start(TRAVERSAL_ID, SESSION_1)

        assert not (pm_dir / "planted.json").exists()
        assert (
            _snapshot(pm_dir) == before
        ), "files were written/changed under ~/.claude-pace-maker"
        # treated as an unregistered subagent: no Langfuse trace, no legacy slot
        assert harness.events == []
        global_state = json.loads(harness.state_file.read_text())
        assert "current_subagent_trace_id" not in global_state


class TestUnsafeAgentIdAtPostToolUse:
    """M1: PostToolUse built `subagent-<agent_id>` from the raw payload."""

    def test_traversal_agent_id_is_unregistered_and_attributes_no_span(self, harness):
        (harness.state_dir / "subagent-x").mkdir()
        pm_dir = harness.state_dir.parent
        # A file the traversal path would reach, carrying a "valid" trace.
        (pm_dir / "planted.json").write_text(
            json.dumps(
                {
                    "trace_id": "planted-trace",
                    "last_pushed_line": 0,
                    "metadata": {"current_trace_id": "planted-trace"},
                }
            )
        )
        before = _snapshot(pm_dir)

        harness.post_tool(TRAVERSAL_ID, SESSION_1)

        assert (
            _snapshot(pm_dir) == before
        ), "a state file outside the agent's own changed"
        # No span anywhere: not in the planted trace, not in the parent's main trace.
        assert [e for e in harness.events if e["type"] == "span-create"] == []


class TestStartAndStopAgreeOnAgentIdValidity:
    """An id accepted at start but rejected at stop would never be finalized
    (and the reverse would finalize something start never registered)."""

    VALID = ["a1000001", "ok-id_9", "x" * 128]
    INVALID = ["x/../../planted", "../p", "a b", "x" * 129, "a\\b"]

    @pytest.mark.parametrize("agent_id", VALID + INVALID)
    def test_stop_finalizes_exactly_the_ids_start_registered(self, harness, agent_id):
        expected_valid = agent_id in self.VALID
        assert is_safe_agent_id(agent_id) is expected_valid

        harness.start(agent_id, SESSION_1)
        registered = list(harness.state_dir.glob("subagent-*.json"))
        assert (len(registered) == 1) is expected_valid

        with patch("pacemaker.langfuse.orchestrator.handle_subagent_stop") as stop:
            harness.stop(agent_id, SESSION_1)
        assert stop.called is expected_valid


class TestLegacySlotWithoutAgentId:
    """L1: a SubagentStop payload with no agent_id may only use the global
    legacy slot when the slot's trace belongs to the payload's own session."""

    def _stop_without_agent_id(self, harness, payload_session):
        payload = {"agent_transcript_path": str(harness.tmp_path / "none.jsonl")}
        if payload_session is not None:
            payload["session_id"] = payload_session
        with patch("sys.stdin.read", return_value=json.dumps(payload)):
            with patch("pacemaker.langfuse.orchestrator.handle_subagent_stop") as stop:
                run_subagent_stop_hook()
        return stop

    def _set_slot(self, harness, trace_id):
        harness.state_file.write_text(
            json.dumps(
                {
                    "subagent_counter": 1,
                    "current_subagent_trace_id": trace_id,
                    "current_subagent_agent_id": "a9999999",
                    "current_subagent_parent_transcript_path": "/p.jsonl",
                }
            )
        )

    def test_slot_of_the_same_session_is_finalized(self, harness):
        trace = f"{SESSION_1}-subagent-general-purpose-1a2b3c4d"
        self._set_slot(harness, trace)

        stop = self._stop_without_agent_id(harness, SESSION_1)

        stop.assert_called_once()
        assert stop.call_args.kwargs["subagent_trace_id"] == trace

    def test_slot_of_another_session_is_not_finalized(self, harness):
        self._set_slot(harness, f"{SESSION_2}-subagent-general-purpose-1a2b3c4d")

        stop = self._stop_without_agent_id(harness, SESSION_1)

        stop.assert_not_called()

    def test_session_id_that_is_only_a_prefix_of_the_slots_session_is_refused(
        self, harness
    ):
        self._set_slot(harness, f"{SESSION_1}-extra-subagent-general-purpose-1a2b")

        stop = self._stop_without_agent_id(harness, SESSION_1)

        stop.assert_not_called()

    def test_payload_without_session_id_cannot_claim_the_slot(self, harness):
        self._set_slot(harness, f"{SESSION_1}-subagent-general-purpose-1a2b3c4d")

        stop = self._stop_without_agent_id(harness, None)

        stop.assert_not_called()


class TestLegacySlotWithAgentId:
    """Same rule as without an agent_id: the slot's agent must match AND its
    trace must belong to the payload's own session."""

    AGENT = "a9999999"  # has no per-agent state file, so the slot is consulted

    def _stop(self, harness, slot_trace, payload_session):
        harness.state_file.write_text(
            json.dumps(
                {
                    "subagent_counter": 1,
                    "current_subagent_trace_id": slot_trace,
                    "current_subagent_agent_id": self.AGENT,
                    "current_subagent_parent_transcript_path": "/p.jsonl",
                }
            )
        )
        payload = {
            "agent_id": self.AGENT,
            "agent_transcript_path": str(harness.tmp_path / "none.jsonl"),
        }
        if payload_session is not None:
            payload["session_id"] = payload_session
        with patch("sys.stdin.read", return_value=json.dumps(payload)):
            with patch("pacemaker.langfuse.orchestrator.handle_subagent_stop") as stop:
                run_subagent_stop_hook()
        return stop

    def test_matching_agent_and_own_session_trace_is_finalized(self, harness):
        trace = f"{SESSION_1}-subagent-general-purpose-1a2b3c4d"
        stop = self._stop(harness, trace, SESSION_1)
        stop.assert_called_once()
        assert stop.call_args.kwargs["subagent_trace_id"] == trace

    def test_matching_agent_but_another_sessions_trace_is_not_finalized(self, harness):
        stop = self._stop(
            harness, f"{SESSION_2}-subagent-general-purpose-1a2b3c4d", SESSION_1
        )
        stop.assert_not_called()

    def test_matching_agent_without_payload_session_is_not_finalized(self, harness):
        stop = self._stop(
            harness, f"{SESSION_1}-subagent-general-purpose-1a2b3c4d", None
        )
        stop.assert_not_called()


class TestSubagentStopResolution:
    def test_stop_passes_the_parent_path_stored_in_the_agents_own_state(self, harness):
        harness.start("a1000001", SESSION_1)

        with patch("pacemaker.langfuse.orchestrator.handle_subagent_stop") as stop:
            harness.stop("a1000001", SESSION_1)

        stop.assert_called_once()
        kwargs = stop.call_args.kwargs
        assert kwargs["subagent_trace_id"] == harness.trace_of("a1000001")
        assert kwargs["parent_transcript_path"] == str(harness.parent_transcript)
        assert kwargs["agent_id"] == "a1000001"

    def test_global_subagent_traces_map_alone_never_triggers_finalization(
        self, harness
    ):
        harness.state_file.write_text(
            json.dumps(
                {
                    "subagent_counter": 1,
                    "subagent_traces": {
                        "ghost0001": {"trace_id": "trace-from-global-map"}
                    },
                }
            )
        )

        with patch("pacemaker.langfuse.orchestrator.handle_subagent_stop") as stop:
            harness.stop("ghost0001", SESSION_1)

        stop.assert_not_called()

    def test_agents_own_state_wins_over_a_conflicting_global_entry(self, harness):
        harness.start("a1000001", SESSION_1)
        own_trace = harness.trace_of("a1000001")
        data = json.loads(harness.state_file.read_text())
        data["subagent_traces"] = {"a1000001": {"trace_id": "stale-global-trace"}}
        harness.state_file.write_text(json.dumps(data))

        with patch("pacemaker.langfuse.orchestrator.handle_subagent_stop") as stop:
            harness.stop("a1000001", SESSION_1)

        assert stop.call_args.kwargs["subagent_trace_id"] == own_trace

    def test_unsafe_agent_id_never_resolves_a_file_outside_langfuse_state(
        self, harness
    ):
        traversal_id = "x/../../planted"
        (harness.state_dir / "subagent-x").mkdir()
        planted = harness.state_dir.parent / "planted.json"
        planted.write_text(json.dumps({"trace_id": "planted-trace"}))
        # Premise: with this name the plain filesystem path WOULD reach the planted file.
        naive = harness.state_dir / f"subagent-{traversal_id}.json"
        assert json.loads(naive.read_text())["trace_id"] == "planted-trace"

        with patch("pacemaker.langfuse.orchestrator.handle_subagent_stop") as stop:
            harness.stop(traversal_id, SESSION_1)

        stop.assert_not_called()

    def test_stop_does_not_overwrite_changes_made_during_finalization(self, harness):
        harness.start("a1000001", SESSION_1)
        write_marker = _write_during(harness.state_file, "marker", "from-another-hook")

        with patch(
            "pacemaker.langfuse.orchestrator.handle_subagent_stop",
            side_effect=lambda **kwargs: write_marker(),
        ):
            harness.stop("a1000001", SESSION_1)

        global_state = json.loads(harness.state_file.read_text())
        assert global_state.get("marker") == "from-another-hook"
        assert "current_subagent_trace_id" not in global_state  # own slot cleared
