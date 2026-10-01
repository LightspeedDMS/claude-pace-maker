"""
Story #155 -- edge cases for the declare_intent building blocks, exercised
in-process (fast, and measurable by coverage; the wire behaviour of the MCP
server is proven against a real subprocess in tests/test_intent_mcp_server.py).
"""

import io
import json
import runpy
import sqlite3
import sys

import pytest

from pacemaker.intent_declarations import gate
from pacemaker.intent_declarations.store import (
    IntentDeclarationStore,
    StoredIntent,
)
from pacemaker.intent_mcp import registration, server

ENABLED = {"intent_declaration_tool_enabled": True}


def _line(**message):
    return json.dumps({"jsonrpc": "2.0", **message})


class TestServerInProcess:
    def _serve(self, *lines):
        stdin = io.StringIO("".join(line + "\n" for line in lines))
        stdout, stderr = io.StringIO(), io.StringIO()
        server.serve(stdin, stdout, stderr)
        return [
            json.loads(x) for x in stdout.getvalue().splitlines()
        ], stderr.getvalue()

    def test_full_handshake_list_call_sequence(self):
        responses, _ = self._serve(
            _line(id=1, method="initialize", params={"protocolVersion": "2025-03-26"}),
            _line(method="notifications/initialized"),
            _line(id=2, method="tools/list"),
            _line(
                id=3,
                method="tools/call",
                params={
                    "name": "declare_intent",
                    "arguments": {"file_path": "/w/a.py", "change": "c", "goal": "g"},
                },
            ),
        )
        assert [r["id"] for r in responses] == [1, 2, 3]
        assert responses[0]["result"]["serverInfo"]["name"] == "pace-maker"
        assert responses[1]["result"]["tools"][0]["name"] == "declare_intent"
        assert (
            "Intent recorded for /w/a.py"
            in responses[2]["result"]["content"][0]["text"]
        )

    def test_error_paths_are_json_rpc_errors(self):
        responses, stderr = self._serve(
            "not json",
            "[1]",
            _line(id=1, method="nope"),
            _line(id=2, method="tools/call", params="x"),
            _line(id=3, method="tools/call", params={"name": "other"}),
            _line(
                id=4,
                method="tools/call",
                params={"name": "declare_intent", "arguments": "x"},
            ),
            _line(
                id=5,
                method="tools/call",
                params={"name": "declare_intent", "arguments": None},
            ),
        )
        codes = [r.get("error", {}).get("code") for r in responses]
        assert codes[:6] == [-32700, -32600, -32601, -32602, -32602, -32602]
        # arguments: null is "no arguments" -> a tool-level isError result.
        assert responses[6]["result"]["isError"] is True
        assert "malformed" in stderr

    def test_recursion_error_while_parsing_is_a_parse_error(self):
        responses, _ = self._serve("[" * 200000)
        assert responses[0]["error"]["code"] == -32700

    def test_main_reads_stdin_and_writes_stdout(self, monkeypatch):
        stdin = io.TextIOWrapper(
            io.BytesIO((_line(id=1, method="ping") + "\n").encode())
        )
        stdout = io.StringIO()
        monkeypatch.setattr(sys, "stdin", stdin)
        monkeypatch.setattr(sys, "stdout", stdout)
        server.main()
        assert json.loads(stdout.getvalue()) == {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {},
        }

    def test_python_dash_m_entry_point_runs_main(self, monkeypatch):
        stdin = io.TextIOWrapper(
            io.BytesIO((_line(id=7, method="ping") + "\n").encode())
        )
        stdout = io.StringIO()
        monkeypatch.setattr(sys, "stdin", stdin)
        monkeypatch.setattr(sys, "stdout", stdout)
        runpy.run_module("pacemaker.intent_mcp", run_name="__main__")
        assert json.loads(stdout.getvalue())["id"] == 7

    def test_invalid_utf8_bytes_do_not_crash_the_server(self, monkeypatch):
        raw = b"\xff\xfe garbage\n" + (_line(id=2, method="ping") + "\n").encode()
        stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="ascii", errors="strict")
        stdout = io.StringIO()
        monkeypatch.setattr(sys, "stdin", stdin)
        monkeypatch.setattr(sys, "stdout", stdout)
        server.main()
        responses = [json.loads(x) for x in stdout.getvalue().splitlines()]
        assert responses[-1] == {"jsonrpc": "2.0", "id": 2, "result": {}}


