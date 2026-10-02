#!/usr/bin/env python3
"""
Bug #162: hooks must save only their OWN changes onto the LATEST state.json.

Root cause: PostToolUse (hook.run_hook) loaded the machine-wide state.json,
then ran the pacing engine (API poll) and `execute_delay` (up to 350 s), then
saved its stale snapshot unconditionally, overwriting whatever every other
concurrent session wrote in that window (cross_session_awareness workspace
roots, subagent_counter / in_subagent, silent_tool_nudge_count, ...). The Stop
hook (LLM review) and SessionStart (version probe) have the same
load -> slow step -> save shape.

Fix contract (same pattern as #161's SubagentStart/SubagentStop): after any
slow step, re-load state.json right before the save and apply only this hook's
own mutation (`hook.update_state`).

All tests use a real state.json in a tmp dir; "another session" is a real
rewrite of that file performed from inside the slow step. Only external
services are replaced: the usage-API poll (pacing engine), the LLM reviewer,
the sleep itself and Langfuse.
"""

import json
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import pacemaker.hook as hook_mod

SESSION = "sess-162-main"
OTHER_SESSION = "sess-162-other"
CLEANUP_TIME = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)

# What "another session" writes while this hook is busy. None of these keys is
# touched by the hook under test, so every one must survive its save.
OTHER_SESSION_FIELDS = {
    "cross_session_awareness": {
        OTHER_SESSION: {"workspace_root": "/work/other", "seen_agent_ids": ["root"]}
    },
    "subagent_counter": 2,
    "in_subagent": True,
    "silent_tool_nudge_count": 2,
    "tempo_session_enabled": True,
}


def _initial_state() -> dict:
    return {
        "session_id": SESSION,
        "tool_execution_count": 3,
        "consecutive_stop_blocks": 2,
        "subagent_counter": 0,
        "in_subagent": False,
    }


def _concurrent_writer(state_file: Path, fields: dict):
    """Stands in for 'another session's hook rewrote state.json meanwhile'."""

    def write(*_args, **_kwargs) -> None:
        data = json.loads(state_file.read_text())
        data.update(fields)
        state_file.write_text(json.dumps(data))

    return write


@pytest.fixture
def state_file(tmp_path) -> Path:
    path = tmp_path / "state.json"
    path.write_text(json.dumps(_initial_state()))
    return path


def _run_post_tool_use(state_file: Path, pacing, delay, tool_name="Bash"):
    """Drive the real PostToolUse handler against the real state file."""
    config = {
        "enabled": True,
        "cross_session_awareness_enabled": False,
        "langfuse_enabled": False,
        "subagent_reminder_enabled": True,
    }
    payload = {
        "tool_name": tool_name,
        "tool_input": {"command": "true"},
        "tool_response": "ok",
    }
    with ExitStack() as stack:
        stack.enter_context(patch("pacemaker.hook.load_config", return_value=config))
        stack.enter_context(patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_file)))
        stack.enter_context(
            patch("pacemaker.hook.pacing_engine.run_pacing_check", side_effect=pacing)
        )
        stack.enter_context(patch("pacemaker.hook.execute_delay", side_effect=delay))
        stack.enter_context(patch("pacemaker.hook._accumulate_fallback_cost"))
        stack.enter_context(patch("sys.stdin.read", return_value=json.dumps(payload)))
        hook_mod.run_hook()
    return json.loads(state_file.read_text())


def _no_throttle(**_kwargs):
    return {"decision": {}, "polled": False}


def _no_delay(_seconds):
    return None


