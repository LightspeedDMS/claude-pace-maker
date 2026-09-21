"""
Issue #139 regression tests: the Write/Edit gate is the twin of issue #93's
danger-bash deadlock. ``get_current_turn_message_for_validation`` returns
``None`` for both "not_found" and "stale" (distinguished via
``_diagnostics["outcome"]``/``_diagnostics["stale_text"]``), but the
Write/Edit gate's ``if current_message_override is None:`` branch blocked on
EVERY ``None`` unconditionally. The block message instructs a byte-identical
re-issue; that re-issue's own turn is often still not flushed within the
window, but the ORIGINAL attempt is now one turn back -- which resolves to
"stale", not "found". Since the gate never consumed "stale", the re-issue
was blocked again with the SAME message, producing an infinite deadlock
(live evidence: 5 consecutive 30s blocks on a one-line Edit).

Fix: mirror the danger-bash gate's issue #93 handling in the Write/Edit gate
-- accept ``outcome == "stale"`` by using ``stale_text`` as the
``current_message_override`` and falling through to normal Stage 1/2
validation, instead of treating it as "transcript not ready". ``not_found``
(or anything else) is still blocked, unchanged, now with ``outcome`` recorded
in blockage telemetry.

MOCKING RATIONALE (mirrors tests/test_issue_93_danger_bash_anchor.py and
tests/test_intent_validation_failclosed_race.py)
============================================================================
Most tests isolate the HOOK's reaction to each (anchor-string, outcome) pair
by mocking ``pacemaker.hook.get_current_turn_message_for_validation``
directly -- the real retry-loop/outcome-disambiguation algorithm is already
covered exhaustively in ``tests/test_issue_93_danger_bash_anchor.py`` and
``tests/test_transcript_staleness_fix.py`` (shared code, not duplicated
here). Two tests exercise the REAL transcript_reader algorithm end-to-end
(with a faked monotonic clock so the retry loop's ceiling costs zero real
wall-clock time) to prove the wiring holds against real transcript content,
not just a mock of the boundary.

Stage 2 (LLM review) is mocked at ``pacemaker.inference.resolve_and_call_with_reviewer``
-- the namespace ``_call_stage2_validation`` actually imports from (per this
project's CLAUDE.md mocking-namespace guidance) -- so no real codex/gemini/
claude call is ever made.
"""

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, patch

# PACEMAKER_TEST_MODE must be set before any pacemaker import.
os.environ.setdefault("PACEMAKER_TEST_MODE", "1")

from pacemaker import database  # noqa: E402


# ---------------------------------------------------------------------------
# JSONL transcript builder helpers (mirrors test_issue_93_danger_bash_anchor.py)
# ---------------------------------------------------------------------------


def _asst(request_id: Optional[str], block: dict) -> dict:
    entry = {"message": {"role": "assistant", "content": [block]}}
    if request_id is not None:
        entry["requestId"] = request_id
    return entry


def _text_block(t: str) -> dict:
    return {"type": "text", "text": t}


def _tool_use_block(name: str, inp: dict, tool_id: str = "toolu_default") -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": inp}


def _tool_result_entry(text: str, tool_use_id: str = "toolu_default") -> dict:
    return {
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": text}
            ],
        }
    }


