"""
Story #155 AC1 -- the declare_intent MCP server, driven as a REAL process over
stdio (newline-delimited JSON-RPC 2.0), exactly the way Claude Code drives it.

No mocks: every test below spawns ``python -m pacemaker.intent_mcp`` (stdlib
only, imported from this checkout's src/) and talks to it through pipes. The
single in-process test injects a failing dispatcher through the server's own
documented seam to prove -32603 handling, since no valid wire message can make
the real dispatcher raise.
"""

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
DESCRIPTION_FILE = (
    SRC_DIR / "pacemaker" / "prompts" / "mcp" / "declare_intent_tool_description.md"
)

TAG_HEADER = "[pace-maker · declare_intent_result]\n"


def _spawn(home=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_DIR)
    env["PYTHONSAFEPATH"] = "1"
    if home is not None:
        env["HOME"] = str(home)
    return subprocess.Popen(
        [sys.executable, "-m", "pacemaker.intent_mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(REPO_ROOT.parent),  # NOT the repo: no cwd shadowing needed
    )


def _exchange(lines, expected_responses=None, timeout=20, home=None):
    """Send raw lines, close stdin, return (parsed responses, stderr, rc)."""
    proc = _spawn(home)
    try:
        payload = "".join(line.rstrip("\n") + "\n" for line in lines)
        out, err = proc.communicate(payload, timeout=timeout)
    finally:
        if proc.poll() is None:
            proc.kill()
    responses = [json.loads(line) for line in out.splitlines() if line.strip()]
    if expected_responses is not None:
        assert len(responses) == expected_responses, (responses, err)
    return responses, err, proc.returncode


def _call(arguments, request_id=1, name="declare_intent"):
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )


class TestHandshake:
    def test_initialize_echoes_protocol_version_and_names_server(self):
        (resp,), _, rc = _exchange(
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-03-26",
                            "capabilities": {},
                            "clientInfo": {"name": "t", "version": "1"},
                        },
                    }
                )
            ],
            expected_responses=1,
        )
        assert rc == 0
        assert resp["id"] == 1
        assert resp["result"]["protocolVersion"] == "2025-03-26"
        assert resp["result"]["serverInfo"]["name"] == "pace-maker"
        assert "tools" in resp["result"]["capabilities"]

    def test_initialize_without_protocol_version_gets_a_default(self):
        (resp,), _, _ = _exchange(
            [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"})],
            expected_responses=1,
        )
        assert isinstance(resp["result"]["protocolVersion"], str)
        assert resp["result"]["protocolVersion"]

    def test_notifications_get_no_response(self):
        responses, _, rc = _exchange(
            [
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}),
            ]
        )
        assert rc == 0
        assert len(responses) == 1
        assert responses[0] == {"jsonrpc": "2.0", "id": 2, "result": {}}

    def test_ping(self):
        (resp,), _, _ = _exchange(
            [json.dumps({"jsonrpc": "2.0", "id": "p", "method": "ping"})],
            expected_responses=1,
        )
        assert resp == {"jsonrpc": "2.0", "id": "p", "result": {}}


class TestToolsList:
    def test_lists_exactly_the_declare_intent_tool(self):
        (resp,), _, _ = _exchange(
            [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})],
            expected_responses=1,
        )
        (tool,) = resp["result"]["tools"]
        assert tool["name"] == "declare_intent"
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert set(schema["properties"]) == {
            "file_path",
            "change",
            "goal",
            "test_coverage",
        }
        assert schema["required"] == ["file_path", "change", "goal"]

    def test_description_comes_from_the_externalized_prompt_file(self):
        (resp,), _, _ = _exchange(
            [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})],
            expected_responses=1,
        )
        (tool,) = resp["result"]["tools"]
        assert DESCRIPTION_FILE.is_file()
        assert tool["description"] == DESCRIPTION_FILE.read_text().strip()
        assert "declare" in tool["description"].lower()

    def test_description_mentions_chain_rule_and_test_coverage_format(self):
        text = DESCRIPTION_FILE.read_text()
        assert "<test file> - <test name>" in text
        assert "same file" in text


