#!/usr/bin/env python3
"""
Bug #162 (finish): every hook that edits state.json must go through the
locked `hook.update_state`, so concurrent hooks (parallel subagents, several
sessions) never lose each other's increments.

Real forked processes run the REAL hook functions against a real state.json in
a tmp dir. To make the load -> edit -> save window wide enough to hit reliably,
each child wraps `hook.load_state` so that it sleeps after loading (test
instrumentation of the window only: the load itself is the real one). A writer
that still does an unlocked load/edit/save loses updates under this wrapper; a
writer on `update_state` keeps them all, because the lock serializes the
window.

Replaced in the children: only external services (LLM reviewer, Langfuse,
transcript/tempo probes), the config read, and stdin.
"""

import json
import multiprocessing
import time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

import pacemaker.hook as hook_mod
from pacemaker import user_commands

PROCESS_COUNT = 4
BARRIER_TIMEOUT_SECONDS = 30
JOIN_TIMEOUT_SECONDS = 90
WINDOW_SLEEP_SECONDS = 0.05  # added after each load_state, inside the window

STOP_SESSION = "sess-162-stop"


def _run_concurrently(state_path: Path, targets: list) -> None:
    """Fork one child per target; all start on a barrier. Each target is a
    callable `target(state_path: str)` run with the state path redirected and
    the load window widened."""
    ctx = multiprocessing.get_context("fork")
    barrier = ctx.Barrier(len(targets))
    real_load_state = hook_mod.load_state

    def widened_load_state(path=None, *args, **kwargs):
        loaded = real_load_state(path, *args, **kwargs)
        time.sleep(WINDOW_SLEEP_SECONDS)
        return loaded

    def child(target) -> None:
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(hook_mod, "DEFAULT_STATE_PATH", str(state_path))
            )
            stack.enter_context(
                patch("pacemaker.constants.DEFAULT_STATE_PATH", str(state_path))
            )
            stack.enter_context(
                patch.object(hook_mod, "load_state", widened_load_state)
            )
            barrier.wait(BARRIER_TIMEOUT_SECONDS)
            target(str(state_path))

    procs = [ctx.Process(target=child, args=(target,)) for target in targets]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(JOIN_TIMEOUT_SECONDS)
        assert proc.exitcode == 0, f"worker exited with {proc.exitcode}"


def _read(state_path: Path) -> dict:
    return json.loads(state_path.read_text())


@pytest.fixture
def state_path(tmp_path) -> Path:
    return tmp_path / "state.json"


def _seed(state_path: Path, **fields) -> None:
    hook_mod.save_state({"session_id": "sess-162-seed", **fields}, str(state_path))


# --- SubagentStart / SubagentStop -------------------------------------------


def _subagent_start(_state_path: str) -> None:
    with (
        patch("pacemaker.hook.load_config", return_value={"enabled": True}),
        patch("sys.stdin.read", return_value=""),
    ):
        hook_mod.run_subagent_start_hook()


def _subagent_stop(_state_path: str) -> None:
    with (
        patch("pacemaker.hook.load_config", return_value={"enabled": True}),
        patch("sys.stdin.read", return_value=""),
    ):
        hook_mod.run_subagent_stop_hook()


def test_concurrent_subagent_starts_keep_every_increment(state_path):
    _seed(state_path, subagent_counter=0, in_subagent=False)

    _run_concurrently(state_path, [_subagent_start] * PROCESS_COUNT)

    final = _read(state_path)
    assert final["subagent_counter"] == PROCESS_COUNT
    assert final["in_subagent"] is True


def test_concurrent_subagent_stops_keep_every_decrement(state_path):
    _seed(state_path, subagent_counter=PROCESS_COUNT * 2, in_subagent=True)

    _run_concurrently(state_path, [_subagent_stop] * PROCESS_COUNT)

    final = _read(state_path)
    assert final["subagent_counter"] == PROCESS_COUNT
    assert final["in_subagent"] is True


# --- Stop hook ----------------------------------------------------------------