def _write_transcript(lines: List[dict], path: str) -> str:
    with open(path, "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return path


# Deliberately NOT under a core-path segment (src/lib/code/core/source/
# libraries/kernel/app/routes/services/internal) and with no project marker
# file above it in a tmp dir -- keeps Stage 1 from also requiring a "Test
# coverage:" declaration, so the fixtures below can focus purely on the
# outcome-branching behavior under test.
NONCORE_FILE = "/tmp/pacemaker_issue139_scratch/version_bump.py"
NEW_CONTENT = "__version__ = '1.2.4'\n"
VALID_INTENT = "INTENT: Bump the version string in version_bump.py for the release.\n"


def _make_hook_stdin(
    tool_name: str, file_path: str, content: str, transcript_path: str
) -> str:
    if tool_name == "Write":
        tool_input = {"file_path": file_path, "content": content}
    else:
        tool_input = {"file_path": file_path, "new_string": content}
    return json.dumps(
        {
            "session_id": "test-session-139",
            "transcript_path": transcript_path,
            "tool_name": tool_name,
            "tool_input": tool_input,
        }
    )


def _config_enabled() -> dict:
    # hook_model deliberately NOT "auto"/"sonnet"/"opus"/"haiku": those
    # trigger validate_intent_and_code's SDK-availability fail-closed gate
    # (Claude Agent SDK is not importable in this dev/test environment,
    # SDK_AVAILABLE=False), which would short-circuit before Stage 2 is
    # ever reached regardless of how resolve_and_call_with_reviewer is
    # mocked below. "codex" routes through the SDK-independent inference
    # path, same as other Write/Edit Stage-2 tests in this project.
    return {
        "enabled": True,
        "intent_validation_enabled": True,
        "tdd_enabled": True,
        "danger_bash_enabled": False,
        "hook_model": "codex",
    }


class _DbHarness:
    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "usage.db")
        self.transcript = os.path.join(self.tmp_dir, "transcript.jsonl")
        Path(self.transcript).write_text("")
        database.initialize_database(self.db_path)

    def teardown_method(self):
        import shutil

        shutil.rmtree(self.tmp_dir, ignore_errors=True)


def _mock_anchor(return_value, outcome: str, stale_text: Optional[str] = None):
    """Side_effect callable for
    pacemaker.hook.get_current_turn_message_for_validation that populates
    _diagnostics exactly as the real function would on give-up/success --
    mirrors _mock_anchor in test_issue_93_danger_bash_anchor.py."""

    def _fn(
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
            _diagnostics["elapsed_seconds"] = 0.01
            _diagnostics["outcome"] = outcome
            if stale_text is not None:
                _diagnostics["stale_text"] = stale_text
        return return_value

    return _fn


# ---------------------------------------------------------------------------
# Requirement 1: not_found -> deferred block, unchanged, with outcome telemetry
# ---------------------------------------------------------------------------


class TestNotFoundStillBlocksWithOutcomeTelemetry(_DbHarness):
    def _run(self, tool_name: str) -> dict:
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _make_hook_stdin(
            tool_name, NONCORE_FILE, NEW_CONTENT, self.transcript
        )
        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                side_effect=_mock_anchor(None, "not_found"),
            ),
        ):
            return run_pre_tool_hook()

    def test_not_found_blocks_for_write(self):
        result = self._run("Write")
        assert (
            result.get("decision") == "block"
        ), f"Expected fail-closed block for not_found; got: {result}"
        reason = result.get("reason", "").lower()
        assert "re-issue" in reason
        assert "transcript" in reason

    def test_not_found_blocks_for_edit(self):
        result = self._run("Edit")
        assert result.get("decision") == "block"

    def test_not_found_blockage_details_include_outcome(self):
        self._run("Write")
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT details FROM blockage_events "
                "WHERE category = 'intent_validation_deferred'"
            ).fetchall()
        finally:
            conn.close()
        assert rows, "Expected an intent_validation_deferred blockage event"
        details = json.loads(rows[0][0])
        assert (
            details.get("outcome") == "not_found"
        ), f"Blockage details must record outcome=not_found; got: {details}"


# ---------------------------------------------------------------------------
# Requirement 2: byte-identical re-issue resolving to "stale" -> NOT a
# deferred block; proceeds to normal Stage 1/2 validation.
# ---------------------------------------------------------------------------


