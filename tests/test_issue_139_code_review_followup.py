"""
Follow-up regression tests for the code-review findings on the issue #139
fix (Write/Edit gate stale-outcome acceptance, see
tests/test_issue_139_write_edit_stale_accept.py for the original fix).

Covers (numbering matches the review):

1. HIGH -- Edit match not byte-exact. ``_tool_input_matches`` compared Edit
   tool_use entries on file_path+new_string only, ignoring old_string and
   replace_all. A stale prior turn declaring intent for one edit (e.g.
   "delete dead helper foo()") could be accepted for a DIFFERENT edit to the
   same file with the same new_string but a different old_string (e.g.
   replacing critical_auth() instead), because Stage 2 only ever sees
   new_string, never old_string. Fixed by also requiring old_string and
   bool(replace_all) equality for Edit.

4. MEDIUM -- stale-recovery latency. A stale match still burned the full
   _max_wait_seconds ceiling (30s for Write/Edit) before being accepted,
   even though further retrying past a short grace window buys nothing (the
   Write/Edit gate accepts the stale match unconditionally once diagnosed).
   Fixed via an additive, opt-in ``_stale_grace_seconds`` parameter on
   ``get_current_turn_message_for_validation`` -- default None preserves
   existing behavior for every other caller (including the danger-bash
   gate, which does not pass it).

6. LOW -- telemetry on stale acceptance was log_debug only. Now log_info,
   including attempts/elapsed_seconds and an explicit "stale_accepted"
   marker in the message.

Findings 2/3 (stale-empty must not fall back to intent_validator's n-back
rescue) are covered in tests/test_issue_139_write_edit_stale_accept.py
(TestStaleAcceptedProceedsToValidation / TestIntentOnlyInFileContentNeverSatisfiesStage1),
which this follow-up modifies directly rather than duplicating here.
Finding 5 is a docs/comment-only fix with no behavioral test. Finding 7 is
explicitly out of scope per the coordinator.
"""

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, patch

os.environ.setdefault("PACEMAKER_TEST_MODE", "1")

from pacemaker import database  # noqa: E402


# ---------------------------------------------------------------------------
# Shared helpers (mirrors tests/test_issue_139_write_edit_stale_accept.py)
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


NONCORE_FILE = "/tmp/pacemaker_issue139_scratch/auth_module.py"


