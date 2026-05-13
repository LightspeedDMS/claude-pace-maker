"""
Session Registry — public API and internal helpers.

Public API:
- register_session(session_id, workspace_root, pid, db_path): INSERT OR REPLACE a session row
- heartbeat_and_purge(session_id, workspace_root, pid, db_path): update last_seen and purge stale rows
- list_siblings(workspace_root, exclude_session_id, db_path): return sibling sessions in same workspace
- unregister_session(session_id, db_path): delete the session row on shutdown

Internal helpers:
- _open_conn(db_path): context manager yielding an open DB connection
- _validate_session_args(session_id, workspace_root, pid, db_path): common arg validator
"""

import os
import time
from contextlib import contextmanager
from typing import Any, Dict, Generator, List, Optional

from .db import get_connection
from ..logger import log_warning

# ── SQL: register ─────────────────────────────────────────────────────────────
_SQL_REGISTER = (
    "INSERT OR REPLACE INTO sessions "
    "(session_id, workspace_root, pid, start_time, last_seen) "
    "VALUES (?, ?, ?, ?, ?)"
)

# ── SQL: heartbeat update/insert/purge ───────────────────────────────────────
_SQL_HEARTBEAT_UPDATE = "UPDATE sessions SET last_seen = ? WHERE session_id = ?"
_SQL_HEARTBEAT_INSERT = (
    "INSERT INTO sessions "
    "(session_id, workspace_root, pid, start_time, last_seen) "
    "VALUES (?, ?, ?, ?, ?)"
)
_SQL_PURGE = "DELETE FROM sessions WHERE last_seen < ?"
_SQL_LIST_SIBLINGS = (
    "SELECT session_id, workspace_root, start_time, last_seen, pid "
    "FROM sessions "
    "WHERE workspace_root = ? AND session_id != ? "
    "ORDER BY start_time ASC"
)
_SQL_UNREGISTER = "DELETE FROM sessions WHERE session_id = ?"

# ── Purge cutoff: env var override or 20-minute default ──────────────────────
_DEFAULT_PURGE_CUTOFF_SECONDS = int(
    os.environ.get("PACEMAKER_PURGE_CUTOFF_SECONDS", "1200")
)


@contextmanager
def _open_conn(db_path: str) -> Generator:
    """Open a registry DB connection, yield it, and always close it."""
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


def _validate_session_args(
    session_id: str,
    workspace_root: str,
    pid: int,
    db_path: str,
) -> bool:
    """Validate the 4 common session arguments. Log warning and return False on failure.

    pid validation uses type(pid) is not int (not isinstance) to reject bool,
    since bool is a subclass of int in Python.
    """
    if not session_id or not isinstance(session_id, str):
        log_warning("session_registry", "session_id must be a non-empty string")
        return False
    if not workspace_root or not isinstance(workspace_root, str):
        log_warning("session_registry", "workspace_root must be a non-empty string")
        return False
    if type(pid) is not int or pid <= 0:
        log_warning(
            "session_registry",
            f"pid={pid!r} must be a positive integer (bool not accepted)",
        )
        return False
    if not db_path or not isinstance(db_path, str):
        log_warning("session_registry", "db_path must be a non-empty string")
        return False
    return True


def register_session(
    session_id: str,
    workspace_root: str,
    pid: int,
    db_path: str,
) -> None:
    """Insert or replace a session row with start_time and last_seen set to now."""
    if not _validate_session_args(session_id, workspace_root, pid, db_path):
        return
    try:
        now = time.time()
        with _open_conn(db_path) as conn:
            conn.execute(_SQL_REGISTER, (session_id, workspace_root, pid, now, now))
            conn.commit()
    except Exception as e:
        log_warning("session_registry", f"register_session failed for {session_id}", e)


