"""
Issue #140 regression tests (code-review revision): Write/Edit Stage 1's
INTENT-marker detection, file-mention check, and TDD-declaration/
version-bump checks could all be satisfied by content that appears ONLY
inside a tool call's rendered parameters (a Write's ``content``, an Edit's
``old_string``/``new_string``), reached via the n-back fallback and even on
the ANCHOR-FOUND path.

CODE REVIEW ON THE FIRST #140 FIX (naive "[TOOL: " string-splitting)
======================================================================
The first #140 fix added ``strip_rendered_tool_params()``, which searched a
RENDERED string for the first literal ``"[TOOL: "`` substring and truncated
everything after it. Code review found THREE problems:

1. HIGH: ``_regex_stage1_check`` still ran ``_has_tdd_declaration``/
   ``_is_version_bump`` on ``current_message[intent_match.end():]`` -- a
   slice of the FULL (only marker-stripped-for-the-MATCH, not for the
   SLICE) ``current_message``, which still included rendered tool params
   after the marker. A real prose INTENT followed by a Write whose
   `content` happened to contain "# test: foo" or "version = 2" would
   wrongly satisfy the TDD-declaration/version-bump check.
2. HIGH: ``_mentions_file()`` was always trivially satisfied, because
   ``_format_message_with_tools`` always renders the tool's own
   ``file_path: <target>`` field -- which is, by construction, the SAME
   file being validated. A real INTENT in the immediately-preceding
   message that named a DIFFERENT file (a 1-back rescue that should be
   REJECTED) would still pass, since the rendered current turn's tool
   info trivially "mentioned" the real target file regardless of what the
   rescued INTENT actually said.
3. MEDIUM (root cause of 1 & 2, and its own bug): naive substring-splitting
   on ``"[TOOL: "`` is unsafe against prose that legitimately QUOTES that
   marker text (e.g. "The log showed `[TOOL: Bash]`." followed by a real
   INTENT declaration) -- the quoted text gets found FIRST and everything
   after it, including the real INTENT, is truncated away, producing a
   NEW false block that did not exist before ANY #140 fix.

THE FIX (this revision) -- structured prose, never string-splitting
======================================================================
Rather than post-hoc stripping a rendered blob, PROSE is now carried as
STRUCTURED data from the point it is first extracted from the JSONL:

- ``transcript_reader.get_last_n_messages_for_validation`` gained a
  keyword-only ``_with_prose: bool = False`` parameter. When True, it
  returns a ``(rendered, prose)`` TUPLE computed from a SINGLE
  parse/grouping pass over the transcript -- ``prose`` is every message,
  including the most recent one, as ``msg["text"]`` only, never rendered
  with tool parameters. Default False preserves the exact pre-existing
  single-list return contract for every other caller. (Re-review finding
  2: an earlier revision of this fix used a separate ``_prose_only``
  boolean requiring a SECOND full transcript re-parse per call -- measured
  ~0.86s on a 99.7MB file, ~2.8s extrapolated to 324MB, eating the gate's
  anchor budget. The single-pass tuple design eliminates that entirely.)
- ``transcript_reader._find_turn_matching_tool_input`` now additionally
  populates ``_outcome["anchor_prose_text"] = merged["text"]`` on both the
  "found" and "stale" branches (alongside the pre-existing
  ``anchor_has_visible_text``/``anchor_has_thinking`` flags), and
  ``_copy_anchor_shape_flags`` copies it too. Re-review finding 1
  (probe L, below): this assignment is a plain dict/string read placed
  BEFORE the try/except that wraps ``_turn_has_thinking()`` -- an earlier
  revision placed it LAST inside that try, so a raise from
  ``_turn_has_thinking`` left the key entirely unset, which could produce
  a false "transcript not ready" block (found path) or an incorrect
  fallback to n-back (stale path, violating the documented ANCHOR-ONLY
  invariant).
- ``intent_validator.validate_intent_and_code`` gained an optional
  ``stage1_fallback_messages`` parameter: when supplied, Stage 1's
  ``extract_current_assistant_message`` fallback searches THIS
  (prose-only) list instead of ``messages`` (which still feeds Stage 2's
  prompt, unchanged, with full tool-rendered content).
- ``hook.py``'s Write/Edit gate now: (a) makes a SINGLE
  ``get_last_n_messages_for_validation(..., _with_prose=True)`` call,
  unpacking both the rendered list (Stage 2) and the prose-only list
  (``stage1_fallback_messages``) from one parse; (b) substitutes
  ``anchor_prose_text`` for ``current_message_override`` whenever the
  override is truthy (found-with-intent, or accepted-stale path), so the
  ANCHOR-FOUND case is fixed too, not just the n-back fallback -- gated by
  ``isinstance(_p, str) and _p`` (re-review finding 1), never
  ``dict.get(key, default)``, since a `dict.get` default is skipped
  whenever the KEY IS PRESENT even with a falsy value.
- ``extract_current_assistant_message`` and ``_regex_stage1_check`` are
  REVERTED to plain ``_has_intent_marker(text)`` checks -- no
  string-splitting anywhere. Since the callers above now guarantee prose-
  only input on the real hook path, there is nothing to strip: the
  guarantee is structural, not string-heuristic.
- ``strip_rendered_tool_params()``/``TOOL_RENDER_MARKER`` were REMOVED
  (Messi Rule 12, Anti-Orphan-Code) once they had zero callers.

The danger-bash gate is UNAFFECTED (structural guard test below): it never
calls ``extract_current_assistant_message`` or
``get_last_n_messages_for_validation`` at all -- anchor-only per #93/#139,
already gated on ``merged["text"]``.

MOCKING RATIONALE (mirrors tests/test_issue_141_thinking_only_notice.py)
============================================================================
Probes B-E (the code review's own numbered findings) are exercised as REAL
synthetic-transcript, hook-level tests driving the ACTUAL (unmocked)
``run_pre_tool_hook()`` / ``transcript_reader`` algorithm end-to-end --
mirroring ``test_issue_141_thinking_only_notice.py``'s
``_asst``/``_write_transcript``/``_DbHarness`` pattern. Stage 2 (LLM
review) is mocked at ``pacemaker.inference.resolve_and_call_with_reviewer``
(the namespace ``_call_stage2_validation`` actually imports from) only for
the ONE probe that must legitimately reach Stage 2 (probe E's YES case);
every BLOCKED probe never reaches Stage 2 at all, so no mock is needed
there.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, patch

os.environ.setdefault("PACEMAKER_TEST_MODE", "1")

from pacemaker import database  # noqa: E402
from pacemaker.intent_validator import (  # noqa: E402
    _regex_stage1_check,
    extract_current_assistant_message,
    validate_intent_and_code,
)
from pacemaker.transcript_reader import (  # noqa: E402
    _find_turn_matching_tool_input,
    get_current_turn_message_for_validation,
    get_last_n_messages_for_validation,
)

# ---------------------------------------------------------------------------
# JSONL transcript builder helpers (mirrors test_issue_141_thinking_only_notice.py)
# ---------------------------------------------------------------------------


def _asst(request_id: Optional[str], block: dict) -> dict:
    entry = {"message": {"role": "assistant", "content": [block]}}
    if request_id is not None:
        entry["requestId"] = request_id
    return entry


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


# Non-core (no default word-list segment, no filesystem marker reachable
# from a relative path) -- Stage 1 needs no TDD declaration for these.
NONCORE_CURRENT_FILE = "scratch_module/mod.py"
NONCORE_OTHER_FILE = "scratch_module/other.py"

# Core via Layer 1's "src" word-list segment (no real file needed).
CORE_FILE = "src/example_module.py"

REAL_INTENT_MOD = "INTENT: Modify mod.py to add a helper function."
REAL_INTENT_OTHER = (
    "INTENT: Modify other.py to fix a bug.\n"
    "Test coverage: tests/test_other.py - test_bug_fix()"
)
REAL_INTENT_CORE_NO_TDD = "INTENT: Modify example_module.py to add validate()."
REAL_INTENT_CORE_WITH_TDD = (
    "INTENT: Modify example_module.py to add validate().\n"
    "Test coverage: tests/test_example_module.py - test_validate()"
)


def _make_hook_stdin(
    tool_name: str, file_path: str, tool_input: dict, transcript_path: str
) -> str:
    return json.dumps(
        {
            "session_id": "test-session-140",
            "transcript_path": transcript_path,
            "tool_name": tool_name,
            "tool_input": tool_input,
        }
    )


def _config_write_edit() -> dict:
    # hook_model deliberately NOT "auto"/"sonnet"/"opus"/"haiku" (mirrors
    # test_issue_139/141's identical rationale): those trigger the
    # SDK-availability fail-closed gate before Stage 2 is ever reached.
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

    def _run(self, tool_name: str, file_path: str, tool_input: dict):
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _make_hook_stdin(
            tool_name, file_path, tool_input, self.transcript
        )
        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_write_edit()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
        ):
            return run_pre_tool_hook()


# ---------------------------------------------------------------------------
# Group A: get_last_n_messages_for_validation(_with_prose=True) -- single-
# pass structural prose extraction, never string-splitting, never a second
# transcript re-parse (issue #140 code-review re-review finding 2).
# ---------------------------------------------------------------------------


class TestProseOnlyNBackList:
    def test_with_prose_uses_text_even_for_most_recent_tool_carrying_message(
        self, tmp_path
    ):
        transcript = _write_transcript(
            [
                _asst("req_A", _text_block(REAL_INTENT_OTHER, 0)),
                _asst(
                    "req_B",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_CURRENT_FILE, "content": "print(1)"},
                        "toolu_B",
                        0,
                    ),
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        rendered_default = get_last_n_messages_for_validation(transcript, n=2)
        rendered, prose = get_last_n_messages_for_validation(
            transcript, n=2, _with_prose=True
        )
        assert "[TOOL: Write]" in rendered_default[-1], "sanity: default is rendered"
        assert rendered == rendered_default, (
            "the rendered half of the tuple must be identical to the "
            "default single-list return"
        )
        assert "[TOOL:" not in prose[-1]
        assert prose[-1] == ""

    def test_prose_only_preserves_prose_that_quotes_the_tool_marker_text(
        self, tmp_path
    ):
        """Probe E at the structural-extraction level: real prose that
        merely QUOTES "[TOOL: Bash]" must never be truncated -- there is
        no string-splitting happening at all in prose-only mode."""
        quoting_prose = "The log showed `[TOOL: Bash]`.\n\n" + REAL_INTENT_MOD
        transcript = _write_transcript(
            [
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Edit",
                        {
                            "file_path": NONCORE_CURRENT_FILE,
                            "old_string": "pass",
                            "new_string": "pass  # done",
                        },
                        "toolu_A",
                        1,
                    ),
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        # Overwrite with the quoting-prose turn as messages[-2] and the
        # tool_use as messages[-1], matching a fragmented turn.
        _write_transcript(
            [
                _asst("req_A", _text_block(quoting_prose, 0)),
                _asst(
                    "req_B",
                    _tool_use_block(
                        "Edit",
                        {
                            "file_path": NONCORE_CURRENT_FILE,
                            "old_string": "pass",
                            "new_string": "pass  # done",
                        },
                        "toolu_A",
                        0,
                    ),
                ),
            ],
            transcript,
        )
        _rendered, prose = get_last_n_messages_for_validation(
            transcript, n=2, _with_prose=True
        )
        assert prose[0] == quoting_prose
        assert "INTENT:" in prose[0]

    def test_with_prose_default_still_returns_single_list(self, tmp_path):
        """Sanity: omitting _with_prose preserves the exact pre-existing
        single-list, rendered-with-tools contract (backward compatibility)."""
        transcript = _write_transcript(
            [
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_CURRENT_FILE, "content": "x = 1"},
                        "toolu_A",
                        0,
                    ),
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        result = get_last_n_messages_for_validation(transcript, n=2)
        assert "[TOOL: Write]" in result[-1]


# ---------------------------------------------------------------------------
# Group B: anchor_prose_text diagnostic propagation.
# ---------------------------------------------------------------------------


class TestAnchorProseTextDiagnostic:
    def test_found_path_exposes_anchor_prose_text(self, tmp_path):
        transcript = _write_transcript(
            [
                _asst("req_A", _text_block(REAL_INTENT_MOD, 0)),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_CURRENT_FILE, "content": "# test: foo"},
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
            {"file_path": NONCORE_CURRENT_FILE, "content": "# test: foo"},
            "Write",
            _outcome=outcome,
        )
        assert result is not None and result.startswith(REAL_INTENT_MOD)
        assert outcome.get("anchor_prose_text") == REAL_INTENT_MOD
        assert "[TOOL:" not in outcome["anchor_prose_text"]

    def test_get_current_turn_message_propagates_anchor_prose_text(self, tmp_path):
        transcript = _write_transcript(
            [
                _asst("req_A", _text_block(REAL_INTENT_MOD, 0)),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_CURRENT_FILE, "content": "# test: foo"},
                        "toolu_A",
                        1,
                    ),
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        diagnostics: dict = {}
        get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": NONCORE_CURRENT_FILE, "content": "# test: foo"},
            tool_name="Write",
            _max_wait_seconds=0.0,
            _diagnostics=diagnostics,
        )
        assert diagnostics.get("anchor_prose_text") == REAL_INTENT_MOD


# ---------------------------------------------------------------------------
# Group C: extract_current_assistant_message / _regex_stage1_check --
# plain marker checks over already-structured prose (no stripping).
# ---------------------------------------------------------------------------


class TestExtractAndStage1OverPureProse:
    def test_prose_quoting_tool_marker_then_real_intent_passes(self):
        """Probe E at the extract_current_assistant_message level."""
        quoting_then_intent = "The log showed `[TOOL: Bash]`.\n\n" + REAL_INTENT_MOD
        messages = ["earlier context", quoting_then_intent]
        result = extract_current_assistant_message(messages)
        assert result == quoting_then_intent
        verdict = _regex_stage1_check(result, NONCORE_CURRENT_FILE, [])
        assert verdict == "YES", (
            f"prose quoting the tool marker must not truncate the real "
            f"INTENT that follows it; got verdict={verdict!r}"
        )

    def test_wrong_file_intent_one_back_does_not_satisfy_stage1(self):
        """Probe D: a real INTENT in the preceding message names a
        DIFFERENT file than the one being validated -- must be rejected,
        not rescued via a trivial tool-rendered file_path match (there is
        no tool rendering here at all, by construction)."""
        messages = [REAL_INTENT_OTHER, ""]
        result = extract_current_assistant_message(
            messages, file_path=NONCORE_CURRENT_FILE
        )
        assert result == "", (
            "the defense-in-depth file-mention guard must discard a "
            f"wrong-file rescue; got: {result!r}"
        )
        verdict = _regex_stage1_check(
            extract_current_assistant_message(messages), NONCORE_CURRENT_FILE, []
        )
        assert verdict == "NO"


# ---------------------------------------------------------------------------
# Group D: validate_intent_and_code's stage1_fallback_messages parameter.
# ---------------------------------------------------------------------------


class TestStage1FallbackMessagesParameter:
    def test_default_none_falls_back_to_messages(self):
        """Backward compatibility: omitting stage1_fallback_messages must
        behave exactly as before (uses `messages` for the n-back
        fallback). Stage 2 is mocked -- REAL_INTENT_MOD names mod.py (the
        target), so Stage 1 must PASS via the 1-back rescue and reach
        Stage 2; without mocking, that reach-Stage-2 behavior previously
        hit the real Anthropic SDK (no login in this sandbox), which the
        #144 test guard now flags as a leaked external call."""
        messages = [REAL_INTENT_MOD, ""]
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            result = validate_intent_and_code(
                messages=messages,
                code="x = 1",
                file_path=NONCORE_CURRENT_FILE,
                tool_name="Write",
                current_message_override="",
            )
        assert result.get("approved") is True, (
            f"REAL_INTENT_MOD mentions mod.py, so Stage 1 should PASS via "
            f"the 1-back rescue and reach (mocked) Stage 2; got: {result}"
        )
        assert result.get("reviewer") != "RegEx", (
            f"reaching (mocked) Stage 2 means Stage 1 did NOT block it at "
            f"the RegEx gate; got: {result}"
        )
        mock_reviewer.assert_called_once()

    def test_explicit_stage1_fallback_messages_overrides_messages(self):
        """A caller-supplied prose-only list is used INSTEAD of `messages`
        for Stage 1, even when `messages` itself would have satisfied it."""
        result = validate_intent_and_code(
            messages=[REAL_INTENT_MOD, ""],  # would satisfy Stage 1 if used
            code="x = 1",
            file_path=NONCORE_CURRENT_FILE,
            tool_name="Write",
            current_message_override="",
            stage1_fallback_messages=["no intent here", ""],
        )
        assert result.get("approved") is False
        assert result.get("reviewer") == "RegEx"
        assert result.get("tdd_failure") is not True