def _make_hook_stdin(
    tool_name: str, file_path: str, tool_input_extra: dict, transcript_path: str
) -> str:
    tool_input = {"file_path": file_path, **tool_input_extra}
    return json.dumps(
        {
            "session_id": "test-session-139-followup",
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


# ---------------------------------------------------------------------------
# Finding 1: Edit matching must be byte-exact (old_string + replace_all)
# ---------------------------------------------------------------------------


class TestEditMatchRequiresOldStringAndReplaceAll:
    """Direct tests against _find_turn_matching_tool_input / _tool_input_matches
    (mirrors tests/test_issue_93_danger_bash_anchor.py Section A style)."""

    def test_different_old_string_same_new_string_does_not_match(self, tmp_path):
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        transcript = str(tmp_path / "t.jsonl")
        _write_transcript(
            [
                _asst(
                    "req_A",
                    _text_block("INTENT: delete dead helper foo() from auth_module.py"),
                ),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Edit",
                        {
                            "file_path": NONCORE_FILE,
                            "old_string": "def foo(): pass\n",
                            "new_string": "",
                        },
                        "toolu_A",
                    ),
                ),
            ],
            transcript,
        )
        outcome: dict = {}
        result = _find_turn_matching_tool_input(
            transcript,
            {
                "file_path": NONCORE_FILE,
                "old_string": "def critical_auth(): check()\n",
                "new_string": "",
            },
            "Edit",
            _outcome=outcome,
        )
        assert result is None
        assert outcome.get("outcome") == "not_found", (
            "A different old_string (different code being deleted) must "
            f"NOT be treated as a match, even with identical new_string; "
            f"got outcome={outcome.get('outcome')!r}"
        )

    def test_different_replace_all_same_strings_does_not_match(self, tmp_path):
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        transcript = str(tmp_path / "t.jsonl")
        _write_transcript(
            [
                _asst("req_A", _text_block("INTENT: rename var in auth_module.py")),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Edit",
                        {
                            "file_path": NONCORE_FILE,
                            "old_string": "x",
                            "new_string": "y",
                            "replace_all": False,
                        },
                        "toolu_A",
                    ),
                ),
            ],
            transcript,
        )
        outcome: dict = {}
        result = _find_turn_matching_tool_input(
            transcript,
            {
                "file_path": NONCORE_FILE,
                "old_string": "x",
                "new_string": "y",
                "replace_all": True,
            },
            "Edit",
            _outcome=outcome,
        )
        assert result is None
        assert outcome.get("outcome") == "not_found", (
            f"Differing replace_all must NOT be treated as a match; got "
            f"outcome={outcome.get('outcome')!r}"
        )

    def test_same_old_string_new_string_and_replace_all_matches(self, tmp_path):
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        transcript = str(tmp_path / "t.jsonl")
        _write_transcript(
            [
                _asst("req_A", _text_block("INTENT: rename var in auth_module.py")),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Edit",
                        {
                            "file_path": NONCORE_FILE,
                            "old_string": "x",
                            "new_string": "y",
                            "replace_all": True,
                        },
                        "toolu_A",
                    ),
                ),
            ],
            transcript,
        )
        outcome: dict = {}
        result = _find_turn_matching_tool_input(
            transcript,
            {
                "file_path": NONCORE_FILE,
                "old_string": "x",
                "new_string": "y",
                "replace_all": True,
            },
            "Edit",
            _outcome=outcome,
        )
        assert result is not None
        assert outcome.get("outcome") == "found"
        assert "INTENT:" in result

    def test_missing_replace_all_on_both_sides_still_matches(self, tmp_path):
        """bool(None) == bool(None) -- omitting replace_all entirely (the
        common case; Claude Code only sends it when explicitly True) must
        not break the ordinary found path."""
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        transcript = str(tmp_path / "t.jsonl")
        _write_transcript(
            [
                _asst("req_A", _text_block("INTENT: rename var in auth_module.py")),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Edit",
                        {
                            "file_path": NONCORE_FILE,
                            "old_string": "x",
                            "new_string": "y",
                        },
                        "toolu_A",
                    ),
                ),
            ],
            transcript,
        )
        outcome: dict = {}
        result = _find_turn_matching_tool_input(
            transcript,
            {"file_path": NONCORE_FILE, "old_string": "x", "new_string": "y"},
            "Edit",
            _outcome=outcome,
        )
        assert result is not None
        assert outcome.get("outcome") == "found"