class TestStaleAcceptedProceedsToValidation(_DbHarness):
    def test_stale_with_intent_proceeds_and_approves(self):
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _make_hook_stdin(
            "Write", NONCORE_FILE, NEW_CONTENT, self.transcript
        )
        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                side_effect=_mock_anchor(None, "stale", stale_text=VALID_INTENT),
            ),
            patch(
                "pacemaker.inference.resolve_and_call_with_reviewer",
                return_value=("APPROVED", "test-reviewer"),
            ) as mock_reviewer,
        ):
            result = run_pre_tool_hook()

        assert result == {"continue": True}, (
            f"Stale turn WITH INTENT must be accepted and proceed to "
            f"Stage 2 (mocked APPROVED); got: {result}"
        )
        mock_reviewer.assert_called_once()

    def test_stale_with_intent_records_no_deferred_blockage(self):
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _make_hook_stdin(
            "Write", NONCORE_FILE, NEW_CONTENT, self.transcript
        )
        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                side_effect=_mock_anchor(None, "stale", stale_text=VALID_INTENT),
            ),
            patch(
                "pacemaker.inference.resolve_and_call_with_reviewer",
                return_value=("APPROVED", "test-reviewer"),
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
            f"A stale match WITH INTENT must never be recorded as the "
            f"'transcript not yet flushed' race; got: {rows}"
        )

    def test_stale_uses_stale_text_as_validated_message(self):
        """The stale_text itself (not some other value) must be what Stage 2
        actually receives -- locks in that hook.py wires stale_text through,
        not just that the gate happens to approve."""
        from pacemaker.hook import run_pre_tool_hook

        distinctive_intent = (
            "INTENT: Bump the version string for the release, marker "
            "DISTINCTIVE_STALE_TOKEN_139.\n"
        )
        stdin_payload = _make_hook_stdin(
            "Write", NONCORE_FILE, NEW_CONTENT, self.transcript
        )
        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                side_effect=_mock_anchor(None, "stale", stale_text=distinctive_intent),
            ),
            patch(
                "pacemaker.intent_validator.validate_intent_and_code"
            ) as mock_validate,
        ):
            mock_validate.return_value = {"approved": True}
            result = run_pre_tool_hook()

        assert result == {"continue": True}
        mock_validate.assert_called_once()
        _, kwargs = mock_validate.call_args
        assert kwargs.get("current_message_override") == distinctive_intent, (
            "hook.py must pass the stale turn's own stale_text as "
            f"current_message_override; got: {kwargs.get('current_message_override')!r}"
        )

    def test_stale_without_intent_blocks_as_ordinary_stage1_not_deferred(self):
        """A stale match whose stale_text has NO INTENT marker (the
        transcript_reader gate already excludes leaked sibling-tool content,
        so this happens when the assistant's own prose genuinely had none)
        must be an ordinary Stage-1 rejection, never re-labeled as the
        'transcript not ready' race.

        The transcript is deliberately NON-EMPTY (unlike an earlier revision
        of this test, which used a blank transcript file -- a trivial case
        where the real n-back function returns [] regardless of the
        messages=[] override under test). Real prose with no INTENT marker
        proves the override actually engages against real content, not just
        an already-empty fallback."""
        from pacemaker.hook import run_pre_tool_hook

        _write_transcript(
            [
                _asst("req_prior1", _text_block("Looking at the file now.")),
                _asst("req_prior2", _text_block("Getting ready to write it.")),
            ],
            self.transcript,
        )

        stdin_payload = _make_hook_stdin(
            "Write", NONCORE_FILE, NEW_CONTENT, self.transcript
        )
        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                side_effect=_mock_anchor(None, "stale", stale_text=""),
            ),
        ):
            result = run_pre_tool_hook()

        assert result.get("decision") == "block"
        reason = result.get("reason", "")
        assert "INTENT" in reason
        assert "transcript timing race" not in reason.lower(), (
            f"A stale match without INTENT must be a real Stage-1 "
            f"rejection, never the deferred-race message; got: {reason!r}"
        )

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute("SELECT category FROM blockage_events").fetchall()
        finally:
            conn.close()
        categories = {r[0] for r in rows}
        assert "intent_validation" in categories
        assert "intent_validation_deferred" not in categories, (
            f"Stale-without-INTENT must never be recorded under the "
            f"deferred category; got categories: {categories}"
        )