# ---------------------------------------------------------------------------
# Group E: real-transcript hook-level probes B, C, D, E (code review's own
# numbered findings).
# ---------------------------------------------------------------------------


class TestProbeBTddDeclarationInContentDoesNotSatisfyStage1(_DbHarness):
    """B: real prose INTENT (core file, no real TDD declaration) + Write
    `content` containing a TDD-declaration-shaped string ("# test: foo")
    must NOT satisfy Stage 1's TDD requirement -> NO_TDD, not YES."""

    def test_fake_tdd_declaration_in_write_content_still_blocks_no_tdd(self):
        _write_transcript(
            [
                _asst("req_A", _text_block(REAL_INTENT_CORE_NO_TDD, 0)),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {
                            "file_path": CORE_FILE,
                            "content": "def f():\n    # test: foo\n    pass\n",
                        },
                        "toolu_A",
                        1,
                    ),
                ),
            ],
            self.transcript,
        )
        result = self._run(
            "Write",
            CORE_FILE,
            {
                "file_path": CORE_FILE,
                "content": "def f():\n    # test: foo\n    pass\n",
            },
        )
        assert result.get("decision") == "block"
        reason = result.get("reason", "")
        assert (
            "TDD Required" in reason
            or "NO_TDD" in reason.upper()
            or "core code" in reason.lower()
        ), (
            f"Fake TDD-looking text inside Write content must not satisfy "
            f"the real TDD-declaration requirement; got: {reason!r}"
        )


