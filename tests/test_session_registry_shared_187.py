"""Story #187: session_registry.db in shared mode, os_user, upgrade safety.

Real SQLite + real files; no mocks.
"""

import json
import os
import sqlite3
import stat
import threading
from pathlib import Path

import pytest

from pacemaker.data_dir import os_user
from pacemaker.session_registry import db as reg_db
from pacemaker.session_registry import registry


def _config(content):
    cfg = Path(os.path.expanduser("~")) / ".claude-pace-maker" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(content))


@pytest.fixture
def prod_like(monkeypatch):
    """Drop the conftest test seam so resolve_db_path() takes the production branch."""
    monkeypatch.delenv("PACEMAKER_SESSION_REGISTRY_PATH", raising=False)
    monkeypatch.setenv("PACEMAKER_TEST_MODE", "0")


def _cols(path, table):
    conn = sqlite3.connect(path)
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


class TestResolver:
    def test_legacy_path_unchanged(self, prod_like):
        assert reg_db.resolve_db_path() == os.path.join(
            os.path.expanduser("~"), ".claude-pace-maker", "session_registry.db"
        )

    def test_shared_path(self, prod_like, tmp_path):
        _config({"shared_data_dir": str(tmp_path)})
        assert reg_db.resolve_db_path() == str(tmp_path / "session_registry.db")

    def test_env_override_still_wins_in_shared_mode(self, tmp_path, monkeypatch):
        _config({"shared_data_dir": str(tmp_path)})
        monkeypatch.setenv("PACEMAKER_SESSION_REGISTRY_PATH", "/x/y.db")
        assert reg_db.resolve_db_path() == "/x/y.db"


class TestOsUserOnRegistryRows:
    def test_new_db_has_columns(self, tmp_path):
        path = str(tmp_path / "r.db")
        reg_db.get_connection(path).close()
        assert "os_user" in _cols(path, "sessions")
        assert "os_user" in _cols(path, "agents")

    def test_register_session_records_os_user(self, tmp_path):
        path = str(tmp_path / "r.db")
        registry.register_session("s1", "/ws", 123, path)
        conn = sqlite3.connect(path)
        assert conn.execute("SELECT os_user FROM sessions").fetchone() == (os_user(),)
        conn.close()

    def test_heartbeat_insert_records_os_user(self, tmp_path):
        path = str(tmp_path / "r.db")
        registry.heartbeat_and_purge("s2", "/ws", 5, path)
        conn = sqlite3.connect(path)
        assert conn.execute(
            "SELECT os_user FROM sessions WHERE session_id='s2'"
        ).fetchone() == (os_user(),)
        conn.close()

    def test_agent_registration_records_os_user(self, tmp_path):
        path = str(tmp_path / "r.db")
        registry.register_agent("a1", "s1", "main", "/ws", path)
        conn = sqlite3.connect(path)
        assert conn.execute("SELECT os_user FROM agents").fetchone() == (os_user(),)
        conn.close()


class TestUpgradeFromPreviousRelease:
    def _old_db(self, path):
        conn = sqlite3.connect(path)
        # The previous release's get_connection() left the registry in WAL mode.
        # (The one-time switch to WAL ignores the busy handler, so concurrent
        # first-ever opens can fail with "locked": that is old behaviour and not
        # what the migration tests are about.)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(reg_db._DDL_CREATE_SESSIONS)
        conn.execute(reg_db._DDL_CREATE_AGENTS)
        conn.execute(reg_db._DDL_CREATE_AGENT_ACTIONS)
        conn.execute("INSERT INTO sessions VALUES ('old', '/w', 1, 1.0, 2.0)")
        conn.execute(
            "INSERT INTO agents VALUES ('ag', 'old', 'main', NULL, '/w', 1.0, 2.0, NULL)"
        )
        conn.commit()
        conn.close()

    def test_old_registry_db_opens_migrates_and_keeps_rows(self, tmp_path):
        path = str(tmp_path / "r.db")
        self._old_db(path)
        assert "os_user" not in _cols(path, "sessions")
        reg_db.get_connection(path).close()
        assert "os_user" in _cols(path, "sessions")
        conn = sqlite3.connect(path)
        assert conn.execute("SELECT session_id, os_user FROM sessions").fetchall() == [
            ("old", None)
        ]
        # The 5/8-column shapes the claude-usage monitor reads still work.
        assert conn.execute(
            "SELECT session_id, workspace_root, pid, start_time, last_seen FROM sessions"
        ).fetchall() == [("old", "/w", 1, 1.0, 2.0)]
        conn.close()

    def test_migration_idempotent_and_concurrent(self, tmp_path):
        path = str(tmp_path / "r.db")
        self._old_db(path)
        errors = []

        def worker():
            try:
                for _ in range(5):
                    reg_db.get_connection(path).close()
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert errors == []
        assert _cols(path, "sessions").count("os_user") == 1
        assert _cols(path, "agents").count("os_user") == 1

    def test_init_schema_also_migrates(self, tmp_path):
        path = str(tmp_path / "r.db")
        self._old_db(path)
        reg_db.init_schema(path)
        assert "os_user" in _cols(path, "sessions")


class TestSharedPermissions:
    def test_shared_registry_created_0660_under_restrictive_umask(self, tmp_path):
        shared = tmp_path / "shared"
        shared.mkdir()
        _config({"shared_data_dir": str(shared)})
        path = str(shared / "session_registry.db")
        old = os.umask(0o077)
        try:
            registry.register_session("s1", "/ws", 1, path)
        finally:
            os.umask(old)
        for suffix in ("", "-wal", "-shm"):
            f = Path(path + suffix)
            if f.exists():
                assert stat.S_IMODE(f.stat().st_mode) == 0o660, f.name

    def test_legacy_registry_file_mode_is_untouched(self, tmp_path):
        """No shared setting: we do not chmod anything (SQLite default mode)."""
        path = tmp_path / "r.db"
        old = os.umask(0o022)
        try:
            reg_db.get_connection(str(path)).close()
        finally:
            os.umask(old)
        assert stat.S_IMODE(path.stat().st_mode) != 0o660
