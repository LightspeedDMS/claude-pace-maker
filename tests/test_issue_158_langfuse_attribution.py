#!/usr/bin/env python3
"""
Bug #158: Langfuse spans attributed to the wrong traces across concurrent
sessions and subagents.

Root cause (proven by these tests): handle_post_tool_use() decided "this call
belongs to subagent X" from the GLOBAL ~/.claude-pace-maker/state.json fields
`in_subagent` / `current_subagent_trace_id` / `current_subagent_agent_id`.
That file is shared by every concurrent Claude Code session on the machine and
holds a single "current subagent" slot, so whichever subagent registered last
(in ANY session / project) received every other session's spans, and a subagent
whose slot had been overwritten or popped by a sibling's SubagentStop had its
own spans written to its parent's main trace.

Fix contract: the trace is selected ONLY from the hook payload identity,
(session_id, agent_id). agent_id present -> that agent's own state file
`subagent-<agent_id>.json`; agent_id absent -> the session's main trace. The
global state.json is never consulted for attribution.

These tests drive the real orchestrator with real StateManager state files
(only the HTTP push to Langfuse is mocked, as in the neighbouring tests).
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from pacemaker.langfuse import orchestrator
from pacemaker.langfuse.state import StateManager

SESSION_1 = "session-one-aaaa"
SESSION_2 = "session-two-bbbb"
AGENT_1 = "agentone1111"  # subagent of SESSION_1
AGENT_2 = "agenttwo2222"  # subagent of SESSION_2
TRACE_MAIN_1 = "trace-main-1"
TRACE_MAIN_2 = "trace-main-2"
TRACE_SUB_1 = "trace-sub-1"
TRACE_SUB_2 = "trace-sub-2"


@pytest.fixture
def config(tmp_path):
    return {
        "langfuse_enabled": True,
        "langfuse_base_url": "https://langfuse.example.com",
        "langfuse_public_key": "pk-test",
        "langfuse_secret_key": "sk-test",
        "db_path": str(tmp_path / "usage.db"),
    }


@pytest.fixture
def state_dir(tmp_path):
    """Two sessions, each with a main trace and one registered subagent trace."""
    d = tmp_path / "langfuse_state"
    sm = StateManager(str(d))
    for sid, main_trace in ((SESSION_1, TRACE_MAIN_1), (SESSION_2, TRACE_MAIN_2)):
        sm.create_or_update(
            session_id=sid,
            trace_id=main_trace,
            last_pushed_line=0,
            metadata={"current_trace_id": main_trace, "trace_start_line": 0},
        )
    for aid, sub_trace in ((AGENT_1, TRACE_SUB_1), (AGENT_2, TRACE_SUB_2)):
        sm.create_or_update(
            session_id=f"subagent-{aid}",
            trace_id=sub_trace,
            last_pushed_line=0,
            metadata={"current_trace_id": sub_trace, "trace_start_line": 0},
        )
    return str(d)


@pytest.fixture
def transcript(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "hello"}],
                },
            }
        )
        + "\n"
    )
    return str(p)


def _write_global_state(tmp_path, data):
    path = tmp_path / "state.json"
    path.write_text(json.dumps(data))
    return str(path)


def _post_tool_use(config, state_dir, transcript, global_state_path, **kw):
    """Run handle_post_tool_use; return (result, trace ids of pushed spans)."""
    with patch("pacemaker.langfuse.orchestrator.push") as mock_push:
        mock_push.push_batch_events = MagicMock(return_value=(True, 1))
        # create=True: the pre-fix orchestrator imported this name and read the
        # global file (RED proof); the fixed one no longer has it at all, and
        # the polluted file written above must simply have no effect.
        with patch(
            "pacemaker.langfuse.orchestrator.DEFAULT_STATE_PATH",
            global_state_path,
            create=True,
        ):
            result = orchestrator.handle_post_tool_use(
                config=config,
                transcript_path=transcript,
                state_dir=state_dir,
                tool_response={"ok": True},
                tool_name=kw.pop("tool_name", "Bash"),
                tool_input={"command": "x"},
                **kw,
            )
        traces = []
        for call in mock_push.push_batch_events.call_args_list:
            for event in call[0][3]:
                if event["type"] == "span-create":
                    traces.append(event["body"]["traceId"])
    return result, traces


class TestSpansFollowPayloadIdentityNotGlobalState:
    def test_subagent_span_goes_to_its_own_trace_not_last_registered(
        self, tmp_path, config, state_dir, transcript
    ):
        """Global slot says AGENT_2 (other session) is current; AGENT_1 calls."""
        g = _write_global_state(
            tmp_path,
            {
                "in_subagent": True,
                "current_subagent_trace_id": TRACE_SUB_2,
                "current_subagent_agent_id": AGENT_2,
            },
        )
        result, traces = _post_tool_use(
            config,
            state_dir,
            transcript,
            g,
            session_id=SESSION_1,
            agent_id=AGENT_1,
        )
        assert result is True
        assert traces == [TRACE_SUB_1]

    def test_subagent_span_not_in_parent_main_trace_when_global_flag_cleared(
        self, tmp_path, config, state_dir, transcript
    ):
        """A sibling's SubagentStop cleared the global slot; AGENT_1 still calls."""
        g = _write_global_state(tmp_path, {"in_subagent": False})
        result, traces = _post_tool_use(
            config,
            state_dir,
            transcript,
            g,
            session_id=SESSION_1,
            agent_id=AGENT_1,
        )
        assert result is True
        assert traces == [TRACE_SUB_1]

    def test_main_thread_never_lands_in_another_sessions_subagent_trace(
        self, tmp_path, config, state_dir, transcript
    ):
        """SESSION_2 main thread (no agent_id) while global slot = AGENT_1."""
        g = _write_global_state(
            tmp_path,
            {
                "in_subagent": True,
                "current_subagent_trace_id": TRACE_SUB_1,
                "current_subagent_agent_id": AGENT_1,
            },
        )
        result, traces = _post_tool_use(
            config, state_dir, transcript, g, session_id=SESSION_2
        )
        assert result is True
        assert traces == [TRACE_MAIN_2]

    def test_main_thread_payload_without_agent_id_uses_session_main_trace(
        self, tmp_path, config, state_dir, transcript
    ):
        """No agent_id == main thread, even if global state names its own subagent."""
        g = _write_global_state(
            tmp_path,
            {
                "in_subagent": True,
                "current_subagent_trace_id": TRACE_SUB_1,
                "current_subagent_agent_id": AGENT_1,
            },
        )
        result, traces = _post_tool_use(
            config, state_dir, transcript, g, session_id=SESSION_1
        )
        assert result is True
        assert traces == [TRACE_MAIN_1]

    def test_works_without_global_state_file(
        self, tmp_path, config, state_dir, transcript
    ):
        missing = str(tmp_path / "does-not-exist.json")
        result, traces = _post_tool_use(
            config,
            state_dir,
            transcript,
            missing,
            session_id=SESSION_1,
            agent_id=AGENT_1,
        )
        assert result is True
        assert traces == [TRACE_SUB_1]

    def test_unregistered_subagent_never_falls_back_to_parent_or_foreign_trace(
        self, tmp_path, config, state_dir, transcript
    ):
        """agent_id with no state file: skip the span, do not misattribute it."""
        g = _write_global_state(
            tmp_path,
            {
                "in_subagent": True,
                "current_subagent_trace_id": TRACE_SUB_2,
                "current_subagent_agent_id": AGENT_2,
            },
        )
        result, traces = _post_tool_use(
            config,
            state_dir,
            transcript,
            g,
            session_id=SESSION_1,
            agent_id="neverregistered",
        )
        assert result is False
        assert traces == []


