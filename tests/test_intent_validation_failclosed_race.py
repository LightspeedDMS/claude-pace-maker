"""
Hook-level regression tests for the Write/Edit gate's fail-CLOSED reaction
to the transcript-flush race (v2.33.2 follow-up to bug #83).

WHY THIS EXISTS
===============
The bug-#83 fix (tool-matched anchor + bounded retry in
``get_current_turn_message_for_validation``) correctly DETECTS "the current
turn isn't flushed yet" by returning ``None``. But the Write/Edit gate's
REACTION to that ``None`` was to fail OPEN (``{"continue": True}``) —
silently letting the edit through completely UNVALIDATED. Confirmed live
(via the ``intent_validation_deferred`` telemetry canary + a raced edit that
passed unvalidated): intent validation enforced NOTHING for any edit that
raced the transcript flush.

The danger-bash gate reacts to the IDENTICAL ``None`` signal by failing
CLOSED (block + re-issue), and this is empirically proven to work: the agent
re-issues the IDENTICAL command, the re-issue's turn is then flushed, the
tool-matched anchor binds to it, and validation proceeds normally on the
second attempt. This module locks in the Write/Edit gate now doing the same
thing (Bug A core regression + adjacent behaviors), so the only paths that
can legitimately reach the not-ready branch flow into a transient BLOCK,
never a silent pass-through.

MOCKING RATIONALE
==================
``pacemaker.hook.get_current_turn_message_for_validation`` is patched
directly (rather than exercising the real retry loop against a real/missing
transcript file) to isolate the HOOK's reaction to each of the three
possible return values (``None`` / ``""`` / a real string) from the SOURCE
of that value. The real retry-loop + tool-matched-anchor matching algorithm
is covered separately and exhaustively in
``tests/test_transcript_staleness_fix.py`` (not duplicated here).

All codex/gemini/claude calls are mocked per project convention — the
autouse guard in ``tests/conftest.py`` blocks real ones. Stage 2 (LLM
review) is never reached in the None/"" cases below (both block before
Stage 2); the valid-override case mocks ``validate_intent_and_code``
directly so no real LLM call is made.
"""

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# PACEMAKER_TEST_MODE must be set before any pacemaker import.
os.environ.setdefault("PACEMAKER_TEST_MODE", "1")

from pacemaker import database  # noqa: E402


# ---------------------------------------------------------------------------
# Shared fixtures / constants
# ---------------------------------------------------------------------------

CORE_FILE = "/home/jsbattig/Dev/my_project/src/my_package/foo.py"
NEW_CONTENT = "result = 42\n"
VALID_INTENT = (
    "INTENT: Modify foo.py to compute the answer.\n"
    "Test coverage: tests/test_foo.py::test_answer_is_42"
)


def _make_hook_stdin(
    tool_name: str, file_path: str, content: str, transcript_path: str
) -> str:
    """Craft the JSON stdin blob that run_pre_tool_hook reads."""
    if tool_name == "Write":
        tool_input = {"file_path": file_path, "content": content}
    else:
        tool_input = {"file_path": file_path, "new_string": content}
    return json.dumps(
        {
            "session_id": "test-session-race",
            "transcript_path": transcript_path,
            "tool_name": tool_name,
            "tool_input": tool_input,
        }
    )


def _config_enabled() -> dict:
    return {
        "enabled": True,
        "intent_validation_enabled": True,
        "tdd_enabled": True,
        "danger_bash_enabled": False,
    }


