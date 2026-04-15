"""
Session Registry nudge message builders.

Public API:
- build_start_banner(siblings) -> str
    Build a banner warning for SessionStart / SubagentStart hooks.
    Includes: session_id, workspace_root, pid, start_time per sibling.
    Returns "" when siblings is empty.

- build_periodic_reminder(siblings) -> str
    Build a periodic reminder for every 5th PreToolUse hook.
    Includes: session_id, workspace_root per sibling.
    Returns "" when siblings is empty.

- build_danger_bash_warning(siblings, command) -> str
    Build a targeted warning for destructive Bash commands in Stage 2 LLM context.
    Includes: the bash command (in header) and session_id per sibling.
    Returns "" when siblings is empty.
"""

from datetime import datetime
from typing import Any, Dict, List

# ── Section header / footer templates ────────────────────────────────────────
_BANNER_HEADER = (
    "\n\u26a0\ufe0f  SIBLING SESSIONS DETECTED \u2014 CROSS-SESSION AWARENESS\n"
    "The following Claude Code sessions are actively working in the same workspace:\n"
)
_REMINDER_HEADER = (
    "\n\U0001f514  PERIODIC REMINDER \u2014 SIBLING SESSIONS ACTIVE\n"
    "Other Claude Code sessions are still working in the same workspace:\n"
)
_DANGER_HEADER_TMPL = (
    "\n\u26d4  DANGER: DESTRUCTIVE COMMAND WITH ACTIVE SIBLING SESSIONS\n"
    "You are about to run: {command}\n"
    "The following sibling Claude Code sessions are actively working "
    "in the same workspace:\n"
)
_SHARED_FOOTER = (
    "\nIMPORTANT: Any unfamiliar files, branches, or uncommitted changes you encounter "
    "are almost certainly the legitimate work of one of the sibling sessions listed "
    "above. Do NOT treat them as corruption or rogue-agent artifacts. Do NOT run "
    "git checkout --, git reset --hard, git clean, or rm to 'recover' from them."
)
_DANGER_FOOTER = (
    "\nDo NOT proceed with this command \u2014 it may destroy a sibling session's "
    "legitimate work. Coordinate with the sibling session owner before running any "
    "destructive operation."
)

# ── Per-builder sibling row templates ─────────────────────────────────────────
_BANNER_ROW = (
    "  \u2022 session_id={session_id}  pid={pid}  "
    "workspace={workspace_root}  started={start_time_str}\n"
)
_REMINDER_ROW = "  \u2022 session_id={session_id}  workspace={workspace_root}\n"
_DANGER_ROW = "  \u2022 session_id={session_id}\n"


def _format_start_time(start_time: float) -> str:
    """Return a human-readable UTC datetime string for a Unix timestamp.

    Fallback: on conversion failure (OSError, OverflowError, ValueError,
    TypeError), returns str(int(start_time)). This fallback is explicitly
    approved per the test spec (test_single_sibling_includes_start_time
    accepts either form).
    """
    try:
        return datetime.utcfromtimestamp(start_time).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (OSError, OverflowError, ValueError, TypeError):
        return str(int(start_time))


def _validate_siblings(siblings: List[Dict[str, Any]]) -> None:
    """Raise TypeError if siblings is not a list."""
    if not isinstance(siblings, list):
        raise TypeError(f"siblings must be a list, got {type(siblings).__name__!r}")


def build_start_banner(siblings: List[Dict[str, Any]]) -> str:
    """Build a sibling-awareness banner for session or subagent start.

    Includes session_id, workspace_root, pid, and start_time for each sibling.

    Args:
        siblings: List of sibling dicts from list_siblings() — each must have
            session_id, workspace_root, pid, start_time.

    Returns:
        Non-empty warning string when siblings is non-empty; "" otherwise.

    Raises:
        TypeError: if siblings is not a list.
    """
    _validate_siblings(siblings)
    if not siblings:
        return ""
    rows = "".join(
        _BANNER_ROW.format(
            session_id=s["session_id"],
            pid=s["pid"],
            workspace_root=s["workspace_root"],
            start_time_str=_format_start_time(s["start_time"]),
        )
        for s in siblings
    )
    return _BANNER_HEADER + rows + _SHARED_FOOTER


def build_periodic_reminder(siblings: List[Dict[str, Any]]) -> str:
    """Build a periodic sibling reminder for every 5th PreToolUse hook.

    Includes session_id and workspace_root for each sibling.

    Args:
        siblings: List of sibling dicts from list_siblings().

    Returns:
        Non-empty reminder string when siblings is non-empty; "" otherwise.

    Raises:
        TypeError: if siblings is not a list.
    """
    _validate_siblings(siblings)
    if not siblings:
        return ""
    rows = "".join(
        _REMINDER_ROW.format(
            session_id=s["session_id"],
            workspace_root=s["workspace_root"],
        )
        for s in siblings
    )
    return _REMINDER_HEADER + rows + _SHARED_FOOTER


def build_danger_bash_warning(siblings: List[Dict[str, Any]], command: str) -> str:
    """Build a targeted danger warning for destructive Bash commands.

    Injected into Stage 2 LLM validation context when the Bash tool call
    matches a danger_bash rule and sibling sessions are active.
    Includes the bash command (in header) and session_id for each sibling.

    Args:
        siblings: List of sibling dicts from list_siblings().
        command: The exact Bash command being executed.

    Returns:
        Non-empty warning string when siblings is non-empty; "" otherwise.

    Raises:
        TypeError: if siblings is not a list.
    """
    _validate_siblings(siblings)
    if not siblings:
        return ""
    header = _DANGER_HEADER_TMPL.format(command=command)
    rows = "".join(_DANGER_ROW.format(session_id=s["session_id"]) for s in siblings)
    return header + rows + _DANGER_FOOTER