class TestToolsCall:
    def test_valid_call_is_acknowledged_with_provenance_tag(self):
        (resp,), _, _ = _exchange(
            [
                _call(
                    {
                        "file_path": "/w/a.py",
                        "change": "add f",
                        "goal": "fix bug",
                        "test_coverage": "tests/test_a.py - test_f",
                    }
                )
            ],
            expected_responses=1,
        )
        result = resp["result"]
        assert "isError" not in result or result["isError"] is False
        (block,) = result["content"]
        assert block["type"] == "text"
        assert block["text"] == (
            TAG_HEADER + "Intent recorded for /w/a.py. Make that Write/Edit next."
        )

    def test_test_coverage_is_optional(self):
        (resp,), _, _ = _exchange(
            [_call({"file_path": "/w/a.md", "change": "c", "goal": "g"})],
            expected_responses=1,
        )
        assert not resp["result"].get("isError")

    @pytest.mark.parametrize(
        "arguments, missing",
        [
            ({"change": "c", "goal": "g"}, "file_path"),
            ({"file_path": None, "change": "c", "goal": "g"}, "file_path"),
            ({"file_path": "/w/a.py", "change": "   ", "goal": "g"}, "change"),
            ({"file_path": "/w/a.py", "change": "c", "goal": ""}, "goal"),
            ({}, "file_path, change, goal"),
            (None, "file_path, change, goal"),
        ],
    )
    def test_missing_null_or_blank_required_fields_are_an_error_result(
        self, arguments, missing
    ):
        (resp,), _, _ = _exchange([_call(arguments)], expected_responses=1)
        result = resp["result"]
        assert result["isError"] is True
        text = result["content"][0]["text"]
        assert text.startswith(TAG_HEADER + "Intent NOT recorded: missing " + missing)

    def test_unknown_tool_is_invalid_params(self):
        (resp,), _, _ = _exchange(
            [_call({"file_path": "x"}, name="nope")], expected_responses=1
        )
        assert resp["error"]["code"] == -32602

    def test_non_object_arguments_is_invalid_params(self):
        (resp,), _, _ = _exchange(
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": "declare_intent", "arguments": [1, 2]},
                    }
                )
            ],
            expected_responses=1,
        )
        assert resp["error"]["code"] == -32602

    def test_non_object_params_is_invalid_params(self):
        (resp,), _, _ = _exchange(
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": "nope",
                    }
                )
            ],
            expected_responses=1,
        )
        assert resp["error"]["code"] == -32602

    def test_unknown_method_is_method_not_found(self):
        (resp,), _, _ = _exchange(
            [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})],
            expected_responses=1,
        )
        assert resp["error"]["code"] == -32601

    def test_server_stores_nothing(self, tmp_path):
        """The server is a pure acknowledger -- hooks do the storing."""
        env_db = tmp_path / "must_not_exist.db"
        proc_env = dict(os.environ)
        proc_env["PYTHONPATH"] = str(SRC_DIR)
        proc_env["PACEMAKER_INTENT_DECLARATIONS_PATH"] = str(env_db)
        proc = subprocess.run(
            [sys.executable, "-m", "pacemaker.intent_mcp"],
            input=_call({"file_path": "/w/a.py", "change": "c", "goal": "g"}) + "\n",
            capture_output=True,
            text=True,
            env=proc_env,
            cwd=str(tmp_path),
            timeout=20,
        )
        assert proc.returncode == 0
        assert not env_db.exists()