class TestUnregisteredSubagentStillRunsSessionLevelSteps:
    """Review finding L2: only the span + state update are skipped for a subagent
    without a registered trace; 🔐 declaration collection and the parent's
    pending_trace flush are session-level work and must still run."""

    SECRET = "l2-declared-secret-value-123"

    def _call(self, tmp_path, config, state_dir):
        from pacemaker.secrets.database import get_all_secrets

        transcript = tmp_path / "t2.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": f"🔐 SECRET_TEXT: {self.SECRET}"}
                        ],
                    },
                }
            )
            + "\n"
        )
        pending = [
            {
                "id": "pending-1",
                "timestamp": "2026-10-01T00:00:00+00:00",
                "type": "trace-create",
                "body": {"id": TRACE_MAIN_1, "name": "pending-turn"},
            }
        ]
        sm = StateManager(state_dir)
        sm.create_or_update(
            session_id=SESSION_1,
            trace_id=TRACE_MAIN_1,
            last_pushed_line=0,
            metadata={"current_trace_id": TRACE_MAIN_1, "trace_start_line": 0},
            pending_trace=pending,
        )
        with patch("pacemaker.langfuse.orchestrator.push") as mock_push:
            mock_push.push_batch_events = MagicMock(return_value=(True, 1))
            result = orchestrator.handle_post_tool_use(
                config=config,
                session_id=SESSION_1,
                transcript_path=str(transcript),
                state_dir=state_dir,
                tool_response={"ok": True},
                tool_name="Bash",
                tool_input={"command": "x"},
                agent_id="neverregistered",
            )
        pushed = [
            e for c in mock_push.push_batch_events.call_args_list for e in c[0][3]
        ]
        return result, pushed, sm, get_all_secrets(config["db_path"])

    def test_secret_declarations_are_still_collected(self, tmp_path, config, state_dir):
        result, _pushed, _sm, stored = self._call(tmp_path, config, state_dir)

        assert result is False  # the span itself was skipped
        assert self.SECRET in stored

    def test_parent_pending_trace_is_still_flushed(self, tmp_path, config, state_dir):
        _result, pushed, sm, _stored = self._call(tmp_path, config, state_dir)

        assert any(e["id"] == "pending-1" for e in pushed)
        assert not sm.read(SESSION_1).get("pending_trace")

    def test_no_span_or_intel_event_is_pushed_for_the_unregistered_agent(
        self, tmp_path, config, state_dir
    ):
        _result, pushed, _sm, _stored = self._call(tmp_path, config, state_dir)

        assert [e for e in pushed if e["type"] == "span-create"] == []
        assert not any(str(e["id"]).startswith("intel-") for e in pushed)


