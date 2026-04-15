"""
Unit tests for session_registry.db module.

Tests:
- Schema initialization (idempotent IF NOT EXISTS)
- PRAGMA verification (WAL, busy_timeout=2000ms exact, synchronous=NORMAL)
- Test-mode enforcement (RuntimeError when PACEMAKER_TEST_MODE=1 and path not set)
- DB path resolution (env var override vs production default)
- Connection factory behavior
"""

import sqlite3
import sys

import pytest

# ── PRAGMA constants ─────────────────────────────────────────────────────────
SQLITE_SYNCHRONOUS_NORMAL = 1
EXPECTED_BUSY_TIMEOUT_MS = 2000
EXPECTED_JOURNAL_MODE = "wal"

# ── SQL query fragments ──────────────────────────────────────────────────────
SQL_LIST_TABLE = "SELECT name FROM sqlite_master WHERE type='table' AND name=?"
SQL_LIST_INDEX = "SELECT name FROM sqlite_master WHERE type='index' AND name=?"
SQL_TABLE_INFO = "PRAGMA table_info(sessions)"
SQL_JOURNAL_MODE = "PRAGMA journal_mode"
SQL_BUSY_TIMEOUT = "PRAGMA busy_timeout"
SQL_SYNCHRONOUS = "PRAGMA synchronous"

# ── Schema constants ─────────────────────────────────────────────────────────
TABLE_SESSIONS = "sessions"
INDEX_WORKSPACE = "idx_workspace"
REQUIRED_COLUMNS = frozenset(
    {"session_id", "workspace_root", "pid", "start_time", "last_seen"}
)

# ── Production path fragments ────────────────────────────────────────────────
EXPECTED_DB_FILENAME = "session_registry.db"
EXPECTED_DB_DIR_FRAGMENT = ".claude-pace-maker"

# ── Environment variable names and values ────────────────────────────────────
ENV_REGISTRY_PATH = "PACEMAKER_SESSION_REGISTRY_PATH"
ENV_TEST_MODE = "PACEMAKER_TEST_MODE"
TEST_MODE_ENABLED = "1"

# ── Module paths for cache-busting ───────────────────────────────────────────
MOD_DB = "pacemaker.session_registry.db"
MOD_PACKAGE = "pacemaker.session_registry"

# ── Expected error messages ──────────────────────────────────────────────────
ERR_TEST_MODE_PATH_REQUIRED = "PACEMAKER_SESSION_REGISTRY_PATH must be set in test mode"


def _fresh_db_module():
    """Return a freshly imported db module, bypassing Python's module cache.

    Uses sys.modules.pop() to ensure env var changes applied by monkeypatch
    are visible to the module even when it was already imported in a prior test.
    """
    sys.modules.pop(MOD_DB, None)
    sys.modules.pop(MOD_PACKAGE, None)
    import pacemaker.session_registry.db as db_module

    return db_module


@pytest.fixture
def registry_db(tmp_path, monkeypatch):
    """Provide a freshly imported db module with PACEMAKER_SESSION_REGISTRY_PATH set to a temp path.

    Returns (db_module, db_path) tuple.
    """
    db_path = str(tmp_path / "test_registry.db")
    monkeypatch.setenv(ENV_REGISTRY_PATH, db_path)
    monkeypatch.setenv(ENV_TEST_MODE, TEST_MODE_ENABLED)
    db = _fresh_db_module()
    return db, db_path


@pytest.fixture
def raw_conn(registry_db):
    """Yield a raw sqlite3 connection to the initialized test DB, then close it."""
    db, db_path = registry_db
    db.init_schema(db_path)
    conn = sqlite3.connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def managed_conn(registry_db):
    """Yield (db, db_path, conn) where conn is from db.get_connection(), then close it."""
    db, db_path = registry_db
    conn = db.get_connection(db_path)
    try:
        yield db, db_path, conn
    finally:
        conn.close()


