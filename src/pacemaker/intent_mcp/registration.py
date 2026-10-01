"""
User-scope registration of the declare_intent MCP server (Story #155).

``install.sh`` runs this module FROM THE INSTALLED SNAPSHOT
(``PYTHONPATH=~/.claude/hooks PYTHONSAFEPATH=1 <hook python> -m
pacemaker.intent_mcp.registration add ...``) so the registration command
and the server it points at both come from the snapshot -- never the Dev
tree (issue #146 rules). ``migrate-to-plugin.sh`` runs the ``remove`` form,
because the plugin declares the same server itself (``.mcp.json``).

The registered command is ``<hook python> -m pacemaker.intent_mcp`` with
``PYTHONPATH=<snapshot dir>`` and ``PYTHONSAFEPATH=1`` in its environment.

Idempotent by remove-then-add: ``claude mcp add`` on an existing name FAILS
(verified against the real CLI), so any previous registration is removed
first. ``claude mcp remove`` of a missing name also fails, with "No MCP server
named ..." -- that one message is the only remove failure treated as success.
Everything else is surfaced (Messi Rule 13), and install.sh turns it into a
warning, never an aborted install: without the tool the transcript path
still validates every edit.
"""

import argparse
import os
import subprocess
import sys
from typing import List, Optional, Sequence

from ..constants import INTENT_MCP_SERVER_NAME

DEFAULT_CLAUDE_BIN = "claude"
DEFAULT_TIMEOUT_SECONDS = 60
SCOPE = "user"
SERVER_MODULE = "pacemaker.intent_mcp"

_NOT_REGISTERED_MARKER = "No MCP server named"


class RegistrationError(RuntimeError):
    """A registration/unregistration step failed (message names the step)."""


def build_add_argv(
    python_path: str, snapshot_dir: str, claude_bin: str = DEFAULT_CLAUDE_BIN
) -> List[str]:
    """The ``claude mcp add`` argv. The server name comes BEFORE ``-e``:
    ``-e`` is variadic in the real CLI and would otherwise swallow the name
    as one more environment value."""
    return [
        claude_bin,
        "mcp",
        "add",
        "--scope",
        SCOPE,
        INTENT_MCP_SERVER_NAME,
        "-e",
        f"PYTHONPATH={snapshot_dir}",
        "-e",
        "PYTHONSAFEPATH=1",
        "--",
        python_path,
        "-m",
        SERVER_MODULE,
    ]


def build_remove_argv(claude_bin: str = DEFAULT_CLAUDE_BIN) -> List[str]:
    return [claude_bin, "mcp", "remove", "--scope", SCOPE, INTENT_MCP_SERVER_NAME]


def _run(
    argv: Sequence[str], timeout_seconds: float
) -> "subprocess.CompletedProcess[str]":
    try:
        return subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise RegistrationError(
            f"claude CLI not found ({argv[0]}): is it on PATH? {exc}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RegistrationError(
            f"`{' '.join(argv)}` timed out after {timeout_seconds}s"
        ) from exc
    except OSError as exc:
        raise RegistrationError(f"could not run {argv[0]}: {exc}") from exc


def _output(result: "subprocess.CompletedProcess[str]") -> str:
    return " ".join(
        part.strip() for part in (result.stderr, result.stdout) if part.strip()
    )


def _failure(
    argv: Sequence[str], result: "subprocess.CompletedProcess[str]"
) -> RegistrationError:
    return RegistrationError(
        f"`{' '.join(argv)}` failed (exit {result.returncode}): {_output(result)}"
    )


def _remove_existing(claude_bin: str, timeout_seconds: float) -> None:
    argv = build_remove_argv(claude_bin)
    result = _run(argv, timeout_seconds)
    if result.returncode == 0:
        return
    if _NOT_REGISTERED_MARKER in _output(result):
        return  # nothing registered yet: exactly the state we want
    raise _failure(argv, result)


def register(
    python_path: str,
    snapshot_dir: str,
    claude_bin: str = DEFAULT_CLAUDE_BIN,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """(Re-)register the server at user scope, pointing at the snapshot.

    Raises:
        RegistrationError: bad inputs (relative interpreter path, snapshot
            without a ``pacemaker/`` package -- registering a server that
            cannot import would be worse than none) or a failing CLI step.
    """
    if not os.path.isabs(python_path):
        raise RegistrationError(
            f"python path must be absolute so the registration does not depend "
            f"on PATH at launch time: {python_path!r}"
        )
    if not os.path.isfile(os.path.join(snapshot_dir, "pacemaker", "__init__.py")):
        raise RegistrationError(
            f"installed snapshot not found: {snapshot_dir}/pacemaker/__init__.py "
            "is missing (run install.sh to deploy the hook modules first)"
        )
    _remove_existing(claude_bin, timeout_seconds)
    argv = build_add_argv(python_path, snapshot_dir, claude_bin)
    result = _run(argv, timeout_seconds)
    if result.returncode != 0:
        raise _failure(argv, result)


def unregister(
    claude_bin: str = DEFAULT_CLAUDE_BIN,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """Remove the user-scope registration (absent is fine)."""
    _remove_existing(claude_bin, timeout_seconds)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="pacemaker.intent_mcp.registration")
    sub = parser.add_subparsers(dest="action", required=True)
    add = sub.add_parser("add", help="register the declare_intent server")
    add.add_argument("--python", required=True, help="absolute interpreter path")
    add.add_argument("--snapshot-dir", required=True, help="dir holding pacemaker/")
    remove = sub.add_parser("remove", help="remove the registration")
    for command in (add, remove):
        command.add_argument("--claude-bin", default=DEFAULT_CLAUDE_BIN)
        command.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    try:
        if args.action == "add":
            register(args.python, args.snapshot_dir, args.claude_bin, args.timeout)
            print(
                f"declare_intent MCP server registered at {SCOPE} scope as "
                f"'{INTENT_MCP_SERVER_NAME}' (snapshot: {args.snapshot_dir})"
            )
        else:
            unregister(args.claude_bin, args.timeout)
            print(
                f"declare_intent MCP server '{INTENT_MCP_SERVER_NAME}' removed "
                f"from {SCOPE} scope (or was not registered)"
            )
    except RegistrationError as exc:
        print(f"declare_intent MCP registration failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
