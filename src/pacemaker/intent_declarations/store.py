"""
Declaration store for the declare_intent MCP tool (Story #155).

A small, TEMPORARY SQLite database -- ``~/.claude-pace-maker/
intent_declarations.db`` -- following the ``session_registry`` pattern: WAL,
busy timeout, env override (``PACEMAKER_INTENT_DECLARATIONS_PATH``), and a
``RuntimeError`` in test mode when the override is unset.

Two tables, both keyed by ``session_id`` + ``agent_key`` (``agent_key`` is the
hook payload's ``agent_id``, or ``"main"`` for the main thread -- subagents
share the parent's ``session_id``, so the agent key is what keeps them apart):

- ``declarations`` -- unconsumed ``declare_intent`` calls, one row per call.
- ``chains`` -- the single declaration an agent may currently REUSE for
  consecutive edits of the same file (PRIMARY KEY (session_id, agent_key)).

Self-cleaning: every store access (record, resolve, approve, reject) first
deletes rows older than ``INTENT_DECLARATION_TTL_SECONDS`` (60 minutes).

Concurrency: hook processes run in parallel (main thread + subagents), so
each operation is ONE ``BEGIN IMMEDIATE`` transaction -- in particular
``resolve`` selects-and-deletes the newest declaration atomically, so two
concurrent gates can never both consume the same row.

File paths are stored exactly as given: callers normalize them first (see
``fields.normalize_file_path``) at both record and match time.
"""

import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

from ..constants import (
    INTENT_DECLARATION_TTL_SECONDS,
    REJECTED_DECLARATION_MARKER_TTL_SECONDS,
    SUBAGENT_GUIDANCE_TTL_SECONDS,
)

SOURCE_DECLARATION = "declare_intent"
SOURCE_CHAIN = "declare_intent_chain"

_ENV_DB_PATH = "PACEMAKER_INTENT_DECLARATIONS_PATH"
_ENV_TEST_MODE = "PACEMAKER_TEST_MODE"
_PROD_DB_DIR = ".claude-pace-maker"
_PROD_DB_FILE = "intent_declarations.db"

_BUSY_TIMEOUT_MS = 2000
_JOURNAL_MODE = "WAL"
_SYNCHRONOUS = "NORMAL"

_DDL_DECLARATIONS = """
CREATE TABLE IF NOT EXISTS declarations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL,
    agent_key     TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    change        TEXT NOT NULL,
    goal          TEXT NOT NULL,
    test_coverage TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL
)
"""

_DDL_DECLARATIONS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_declarations_lookup
    ON declarations (session_id, agent_key, file_path)