class TestInterleavedSessionsAndSubagents:
    def test_interleaved_calls_each_land_in_their_own_trace(
        self, tmp_path, config, state_dir, transcript
    ):
        """Four callers alternate against a global slot that keeps flipping."""
        calls = [
            (SESSION_1, AGENT_1, TRACE_SUB_1),
            (SESSION_2, None, TRACE_MAIN_2),
            (SESSION_2, AGENT_2, TRACE_SUB_2),
            (SESSION_1, None, TRACE_MAIN_1),
            (SESSION_1, AGENT_1, TRACE_SUB_1),
            (SESSION_2, AGENT_2, TRACE_SUB_2),
            (SESSION_2, None, TRACE_MAIN_2),
            (SESSION_1, None, TRACE_MAIN_1),
        ]
        slots = [
            {
                "in_subagent": True,
                "current_subagent_trace_id": TRACE_SUB_2,
                "current_subagent_agent_id": AGENT_2,
            },
            {
                "in_subagent": True,
                "current_subagent_trace_id": TRACE_SUB_1,
                "current_subagent_agent_id": AGENT_1,
            },
            {"in_subagent": False},
        ]
        for i, (sid, aid, expected) in enumerate(calls):
            g = _write_global_state(tmp_path, slots[i % len(slots)])
            kwargs = {"session_id": sid}
            if aid:
                kwargs["agent_id"] = aid
            result, traces = _post_tool_use(config, state_dir, transcript, g, **kwargs)
            assert result is True, f"call {i} failed"
            assert traces == [expected], f"call {i} ({sid}, {aid}) misattributed"

    def test_state_bookkeeping_goes_to_the_calling_agents_state_file(
        self, tmp_path, config, state_dir, transcript
    ):
        """Updating last_pushed_line must not touch the parent or sibling state."""
        sm = StateManager(state_dir)
        g = _write_global_state(tmp_path, {"in_subagent": False})
        _post_tool_use(
            config, state_dir, transcript, g, session_id=SESSION_1, agent_id=AGENT_1
        )
        assert sm.read(f"subagent-{AGENT_1}")["trace_id"] == TRACE_SUB_1
        assert sm.read(SESSION_1)["trace_id"] == TRACE_MAIN_1
        assert sm.read(f"subagent-{AGENT_2}")["trace_id"] == TRACE_SUB_2


