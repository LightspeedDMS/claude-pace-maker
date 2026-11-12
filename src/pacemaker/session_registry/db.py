"""
Session Registry SQLite database layer.

Public API:
- resolve_db_path() -> str
    Determine DB file path from env var or production default.
    Raises RuntimeError in test mode if path env var is unset.

- init_schema(db_path: str) -> None
    Idempotent schema creation (IF NOT EXISTS). Opens its own connection.

- get_connection(db_path: str) -> sqlite3.Connection
    Open a connection with WAL/busy_timeout/synchronous PRAGMAs applied
    and schema initialised on that same connection. Caller closes it.

Internal:
- _apply_schema(conn) — executes DDL statements only; caller owns commit.
"""

import os
import sqlite3

from ..data_dir import (
    confirm_shared_files,
    monitoring_db_path,
    prepare_db_file,
    shared_umask,
)

# ── DDL ───────────────────────────────────────────────────────────────────────
_DDL_CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id     TEXT PRIMARY KEY,
    workspace_root TEXT NOT NULL,
    pid            INTEGER,
    start_time     REAL,
    last_seen      REAL NOT NULL
)
"""

_DDL_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_workspace ON sessions (workspace_root)
"""

_DDL_CREATE_AGENTS = """
CREATE TABLE IF NOT EXISTS agents (
    agent_id       TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL,
    role           TEXT NOT NULL,
    subagent_type  TEXT,
    workspace_root TEXT NOT NULL,
    start_time     REAL NOT NULL,
    last_seen      REAL NOT NULL,
    ended_at       REAL
)
"""

_DDL_CREATE_AGENT_ACTIONS = """
CREATE TABLE IF NOT EXISTS agent_actions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id   TEXT NOT NULL,
    tool_name  TEXT NOT NULL,
    target     TEXT NOT NULL DEFAULT '-',
    ts         REAL NOT NULL,
    FOREIGN KEY (agent_id) REFERENCES agents(agent_id) ON DELETE CASCADE
)
"""

# ── PRAGMA values ─────────────────────────────────────────────────────────────
_BUSY_TIMEOUT_MS = 2000
_SYNCHRONOUS = "NORMAL"
_JOURNAL_MODE = "WAL"

# ── Environment variable names ────────────────────────────────────────────────
_ENV_REGISTRY_PATH = "PACEMAKER_SESSION_REGISTRY_PATH"
_ENV_TEST_MODE = "PACEMAKER_TEST_MODE"

# ── Production DB path components (computed dynamically at call time) ─────────
_PROD_DB_FILE = "session_registry.db"


def _production_db_path() -> str:
    """Production DB path, resolved at call time.

    Per-user ``~/.claude-pace-maker/session_registry.db`` unless the per-user
    config sets ``shared_data_dir`` (story #187), in which case it lives there.
    """
    return monitoring_db_path(_PROD_DB_FILE)


# Tables that record the writing OS user (story #187, AC4).
_OS_USER_TABLES = ("sessions", "agents")


def _migrate_os_user(conn: sqlite3.Connection) -> None:
    """Add nullable ``os_user`` to sessions/agents. Idempotent, race tolerant.

    Additive only (the claude-usage monitor reads these tables). A concurrent
    opener that wins the ALTER makes ours raise "duplicate column name",
    which is the expected outcome and is ignored.
    """
    for table in _OS_USER_TABLES:
        columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
        if "os_user" in columns:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN os_user TEXT")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise


def _apply_schema(conn: sqlite3.Connection) -> None:
    """Execute schema DDL statements on an existing connection.

    Execution order:
    1. PRAGMA foreign_keys = ON
    2. CREATE TABLE IF NOT EXISTS sessions
    3. CREATE INDEX IF NOT EXISTS idx_workspace
    4. CREATE TABLE IF NOT EXISTS agents
    5. CREATE TABLE IF NOT EXISTS agent_actions (FK to agents, cascade)

    Does NOT commit — transaction ownership belongs to the caller.
    """
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(_DDL_CREATE_SESSIONS)
    conn.execute(_DDL_CREATE_INDEX)
    conn.execute(_DDL_CREATE_AGENTS)
    conn.execute(_DDL_CREATE_AGENT_ACTIONS)
    _migrate_os_user(conn)


def resolve_db_path() -> str:
    """Return the DB file path to use.

    Resolution order:
    1. PACEMAKER_SESSION_REGISTRY_PATH env var (always honoured when set)
    2. Production default (~/.claude-pace-maker/session_registry.db)

    Test-mode enforcement: if PACEMAKER_TEST_MODE=1 and
    PACEMAKER_SESSION_REGISTRY_PATH is not set, raises RuntimeError so tests
    never accidentally write to the production DB.
    """
    env_path = os.environ.get(_ENV_REGISTRY_PATH)
    if env_path:
        return env_path

    if os.environ.get(_ENV_TEST_MODE) == "1":
        raise RuntimeError(
            "PACEMAKER_SESSION_REGISTRY_PATH must be set in test mode — "
            "conftest.py must provide a tmp path via monkeypatch.setenv"
        )

    return _production_db_path()


def init_schema(db_path: str) -> None:
    """Create the sessions table and idx_workspace index if they do not exist.

    Opens its own connection, applies schema DDL, commits, then closes.
    Idempotent: IF NOT EXISTS guards make concurrent calls safe.

    Args:
        db_path: Non-empty path to the SQLite file.

    Raises:
        ValueError: if db_path is empty or None.
    """
    if not db_path:
        raise ValueError("db_path must be a non-empty string")

    ident = prepare_db_file(db_path)
    with shared_umask(db_path):
        conn = sqlite3.connect(db_path)
        try:
            _apply_schema(conn)
            conn.commit()
            confirm_shared_files(db_path, ident)  # no symlink swapped in meanwhile
        finally:
            conn.close()


def get_connection(db_path: str) -> sqlite3.Connection:
    """Open a connection to the registry DB with PRAGMAs and schema on the same connection.

    Steps performed on the single returned connection:
    1. PRAGMA journal_mode=WAL
    2. PRAGMA busy_timeout=2000
    3. PRAGMA synchronous=NORMAL
    4. Commit PRAGMA changes
    5. _apply_schema() — sessions table + idx_workspace (DDL only, no commit)
    6. Commit schema DDL

    Caller is responsible for closing the returned connection.

    Args:
        db_path: Non-empty path to the SQLite file.

    Raises:
        ValueError: if db_path is empty or None.
    """
    if not db_path:
        raise ValueError("db_path must be a non-empty string")

    ident = prepare_db_file(db_path)
    with shared_umask(db_path):
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(f"PRAGMA journal_mode={_JOURNAL_MODE}")
            conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            conn.execute(f"PRAGMA synchronous={_SYNCHRONOUS}")
            conn.commit()

            _apply_schema(conn)
            conn.commit()
            confirm_shared_files(db_path, ident)  # no symlink swapped in meanwhile
        except BaseException:
            conn.close()
            raise

    return conn