def heartbeat_and_purge(
    session_id: str,
    workspace_root: str,
    pid: int,
    db_path: str,
    purge_cutoff_seconds: int = _DEFAULT_PURGE_CUTOFF_SECONDS,
) -> None:
    """Update last_seen and purge stale sessions atomically via BEGIN IMMEDIATE."""
    if not _validate_session_args(session_id, workspace_root, pid, db_path):
        return
    if not isinstance(purge_cutoff_seconds, (int, float)) or purge_cutoff_seconds < 0:
        log_warning(
            "session_registry",
            f"purge_cutoff_seconds={purge_cutoff_seconds!r} must be >= 0",
        )
        return
    try:
        now = time.time()
        cutoff = now - purge_cutoff_seconds
        with _open_conn(db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(_SQL_HEARTBEAT_UPDATE, (now, session_id))
            if cursor.rowcount == 0:
                conn.execute(
                    _SQL_HEARTBEAT_INSERT,
                    (session_id, workspace_root, pid, now, now),
                )
            conn.execute(_SQL_PURGE, (cutoff,))
            conn.commit()
    except Exception as e:
        log_warning(
            "session_registry", f"heartbeat_and_purge failed for {session_id}", e
        )


def list_siblings(
    workspace_root: str,
    exclude_session_id: str,
    db_path: str,
) -> List[Dict[str, Any]]:
    """Return sessions in the same workspace, excluding the caller.

    Returns an empty list on invalid input or any DB error (fail-open).
    Rows are ordered by start_time ascending (oldest sibling first).
    """
    if not workspace_root or not isinstance(workspace_root, str):
        log_warning(
            "session_registry",
            "list_siblings: workspace_root must be a non-empty string",
        )
        return []
    if not exclude_session_id or not isinstance(exclude_session_id, str):
        log_warning(
            "session_registry",
            "list_siblings: exclude_session_id must be a non-empty string",
        )
        return []
    if not db_path or not isinstance(db_path, str):
        log_warning(
            "session_registry", "list_siblings: db_path must be a non-empty string"
        )
        return []
    try:
        with _open_conn(db_path) as conn:
            cursor = conn.execute(
                _SQL_LIST_SIBLINGS, (workspace_root, exclude_session_id)
            )
            rows = cursor.fetchall()
        return [
            {
                "session_id": row[0],
                "workspace_root": row[1],
                "start_time": row[2],
                "last_seen": row[3],
                "pid": row[4],
            }
            for row in rows
        ]
    except Exception as e:
        log_warning(
            "session_registry",
            f"list_siblings failed for workspace {workspace_root}",
            e,
        )
        return []


def unregister_session(session_id: str, db_path: str) -> None:
    """Delete the session row. No-op if the row does not exist (best-effort)."""
    if not session_id or not isinstance(session_id, str):
        log_warning(
            "session_registry",
            "unregister_session: session_id must be a non-empty string",
        )
        return
    if not db_path or not isinstance(db_path, str):
        log_warning(
            "session_registry", "unregister_session: db_path must be a non-empty string"
        )
        return
    try:
        with _open_conn(db_path) as conn:
            conn.execute(_SQL_UNREGISTER, (session_id,))
            conn.commit()
    except Exception as e:
        log_warning(
            "session_registry", f"unregister_session failed for {session_id}", e
        )


# ── Agent activity tracking ──────────────────────────────────────────────────

_AGENT_STALE_CUTOFF_SECONDS = 1200
_AGENT_ENDED_RETENTION_SECONDS = 60
_MAX_AGENT_ACTIONS = 3

_SQL_REGISTER_AGENT = (
    "INSERT OR REPLACE INTO agents "
    "(agent_id, session_id, role, subagent_type, workspace_root, start_time, last_seen, ended_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)"
)
_SQL_INSERT_ACTION = (
    "INSERT INTO agent_actions (agent_id, tool_name, target, ts) VALUES (?, ?, ?, ?)"
)
_SQL_TRIM_ACTIONS = (
    "DELETE FROM agent_actions WHERE agent_id = ? AND id NOT IN "
    "(SELECT id FROM agent_actions WHERE agent_id = ? ORDER BY ts DESC, id DESC LIMIT ?)"
)
_SQL_MARK_ENDED = "UPDATE agents SET ended_at = ? WHERE agent_id = ?"
_SQL_PURGE_STALE_ACTIVE = "DELETE FROM agents WHERE ended_at IS NULL AND last_seen < ?"
_SQL_PURGE_ENDED = "DELETE FROM agents WHERE ended_at IS NOT NULL AND ended_at < ?"
_SQL_PURGE_ORPHAN_ACTIONS = (
    "DELETE FROM agent_actions WHERE agent_id NOT IN (SELECT agent_id FROM agents)"
)
_SQL_LIST_AGENTS = (
    "SELECT agent_id, session_id, role, subagent_type, workspace_root, "
    "start_time, last_seen, ended_at FROM agents"
)
_SQL_LIST_ACTIONS = (
    "SELECT tool_name, target, ts FROM agent_actions "
    "WHERE agent_id = ? ORDER BY ts ASC, id ASC"
)
_SQL_HEARTBEAT_AGENT = (
    "UPDATE agents SET last_seen = ?, ended_at = NULL WHERE agent_id = ?"
)

# ── Shared validators ─────────────────────────────────────────────────────────


def _require_str(value: Any, param_name: str, caller: str) -> bool:
    """Return True if value is a non-empty str; log warning and return False otherwise."""
    if not value or not isinstance(value, str):
        log_warning(
            "session_registry", f"{caller}: {param_name} must be a non-empty string"
        )
        return False
    return True


def _require_numeric_or_none(value: Any, param_name: str, caller: str) -> bool:
    """Return True if value is None or a non-bool numeric; log warning and return False otherwise.

    Explicitly rejects bool because bool is a subclass of int in Python.
    """
    if value is None:
        return True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        log_warning(
            "session_registry", f"{caller}: {param_name} must be numeric or None"
        )
        return False
    return True


def extract_target(tool_name: str, tool_input: Dict[str, Any]) -> str:
    """Extract a short display target string from a tool call.

    Validates inputs and logs a warning on invalid types.
    Returns the basename of file_path for Edit/Write/Read, the full command
    for Bash, the subagent_type for Task, or '' for unknown tools or invalid inputs.
    """
    if not isinstance(tool_name, str):
        log_warning("session_registry", "extract_target: tool_name must be a string")
        return ""
    if not isinstance(tool_input, dict):
        log_warning("session_registry", "extract_target: tool_input must be a dict")
        return ""
    if tool_name in ("Edit", "Write", "Read"):
        fp = tool_input.get("file_path", "")
        if not isinstance(fp, str) or not fp:
            return ""
        return os.path.basename(fp)
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        return cmd if isinstance(cmd, str) else ""
    if tool_name == "Task":
        st = tool_input.get("subagent_type", "")
        return st if isinstance(st, str) else ""
    return ""


def classify_agent(ended_at: Optional[float], now: float) -> str:
    """Classify an agent as 'active', 'ended_visible', or 'purged'.

    Validates inputs and logs a warning on invalid types; returns 'active' on bad input.
    ended_at=None: active. ended_at within retention window: ended_visible. Older: purged.
    """
    if not _require_numeric_or_none(ended_at, "ended_at", "classify_agent"):
        return "active"
    if isinstance(now, bool) or not isinstance(now, (int, float)):
        log_warning("session_registry", "classify_agent: now must be numeric")
        return "active"
    if ended_at is None:
        return "active"
    if now - ended_at <= _AGENT_ENDED_RETENTION_SECONDS:
        return "ended_visible"
    return "purged"


def register_agent(
    agent_id: str,
    session_id: str,
    role: str,
    workspace_root: str,
    db_path: str,
    subagent_type: Optional[str] = None,
) -> None:
    """Insert or replace an agent row with start_time and last_seen set to now."""
    if not _require_str(agent_id, "agent_id", "register_agent"):
        return
    if not _require_str(session_id, "session_id", "register_agent"):
        return
    if not _require_str(role, "role", "register_agent"):
        return
    if not _require_str(workspace_root, "workspace_root", "register_agent"):
        return
    if not _require_str(db_path, "db_path", "register_agent"):
        return
    if subagent_type is not None and not isinstance(subagent_type, str):
        log_warning(
            "session_registry", "register_agent: subagent_type must be a string or None"
        )
        return
    try:
        now = time.time()
        with _open_conn(db_path) as conn:
            conn.execute(
                _SQL_REGISTER_AGENT,
                (agent_id, session_id, role, subagent_type, workspace_root, now, now),
            )
            conn.commit()
    except Exception as e:
        log_warning("session_registry", f"register_agent failed for {agent_id}", e)


def record_action(
    agent_id: str,
    tool_name: str,
    tool_input: Dict[str, Any],
    ts: float,
    db_path: str,
) -> None:
    """Insert a tool-use action for an agent and trim to the last _MAX_AGENT_ACTIONS."""
    if not _require_str(agent_id, "agent_id", "record_action"):
        return
    if not _require_str(tool_name, "tool_name", "record_action"):
        return
    if not isinstance(tool_input, dict):
        log_warning("session_registry", "record_action: tool_input must be a dict")
        return
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        log_warning("session_registry", "record_action: ts must be a numeric value")
        return
    if not _require_str(db_path, "db_path", "record_action"):
        return
    try:
        target = extract_target(tool_name, tool_input)
        if not target:
            target = "-"
        with _open_conn(db_path) as conn:
            conn.execute(_SQL_INSERT_ACTION, (agent_id, tool_name, target, ts))
            conn.execute(_SQL_TRIM_ACTIONS, (agent_id, agent_id, _MAX_AGENT_ACTIONS))
            conn.commit()
    except Exception as e:
        log_warning("session_registry", f"record_action failed for {agent_id}", e)


def mark_agent_ended(agent_id: str, db_path: str) -> None:
    """Set ended_at to now for the given agent. No-op if agent row does not exist."""
    if not _require_str(agent_id, "agent_id", "mark_agent_ended"):
        return
    if not _require_str(db_path, "db_path", "mark_agent_ended"):
        return
    try:
        now = time.time()
        with _open_conn(db_path) as conn:
            conn.execute(_SQL_MARK_ENDED, (now, agent_id))
            conn.commit()
    except Exception as e:
        log_warning("session_registry", f"mark_agent_ended failed for {agent_id}", e)


def purge_agents(db_path: str, now: Optional[float] = None) -> None:
    """Delete stale active agents, expired ended agents, and orphaned actions."""
    if not _require_str(db_path, "db_path", "purge_agents"):
        return
    if not _require_numeric_or_none(now, "now", "purge_agents"):
        return
    if now is None:
        now = time.time()
    try:
        stale_cutoff = now - _AGENT_STALE_CUTOFF_SECONDS
        ended_cutoff = now - _AGENT_ENDED_RETENTION_SECONDS
        with _open_conn(db_path) as conn:
            conn.execute(_SQL_PURGE_STALE_ACTIVE, (stale_cutoff,))
            conn.execute(_SQL_PURGE_ENDED, (ended_cutoff,))
            conn.execute(_SQL_PURGE_ORPHAN_ACTIONS)
            conn.commit()
    except Exception as e:
        log_warning("session_registry", "purge_agents failed", e)


def update_agent_heartbeat(agent_id: str, db_path: str) -> None:
    """Update last_seen to now for the given agent. No-op if agent row does not exist."""
    if not _require_str(agent_id, "agent_id", "update_agent_heartbeat"):
        return
    if not _require_str(db_path, "db_path", "update_agent_heartbeat"):
        return
    try:
        now = time.time()
        with _open_conn(db_path) as conn:
            conn.execute(_SQL_HEARTBEAT_AGENT, (now, agent_id))
            conn.commit()
    except Exception as e:
        log_warning(
            "session_registry", f"update_agent_heartbeat failed for {agent_id}", e
        )


def list_active_tree(db_path: str, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Return a nested agent tree of all non-purged agents.

    Each root agent dict contains a 'subagents' list and an 'actions' list.
    Orphan subagents (root purged) are grouped under a sentinel dict with
    key 'label' = '(parent ended)'. Active roots sort before ended-visible roots.
    Returns [] on invalid input or any DB error.
    """
    if not _require_str(db_path, "db_path", "list_active_tree"):
        return []
    if not _require_numeric_or_none(now, "now", "list_active_tree"):
        return []
    if now is None:
        now = time.time()
    try:
        with _open_conn(db_path) as conn:
            rows = conn.execute(_SQL_LIST_AGENTS).fetchall()
            agents = []
            for row in rows:
                agent = {
                    "agent_id": row[0],
                    "session_id": row[1],
                    "role": row[2],
                    "subagent_type": row[3],
                    "workspace_root": row[4],
                    "start_time": row[5],
                    "last_seen": row[6],
                    "ended_at": row[7],
                }
                status = classify_agent(agent["ended_at"], now)
                if status == "purged":
                    continue
                agent["status"] = status
                action_rows = conn.execute(
                    _SQL_LIST_ACTIONS, (agent["agent_id"],)
                ).fetchall()
                agent["actions"] = [
                    {"tool_name": a[0], "target": a[1], "ts": a[2]} for a in action_rows
                ]
                agents.append(agent)

        roots: Dict[str, Dict[str, Any]] = {}
        orphans: List[Dict[str, Any]] = []
        for agent in agents:
            if agent["role"] == "root":
                agent["subagents"] = []
                roots[agent["session_id"]] = agent
        for agent in agents:
            if agent["role"] != "root":
                parent = roots.get(agent["session_id"])
                if parent is not None:
                    parent["subagents"].append(agent)
                else:
                    orphans.append(agent)

        result = list(roots.values())
        result.sort(
            key=lambda r: (0 if r["status"] == "active" else 1, r["start_time"])
        )
        if orphans:
            result.append({"label": "(parent ended)", "subagents": orphans})
        return result
    except Exception as e:
        log_warning("session_registry", "list_active_tree failed", e)
        return []