class TestPostToolUsePreservesConcurrentUpdates:
    def test_state_written_during_throttle_delay_survives(self, state_file):
        def throttled(**_kwargs):
            return {
                "decision": {"should_throttle": True, "delay_seconds": 30},
                "polled": False,
            }

        final = _run_post_tool_use(
            state_file,
            pacing=throttled,
            delay=_concurrent_writer(state_file, OTHER_SESSION_FIELDS),
        )

        for key, value in OTHER_SESSION_FIELDS.items():
            assert final[key] == value, f"{key} written during the delay was lost"
        # this hook's own changes are still applied, onto the latest state
        assert final["tool_execution_count"] == 4
        assert final["consecutive_stop_blocks"] == 0

    def test_state_written_during_pacing_poll_survives_and_cleanup_is_saved(
        self, state_file
    ):
        write_other = _concurrent_writer(state_file, OTHER_SESSION_FIELDS)

        def slow_poll(**_kwargs):
            write_other()
            return {"decision": {}, "polled": True, "cleanup_time": CLEANUP_TIME}

        final = _run_post_tool_use(state_file, pacing=slow_poll, delay=_no_delay)

        for key, value in OTHER_SESSION_FIELDS.items():
            assert final[key] == value, f"{key} written during the poll was lost"
        assert final["last_cleanup_time"] == CLEANUP_TIME.isoformat()
        assert final["tool_execution_count"] == 4

    def test_stop_block_counter_raised_during_delay_is_not_reset_by_stale_copy(
        self, state_file
    ):
        # A Stop hook of another session blocked while this hook slept: its
        # counter value is newer than this hook's own reset and must survive.
        def throttled(**_kwargs):
            return {
                "decision": {"should_throttle": True, "delay_seconds": 30},
                "polled": False,
            }

        final = _run_post_tool_use(
            state_file,
            pacing=throttled,
            delay=_concurrent_writer(state_file, {"consecutive_stop_blocks": 1}),
        )

        assert final["consecutive_stop_blocks"] == 1

    def test_own_changes_are_persisted_even_when_pacing_raises(self, state_file):
        def failing_poll(**_kwargs):
            raise RuntimeError("usage API unreachable")

        final = _run_post_tool_use(state_file, pacing=failing_poll, delay=_no_delay)

        assert final["tool_execution_count"] == 4
        assert final["consecutive_stop_blocks"] == 0


class TestReminderGateUsesLatestState:
    """`in_subagent` gates the delegation reminder; a value another session set
    during the delay must be honoured, not the stale pre-delay copy."""

    def _throttled(self, **_kwargs):
        return {
            "decision": {"should_throttle": True, "delay_seconds": 30},
            "polled": False,
        }

    def test_control_reminder_is_emitted_when_nothing_changed(self, state_file, capsys):
        _run_post_tool_use(
            state_file, pacing=self._throttled, delay=_no_delay, tool_name="Write"
        )

        assert "subagent_delegation_reminder" in capsys.readouterr().out

    def test_subagent_started_during_delay_suppresses_the_reminder(
        self, state_file, capsys
    ):
        _run_post_tool_use(
            state_file,
            pacing=self._throttled,
            delay=_concurrent_writer(
                state_file, {"in_subagent": True, "subagent_counter": 1}
            ),
            tool_name="Write",
        )

        assert "subagent_delegation_reminder" not in capsys.readouterr().out


STOP_SESSION = "sess-162-stop"
STOP_OTHER_FIELDS = {
    "cross_session_awareness": {OTHER_SESSION: {"workspace_root": "/work/other"}},
    "subagent_counter": 1,
    "in_subagent": True,
    "tempo_session_enabled": True,
}


def _run_stop_hook(
    state_file: Path,
    tmp_path: Path,
    *,
    validator=None,
    finalize=None,
    silent_stop=False,
):
    """Drive the real Stop handler against the real state file. Replaced:
    the LLM reviewer, Langfuse finalize, and the transcript/tempo probes."""
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "go"}})
        + "\n"
    )
    config = {"enabled": True, "tempo_mode": "on", "hook_model": "auto"}
    payload = {"session_id": STOP_SESSION, "transcript_path": str(transcript)}
    with ExitStack() as stack:
        stack.enter_context(patch("pacemaker.hook.load_config", return_value=config))
        stack.enter_context(patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_file)))
        stack.enter_context(
            patch("pacemaker.hook.is_context_exhaustion_detected", return_value=False)
        )
        stack.enter_context(
            patch(
                "pacemaker.transcript_reader.detect_silent_tool_stop",
                return_value=silent_stop,
            )
        )
        stack.enter_context(patch("pacemaker.hook.should_run_tempo", return_value=True))
        stack.enter_context(
            patch(
                "pacemaker.langfuse.orchestrator.handle_stop_finalize",
                side_effect=finalize,
            )
        )
        stack.enter_context(
            patch("pacemaker.intent_validator.validate_intent", side_effect=validator)
        )
        stack.enter_context(patch("pacemaker.hook.record_blockage"))
        stack.enter_context(patch("pacemaker.hook.record_activity_event"))
        stack.enter_context(patch("sys.stdin.read", return_value=json.dumps(payload)))
        result = hook_mod.run_stop_hook()
    return result, json.loads(state_file.read_text())


