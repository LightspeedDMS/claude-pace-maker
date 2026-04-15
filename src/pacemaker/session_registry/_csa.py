"""
Cross-Session Awareness (CSA) hook integration helpers.

Current public API:
- on_session_start(session_id, source, cwd, pid, db_path, state, config) -> str
    Handle SessionStart hook. Returns banner str (may be empty).

Internal helpers:
- _is_enabled(config) -> bool
- _get_cs(state) -> Optional[dict]
"""

from typing import Any, Dict, List, Optional

from ..logger import log_debug, log_warning

# ── Configuration keys ────────────────────────────────────────────────────────
_CFG_ENABLED = "enabled"
_CFG_CSA_ENABLED = "cross_session_awareness_enabled"

# ── State namespace key ───────────────────────────────────────────────────────
_NS = "cross_session_awareness"

# ── State sub-keys ────────────────────────────────────────────────────────────
_KEY_WORKSPACE = "workspace_root"
_KEY_SEEN = "seen_agent_ids"
_KEY_COUNTER = "tool_use_counter"

# ── Root agent identifier ─────────────────────────────────────────────────────
_AGENT_ROOT = "root"

# ── Source sets ───────────────────────────────────────────────────────────────
_FRESH_SOURCES = frozenset({"startup", "resume"})
_RESET_SOURCES = frozenset({"clear", "compact"})


def _is_enabled(config: Dict[str, Any]) -> bool:
    """Return True when both the master switch and CSA feature flag are on."""
    return config.get(_CFG_ENABLED, True) and config.get(_CFG_CSA_ENABLED, True)


def _get_cs(state: Dict[str, Any], session_id: str) -> Optional[Dict[str, Any]]:
    """Return the per-session sub-dict from state[_NS][session_id], or None."""
    ns = state.get(_NS)
    if not isinstance(ns, dict):
        return None
    return ns.get(session_id)


def on_session_start(
    session_id: str,
    source: str,
    cwd: str,
    pid: int,
    db_path: str,
    state: Dict[str, Any],
    config: Dict[str, Any],
) -> str:
    """Handle SessionStart hook for cross-session awareness.

    source in {startup, resume}: resolve workspace, register, heartbeat,
        list siblings, initialize state namespace, return banner if siblings.
    source in {clear, compact}: reset tool_use_counter to 0, preserve
        seen_agent_ids, run heartbeat, return "".
    Unknown source: log and return "" without touching state or DB.
    Feature disabled: return "" immediately without touching state or DB.

    All registry calls are wrapped in try/except (fail-open).
    """
    if not _is_enabled(config):
        return ""

    if source in _FRESH_SOURCES:
        try:
            from .workspace import resolve_workspace_root
            from . import registry
            from .nudges import build_start_banner

            workspace_root = resolve_workspace_root(cwd)
            # Migrate legacy flat format: if state[_NS] contains bare keys like
            # "workspace_root" it is the old shape — reset to empty dict.
            existing_ns = state.get(_NS)
            if isinstance(existing_ns, dict) and _KEY_WORKSPACE in existing_ns:
                state[_NS] = {}
            elif not isinstance(existing_ns, dict):
                state[_NS] = {}
            state[_NS][session_id] = {
                _KEY_WORKSPACE: workspace_root,
                _KEY_SEEN: [_AGENT_ROOT],
                _KEY_COUNTER: {_AGENT_ROOT: 0},
            }
            siblings: List[Dict[str, Any]] = []
            try:
                registry.register_session(session_id, workspace_root, pid, db_path)
                registry.heartbeat_and_purge(session_id, workspace_root, pid, db_path)
                siblings = registry.list_siblings(workspace_root, session_id, db_path)
            except Exception as e:
                log_warning(
                    "session_registry", f"CSA: registry error on session_start: {e}"
                )
            return build_start_banner(siblings)
        except Exception as e:
            log_warning("session_registry", f"CSA: on_session_start failed: {e}")
            return ""

    if source in _RESET_SOURCES:
        cs = _get_cs(state, session_id)
        if not cs:
            log_debug(
                "session_registry",
                "CSA: clear/compact — cs namespace missing, skipping",
            )
            return ""
        workspace_root = cs.get(_KEY_WORKSPACE)
        if not workspace_root:
            log_debug(
                "session_registry",
                "CSA: clear/compact — workspace_root missing, skipping",
            )
            return ""
        for agent_id in list(cs.get(_KEY_COUNTER, {})):
            cs[_KEY_COUNTER][agent_id] = 0
        try:
            from . import registry

            registry.heartbeat_and_purge(session_id, workspace_root, pid, db_path)
        except Exception as e:
            log_warning("session_registry", f"CSA: heartbeat error on reset: {e}")
        return ""

    log_debug(
        "session_registry", f"CSA: on_session_start unknown source={source!r}, skipping"
    )
    return ""