class TestProbeCVersionBumpInContentDoesNotSatisfyStage1(_DbHarness):
    """C: real prose INTENT (core file, no real TDD/version-bump
    declaration) + Edit `new_string` containing version-bump-shaped text
    ("def update(...)" / "version = 2") must NOT satisfy Stage 1."""

    def test_fake_version_bump_in_edit_new_string_still_blocks_no_tdd(self):
        fake_bump_code = "def update(x):\n    version = 2\n    return x\n"
        _write_transcript(
            [
                _asst("req_A", _text_block(REAL_INTENT_CORE_NO_TDD, 0)),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Edit",
                        {
                            "file_path": CORE_FILE,
                            "old_string": "pass",
                            "new_string": fake_bump_code,
                        },
                        "toolu_A",
                        1,
                    ),
                ),
            ],
            self.transcript,
        )
        result = self._run(
            "Edit",
            CORE_FILE,
            {
                "file_path": CORE_FILE,
                "old_string": "pass",
                "new_string": fake_bump_code,
            },
        )
        assert result.get("decision") == "block"
        reason = result.get("reason", "")
        assert "TDD Required" in reason or "core code" in reason.lower(), (
            f"Fake version-bump-looking text inside Edit new_string must "
            f"not satisfy Stage 1; got: {reason!r}"
        )