class TestHookWiringPassesAgentId:
    def test_run_hook_forwards_payload_agent_id(self, tmp_path):
        """hook.run_hook must hand the payload's agent_id to the orchestrator."""
        from pacemaker.hook import run_hook

        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "session_id": SESSION_1,
            "agent_id": AGENT_1,
            "transcript_path": "/tmp/x.jsonl",
        }
        cfg = {
            "enabled": True,
            "langfuse_enabled": True,
            "subagent_reminder_enabled": False,
        }
        with (
            patch("pacemaker.hook.load_config", return_value=cfg),
            patch(
                "pacemaker.hook.load_state",
                return_value={
                    "session_id": "x",
                    "in_subagent": False,
                    "subagent_counter": 0,
                    "tool_execution_count": 0,
                },
            ),
            patch("pacemaker.hook.save_state"),
            patch("sys.stdin.read", return_value=json.dumps(payload)),
            patch("pacemaker.hook.database.initialize_database"),
            patch(
                "pacemaker.hook.pacing_engine.run_pacing_check",
                return_value={"polled": False, "decision": {}},
            ),
            patch("pacemaker.langfuse.orchestrator.handle_post_tool_use") as handle,
        ):
            run_hook()
        handle.assert_called_once()
        assert handle.call_args.kwargs["agent_id"] == AGENT_1
        assert handle.call_args.kwargs["session_id"] == SESSION_1

    def test_run_hook_main_thread_payload_passes_no_agent_id(self):
        from pacemaker.hook import run_hook

        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "session_id": SESSION_1,
            "transcript_path": "/tmp/x.jsonl",
        }
        cfg = {
            "enabled": True,
            "langfuse_enabled": True,
            "subagent_reminder_enabled": False,
        }
        with (
            patch("pacemaker.hook.load_config", return_value=cfg),
            patch(
                "pacemaker.hook.load_state",
                return_value={
                    "session_id": "x",
                    "in_subagent": False,
                    "subagent_counter": 0,
                    "tool_execution_count": 0,
                },
            ),
            patch("pacemaker.hook.save_state"),
            patch("sys.stdin.read", return_value=json.dumps(payload)),
            patch("pacemaker.hook.database.initialize_database"),
            patch(
                "pacemaker.hook.pacing_engine.run_pacing_check",
                return_value={"polled": False, "decision": {}},
            ),
            patch("pacemaker.langfuse.orchestrator.handle_post_tool_use") as handle,
        ):
            run_hook()
        assert handle.call_args.kwargs["agent_id"] is None


class TestSubagentStopDoesNotFinalizeAForeignTrace:
    """SubagentStop's legacy single-slot fallback must not hijack another agent."""

    def _run_stop(self, tmp_path, state, hook_data):
        from pacemaker.hook import run_subagent_stop_hook, save_state

        state_file = str(tmp_path / "state.json")
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"enabled": True, "langfuse_enabled": True}))
        save_state(state, state_file)
        with (
            patch("pacemaker.hook.DEFAULT_STATE_PATH", state_file),
            patch("pacemaker.hook.DEFAULT_CONFIG_PATH", str(cfg_file)),
            patch("pacemaker.hook.get_transcript_path", return_value=None),
            patch("sys.stdin.read", return_value=json.dumps(hook_data)),
            patch("pacemaker.langfuse.orchestrator.handle_subagent_stop") as stop,
        ):
            run_subagent_stop_hook()
        return stop

    def test_stop_of_unregistered_agent_does_not_finalize_other_agents_trace(
        self, tmp_path
    ):
        state = {
            "subagent_counter": 1,
            "in_subagent": True,
            "current_subagent_trace_id": TRACE_SUB_2,
            "current_subagent_agent_id": AGENT_2,
            "current_subagent_parent_transcript_path": "/tmp/other.jsonl",
        }
        stop = self._run_stop(
            tmp_path,
            state,
            {"session_id": SESSION_1, "agent_id": AGENT_1},
        )
        stop.assert_not_called()

    def test_stop_without_agent_id_still_uses_legacy_single_slot(self, tmp_path):
        state = {
            "subagent_counter": 1,
            "in_subagent": True,
            "current_subagent_trace_id": TRACE_SUB_2,
            "current_subagent_agent_id": AGENT_2,
            "current_subagent_parent_transcript_path": "/tmp/other.jsonl",
        }
        stop = self._run_stop(tmp_path, state, {"session_id": SESSION_2})
        stop.assert_called_once()
        assert stop.call_args.kwargs["subagent_trace_id"] == TRACE_SUB_2
