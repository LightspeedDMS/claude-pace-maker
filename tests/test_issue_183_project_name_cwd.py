"""Issue #183: telemetry project_name must come from hook_data["cwd"], not the
hook process cwd. Falls back to the process cwd only when no cwd is given.

Real SQLite DB (hook.DEFAULT_DB_PATH is redirected to tmp by conftest); no mocks.
"""

import sqlite3

import pytest

from pacemaker import hook
from pacemaker.database import initialize_database

DEGRADED = {"degraded": True, "failed_providers": {"gemini": "timeout"}}


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """Process cwd = proc_A; the session's stdin cwd = proj_B."""
    proc_a = tmp_path / "proc_A"
    proj_b = tmp_path / "proj_B"
    proc_a.mkdir()
    proj_b.mkdir()
    monkeypatch.chdir(proc_a)
    assert initialize_database(hook.DEFAULT_DB_PATH)
    return proc_a, proj_b


def _projects(event_type):
    conn = sqlite3.connect(hook.DEFAULT_DB_PATH)
    try:
        rows = conn.execute(
            "SELECT project_name FROM governance_events WHERE event_type = ?",
            (event_type,),
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


class TestDegradedTelemetryProject:
    def test_uses_stdin_cwd_not_process_cwd(self, dirs):
        _, proj_b = dirs
        hook._record_degraded_review_telemetry(
            DEGRADED, "codex", "sess-1", cwd=str(proj_b)
        )
        assert _projects("DG") == ["proj_B"]


class TestReasoningSummaryTelemetryProject:
    def test_uses_stdin_cwd_not_process_cwd(self, dirs):
        _, proj_b = dirs
        hook._record_reasoning_summary_telemetry(
            "reasoning_summary", "codex", "sess-1", cwd=str(proj_b)
        )
        assert _projects("RS") == ["proj_B"]


class TestNoCwdFallsBackToProcessCwd:
    def test_degraded_helper_without_cwd_uses_process_cwd(self, dirs):
        hook._record_degraded_review_telemetry(DEGRADED, "codex", "sess-1")
        assert _projects("DG") == ["proc_A"]

    def test_reasoning_summary_helper_without_cwd_uses_process_cwd(self, dirs):
        hook._record_reasoning_summary_telemetry(
            "reasoning_summary", "codex", "sess-1", cwd=None
        )
        assert _projects("RS") == ["proc_A"]


EXPECTED_RESOLVE_CALL_SITES = 7  # 5 direct gate sites + 2 telemetry helpers
EXPECTED_HELPER_CALL_SITES = 4  # Write/Edit gate + Danger-Bash gate, x 2 helpers


def _calls_to(name):
    import ast
    from pathlib import Path

    tree = ast.parse(Path(hook.__file__).read_text())
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
    ]


class TestEveryCallSitePassesCwd:
    def test_resolve_project_name_always_gets_the_payload_cwd(self):
        import ast

        # ast.unparse normalises quote style on both sides of the comparison.
        allowed = [
            [ast.unparse(ast.parse(src))] for src in ('hook_data.get("cwd")', "cwd")
        ]
        calls = _calls_to("_resolve_project_name")
        assert len(calls) == EXPECTED_RESOLVE_CALL_SITES
        bad = [
            c.lineno
            for c in calls
            if c.keywords or [ast.unparse(a) for a in c.args] not in allowed
        ]
        assert bad == [], f"_resolve_project_name without the payload cwd at {bad}"

    def test_telemetry_helpers_are_always_called_with_cwd_keyword(self):
        calls = _calls_to("_record_degraded_review_telemetry") + _calls_to(
            "_record_reasoning_summary_telemetry"
        )
        assert len(calls) == EXPECTED_HELPER_CALL_SITES
        bad = [c.lineno for c in calls if "cwd" not in [k.arg for k in c.keywords]]
        assert bad == [], f"telemetry helper called without cwd= at {bad}"


def _drive_blocked_write(tmp_path, stdin_cwd):
    """Run the real run_pre_tool_hook for a Write whose review is rejected.

    Only the reviewer (validate_intent_and_code) and config/extension lookups
    are patched, exactly as tests/test_pre_tool_hook.py does.
    """
    import json
    from unittest.mock import patch

    target = "/path/to/test.py"
    transcript = tmp_path / "t.jsonl"
    entries = [
        {"message": {"role": "assistant", "content": [{"type": "text", "text": "x"}]}},
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Write",
                        "input": {"file_path": target, "content": "code"},
                    }
                ],
            }
        },
    ]
    transcript.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    payload = {
        "session_id": "sess-183",
        "transcript_path": str(transcript),
        "tool_name": "Write",
        "tool_input": {"file_path": target, "content": "code"},
    }
    if stdin_cwd is not None:
        payload["cwd"] = stdin_cwd
    with (
        patch("pacemaker.hook.load_config") as cfg,
        patch("pacemaker.extension_registry.load_extensions", return_value=[".py"]),
        patch("pacemaker.extension_registry.is_source_code_file", return_value=True),
        patch(
            "pacemaker.hook.get_last_n_messages_for_validation",
            return_value=(["m"], ["m"]),
        ),
        patch(
            "pacemaker.intent_validator.validate_intent_and_code",
            return_value={"approved": False, "feedback": "Intent declaration required"},
        ),
        patch("sys.stdin") as stdin,
    ):
        cfg.return_value = {"intent_validation_enabled": True}
        stdin.read.return_value = json.dumps(payload)
        return hook.run_pre_tool_hook()


class TestRunPreToolHookProject:
    def test_blocked_write_records_stdin_cwd_project(self, dirs, tmp_path):
        _, proj_b = dirs
        result = _drive_blocked_write(tmp_path, str(proj_b))
        assert result["decision"] == "block"
        assert _projects("IV") == ["proj_B"]

    def test_blocked_write_without_stdin_cwd_records_process_cwd(self, dirs, tmp_path):
        result = _drive_blocked_write(tmp_path, None)
        assert result["decision"] == "block"
        assert _projects("IV") == ["proc_A"]
