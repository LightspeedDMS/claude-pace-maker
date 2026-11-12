"""Story #187 AC2/AC3/AC6: secrets stay per user; default mode untouched.

Real SQLite files and real masking; no mocks.
"""

import json
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from pacemaker import database as db
from schema_v1_support import v1_schema
from pacemaker.data_dir import SharedDataDirError
from pacemaker.secrets import database as sdb
from pacemaker.secrets.sanitizer import sanitize_trace


def _per_user():
    return Path(os.path.expanduser("~")) / ".claude-pace-maker"


def _config(content):
    cfg = _per_user() / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(content))


def _mode(p):
    return stat.S_IMODE(Path(p).stat().st_mode)


def _old_release_usage_db(path, secrets):
    """Build usage.db exactly as the previous release's code left it: the
    SCHEMA at version 1 plus the secrets table created by secrets/database.py."""
    conn = sqlite3.connect(path)
    conn.executescript(v1_schema())
    conn.execute("PRAGMA user_version = 1")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS secrets (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "type TEXT NOT NULL, value TEXT NOT NULL, "
        "created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')))"
    )
    conn.execute("CREATE UNIQUE INDEX idx_secrets_type_value ON secrets(type, value)")
    for kind, value in secrets:
        conn.execute("INSERT INTO secrets (type, value) VALUES (?, ?)", (kind, value))
    conn.commit()
    conn.close()
    os.chmod(path, 0o600)


class TestDefaultModeRegression:
    """AC6: no setting => secrets live in the SAME db_path, mode 0600."""

    def test_secret_stored_in_given_db_with_0600(self, tmp_path):
        path = str(tmp_path / "usage.db")
        assert sdb.create_secret(path, "text", "hunter2-hunter2") is not None
        assert _mode(path) == 0o600
        conn = sqlite3.connect(path)
        assert conn.execute("SELECT value FROM secrets").fetchall() == [
            ("hunter2-hunter2",)
        ]
        conn.close()
        assert not (_per_user() / "secrets.db").exists()

    def test_pre_upgrade_usage_db_with_secrets_keeps_working(self, tmp_path):
        """Existing install, upgrade, NO setting: secrets, masking and metrics work."""
        path = str(tmp_path / "usage.db")
        _old_release_usage_db(path, [("text", "sk-live-ABCDEF123456")])

        assert db.initialize_database(path)  # new code opens + migrates
        assert db.record_activity_event(path, "IV", "green", "s")

        secrets = sdb.list_secrets(path)
        assert [s["value"] for s in secrets] == ["sk-live-ABCDEF123456"]
        sanitized, count = sanitize_trace(
            {"msg": "token sk-live-ABCDEF123456 here"}, path
        )
        assert count == 1
        assert "sk-live-ABCDEF123456" not in json.dumps(sanitized)
        assert _mode(path) == 0o600
        # secrets did NOT move out of usage.db
        conn = sqlite3.connect(path)
        assert conn.execute("SELECT COUNT(*) FROM secrets").fetchone() == (1,)
        assert conn.execute("PRAGMA user_version").fetchone() == (2,)
        conn.close()
        assert not (_per_user() / "secrets.db").exists()

    def test_secrets_added_after_upgrade_still_land_in_usage_db(self, tmp_path):
        path = str(tmp_path / "usage.db")
        _old_release_usage_db(path, [("text", "old-secret-value")])
        db.initialize_database(path)
        sdb.create_secret(path, "text", "new-secret-value")
        conn = sqlite3.connect(path)
        assert {r[0] for r in conn.execute("SELECT value FROM secrets")} == {
            "old-secret-value",
            "new-secret-value",
        }
        conn.close()


