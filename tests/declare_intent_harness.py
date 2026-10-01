"""
Shared hook-level harness for the declare_intent tests (Story #155, Bug #159).

NOT a test module (no ``test_`` prefix -- ``scripts/run_tests.sh`` globs
``tests/test_*.py``). Test files import it as a flat sibling
(``from declare_intent_harness import Harness``), the same convention
``tests/test_external_cli_guard.py`` uses for ``conftest``.

Drives the REAL ``run_pre_tool_hook`` (Write/Edit gate) against the real
SQLite declaration store (tmp path from the conftest guard). The ONLY thing
mocked is the external reviewer LLM call, at the namespace Stage 2 imports it
from (``pacemaker.inference.resolve_and_call_with_reviewer``) -- per this
project's CLAUDE.md. The ``fixture`` itself (``h``) stays in each test module
so each file owns its own tmp_path scope.
"""

import contextlib
import json
import os
import sqlite3
from typing import List
from unittest.mock import MagicMock, patch

os.environ.setdefault("PACEMAKER_TEST_MODE", "1")

from pacemaker import database  # noqa: E402
from pacemaker.intent_declarations import gate  # noqa: E402
from pacemaker.intent_declarations.store import resolve_db_path  # noqa: E402

USER_TOOL = "mcp__pace-maker__declare_intent"
SESSION = "sess-155"
APPROVED = ("APPROVED", "codex-gpt5")


def _config(**overrides) -> dict:
    # hook_model "codex": routes through the SDK-independent inference path
    # (the Claude Agent SDK is not importable in every test environment).
    cfg = {
        "enabled": True,
        "intent_validation_enabled": True,
        "tdd_enabled": True,
        "danger_bash_enabled": False,
        "hook_model": "codex",
    }
    cfg.update(overrides)
    return cfg


def _anchor_must_not_run(*args, **kwargs):
    raise AssertionError("the transcript anchor must not be consulted (AC4)")


def _anchor_not_found(
    transcript_path,
    tool_input=None,
    tool_name=None,
    _max_wait_seconds=30.0,
    _initial_sleep=0.25,
    _backoff_multiplier=2.0,
    _max_sleep=2.0,
    _diagnostics=None,
    _stale_grace_seconds=None,
):
    if _diagnostics is not None:
        _diagnostics["attempts"] = 1
        _diagnostics["elapsed_seconds"] = 0.0
        _diagnostics["outcome"] = "not_found"
    return None


class Harness:
    """One tmp project + usage DB + empty transcript, and a ``run`` that
    pushes a real Write/Edit payload through the real gate."""

    def __init__(self, tmp_path):
        self.root = tmp_path / "proj"
        (self.root / "src").mkdir(parents=True)
        (self.root / "scratch").mkdir()
        self.core_file = str(self.root / "src" / "a.py")
        self.core_file_b = str(self.root / "src" / "b.py")
        self.noncore_file = str(self.root / "scratch" / "n.py")
        self.db_path = str(tmp_path / "usage.db")
        self.transcript = str(tmp_path / "transcript.jsonl")
        open(self.transcript, "w").close()
        database.initialize_database(self.db_path)
        self.reviewer_prompts: List[str] = []

    # -- payloads ---------------------------------------------------------
    def payload(self, tool_name, file_path, agent_id=None, **tool_input):
        tool_input.setdefault("file_path", file_path)
        if tool_name == "Write":
            tool_input.setdefault("content", "x = 1\n")
        else:
            tool_input.setdefault("old_string", "x = 0")
            tool_input.setdefault("new_string", "x = 1")
        data = {
            "session_id": SESSION,
            "transcript_path": self.transcript,
            "cwd": str(self.root),
            "tool_name": tool_name,
            "tool_input": tool_input,
        }
        if agent_id:
            data["agent_id"] = agent_id
        return data

    def declare(
        self,
        file_path,
        change="add x",
        goal="make x one",
        test_coverage="tests/test_a.py - test_x",
        agent_id=None,
        tool_name=USER_TOOL,
        config=None,
    ):
        tool_input = {"file_path": file_path, "change": change, "goal": goal}
        if test_coverage:
            tool_input["test_coverage"] = test_coverage
        data = {
            "session_id": SESSION,
            "cwd": str(self.root),
            "tool_name": tool_name,
            "tool_input": tool_input,
        }
        if agent_id:
            data["agent_id"] = agent_id
        return gate.record_declare_intent(data, config or _config())

    # -- running the gate -------------------------------------------------
    def run(
        self,
        tool_name,
        file_path,
        agent_id=None,
        anchor=_anchor_must_not_run,
        reviewer_response=APPROVED,
        config=None,
        **tool_input,
    ):
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = json.dumps(
            self.payload(tool_name, file_path, agent_id=agent_id, **tool_input)
        )

        def fake_reviewer(*args, **kwargs):
            self.reviewer_prompts.append(kwargs.get("prompt", ""))
            return reviewer_response

        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch("sys.stdin", MagicMock(read=lambda: stdin_payload))
            )
            stack.enter_context(
                patch("pacemaker.hook.load_config", return_value=config or _config())
            )
            stack.enter_context(patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path))
            if anchor is not None:
                stack.enter_context(
                    patch(
                        "pacemaker.hook.get_current_turn_message_for_validation",
                        side_effect=anchor,
                    )
                )
            stack.enter_context(
                patch(
                    "pacemaker.inference.resolve_and_call_with_reviewer",
                    side_effect=fake_reviewer,
                )
            )
            return run_pre_tool_hook()

    # -- inspection -------------------------------------------------------
    def blockages(self) -> List[dict]:
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT category, details FROM blockage_events ORDER BY id"
            ).fetchall()
        finally:
            conn.close()
        return [
            {"category": r[0], "details": json.loads(r[1]) if r[1] else {}}
            for r in rows
        ]

    def store_rows(self, table) -> List[dict]:
        conn = sqlite3.connect(resolve_db_path())
        try:
            conn.row_factory = sqlite3.Row
            try:
                return [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
            except sqlite3.OperationalError:
                return []
        finally:
            conn.close()
