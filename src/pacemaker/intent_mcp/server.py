"""
declare_intent MCP server (Story #155) -- stdlib-only, newline-delimited
JSON-RPC 2.0 over stdio (the MCP stdio transport).

Handles ``initialize``, ``tools/list``, ``tools/call`` and ``ping``; ignores
notifications. A bad message never ends the session: malformed JSON, a
non-object message, non-object params/arguments and unexpected handler
errors each produce a JSON-RPC error response (and a stderr line) and the
loop carries on.

This server is a PURE ACKNOWLEDGER -- it stores nothing. Pace-maker's hooks do
the storing: they see both ``session_id`` and ``agent_id``, which this process
(spawned by Claude Code per session, with no hook payload) cannot know.

Process-model note (Messi Rule 14): ``serve`` is a stream-driven server loop.
It is bounded by its input -- it returns when the client closes stdin (EOF),
which is how Claude Code ends an MCP stdio session.
"""

import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional, TextIO, Tuple

from .. import __version__
from ..constants import DECLARE_INTENT_TOOL, INTENT_MCP_SERVER_NAME
from ..intent_declarations.fields import (
    REQUIRED_FIELDS,
    intent_declaration_tool_enabled,
    load_user_config,
    missing_required_fields,
)
from ..prompt_provenance import format_tag

DEFAULT_PROTOCOL_VERSION = "2025-06-18"

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts" / "mcp"
_DESCRIPTION_FILE = "declare_intent_tool_description.md"
_DISABLED_FILE = "declare_intent_disabled.md"

# (result, error) -- exactly one is not None. error is (code, message).
Outcome = Tuple[Optional[Dict[str, Any]], Optional[Tuple[int, str]]]


def _load_prompt(name: str) -> str:
    """Read an externalized prompt (Messi Rule 11). A missing file is a broken
    install: raise (at startup for the description, so Claude Code shows the
    server as failed rather than serving a tool with no description)."""
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


def _tool_enabled() -> bool:
    """The ``intent_declaration_tool_enabled`` kill switch, read from the user
    config at CALL time (no restart needed). A missing config means enabled; an
    unreadable/malformed one is reported on stderr and ALSO means the default
    (enabled) -- the hooks fall back the same way, so both sides agree."""
    try:
        return intent_declaration_tool_enabled(load_user_config())
    except (OSError, ValueError, RecursionError) as exc:
        # RecursionError: json.load on a pathologically nested config.json
        # (not a ValueError); the hooks fall back to the defaults, so do we.
        sys.stderr.write(
            f"pace-maker intent server: unreadable config.json ({exc}); "
            "using the default (tool enabled)\n"
        )
        return True


TOOL: Dict[str, Any] = {
    "name": DECLARE_INTENT_TOOL,
    "description": _load_prompt(_DESCRIPTION_FILE),
    "inputSchema": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "File the next Write/Edit targets.",
            },
            "change": {
                "type": "string",
                "description": "What the edit changes.",
            },
            "goal": {
                "type": "string",
                "description": "Why the change is being made.",
            },
            "test_coverage": {
                "type": "string",
                "description": (
                    "For source files: '<test file> - <test name>' "
                    "covering the change."
                ),
            },
        },
        "required": list(REQUIRED_FIELDS),
    },
}


def _text_result(text: str, is_error: bool = False) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "content": [{"type": "text", "text": format_tag(text, "declare_intent_result")}]
    }
    if is_error:
        result["isError"] = True
    return result


def call_tool(params: Dict[str, Any]) -> Outcome:
    """Validate a declare_intent call and acknowledge it (stores nothing)."""
    if params.get("name") != TOOL["name"]:
        return None, (JSONRPC_INVALID_PARAMS, f"unknown tool: {params.get('name')}")
    arguments = params.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return None, (JSONRPC_INVALID_PARAMS, "arguments must be an object")
    if not _tool_enabled():
        # Kill switch (M1 / AC10): the hooks store nothing while it is off, so
        # never claim "Intent recorded". Checked before the field check: the
        # agent should be told the tool is off, not asked to retry it.
        return _text_result(_load_prompt(_DISABLED_FILE), is_error=True), None
    missing = missing_required_fields(arguments)
    if missing:
        return (
            _text_result(
                f"Intent NOT recorded: missing {', '.join(missing)}. "
                "Call declare_intent again with every required field.",
                is_error=True,
            ),
            None,
        )
    return (
        _text_result(
            f"Intent recorded for {arguments['file_path']}. "
            "Make that Write/Edit next."
        ),
        None,
    )


def handle(request: Dict[str, Any]) -> Outcome:
    """Dispatch one JSON-RPC request object."""
    method = request.get("method")
    params = request.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return None, (JSONRPC_INVALID_PARAMS, "params must be an object")
    if method == "initialize":
        return (
            {
                "protocolVersion": params.get(
                    "protocolVersion", DEFAULT_PROTOCOL_VERSION
                ),
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": INTENT_MCP_SERVER_NAME,
                    "version": __version__,
                },
            },
            None,
        )
    if method == "tools/list":
        if _tool_enabled():
            return {"tools": [TOOL]}, None
        # Still listed (stable tool surface) but described as disabled.
        return {"tools": [{**TOOL, "description": _load_prompt(_DISABLED_FILE)}]}, None
    if method == "tools/call":
        return call_tool(params)
    if method == "ping":
        return {}, None
    return None, (JSONRPC_METHOD_NOT_FOUND, f"method not found: {method}")


def _error_response(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def process_line(
    line: str,
    stderr: TextIO,
    dispatch: Callable[[Dict[str, Any]], Outcome] = handle,
) -> Optional[Dict[str, Any]]:
    """Turn one input line into the response dict to send, or ``None`` when
    nothing is to be sent (blank line or notification)."""
    line = line.strip()
    if not line:
        return None
    try:
        request = json.loads(line)
    except (ValueError, RecursionError) as exc:
        stderr.write(f"pace-maker intent server: dropping malformed line: {exc}\n")
        return _error_response(None, JSONRPC_PARSE_ERROR, "parse error")
    if not isinstance(request, dict):
        stderr.write(
            "pace-maker intent server: dropping non-object message: " f"{line[:80]}\n"
        )
        return _error_response(
            None, JSONRPC_INVALID_REQUEST, "request must be a JSON object"
        )
    if "id" not in request:
        return None  # notification (e.g. notifications/initialized): no reply
    request_id = request["id"]
    try:
        result, error = dispatch(request)
    except Exception as exc:  # one bad message must not end the session
        stderr.write(f"pace-maker intent server: internal error: {exc!r}\n")
        return _error_response(
            request_id, JSONRPC_INTERNAL_ERROR, f"internal error: {exc}"
        )
    if error is not None:
        return _error_response(request_id, error[0], error[1])
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    dispatch: Callable[[Dict[str, Any]], Outcome] = handle,
) -> None:
    """Run the request/response loop until ``stdin`` reaches EOF."""
    for line in stdin:
        response = process_line(line, stderr, dispatch)
        if response is None:
            continue
        stdout.write(json.dumps(response) + "\n")
        stdout.flush()
        stderr.flush()


def main() -> None:
    # Tolerate any bytes the client sends; replies are ASCII (json.dumps
    # escapes non-ASCII), so stdout needs no special handling.
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    serve(sys.stdin, sys.stdout, sys.stderr)
