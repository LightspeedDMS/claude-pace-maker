"""
Issue #141 / #148 regression tests: an assistant turn can carry NO visible
text block at all. This has THREE observed shapes (apiBlockIndex numbering
is contiguous, so a gap-free sequence proves there never was a text block):
  (a) `thinking` block(s) with non-empty text, then `tool_use` -- the model
      wrote its INTENT declaration only inside its own reasoning, which is
      summarized (not verbatim) and never shown to the user.
  (b) an EMPTY `thinking: ""` block, then `tool_use` -- no reasoning shown
      either.
  (c) `tool_use` alone, with no `thinking` block at all.
In all three shapes the gate is CORRECT to block (there is no visible
INTENT), but the pre-existing "you must declare INTENT:" message doesn't
say WHY, so the agent believes it already declared INTENT and loops.

Issue #141 shipped the notice only for shape (a) (gated on
`anchor_has_thinking` being true). Issue #148 is the follow-up: shapes (b)
and (c) got no notice either, because `anchor_has_thinking` is False for
both. The fix: the notice fires whenever `anchor_has_visible_text is
False`, regardless of `anchor_has_thinking` -- covering all three shapes.

This module covers the combined #141/#148 fix:

1. transcript_reader.py's `_find_turn_matching_tool_input` /
   `get_current_turn_message_for_validation` record the anchored turn's
   SHAPE in the existing `_outcome`/`_diagnostics` channel:
   `anchor_has_visible_text` (bool) and `anchor_has_thinking` (bool),
   scoped to the same requestId group `_merge_anchor_turn` already uses.
   Purely additive -- the return-value CONTRACT (None/""/str) is unchanged.
   Unaffected by #148 -- both flags were already computed correctly for all
   three shapes; #148 only fixed how the DOWNSTREAM gates (hook.py) combine
   them.
2. The Write/Edit Stage 1 "missing INTENT" block
   (intent_validator.validate_intent_and_code) and the danger-bash Phase 1
   "no INTENT" block (hook.py) both append a shared
   `transcript_reader.THINKING_ONLY_NOTICE` to their block reason whenever
   the anchored turn had NO visible text -- regardless of whether thinking
   was present, empty, or absent entirely (#148).
3. Thinking is NEVER accepted as an INTENT source. A turn with visible text
   (with or without INTENT) is unaffected; the notice is additive to an
   EXISTING block, it never changes whether a block occurs.
4. The gating boolean (hook.py's `_bash_no_visible_text`/
   `_write_edit_no_visible_text`, `validate_intent_and_code`'s
   `no_visible_text` parameter, and the `blockage_events.details`
   `"no_visible_text"` key) was renamed from `thinking_only` to
   `no_visible_text` to keep the telemetry meaning honest post-#148 -- it
   no longer implies thinking was present. No external consumer
   (claude-usage-reporting) reads this key (verified by grep), so no
   backward-compat shim was needed.

MOCKING RATIONALE (mirrors tests/test_issue_139_write_edit_stale_accept.py
and tests/test_issue_93_danger_bash_anchor.py)
============================================================================
Hook-level tests drive the REAL `run_pre_tool_hook` against REAL synthetic
JSONL transcripts through the REAL (unmocked) transcript_reader algorithm --
the point of this issue is the interaction between transcript shape and the
block message, so the anchor-resolution boundary itself must not be mocked
here. Stage 2 (LLM review) is mocked at
`pacemaker.inference.resolve_and_call_with_reviewer` -- the namespace
`_call_stage2_validation` actually imports from (per this project's
CLAUDE.md mocking-namespace guidance) -- so no real codex/gemini/claude call
is ever made. Danger-bash rule matching is mocked at
`pacemaker.danger_bash_rules.load_rules`/`match_command` (mirrors
test_issue_93's `_rules_patch`) so the test does not depend on the real
bundled YAML's regex set. A faked monotonic clock is used for the stale-path
test so the real retry/grace loop costs zero real wall-clock time.
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
# JSONL transcript builder helpers (mirrors test_issue_139_write_edit_stale_accept.py
# and test_issue_93_danger_bash_anchor.py) -- extended with `idx` so blocks
# carry an apiBlockIndex field, mirroring the real transcript shape from the
# issue's own evidence (thinking(0), tool_use(1) -- no gap, no text block).
# ---------------------------------------------------------------------------


def _asst(request_id: Optional[str], block: dict) -> dict:
    entry = {"message": {"role": "assistant", "content": [block]}}
    if request_id is not None:
        entry["requestId"] = request_id
    return entry


def _thinking_block(t: str, idx: int = 0) -> dict:
    return {"type": "thinking", "thinking": t, "apiBlockIndex": idx}


def _text_block(t: str, idx: int = 0) -> dict:
    return {"type": "text", "text": t, "apiBlockIndex": idx}


def _tool_use_block(
    name: str, inp: dict, tool_id: str = "toolu_default", idx: int = 1
) -> dict:
    return {
        "type": "tool_use",
        "id": tool_id,
        "name": name,
        "input": inp,
        "apiBlockIndex": idx,
    }


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


# Deliberately NOT under a core-path segment and with no project marker file
# above it in a tmp dir -- keeps Stage 1 from also requiring a "Test
# coverage:" declaration (mirrors test_issue_139's NONCORE_FILE rationale).
NONCORE_FILE = "/tmp/pacemaker_issue141_scratch/version_bump.py"
NEW_CONTENT = "__version__ = '1.2.5'\n"
VALID_INTENT = "INTENT: Bump the version string in version_bump.py for the release.\n"
NO_INTENT_TEXT = "Let me go ahead and make this edit now.\n"
THINKING_WITH_INTENT_LOOKING_TEXT = (
    "The user wants a version bump. INTENT: Bump version_bump.py's version "
    "string for the release.\n"
)


def _make_hook_stdin(
    tool_name: str, file_path: str, content: str, transcript_path: str
) -> str:
    tool_input = {"file_path": file_path, "content": content}
    return json.dumps(
        {
            "session_id": "test-session-141",
            "transcript_path": transcript_path,
            "tool_name": tool_name,
            "tool_input": tool_input,
        }
    )


def _config_write_edit() -> dict:
    # hook_model deliberately NOT "auto"/"sonnet"/"opus"/"haiku" (see
    # test_issue_139's identical rationale): those trigger the
    # SDK-availability fail-closed gate before Stage 2 is ever reached.
    return {
        "enabled": True,
        "intent_validation_enabled": True,
        "tdd_enabled": True,
        "danger_bash_enabled": False,
        "hook_model": "codex",
    }


def _config_danger_bash() -> dict:
    return {
        "enabled": True,
        "intent_validation_enabled": True,
        "danger_bash_enabled": True,
        "hook_model": "auto",
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
# Group A: transcript_reader unit tests for the new diagnostics flags.
# ---------------------------------------------------------------------------


class TestAnchorShapeExceptionLogsWarning:
    """Re-review nit (a): the defensive except handlers around the
    anchor-shape flag computation (both the found and stale paths of
    ``_find_turn_matching_tool_input``) must log via ``log_warning``, not
    ``log_debug`` -- a real bug in that path must surface in the normal
    log, not be silently swallowed at DEBUG level."""

    def test_found_path_exception_logs_warning_not_debug(self, tmp_path):
        from pacemaker import transcript_reader
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        transcript = _write_transcript(
            [
                _asst("req_A", _text_block(VALID_INTENT, 0)),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
                        "toolu_A",
                        1,
                    ),
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with (
            patch.object(
                transcript_reader,
                "_turn_has_thinking",
                side_effect=RuntimeError("boom"),
            ),
            patch.object(transcript_reader, "log_warning") as mock_warn,
            patch.object(transcript_reader, "log_debug") as mock_debug,
        ):
            outcome: dict = {}
            result = _find_turn_matching_tool_input(
                transcript,
                {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
                "Write",
                _outcome=outcome,
            )
        assert result is not None and result.startswith(VALID_INTENT), (
            f"the found outcome itself must be unaffected by the flag "
            f"computation failure; got: {result!r}"
        )
        assert outcome.get("outcome") == "found"
        # Issue #140 code-review re-review finding 1: anchor_prose_text is
        # now a plain dict/string read placed BEFORE this same try/except,
        # so it must survive the _turn_has_thinking raise this test
        # already injects (previously it was the LAST statement inside
        # the try, so the raise left the key entirely unset).
        assert outcome.get("anchor_prose_text") == VALID_INTENT, (
            f"anchor_prose_text must survive a _turn_has_thinking raise "
            f"on the found path; got outcome={outcome}"
        )
        mock_warn.assert_called_once()
        assert "anchor-shape" in mock_warn.call_args[0][1]
        mock_debug.assert_not_called()

    def test_stale_path_exception_logs_warning_not_debug(self, tmp_path):
        from pacemaker import transcript_reader
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        transcript = _write_transcript(
            [
                _asst("req_A", _text_block(VALID_INTENT, 0)),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
                        "toolu_A",
                        1,
                    ),
                ),
                _tool_result_entry("done", tool_use_id="toolu_A"),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with (
            patch.object(
                transcript_reader,
                "_turn_has_thinking",
                side_effect=RuntimeError("boom"),
            ),
            patch.object(transcript_reader, "log_warning") as mock_warn,
            patch.object(transcript_reader, "log_debug") as mock_debug,
        ):
            outcome: dict = {}
            result = _find_turn_matching_tool_input(
                transcript,
                {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
                "Write",
                _outcome=outcome,
            )
        assert result is None, "stale match still returns None (unchanged contract)"
        assert outcome.get("outcome") == "stale", (
            f"the stale outcome itself must be unaffected by the flag "
            f"computation failure; got: {outcome}"
        )
        # Issue #140 code-review re-review finding 1: anchor_prose_text
        # must survive this same _turn_has_thinking raise on the stale
        # path too (previously unset, which could cause an incorrect
        # n-back fallback in hook.py, violating the ANCHOR-ONLY invariant).
        assert outcome.get("anchor_prose_text") == VALID_INTENT, (
            f"anchor_prose_text must survive a _turn_has_thinking raise "
            f"on the stale path; got outcome={outcome}"
        )
        mock_warn.assert_called_once()
        assert "anchor-shape" in mock_warn.call_args[0][1]
        mock_debug.assert_not_called()


class TestCopyAnchorShapeFlagsHelper:
    """Code review follow-up item 5: the 3x copy-pasted
    `_diagnostics["anchor_has_visible_text"]`/`["anchor_has_thinking"]`
    block inside `get_current_turn_message_for_validation` was deduped
    into one small helper. Direct unit tests on the helper itself."""

    def test_copies_both_keys_from_attempt_outcome(self):
        from pacemaker.transcript_reader import _copy_anchor_shape_flags

        diagnostics: dict = {}
        attempt_outcome = {
            "anchor_has_visible_text": True,
            "anchor_has_thinking": False,
        }
        _copy_anchor_shape_flags(diagnostics, attempt_outcome)
        assert diagnostics["anchor_has_visible_text"] is True
        assert diagnostics["anchor_has_thinking"] is False

    def test_absent_keys_copy_as_none(self):
        from pacemaker.transcript_reader import _copy_anchor_shape_flags

        diagnostics: dict = {}
        _copy_anchor_shape_flags(diagnostics, {})
        assert diagnostics["anchor_has_visible_text"] is None
        assert diagnostics["anchor_has_thinking"] is None


class TestFindTurnMatchingToolInputDiagnosticsFlags:
    def test_found_thinking_only_sets_thinking_true_text_false(self, tmp_path):
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("INTENT: hidden in reasoning.", 0)),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
                        "toolu_A",
                        1,
                    ),
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        outcome: dict = {}
        result = _find_turn_matching_tool_input(
            transcript,
            {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
            "Write",
            _outcome=outcome,
        )
        assert result == "", "found, but TEXT has no INTENT -> empty string"
        assert outcome.get("outcome") == "found"
        assert outcome.get("anchor_has_thinking") is True
        assert outcome.get("anchor_has_visible_text") is False

    def test_found_no_thinking_no_text_sets_both_flags_false(self, tmp_path):
        """Issue #148 shape (c): a turn with ONLY a tool_use block -- no
        thinking block at all, no text block. `anchor_has_visible_text` and
        `anchor_has_thinking` were already correctly computed as False for
        this shape before #148 -- this pins that pre-existing contract,
        since #148's actual fix is entirely in the DOWNSTREAM gates
        (hook.py) that combine these two flags, not in this computation."""
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        transcript = _write_transcript(
            [
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
                        "toolu_A",
                        0,
                    ),
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        outcome: dict = {}
        result = _find_turn_matching_tool_input(
            transcript,
            {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
            "Write",
            _outcome=outcome,
        )
        assert result == "", "found, but TEXT has no INTENT -> empty string"
        assert outcome.get("outcome") == "found"
        assert outcome.get("anchor_has_thinking") is False
        assert outcome.get("anchor_has_visible_text") is False

    def test_found_empty_thinking_and_no_text_sets_both_flags_false(self, tmp_path):
        """Issue #148 shape (b): an EMPTY `thinking: ""` block plus
        tool_use -- the exact repro from the issue's own evidence (a
        reviewer subagent transcript with an empty thinking block was
        blocked with no notice)."""
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0)),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
                        "toolu_A",
                        1,
                    ),
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        outcome: dict = {}
        result = _find_turn_matching_tool_input(
            transcript,
            {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
            "Write",
            _outcome=outcome,
        )
        assert result == "", "found, but TEXT has no INTENT -> empty string"
        assert outcome.get("outcome") == "found"
        assert (
            outcome.get("anchor_has_thinking") is False
        ), "an EMPTY thinking block must not count as 'has thinking'"
        assert outcome.get("anchor_has_visible_text") is False

    def test_thinking_null_with_visible_intent_still_resolves_found(self, tmp_path):
        """Issue #141 code-review regression: `thinking: null` (a JSON
        null value, not a missing key or empty string) previously raised
        AttributeError inside the anchor-shape flag computation
        (`block.get("thinking", "").strip()` -- the default only applies
        when the key is ABSENT, not when its value is explicitly None).
        Because that computation lived inside
        `_find_turn_matching_tool_input`'s own outer try/except, the
        exception silently flipped a legitimately FOUND turn -- with a
        perfectly good visible INTENT declaration -- to not_found. A
        real edit with a real INTENT must never be blocked because of a
        null thinking field elsewhere in the same turn."""
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        edit_input = {
            "file_path": NONCORE_FILE,
            "old_string": "old",
            "new_string": "new",
            "replace_all": False,
        }
        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block(None, 0)),
                _asst("req_A", _text_block(VALID_INTENT, 1)),
                _asst("req_A", _tool_use_block("Edit", edit_input, "toolu_A", 2)),
            ],
            str(tmp_path / "t.jsonl"),
        )
        outcome: dict = {}
        result = _find_turn_matching_tool_input(
            transcript, edit_input, "Edit", _outcome=outcome
        )
        assert outcome.get("outcome") == "found", (
            f"A null `thinking` field must never flip a genuinely found "
            f"turn to not_found; got outcome={outcome.get('outcome')!r}"
        )
        assert result is not None and result.startswith(
            VALID_INTENT
        ), f"The real INTENT text must still be returned; got: {result!r}"

    def test_found_visible_text_sets_text_true_thinking_false(self, tmp_path):
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        transcript = _write_transcript(
            [
                _asst("req_A", _text_block(NO_INTENT_TEXT, 0)),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
                        "toolu_A",
                        1,
                    ),
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        outcome: dict = {}
        result = _find_turn_matching_tool_input(
            transcript,
            {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
            "Write",
            _outcome=outcome,
        )
        assert result == ""
        assert outcome.get("outcome") == "found"
        assert outcome.get("anchor_has_visible_text") is True
        assert outcome.get("anchor_has_thinking") is False

    def test_found_visible_text_and_thinking_both_true(self, tmp_path):
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("mulling it over", 0)),
                _asst("req_A", _text_block(VALID_INTENT, 1)),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
                        "toolu_A",
                        2,
                    ),
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        outcome: dict = {}
        result = _find_turn_matching_tool_input(
            transcript,
            {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
            "Write",
            _outcome=outcome,
        )
        assert result.startswith(VALID_INTENT), (
            "found path returns the merged text PLUS formatted tool info "
            f"(_format_message_with_tools); got: {result!r}"
        )
        assert outcome.get("anchor_has_thinking") is True
        assert outcome.get("anchor_has_visible_text") is True

    def test_stale_thinking_only_sets_thinking_true_text_false(self, tmp_path):
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("INTENT: hidden in reasoning.", 0)),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
                        "toolu_A",
                        1,
                    ),
                ),
                _tool_result_entry("done", tool_use_id="toolu_A"),
            ],
            str(tmp_path / "t.jsonl"),
        )
        outcome: dict = {}
        result = _find_turn_matching_tool_input(
            transcript,
            {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
            "Write",
            _outcome=outcome,
        )
        assert result is None, "stale match returns None (existing contract)"
        assert outcome.get("outcome") == "stale"
        assert outcome.get("anchor_has_thinking") is True
        assert outcome.get("anchor_has_visible_text") is False

    def test_not_found_does_not_set_anchor_flags(self, tmp_path):
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        transcript = _write_transcript(
            [_asst("req_A", _text_block("unrelated text, no matching tool_use", 0))],
            str(tmp_path / "t.jsonl"),
        )
        outcome: dict = {}
        result = _find_turn_matching_tool_input(
            transcript,
            {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
            "Write",
            _outcome=outcome,
        )
        assert result is None
        assert outcome.get("outcome") == "not_found"
        assert "anchor_has_thinking" not in outcome
        assert "anchor_has_visible_text" not in outcome


class TestGetCurrentTurnMessageDiagnosticsPropagation:
    def test_propagates_flags_on_success(self, tmp_path):
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("INTENT: hidden in reasoning.", 0)),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
                        "toolu_A",
                        1,
                    ),
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        diagnostics: dict = {}
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": NONCORE_FILE, "content": NEW_CONTENT},
            tool_name="Write",
            _max_wait_seconds=0.0,
            _diagnostics=diagnostics,
        )
        assert result == ""
        assert diagnostics.get("anchor_has_thinking") is True
        assert diagnostics.get("anchor_has_visible_text") is False

    def test_not_found_give_up_leaves_flags_unset(self, tmp_path):
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = str(tmp_path / "t.jsonl")
        Path(transcript).write_text("")
        diagnostics: dict = {}
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": NONCORE_FILE, "content": NEW_CONTENT},
            tool_name="Write",
            _max_wait_seconds=0.0,
            _diagnostics=diagnostics,
        )
        assert result is None
        assert diagnostics.get("outcome") == "not_found"
        assert diagnostics.get("anchor_has_thinking") is None
        assert diagnostics.get("anchor_has_visible_text") is None

    def test_stale_grace_early_return_propagates_flags(self, tmp_path):
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("INTENT: hidden in reasoning.", 0)),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
                        "toolu_A",
                        1,
                    ),
                ),
                _tool_result_entry("done", tool_use_id="toolu_A"),
            ],
            str(tmp_path / "t.jsonl"),
        )
        fake_now = [0.0]

        def fake_monotonic():
            return fake_now[0]

        def fake_sleep(seconds):
            fake_now[0] += seconds

        diagnostics: dict = {}
        with (
            patch("pacemaker.transcript_reader.time.sleep", fake_sleep),
            patch("pacemaker.transcript_reader.time.monotonic", fake_monotonic),
        ):
            result = get_current_turn_message_for_validation(
                transcript,
                tool_input={"file_path": NONCORE_FILE, "content": NEW_CONTENT},
                tool_name="Write",
                _max_wait_seconds=30.0,
                _stale_grace_seconds=3.0,
                _diagnostics=diagnostics,
            )
        assert result is None
        assert diagnostics.get("outcome") == "stale"
        assert diagnostics.get("anchor_has_thinking") is True
        assert diagnostics.get("anchor_has_visible_text") is False


# ---------------------------------------------------------------------------
# Group B: Write/Edit gate hook-level tests (real run_pre_tool_hook, real
# transcript_reader, synthetic transcripts).
# ---------------------------------------------------------------------------


class TestWriteEditThinkingOnlyNotice(_DbHarness):
    def _run(self):
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _make_hook_stdin(
            "Write", NONCORE_FILE, NEW_CONTENT, self.transcript
        )
        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_write_edit()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
        ):
            return run_pre_tool_hook()

    def test_thinking_only_turn_blocks_with_notice(self):
        from pacemaker.transcript_reader import THINKING_ONLY_NOTICE

        _write_transcript(
            [
                _asst("req_A", _thinking_block(THINKING_WITH_INTENT_LOOKING_TEXT, 0)),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
                        "toolu_A",
                        1,
                    ),
                ),
            ],
            self.transcript,
        )
        result = self._run()
        assert result.get("decision") == "block"
        reason = result.get("reason", "")
        assert "INTENT" in reason
        assert (
            THINKING_ONLY_NOTICE in reason
        ), f"Thinking-only turn must surface the explanatory notice; got: {reason!r}"

    def test_visible_text_without_intent_blocks_without_notice(self):
        from pacemaker.transcript_reader import THINKING_ONLY_NOTICE

        _write_transcript(
            [
                _asst("req_A", _text_block(NO_INTENT_TEXT, 0)),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
                        "toolu_A",
                        1,
                    ),
                ),
            ],
            self.transcript,
        )
        result = self._run()
        assert result.get("decision") == "block"
        reason = result.get("reason", "")
        assert "INTENT" in reason
        assert THINKING_ONLY_NOTICE not in reason, (
            f"Visible text (even without INTENT) must NOT get the "
            f"thinking-only notice; got: {reason!r}"
        )

    def test_visible_text_with_intent_proceeds_unchanged(self):
        _write_transcript(
            [
                _asst("req_A", _text_block(VALID_INTENT, 0)),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
                        "toolu_A",
                        1,
                    ),
                ),
            ],
            self.transcript,
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            result = self._run()
        assert result == {"continue": True}
        mock_reviewer.assert_called_once()

    def test_stale_thinking_only_prior_attempt_notice_present(self):
        """Stale path variant: the ORIGINAL (now stale) attempt was
        thinking-only, already has its own tool_result. The byte-identical
        re-issue's own tool_use has NOT yet flushed. The stale outcome must
        still surface the notice (transcript_reader gates anchor flags the
        same way on the stale branch as on found)."""
        from pacemaker.transcript_reader import THINKING_ONLY_NOTICE

        _write_transcript(
            [
                _asst("req_A", _thinking_block(THINKING_WITH_INTENT_LOOKING_TEXT, 0)),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
                        "toolu_A",
                        1,
                    ),
                ),
                _tool_result_entry("blocked", tool_use_id="toolu_A"),
                # The re-issue's own tool_use is deliberately absent --
                # it represents the not-yet-flushed CURRENT attempt.
            ],
            self.transcript,
        )

        fake_now = [0.0]

        def fake_monotonic():
            return fake_now[0]

        def fake_sleep(seconds):
            fake_now[0] += seconds

        with (
            patch("pacemaker.transcript_reader.time.sleep", fake_sleep),
            patch("pacemaker.transcript_reader.time.monotonic", fake_monotonic),
        ):
            result = self._run()

        assert result.get("decision") == "block"
        reason = result.get("reason", "")
        assert "INTENT" in reason
        assert "transcript timing race" not in reason.lower(), (
            f"Must be an ordinary Stage-1 rejection via the accepted stale "
            f"match, not the deferred-race message; got: {reason!r}"
        )
        assert THINKING_ONLY_NOTICE in reason, (
            f"Stale thinking-only anchor must still surface the notice; "
            f"got: {reason!r}"
        )


class TestIssue148WriteEditNoVisibleTextNotice(_DbHarness):
    """Issue #148: the notice must fire for the two shapes issue #141
    missed -- a turn with NO thinking block at all (pure tool_use), and a
    turn with an EMPTY thinking block. Both have `anchor_has_visible_text
    is False` and `anchor_has_thinking is False`, which issue #141's gate
    (`bool(anchor_has_thinking) and anchor_has_visible_text is False`)
    incorrectly treated as "don't show the notice"."""

    def _run(self):
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _make_hook_stdin(
            "Write", NONCORE_FILE, NEW_CONTENT, self.transcript
        )
        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_write_edit()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
        ):
            return run_pre_tool_hook()

    def test_no_thinking_no_text_turn_blocks_with_notice(self):
        """Shape (c): tool_use only, no thinking block at all."""
        from pacemaker.transcript_reader import THINKING_ONLY_NOTICE

        _write_transcript(
            [
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
                        "toolu_A",
                        0,
                    ),
                ),
            ],
            self.transcript,
        )
        result = self._run()
        assert result.get("decision") == "block"
        reason = result.get("reason", "")
        assert "INTENT" in reason
        assert THINKING_ONLY_NOTICE in reason, (
            f"A pure tool_use turn (no thinking at all) must surface the "
            f"no-visible-text notice; got: {reason!r}"
        )

    def test_empty_thinking_only_turn_blocks_with_notice(self):
        """Shape (b): an EMPTY thinking block plus tool_use -- the issue's
        own repro (a reviewer subagent transcript with `thinking: ""`)."""
        from pacemaker.transcript_reader import THINKING_ONLY_NOTICE

        _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0)),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
                        "toolu_A",
                        1,
                    ),
                ),
            ],
            self.transcript,
        )
        result = self._run()
        assert result.get("decision") == "block"
        reason = result.get("reason", "")
        assert "INTENT" in reason
        assert THINKING_ONLY_NOTICE in reason, (
            f"An empty-thinking-only turn must surface the no-visible-text "
            f"notice; got: {reason!r}"
        )


# ---------------------------------------------------------------------------
# Group C: danger-bash gate hook-level tests (real run_pre_tool_hook, real
# transcript_reader; danger rule matching mocked, mirrors test_issue_93's
# `_rules_patch`).
# ---------------------------------------------------------------------------


class TestDangerBashThinkingOnlyNotice(_DbHarness):
    COMMAND = "rm -rf /tmp/pacemaker_issue141_scratch/doomed"

    def _rules_patch(self):
        rule = {"id": "SD-141", "description": "rm -rf (test fixture)"}
        return (
            patch("pacemaker.danger_bash_rules.load_rules", return_value=[rule]),
            patch("pacemaker.danger_bash_rules.match_command", return_value=[rule]),
        )

    def _run(self):
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = json.dumps(
            {
                "session_id": "test-session-141-bash",
                "transcript_path": self.transcript,
                "tool_name": "Bash",
                "tool_input": {"command": self.COMMAND},
            }
        )
        p1, p2 = self._rules_patch()
        with (
            p1,
            p2,
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_danger_bash()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
        ):
            return run_pre_tool_hook()

    def test_thinking_only_turn_blocks_with_notice(self):
        from pacemaker.transcript_reader import THINKING_ONLY_NOTICE

        _write_transcript(
            [
                _asst(
                    "req_A",
                    _thinking_block("INTENT: delete the doomed scratch dir.", 0),
                ),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": self.COMMAND}, "toolu_A", 1),
                ),
            ],
            self.transcript,
        )
        result = self._run()
        assert result.get("decision") == "block"
        reason = result.get("reason", "")
        assert "INTENT" in reason
        assert THINKING_ONLY_NOTICE in reason, (
            f"Thinking-only Bash turn must surface the explanatory notice; "
            f"got: {reason!r}"
        )

    def test_visible_text_without_intent_blocks_without_notice(self):
        from pacemaker.transcript_reader import THINKING_ONLY_NOTICE

        _write_transcript(
            [
                _asst("req_A", _text_block("Cleaning up the scratch dir now.", 0)),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": self.COMMAND}, "toolu_A", 1),
                ),
            ],
            self.transcript,
        )
        result = self._run()
        assert result.get("decision") == "block"
        reason = result.get("reason", "")
        assert "INTENT" in reason
        assert THINKING_ONLY_NOTICE not in reason, (
            f"Visible text (even without INTENT) must NOT get the "
            f"thinking-only notice; got: {reason!r}"
        )

    def test_stale_thinking_only_prior_attempt_notice_present(self):
        """Danger-bash twin of TestWriteEditThinkingOnlyNotice's stale test
        (code review follow-up, item 4): the ORIGINAL (now stale) Bash
        attempt was thinking-only and already has its own tool_result; the
        byte-identical re-issue's own tool_use has NOT yet flushed. The
        gate's existing stale-outcome handling (accepted per issue #93)
        must still surface the notice, since `_bash_no_visible_text` is
        computed from `_bash_diagnostics` right after `_bash_outcome` is
        read -- before the found/stale/not_found branching -- so it is
        populated identically regardless of which branch is taken."""
        from pacemaker.transcript_reader import THINKING_ONLY_NOTICE

        _write_transcript(
            [
                _asst(
                    "req_A",
                    _thinking_block("INTENT: delete the doomed scratch dir.", 0),
                ),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": self.COMMAND}, "toolu_A", 1),
                ),
                _tool_result_entry("blocked", tool_use_id="toolu_A"),
                # The re-issue's own tool_use is deliberately absent -- it
                # represents the not-yet-flushed CURRENT attempt.
            ],
            self.transcript,
        )

        fake_now = [0.0]

        def fake_monotonic():
            return fake_now[0]

        def fake_sleep(seconds):
            fake_now[0] += seconds

        with (
            patch("pacemaker.transcript_reader.time.sleep", fake_sleep),
            patch("pacemaker.transcript_reader.time.monotonic", fake_monotonic),
        ):
            result = self._run()

        assert result.get("decision") == "block"
        reason = result.get("reason", "")
        assert "INTENT" in reason
        assert THINKING_ONLY_NOTICE in reason, (
            f"Stale thinking-only Bash anchor must still surface the "
            f"notice; got: {reason!r}"
        )

    def test_thinking_only_records_thinking_only_true_in_blockage_details(self):
        """Code review follow-up item 3: the Phase-1 no-INTENT blockage
        event's `details` JSON must record `no_visible_text` so the
        claude-usage monitor / usage.db telemetry can distinguish this
        block shape from an ordinary missing-INTENT block."""
        _write_transcript(
            [
                _asst(
                    "req_A",
                    _thinking_block("INTENT: delete the doomed scratch dir.", 0),
                ),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": self.COMMAND}, "toolu_A", 1),
                ),
            ],
            self.transcript,
        )
        self._run()

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT details FROM blockage_events WHERE category = 'intent_validation'"
            ).fetchall()
        finally:
            conn.close()
        assert rows, "Expected an intent_validation blockage event"
        details = json.loads(rows[0][0])
        assert details.get("no_visible_text") is True, (
            f"No-visible-text Bash block must record no_visible_text=True "
            f"in blockage details; got: {details}"
        )

    def test_visible_text_records_thinking_only_false_in_blockage_details(self):
        _write_transcript(
            [
                _asst("req_A", _text_block("Cleaning up the scratch dir now.", 0)),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": self.COMMAND}, "toolu_A", 1),
                ),
            ],
            self.transcript,
        )
        self._run()

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT details FROM blockage_events WHERE category = 'intent_validation'"
            ).fetchall()
        finally:
            conn.close()
        assert rows, "Expected an intent_validation blockage event"
        details = json.loads(rows[0][0])
        assert details.get("no_visible_text") is False, (
            f"Visible-text (no-INTENT) Bash block must record "
            f"no_visible_text=False in blockage details; got: {details}"
        )


class TestIssue148DangerBashNoVisibleTextNotice(_DbHarness):
    """Issue #148's own repro: a dangerous Bash turn with only an EMPTY
    `thinking: ""` block plus the tool_use was blocked with no notice
    (evidence: `agent-a2adca28bd8218fbc.jsonl`). This base class covers the
    pure tool_use-only shape (no thinking block at all); subclasses below
    (added separately, to keep each edit's method count small) reuse these
    helpers to cover the empty-thinking-only shape plus blockage-detail
    telemetry for both shapes."""

    COMMAND = "rm -rf /tmp/pacemaker_issue148_scratch/doomed"

    def _rules_patch(self):
        rule = {"id": "SD-148", "description": "rm -rf (test fixture)"}
        return (
            patch("pacemaker.danger_bash_rules.load_rules", return_value=[rule]),
            patch("pacemaker.danger_bash_rules.match_command", return_value=[rule]),
        )

    def _run(self):
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = json.dumps(
            {
                "session_id": "test-session-148-bash",
                "transcript_path": self.transcript,
                "tool_name": "Bash",
                "tool_input": {"command": self.COMMAND},
            }
        )
        p1, p2 = self._rules_patch()
        with (
            p1,
            p2,
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_danger_bash()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
        ):
            return run_pre_tool_hook()

    def test_no_thinking_no_text_turn_blocks_with_notice(self):
        from pacemaker.transcript_reader import THINKING_ONLY_NOTICE

        _write_transcript(
            [
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": self.COMMAND}, "toolu_A", 0),
                ),
            ],
            self.transcript,
        )
        result = self._run()
        assert result.get("decision") == "block"
        reason = result.get("reason", "")
        assert "INTENT" in reason
        assert THINKING_ONLY_NOTICE in reason, (
            f"A pure tool_use Bash turn (no thinking at all) must surface "
            f"the no-visible-text notice; got: {reason!r}"
        )


class TestIssue148DangerBashNoVisibleTextNoticeEmptyThinking(
    TestIssue148DangerBashNoVisibleTextNotice
):
    """The exact repro from issue #148's own evidence: an EMPTY
    `thinking: ""` block plus tool_use."""

    def test_empty_thinking_only_turn_blocks_with_notice(self):
        from pacemaker.transcript_reader import THINKING_ONLY_NOTICE

        _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0)),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": self.COMMAND}, "toolu_A", 1),
                ),
            ],
            self.transcript,
        )
        result = self._run()
        assert result.get("decision") == "block"
        reason = result.get("reason", "")
        assert "INTENT" in reason
        assert THINKING_ONLY_NOTICE in reason, (
            f"An empty-thinking-only Bash turn must surface the "
            f"no-visible-text notice; got: {reason!r}"
        )


class TestIssue148DangerBashNoVisibleTextTelemetry(
    TestIssue148DangerBashNoVisibleTextNotice
):
    """Blockage-detail `no_visible_text` telemetry for both #148 shapes."""

    def _blockage_details(self):
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT details FROM blockage_events WHERE category = 'intent_validation'"
            ).fetchall()
        finally:
            conn.close()
        assert rows, "Expected an intent_validation blockage event"
        return json.loads(rows[0][0])

    def test_no_thinking_no_text_records_no_visible_text_true(self):
        _write_transcript(
            [
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": self.COMMAND}, "toolu_A", 0),
                ),
            ],
            self.transcript,
        )
        self._run()
        details = self._blockage_details()
        assert details.get("no_visible_text") is True, (
            f"No-thinking-no-text Bash block must record "
            f"no_visible_text=True in blockage details; got: {details}"
        )

    def test_empty_thinking_only_records_no_visible_text_true(self):
        _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0)),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": self.COMMAND}, "toolu_A", 1),
                ),
            ],
            self.transcript,
        )
        self._run()
        details = self._blockage_details()
        assert details.get("no_visible_text") is True, (
            f"Empty-thinking-only Bash block must record "
            f"no_visible_text=True in blockage details; got: {details}"
        )


