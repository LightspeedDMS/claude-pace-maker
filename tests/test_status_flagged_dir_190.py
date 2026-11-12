"""Bug #190: read paths of ``pace-maker status`` / ``sessions`` must not touch a
shared dir that the #189 check flags (sticky bit + fs.protected_regular>=2).
Opening the DB there leaves caller-owned -wal/-shm files behind.

Real directories and databases; the sysctl value is injected through the
``data_dir.PROTECTED_REGULAR_PATH`` seam (a temp file).
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pacemaker import data_dir, user_commands
from pacemaker import database as db
from pacemaker.session_registry import registry

STICKY_GROUP_WRITABLE = 0o3770
PLAIN_SETGID = 0o2770
FLAGGED_ERROR = "sticky bit and fs.protected_regular=2"
UUID_A = "aaaaaaaa-0000-4000-8000-00000000000a"
SEEDED_FIVE_HOUR_UTIL = 12.5
SEEDED_SEVEN_DAY_UTIL = 40.0


def _config(content):
    cfg = Path(os.path.expanduser("~")) / ".claude-pace-maker" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(content))
    return str(cfg)


def _seed_usage_db(path):
    """Create a usage.db holding one snapshot; every step is asserted."""
    assert db.initialize_database(path)
    assert db.insert_usage_snapshot(
        path,
        datetime.now(timezone.utc),
        SEEDED_FIVE_HOUR_UTIL,
        None,
        SEEDED_SEVEN_DAY_UTIL,
        None,
        "s",
    )


@pytest.fixture
def flagged_dir(tmp_path, monkeypatch):
    """A shared dir that already holds valid usage.db / session_registry.db
    (created while the dir was still fine), then gets flagged: sticky bit on
    and fs.protected_regular=2. Returns (dir, config_path)."""
    proc = tmp_path / "protected_regular"
    proc.write_text("2\n")
    monkeypatch.setattr(data_dir, "PROTECTED_REGULAR_PATH", str(proc))
    d = tmp_path / "shared"
    d.mkdir()
    os.chmod(d, PLAIN_SETGID)
    cfg = _config({"enabled": True, "shared_data_dir": str(d)})
    _seed_usage_db(str(d / "usage.db"))
    registry.register_session(
        UUID_A, "/ws", os.getpid(), str(d / "session_registry.db")
    )
    # SQLite leaves the DBs without sidecar files once the last connection closes
    for name in ("usage.db", "session_registry.db"):
        for suffix in ("-wal", "-shm"):
            Path(str(d / name) + suffix).unlink(missing_ok=True)
    os.chmod(d, STICKY_GROUP_WRITABLE)  # now the dir is flagged
    db.reset_initialized_dbs()
    return d, cfg


def _listing(d):
    return sorted(p.name for p in d.iterdir())


class TestFlaggedDirIsNotTouched:
    def test_get_latest_usage_skips_the_shared_db(self, flagged_dir):
        d, _ = flagged_dir
        before = _listing(d)
        assert user_commands._get_latest_usage(str(d / "usage.db")) is None
        assert _listing(d) == before  # no -wal / -shm created

    def test_status_creates_no_files_and_still_shows_the_error(self, flagged_dir):
        d, cfg = flagged_dir
        before = _listing(d)
        out = user_commands.execute_command("status", cfg, db_path=str(d / "usage.db"))
        assert out["success"]
        assert FLAGGED_ERROR in out["message"]
        assert _listing(d) == before

    def test_sessions_list_creates_no_files_and_reports_the_error(
        self, flagged_dir, monkeypatch
    ):
        d, _ = flagged_dir
        monkeypatch.setenv(
            "PACEMAKER_SESSION_REGISTRY_PATH", str(d / "session_registry.db")
        )
        before = _listing(d)
        out = user_commands.execute_command("sessions", "", subcommand="list")
        assert out["success"] is False
        assert FLAGGED_ERROR in out["message"]
        assert _listing(d) == before

    def test_audited_read_helpers_degrade_without_touching_the_dir(self, flagged_dir):
        """Audit of every other read path status/sessions use."""
        d, _ = flagged_dir
        before = _listing(d)

        blockages = user_commands._format_blockage_stats(str(d / "usage.db"))
        assert "Total:" in blockages and blockages.rstrip().endswith("0")

        secrets = user_commands._format_secrets_stats(str(d / "usage.db"))
        assert "(unavailable)" in secrets

        rows, error = user_commands._load_session_rows(str(d / "session_registry.db"))
        assert rows is None
        assert FLAGGED_ERROR in error["message"]

        assert _listing(d) == before

    def test_unflagged_shared_dir_still_reads_usage(self, flagged_dir):
        d, _ = flagged_dir
        os.chmod(d, PLAIN_SETGID)  # chmod o-t: fixed
        got = user_commands._get_latest_usage(str(d / "usage.db"))
        assert got["five_hour_util"] == SEEDED_FIVE_HOUR_UTIL


class TestLegacyUnchanged:
    def test_legacy_get_latest_usage_reads_value(self, tmp_path):
        _config({"enabled": True})
        path = str(tmp_path / "usage.db")
        _seed_usage_db(path)
        got = user_commands._get_latest_usage(path)
        assert got["five_hour_util"] == SEEDED_FIVE_HOUR_UTIL

    def test_legacy_status_has_no_shared_text(self, tmp_path):
        cfg = _config({"enabled": True})
        out = user_commands.execute_command(
            "status", cfg, db_path=str(tmp_path / "usage.db")
        )
        assert out["success"]
        assert "Shared Data Dir" not in out["message"]
        assert "protected_regular" not in out["message"]

    def test_get_latest_usage_does_not_read_proc_in_legacy(self, tmp_path, monkeypatch):
        calls = []
        real = data_dir.read_protected_regular
        monkeypatch.setattr(
            data_dir, "read_protected_regular", lambda: calls.append(1) or real()
        )
        _config({"enabled": True})
        path = str(tmp_path / "usage.db")
        _seed_usage_db(path)
        assert user_commands._get_latest_usage(path) is not None  # path really ran
        assert calls == []
