"""Story #187 review round 3: R1 (pre-versioning DBs, user_version 0) and B3
(symlinks planted in the shared dir). Real SQLite/files/subprocesses."""

import json
import os
import sqlite3
import stat
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from schema_v1_support import v1_schema

from pacemaker import data_dir
from pacemaker import database as db
from pacemaker import user_commands
from pacemaker.session_registry import registry

SRC = str(Path(__file__).resolve().parent.parent / "src")


def _per_user():
    return Path(os.path.expanduser("~")) / ".claude-pace-maker"


def _config(content):
    cfg = _per_user() / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(content if isinstance(content, str) else json.dumps(content))
    return str(cfg)


def _mode(p):
    return stat.S_IMODE(os.lstat(p).st_mode)


def _v0_db(path, drop=()):
    """A DB as releases <= 2.35.4 left it: tables present, user_version 0."""
    conn = sqlite3.connect(path)
    conn.executescript(v1_schema())
    for t in drop:
        conn.execute(f"DROP TABLE {t}")
    conn.execute("PRAGMA user_version = 0")
    conn.execute(
        "INSERT INTO activity_events (timestamp, event_code, status, session_id) "
        "VALUES (1.0, 'IV', 'green', 'old')"
    )
    conn.commit()
    conn.close()


class TestPreVersioningDatabases:
    def test_writers_work_on_v0_db_without_initialize(self, tmp_path):
        path = str(tmp_path / "usage.db")
        _v0_db(path)
        assert db.record_activity_event(path, "SS", "green", "s1")
        assert db.record_governance_event(path, "IV", "p", "s1", "fb")
        assert db.record_blockage(path, "other", "r", "stop", "s1")
        conn = sqlite3.connect(path)
        rows = conn.execute(
            "SELECT session_id, os_user FROM activity_events ORDER BY id"
        ).fetchall()
        conn.close()
        assert rows == [("old", None), ("s1", data_dir.os_user())]

    def test_version_stays_0_so_initialize_still_runs_later(self, tmp_path):
        path = str(tmp_path / "usage.db")
        _v0_db(path, drop=("codex_usage",))
        assert db.record_activity_event(path, "SS", "green", "s1")
        conn = sqlite3.connect(path)
        assert conn.execute("PRAGMA user_version").fetchone() == (0,)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        conn.close()
        assert "codex_usage" not in names
        db.reset_initialized_dbs()
        assert db.initialize_database(path)
        conn = sqlite3.connect(path)
        assert conn.execute("PRAGMA user_version").fetchone() == (db.SCHEMA_VERSION,)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        conn.close()
        assert "codex_usage" in names

    def test_missing_tables_behave_exactly_as_before(self, tmp_path):
        """A table an old release never had is still 'no such table' until
        initialize_database runs (unchanged), not a new kind of failure."""
        path = str(tmp_path / "usage.db")
        _v0_db(path, drop=("governance_events",))
        assert db.record_governance_event(path, "IV", "p", "s", "fb") is False
        assert db.record_activity_event(path, "SS", "green", "s") is True

    def test_empty_db_file_is_untouched_by_writer_open(self, tmp_path):
        path = tmp_path / "usage.db"
        path.write_bytes(b"")
        with db.get_db_connection(str(path)):
            pass
        conn = sqlite3.connect(path)
        assert conn.execute("SELECT count(*) FROM sqlite_master").fetchone() == (0,)
        conn.close()

    def test_real_hooks_on_v0_db_record_rows_and_log_no_errors(self, tmp_path):
        home = tmp_path / "home"
        pm = home / ".claude-pace-maker"
        pm.mkdir(parents=True)
        (pm / "config.json").write_text(
            json.dumps({"enabled": True, "tdd_enabled": False})
        )
        ws = home / "proj"
        ws.mkdir()
        _v0_db(str(pm / "usage.db"))
        env = {k: v for k, v in os.environ.items() if not k.startswith("PACEMAKER_")}
        env.update(HOME=str(home), PYTHONPATH=SRC, PYTHONSAFEPATH="1")

        def hook(kind, payload):
            return subprocess.run(
                [sys.executable, "-m", "pacemaker.hook", kind],
                input=json.dumps(payload),
                cwd=ws,
                env=env,
                capture_output=True,
                text=True,
                timeout=90,
            )

        sid = str(uuid.uuid4())
        hook("session_start", {"session_id": sid, "source": "startup", "cwd": str(ws)})
        for i in range(3):
            hook(
                "user_prompt_submit",
                {
                    "session_id": sid,
                    "prompt": f"hi {i}",
                    "cwd": str(ws),
                    "transcript_path": "/x",
                },
            )
        conn = sqlite3.connect(pm / "usage.db")
        codes = sorted(
            r[0] for r in conn.execute("SELECT event_code FROM activity_events")
        )
        conn.close()
        # Identical to what the previous release (HEAD 4f6b1d0) records on the
        # same DB: SessionStart + 3 UserPromptSubmit (the 1970 'IV' row is
        # purged by the hook's own 60 s activity cleanup).
        assert codes == ["SE", "UP", "UP", "UP"]
        log = "".join(p.read_text() for p in pm.glob("*.log"))
        assert "ERROR" not in log, log