"""

# Bug #157: per-(session, agent) guidance-delivery record. start_completed_at
# is set as the LAST step of a finished SubagentStart; delivered_at when
# PostToolUse injected the guidance late. Either one means "never again".
_DDL_SUBAGENT_GUIDANCE = """
CREATE TABLE IF NOT EXISTS subagent_guidance (
    session_id         TEXT NOT NULL,
    agent_key          TEXT NOT NULL,
    start_completed_at REAL,
    delivered_at       REAL,
    updated_at         REAL NOT NULL,
    PRIMARY KEY (session_id, agent_key)
)
"""

_DDL_CHAINS = """
CREATE TABLE IF NOT EXISTS chains (
    session_id    TEXT NOT NULL,
    agent_key     TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    change        TEXT NOT NULL,
    goal          TEXT NOT NULL,
    test_coverage TEXT NOT NULL DEFAULT '',
    updated_at    REAL NOT NULL,
    PRIMARY KEY (session_id, agent_key)
)
"""

# Bug #163: "a rejection just consumed this agent's declaration for this
# file". Purged after REJECTED_DECLARATION_MARKER_TTL_SECONDS on every access.
_DDL_REJECTED_DECLARATIONS = """
CREATE TABLE IF NOT EXISTS rejected_declarations (
    session_id TEXT NOT NULL,
    agent_key  TEXT NOT NULL,
    file_path  TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (session_id, agent_key, file_path)
)
"""


@dataclass(frozen=True)
class StoredIntent:
    """One declared intent (a declaration row or a chain row)."""

    file_path: str
    change: str
    goal: str
    test_coverage: str


@dataclass(frozen=True)
class Resolution:
    """The outcome of a gate lookup: where the intent came from, and it.

    ``source`` is ``"declare_intent"`` (a consumed declaration) or
    ``"declare_intent_chain"`` (the agent's reusable chain).
    """

    source: str
    intent: StoredIntent


def resolve_db_path() -> str:
    """Return the DB file path to use.

    1. ``PACEMAKER_INTENT_DECLARATIONS_PATH`` when set (always honoured).
    2. Production default ``~/.claude-pace-maker/intent_declarations.db``.

    Raises:
        RuntimeError: ``PACEMAKER_TEST_MODE=1`` and the override is unset, so
            a test can never silently write to the production database.
    """
    env_path = os.environ.get(_ENV_DB_PATH)
    if env_path:
        return env_path
    if os.environ.get(_ENV_TEST_MODE) == "1":
        raise RuntimeError(
            f"{_ENV_DB_PATH} must be set in test mode -- conftest.py must "
            "provide a tmp path via monkeypatch.setenv"
        )
    return os.path.join(os.path.expanduser("~"), _PROD_DB_DIR, _PROD_DB_FILE)


class IntentDeclarationStore:
    """SQLite-backed declaration store. Cheap to construct: every operation
    opens its own short-lived connection, so instances hold no handles."""

    def __init__(
        self,
        db_path: str,
        ttl_seconds: float = INTENT_DECLARATION_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
        guidance_ttl_seconds: float = SUBAGENT_GUIDANCE_TTL_SECONDS,
        marker_ttl_seconds: float = REJECTED_DECLARATION_MARKER_TTL_SECONDS,
    ):
        if not db_path:
            raise ValueError("db_path must be a non-empty string")
        self._db_path = db_path
        self._ttl_seconds = ttl_seconds
        self._guidance_ttl_seconds = guidance_ttl_seconds
        self._marker_ttl_seconds = marker_ttl_seconds
        self._clock = clock

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """One BEGIN IMMEDIATE transaction on a fresh connection, with the
        TTL purge as its first statement -- the single place that makes
        "purge on every store access" true."""
        directory = os.path.dirname(self._db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        conn = sqlite3.connect(
            self._db_path, timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None
        )
        try:
            conn.execute(f"PRAGMA journal_mode={_JOURNAL_MODE}")
            conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            conn.execute(f"PRAGMA synchronous={_SYNCHRONOUS}")
            conn.execute(_DDL_DECLARATIONS)
            conn.execute(_DDL_DECLARATIONS_INDEX)
            conn.execute(_DDL_CHAINS)
            conn.execute(_DDL_SUBAGENT_GUIDANCE)
            conn.execute(_DDL_REJECTED_DECLARATIONS)
            conn.execute("BEGIN IMMEDIATE")
            try:
                now = self._clock()
                cutoff = now - self._ttl_seconds
                conn.execute("DELETE FROM declarations WHERE created_at < ?", (cutoff,))
                conn.execute("DELETE FROM chains WHERE updated_at < ?", (cutoff,))
                conn.execute(
                    "DELETE FROM subagent_guidance WHERE updated_at < ?",
                    (now - self._guidance_ttl_seconds,),
                )
                conn.execute(
                    "DELETE FROM rejected_declarations WHERE created_at < ?",
                    (now - self._marker_ttl_seconds,),
                )
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    def record(
        self,
        session_id: str,
        agent_key: str,
        file_path: str,
        change: str,
        goal: str,
        test_coverage: str = "",
    ) -> None:
        """Insert one unconsumed declaration. A fresh declaration for the
        file ends any Bug #163 rejection marker for it: the agent has done
        what the marker's note asks."""
        with self._transaction() as conn:
            conn.execute(
                "DELETE FROM rejected_declarations "
                "WHERE session_id = ? AND agent_key = ? AND file_path = ?",
                (session_id, agent_key, file_path),
            )
            conn.execute(
                "INSERT INTO declarations "
                "(session_id, agent_key, file_path, change, goal, "
                "test_coverage, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    agent_key,
                    file_path,
                    change,
                    goal,
                    test_coverage or "",
                    self._clock(),
                ),
            )

    def resolve(
        self, session_id: str, agent_key: str, file_path: str
    ) -> Optional[Resolution]:
        """Gate lookup, steps 1-3 of the Write/Edit gate, atomically.

        1. A declaration for ``file_path``: take the NEWEST as the intent,
           delete ALL declarations for that session+agent+file (consume),
           return it.
        2. Else the agent's chain, when it is for the same ``file_path``:
           return it (the chain row stays; approve/reject update it).
        3. Else any chain for a DIFFERENT file is deleted -- different files
           need different declarations -- and ``None`` is returned so the
           caller falls back to the transcript path.
        """
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT id, change, goal, test_coverage FROM declarations "
                "WHERE session_id = ? AND agent_key = ? AND file_path = ? "
                "ORDER BY id DESC LIMIT 1",
                (session_id, agent_key, file_path),
            ).fetchone()
            if row is not None:
                # Consume EVERY row for this session+agent+file (the newest
                # is the intent): a leftover older duplicate would otherwise
                # satisfy a later "return to this file", which AC5 says
                # needs a NEW declaration.
                conn.execute(
                    "DELETE FROM declarations "
                    "WHERE session_id = ? AND agent_key = ? AND file_path = ?",
                    (session_id, agent_key, file_path),
                )
                return Resolution(
                    SOURCE_DECLARATION,
                    StoredIntent(file_path, row[1], row[2], row[3]),
                )
            chain = conn.execute(
                "SELECT file_path, change, goal, test_coverage FROM chains "
                "WHERE session_id = ? AND agent_key = ?",
                (session_id, agent_key),
            ).fetchone()
            if chain is None:
                return None
            if chain[0] == file_path:
                return Resolution(
                    SOURCE_CHAIN, StoredIntent(chain[0], chain[1], chain[2], chain[3])
                )
            conn.execute(
                "DELETE FROM chains WHERE session_id = ? AND agent_key = ?",
                (session_id, agent_key),
            )
            return None

    def approve(self, session_id: str, agent_key: str, intent: StoredIntent) -> None:
        """Upsert the agent's chain with an approved intent, so consecutive
        edits of the same file can reuse it."""
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO chains "
                "(session_id, agent_key, file_path, change, goal, "
                "test_coverage, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id, agent_key) DO UPDATE SET "
                "file_path = excluded.file_path, change = excluded.change, "
                "goal = excluded.goal, "
                "test_coverage = excluded.test_coverage, "
                "updated_at = excluded.updated_at",
                (
                    session_id,
                    agent_key,
                    intent.file_path,
                    intent.change,
                    intent.goal,
                    intent.test_coverage or "",
                    self._clock(),
                ),
            )

    def reject(
        self,
        session_id: str,
        agent_key: str,
        rejected_file_path: Optional[str] = None,
    ) -> None:
        """Delete the agent's chain (a rejected Write/Edit ends reuse).

        Bug #163: when ``rejected_file_path`` is given (the rejected edit was
        using a declaration/chain for that file, which this rejection ends),
        also leave a short-lived marker so a sibling edit of the same batch,
        blocked for having no declaration, can be told why."""
        with self._transaction() as conn:
            conn.execute(
                "DELETE FROM chains WHERE session_id = ? AND agent_key = ?",
                (session_id, agent_key),
            )
            if rejected_file_path is not None:
                conn.execute(
                    "INSERT INTO rejected_declarations "
                    "(session_id, agent_key, file_path, created_at) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(session_id, agent_key, file_path) DO UPDATE "
                    "SET created_at = excluded.created_at",
                    (session_id, agent_key, rejected_file_path, self._clock()),
                )

    def has_rejection_marker(
        self, session_id: str, agent_key: str, file_path: str
    ) -> bool:
        """Bug #163: True iff a rejection consumed this agent's declaration
        for ``file_path`` within the marker TTL (expired markers are purged
        by the transaction itself, so presence means unexpired)."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT 1 FROM rejected_declarations "
                "WHERE session_id = ? AND agent_key = ? AND file_path = ?",
                (session_id, agent_key, file_path),
            ).fetchone()
        return row is not None

    def mark_subagent_start_completed(self, session_id: str, agent_key: str) -> None:
        """Bug #157: record that SubagentStart FINISHED (output written) for
        this agent, so its first PostToolUse never repeats the guidance."""
        now = self._clock()
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO subagent_guidance "
                "(session_id, agent_key, start_completed_at, delivered_at, "
                "updated_at) VALUES (?, ?, ?, NULL, ?) "
                "ON CONFLICT(session_id, agent_key) DO UPDATE SET "
                "start_completed_at = excluded.start_completed_at, "
                "updated_at = excluded.updated_at",
                (session_id, agent_key, now, now),
            )

    def is_late_guidance_settled(self, session_id: str, agent_key: str) -> bool:
        """Cheap READ-ONLY pre-check (code-review L2): True iff a completion or
        a late delivery is already recorded for this agent -- i.e. the common
        case of every subagent tool call after the first. No write lock, no
        purge, no schema creation: a missing DB file or table simply means
        "not settled". The authoritative decision stays ``claim_late_guidance``
        (atomic); a store ERROR (not a missing file/table) propagates."""
        if not os.path.exists(self._db_path):
            return False
        conn = sqlite3.connect(self._db_path, timeout=_BUSY_TIMEOUT_MS / 1000)
        try:
            conn.execute("PRAGMA query_only = ON")
            try:
                row = conn.execute(
                    "SELECT start_completed_at, delivered_at FROM subagent_guidance "
                    "WHERE session_id = ? AND agent_key = ?",
                    (session_id, agent_key),
                ).fetchone()
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc):
                    return False
                raise
        finally:
            conn.close()
        return row is not None and (row[0] is not None or row[1] is not None)

    def claim_late_guidance(self, session_id: str, agent_key: str) -> bool:
        """Bug #157: atomically decide whether PostToolUse must inject the
        subagent guidance NOW. True iff SubagentStart never completed for this
        agent AND no late delivery happened yet; the delivery is recorded in
        the same transaction, so concurrent PostToolUse hooks of one agent can
        never both claim it."""
        now = self._clock()
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT start_completed_at, delivered_at FROM subagent_guidance "
                "WHERE session_id = ? AND agent_key = ?",
                (session_id, agent_key),
            ).fetchone()
            if row is not None and (row[0] is not None or row[1] is not None):
                return False
            conn.execute(
                "INSERT INTO subagent_guidance "
                "(session_id, agent_key, start_completed_at, delivered_at, "
                "updated_at) VALUES (?, ?, NULL, ?, ?) "
                "ON CONFLICT(session_id, agent_key) DO UPDATE SET "
                "delivered_at = excluded.delivered_at, "
                "updated_at = excluded.updated_at",
                (session_id, agent_key, now, now),
            )
            return True
