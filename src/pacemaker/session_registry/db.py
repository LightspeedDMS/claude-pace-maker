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
_PROD_DB_DIR = ".claude-pace-maker"
_PROD_DB_FILE = "session_registry.db"


def _production_db_path() -> str:
    """Compute the production DB path from the current home directory at call time."""
    return os.path.join(os.path.expanduser("~"), _PROD_DB_DIR, _PROD_DB_FILE)


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

    conn = sqlite3.connect(db_path)
    try:
        _apply_schema(conn)
        conn.commit()
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

    conn = sqlite3.connect(db_path)
    conn.execute(f"PRAGMA journal_mode={_JOURNAL_MODE}")
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    conn.execute(f"PRAGMA synchronous={_SYNCHRONOUS}")
    conn.commit()

    _apply_schema(conn)
    conn.commit()

    return conn
