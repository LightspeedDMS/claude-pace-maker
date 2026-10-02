"""
Bug #157 fix 4 -- defence in depth for the 10 s SubagentStart/SubagentStop
budget.

Even with the prefilter, the Langfuse step does network I/O (the subagent-trace
push has a 10 s HTTP timeout of its own, the OAuth profile lookup 3 s), so a
slow or unreachable Langfuse could by itself blow the hook timeout and cost the
subagent its guidance. ``run_with_deadline`` bounds that work: SubagentStart and
SubagentStop return in time with whatever finished, and SubagentStart's
guidance output never depends on Langfuse.
"""

import contextlib
import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from pacemaker.bounded_call import run_with_deadline

SESSION = "sess-157-budget"
MANIFEST_MARK = "PACE-MAKER PROVENANCE CONTRACT (subagent)"


class TestRunWithDeadline:
    def test_returns_the_result_of_a_function_that_finishes_in_time(self):
        assert run_with_deadline(lambda a, b: a + b, 5.0, 2, 3) == (True, 5)

    def test_returns_promptly_with_not_finished_when_the_function_hangs(self):
        release = threading.Event()
        start = time.monotonic()
        finished, value = run_with_deadline(lambda: release.wait(30), 0.3)
        elapsed = time.monotonic() - start
        release.set()
        assert (finished, value) == (False, None)
        assert elapsed < 2.0

    def test_exception_in_the_function_propagates_to_the_caller(self):
        def boom():
            raise ValueError("kaput")

        with pytest.raises(ValueError, match="kaput"):
            run_with_deadline(boom, 5.0)

    def test_zero_or_negative_budget_still_returns_without_hanging(self):
        release = threading.Event()
        finished, _ = run_with_deadline(lambda: release.wait(30), 0)
        release.set()
        assert finished is False

    def test_worker_thread_is_a_daemon_so_it_cannot_block_interpreter_exit(self):
        seen = []
        release = threading.Event()

        def work():
            seen.append(threading.current_thread().daemon)
            release.wait(30)

        run_with_deadline(work, 0.2)
        release.set()
        assert seen == [True]


def _start_payload(agent_id="agent-b1"):
    return {
        "hook_event_name": "SubagentStart",
        "session_id": SESSION,
        "agent_id": agent_id,
        "agent_type": "tdd-engineer",
        "transcript_path": "/nonexistent.jsonl",
        "cwd": "/tmp",
    }


def _stop_payload(agent_id="agent-b1"):
    return {
        "hook_event_name": "SubagentStop",
        "session_id": SESSION,
        "agent_id": agent_id,
        "agent_type": "tdd-engineer",
        "transcript_path": "/nonexistent.jsonl",
    }


def _config(**overrides):
    cfg = {
        "enabled": True,
        "langfuse_enabled": True,
        "langfuse_base_url": "http://127.0.0.1:1",
        "langfuse_public_key": "pk",
        "langfuse_secret_key": "sk",
        "intent_validation_enabled": True,
        "cross_session_awareness_enabled": False,
    }
    cfg.update(overrides)
    return cfg


class TestSubagentStartStaysInsideItsBudget:
    def test_hanging_langfuse_does_not_delay_or_lose_the_guidance(
        self, tmp_path, capsys
    ):
        import pacemaker.hook as hook_mod

        release = threading.Event()
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps({"subagent_counter": 0}))

        def hang(hook_data, config):
            release.wait(30)
            return "trace-never"

        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch("pacemaker.hook.load_config", return_value=_config())
            )
            stack.enter_context(
                patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path))
            )
            stack.enter_context(
                patch("pacemaker.hook.SUBAGENT_HOOK_LANGFUSE_BUDGET_SECONDS", 0.4)
            )
            stack.enter_context(
                patch("pacemaker.hook._handle_langfuse_subagent_start", hang)
            )
            stack.enter_context(
                patch("sys.stdin", MagicMock(read=lambda: json.dumps(_start_payload())))
            )
            start = time.monotonic()
            hook_mod.run_subagent_start_hook()
            elapsed = time.monotonic() - start
        release.set()
        assert elapsed < 3.0, f"SubagentStart took {elapsed:.2f}s"
        out = capsys.readouterr().out
        assert MANIFEST_MARK in out
        assert "INTENT VALIDATION ENABLED" in out

    def test_completion_is_still_recorded_after_a_bounded_langfuse_step(self, tmp_path):
        import pacemaker.hook as hook_mod
        from pacemaker.intent_declarations import subagent_guidance

        release = threading.Event()
        state_path = tmp_path / "state.json"
        state_path.write_text("{}")
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch("pacemaker.hook.load_config", return_value=_config())
            )
            stack.enter_context(
                patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path))
            )
            stack.enter_context(
                patch("pacemaker.hook.SUBAGENT_HOOK_LANGFUSE_BUDGET_SECONDS", 0.3)
            )
            stack.enter_context(
                patch(
                    "pacemaker.hook._handle_langfuse_subagent_start",
                    lambda hd, cfg: release.wait(30),
                )
            )
            stack.enter_context(
                patch(
                    "sys.stdin",
                    MagicMock(read=lambda: json.dumps(_start_payload("agent-r"))),
                )
            )
            hook_mod.run_subagent_start_hook()
        release.set()
        assert (
            subagent_guidance.claim_late_guidance(
                {"session_id": SESSION, "agent_id": "agent-r"}
            )
            is False
        )

    def test_a_fast_langfuse_step_still_stores_the_trace(self, tmp_path):
        import pacemaker.hook as hook_mod

        state_path = tmp_path / "state.json"
        state_path.write_text("{}")
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch("pacemaker.hook.load_config", return_value=_config())
            )
            stack.enter_context(
                patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path))
            )
            stack.enter_context(
                patch(
                    "pacemaker.hook._handle_langfuse_subagent_start",
                    lambda hd, cfg: "trace-123",
                )
            )
            stack.enter_context(
                patch(
                    "sys.stdin",
                    MagicMock(read=lambda: json.dumps(_start_payload("agent-f"))),
                )
            )
            hook_mod.run_subagent_start_hook()
        stored = json.loads(state_path.read_text())
        # Bug #161: the shared file keeps only the legacy single slot; the
        # per-agent trace map was retired (it was clobber-prone).
        assert stored["current_subagent_trace_id"] == "trace-123"
        assert stored["current_subagent_agent_id"] == "agent-f"
        assert "subagent_traces" not in stored

    def test_budget_constants_leave_room_inside_the_ten_second_timeout(self):
        from pacemaker import hook

        # Wrapper find_python (~0.7 s) + interpreter/imports (~0.5 s) + emit
        # must still fit around the Langfuse budget.
        assert hook.SUBAGENT_HOOK_LANGFUSE_BUDGET_SECONDS <= 6.0
        assert hook.SUBAGENT_HOOK_LANGFUSE_BUDGET_SECONDS >= 1.0