class TestProbeDWrongFileOneBackDoesNotSatisfyStage1(_DbHarness):
    """D: messages[-2] has a REAL intent for a DIFFERENT file (other.py,
    with its own Test coverage declaration); the current turn (mod.py) has
    no prose of its own. Must block (NO), never rescued via the tool's own
    rendered file_path field."""

    def test_wrong_file_rescue_blocks(self):
        _write_transcript(
            [
                _asst("req_A", _text_block(REAL_INTENT_OTHER, 0)),
                _asst(
                    "req_B",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_CURRENT_FILE, "content": "x = 1\n"},
                        "toolu_B",
                        0,
                    ),
                ),
            ],
            self.transcript,
        )
        result = self._run(
            "Write",
            NONCORE_CURRENT_FILE,
            {"file_path": NONCORE_CURRENT_FILE, "content": "x = 1\n"},
        )
        assert result.get("decision") == "block"
        reason = result.get("reason", "")
        assert "INTENT" in reason
        assert "other.py" not in reason or "declaration required" in reason.lower(), (
            f"A real INTENT for a DIFFERENT file must never rescue the "
            f"current (unrelated) edit; got: {reason!r}"
        )


class TestProbeEProseQuotingToolMarkerThenRealIntentPasses(_DbHarness):
    """E: the CURRENT turn's OWN prose quotes "[TOOL: Bash]" (as text
    discussing a prior command) and THEN declares a real INTENT with a
    real Test coverage line. Must PASS Stage 1 (and reach Stage 2, mocked
    APPROVED here) -- the naive #140-v1 fix falsely blocked this."""

    def test_quoted_marker_in_own_prose_does_not_false_block(self):
        quoting_then_intent = (
            "The log showed `[TOOL: Bash]`.\n\n" + REAL_INTENT_CORE_WITH_TDD
        )
        _write_transcript(
            [
                _asst("req_A", _text_block(quoting_then_intent, 0)),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Edit",
                        {
                            "file_path": CORE_FILE,
                            "old_string": "pass",
                            "new_string": "pass  # updated",
                        },
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
            result = self._run(
                "Edit",
                CORE_FILE,
                {
                    "file_path": CORE_FILE,
                    "old_string": "pass",
                    "new_string": "pass  # updated",
                },
            )
        assert result == {"continue": True}, (
            f"prose quoting the tool marker before a real INTENT must not "
            f"false-block; got: {result}"
        )
        mock_reviewer.assert_called_once()


# ---------------------------------------------------------------------------
# Group F (probe L, code review re-review finding 1, MEDIUM): hook.py's
# isinstance-guarded substitution for anchor_prose_text. The underlying
# transcript_reader.py fix (anchor_prose_text moved to a plain dict/string
# read BEFORE the try/except wrapping _turn_has_thinking) is already
# regression-locked directly at the transcript_reader level by extending
# the EXISTING, already-merged precedent tests in
# tests/test_issue_141_thinking_only_notice.py::
# TestAnchorShapeExceptionLogsWarning (both tests there now additionally
# assert anchor_prose_text survives the same _turn_has_thinking raise they
# already inject) -- not duplicated here.
#
# These tests instead verify hook.py's OWN defensive guard: the
# isinstance(_p, str) and _p check must safely fall back to the
# already-truthy override rather than crash or silently downgrade to None.
# Proven WITHOUT mocking any transcript_reader internals -- only hook.py's
# own declared external dependency get_current_turn_message_for_validation
# is mocked (the established _mock_anchor-style pattern from
# tests/test_issue_139_write_edit_stale_accept.py). anchor_prose_text is
# set to an EXPLICIT None (not merely omitted) to accurately reproduce the
# real failure precondition: _copy_anchor_shape_flags's unconditional
# `.get()` (no default) sets the key to None in the CALLER-VISIBLE
# diagnostics dict even when the source _outcome never set it.
#
# self.transcript (from _DbHarness.setup_method) is deliberately left
# EMPTY -- never written by these tests. If the guard were broken and
# fell back to None, current_message_override would become falsy and
# Stage 1 would consult the n-back fallback against this EMPTY
# transcript, which can never contain an INTENT -> Stage 1 BLOCKS. A
# passing {"continue": True} result is therefore only reachable when the
# guard correctly preserved the already-truthy override -- the pass/fail
# outcome itself is the discriminating proof, not merely
# mock_reviewer.assert_called_once() in isolation.
# ---------------------------------------------------------------------------


def _mock_anchor_none_prose(
    return_value, outcome: str, stale_text: Optional[str] = None
):
    """Like test_issue_139's _mock_anchor, but with anchor_prose_text
    EXPLICITLY set to None -- reproducing exactly what
    _copy_anchor_shape_flags produces when the source _outcome never set
    the key (e.g. a _turn_has_thinking raise before it)."""

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
            _diagnostics["outcome"] = outcome
            _diagnostics["anchor_prose_text"] = None
            if stale_text is not None:
                _diagnostics["stale_text"] = stale_text
        return return_value

    return _fn


class TestProbeLHookGuardFallsBackWhenAnchorProseTextIsNone(_DbHarness):
    def test_found_override_falls_back_when_anchor_prose_text_is_none(self):
        with (
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                side_effect=_mock_anchor_none_prose(REAL_INTENT_CORE_WITH_TDD, "found"),
            ),
            patch(
                "pacemaker.inference.resolve_and_call_with_reviewer",
                return_value=("APPROVED", "test-reviewer"),
            ) as mock_reviewer,
        ):
            result = self._run(
                "Edit",
                CORE_FILE,
                {
                    "file_path": CORE_FILE,
                    "old_string": "pass",
                    "new_string": "pass  # updated",
                },
            )
        assert result == {"continue": True}, (
            f"anchor_prose_text=None must never downgrade an "
            f"already-truthy override -- a broken guard would fall to "
            f"the n-back fallback against this test's EMPTY transcript "
            f"and BLOCK; got: {result}"
        )
        mock_reviewer.assert_called_once()

    def test_stale_override_falls_back_when_anchor_prose_text_is_none(self):
        with (
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                side_effect=_mock_anchor_none_prose(
                    None, "stale", stale_text=REAL_INTENT_CORE_WITH_TDD
                ),
            ),
            patch(
                "pacemaker.inference.resolve_and_call_with_reviewer",
                return_value=("APPROVED", "test-reviewer"),
            ) as mock_reviewer,
        ):
            result = self._run(
                "Edit",
                CORE_FILE,
                {
                    "file_path": CORE_FILE,
                    "old_string": "pass",
                    "new_string": "pass  # updated",
                },
            )
        assert result == {"continue": True}, (
            f"stale-with-INTENT must still use the matched turn's own "
            f"declaration (not fall back to an n-back rescue against "
            f"this test's EMPTY transcript) when anchor_prose_text is "
            f"None; got: {result}"
        )
        mock_reviewer.assert_called_once()


# ---------------------------------------------------------------------------
# Group G: structural guard -- the danger-bash gate must remain unaffected
# (it never uses extract_current_assistant_message or
# get_last_n_messages_for_validation at all; it is anchor-only per
# issue #93/#139).
# ---------------------------------------------------------------------------


class TestDangerBashGateUnaffected:
    def test_danger_bash_section_does_not_call_n_back_helpers(self):
        import inspect

        from pacemaker import hook

        source = inspect.getsource(hook)
        danger_start = source.index("# 2a. Danger Bash validation")
        write_edit_start = source.index(
            "# 6. Read last 2 messages for validation", danger_start
        )
        danger_bash_section = source[danger_start:write_edit_start]
        assert "extract_current_assistant_message" not in danger_bash_section
        assert "get_last_n_messages_for_validation" not in danger_bash_section
