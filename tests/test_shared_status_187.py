"""Story #187 AC1/AC4: `pace-maker status` / `sessions` in shared and legacy mode.

Real SQLite, real config.json; no mocks.
"""

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from pacemaker import user_commands
from pacemaker.data_dir import os_user
from pacemaker.session_registry import db as reg_db
from pacemaker.session_registry import registry


def _per_user():
    return Path(os.path.expanduser("~")) / ".claude-pace-maker"


def _config(content):
    cfg = _per_user() / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(content))
    return str(cfg)


def _status(config_path, db_path):
    result = user_commands.execute_command("status", config_path, db_path=db_path)
    assert result["success"], result
    return result["message"]


def _add_foreign_session(reg_path, user, sid):
    registry.register_session(sid, "/home/%s/work" % user, 4242, reg_path)
    conn = sqlite3.connect(reg_path)
    conn.execute("UPDATE sessions SET os_user = ? WHERE session_id = ?", (user, sid))
    conn.commit()
    conn.close()


class TestLegacyOutputUnchanged:
    def test_status_has_no_shared_section(self, tmp_path):
        cfg = _config({"enabled": True})
        msg = _status(cfg, str(tmp_path / "usage.db"))
        assert "Shared Data Dir" not in msg

    def test_sessions_table_has_no_os_user_column_in_legacy_mode(self):
        reg_path = os.environ["PACEMAKER_SESSION_REGISTRY_PATH"]
        registry.register_session("legacy-s", "/ws", 1, reg_path)
        out = user_commands.execute_command("sessions", "", subcommand="list")
        assert out["success"]
        header = out["message"].splitlines()[0]
        assert header.split() == ["SESSION", "WORKSPACE", "PID", "STARTED", "LAST_SEEN"]


class TestSharedStatus:
    @pytest.fixture
    def shared(self, tmp_path):
        d = tmp_path / "shared"
        d.mkdir()
        cfg = _config({"enabled": True, "shared_data_dir": str(d)})
        return d, cfg

    def test_status_shows_shared_dir_and_other_users_sessions(
        self, shared, monkeypatch
    ):
        d, cfg = shared
        reg_path = str(d / "session_registry.db")
        monkeypatch.setenv("PACEMAKER_SESSION_REGISTRY_PATH", reg_path)
        registry.register_session(
            "aaaaaaaa-0000-4000-8000-000000000001", "/ws/mine", os.getpid(), reg_path
        )
        _add_foreign_session(
            reg_path, "claudedev", "bbbbbbbb-0000-4000-8000-000000000002"
        )

        msg = _status(cfg, str(d / "usage.db"))

        assert f"Shared Data Dir: {d}" in msg
        assert "bbbbbbbb-0000-4000-8000-000000000002" in msg and "claudedev" in msg
        assert "aaaaaaaa-0000-4000-8000-000000000001" in msg and os_user() in msg

    def test_stale_sessions_are_not_listed(self, shared, monkeypatch):
        d, cfg = shared
        reg_path = str(d / "session_registry.db")
        monkeypatch.setenv("PACEMAKER_SESSION_REGISTRY_PATH", reg_path)
        _add_foreign_session(
            reg_path, "claudedev", "cccccccc-0000-4000-8000-000000000003"
        )
        conn = sqlite3.connect(reg_path)
        conn.execute("UPDATE sessions SET last_seen = ?", (time.time() - 86400,))
        conn.commit()
        conn.close()
        assert "cccccccc-0000-4000-8000-000000000003" not in _status(
            cfg, str(d / "usage.db")
        )

    def test_sessions_command_shows_os_user_column_in_shared_mode(
        self, shared, monkeypatch
    ):
        d, _ = shared
        reg_path = str(d / "session_registry.db")
        monkeypatch.setenv("PACEMAKER_SESSION_REGISTRY_PATH", reg_path)
        _add_foreign_session(
            reg_path, "claudedev", "dddddddd-0000-4000-8000-000000000004"
        )
        out = user_commands.execute_command("sessions", "", subcommand="list")
        lines = out["message"].splitlines()
        assert "OS_USER" in lines[0]
        assert any(
            "claudedev" in line and "dddddddd-0000-4000-8000-000000000004" in line
            for line in lines
        )

    def test_status_surfaces_missing_shared_dir_loudly(self, tmp_path):
        missing = tmp_path / "not-there"
        cfg = _config({"enabled": True, "shared_data_dir": str(missing)})
        msg = _status(cfg, str(missing / "usage.db"))
        assert f"shared_data_dir '{missing}' does not exist" in msg
        assert "Shared Data Dir:" in msg
        # never silently fell back to the per-user dir
        assert not (missing).exists()

    def test_old_registry_rows_without_os_user_render_as_unknown(
        self, shared, monkeypatch
    ):
        d, cfg = shared
        reg_path = str(d / "session_registry.db")
        monkeypatch.setenv("PACEMAKER_SESSION_REGISTRY_PATH", reg_path)
        conn = sqlite3.connect(reg_path)
        conn.execute(reg_db._DDL_CREATE_SESSIONS)
        now = time.time()
        conn.execute(
            "INSERT INTO sessions VALUES ('eeeeeeee-0000-4000-8000-000000000005', '/w', 1, ?, ?)",
            (now, now),
        )
        conn.commit()
        conn.close()
        msg = _status(cfg, str(d / "usage.db"))
        assert "eeeeeeee-0000-4000-8000-000000000005" in msg
        assert "?" in msg
