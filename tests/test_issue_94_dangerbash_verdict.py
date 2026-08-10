"""
Issue #94 regression tests: the danger-bash Phase 2 gate compared the
reviewer's response with strict whole-string equality
(``response.strip().upper() == "APPROVED"``) instead of the project's
canonical verdict-normalization primitive,
``pacemaker.inference.verdict.verdict_passes``. Any trailing punctuation,
repeated token, or commentary after "APPROVED" made the comparison false and
blocked a command the reviewer had actually approved.

This module proves, at the danger-bash GATE level (not just
``tests/test_verdict.py``'s unit-level truth table for the primitive
itself), that:

1. The exact reviewer strings recorded in ``usage.db`` for issue #94
   (``APPROVED APPROVED``, ``APPROVEDAPPROVED``, and two commentary
   variants) now ALLOW the command, where they previously blocked it.
2. ``APPROVED.`` (trailing punctuation) allows the command.
3. Safety is NOT loosened: ``NOT APPROVED``, an ``APPROVED`` line combined
   with a ``BLOCKED:`` line, empty/whitespace-only responses, and ordinary
   mismatch feedback prose all still BLOCK the command, with the
   ``intent_validation_dangerbash`` blockage telemetry recorded exactly as
   before.

Harness: drives the real ``pacemaker.hook.run_pre_tool_hook()`` entry point
against a REAL transcript fixture (a Bash tool_use whose input byte-matches
the hook's tool_input, with a real INTENT-bearing text block, mirroring
tests/test_issue_93_danger_bash_anchor.py's fixture-building helpers) so the
real (unmocked) transcript_reader anchor logic runs, and against the REAL
danger_bash_rules.load_rules/match_command functions (unmocked, called
un-patched from inside hook.py) pointed at a non-existent user-config path
via ``pacemaker.hook.DEFAULT_DANGER_RULES_PATH`` so only the bundled
defaults apply — command "chmod 777 ..." matches default rule SD-011 for
real.

MOCKING RATIONALE: ``pacemaker.inference.resolve_and_call_with_reviewer``
(the actual LLM call) is the one piece of PRODUCTION BUSINESS LOGIC mocked
here, and it is the sanctioned external-service exception under the
project's anti-mock policy — no internal decision function is stubbed.
``sys.stdin``, ``pacemaker.hook.load_config``, ``pacemaker.hook.DEFAULT_DB_PATH``,
and ``pacemaker.hook.DEFAULT_DANGER_RULES_PATH`` are TEST HARNESS
SCAFFOLDING, not business-logic mocks: they inject synthetic hook input and
redirect the real functions to temp-directory paths so the test does not
read real stdin or touch the user's real
``~/.claude-pace-maker/{usage.db,danger_bash_rules.yaml}``. Every one of
these still runs its real, unmocked implementation (real JSON parsing, real
config dict handling, real regex rule matching, real SQLite writes) — only
its *source path* is swapped, exactly mirroring the DEFAULT_DB_PATH
redirection pattern already used throughout this test suite (e.g.
tests/test_issue_93_danger_bash_anchor.py).
"""

import json
import os
import sqlite3
import tempfile
from typing import List, Optional
from unittest.mock import MagicMock, patch

os.environ.setdefault("PACEMAKER_TEST_MODE", "1")

from pacemaker import database  # noqa: E402


def _asst(request_id: Optional[str], block: dict) -> dict:
    entry = {"message": {"role": "assistant", "content": [block]}}
    if request_id is not None:
        entry["requestId"] = request_id
    return entry


def _text_block(t: str) -> dict:
    return {"type": "text", "text": t}


def _tool_use_block(name: str, inp: dict, tool_id: str = "toolu_default") -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": inp}