class TestSymlinksInSharedDir:
    @pytest.fixture
    def shared(self, tmp_path):
        d = tmp_path / "shared"
        d.mkdir()
        _config({"shared_data_dir": str(d)})
        return d

    def _victim(self, tmp_path):
        v = tmp_path / "private_key"
        v.write_text("-----BEGIN KEY-----\n")
        v.chmod(0o600)
        return v

    def test_prepare_refuses_symlinked_db_and_never_chmods_target(
        self, shared, tmp_path
    ):
        victim = self._victim(tmp_path)
        (shared / "usage.db").symlink_to(victim)
        with pytest.raises(data_dir.SharedDataDirError, match="not a regular file"):
            data_dir.prepare_db_file(str(shared / "usage.db"))
        assert _mode(victim) == 0o600

    def test_every_open_path_refuses_it(self, shared, tmp_path):
        victim = self._victim(tmp_path)
        before = victim.read_bytes()
        (shared / "usage.db").symlink_to(victim)
        (shared / "session_registry.db").symlink_to(victim)
        assert db.initialize_database(str(shared / "usage.db")) is False
        assert (
            db.record_activity_event(str(shared / "usage.db"), "IV", "green", "s")
            is False
        )
        registry.register_session("s", "/ws", 1, str(shared / "session_registry.db"))
        assert (
            db.should_poll_globally(str(shared / "usage.db"), 300, "s") is True
        )  # fail-open, logged
        assert _mode(victim) == 0o600
        assert victim.read_bytes() == before

    def test_dangling_symlink_does_not_create_the_target(self, shared, tmp_path):
        target = tmp_path / "created_by_victim"
        (shared / "usage.db").symlink_to(target)
        assert (
            db.record_activity_event(str(shared / "usage.db"), "IV", "green", "s")
            is False
        )
        assert not target.exists()

    @pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
    def test_symlinked_side_files_are_refused(self, shared, tmp_path, suffix):
        path = str(shared / "usage.db")
        assert db.initialize_database(path)
        db.reset_initialized_dbs()
        victim = tmp_path / "bashrc"
        victim.write_text("export X=1\n")
        victim.chmod(0o644)
        (shared / ("usage.db" + suffix)).symlink_to(victim)
        with pytest.raises(data_dir.SharedDataDirError, match="not a regular file"):
            with db.get_db_connection(path):
                pass
        assert _mode(victim) == 0o644
        assert victim.read_text() == "export X=1\n"

    def test_registry_connection_refuses_symlink(self, shared, tmp_path):
        from pacemaker.session_registry import db as reg_db

        victim = self._victim(tmp_path)
        (shared / "session_registry.db").symlink_to(victim)
        with pytest.raises(data_dir.SharedDataDirError):
            reg_db.get_connection(str(shared / "session_registry.db"))
        assert _mode(victim) == 0o600

    def test_csa_heartbeat_catchup_uses_the_guarded_open(self, shared, tmp_path):
        """_csa.on_heartbeat must not raw-connect to a symlinked registry."""
        from pacemaker.session_registry import _csa

        victim = self._victim(tmp_path)
        (shared / "session_registry.db").symlink_to(victim)
        state = {
            "cross_session_awareness": {
                "sid-1": {"workspace_root": "/ws"},
            }
        }
        _csa.on_heartbeat(
            "sid-1",
            os.getpid(),
            str(shared / "session_registry.db"),
            state,
            {"cross_session_awareness_enabled": True},
        )
        assert _mode(victim) == 0o600

    def test_regular_file_still_repaired_through_fd(self, shared):
        path = shared / "usage.db"
        path.write_text("")
        path.chmod(0o600)
        data_dir.prepare_db_file(str(path))
        assert _mode(path) == 0o660

    def test_real_hook_subprocess_leaves_victim_file_alone_and_logs(self, tmp_path):
        home = tmp_path / "home"
        pm = home / ".claude-pace-maker"
        pm.mkdir(parents=True)
        shared = tmp_path / "shared"
        shared.mkdir()
        shared.chmod(0o2770)
        (pm / "config.json").write_text(
            json.dumps(
                {"enabled": True, "tdd_enabled": False, "shared_data_dir": str(shared)}
            )
        )
        ws = home / "proj"
        ws.mkdir()
        victim = home / "private_key"
        victim.write_text("-----BEGIN KEY-----\n")
        victim.chmod(0o600)
        (shared / "usage.db").symlink_to(victim)
        env = {k: v for k, v in os.environ.items() if not k.startswith("PACEMAKER_")}
        env.update(HOME=str(home), PYTHONPATH=SRC, PYTHONSAFEPATH="1")
        sid = str(uuid.uuid4())
        for kind in ("session_start", "post_tool_use"):
            out = subprocess.run(
                [sys.executable, "-m", "pacemaker.hook", kind],
                input=json.dumps(
                    {"session_id": sid, "source": "startup", "cwd": str(ws), "tool_name": "Bash",
                     "tool_input": {"command": "ls"}, "tool_response": {}, "transcript_path": "/x"}
                ),
                cwd=ws, env=env, capture_output=True, text=True, timeout=90,
            )  # fmt: skip
            assert out.returncode == 0, out.stderr
        assert _mode(victim) == 0o600
        assert victim.read_text() == "-----BEGIN KEY-----\n"
        log = "".join(p.read_text() for p in pm.glob("*.log"))
        assert "not a regular file" in log

    def test_status_shows_the_symlink_error(self, shared, tmp_path):
        victim = self._victim(tmp_path)
        (shared / "usage.db").symlink_to(victim)
        cfg = str(_per_user() / "config.json")
        out = user_commands.execute_command(
            "status", cfg, db_path=str(shared / "usage.db")
        )
        assert out["success"]
        assert "not a regular file" in out["message"]
        assert _mode(victim) == 0o600