class TestEndToEndDifferentOldStringNotFoundDeferredBlock(_DbHarness):
    """Reproduces the reviewer's exact end-to-end scenario: a prior stale
    turn declared intent to delete foo(), the current (unflushed) Edit
    targets a DIFFERENT function (critical_auth()) with the same
    (empty) new_string. Must be not_found -> deferred block, NEVER a stale
    accept -> Stage 2 (which would only ever see new_string="" and could
    never catch the mismatch)."""

    def test_different_old_string_is_not_found_not_stale(self):
        from pacemaker.hook import run_pre_tool_hook

        _write_transcript(
            [
                _asst("req_A", _text_block("INTENT: delete dead helper foo()")),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Edit",
                        {
                            "file_path": NONCORE_FILE,
                            "old_string": "def foo(): pass\n",
                            "new_string": "",
                        },
                        "toolu_A",
                    ),
                ),
                _tool_result_entry("blocked", tool_use_id="toolu_A"),
            ],
            self.transcript,
        )

        stdin_payload = _make_hook_stdin(
            "Edit",
            NONCORE_FILE,
            {"old_string": "def critical_auth(): check()\n", "new_string": ""},
            self.transcript,
        )

        # Fake monotonic clock: the fixture never resolves to "found" (the
        # current re-issue's own tool_use is deliberately absent), so the
        # real retry loop runs through the full ~30s not_found ceiling.
        # Faking time avoids a real multi-second sleep here (mirrors
        # TestIntentOnlyInFileContentNeverSatisfiesStage1 in
        # tests/test_issue_139_write_edit_stale_accept.py and
        # test_bounded_by_reduced_ceiling_against_real_retry_loop in
        # tests/test_issue_93_danger_bash_anchor.py) -- without it, this
        # test exceeds the test harness's per-test timeout
        # (scripts/run_tests.sh's --timeout=15).
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
            f"A different old_string must never be silently accepted via "
            f"the stale path; got: {result}"
        )
        reason = result.get("reason", "")
        assert "transcript timing race" in reason.lower(), (
            f"Must be the not_found deferred-race block, not a Stage-1/2 "
            f"rejection (which would imply the mismatched turn was "
            f"consulted); got: {reason!r}"
        )
        mock_reviewer.assert_not_called()

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT details FROM blockage_events "
                "WHERE category = 'intent_validation_deferred'"
            ).fetchall()
        finally:
            conn.close()
        assert rows
        details = json.loads(rows[0][0])
        assert details.get("outcome") == "not_found", (
            f"Different old_string must classify as not_found, never "
            f"stale; got: {details}"
        )


# ---------------------------------------------------------------------------
# Finding 6: telemetry on stale acceptance is log_info, includes
# attempts/elapsed_seconds, and an explicit "stale_accepted" marker.
# ---------------------------------------------------------------------------


def _mock_anchor(return_value, outcome: str, stale_text: Optional[str] = None):
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
            _diagnostics["attempts"] = 4
            _diagnostics["elapsed_seconds"] = 3.007
            _diagnostics["outcome"] = outcome
            if stale_text is not None:
                _diagnostics["stale_text"] = stale_text
        return return_value

    return _fn


VALID_INTENT_FOLLOWUP = (
    "INTENT: Bump the version string in auth_module.py for the release.\n"
)


class TestStaleAcceptanceTelemetry(_DbHarness):
    def test_stale_acceptance_logs_info_with_attempts_elapsed_and_marker(self):
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _make_hook_stdin(
            "Write", NONCORE_FILE, {"content": "x = 1\n"}, self.transcript
        )
        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                side_effect=_mock_anchor(
                    None, "stale", stale_text=VALID_INTENT_FOLLOWUP
                ),
            ),
            patch(
                "pacemaker.inference.resolve_and_call_with_reviewer",
                return_value=("APPROVED", "test-reviewer"),
            ),
            patch("pacemaker.hook.log_info") as mock_log_info,
        ):
            result = run_pre_tool_hook()

        assert result == {"continue": True}
        stale_calls = [
            c
            for c in mock_log_info.call_args_list
            if "stale_accepted" in str(c.args) or "stale_accepted" in str(c.kwargs)
        ]
        assert stale_calls, (
            f"Expected a log_info call marking outcome=stale_accepted; "
            f"got calls: {mock_log_info.call_args_list}"
        )
        logged_message = stale_calls[0].args[-1]
        assert "4" in logged_message, (
            f"Expected attempts (4) in the stale-acceptance log message; "
            f"got: {logged_message!r}"
        )
        assert "3.007" in logged_message, (
            f"Expected elapsed_seconds (3.007) in the stale-acceptance log "
            f"message; got: {logged_message!r}"
        )


# ---------------------------------------------------------------------------
# Finding 4: stale-recovery latency. An opt-in _stale_grace_seconds
# parameter on get_current_turn_message_for_validation lets a caller (the
# Write/Edit gate) stop retrying once a "stale" outcome has persisted for at
# least that many REAL seconds, instead of paying the full _max_wait_seconds
# ceiling. Default None preserves existing behavior for every other caller.
# ---------------------------------------------------------------------------