class TestSubagentStopStaysInsideItsBudget:
    def test_hanging_finalize_returns_in_time_and_still_cleans_state(self, tmp_path):
        import pacemaker.hook as hook_mod

        release = threading.Event()
        state_path = tmp_path / "state.json"
        state_path.write_text(
            json.dumps(
                {
                    "subagent_counter": 1,
                    "subagent_traces": {
                        "agent-b1": {
                            "trace_id": f"{SESSION}-subagent-general-purpose-1a2b3c4d",
                            "parent_transcript_path": "/x",
                        }
                    },
                    "current_subagent_trace_id": f"{SESSION}-subagent-general-purpose-1a2b3c4d",
                    "current_subagent_agent_id": "agent-b1",
                }
            )
        )
        from pacemaker.langfuse import orchestrator

        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch("pacemaker.hook.load_config", return_value=_config())
            )
            stack.enter_context(
                patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path))
            )
            stack.enter_context(
                patch("pacemaker.hook.SUBAGENT_HOOK_LANGFUSE_BUDGET_SECONDS", 0.4)
            )
            stack.enter_context(
                patch.object(
                    orchestrator,
                    "handle_subagent_stop",
                    lambda **kwargs: release.wait(30),
                )
            )
            stack.enter_context(
                patch("sys.stdin", MagicMock(read=lambda: json.dumps(_stop_payload())))
            )
            start = time.monotonic()
            hook_mod.run_subagent_stop_hook()
            elapsed = time.monotonic() - start
        release.set()
        assert elapsed < 3.0, f"SubagentStop took {elapsed:.2f}s"
        stored = json.loads(state_path.read_text())
        assert "agent-b1" not in stored.get("subagent_traces", {})
        assert "current_subagent_trace_id" not in stored
        assert stored["subagent_counter"] == 0

    def test_a_fast_finalize_still_runs_to_completion(self, tmp_path):
        import pacemaker.hook as hook_mod
        from pacemaker.langfuse import orchestrator

        from pathlib import Path

        from pacemaker.langfuse.state import StateManager

        calls = []
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps({"subagent_counter": 1}))
        # Bug #161: the trace is resolved from the agent's OWN state file. The
        # hook locates it via expanduser("~/.claude-pace-maker/langfuse_state"),
        # so it must live under HOME -- which is hermetic here because
        # tests/conftest.py's autouse _guard_production_db points HOME at a
        # per-test temp dir.
        StateManager(
            str(Path.home() / ".claude-pace-maker" / "langfuse_state")
        ).create_or_update(
            session_id="subagent-agent-b1",
            # production shape: "<parent_session_id>-subagent-<type>-<uuid8>"
            trace_id=f"{SESSION}-subagent-general-purpose-1a2b3c4d",
            last_pushed_line=0,
            metadata={"parent_transcript_path": "/x"},
        )
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch("pacemaker.hook.load_config", return_value=_config())
            )
            stack.enter_context(
                patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path))
            )
            stack.enter_context(
                patch.object(
                    orchestrator, "handle_subagent_stop", lambda **kw: calls.append(kw)
                )
            )
            stack.enter_context(
                patch("sys.stdin", MagicMock(read=lambda: json.dumps(_stop_payload())))
            )
            hook_mod.run_subagent_stop_hook()
        assert len(calls) == 1
        assert (
            calls[0]["subagent_trace_id"]
            == f"{SESSION}-subagent-general-purpose-1a2b3c4d"
        )