class TestLows:
    def test_forged_huge_start_time_is_capped_in_shared_mode(self, tmp_path):
        from pacemaker.session_registry import nudges

        shared = tmp_path / "shared"
        shared.mkdir()
        _config({"shared_data_dir": str(shared)})
        sib = [
            {
                "session_id": "a",
                "workspace_root": "/w",
                "pid": 1,
                "start_time": 1e300,
                "last_seen": 1.0,
            }
        ]
        out = nudges.build_start_banner(sib)
        assert "9" * 150 not in out and len(max(out.splitlines(), key=len)) < 400

    def test_legacy_start_time_fallback_unchanged(self):
        from pacemaker.session_registry import nudges

        sib = [
            {
                "session_id": "a",
                "workspace_root": "/w",
                "pid": 1,
                "start_time": 1e300,
                "last_seen": 1.0,
            }
        ]
        assert str(int(1e300)) in nudges.build_start_banner(sib)

    def test_hooks_do_not_log_malformed_config_warning(self, tmp_path):
        """L-e: no per-hook-call spam; the problem is shown by `status`."""
        _config("{not json")
        data_dir._setting_cache.clear()
        path = str(tmp_path / "usage.db")
        assert db.initialize_database(path)
        assert db.record_activity_event(path, "IV", "green", "s")
        assert not hasattr(data_dir, "log_config_problem_once")

    def test_status_still_reports_a_corrupted_config_as_before(self, tmp_path):
        cfg = _config("{not json")
        out = user_commands.execute_command(
            "status", cfg, db_path=str(tmp_path / "u.db")
        )
        assert "corrupted" in out["message"]  # existing behaviour, unchanged


class TestOwnershipFifoAndUsageRead:
    """Round 4 lows L-h and L-j."""

    def test_foreign_owned_file_is_refused_before_any_open(self, tmp_path, monkeypatch):
        shared = tmp_path / "shared"
        shared.mkdir()
        _config({"shared_data_dir": str(shared)})
        f = shared / "usage.db"
        f.write_text("")
        f.chmod(0o600)  # lacks group rw, so a repair would be needed
        opened = []
        real_open = os.open
        monkeypatch.setattr(
            os, "geteuid", lambda: os.getuid() + 12345
        )  # "not the owner"
        monkeypatch.setattr(
            data_dir.os,
            "open",
            lambda *a, **k: opened.append(a[0]) or real_open(*a, **k),
        )
        with pytest.raises(data_dir.SharedDataDirError, match="does not own it"):
            data_dir.prepare_db_file(str(f))
        assert str(f) not in opened  # refused from lstat, no open() at all
        assert _mode(f) == 0o600

    @pytest.mark.timeout(15)
    def test_fifo_swapped_in_after_lstat_does_not_hang(self, tmp_path):
        shared = tmp_path / "shared"
        shared.mkdir()
        _config({"shared_data_dir": str(shared)})
        regular = shared / "other.db"
        regular.write_text("")
        regular.chmod(0o600)
        st = os.lstat(regular)
        fifo = shared / "usage.db"
        os.mkfifo(fifo)  # what the attacker swapped in after our lstat
        with pytest.raises(data_dir.SharedDataDirError):
            data_dir._widen_mode_nofollow(str(fifo), st, 0o600)

    def test_usage_read_refuses_symlinked_shared_db(self, tmp_path):
        shared = tmp_path / "shared"
        shared.mkdir()
        _config({"shared_data_dir": str(shared)})
        other = tmp_path / "victim.db"
        conn = sqlite3.connect(other)
        conn.executescript(db.SCHEMA)
        conn.execute(
            "INSERT INTO usage_snapshots (timestamp, five_hour_util, seven_day_util, "
            "session_id) VALUES (1, 55.0, 66.0, 's')"
        )
        conn.commit()
        conn.close()
        (shared / "usage.db").symlink_to(other)
        assert user_commands._get_latest_usage(str(shared / "usage.db")) is None

    def test_usage_read_unchanged_for_legacy_and_regular_shared_file(self, tmp_path):
        from datetime import datetime, timezone

        path = str(tmp_path / "usage.db")
        db.initialize_database(path)
        db.insert_usage_snapshot(
            path, datetime.now(timezone.utc), 12.5, None, 40.0, None, "s"
        )
        assert user_commands._get_latest_usage(path)["five_hour_util"] == 12.5