def _fake_clock():
    fake_now = [0.0]

    def fake_monotonic():
        return fake_now[0]

    def fake_sleep(seconds):
        fake_now[0] += seconds

    return fake_now, fake_monotonic, fake_sleep


class TestStaleGraceEarlyReturn:
    """Direct tests against get_current_turn_message_for_validation with a
    faked monotonic clock (mirrors
    test_bounded_by_reduced_ceiling_against_real_retry_loop in
    tests/test_issue_93_danger_bash_anchor.py) -- a STALE-only fixture (the
    matching tool_use always has its own tool_result, so the real algorithm
    can never transition from "stale" to "found") that never resolves."""

    def _stale_only_transcript(self, tmp_path) -> str:
        transcript = str(tmp_path / "t.jsonl")
        _write_transcript(
            [
                _asst("req_A", _text_block("Applying the edit now.")),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": "x = 1\n"},
                        "toolu_A",
                    ),
                ),
                _tool_result_entry("blocked", tool_use_id="toolu_A"),
            ],
            transcript,
        )
        return transcript

    def test_default_none_retries_through_full_ceiling_unchanged(self, tmp_path):
        """Regression lock: omitting _stale_grace_seconds (every existing
        caller, including the danger-bash gate) must retry through the full
        ceiling exactly as before -- this finding is purely additive."""
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = self._stale_only_transcript(tmp_path)
        fake_now, fake_monotonic, fake_sleep = _fake_clock()

        with (
            patch("pacemaker.transcript_reader.time.sleep", fake_sleep),
            patch("pacemaker.transcript_reader.time.monotonic", fake_monotonic),
        ):
            diagnostics: dict = {}
            result = get_current_turn_message_for_validation(
                transcript,
                tool_input={"file_path": NONCORE_FILE, "content": "x = 1\n"},
                tool_name="Write",
                _max_wait_seconds=30.0,
                _diagnostics=diagnostics,
            )

        assert result is None
        assert diagnostics.get("outcome") == "stale"
        assert diagnostics.get("elapsed_seconds") >= 29.0, (
            f"Without _stale_grace_seconds, must retry through the full "
            f"~30s ceiling exactly as before this finding; got: {diagnostics}"
        )

    def test_grace_returns_early_once_elapsed_exceeds_grace(self, tmp_path):
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = self._stale_only_transcript(tmp_path)
        fake_now, fake_monotonic, fake_sleep = _fake_clock()

        with (
            patch("pacemaker.transcript_reader.time.sleep", fake_sleep),
            patch("pacemaker.transcript_reader.time.monotonic", fake_monotonic),
        ):
            diagnostics: dict = {}
            result = get_current_turn_message_for_validation(
                transcript,
                tool_input={"file_path": NONCORE_FILE, "content": "x = 1\n"},
                tool_name="Write",
                _max_wait_seconds=30.0,
                _stale_grace_seconds=3.0,
                _diagnostics=diagnostics,
            )

        assert result is None
        assert diagnostics.get("outcome") == "stale"
        assert diagnostics.get("stale_text") is not None
        assert 3.0 <= diagnostics.get("elapsed_seconds") < 10.0, (
            f"With _stale_grace_seconds=3.0 on a 30s ceiling, must return "
            f"shortly after the grace window, well before the full "
            f"ceiling; got: {diagnostics}"
        )

    def test_grace_does_not_affect_not_found_outcome(self, tmp_path):
        """Grace is scoped to "stale" only -- a genuinely not_found fixture
        must still retry through the full ceiling even with grace set."""
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = str(tmp_path / "t.jsonl")
        _write_transcript(
            [_asst("req_A", _text_block("unrelated, no matching tool_use"))],
            transcript,
        )
        fake_now, fake_monotonic, fake_sleep = _fake_clock()

        with (
            patch("pacemaker.transcript_reader.time.sleep", fake_sleep),
            patch("pacemaker.transcript_reader.time.monotonic", fake_monotonic),
        ):
            diagnostics: dict = {}
            result = get_current_turn_message_for_validation(
                transcript,
                tool_input={"file_path": NONCORE_FILE, "content": "x = 1\n"},
                tool_name="Write",
                _max_wait_seconds=30.0,
                _stale_grace_seconds=3.0,
                _diagnostics=diagnostics,
            )

        assert result is None
        assert diagnostics.get("outcome") == "not_found"
        assert diagnostics.get("elapsed_seconds") >= 29.0, (
            f"Grace must only shorten STALE outcomes, never not_found; "
            f"got: {diagnostics}"
        )

    def test_grace_found_still_returns_immediately(self, tmp_path):
        """A "found" outcome must still return immediately on the first
        attempt regardless of grace -- grace only ever shortens a wait, it
        never delays a success."""
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = str(tmp_path / "t.jsonl")
        _write_transcript(
            [
                _asst("req_A", _text_block(VALID_INTENT_FOLLOWUP)),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": "x = 1\n"},
                        "toolu_A",
                    ),
                ),
            ],
            transcript,
        )

        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": NONCORE_FILE, "content": "x = 1\n"},
            tool_name="Write",
            _max_wait_seconds=30.0,
            _stale_grace_seconds=3.0,
        )
        assert result is not None
        assert "INTENT:" in result