class TestSurvivesMalformedInput:
    def test_garbage_line_gets_parse_error_and_server_keeps_going(self):
        responses, err, rc = _exchange(
            [
                "this is not json",
                json.dumps({"jsonrpc": "2.0", "id": 5, "method": "ping"}),
            ]
        )
        assert rc == 0
        assert responses[0]["error"]["code"] == -32700
        assert responses[0]["id"] is None
        assert responses[1] == {"jsonrpc": "2.0", "id": 5, "result": {}}
        assert "malformed" in err.lower()

    @pytest.mark.parametrize("raw", ["[1, 2, 3]", "42", '"str"', "null", "true"])
    def test_non_object_json_gets_invalid_request(self, raw):
        responses, _, rc = _exchange(
            [raw, json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"})]
        )
        assert rc == 0
        assert responses[0]["error"]["code"] == -32600
        assert responses[0]["id"] is None
        assert responses[1]["id"] == 9

    def test_blank_lines_are_ignored(self):
        responses, _, rc = _exchange(
            ["", "   ", json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping"})]
        )
        assert rc == 0
        assert len(responses) == 1

    def test_pathologically_deep_json_does_not_crash_the_server(self):
        deep = "[" * 100000 + "]" * 100000
        responses, _, rc = _exchange(
            [deep, json.dumps({"jsonrpc": "2.0", "id": 4, "method": "ping"})]
        )
        assert rc == 0
        assert responses[-1] == {"jsonrpc": "2.0", "id": 4, "result": {}}

    def test_eof_exits_cleanly(self):
        responses, _, rc = _exchange([])
        assert responses == []
        assert rc == 0


class TestInternalErrors:
    def test_unexpected_dispatch_exception_is_minus_32603_and_loop_survives(self):
        from pacemaker.intent_mcp import server

        calls = []

        def exploding(request):
            calls.append(request["method"])
            if request["method"] == "boom":
                raise RuntimeError("kaboom")
            return {}, None

        stdin = io.StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "boom"})
            + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"})
            + "\n"
        )
        stdout, stderr = io.StringIO(), io.StringIO()
        server.serve(stdin, stdout, stderr, dispatch=exploding)
        first, second = [json.loads(line) for line in stdout.getvalue().splitlines()]
        assert first["id"] == 1
        assert first["error"]["code"] == -32603
        assert "kaboom" in first["error"]["message"]
        assert second == {"jsonrpc": "2.0", "id": 2, "result": {}}
        assert "internal error" in stderr.getvalue().lower()
        assert calls == ["boom", "ping"]


DISABLED_NOTICE = (
    "declare_intent is disabled — write the INTENT: line in your response instead."
)
VALID_CALL = {"file_path": "/w/a.py", "change": "c", "goal": "g"}


def _home_with_config(tmp_path, content):
    """A throwaway HOME whose ~/.claude-pace-maker/config.json is ``content``
    (a str written verbatim, a dict dumped as JSON, or None for no file)."""
    home = tmp_path / "home"
    (home / ".claude-pace-maker").mkdir(parents=True)
    if content is not None:
        text = content if isinstance(content, str) else json.dumps(content)
        (home / ".claude-pace-maker" / "config.json").write_text(text)
    return home