class TestStopHookPreservesConcurrentUpdates:
    def test_block_counter_save_keeps_state_written_during_llm_review(
        self, state_file, tmp_path
    ):
        write_other = _concurrent_writer(state_file, STOP_OTHER_FIELDS)

        def slow_review(**_kwargs):
            write_other()
            return {"decision": "block", "reason": "not done"}

        result, final = _run_stop_hook(state_file, tmp_path, validator=slow_review)

        assert result["decision"] == "block"
        assert final["consecutive_stop_blocks"] == 3  # 2 -> 3
        for key, value in STOP_OTHER_FIELDS.items():
            assert final[key] == value, f"{key} written during the review was lost"

    def test_approval_reset_keeps_state_written_during_llm_review(
        self, state_file, tmp_path
    ):
        write_other = _concurrent_writer(state_file, STOP_OTHER_FIELDS)

        def slow_review(**_kwargs):
            write_other()
            return {"decision": "approve"}

        _, final = _run_stop_hook(state_file, tmp_path, validator=slow_review)

        assert final["consecutive_stop_blocks"] == 0
        for key, value in STOP_OTHER_FIELDS.items():
            assert final[key] == value, f"{key} written during the review was lost"

    def test_silent_stop_nudge_save_keeps_state_written_during_finalize(
        self, state_file, tmp_path
    ):
        result, final = _run_stop_hook(
            state_file,
            tmp_path,
            silent_stop=True,
            finalize=_concurrent_writer(state_file, STOP_OTHER_FIELDS),
        )

        assert result["decision"] == "block"
        assert final["silent_tool_nudge_count"] == 1
        for key, value in STOP_OTHER_FIELDS.items():
            assert final[key] == value, f"{key} written during finalize was lost"


START_SESSION = "sess-162-start"
START_OTHER_FIELDS = {
    "cross_session_awareness": {OTHER_SESSION: {"workspace_root": "/work/other"}},
    "subagent_counter": 0,
    "tempo_session_enabled": True,
    "silent_tool_nudge_count": 2,
}


def _run_session_start(state_file: Path, tmp_path: Path, config=None, **patches):
    """Drive the real SessionStart handler (source=startup) on the real state
    file. `patches` maps a dotted target to a replacement callable."""
    config = {
        "enabled": True,
        "cross_session_awareness_enabled": False,
        "intent_validation_enabled": False,
        **(config or {}),
    }
    payload = {"session_id": START_SESSION, "source": "startup", "cwd": str(tmp_path)}
    with ExitStack() as stack:
        stack.enter_context(patch("pacemaker.hook.load_config", return_value=config))
        stack.enter_context(patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_file)))
        stack.enter_context(patch("sys.stdin.read", return_value=json.dumps(payload)))
        for target, replacement in patches.items():
            stack.enter_context(patch(target, side_effect=replacement))
        hook_mod.run_session_start_hook()
    return json.loads(state_file.read_text())


class TestSessionStartPreservesConcurrentUpdates:
    def test_state_written_during_version_probe_survives(self, state_file, tmp_path):
        write_other = _concurrent_writer(state_file, START_OTHER_FIELDS)

        def slow_probe(state, config, stderr=None):
            write_other()
            state["version_block_active"] = True
            state["version_block_message"] = "claude too old"

        final = _run_session_start(
            state_file,
            tmp_path,
            **{
                "pacemaker.version_check.perform_session_start_version_check": slow_probe
            },
        )

        for key, value in START_OTHER_FIELDS.items():
            assert final[key] == value, f"{key} written during the probe was lost"
        assert final["version_block_active"] is True
        assert final["version_block_message"] == "claude too old"
        assert final["session_id"] == START_SESSION

    def test_state_written_during_csa_registration_survives(self, state_file, tmp_path):
        from pacemaker.session_registry import _csa

        real_on_session_start = _csa.on_session_start
        write_other = _concurrent_writer(state_file, START_OTHER_FIELDS)

        def slow_registration(**kwargs):
            write_other()
            return real_on_session_start(**kwargs)

        final = _run_session_start(
            state_file,
            tmp_path,
            config={"cross_session_awareness_enabled": True},
            **{"pacemaker.session_registry._csa.on_session_start": slow_registration},
        )

        csa = final["cross_session_awareness"]
        assert csa[OTHER_SESSION] == {"workspace_root": "/work/other"}
        assert csa[START_SESSION]["workspace_root"], "own CSA registration not saved"
        assert final["tempo_session_enabled"] is True
        assert final["silent_tool_nudge_count"] == 2


