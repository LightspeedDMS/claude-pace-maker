"""
Allow-listing of the declare_intent tool in a Claude Code settings file
(Story #155 review M3).

Without a ``permissions.allow`` rule, an interactive or ``-p`` session that is
not in bypass mode gets a permission PROMPT for ``mcp__pace-maker__
declare_intent`` (a subagent gets a denial), and every edit silently falls back
to the transcript path. ``install.sh`` therefore adds the rule to the same
settings file it registers the hooks in; ``migrate-to-plugin.sh`` removes it.

Both run this module FROM THE INSTALLED SNAPSHOT, like ``registration``:
``python -m pacemaker.intent_mcp.permissions add|remove --settings-file F``.
The merge logic takes a file PATH, so it is tested on tmp files only.

A plugin cannot ship this permission (verified against Claude Code 2.1.286: a
plugin ``settings.json`` / a ``permissions`` key in ``plugin.json`` is
ignored); plugin users add ``mcp__plugin_claude-pace-maker_pace-maker__
declare_intent`` to their own settings -- see CLAUDE.md, "Story #155".

Safety: the file is read with stdlib json and rewritten atomically (temp file
in the same directory + ``os.replace``, mode preserved). Anything that is not
the expected shape (invalid JSON, a non-object document, ``permissions`` not an
object, ``allow`` not a list) is REFUSED with ``PermissionsError`` and the file
is left byte-for-byte untouched -- never "repaired" by overwriting.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from typing import Any, Dict, Optional, Sequence

from ..constants import DECLARE_INTENT_TOOL, INTENT_MCP_SERVER_NAME

# The user-scope registration's tool name (what install.sh registers).
ALLOW_RULE = f"mcp__{INTENT_MCP_SERVER_NAME}__{DECLARE_INTENT_TOOL}"


class PermissionsError(RuntimeError):
    """The settings file is not in a shape this helper may safely edit."""


def _load(path: str) -> Dict[str, Any]:
    """The settings document (``{}`` for a missing or whitespace-only file)."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise PermissionsError(
            f"settings file {path} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise PermissionsError(f"settings file {path} is not a JSON object")
    return data


def _allow_list(data: Dict[str, Any], path: str, create: bool) -> Optional[list]:
    """``data["permissions"]["allow"]``, validated; ``None`` when absent and
    ``create`` is false. With ``create`` the containers are created."""
    permissions = data.get("permissions")
    if permissions is None:
        if not create:
            return None
        permissions = data["permissions"] = {}
    if not isinstance(permissions, dict):
        raise PermissionsError(f"{path}: 'permissions' is not an object")
    allow = permissions.get("allow")
    if allow is None:
        if not create:
            return None
        allow = permissions["allow"] = []
    if not isinstance(allow, list):
        raise PermissionsError(f"{path}: 'permissions.allow' is not a list")
    return allow


def _write_atomic(path: str, data: Dict[str, Any]) -> None:
    """Replace the settings file atomically. A symlinked settings file (e.g.
    ``settings.json -> real.json`` from a dotfiles manager) is written THROUGH
    the link: the temp file goes in the TARGET's directory and replaces the
    target, so the link survives and the target receives the change. Mode is
    preserved; non-ASCII content is written as UTF-8, not ``\\u`` escapes."""
    target = os.path.realpath(path)
    directory = os.path.dirname(target)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".settings.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        if os.path.exists(target):
            shutil.copymode(target, tmp)
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def add_allow_rule(settings_path: str, rule: str = ALLOW_RULE) -> bool:
    """Ensure ``rule`` is in ``permissions.allow`` (existing entries and every
    other key preserved, no duplicates). Returns True iff the file changed;
    a rule already present leaves the file untouched (not even rewritten)."""
    data = _load(settings_path)
    allow = _allow_list(data, settings_path, create=False)
    if allow is not None and rule in allow:
        return False
    allow = _allow_list(data, settings_path, create=True)
    assert allow is not None  # create=True always returns the list
    allow.append(rule)
    _write_atomic(settings_path, data)
    return True


def remove_allow_rule(settings_path: str, rule: str = ALLOW_RULE) -> bool:
    """Remove every occurrence of ``rule`` from ``permissions.allow``; an
    ``allow`` list / ``permissions`` object that this EMPTIES is pruned (so add
    then remove restores the original document). Returns True iff the file
    changed; an absent file or rule is a no-op."""
    if not os.path.exists(settings_path):
        return False
    data = _load(settings_path)
    allow = _allow_list(data, settings_path, create=False)
    if allow is None or rule not in allow:
        return False
    allow[:] = [entry for entry in allow if entry != rule]
    if not allow:
        del data["permissions"]["allow"]
        if not data["permissions"]:
            del data["permissions"]
    _write_atomic(settings_path, data)
    return True


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="pacemaker.intent_mcp.permissions")
    parser.add_argument("action", choices=("add", "remove"))
    parser.add_argument("--settings-file", required=True)
    parser.add_argument("--rule", default=ALLOW_RULE)
    args = parser.parse_args(argv)
    try:
        if args.action == "add":
            changed = add_allow_rule(args.settings_file, args.rule)
            print(
                f"permission '{args.rule}' "
                + ("added to" if changed else "already present in")
                + f" {args.settings_file}"
            )
        else:
            changed = remove_allow_rule(args.settings_file, args.rule)
            print(
                f"permission '{args.rule}' "
                + ("removed from" if changed else "not present in")
                + f" {args.settings_file}"
            )
    except (PermissionsError, OSError) as exc:
        print(
            f"declare_intent permission update failed (settings): {exc}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