def _write_transcript(lines: List[dict], path: str) -> str:
    with open(path, "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return path


BASH_CMD = "chmod 777 /tmp/sandbox/probe.txt"
BASH_INTENT = (
    "INTENT: Loosen permissions on the sandbox probe file for a local test.\n"
    "This affects only the throwaway sandbox file, not real project data."
)


def _config_enabled() -> dict:
    return {
        "enabled": True,
        "intent_validation_enabled": True,
        "danger_bash_enabled": True,
        "hook_model": "auto",
    }


def _hook_stdin(command: str, transcript_path: str, session_id: str = "s1") -> str:
    return json.dumps(
        {
            "session_id": session_id,
            "transcript_path": transcript_path,
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
    )


class _DbHarness:
    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "usage.db")
        self.transcript = os.path.join(self.tmp_dir, "transcript.jsonl")
        # Real Bash tool_use with a byte-identical input match plus a real
        # INTENT-bearing text block in the same logical turn, so the real
        # (unmocked) transcript_reader anchor finds it immediately.
        _write_transcript(
            [
                _asst("req_A", _text_block(BASH_INTENT)),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": BASH_CMD}, "toolu_A"),
                ),
            ],
            self.transcript,
        )
        # Point danger_bash_rules at a non-existent user-config path so only
        # the bundled defaults apply — no real ~/.claude-pace-maker file is
        # touched, and no user customizations leak into the test.
        self.no_user_rules_path = os.path.join(self.tmp_dir, "no_such_rules.yaml")
        database.initialize_database(self.db_path)

    def teardown_method(self):
        import shutil

        shutil.rmtree(self.tmp_dir, ignore_errors=True)


def _run_gate_with_reviewer_response(harness: _DbHarness, reviewer_response: str):
    """Drives run_pre_tool_hook() with Phase 1 passing for real (real
    transcript anchor + real default danger-rule match on "chmod 777") and
    Phase 2's reviewer call mocked to return `reviewer_response`. Returns
    the hook result dict."""
    from pacemaker.hook import run_pre_tool_hook

    stdin_payload = _hook_stdin(BASH_CMD, harness.transcript)

    with (
        patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
        patch("pacemaker.hook.load_config", return_value=_config_enabled()),
        patch("pacemaker.hook.DEFAULT_DB_PATH", harness.db_path),
        patch("pacemaker.hook.DEFAULT_DANGER_RULES_PATH", harness.no_user_rules_path),
        patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=(reviewer_response, "test-reviewer"),
        ),
    ):
        result = run_pre_tool_hook()
    return result


def _dangerbash_blockage_rows(harness: _DbHarness):
    conn = sqlite3.connect(harness.db_path)
    try:
        return conn.execute(
            "SELECT reason, details FROM blockage_events "
            "WHERE category = 'intent_validation_dangerbash'"
        ).fetchall()
    finally:
        conn.close()


class TestPhase2RecordedFalseBlocksNowAllow(_DbHarness):
    """The three exact reviewer strings recorded in usage.db for issue #94
    must now ALLOW the command (verdict_passes semantics)."""

    def test_exact_double_word_approved_allows(self):
        result = _run_gate_with_reviewer_response(self, "APPROVED APPROVED")
        assert result == {"continue": True}, result

    def test_exact_concatenated_approved_allows(self):
        result = _run_gate_with_reviewer_response(self, "APPROVEDAPPROVED")
        assert result == {"continue": True}, result

    def test_approved_with_sd014_commentary_allows(self):
        response = (
            "APPROVED  The SD-014 danger-rule match is a false positive: "
            'the word "shutdown" appears only in test file *names*'
        )
        result = _run_gate_with_reviewer_response(self, response)
        assert result == {"continue": True}, result


class TestPhase2AdditionalLeniencyCasesAllow(_DbHarness):
    """Additional leniency cases: more recorded commentary, trailing
    punctuation, and confirmation that no blockage telemetry is recorded on
    the allow path."""

    def test_approved_with_pytest_readonly_commentary_allows(self):
        response = (
            "APPROVED  The command runs pytest in quiet mode on the "
            "declared test file with output truncated via `tail -6` — "
            "exactly matching the stated intent of a read-only RED-phase "
            "test run."
        )
        result = _run_gate_with_reviewer_response(self, response)
        assert result == {"continue": True}, result

    def test_approved_with_trailing_period_allows(self):
        result = _run_gate_with_reviewer_response(self, "APPROVED.")
        assert result == {"continue": True}, result

    def test_allowed_path_does_not_record_dangerbash_blockage(self):
        _run_gate_with_reviewer_response(self, "APPROVED APPROVED")
        rows = _dangerbash_blockage_rows(self)
        assert rows == [], (
            f"An approved (even with trailing text) verdict must not "
            f"record an intent_validation_dangerbash blockage; got: {rows}"
        )