class _DbHarness:
    """Shared tmp-DB + tmp-transcript setup/teardown."""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "usage.db")
        self.transcript = os.path.join(self.tmp_dir, "transcript.jsonl")
        Path(self.transcript).write_text("")
        database.initialize_database(self.db_path)

    def teardown_method(self):
        import shutil

        shutil.rmtree(self.tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Item 1: CORE REGRESSION (Bug A) — None override must BLOCK, not continue
# ---------------------------------------------------------------------------


class TestCoreRegressionBugA(_DbHarness):
    """get_current_turn_message_for_validation() -> None must now BLOCK
    (fail-closed), not silently continue (the bug this whole fix addresses)."""

    def _run(self, tool_name: str) -> dict:
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _make_hook_stdin(
            tool_name, CORE_FILE, NEW_CONTENT, self.transcript
        )

        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            # The exact condition Bug A is about: the resolver says "not
            # flushed yet" via None.
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                return_value=None,
            ),
            patch(
                "pacemaker.hook.get_last_n_messages_for_validation",
                return_value=[],
            ),
            patch("pacemaker.extension_registry.load_extensions", return_value=set()),
            patch(
                "pacemaker.extension_registry.is_source_code_file",
                return_value=True,
            ),
        ):
            return run_pre_tool_hook()

    def test_none_override_blocks_for_write(self):
        result = self._run("Write")
        assert (
            result.get("decision") == "block"
        ), f"Expected decision=block (fail-closed) for None override; got: {result}"
        assert (
            result.get("continue") is not True
        ), f"Must NOT also signal continue=True; got: {result}"
        assert "reason" in result and result["reason"], (
            "Block response must include a non-empty reason; " f"got: {result}"
        )

    def test_none_override_blocks_for_edit(self):
        result = self._run("Edit")
        assert result.get("decision") == "block", (
            f"Expected decision=block (fail-closed) for None override on "
            f"Edit; got: {result}"
        )

    def test_none_override_records_deferred_blockage_event(self):
        self._run("Write")
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT category, hook_type FROM blockage_events "
                "WHERE category = 'intent_validation_deferred'"
            ).fetchall()
        finally:
            conn.close()
        assert rows, (
            "Expected an intent_validation_deferred blockage event when the "
            "gate fails closed on the transcript race."
        )
        hook_types = {r[1] for r in rows}
        assert "pre_tool_use" in hook_types

    def test_none_override_reason_explains_race_not_rejection(self):
        """The block reason must read as a transient timing race, not a
        rejection of the agent's intent or code — and must instruct
        re-issuing the IDENTICAL tool call (required for the tool-matched
        anchor to bind on the next attempt)."""
        result = self._run("Write")
        reason = result.get("reason", "").lower()
        assert "transcript" in reason
        assert "re-issue" in reason
        assert (
            "write" in reason
        ), f"Reason should name the tool to re-issue (Write); got: {result.get('reason')!r}"

    def test_none_override_telemetry_failure_does_not_break_block_decision(self):
        """If record_activity_event/record_governance_event raise (e.g. a
        transient DB error), the fail-closed BLOCK decision must still be
        returned — telemetry is best-effort and must never cascade into
        silently letting an unvalidated edit through (the exact bug this
        suite exists to prevent)."""
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _make_hook_stdin(
            "Write", CORE_FILE, NEW_CONTENT, self.transcript
        )

        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                return_value=None,
            ),
            patch(
                "pacemaker.hook.get_last_n_messages_for_validation",
                return_value=[],
            ),
            patch("pacemaker.extension_registry.load_extensions", return_value=set()),
            patch(
                "pacemaker.extension_registry.is_source_code_file",
                return_value=True,
            ),
            patch(
                "pacemaker.hook.record_activity_event",
                side_effect=RuntimeError("simulated telemetry failure"),
            ),
            patch(
                "pacemaker.hook.record_governance_event",
                side_effect=RuntimeError("simulated telemetry failure"),
            ),
        ):
            result = run_pre_tool_hook()

        assert result.get("decision") == "block", (
            "Telemetry failure must not prevent the fail-closed block "
            f"decision from being returned; got: {result}"
        )


# ---------------------------------------------------------------------------
# Item 2: valid INTENT override -> gate proceeds INTO validation
# (not the not-ready block)
# ---------------------------------------------------------------------------