def _stop_hook(tmp_path: Path, *, silent_stop: bool, decision: dict):
    """Build a target that runs the real Stop handler."""
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "go"}})
        + "\n"
    )
    config = {
        "enabled": True,
        "tempo_mode": "on",
        "hook_model": "auto",
        "max_silent_tool_nudges": 99,
    }
    payload = {"session_id": STOP_SESSION, "transcript_path": str(transcript)}

    def target(_state_path: str) -> None:
        with ExitStack() as stack:
            stack.enter_context(
                patch("pacemaker.hook.load_config", return_value=config)
            )
            stack.enter_context(
                patch(
                    "pacemaker.hook.is_context_exhaustion_detected", return_value=False
                )
            )
            stack.enter_context(
                patch(
                    "pacemaker.transcript_reader.detect_silent_tool_stop",
                    return_value=silent_stop,
                )
            )
            stack.enter_context(
                patch("pacemaker.hook.should_run_tempo", return_value=True)
            )
            stack.enter_context(
                patch("pacemaker.langfuse.orchestrator.handle_stop_finalize")
            )
            stack.enter_context(
                patch(
                    "pacemaker.intent_validator.validate_intent", return_value=decision
                )
            )
            stack.enter_context(patch("pacemaker.hook.record_blockage"))
            stack.enter_context(patch("pacemaker.hook.record_activity_event"))
            stack.enter_context(
                patch("sys.stdin.read", return_value=json.dumps(payload))
            )
            hook_mod.run_stop_hook()

    return target


def test_concurrent_silent_nudges_keep_every_increment(state_path, tmp_path):
    _seed(state_path, silent_tool_nudge_count=0)
    target = _stop_hook(tmp_path, silent_stop=True, decision={"decision": "approve"})

    _run_concurrently(state_path, [target] * PROCESS_COUNT)

    assert _read(state_path)["silent_tool_nudge_count"] == PROCESS_COUNT


def test_concurrent_stop_blocks_keep_every_increment(state_path, tmp_path):
    # The Nth block still increments (from N-1); the valve fires from N.
    assert PROCESS_COUNT <= hook_mod.STOP_EXIT_VALVE_THRESHOLD
    _seed(state_path, consecutive_stop_blocks=0)
    target = _stop_hook(
        tmp_path,
        silent_stop=False,
        decision={"decision": "block", "reason": "work incomplete"},
    )

    _run_concurrently(state_path, [target] * PROCESS_COUNT)

    assert _read(state_path)["consecutive_stop_blocks"] == PROCESS_COUNT


# --- UserPromptSubmit / SessionStart ---------------------------------------------


def _user_prompt(session_id: str):
    payload = {"session_id": session_id, "prompt": "please continue"}

    def target(_state_path: str) -> None:
        with (
            patch("pacemaker.hook.load_config", return_value={"enabled": True}),
            patch("sys.stdin.read", return_value=json.dumps(payload)),
        ):
            hook_mod.run_user_prompt_submit()

    return target


def test_concurrent_user_prompts_keep_every_session_entry(state_path):
    _seed(state_path)
    sessions = [f"sess-162-prompt-{i}" for i in range(PROCESS_COUNT)]

    _run_concurrently(state_path, [_user_prompt(s) for s in sessions])

    by_session = _read(state_path).get("last_user_interaction_time_by_session", {})
    assert sorted(by_session) == sorted(sessions)


def _session_start(session_id: str):
    payload = {"session_id": session_id, "source": "startup"}

    # `enabled` must be True or SessionStart returns before touching state.
    config = {
        "enabled": True,
        "cross_session_awareness_enabled": False,
        "langfuse_enabled": False,
    }

    def target(_state_path: str) -> None:
        with (
            patch("pacemaker.hook.load_config", return_value=config),
            # external effects: the `claude --version` probe subprocess and
            # the memory-folder symlink in the cwd
            patch("pacemaker.version_check.perform_session_start_version_check"),
            patch("pacemaker.memory_localization.core.link_if_local_exists"),
            patch("sys.stdin.read", return_value=json.dumps(payload)),
        ):
            hook_mod.run_session_start_hook()

    return target


def test_concurrent_session_starts_keep_every_removal(state_path):
    sessions = [f"sess-162-start-{i}" for i in range(PROCESS_COUNT)]
    _seed(
        state_path,
        last_user_interaction_time_by_session={
            s: "2026-10-01T00:00:00+00:00" for s in sessions
        },
    )

    _run_concurrently(state_path, [_session_start(s) for s in sessions])

    # Each startup removes its OWN entry; a lost update leaves one behind.
    assert _read(state_path).get("last_user_interaction_time_by_session", {}) == {}


# --- tempo session on/off ----------------------------------------------------------


def _tempo_session_on(tmp_path: Path):
    def target(_state_path: str) -> None:
        result = user_commands._execute_tempo(
            str(tmp_path / "config.json"), "session on"
        )
        assert result["success"] is True, result

    return target


def test_tempo_session_toggle_survives_concurrent_subagent_start(state_path, tmp_path):
    _seed(state_path, subagent_counter=0, tempo_session_enabled=False)

    _run_concurrently(
        state_path,
        [_tempo_session_on(tmp_path), _subagent_start, _subagent_start],
    )

    final = _read(state_path)
    assert final["tempo_session_enabled"] is True
    assert final["subagent_counter"] == 2