class TestPhase2HardRejectionsStillBlock(_DbHarness):
    """The leniency fix must not loosen genuine rejections — these must
    remain BLOCKED exactly as before, with telemetry recorded."""

    def test_not_approved_still_blocks(self):
        result = _run_gate_with_reviewer_response(self, "NOT APPROVED")
        assert result.get("decision") == "block", result
        rows = _dangerbash_blockage_rows(self)
        assert len(rows) == 1
        assert rows[0][0].startswith("NOT APPROVED")

    def test_approved_line_plus_blocked_line_still_blocks(self):
        response = "APPROVED\nBLOCKED: unsafe scope mismatch"
        result = _run_gate_with_reviewer_response(self, response)
        assert result.get("decision") == "block", (
            f"BLOCKED: must win over an APPROVED line on another line "
            f"(fail-closed priority); got: {result}"
        )
        rows = _dangerbash_blockage_rows(self)
        assert len(rows) == 1

    def test_empty_response_still_blocks(self):
        result = _run_gate_with_reviewer_response(self, "")
        assert result.get("decision") == "block", result
        rows = _dangerbash_blockage_rows(self)
        assert len(rows) == 1


class TestPhase2SoftRejectionsAndTelemetryStillBlock(_DbHarness):
    """Whitespace-only and ordinary mismatch prose must still block, and the
    blocked-path telemetry details must record the reviewer identity and
    matched rules exactly as before."""

    def test_whitespace_only_response_still_blocks(self):
        result = _run_gate_with_reviewer_response(self, "   \n  \n")
        assert result.get("decision") == "block", result
        rows = _dangerbash_blockage_rows(self)
        assert len(rows) == 1

    def test_ordinary_mismatch_feedback_still_blocks(self):
        response = (
            "The command deletes files outside the declared scope. The "
            "INTENT only mentions the sandbox probe file, but this command "
            "also touches /etc/passwd. Mismatch."
        )
        result = _run_gate_with_reviewer_response(self, response)
        assert result.get("decision") == "block", result
        rows = _dangerbash_blockage_rows(self)
        assert len(rows) == 1
        assert "Mismatch" in rows[0][0]

    def test_blocked_path_records_reviewer_and_matched_rules_in_details(self):
        _run_gate_with_reviewer_response(self, "NOT APPROVED")
        rows = _dangerbash_blockage_rows(self)
        details = json.loads(rows[0][1])
        assert details.get("reviewer") == "test-reviewer"
        assert details.get("matched_rules") == ["SD-011"]
        assert details.get("tool") == "Bash"


class TestPhase2PromptRequiresBlockedPrefix:
    """Code-review followup: verdict_passes() only engages its BLOCKED:
    priority guard if the reviewer actually emits a BLOCKED: line. The
    Phase 2 prompt previously never asked for that prefix, leaving the
    guard dormant and exploitable shapes like "Approved: NO" or
    "APPROVED? No." (lines that start with the literal token "APPROVED")
    passing as approvals. This class proves (1) the prompt sent to the
    reviewer now instructs it to prefix rejections with 'BLOCKED:', and
    (2) a reviewer response that follows that instruction is correctly
    blocked at the gate level."""

    def setup_method(self):
        self.harness = _DbHarness()
        self.harness.setup_method()

    def teardown_method(self):
        self.harness.teardown_method()

    def test_prompt_sent_to_reviewer_requires_blocked_prefix(self):
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _hook_stdin(BASH_CMD, self.harness.transcript)

        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.harness.db_path),
            patch(
                "pacemaker.hook.DEFAULT_DANGER_RULES_PATH",
                self.harness.no_user_rules_path,
            ),
            patch(
                "pacemaker.inference.resolve_and_call_with_reviewer",
                return_value=("APPROVED", "test-reviewer"),
            ) as mock_reviewer,
        ):
            run_pre_tool_hook()

        assert mock_reviewer.called
        _, call_kwargs = mock_reviewer.call_args
        assert "BLOCKED:" in call_kwargs["prompt"], (
            "Phase 2 user prompt must instruct the reviewer to prefix "
            "rejections with 'BLOCKED:' so verdict_passes()'s BLOCKED "
            "priority guard can engage."
        )
        assert "BLOCKED:" in call_kwargs["system_prompt"], (
            "Phase 2 system prompt must also require the 'BLOCKED:' "
            "prefix on rejection."
        )

    def test_blocked_prefix_response_blocks_at_gate_level(self):
        response = (
            "BLOCKED: The intent only covers the sandbox probe file, but "
            "this chmod 777 also affects group/other write permissions "
            "beyond what was declared."
        )
        result = _run_gate_with_reviewer_response(self.harness, response)
        assert result.get("decision") == "block", result
        rows = _dangerbash_blockage_rows(self.harness)
        assert len(rows) == 1
        assert rows[0][0].startswith("BLOCKED:")
