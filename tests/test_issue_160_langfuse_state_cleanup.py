#!/usr/bin/env python3
"""
Bug #160 item 3: Langfuse subagent state files accumulate.

Facts established while fixing it: a 7-day cleanup (StateManager.
cleanup_stale_files, called from the SessionStart hook) already existed and
works - the 242 subagent-*.json files observed were all younger than 7 days
(oldest 6.94 d), i.e. the TTL simply had not elapsed. But every subagent writes
one state file that is dead the moment the subagent stops, and the 7-day TTL
(right for long-lived SESSION state) lets them pile up at ~35/day.

Contract:
- subagent-*.json files expire after a short TTL (SUBAGENT_STATE_MAX_AGE_DAYS);
  session state files keep the 7-day TTL;
- cleanup runs at most once per interval (stamp file in the state dir), so it
  is a cheap no-op on every other SessionStart;
- the SessionStart hook runs it (real files in a tmp HOME, no mocks).
"""

import io
import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from pacemaker import hook
from pacemaker.langfuse.state import (
    CLEANUP_STAMP_FILENAME,
    SUBAGENT_STATE_MAX_AGE_DAYS,
    StateManager,
)

DAY = 24 * 60 * 60


@pytest.fixture
def state_dir(tmp_path):
    return str(tmp_path / "langfuse_state")


@pytest.fixture
def manager(state_dir):
    return StateManager(state_dir)


def _make(state_dir, name, age_days):
    path = Path(state_dir) / name
    path.write_text(json.dumps({"session_id": name}))
    ts = time.time() - age_days * DAY
    os.utime(path, (ts, ts))
    return path


class TestSubagentTtl:
    def test_cleanup_applies_short_ttl_to_subagent_files_only(self, manager, state_dir):
        old_sub = _make(state_dir, "subagent-aaa.json", SUBAGENT_STATE_MAX_AGE_DAYS + 1)
        fresh_sub = _make(state_dir, "subagent-bbb.json", 0.1)
        # 3 days old: past the subagent TTL, well inside the session TTL
        session_3d = _make(state_dir, "session-ccc.json", 3)
        old_session = _make(state_dir, "session-ddd.json", 8)

        manager.cleanup_stale_files(
            max_age_days=7, subagent_max_age_days=SUBAGENT_STATE_MAX_AGE_DAYS
        )

        assert not old_sub.exists()
        assert fresh_sub.exists()
        assert session_3d.exists()
        assert not old_session.exists()

    def test_default_call_keeps_legacy_behaviour(self, manager, state_dir):
        """No subagent TTL given: subagent files follow the plain max_age_days."""
        sub_3d = _make(state_dir, "subagent-eee.json", 3)
        sub_8d = _make(state_dir, "subagent-fff.json", 8)

        manager.cleanup_stale_files(max_age_days=7)

        assert sub_3d.exists()
        assert not sub_8d.exists()


class TestMaybeCleanupStaleFiles:
    def test_first_call_runs_and_writes_stamp(self, manager, state_dir):
        old_sub = _make(state_dir, "subagent-aaa.json", SUBAGENT_STATE_MAX_AGE_DAYS + 1)

        ran = manager.maybe_cleanup_stale_files()

        assert ran is True
        assert not old_sub.exists()
        assert (Path(state_dir) / CLEANUP_STAMP_FILENAME).exists()

    def test_second_call_within_interval_is_a_noop(self, manager, state_dir):
        manager.maybe_cleanup_stale_files()
        old_sub = _make(
            state_dir, "subagent-late.json", SUBAGENT_STATE_MAX_AGE_DAYS + 1
        )

        ran = manager.maybe_cleanup_stale_files()

        assert ran is False
        assert old_sub.exists()  # not scanned: throttled

    def test_runs_again_after_interval_elapsed(self, manager, state_dir):
        manager.maybe_cleanup_stale_files()
        stamp = Path(state_dir) / CLEANUP_STAMP_FILENAME
        yesterday = time.time() - DAY - 60
        os.utime(stamp, (yesterday, yesterday))
        old_sub = _make(
            state_dir, "subagent-late.json", SUBAGENT_STATE_MAX_AGE_DAYS + 1
        )

        ran = manager.maybe_cleanup_stale_files()

        assert ran is True
        assert not old_sub.exists()

    def test_stamp_file_is_never_deleted_by_cleanup(self, manager, state_dir):
        manager.maybe_cleanup_stale_files()
        stamp = Path(state_dir) / CLEANUP_STAMP_FILENAME
        ancient = time.time() - 30 * DAY
        os.utime(stamp, (ancient, ancient))

        manager.maybe_cleanup_stale_files()  # runs (stamp is old) and re-stamps

        assert stamp.exists()

    def test_custom_interval_is_honoured(self, manager, state_dir):
        manager.maybe_cleanup_stale_files(min_interval_seconds=3600)
        stamp = Path(state_dir) / CLEANUP_STAMP_FILENAME
        two_hours_ago = time.time() - 7200
        os.utime(stamp, (two_hours_ago, two_hours_ago))

        assert manager.maybe_cleanup_stale_files(min_interval_seconds=3600) is True

    def test_never_raises_on_unusable_state_dir(self, tmp_path):
        manager = StateManager(str(tmp_path / "langfuse_state"))
        os.rmdir(manager.state_dir)  # directory vanished underneath us

        assert manager.maybe_cleanup_stale_files() is False


class TestSessionStartRunsTheCleanup:
    def test_session_start_removes_expired_subagent_state(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        state_dir = home / ".claude-pace-maker" / "langfuse_state"
        state_dir.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        old_sub = _make(
            str(state_dir), "subagent-old.json", SUBAGENT_STATE_MAX_AGE_DAYS + 1
        )
        fresh_sub = _make(str(state_dir), "subagent-new.json", 0.1)
        session_3d = _make(str(state_dir), "session-keep.json", 3)

        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps({"enabled": True, "intent_validation_enabled": False})
        )
        state_path = tmp_path / "state.json"
        state_path.write_text("{}")
        stdin = io.StringIO(
            json.dumps(
                {
                    "session_id": "s-1",
                    "source": "startup",
                    "transcript_path": "/tmp/none.jsonl",
                    "hook_event_name": "SessionStart",
                }
            )
        )

        with (
            patch("pacemaker.hook.DEFAULT_CONFIG_PATH", str(config_path)),
            patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path)),
            patch("sys.stdin", stdin),
        ):
            hook.run_session_start_hook()

        assert not old_sub.exists()
        assert fresh_sub.exists()
        assert session_3d.exists()