PRE_SESSION = "sess-162-pre"


def _run_pre_tool_use(state_file: Path, on_pre_tool_use):
    config = {
        "enabled": True,
        "cross_session_awareness_enabled": True,
        "intent_validation_enabled": False,
        "danger_bash_enabled": False,
    }
    payload = {
        "session_id": PRE_SESSION,
        "agent_id": "agent-a",
        "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/x"},
        "transcript_path": "/tmp/none.jsonl",
    }
    with ExitStack() as stack:
        stack.enter_context(patch("pacemaker.hook.load_config", return_value=config))
        stack.enter_context(patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_file)))
        stack.enter_context(
            patch(
                "pacemaker.session_registry._csa.on_pre_tool_use",
                side_effect=on_pre_tool_use,
            )
        )
        stack.enter_context(patch("sys.stdin.read", return_value=json.dumps(payload)))
        hook_mod.run_pre_tool_hook()
    return json.loads(state_file.read_text())


class TestPreToolUseCsaCounterPreservesConcurrentUpdates:
    def test_agent_counter_is_saved_without_erasing_concurrent_updates(
        self, state_file
    ):
        from pacemaker.session_registry import _csa

        initial = json.loads(state_file.read_text())
        initial["cross_session_awareness"] = {
            PRE_SESSION: {
                "workspace_root": "/work/pre",
                "seen_agent_ids": ["root", "agent-a"],
                "tool_use_counter": {"root": 4, "agent-a": 1},
            }
        }
        state_file.write_text(json.dumps(initial))
        real_on_pre_tool_use = _csa.on_pre_tool_use
        write_other = _concurrent_writer(
            state_file,
            {
                "silent_tool_nudge_count": 2,
                "subagent_counter": 1,
                "cross_session_awareness": {
                    PRE_SESSION: {
                        "workspace_root": "/work/pre",
                        "seen_agent_ids": ["root", "agent-a", "agent-b"],
                        "tool_use_counter": {"root": 4, "agent-a": 1, "agent-b": 7},
                    },
                    OTHER_SESSION: {"workspace_root": "/work/other"},
                },
            },
        )

        def slow_registry_step(**kwargs):
            write_other()
            return real_on_pre_tool_use(**kwargs)

        final = _run_pre_tool_use(state_file, slow_registry_step)

        assert final["silent_tool_nudge_count"] == 2
        assert final["subagent_counter"] == 1
        csa = final["cross_session_awareness"]
        assert csa[OTHER_SESSION] == {"workspace_root": "/work/other"}
        counters = csa[PRE_SESSION]["tool_use_counter"]
        assert counters["agent-b"] == 7, "another agent's counter was lost"
        assert counters["agent-a"] == 2, "own counter increment was not saved"
        assert csa[PRE_SESSION]["seen_agent_ids"] == ["root", "agent-a", "agent-b"]

    def test_ended_session_is_not_resurrected_by_the_counter_save(self, state_file):
        from pacemaker.session_registry import _csa

        initial = json.loads(state_file.read_text())
        initial["cross_session_awareness"] = {
            PRE_SESSION: {
                "workspace_root": "/work/pre",
                "tool_use_counter": {"agent-a": 1},
            }
        }
        state_file.write_text(json.dumps(initial))
        real_on_pre_tool_use = _csa.on_pre_tool_use

        def session_ended_meanwhile(**kwargs):
            data = json.loads(state_file.read_text())
            data["cross_session_awareness"].pop(PRE_SESSION)  # Stop hook GC
            state_file.write_text(json.dumps(data))
            return real_on_pre_tool_use(**kwargs)

        final = _run_pre_tool_use(state_file, session_ended_meanwhile)

        assert PRE_SESSION not in final["cross_session_awareness"]