class TestKillSwitchHonouredByTheServer:
    """M1 / AC10: with ``intent_declaration_tool_enabled`` false the tool must
    not claim "recorded" (the hooks store nothing) nor stay "preferred"."""

    def test_disabled_call_is_an_error_result_with_the_notice(self, tmp_path):
        home = _home_with_config(tmp_path, {"intent_declaration_tool_enabled": False})
        (resp,), _, _ = _exchange([_call(VALID_CALL)], expected_responses=1, home=home)
        result = resp["result"]
        assert result["isError"] is True
        assert result["content"][0]["text"] == TAG_HEADER + DISABLED_NOTICE
        assert "Intent recorded" not in result["content"][0]["text"]

    @pytest.mark.parametrize("value", [None, 0, "false", "no", []])
    def test_anything_but_true_disables_like_the_hooks_do(self, tmp_path, value):
        home = _home_with_config(tmp_path, {"intent_declaration_tool_enabled": value})
        (resp,), _, _ = _exchange([_call(VALID_CALL)], expected_responses=1, home=home)
        assert resp["result"]["isError"] is True

    @pytest.mark.parametrize(
        "content",
        [
            {"intent_declaration_tool_enabled": True},
            {"enabled": True},  # key absent -> shipped default (on)
            {},
            None,  # no config file at all
            "{not json",  # unreadable/malformed -> default
            "[1, 2]",  # valid JSON but not an object -> default
            "",
        ],
    )
    def test_enabled_or_unreadable_config_means_the_default_enabled(
        self, tmp_path, content
    ):
        home = _home_with_config(tmp_path, content)
        (resp,), _, _ = _exchange([_call(VALID_CALL)], expected_responses=1, home=home)
        assert not resp["result"].get("isError")
        assert "Intent recorded for /w/a.py" in resp["result"]["content"][0]["text"]

    def test_pathologically_nested_config_falls_back_to_enabled(self, tmp_path):
        """json.load raises RecursionError (not a ValueError) on this input;
        the hooks fall back to the defaults, so must the server -- not -32603."""
        home = _home_with_config(tmp_path, "[" * 200000 + "]" * 200000)
        (resp,), err, _ = _exchange(
            [_call(VALID_CALL)], expected_responses=1, home=home
        )
        assert "error" not in resp
        assert not resp["result"].get("isError")
        assert "Intent recorded for /w/a.py" in resp["result"]["content"][0]["text"]
        assert "config" in err.lower()

    def test_pathologically_nested_config_does_not_break_tools_list(self, tmp_path):
        home = _home_with_config(tmp_path, "[" * 200000 + "]" * 200000)
        (resp,), _, _ = _exchange(
            [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})],
            expected_responses=1,
            home=home,
        )
        assert resp["result"]["tools"][0]["description"] == (
            DESCRIPTION_FILE.read_text().strip()
        )

    def test_malformed_config_is_reported_on_stderr_not_swallowed(self, tmp_path):
        home = _home_with_config(tmp_path, "{not json")
        _, err, _ = _exchange([_call(VALID_CALL)], home=home)
        assert "config" in err.lower()

    def test_missing_fields_still_win_over_the_disabled_notice_order(self, tmp_path):
        """Disabled is checked first: nothing is recorded either way, and the
        agent should be told the tool is off, not asked to retry it."""
        home = _home_with_config(tmp_path, {"intent_declaration_tool_enabled": False})
        (resp,), _, _ = _exchange([_call({})], expected_responses=1, home=home)
        assert resp["result"]["content"][0]["text"] == TAG_HEADER + DISABLED_NOTICE

    def test_tools_list_description_says_disabled_when_off(self, tmp_path):
        home = _home_with_config(tmp_path, {"intent_declaration_tool_enabled": False})
        (resp,), _, _ = _exchange(
            [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})],
            expected_responses=1,
            home=home,
        )
        (tool,) = resp["result"]["tools"]
        assert tool["description"] == DISABLED_NOTICE
        assert tool["name"] == "declare_intent"  # still listed: stable surface

    def test_tools_list_description_is_the_normal_one_when_on(self, tmp_path):
        home = _home_with_config(tmp_path, {"intent_declaration_tool_enabled": True})
        (resp,), _, _ = _exchange(
            [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})],
            expected_responses=1,
            home=home,
        )
        assert resp["result"]["tools"][0]["description"] == (
            DESCRIPTION_FILE.read_text().strip()
        )

    def test_switch_is_read_at_call_time_without_restarting_the_server(self, tmp_path):
        home = _home_with_config(tmp_path, {"intent_declaration_tool_enabled": True})
        config = home / ".claude-pace-maker" / "config.json"
        proc = _spawn(home)
        try:

            def roundtrip(request_id):
                proc.stdin.write(_call(VALID_CALL, request_id=request_id) + "\n")
                proc.stdin.flush()
                return json.loads(proc.stdout.readline())

            assert not roundtrip(1)["result"].get("isError")
            config.write_text(json.dumps({"intent_declaration_tool_enabled": False}))
            assert roundtrip(2)["result"]["isError"] is True
            config.write_text(json.dumps({"intent_declaration_tool_enabled": True}))
            assert not roundtrip(3)["result"].get("isError")
        finally:
            proc.stdin.close()
            proc.wait(timeout=20)

    def test_notice_text_is_externalized(self):
        notice = (
            SRC_DIR / "pacemaker" / "prompts" / "mcp" / "declare_intent_disabled.md"
        )
        assert notice.read_text().strip() == DISABLED_NOTICE


class TestProvenanceChannelDeclared:
    def test_declare_intent_result_is_a_declared_channel(self):
        from pacemaker import prompt_provenance as pp

        assert "declare_intent_result" in pp.CHANNELS
        assert pp.format_tag("x", "declare_intent_result").startswith(TAG_HEADER)

    def test_session_start_manifest_enumerates_the_channel(self):
        from pacemaker import prompt_provenance as pp

        assert "declare_intent_result" in pp.session_start_manifest()
        assert "declare_intent_result" in pp.subagent_start_manifest()