# ---------------------------------------------------------------------------
# Requirement 3 (real transcript, security-relevant): INTENT text that only
# appears inside the WRITTEN FILE CONTENT (the tool_use's own content/
# new_string field), never in the assistant's own prose text, must NOT
# satisfy Stage 1 via the stale path -- mirrors
# TestSecurityFix1StaleTextGatedOnActualText / ...Phase2NotCalled in
# test_issue_93_danger_bash_anchor.py, but for the Write/Edit gate and using
# the REAL (unmocked) transcript_reader algorithm end-to-end through
# run_pre_tool_hook.
# ---------------------------------------------------------------------------


class TestIntentOnlyInFileContentNeverSatisfiesStage1(_DbHarness):
    def test_intent_only_in_written_content_blocks_stage1_not_deferred(self):
        from pacemaker.hook import run_pre_tool_hook

        fake_intent_in_content = (
            "def f():\n"
            "    # INTENT: fake-marker-embedded-in-file-content, not a real "
            "declaration\n"
            "    pass\n"
        )

        # Prior (now stale) attempt: assistant prose has NO INTENT marker;
        # the Write tool_use's own `content` field happens to contain an
        # INTENT:-looking string (e.g. the file being written is itself
        # about intent validation). Already has its own tool_result, so a
        # byte-identical re-issue resolves to "stale".
        _write_transcript(
            [
                _asst("req_A", _text_block("Writing the file now.")),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {
                            "file_path": NONCORE_FILE,
                            "content": fake_intent_in_content,
                        },
                        "toolu_A",
                    ),
                ),
                _tool_result_entry("done", tool_use_id="toolu_A"),
            ],
            self.transcript,
        )

        stdin_payload = _make_hook_stdin(
            "Write", NONCORE_FILE, fake_intent_in_content, self.transcript
        )

        # Fake monotonic clock: the fixture is static, so the real retry
        # loop cannot ever transition from "stale" to "found" -- it retries
        # through the full ceiling. Faking time avoids a real multi-second
        # sleep in the test suite (mirrors
        # test_bounded_by_reduced_ceiling_against_real_retry_loop in
        # test_issue_93_danger_bash_anchor.py).
        fake_now = [0.0]

        def fake_monotonic():
            return fake_now[0]

        def fake_sleep(seconds):
            fake_now[0] += seconds

        # current_message_override resolves to "" (stale_text, correctly
        # gated -- no INTENT in the turn's own TEXT). Before the finding-#2
        # fix, Stage 1 in intent_validator.py did `current_message_override
        # or extract_current_assistant_message(messages, ...)` -- an empty
        # string is falsy, so it fell back to the REAL (unmocked here, on
        # purpose) n-back rescue path, which renders the newest message
        # WITH its full tool content (get_last_n_messages_for_validation's
        # own documented behavior) -- i.e. exactly `fake_intent_in_content`
        # above, leaking the fake INTENT and wrongly passing Stage 1. The
        # fix (hook.py's stale-empty branch overrides `messages = []`
        # before calling validate_intent_and_code) makes the fallback
        # resolve to "" regardless of what the real transcript contains, so
        # this test deliberately does NOT mock
        # get_last_n_messages_for_validation -- it must exercise the real
        # bypass this finding describes, not a sanitized substitute.
        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch("pacemaker.transcript_reader.time.sleep", fake_sleep),
            patch("pacemaker.transcript_reader.time.monotonic", fake_monotonic),
            patch(
                "pacemaker.inference.resolve_and_call_with_reviewer"
            ) as mock_reviewer,
        ):
            result = run_pre_tool_hook()

        assert result.get("decision") == "block", (
            f"INTENT text present ONLY inside written file content must "
            f"never satisfy Stage 1; got: {result}"
        )
        reason = result.get("reason", "")
        assert "INTENT" in reason
        assert "transcript timing race" not in reason.lower(), (
            f"Must be an ordinary Stage-1 rejection, not the deferred-race "
            f"message; got: {reason!r}"
        )
        mock_reviewer.assert_not_called()

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute("SELECT category FROM blockage_events").fetchall()
        finally:
            conn.close()
        categories = {r[0] for r in rows}
        assert "intent_validation" in categories
        assert "intent_validation_deferred" not in categories


