"""Story #187 AC3/AC5: usage.db in a shared dir (real SQLite, real files)."""

import json
import os
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pacemaker import database as db
from pacemaker.data_dir import SharedDataDirError


def _config(content):
    cfg = Path(os.path.expanduser("~")) / ".claude-pace-maker" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(content))


@pytest.fixture
def shared(tmp_path):
    d = tmp_path / "shared"
    d.mkdir()
    _config({"shared_data_dir": str(d)})
    return d


def _modes(directory):
    return {
        p.name: stat.S_IMODE(p.stat().st_mode)
        for p in directory.iterdir()
        if p.name.startswith("usage.db")
    }


class TestSharedUsageDbModes:
    def test_usage_db_and_sidecars_are_0660_under_restrictive_umask(self, shared):
        path = str(shared / "usage.db")
        old = os.umask(0o077)
        try:
            assert db.initialize_database(path)
            assert db.record_activity_event(path, "IV", "green", "s1")
        finally:
            os.umask(old)
        modes = _modes(shared)
        assert modes["usage.db"] == 0o660
        for name, mode in modes.items():
            assert mode == 0o660, name

    def test_modes_stay_0660_after_many_writes(self, shared):
        path = str(shared / "usage.db")
        db.initialize_database(path)
        for i in range(50):
            db.record_activity_event(path, "IV", "green", f"s{i}")
            db.insert_usage_snapshot(
                path, datetime.now(timezone.utc), 1.0, None, 2.0, None, f"s{i}"
            )
        for name, mode in _modes(shared).items():
            assert mode == 0o660, name

    def test_missing_shared_dir_fails_loudly_no_fallback(self, tmp_path):
        missing = tmp_path / "gone"
        _config({"shared_data_dir": str(missing)})
        path = str(missing / "usage.db")
        assert db.initialize_database(path) is False  # logged, reported
        assert not (missing).exists()  # we never create the shared dir ourselves
        with pytest.raises(SharedDataDirError):
            with db.get_db_connection(path):
                pass
        # no silent fallback: a write attempt must not land in the per-user DB
        assert db.record_activity_event(path, "IV", "green", "s") is False
        per_user_db = Path(os.path.expanduser("~")) / ".claude-pace-maker" / "usage.db"
        if per_user_db.exists():
            conn = sqlite3.connect(per_user_db)
            assert conn.execute("SELECT COUNT(*) FROM activity_events").fetchone() == (
                0,
            )
            conn.close()


class TestSinglePollAcrossUsers:
    """AC5: global_poll_state in the shared usage.db de-duplicates polling."""

    def test_second_user_does_not_poll_within_interval(self, shared):
        path = str(shared / "usage.db")
        db.initialize_database(path)
        assert db.should_poll_globally(path, 300, "session-user-a") is True
        assert db.should_poll_globally(path, 300, "session-user-b") is False
        conn = sqlite3.connect(path)
        row = conn.execute(
            "SELECT last_poll_session FROM global_poll_state WHERE id = 1"
        ).fetchone()
        conn.close()
        assert row == ("session-user-a",)

    def test_poll_after_interval_is_allowed_again(self, shared):
        path = str(shared / "usage.db")
        db.initialize_database(path)
        assert db.should_poll_globally(path, 0, "a") is True
        assert db.should_poll_globally(path, 0, "b") is True


class TestLegacyUsageDbUntouched:
    def test_default_mode_creates_no_extra_files_and_default_modes(self, tmp_path):
        """Setting absent: DB created by SQLite exactly as before (no 0660 forcing)."""
        path = tmp_path / "usage.db"
        old = os.umask(0o022)
        try:
            assert db.initialize_database(str(path))
        finally:
            os.umask(old)
        assert stat.S_IMODE(path.stat().st_mode) != 0o660
        assert not any(p.suffix == ".lock" for p in tmp_path.iterdir())