class TestValidOverrideProceedsToValidation(_DbHarness):
    """A real (non-None) override must be treated as 'transcript ready' and
    flow into Stage 1/2 validation — never mistaken for the not-ready race."""

    def test_valid_intent_override_invokes_validate_intent_and_code(self):
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _make_hook_stdin(
            "Write", CORE_FILE, NEW_CONTENT, self.transcript
        )

        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                return_value=VALID_INTENT,
            ),
            patch(
                "pacemaker.hook.get_last_n_messages_for_validation",
                return_value=[],
            ),
            patch("pacemaker.extension_registry.load_extensions", return_value=set()),
            patch(
                "pacemaker.extension_registry.is_source_code_file",
                return_value=True,
            ),
            patch(
                "pacemaker.intent_validator.validate_intent_and_code"
            ) as mock_validate,
        ):
            mock_validate.return_value = {"approved": True}
            result = run_pre_tool_hook()

        # Proceeded INTO validation (Stage 1/2), not the not-ready block.
        mock_validate.assert_called_once()
        _, kwargs = mock_validate.call_args
        assert kwargs.get("current_message_override") == VALID_INTENT
        assert result == {"continue": True}, (
            f"Approved validation must pass through as plain continue; "
            f"got: {result}"
        )

    def test_valid_override_does_not_record_deferred_blockage(self):
        """A valid override must never be conflated with the not-ready race —
        no intent_validation_deferred telemetry should appear."""
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _make_hook_stdin(
            "Write", CORE_FILE, NEW_CONTENT, self.transcript
        )

        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                return_value=VALID_INTENT,
            ),
            patch(
                "pacemaker.hook.get_last_n_messages_for_validation",
                return_value=[],
            ),
            patch("pacemaker.extension_registry.load_extensions", return_value=set()),
            patch(
                "pacemaker.extension_registry.is_source_code_file",
                return_value=True,
            ),
            patch(
                "pacemaker.intent_validator.validate_intent_and_code",
                return_value={"approved": True},
            ),
        ):
            run_pre_tool_hook()

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT category FROM blockage_events "
                "WHERE category = 'intent_validation_deferred'"
            ).fetchall()
        finally:
            conn.close()
        assert not rows, (
            "A valid override must not be treated as the not-ready/deferred "
            f"race; found deferred blockage(s): {rows}"
        )


# ---------------------------------------------------------------------------
# Item 3: empty-string override (turn found, no INTENT) -> Stage-1 block
# ---------------------------------------------------------------------------


class TestEmptyOverrideStage1Block(_DbHarness):
    """ "" means the tool-matched anchor FOUND the turn but its text carries
    no INTENT: marker — a real Stage-1 rejection, NOT the not-ready race.
    Real Stage-1 regex executes (no LLM call, no mocking of
    validate_intent_and_code needed — Anti-Mock: prefer the real
    implementation when it is side-effect-free)."""

    def test_empty_override_with_no_intent_in_nback_blocks_stage1(self):
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _make_hook_stdin(
            "Write", CORE_FILE, NEW_CONTENT, self.transcript
        )

        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                return_value="",
            ),
            # n-back fallback also has no INTENT marker -> Stage 1 must reject.
            patch(
                "pacemaker.hook.get_last_n_messages_for_validation",
                return_value=["I will write the code now."],
            ),
            patch("pacemaker.extension_registry.load_extensions", return_value=set()),
            patch(
                "pacemaker.extension_registry.is_source_code_file",
                return_value=True,
            ),
        ):
            result = run_pre_tool_hook()

        assert result.get("decision") == "block", (
            f"Expected a Stage-1 intent_validation block for no-INTENT "
            f"content; got: {result}"
        )
        assert "INTENT" in result.get("reason", "")

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute("SELECT category FROM blockage_events").fetchall()
        finally:
            conn.close()
        categories = {r[0] for r in rows}
        assert "intent_validation" in categories, (
            f"Expected category='intent_validation' (Stage-1 reject); "
            f"got categories: {categories}"
        )
        assert "intent_validation_deferred" not in categories, (
            "Empty-string override (turn found) must be a Stage-1 BLOCK, "
            f"never the not-ready/deferred race path; categories: {categories}"
        )