class TestStoreEdges:
    def test_failed_write_rolls_back_and_the_store_stays_usable(self, tmp_path):
        store = IntentDeclarationStore(str(tmp_path / "d.db"))
        with pytest.raises(sqlite3.IntegrityError):
            store.record("s", "main", "/w/a.py", None, "g")  # NOT NULL change
        store.record("s", "main", "/w/a.py", "c", "g")
        assert store.resolve("s", "main", "/w/a.py").intent.change == "c"

    def test_resolve_of_empty_store_is_none(self, tmp_path):
        assert (
            IntentDeclarationStore(str(tmp_path / "d.db")).resolve(
                "s", "main", "/w/a.py"
            )
            is None
        )

    def test_concurrent_resolvers_consume_a_declaration_exactly_once(self, tmp_path):
        import threading

        db = str(tmp_path / "d.db")
        IntentDeclarationStore(db).record("s", "main", "/w/a.py", "c", "g")
        winners = []

        def take():
            got = IntentDeclarationStore(db).resolve("s", "main", "/w/a.py")
            if got is not None:
                winners.append(got)

        threads = [threading.Thread(target=take) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert len(winners) == 1


class TestGateEdges:
    def test_record_outcome_without_session_id_is_a_noop(self):
        gate.record_outcome({}, "/w/a.py", None, approved=False, config=ENABLED)

    def test_same_message_with_empty_target_path_is_none(self):
        candidates = [{"file_path": "/w/a.py", "change": "c", "goal": "g"}]
        assert gate.same_message_declared_intent(candidates, "", "/w") is None

    def test_same_message_candidate_with_unnormalizable_path_is_skipped(self):
        candidates = [
            {"file_path": "/w/a\x00.py", "change": "c", "goal": "g"},
            {"file_path": "/w/a.py", "change": "ok", "goal": "g"},
        ]
        declared = gate.same_message_declared_intent(candidates, "/w/a.py", "/w")
        assert declared.intent.change == "ok"

    def test_non_dict_hook_data_fields_do_not_crash_helpers(self):
        assert gate.record_declare_intent({"tool_name": 5}, ENABLED) is False
        assert (
            gate.resolve_declared_intent({"session_id": ["x"]}, "/w/a.py", ENABLED)
            is None
        )

    def test_approve_stores_normalized_path_even_if_declared_path_differs(
        self, tmp_path
    ):
        # The intent carries the declaration's own path; the chain is keyed by
        # the Write/Edit's normalized path so the NEXT edit of that file matches.
        link = tmp_path / "link"
        link.symlink_to(tmp_path)
        real = str(tmp_path / "a.py")
        declared = gate.DeclaredIntent(
            "declare_intent", StoredIntent("/stale/other.py", "c", "g", "")
        )
        hook_data = {"session_id": "s", "cwd": str(tmp_path)}
        gate.record_outcome(
            hook_data, str(link / "a.py"), declared, approved=True, config=ENABLED
        )
        again = gate.resolve_declared_intent(hook_data, real, ENABLED)
        assert again is not None and again.source == "declare_intent_chain"


class TestRegistrationEdges:
    def test_unrunnable_binary_is_reported_not_raised_raw(self, tmp_path):
        # A directory is neither "not found" nor runnable: PermissionError.
        with pytest.raises(registration.RegistrationError, match="could not run"):
            registration.unregister(claude_bin=str(tmp_path))