class TestWriteEditGatePassesStaleGrace(_DbHarness):
    """hook.py wiring: the Write/Edit gate passes a named
    _WRITE_EDIT_STALE_GRACE_SECONDS constant, and the not_found ceiling
    (_max_wait_seconds) is NOT lowered by this finding."""

    def test_gate_passes_stale_grace_kwarg(self):
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _make_hook_stdin(
            "Write", NONCORE_FILE, {"content": "x = 1\n"}, self.transcript
        )
        captured = {}

        def _capture(*args, **kwargs):
            captured.update(kwargs)
            if kwargs.get("_diagnostics") is not None:
                kwargs["_diagnostics"]["outcome"] = "not_found"
                kwargs["_diagnostics"]["attempts"] = 1
                kwargs["_diagnostics"]["elapsed_seconds"] = 0.01
            return None

        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                side_effect=_capture,
            ),
        ):
            run_pre_tool_hook()

        assert "_stale_grace_seconds" in captured, (
            "Write/Edit gate must explicitly pass _stale_grace_seconds "
            "(issue #139 finding #4)"
        )
        assert 0 < captured["_stale_grace_seconds"] < 10.0, (
            f"Expected a small grace window; got: "
            f"{captured['_stale_grace_seconds']}"
        )

    def test_not_found_ceiling_not_lowered(self):
        """The not_found ceiling (_max_wait_seconds) must remain governed
        by the pre-existing PRE_TOOL_ANCHOR_CAP_SECONDS/gate-deadline
        clamp -- this finding must not shrink it."""
        from pacemaker.hook import run_pre_tool_hook
        from pacemaker.constants import PRE_TOOL_ANCHOR_CAP_SECONDS

        stdin_payload = _make_hook_stdin(
            "Write", NONCORE_FILE, {"content": "x = 1\n"}, self.transcript
        )
        captured = {}

        def _capture(*args, **kwargs):
            captured.update(kwargs)
            if kwargs.get("_diagnostics") is not None:
                kwargs["_diagnostics"]["outcome"] = "not_found"
                kwargs["_diagnostics"]["attempts"] = 1
                kwargs["_diagnostics"]["elapsed_seconds"] = 0.01
            return None

        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                side_effect=_capture,
            ),
        ):
            run_pre_tool_hook()

        assert captured.get("_max_wait_seconds") is not None
        assert captured["_max_wait_seconds"] > PRE_TOOL_ANCHOR_CAP_SECONDS - 1.0, (
            f"The not_found ceiling must stay close to "
            f"PRE_TOOL_ANCHOR_CAP_SECONDS "
            f"({PRE_TOOL_ANCHOR_CAP_SECONDS}s), not be lowered by the "
            f"stale-grace finding; got: {captured.get('_max_wait_seconds')}"
        )