# ---------------------------------------------------------------------------
# Item 4: subagent transcript regression — gate must validate subagent edits
# via the subagent's OWN transcript path (.../<session>/subagents/agent-X.jsonl)
# ---------------------------------------------------------------------------


class TestSubagentTranscriptRegression:
    """Claude Code 2.1.39+ provides the SUBAGENT's own transcript_path
    directly in PreToolUse hook_data when running inside a subagent context.
    The agent_id/tool_use_id resolver at hook.py ~2429-2496 is therefore
    vestigial for this scenario and correctly never fires: neither branch's
    precondition is met when transcript_path already contains "/agent-" (no
    agent_id supplied here, and the elif's "'/agent-' not in transcript_path"
    guard is False). This suite proves the gate validates correctly against
    a transcript at that nested path, for both the no-INTENT (block) and
    INTENT-present (pass) cases — locking in that subagent edits are
    validated via the SUBAGENT's transcript, not silently skipped.
    """

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "usage.db")
        database.initialize_database(self.db_path)
        subagents_dir = os.path.join(self.tmp_dir, "session-abc123", "subagents")
        os.makedirs(subagents_dir, exist_ok=True)
        self.transcript = os.path.join(subagents_dir, "agent-X.jsonl")

    def teardown_method(self):
        import shutil

        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write_transcript(self, lines):
        with open(self.transcript, "w") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")

    def _run(self) -> dict:
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _make_hook_stdin(
            "Write", CORE_FILE, NEW_CONTENT, self.transcript
        )
        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch("pacemaker.extension_registry.load_extensions", return_value=set()),
            patch(
                "pacemaker.extension_registry.is_source_code_file",
                return_value=True,
            ),
        ):
            return run_pre_tool_hook()

    def test_subagent_no_intent_blocks_stage1(self):
        """Subagent transcript has the matching (flushed) Write tool_use but
        NO INTENT marker in its text -> real tool-matched anchor returns ""
        -> Stage-1 must block."""
        self._write_transcript(
            [
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "Writing the answer now."},
                            {
                                "type": "tool_use",
                                "name": "Write",
                                "input": {
                                    "file_path": CORE_FILE,
                                    "content": NEW_CONTENT,
                                },
                            },
                        ],
                    }
                }
            ]
        )
        result = self._run()
        assert (
            result.get("decision") == "block"
        ), f"Subagent edit without INTENT must Stage-1 block; got: {result}"
        assert "INTENT" in result.get("reason", "")

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute("SELECT category FROM blockage_events").fetchall()
        finally:
            conn.close()
        categories = {r[0] for r in rows}
        assert "intent_validation" in categories
        assert "intent_validation_deferred" not in categories, (
            "Flushed subagent transcript must never hit the not-ready/"
            f"deferred path; categories: {categories}"
        )

    def test_subagent_with_intent_passes(self):
        """Subagent transcript has the matching Write tool_use WITH an
        INTENT marker in its text -> the real tool-matched anchor finds it,
        validation proceeds to Stage 2 (mocked APPROVED so no real LLM call
        is made) and passes."""
        self._write_transcript(
            [
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": VALID_INTENT},
                            {
                                "type": "tool_use",
                                "name": "Write",
                                "input": {
                                    "file_path": CORE_FILE,
                                    "content": NEW_CONTENT,
                                },
                            },
                        ],
                    }
                }
            ]
        )
        with patch(
            "pacemaker.intent_validator.validate_intent_and_code"
        ) as mock_validate:
            mock_validate.return_value = {"approved": True}
            result = self._run()

        assert result == {"continue": True}, (
            f"Subagent edit with INTENT must pass through to Stage 2 and be "
            f"approved; got: {result}"
        )
        mock_validate.assert_called_once()
        _, kwargs = mock_validate.call_args
        assert "INTENT:" in kwargs.get("current_message_override", ""), (
            "The subagent transcript's flushed INTENT text must reach "
            "validate_intent_and_code as the current_message_override."
        )