def on_subagent_start(
    session_id: str,
    agent_id: str,
    pid: int,
    db_path: str,
    state: Dict[str, Any],
    config: Dict[str, Any],
) -> str:
    """Handle SubagentStart hook for cross-session awareness.

    New agent_id: add to seen_agent_ids, initialize tool_use_counter to 0,
        list siblings, return banner if siblings present.
    Already-seen agent_id: return "" (no banner, no state mutation).
    Missing cs namespace: return "" without crash.
    Feature disabled: return "" immediately.
    """
    if not isinstance(state, dict) or not isinstance(config, dict):
        return ""
    if not isinstance(session_id, str) or not session_id:
        return ""
    if not isinstance(agent_id, str) or not agent_id:
        return ""
    if not isinstance(db_path, str) or not db_path:
        return ""
    if not _is_enabled(config):
        return ""

    cs = _get_cs(state, session_id)
    if cs is None:
        log_debug(
            "session_registry",
            "CSA: on_subagent_start — cs namespace missing, skipping",
        )
        return ""

    seen: List[str] = cs.setdefault(_KEY_SEEN, [])
    if agent_id in seen:
        return ""

    seen.append(agent_id)
    counter: Dict[str, int] = cs.setdefault(_KEY_COUNTER, {})
    counter[agent_id] = 0

    workspace_root = cs.get(_KEY_WORKSPACE, "")
    try:
        from . import registry
        from .nudges import build_start_banner

        siblings = registry.list_siblings(workspace_root, session_id, db_path)
        return build_start_banner(siblings)
    except Exception as e:
        log_warning("session_registry", f"CSA: on_subagent_start failed: {e}")
        return ""


def on_heartbeat(
    session_id: str,
    pid: int,
    db_path: str,
    state: Dict[str, Any],
    config: Dict[str, Any],
) -> None:
    """Handle periodic heartbeat: update last_seen in registry.

    Missing cs namespace: return without crash.
    Feature disabled: return None immediately.
    """
    if not isinstance(state, dict) or not isinstance(config, dict):
        return None
    if not isinstance(session_id, str) or not session_id:
        return None
    if not isinstance(db_path, str) or not db_path:
        return None
    if not _is_enabled(config):
        return None

    cs = _get_cs(state, session_id)
    if cs is None:
        log_debug(
            "session_registry", "CSA: on_heartbeat — cs namespace missing, skipping"
        )
        return None

    workspace_root = cs.get(_KEY_WORKSPACE, "")
    try:
        from . import registry

        registry.heartbeat_and_purge(session_id, workspace_root, pid, db_path)
    except Exception as e:
        log_warning("session_registry", f"CSA: on_heartbeat failed: {e}")
    return None


# ── Periodic reminder interval ────────────────────────────────────────────────
_PERIODIC_INTERVAL = 5


def on_pre_tool_use(
    session_id: str,
    agent_id: str,
    pid: int,
    tool_name: str,
    command: Optional[str],
    db_path: str,
    state: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """Handle PreToolUse hook for cross-session awareness.

    Increments tool_use_counter for agent_id.
    Every _PERIODIC_INTERVAL calls with siblings: add periodic_reminder to result.
    Bash tool with danger command and siblings: add danger_bash_warning to result.
    Sibling lookup is guarded — only performed when counter hits interval or
        tool is Bash with a non-empty command.
    Missing cs namespace: return {} without crash.
    Feature disabled: return {} without mutating state.
    """
    if not isinstance(state, dict) or not isinstance(config, dict):
        return {}
    if not isinstance(session_id, str) or not session_id:
        return {}
    if not isinstance(agent_id, str) or not agent_id:
        return {}
    if not isinstance(db_path, str) or not db_path:
        return {}
    if not isinstance(tool_name, str):
        return {}
    if command is not None and not isinstance(command, str):
        return {}
    if not _is_enabled(config):
        return {}

    cs = _get_cs(state, session_id)
    if cs is None:
        log_debug(
            "session_registry", "CSA: on_pre_tool_use — cs namespace missing, skipping"
        )
        return {}

    counter: Dict[str, int] = cs.setdefault(_KEY_COUNTER, {})
    counter[agent_id] = counter.get(agent_id, 0) + 1
    current_count = counter[agent_id]

    is_periodic = current_count % _PERIODIC_INTERVAL == 0
    is_danger_bash = tool_name == "Bash" and bool(command)
    need_siblings = is_periodic or is_danger_bash
    if not need_siblings:
        return {}

    workspace_root = cs.get(_KEY_WORKSPACE, "")
    result: Dict[str, Any] = {}
    try:
        from . import registry
        from .nudges import build_periodic_reminder, build_danger_bash_warning

        siblings = registry.list_siblings(workspace_root, session_id, db_path)

        if is_periodic and siblings:
            result["periodic_reminder"] = build_periodic_reminder(siblings)

        if is_danger_bash and siblings:
            from ..danger_bash_rules import load_rules, match_command
            from ..constants import DEFAULT_DANGER_RULES_PATH

            rules = load_rules(DEFAULT_DANGER_RULES_PATH)
            if match_command(command, rules):
                result["danger_bash_warning"] = build_danger_bash_warning(
                    siblings, command
                )
    except Exception as e:
        log_warning("session_registry", f"CSA: on_pre_tool_use failed: {e}")

    return result


def on_session_end(
    session_id: str,
    pid: int,
    db_path: str,
    state: Dict[str, Any],
    config: Dict[str, Any],
) -> None:
    """Handle session stop: remove session from registry.

    Missing cs namespace: return without crash.
    Feature disabled: return None immediately.
    """
    if not isinstance(state, dict) or not isinstance(config, dict):
        return None
    if not isinstance(session_id, str) or not session_id:
        return None
    if not isinstance(db_path, str) or not db_path:
        return None
    if not _is_enabled(config):
        return None

    cs = _get_cs(state, session_id)
    if cs is None:
        log_debug(
            "session_registry", "CSA: on_session_end — cs namespace missing, skipping"
        )
        return None

    try:
        from . import registry

        registry.unregister_session(session_id, db_path)
    except Exception as e:
        log_warning("session_registry", f"CSA: on_session_end failed: {e}")

    # GC: remove this session's sub-dict so state[_NS] does not grow unbounded.
    ns = state.get(_NS)
    if isinstance(ns, dict):
        ns.pop(session_id, None)
    return None