# ---------------------------------------------------------------------------
# Group D: Write/Edit gate blockage-telemetry (code review follow-up item 3).
# ---------------------------------------------------------------------------


class TestWriteEditThinkingOnlyBlockageTelemetry(_DbHarness):
    def _run(self):
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _make_hook_stdin(
            "Write", NONCORE_FILE, NEW_CONTENT, self.transcript
        )
        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_write_edit()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
        ):
            return run_pre_tool_hook()

    def _blockage_details(self):
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT details FROM blockage_events WHERE category = 'intent_validation'"
            ).fetchall()
        finally:
            conn.close()
        assert rows, "Expected an intent_validation blockage event"
        return json.loads(rows[0][0])

    def test_thinking_only_turn_records_thinking_only_true(self):
        _write_transcript(
            [
                _asst("req_A", _thinking_block(THINKING_WITH_INTENT_LOOKING_TEXT, 0)),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
                        "toolu_A",
                        1,
                    ),
                ),
            ],
            self.transcript,
        )
        self._run()
        details = self._blockage_details()
        assert details.get("no_visible_text") is True, (
            f"No-visible-text Write/Edit block must record "
            f"no_visible_text=True in blockage details; got: {details}"
        )

    def test_visible_text_without_intent_records_thinking_only_false(self):
        _write_transcript(
            [
                _asst("req_A", _text_block(NO_INTENT_TEXT, 0)),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": NEW_CONTENT},
                        "toolu_A",
                        1,
                    ),
                ),
            ],
            self.transcript,
        )
        self._run()
        details = self._blockage_details()
        assert details.get("no_visible_text") is False, (
            f"Visible-text (no-INTENT) Write/Edit block must record "
            f"no_visible_text=False in blockage details; got: {details}"
        )