# ---------------------------------------------------------------------------
# Code-review finding #2/#3 follow-up (real transcript, unmocked
# get_last_n_messages_for_validation AND get_current_turn_message_for_validation):
# an UNRELATED, genuinely-declared INTENT two logical turns back (naming the
# target file) must never rescue a stale match whose OWN turn has no INTENT.
# Before the finding-#2 fix, intent_validator's
# `current_message_override or extract_current_assistant_message(messages, ...)`
# fallback treated stale_text="" as falsy and fell through to
# extract_current_assistant_message, which -- per its own docstring --
# checks messages[-2] for an INTENT mentioning the file and merges it in.
# ---------------------------------------------------------------------------


class TestStaleEmptyDoesNotRescueFromUnrelatedNBackIntent(_DbHarness):
    def test_unrelated_two_turns_back_intent_does_not_rescue_stale_empty(self):
        from pacemaker.hook import run_pre_tool_hook

        # Turn A (2 logical turns back): a genuine, real INTENT declaration
        # that names the target file -- exactly the shape
        # extract_current_assistant_message's messages[-2] rescue looks for.
        # Turn B (1 back, the STALE match for the current re-issue): matches
        # the current tool_input byte-for-byte, already has its own
        # tool_result, but its OWN prose has no INTENT marker at all.
        _write_transcript(
            [
                _asst("req_A", _text_block(VALID_INTENT)),
                _asst("req_B", _text_block("Applying the edit now.")),
                _asst(
                    "req_B",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
                        "toolu_B",
                    ),
                ),
                _tool_result_entry("blocked", tool_use_id="toolu_B"),
            ],
            self.transcript,
        )

        stdin_payload = _make_hook_stdin(
            "Write", NONCORE_FILE, NEW_CONTENT, self.transcript
        )

        # Fake monotonic clock: the fixture is static (the current re-issue's
        # own tool_use is deliberately absent), so the real retry loop pays
        # the real _WRITE_EDIT_STALE_GRACE_SECONDS (3.0s) grace wait before
        # accepting the "stale" outcome. Faking time avoids that real sleep
        # (mirrors TestIntentOnlyInFileContentNeverSatisfiesStage1 above and
        # test_bounded_by_reduced_ceiling_against_real_retry_loop in
        # tests/test_issue_93_danger_bash_anchor.py).
        fake_now = [0.0]

        def fake_monotonic():
            return fake_now[0]

        def fake_sleep(seconds):
            fake_now[0] += seconds

        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch("pacemaker.transcript_reader.time.sleep", fake_sleep),
            patch("pacemaker.transcript_reader.time.monotonic", fake_monotonic),
            patch(
                "pacemaker.inference.resolve_and_call_with_reviewer"
            ) as mock_reviewer,
        ):
            result = run_pre_tool_hook()

        assert result.get("decision") == "block", (
            f"An unrelated INTENT two turns back must never rescue a stale "
            f"match whose own turn declared none; got: {result}"
        )
        reason = result.get("reason", "")
        assert "INTENT" in reason
        assert "transcript timing race" not in reason.lower(), (
            f"Must be an ordinary Stage-1 rejection, not the deferred-race "
            f"message; got: {reason!r}"
        )
        mock_reviewer.assert_not_called()

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute("SELECT category FROM blockage_events").fetchall()
        finally:
            conn.close()
        categories = {r[0] for r in rows}
        assert "intent_validation" in categories
        assert "intent_validation_deferred" not in categories