class TestDbPathResolution:
    """Tests for DB_PATH resolution logic."""

    def test_uses_env_var_when_set(self, tmp_path, monkeypatch):
        """When PACEMAKER_SESSION_REGISTRY_PATH is set, resolve_db_path returns that exact path."""
        custom_path = str(tmp_path / "custom_registry.db")
        monkeypatch.setenv(ENV_REGISTRY_PATH, custom_path)
        monkeypatch.setenv(ENV_TEST_MODE, TEST_MODE_ENABLED)

        db = _fresh_db_module()
        assert db.resolve_db_path() == custom_path

    def test_raises_in_test_mode_without_env_var(self, monkeypatch):
        """When PACEMAKER_TEST_MODE=1 and PACEMAKER_SESSION_REGISTRY_PATH unset, raise RuntimeError."""
        monkeypatch.setenv(ENV_TEST_MODE, TEST_MODE_ENABLED)
        monkeypatch.delenv(ENV_REGISTRY_PATH, raising=False)

        db = _fresh_db_module()
        with pytest.raises(RuntimeError, match=ERR_TEST_MODE_PATH_REQUIRED):
            db.resolve_db_path()

    def test_uses_default_path_in_production(self, monkeypatch):
        """When not in test mode and env var unset, resolve_db_path returns the default production path."""
        monkeypatch.delenv(ENV_TEST_MODE, raising=False)
        monkeypatch.delenv(ENV_REGISTRY_PATH, raising=False)

        db = _fresh_db_module()
        resolved = db.resolve_db_path()
        assert resolved.endswith(EXPECTED_DB_FILENAME)
        assert EXPECTED_DB_DIR_FRAGMENT in resolved


class TestSchemaInit:
    """Tests for schema initialization."""

    def test_schema_creates_sessions_table(self, raw_conn):
        """init_schema creates the sessions table."""
        cursor = raw_conn.execute(SQL_LIST_TABLE, (TABLE_SESSIONS,))
        assert cursor.fetchone() is not None

    def test_schema_creates_workspace_index(self, raw_conn):
        """init_schema creates the idx_workspace index."""
        cursor = raw_conn.execute(SQL_LIST_INDEX, (INDEX_WORKSPACE,))
        assert cursor.fetchone() is not None

    def test_schema_init_is_idempotent(self, registry_db):
        """Calling init_schema twice does not raise an error (IF NOT EXISTS guards)."""
        db, db_path = registry_db
        db.init_schema(db_path)
        db.init_schema(db_path)  # Must not raise

    def test_sessions_table_has_correct_columns(self, raw_conn):
        """Sessions table contains all required columns."""
        cursor = raw_conn.execute(SQL_TABLE_INFO)
        columns = {row[1] for row in cursor.fetchall()}
        assert REQUIRED_COLUMNS.issubset(columns)


class TestPragmas:
    """Tests that WAL mode, exact busy_timeout=2000ms, and synchronous=NORMAL are applied."""

    def test_wal_mode_enabled(self, managed_conn):
        """get_connection applies WAL journal mode."""
        _db, _db_path, conn = managed_conn
        cursor = conn.execute(SQL_JOURNAL_MODE)
        assert cursor.fetchone()[0] == EXPECTED_JOURNAL_MODE

    def test_busy_timeout_exactly_2000ms(self, managed_conn):
        """get_connection sets busy_timeout to exactly EXPECTED_BUSY_TIMEOUT_MS (2000ms)."""
        _db, _db_path, conn = managed_conn
        cursor = conn.execute(SQL_BUSY_TIMEOUT)
        assert cursor.fetchone()[0] == EXPECTED_BUSY_TIMEOUT_MS

    def test_synchronous_normal(self, managed_conn):
        """get_connection sets synchronous to NORMAL (SQLITE_SYNCHRONOUS_NORMAL=1)."""
        _db, _db_path, conn = managed_conn
        cursor = conn.execute(SQL_SYNCHRONOUS)
        assert cursor.fetchone()[0] == SQLITE_SYNCHRONOUS_NORMAL


class TestConnectionFactory:
    """Tests for connection factory."""

    def test_get_connection_returns_sqlite_connection(self, managed_conn):
        """get_connection returns a sqlite3.Connection instance."""
        _db, _db_path, conn = managed_conn
        assert isinstance(conn, sqlite3.Connection)

    def test_get_connection_initializes_schema(self, managed_conn):
        """get_connection initializes schema automatically so sessions table exists immediately."""
        _db, _db_path, conn = managed_conn
        cursor = conn.execute(SQL_LIST_TABLE, (TABLE_SESSIONS,))
        assert cursor.fetchone() is not None