# ---------------------------------------------------------------------------
# Group E: NO_TDD branch notice (code review follow-up item 2). Direct unit
# tests on validate_intent_and_code -- the NO_TDD branch is reachable in
# production via the n-back rescue (current_message_override resolves to ""
# from a thinking-only anchor, then intent_validator falls back to
# extract_current_assistant_message(messages, ...), which can resolve a
# PRIOR turn's real visible INTENT+file mention with no TDD declaration).
# Passing thinking_only directly exercises the branch's own logic without
# needing to reconstruct that full rescue chain.
# ---------------------------------------------------------------------------


CORE_FILE_FOR_NO_TDD = "src/auth_example.py"
NO_TDD_INTENT_TEXT = (
    "INTENT: Modify src/auth_example.py to add a new validate() function "
    "for input checks.\n"
)


class TestNoTddBranchThinkingOnlyNotice:
    def test_no_tdd_with_thinking_only_appends_notice(self):
        from pacemaker.intent_validator import validate_intent_and_code
        from pacemaker.transcript_reader import THINKING_ONLY_NOTICE

        result = validate_intent_and_code(
            messages=[],
            code="def validate():\n    pass\n",
            file_path=CORE_FILE_FOR_NO_TDD,
            tool_name="Write",
            current_message_override=NO_TDD_INTENT_TEXT,
            no_visible_text=True,
        )
        assert result.get("approved") is False
        assert (
            result.get("tdd_failure") is True
        ), f"Fixture must actually hit the NO_TDD branch; got: {result}"
        assert THINKING_ONLY_NOTICE in result.get("feedback", ""), (
            f"NO_TDD block with no_visible_text=True must surface the "
            f"notice in the tagged feedback; got: {result.get('feedback')!r}"
        )
        assert THINKING_ONLY_NOTICE in result.get("raw_feedback", ""), (
            f"NO_TDD block with no_visible_text=True must surface the "
            f"notice in raw_feedback too; got: {result.get('raw_feedback')!r}"
        )

    def test_no_tdd_without_thinking_only_omits_notice(self):
        from pacemaker.intent_validator import validate_intent_and_code
        from pacemaker.transcript_reader import THINKING_ONLY_NOTICE

        result = validate_intent_and_code(
            messages=[],
            code="def validate():\n    pass\n",
            file_path=CORE_FILE_FOR_NO_TDD,
            tool_name="Write",
            current_message_override=NO_TDD_INTENT_TEXT,
            no_visible_text=False,
        )
        assert result.get("approved") is False
        assert result.get("tdd_failure") is True
        assert THINKING_ONLY_NOTICE not in result.get("feedback", ""), (
            f"NO_TDD block with no_visible_text=False (the default) must "
            f"NOT surface the notice; got: {result.get('feedback')!r}"
        )
        assert THINKING_ONLY_NOTICE not in result.get("raw_feedback", "")


