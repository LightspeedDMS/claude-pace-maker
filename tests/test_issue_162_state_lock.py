#!/usr/bin/env python3
"""
Bug #162 (remaining part): `hook.update_state` must serialize its
load -> mutate -> save with an advisory file lock.

Re-loading right before the save (the first part of #162) shrinks the window
but cannot close it: two processes inside load -> save at the same instant
each save a copy that lacks the other's key. The fix is an `fcntl.flock` on a
SIDECAR file (`<state path>.lock`; os.replace swaps the state file's inode, so
the state file itself cannot carry the lock), bounded by
`hook.STATE_LOCK_TIMEOUT_SECONDS`. On timeout the hook warns and proceeds
unlocked: it must never block or fail because of the lock.

Real processes and a real file in a tmp dir; nothing is mocked except (in the
timeout test) the log call that is being asserted on and the timeout constant.
"""

import fcntl
import json
import multiprocessing
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import pacemaker.hook as hook_mod

PROCESS_COUNT = 4
ROUNDS = 5
BARRIER_TIMEOUT_SECONDS = 30
JOIN_TIMEOUT_SECONDS = 60
MUTATE_SLEEP_SECONDS = 0.05


def _worker(state_path: str, key: str, barrier) -> None:
    """Start on the barrier, then set ONE key through update_state."""

    def mutate(state: dict) -> None:
        time.sleep(MUTATE_SLEEP_SECONDS)  # widen the load -> save window
        state[key] = True

    barrier.wait(BARRIER_TIMEOUT_SECONDS)
    hook_mod.update_state(mutate, state_path)


def _run_round(state_path: Path, round_no: int) -> list:
    ctx = multiprocessing.get_context("fork")
    barrier = ctx.Barrier(PROCESS_COUNT)
    keys = [f"round{round_no}_proc{i}" for i in range(PROCESS_COUNT)]
    procs = [
        ctx.Process(target=_worker, args=(str(state_path), key, barrier))
        for key in keys
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(JOIN_TIMEOUT_SECONDS)
        assert proc.exitcode == 0, f"worker exited with {proc.exitcode}"
    return keys


def test_concurrent_update_state_keeps_every_key(tmp_path):
    state_path = tmp_path / "state.json"
    hook_mod.save_state({"session_id": "sess-162-lock"}, str(state_path))

    expected = []
    for round_no in range(ROUNDS):
        expected.extend(_run_round(state_path, round_no))

    saved = json.loads(state_path.read_text())
    missing = [key for key in expected if not saved.get(key)]
    assert missing == [], f"lost updates: {missing}"


def test_held_lock_beyond_timeout_warns_and_updates_unlocked(tmp_path):
    state_path = tmp_path / "state.json"
    hook_mod.save_state({"session_id": "sess-162-held"}, str(state_path))
    lock_path = str(state_path) + ".lock"

    holder_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(holder_fd, fcntl.LOCK_EX)
    try:
        with (
            patch.object(hook_mod, "STATE_LOCK_TIMEOUT_SECONDS", 0.2),
            patch.object(hook_mod, "log_warning") as warn,
        ):
            started = time.monotonic()
            result = hook_mod.update_state(
                lambda s: s.__setitem__("mine", 1), str(state_path)
            )
            elapsed = time.monotonic() - started
    finally:
        os.close(holder_fd)

    assert result["mine"] == 1
    assert json.loads(state_path.read_text())["mine"] == 1
    assert 0.2 <= elapsed < 5.0, f"wait was not bounded by the timeout: {elapsed}"
    messages = [str(call.args) for call in warn.call_args_list]
    assert any(str(state_path) in m for m in messages), messages


def test_lock_released_when_mutate_raises(tmp_path):
    state_path = tmp_path / "state.json"
    hook_mod.save_state({"session_id": "sess-162-raise"}, str(state_path))

    def boom(state: dict) -> None:
        raise RuntimeError("mutate failed")

    with pytest.raises(RuntimeError, match="mutate failed"):
        hook_mod.update_state(boom, str(state_path))

    # The lock file stays on disk (never deleted) but is no longer held:
    # a non-blocking exclusive lock on it must succeed immediately.
    lock_path = str(state_path) + ".lock"
    assert os.path.exists(lock_path)
    probe_fd = os.open(lock_path, os.O_RDWR)
    try:
        fcntl.flock(probe_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(probe_fd)


def test_update_state_without_contention_does_not_warn(tmp_path):
    state_path = tmp_path / "state.json"
    hook_mod.save_state({"session_id": "sess-162-quiet"}, str(state_path))
    with patch.object(hook_mod, "log_warning") as warn:
        hook_mod.update_state(lambda s: s.__setitem__("k", 1), str(state_path))
    warn.assert_not_called()
    assert json.loads(state_path.read_text())["k"] == 1