class TestSharedMode:
    @pytest.fixture
    def shared(self, tmp_path):
        d = tmp_path / "shared"
        d.mkdir()
        _config({"shared_data_dir": str(d)})
        return d

    def test_secrets_go_to_per_user_secrets_db_0600(self, shared):
        shared_usage = str(shared / "usage.db")
        db.initialize_database(shared_usage)
        assert sdb.create_secret(shared_usage, "text", "shared-mode-secret") is not None

        user_db = _per_user() / "secrets.db"
        assert user_db.exists()
        assert _mode(user_db) == 0o600
        conn = sqlite3.connect(user_db)
        assert conn.execute("SELECT value FROM secrets").fetchall() == [
            ("shared-mode-secret",)
        ]
        conn.close()

    def test_shared_usage_db_never_holds_secrets_and_stays_0660(self, shared):
        shared_usage = str(shared / "usage.db")
        db.initialize_database(shared_usage)
        sdb.create_secret(shared_usage, "text", "another-secret-value")
        sdb.list_secrets(shared_usage)
        conn = sqlite3.connect(shared_usage)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        assert "secrets" not in tables
        conn.close()
        assert _mode(shared_usage) == 0o660
        assert sorted(p.name for p in shared.iterdir() if p.is_file()) == sorted(
            n for n in (p.name for p in shared.iterdir()) if n.startswith("usage.db")
        )

    def test_masking_works_in_shared_mode(self, shared):
        shared_usage = str(shared / "usage.db")
        db.initialize_database(shared_usage)
        sdb.create_secret(shared_usage, "text", "masked-in-shared-mode")
        sanitized, count = sanitize_trace(
            {"m": "x masked-in-shared-mode y"}, shared_usage
        )
        assert count == 1
        assert "masked-in-shared-mode" not in json.dumps(sanitized)

    def test_switching_to_shared_preserves_existing_per_user_secrets(self, tmp_path):
        # 1. existing install: secrets live in the per-user usage.db
        legacy = str(_per_user() / "usage.db")
        _old_release_usage_db(legacy, [("text", "pre-switch-secret"), ("file", "/f")])
        # 2. user turns shared mode on later
        shared = tmp_path / "shared"
        shared.mkdir()
        _config({"shared_data_dir": str(shared)})
        shared_usage = str(shared / "usage.db")
        db.initialize_database(shared_usage)

        values = sorted(s["value"] for s in sdb.list_secrets(shared_usage))
        assert values == ["/f", "pre-switch-secret"]
        sanitized, count = sanitize_trace({"m": "pre-switch-secret"}, shared_usage)
        assert count == 1
        # copy, not move: the per-user legacy file is left intact for rollback
        conn = sqlite3.connect(legacy)
        assert conn.execute("SELECT COUNT(*) FROM secrets").fetchone() == (2,)
        conn.close()

    def test_migration_runs_once_so_removed_secrets_do_not_resurrect(self, tmp_path):
        legacy = str(_per_user() / "usage.db")
        _old_release_usage_db(legacy, [("text", "to-be-removed-secret")])
        shared = tmp_path / "shared"
        shared.mkdir()
        _config({"shared_data_dir": str(shared)})
        shared_usage = str(shared / "usage.db")
        rows = sdb.list_secrets(shared_usage)
        assert len(rows) == 1
        assert sdb.remove_secret(shared_usage, rows[0]["id"])
        sdb._initialized_dbs.clear()  # simulate a new process
        assert sdb.list_secrets(shared_usage) == []

    def test_no_legacy_db_means_empty_secrets(self, shared):
        assert sdb.list_secrets(str(shared / "usage.db")) == []

    def test_chmod_0600_is_refused_on_a_shared_file(self, shared):
        """Defensive invariant: the 0600 path can never target the shared dir."""
        with pytest.raises(SharedDataDirError):
            sdb._secure_per_user_file(str(shared / "usage.db"))

    def test_explicit_non_shared_path_is_not_remapped(self, shared, tmp_path):
        other = str(tmp_path / "elsewhere.db")
        sdb.create_secret(other, "text", "explicit-path-secret")
        assert Path(other).exists()
        assert _mode(other) == 0o600