# ---------------------------------------------------------------------------
# Group F: raw_feedback is untagged (code review follow-up item 4, second
# half). Direct unit test on validate_intent_and_code's return dict --
# "feedback" carries the pace-maker provenance tag, "raw_feedback" must
# carry the SAME notice text with no tag prefix (Story #101 AC5 discipline:
# the governance feed reads raw_feedback, never the tagged variant).
# ---------------------------------------------------------------------------


class TestRawFeedbackNoticeUntagged:
    def test_no_branch_raw_feedback_contains_notice_untagged(self):
        from pacemaker.intent_validator import validate_intent_and_code
        from pacemaker.transcript_reader import THINKING_ONLY_NOTICE

        result = validate_intent_and_code(
            messages=[],
            code=NEW_CONTENT,
            file_path=NONCORE_FILE,
            tool_name="Write",
            current_message_override="",
            no_visible_text=True,
        )
        assert result.get("approved") is False
        raw_feedback = result.get("raw_feedback", "")
        feedback = result.get("feedback", "")
        assert THINKING_ONLY_NOTICE in raw_feedback
        assert THINKING_ONLY_NOTICE in feedback
        assert "[pace-maker" not in raw_feedback, (
            f"raw_feedback must be untagged (governance-feed consumer, "
            f"Story #101 AC5); got: {raw_feedback!r}"
        )
        assert "[pace-maker" in feedback, (
            f"feedback (Claude-facing) must carry the provenance tag; "
            f"got: {feedback!r}"
        )