# ---------------------------------------------------------------------------
# Requirement 2 (real transcript, end-to-end): reproduces the exact issue
# #139 deadlock scenario -- a prior identical attempt (WITH INTENT text) is
# present in the transcript, already blocked once (has its own tool_result),
# and the current byte-identical re-issue's own tool_use has NOT yet
# flushed. Proves the fix breaks the deadlock end-to-end against the real
# transcript_reader algorithm, not just a mocked boundary.
# ---------------------------------------------------------------------------


class TestRealTranscriptDeadlockRecovery(_DbHarness):
    def test_reissue_after_deferred_block_recovers_via_real_algorithm(self):
        from pacemaker.hook import run_pre_tool_hook

        # The ORIGINAL attempt: matching tool_use WITH INTENT in the
        # assistant's own text, already has its own tool_result (simulating
        # the prior deferred block).
        _write_transcript(
            [
                _asst("req_A", _text_block(VALID_INTENT)),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
                        "toolu_A",
                    ),
                ),
                _tool_result_entry("blocked", tool_use_id="toolu_A"),
                # NOTE: the byte-identical re-issue's own tool_use is
                # deliberately NOT in this transcript -- it represents the
                # not-yet-flushed CURRENT attempt being searched for.
            ],
            self.transcript,
        )

        stdin_payload = _make_hook_stdin(
            "Write", NONCORE_FILE, NEW_CONTENT, self.transcript
        )

        fake_now = [0.0]

        def fake_monotonic():
            return fake_now[0]

        def fake_sleep(seconds):
            fake_now[0] += seconds

        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch("pacemaker.transcript_reader.time.sleep", fake_sleep),
            patch("pacemaker.transcript_reader.time.monotonic", fake_monotonic),
            patch(
                "pacemaker.inference.resolve_and_call_with_reviewer",
                return_value=("APPROVED", "test-reviewer"),
            ) as mock_reviewer,
        ):
            result = run_pre_tool_hook()

        assert result == {"continue": True}, (
            f"The byte-identical re-issue must recover via the stale path "
            f"(the deadlock this issue exists to fix); got: {result}"
        )
        mock_reviewer.assert_called_once()

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT category FROM blockage_events "
                "WHERE category = 'intent_validation_deferred'"
            ).fetchall()
        finally:
            conn.close()
        assert not rows, (
            f"The recovered re-issue must not itself be recorded as a "
            f"deferred block; got: {rows}"
        )


# ---------------------------------------------------------------------------
# Requirement 4: found path unchanged.
# ---------------------------------------------------------------------------


class TestFoundPathUnchanged(_DbHarness):
    def test_found_with_intent_proceeds_and_approves(self):
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _make_hook_stdin(
            "Write", NONCORE_FILE, NEW_CONTENT, self.transcript
        )
        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                side_effect=_mock_anchor(VALID_INTENT, "found"),
            ),
            patch(
                "pacemaker.inference.resolve_and_call_with_reviewer",
                return_value=("APPROVED", "test-reviewer"),
            ) as mock_reviewer,
        ):
            result = run_pre_tool_hook()

        assert result == {"continue": True}
        mock_reviewer.assert_called_once()

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute("SELECT category FROM blockage_events").fetchall()
        finally:
            conn.close()
        assert (
            not rows
        ), f"A clean found+APPROVED path must record no blockage; got: {rows}"

    def test_found_empty_string_no_intent_blocks_stage1_not_deferred(self):
        """found (turn present, no INTENT marker in text) -> "" override ->
        ordinary Stage-1 block, never the deferred race path -- unchanged
        behavior, locked in for regression."""
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _make_hook_stdin(
            "Write", NONCORE_FILE, NEW_CONTENT, self.transcript
        )
        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                side_effect=_mock_anchor("", "found"),
            ),
        ):
            result = run_pre_tool_hook()

        assert result.get("decision") == "block"
        assert "INTENT" in result.get("reason", "")

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute("SELECT category FROM blockage_events").fetchall()
        finally:
            conn.close()
        categories = {r[0] for r in rows}
        assert "intent_validation" in categories
        assert "intent_validation_deferred" not in categories
