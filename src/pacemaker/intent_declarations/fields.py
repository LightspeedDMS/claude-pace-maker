"""
Shared declare_intent field rules (Story #155).

STDLIB-ONLY leaf module: imported by BOTH the MCP server process
(``pacemaker.intent_mcp.server``) and the hook-side recorder/gate, so the
"which arguments count as a complete declaration" rule exists exactly once.
A declaration the server would reject must never be stored by the recorder,
and vice versa.
"""

import json
import os
from typing import Any, Dict, List, Optional

from ..constants import DECLARE_INTENT_TOOL_NAMES

REQUIRED_FIELDS = ("file_path", "change", "goal")

CONFIG_KEY = "intent_declaration_tool_enabled"


def intent_declaration_tool_enabled(config: Dict[str, Any]) -> bool:
    """Kill switch. Absent key = enabled (the shipped default); any value
    other than a real ``True`` (``false``, ``null``, ``"false"`` ...) turns
    the whole tool path off, restoring pre-#155 behaviour exactly. Shared by
    the hooks (via ``gate``) and the MCP server so both read it the same way."""
    return config.get(CONFIG_KEY, True) is True


def load_user_config() -> Dict[str, Any]:
    """Read ``~/.claude-pace-maker/config.json`` (stdlib json; the same file
    the hooks read -- the home directory is resolved at call time). A missing
    file is ``{}`` (defaults apply).

    Raises:
        OSError / ValueError: the file exists but is unreadable, is not valid
            JSON, or is not a JSON object -- the caller decides (the MCP
            server reports it on stderr and falls back to the defaults).
    """
    path = os.path.join(os.path.expanduser("~"), ".claude-pace-maker", "config.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("config.json is not a JSON object")
    return data


_TRUNCATION_MARK = "…"


def truncate_text(value: str, limit: int) -> str:
    """Cap ``value`` at ``limit`` characters; an over-long value is cut to
    ``limit`` total, ending in an ellipsis so the cut is visible."""
    if len(value) <= limit:
        return value
    return value[: limit - len(_TRUNCATION_MARK)] + _TRUNCATION_MARK


def declare_inputs_from_tools(tools: Any) -> List[Dict[str, Any]]:
    """The ``input`` dicts of every ``declare_intent`` tool_use in an
    anchored turn's merged tool list, in turn order (garbage-tolerant).
    Feeds ``gate.same_message_declared_intent`` via transcript_reader's
    additive ``anchor_declare_intents`` diagnostic. Lives in this stdlib
    leaf (not the gate) so transcript_reader never imports the store/sqlite
    wiring."""
    if not isinstance(tools, list):
        return []
    return [
        tool["input"]
        for tool in tools
        if isinstance(tool, dict)
        and tool.get("name") in DECLARE_INTENT_TOOL_NAMES
        and isinstance(tool.get("input"), dict)
    ]


def missing_required_fields(arguments: Any) -> List[str]:
    """Return the required fields that are absent, ``None``, non-string or
    blank. A non-dict ``arguments`` reports every required field missing."""
    if not isinstance(arguments, dict):
        return list(REQUIRED_FIELDS)
    missing = []
    for name in REQUIRED_FIELDS:
        value = arguments.get(name)
        if not isinstance(value, str) or not value.strip():
            missing.append(name)
    return missing


def normalize_file_path(file_path: str, cwd: Optional[str]) -> str:
    """Normalize ``file_path`` to an absolute real path.

    A relative path is resolved against ``cwd`` (the hook payload's cwd);
    when the payload carries none, against the hook process's own working
    directory (Claude Code runs hooks in the session's cwd). Applied at BOTH
    record time and match time, so a declaration for ``src/a.py`` matches a
    Write whose tool input says ``/work/src/a.py``.

    Raises:
        ValueError: ``file_path`` is empty -- an empty path can never be
            matched, so refusing it loudly beats storing an unmatchable row.
    """
    if not file_path:
        raise ValueError("file_path must be a non-empty string")
    if not os.path.isabs(file_path):
        file_path = os.path.join(cwd or os.getcwd(), file_path)
    return os.path.realpath(file_path)
