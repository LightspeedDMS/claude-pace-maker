"""
Issue #151 regression tests (code-review revision): the Opus 5.5
reasoning-summary intent exception.

Background (see #150 and CLAUDE.md's "Issue #150"/"Issue #151" sections):
at xhigh reasoning effort, Opus 5.5 routinely emits Write/Edit/Bash turns
shaped `thinking(empty, signature-only), thinking(~240-char summary),
tool_use`, with NO visible text block at all. #151 removes the "reasoning
is never an intent source" rule for a CONFIGURABLE list of models
(default: ["claude-opus-5-5"]).

CODE REVIEW REVISION (this file)
=================================
The first #151 pass had defects, fixed here:

H1: a malformed `reasoning_summary_intent_models` config value (null,
    False, a bare string, a list with non-string entries) must never crash
    the gate (TypeError -> fail_closed_error) or do a substring match on a
    string value. `pacemaker.hook._normalize_reasoning_summary_intent_models`
    is the shared fix, tested directly and through the real hook.

H2: an Opus turn whose ANCHORED VISIBLE TEXT already carries a real
    INTENT: marker must take the NORMAL strict path (intent_source=
    "declaration") -- the relaxed exception applies ONLY when there is no
    visible marker.
    `pacemaker.intent_validator.resolve_reasoning_summary_intent_source`
    is the shared decision function (used by both gates), tested directly
    and through the real pipeline.

M1: when the exception's own-turn intent_text is empty, the gate must NOT
    hard-block -- it must fall through to the SAME strict Stage 1 path a
    non-exception model would take (including the prose-only n-back rescue,
    #140), rather than short-circuiting.

M2: the danger-bash Phase 2 prompt must not contradict itself when the
    intent source is relaxed (visible_text/reasoning_summary) -- the
    "declared intent"/"INTENT: declaration" wording is reframed to "the
    stated intent" throughout, and the assistant-message label never
    claims "(contains intent declaration)" for a marker-less visible-text
    turn. The non-exception prompt stays byte-identical (locked by a fixed
    expected string).

M3: `stage2_code_review_reasoning_summary.md`'s CHECK 1/CHECK 3/
    CLASSIFICATION VALUES sections must be VERBATIM identical to
    `stage2_code_review.md`'s (the #94 lesson: two independently
    maintained copies of the same judgement criteria drift). Locked by a
    test extracting and diffing those sections between both files.

L1: `intent_source` is added to blockage_events.details ONLY for
    exception-model turns (never `"intent_source": null` for a
    non-exception-model block).

L3/L5/L6: `_build_stage2_prompt_reasoning_summary` drops the unused
    `tool_name` param, a core-path CLASSIFICATION: TDD value flows into
    `tdd_failure`, and `intent_text` is capped at 3000 chars (matching the
    danger-bash gate's own cap).

Mocking boundary (per CLAUDE.md): only the LLM provider boundary
(`pacemaker.inference.resolve_and_call_with_reviewer`) is mocked.
Danger-bash rules are REAL (no mocking of `danger_bash_rules.load_rules`/
`match_command` -- `DEFAULT_DANGER_RULES_PATH` is pointed at a fresh,
nonexistent tmp path so only the BUNDLED defaults apply, isolated from any
real `~/.claude-pace-maker/danger_bash_rules.yaml`). Everything else --
transcript anchor resolution, Stage 1 regex/text logic, the real
`run_pre_tool_hook()`, and real temporary usage.db writes via the
`_guard_production_db` autouse conftest fixture -- runs for real.
"""

import inspect
import json
import os
import sqlite3
from typing import List, Optional
from unittest.mock import MagicMock, patch

os.environ.setdefault("PACEMAKER_TEST_MODE", "1")

from pacemaker.constants import DEFAULT_CONFIG  # noqa: E402
from pacemaker.hook import _normalize_reasoning_summary_intent_models  # noqa: E402
from pacemaker.intent_validator import (  # noqa: E402
    _build_recent_context_section,
    _build_stage2_prompt_reasoning_summary,
    _parse_stage2_classification,
    resolve_reasoning_summary_intent_source,
    validate_intent_and_code,
)
from pacemaker.transcript_reader import _find_turn_matching_tool_input  # noqa: E402

OPUS_MODEL = "claude-opus-5-5"
SONNET_MODEL = "claude-sonnet-4-5-20250929"

REASONING_SUMMARY = (
    "The user wants a rate-limit guard added to fetch_data(). I'll modify "
    "src/example_module.py to add a check before the network call and add "
    "a corresponding unit test."
)

# ---------------------------------------------------------------------------
# JSONL transcript builder helpers
# ---------------------------------------------------------------------------


def _asst(request_id: Optional[str], block: dict, model: Optional[str] = None) -> dict:
    message: dict = {"role": "assistant", "content": [block]}
    if model is not None:
        message["model"] = model
    entry: dict = {"message": message}
    if request_id is not None:
        entry["requestId"] = request_id
    return entry


def _thinking_block(text: str, idx: int) -> dict:
    return {"type": "thinking", "thinking": text, "apiBlockIndex": idx}


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


NONCORE_FILE = "scratch_module/mod.py"
CORE_FILE = "src/example_module.py"


# ===========================================================================
# Group 1: config default (unaffected by the review)
# ===========================================================================


class TestConfigDefault:
    def test_reasoning_summary_intent_models_default(self):
        assert DEFAULT_CONFIG.get("reasoning_summary_intent_models") == [
            "claude-opus-5-5"
        ]


# ===========================================================================
# Group 2: transcript_reader exposes anchor_model / anchor_reasoning_summary
# (unaffected by the review -- kept as regression coverage)
# ===========================================================================


class TestTranscriptReaderAnchorModelAndReasoningSummary:
    def test_found_path_exposes_model_and_reasoning_summary(self, tmp_path):
        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0), model=OPUS_MODEL),
                _asst("req_A", _thinking_block(REASONING_SUMMARY, 1), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Edit",
                        {
                            "file_path": "src/example_module.py",
                            "old_string": "pass",
                            "new_string": "pass  # guarded",
                        },
                        "toolu_A",
                        2,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        outcome: dict = {}
        result = _find_turn_matching_tool_input(
            transcript,
            {
                "file_path": "src/example_module.py",
                "old_string": "pass",
                "new_string": "pass  # guarded",
            },
            "Edit",
            _outcome=outcome,
        )
        assert result == "", "no INTENT: marker present -- found, empty text"
        assert outcome.get("outcome") == "found"
        assert outcome.get("anchor_model") == OPUS_MODEL
        assert outcome.get("anchor_reasoning_summary") == REASONING_SUMMARY

    def test_signature_only_thinking_yields_empty_reasoning_summary(self, tmp_path):
        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": "scratch/x.py", "content": "x = 1\n"},
                        "toolu_A",
                        1,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        outcome: dict = {}
        _find_turn_matching_tool_input(
            transcript,
            {"file_path": "scratch/x.py", "content": "x = 1\n"},
            "Write",
            _outcome=outcome,
        )
        assert outcome.get("anchor_model") == OPUS_MODEL
        assert outcome.get("anchor_reasoning_summary") == ""

    def test_stale_path_also_exposes_model_and_reasoning_summary(self, tmp_path):
        transcript = str(tmp_path / "t.jsonl")
        _write_transcript(
            [
                _asst("req_A", _thinking_block(REASONING_SUMMARY, 0), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": "scratch/x.py", "content": "x = 1\n"},
                        "toolu_A",
                        1,
                    ),
                    model=OPUS_MODEL,
                ),
                _tool_result_entry("ok", "toolu_A"),
            ],
            transcript,
        )
        outcome: dict = {}
        result = _find_turn_matching_tool_input(
            transcript,
            {"file_path": "scratch/x.py", "content": "x = 1\n"},
            "Write",
            _outcome=outcome,
        )
        assert result is None
        assert outcome.get("outcome") == "stale"
        assert outcome.get("anchor_model") == OPUS_MODEL
        assert outcome.get("anchor_reasoning_summary") == REASONING_SUMMARY


# ===========================================================================
# Group: live-test follow-up (round 2) -- anchor-relative RECENT CONTEXT.
# _find_turn_matching_tool_input now populates _outcome["anchor_recent_context"]
# directly from the SAME tail window it already read, instead of hook.py
# doing a SECOND full-transcript parse (measured 0.824s on top of the
# pre-existing 1.216s n=2 call, on a real 99.7MB transcript -- eating the
# shared _gate_deadline before Stage 2 could even run). Set on BOTH the
# "found" and "stale" branches, as a list of (visible_text, thinking_text)
# tuples for up to 3 LOGICAL assistant turns immediately BEFORE the anchor's
# own turn, oldest to newest -- the anchor's own key is never included, by
# construction, so this also fixes the stale-path bug where the old
# `prose[:-1]` positional slice could surface the anchor's OWN re-issued
# text as "context" once its thinking was flushed.
# ===========================================================================


class TestAnchorRecentContextField:
    def test_found_path_with_no_prior_turns_is_empty(self, tmp_path):
        transcript = _write_transcript(
            [
                _asst("req_A", _text_block("only turn", 0), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": "scratch/x.py", "content": "x = 1\n"},
                        "toolu_A",
                        1,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        outcome: dict = {}
        _find_turn_matching_tool_input(
            transcript,
            {"file_path": "scratch/x.py", "content": "x = 1\n"},
            "Write",
            _outcome=outcome,
        )
        assert outcome.get("outcome") == "found"
        assert outcome.get("anchor_recent_context") == []

    def test_found_path_with_one_prior_turn(self, tmp_path):
        transcript = _write_transcript(
            [
                _asst(
                    "req_PLAN", _thinking_block(PRIOR_PLAN_SUMMARY, 0), model=OPUS_MODEL
                ),
                _asst(
                    "req_PLAN",
                    _tool_use_block("Read", {"file_path": "a.py"}, "toolu_PLAN", 1),
                    model=OPUS_MODEL,
                ),
                _asst("req_A", _text_block(TERSE_CURRENT_VISIBLE, 0), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": "scratch/x.py", "content": "x = 1\n"},
                        "toolu_A",
                        1,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        outcome: dict = {}
        _find_turn_matching_tool_input(
            transcript,
            {"file_path": "scratch/x.py", "content": "x = 1\n"},
            "Write",
            _outcome=outcome,
        )
        assert outcome.get("outcome") == "found"
        context = outcome.get("anchor_recent_context")
        assert context == [("", PRIOR_PLAN_SUMMARY)]

    def test_found_path_caps_at_three_most_recent_prior_turns_ordered(self, tmp_path):
        lines = []
        for i in range(5):
            lines.append(
                _asst(f"req_{i}", _thinking_block(f"plan-{i}", 0), model=OPUS_MODEL)
            )
            lines.append(
                _asst(
                    f"req_{i}",
                    _tool_use_block("Read", {"file_path": f"{i}.py"}, f"toolu_{i}", 1),
                    model=OPUS_MODEL,
                )
            )
        lines.append(_asst("req_A", _text_block("final note", 0), model=OPUS_MODEL))
        lines.append(
            _asst(
                "req_A",
                _tool_use_block(
                    "Write",
                    {"file_path": "scratch/x.py", "content": "x = 1\n"},
                    "toolu_A",
                    1,
                ),
                model=OPUS_MODEL,
            )
        )
        transcript = _write_transcript(lines, str(tmp_path / "t.jsonl"))
        outcome: dict = {}
        _find_turn_matching_tool_input(
            transcript,
            {"file_path": "scratch/x.py", "content": "x = 1\n"},
            "Write",
            _outcome=outcome,
        )
        context = outcome.get("anchor_recent_context")
        assert context == [
            ("", "plan-2"),
            ("", "plan-3"),
            ("", "plan-4"),
        ], f"Expected only the 3 closest prior turns, oldest-to-newest; got {context!r}"

    def test_stale_path_excludes_anchors_own_text_includes_prior_turn(self, tmp_path):
        transcript = str(tmp_path / "t.jsonl")
        _write_transcript(
            [
                _asst(
                    "req_PLAN", _thinking_block(PRIOR_PLAN_SUMMARY, 0), model=OPUS_MODEL
                ),
                _asst(
                    "req_PLAN",
                    _tool_use_block("Read", {"file_path": "a.py"}, "toolu_PLAN", 1),
                    model=OPUS_MODEL,
                ),
                _asst("req_A", _thinking_block(REASONING_SUMMARY, 0), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": "scratch/x.py", "content": "x = 1\n"},
                        "toolu_A",
                        1,
                    ),
                    model=OPUS_MODEL,
                ),
                _tool_result_entry("ok", "toolu_A"),
            ],
            transcript,
        )
        outcome: dict = {}
        result = _find_turn_matching_tool_input(
            transcript,
            {"file_path": "scratch/x.py", "content": "x = 1\n"},
            "Write",
            _outcome=outcome,
        )
        assert result is None
        assert outcome.get("outcome") == "stale"
        context = outcome.get("anchor_recent_context")
        assert context == [("", PRIOR_PLAN_SUMMARY)]
        # The anchor's own reasoning summary must never appear in its own
        # "recent context" -- it's excluded by construction (context is
        # always relative to whichever turn is CURRENTLY anchored).
        flattened = " ".join(v + t for v, t in context)
        assert REASONING_SUMMARY not in flattened


class TestAnchorRecentContextTiming:
    """Issue #151 live-test follow-up round 2, item 1: the anchor-relative
    RECENT CONTEXT computation reuses the SAME fixed-cost tail window
    `_find_turn_matching_tool_input` already reads (mirrors
    tests/test_transcript_staleness_fix.py's TestFixedCostTailRead pattern)
    -- must stay fast regardless of total transcript size, unlike the
    removed second `get_last_n_messages_for_validation(n=4)` full-transcript
    re-parse it replaces."""

    def test_anchor_relative_recent_context_completes_fast_on_large_transcript(
        self, tmp_path
    ):
        import time as time_module

        p = tmp_path / "large_transcript_recent_context.jsonl"
        padding_text = "x" * 2000
        with open(str(p), "w") as f:
            for i in range(15000):
                f.write(
                    json.dumps(_asst(f"req_pad_{i}", _text_block(padding_text, 0)))
                    + "\n"
                )
            for label in ("prior1", "prior2", "prior3"):
                f.write(
                    json.dumps(
                        _asst(
                            f"req_{label}",
                            _thinking_block(f"{label} plan detail", 0),
                            model=OPUS_MODEL,
                        )
                    )
                    + "\n"
                )
                f.write(
                    json.dumps(
                        _asst(
                            f"req_{label}",
                            _tool_use_block(
                                "Read",
                                {"file_path": f"{label}.py"},
                                f"toolu_{label}",
                                1,
                            ),
                            model=OPUS_MODEL,
                        )
                    )
                    + "\n"
                )
            f.write(
                json.dumps(
                    _asst(
                        "req_TARGET",
                        _text_block("final terse note", 0),
                        model=OPUS_MODEL,
                    )
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    _asst(
                        "req_TARGET",
                        _tool_use_block(
                            "Write",
                            {
                                "file_path": "/project/large_file.py",
                                "content": "print('large')",
                            },
                            "toolu_TARGET",
                            1,
                        ),
                        model=OPUS_MODEL,
                    )
                )
                + "\n"
            )

        target_input = {
            "file_path": "/project/large_file.py",
            "content": "print('large')",
        }
        outcome: dict = {}
        t0 = time_module.perf_counter()
        result = _find_turn_matching_tool_input(
            str(p), target_input, "Write", _outcome=outcome
        )
        elapsed = time_module.perf_counter() - t0

        assert result == ""  # no INTENT: marker in "final terse note"
        assert outcome.get("outcome") == "found"
        assert elapsed < 0.1, (
            f"Anchor-relative RECENT CONTEXT must complete well under 100ms "
            f"regardless of file size; took {elapsed:.4f}s"
        )
        context = outcome.get("anchor_recent_context")
        assert context == [
            ("", "prior1 plan detail"),
            ("", "prior2 plan detail"),
            ("", "prior3 plan detail"),
        ], f"Expected the 3 turns immediately before the anchor, oldest-to-newest; got {context!r}"


# ===========================================================================
# Group H1: config-value normalization
# ===========================================================================


class TestH1NormalizeReasoningSummaryIntentModels:
    def test_none_returns_empty_list(self):
        assert _normalize_reasoning_summary_intent_models(None) == []

    def test_false_returns_empty_list(self):
        assert _normalize_reasoning_summary_intent_models(False) == []

    def test_string_value_returns_empty_list_never_substring_matched(self):
        """A bare string config value must NEVER enable the exception via
        Python's `in` operator doing substring matching on a string."""
        result = _normalize_reasoning_summary_intent_models("claude-opus-5-5-and-more")
        assert result == []
        assert "claude-opus-5-5" not in result

    def test_list_with_non_string_entries_filters_them(self):
        result = _normalize_reasoning_summary_intent_models(
            ["claude-opus-5-5", 42, None, {"nested": True}]
        )
        assert result == ["claude-opus-5-5"]

    def test_valid_list_returned_unchanged(self):
        result = _normalize_reasoning_summary_intent_models(
            ["claude-opus-5-5", "some-other-model"]
        )
        assert result == ["claude-opus-5-5", "some-other-model"]

    def test_empty_list_returns_empty_list(self):
        assert _normalize_reasoning_summary_intent_models([]) == []


# ===========================================================================
# Group H2/M1: resolve_reasoning_summary_intent_source -- shared decision
# logic used by BOTH gates.
# ===========================================================================


class TestH2M1ResolveReasoningSummaryIntentSource:
    def test_non_exception_model_returns_none_none(self):
        source, relaxed = resolve_reasoning_summary_intent_source(
            False, "some visible text with no marker", REASONING_SUMMARY
        )
        assert (source, relaxed) == (None, None)

    def test_visible_marker_present_returns_declaration_no_relaxed_text(self):
        """H2: a real INTENT: declaration in the anchor's own VISIBLE text
        must take the STRICT path -- the relaxed exception never applies."""
        source, relaxed = resolve_reasoning_summary_intent_source(
            True, "INTENT: Edit mod.py to add a helper.", REASONING_SUMMARY
        )
        assert source == "declaration"
        assert relaxed is None

    def test_marker_check_is_visible_text_only_not_summary(self):
        """A reasoning SUMMARY containing "INTENT:" must NOT trigger the
        declaration path -- only the anchor's own VISIBLE text is checked
        (matches the structural INTENT:-in-content-never-counts guarantee
        elsewhere in the codebase)."""
        source, relaxed = resolve_reasoning_summary_intent_source(
            True, "", "INTENT: this INTENT: text is only in the summary"
        )
        assert source == "reasoning_summary"
        assert relaxed == "INTENT: this INTENT: text is only in the summary"

    def test_visible_no_marker_with_summary_returns_reasoning_summary(self):
        source, relaxed = resolve_reasoning_summary_intent_source(
            True, "quick note", REASONING_SUMMARY
        )
        assert source == "reasoning_summary"
        assert relaxed == f"quick note\n\n{REASONING_SUMMARY}"

    def test_visible_no_marker_no_summary_returns_visible_text(self):
        source, relaxed = resolve_reasoning_summary_intent_source(
            True, "I'm going to add a helper here.", ""
        )
        assert source == "visible_text"
        assert relaxed == "I'm going to add a helper here."

    def test_both_empty_m1_returns_none_source_none_relaxed(self):
        """M1: nothing to relax against -- fall through to the STRICT
        path (same as a non-exception model's empty-anchor case), not a
        hard exception-specific block. Re-review follow-up: this must be
        tagged "none", NOT "declaration" -- there was no declaration, and
        conflating the two would make a no_visible_text=True blockage row
        say intent_source="declaration" (contradictory) and would pad any
        count of "compliant Opus turns". The key is still populated (never
        omitted) so these turns stay countable under their own value."""
        source, relaxed = resolve_reasoning_summary_intent_source(True, "", "")
        assert source == "none"
        assert relaxed is None

    def test_none_inputs_treated_as_empty(self):
        source, relaxed = resolve_reasoning_summary_intent_source(True, None, None)
        assert source == "none"
        assert relaxed is None


# ===========================================================================
# Group (round 3, CHANGE 2): empty anchor turn falls back to the
# IMMEDIATELY PRECEDING turn's own reasoning summary / visible text.
#
# Live evidence: 4 live blocks in one day, and 84 of 1316 historical Opus
# edits, where the anchored turn has NO visible text and NO reasoning
# summary, but the immediately preceding assistant turn has a reasoning
# summary describing the edit -- e.g. "I'll add a new test in
# test_elevate_session.py verifying that calls missing a session key
# return missing_session_key...".
# ===========================================================================


class TestChange2PriorReasoningSummaryFallback:
    def test_empty_anchor_with_prior_summary_uses_prior_reasoning_summary(self):
        prior_summary = (
            "I'll add a new test in test_elevate_session.py verifying "
            "that calls missing a session key return missing_session_key."
        )
        source, relaxed = resolve_reasoning_summary_intent_source(
            True,
            "",
            "",
            recent_context=[("", prior_summary)],
        )
        assert source == "prior_reasoning_summary"
        assert relaxed == prior_summary

    def test_empty_anchor_with_prior_visible_text_only(self):
        source, relaxed = resolve_reasoning_summary_intent_source(
            True,
            "",
            "",
            recent_context=[("previous turn's own visible note", "")],
        )
        assert source == "prior_reasoning_summary"
        assert relaxed == "previous turn's own visible note"

    def test_empty_anchor_with_empty_prior_turn_falls_through_to_none(self):
        """If the previous turn is ALSO empty, fall through to the
        pre-existing M1 behavior exactly as if recent_context had never
        been supplied."""
        source, relaxed = resolve_reasoning_summary_intent_source(
            True,
            "",
            "",
            recent_context=[("", "")],
        )
        assert source == "none"
        assert relaxed is None

    def test_empty_anchor_with_no_recent_context_falls_through_to_none(self):
        source, relaxed = resolve_reasoning_summary_intent_source(
            True, "", "", recent_context=None
        )
        assert source == "none"
        assert relaxed is None

    def test_empty_anchor_with_empty_recent_context_list_falls_through(self):
        source, relaxed = resolve_reasoning_summary_intent_source(
            True, "", "", recent_context=[]
        )
        assert source == "none"
        assert relaxed is None

    def test_only_the_last_prior_turn_is_consulted(self):
        """Only the IMMEDIATELY preceding turn (the LAST element of the
        oldest-to-newest recent_context list) is consulted -- an EARLIER
        prior turn's own summary must never be used."""
        source, relaxed = resolve_reasoning_summary_intent_source(
            True,
            "",
            "",
            recent_context=[("", "an even earlier turn's plan"), ("", "")],
        )
        assert source == "none"
        assert relaxed is None

    def test_non_empty_anchor_is_unaffected_by_recent_context(self):
        """A non-empty anchor's own text always wins -- recent_context is
        consulted ONLY when the anchor itself is empty."""
        source, relaxed = resolve_reasoning_summary_intent_source(
            True,
            "the anchor's own note",
            "",
            recent_context=[("", "should never be used")],
        )
        assert source == "visible_text"
        assert relaxed == "the anchor's own note"

    def test_non_exception_model_ignores_recent_context(self):
        source, relaxed = resolve_reasoning_summary_intent_source(
            False, "", "", recent_context=[("", "some prior summary")]
        )
        assert (source, relaxed) == (None, None)


class TestAllowPriorTurnFallbackParam:
    """Issue #154 item 2: the CHANGE 2 fallback is restricted to Write/Edit
    only via an EXPLICIT `allow_prior_turn_fallback` parameter on the
    resolver -- never an implicit tool-name check buried inside it. The
    user's approval for CHANGE 2 was based on Edit evidence only; letting
    it also apply to danger-bash relaxes #93's deliberate "Bash is
    anchor-only" tightening and #139's stale-path anchor-only rule."""

    def test_default_allows_the_fallback(self):
        """Default (unset) preserves CHANGE 2's original behavior for
        any caller that doesn't explicitly opt out."""
        source, relaxed = resolve_reasoning_summary_intent_source(
            True, "", "", recent_context=[("", "a usable prior summary")]
        )
        assert source == "prior_reasoning_summary"
        assert relaxed == "a usable prior summary"

    def test_false_disables_the_fallback_even_with_a_usable_prior_turn(self):
        source, relaxed = resolve_reasoning_summary_intent_source(
            True,
            "",
            "",
            recent_context=[("", "a usable prior summary")],
            allow_prior_turn_fallback=False,
        )
        assert source == "none"
        assert relaxed is None

    def test_false_does_not_affect_the_anchors_own_non_empty_text(self):
        """The flag only gates the FALLBACK path -- it must never affect
        the anchor's own text winning when it is non-empty."""
        source, relaxed = resolve_reasoning_summary_intent_source(
            True,
            "the anchor's own note",
            "",
            recent_context=[("", "should never matter")],
            allow_prior_turn_fallback=False,
        )
        assert source == "visible_text"
        assert relaxed == "the anchor's own note"


# ===========================================================================
# Group: validate_intent_and_code -- non-exception path fixed-string
# regression lock (replaces the tautological "compare two current-code
# calls" test).
# ===========================================================================

_EXPECTED_NO_BRANCH_RAW = """⛔ Intent declaration required

You must declare your intent BEFORE using Write/Edit tools.

⚠️  CRITICAL: Start with "INTENT:" marker!

Required format - include ALL 3 components IN YOUR CURRENT MESSAGE:
  1. FILE: Which file you're modifying
  2. CHANGES: What specific changes you're making
  3. GOAL: Why you're making these changes

Example (all in same message as Write/Edit):
  "INTENT: Modify src/auth.py to add a validate_input() function
   that checks user input for XSS attacks, to improve security."

Then use your Write/Edit tool in the same message."""

_EXPECTED_SDK_UNAVAILABLE_RAW = """⛔ Intent Validation Unavailable

Claude Agent SDK is not available for Stage 2 code review.

This is a REQUIRED dependency for intent validation to function.
Please install the SDK or disable intent validation in config:

  pace-maker tdd off

System failing closed to prevent bypassing intent declaration requirements."""


class TestNonExceptionModelsFixedStringRegression:
    """Fixed-string assertions captured from the pre-#151 behaviour --
    NOT a tautological "compare two current-code calls" test."""

    def test_no_branch_matches_pre_151_fixed_string(self):
        result = validate_intent_and_code(
            messages=[],
            code="x = 1\n",
            file_path=NONCORE_FILE,
            tool_name="Write",
            current_message_override="",
        )
        assert result["approved"] is False
        assert result["raw_feedback"] == _EXPECTED_NO_BRANCH_RAW
        assert result["feedback"] == (
            "[pace-maker · intent_validation_block]\n" + _EXPECTED_NO_BRANCH_RAW
        )
        assert "intent_source" not in result

    def test_sdk_unavailable_matches_pre_151_fixed_string(self):
        # Force the branch deterministically (SDK_AVAILABLE is
        # environment-dependent -- this test env has claude_agent_sdk
        # installed, so without this patch the branch is never reached
        # and Stage 2 would hit the real, unmocked Anthropic SDK).
        with patch("pacemaker.intent_validator.SDK_AVAILABLE", False):
            result = validate_intent_and_code(
                messages=[],
                code="x = 1\n",
                file_path=NONCORE_FILE,
                tool_name="Write",
                current_message_override="INTENT: Modify mod.py to add a helper.",
                hook_model="auto",
            )
        assert result["raw_feedback"] == _EXPECTED_SDK_UNAVAILABLE_RAW
        assert "intent_source" not in result

    def test_no_visible_text_true_matches_pre_150_notice_and_no_intent_source_key(self):
        result = validate_intent_and_code(
            messages=[],
            code="x = 1\n",
            file_path=NONCORE_FILE,
            tool_name="Write",
            current_message_override="",
            no_visible_text=True,
        )
        assert result["approved"] is False
        assert "⛔ Your message had NO visible text" in result["feedback"]
        assert "intent_source" not in result


# ===========================================================================
# Group H2/M1 (integration level): validate_intent_and_code's new
# reasoning_summary_relaxed_text / reasoning_summary_intent_source params.
# ===========================================================================


class TestValidateIntentAndCodeReasoningSummaryParams:
    def test_relaxed_text_none_and_source_none_is_byte_identical_to_omission(self):
        without_params = validate_intent_and_code(
            messages=[],
            code="x = 1\n",
            file_path=NONCORE_FILE,
            tool_name="Write",
            current_message_override="",
            no_visible_text=True,
        )
        with_params_explicit_none = validate_intent_and_code(
            messages=[],
            code="x = 1\n",
            file_path=NONCORE_FILE,
            tool_name="Write",
            current_message_override="",
            no_visible_text=True,
            reasoning_summary_relaxed_text=None,
            reasoning_summary_intent_source=None,
        )
        assert without_params == with_params_explicit_none

    def test_declaration_source_with_none_relaxed_text_takes_strict_path_tagged(self):
        """H2 integration: intent_source="declaration" (relaxed_text=None)
        must run the NORMAL strict Stage 1 (TDD enforced on core paths)
        and tag the result "declaration"."""
        result = validate_intent_and_code(
            messages=[],
            code="def f():\n    pass\n",
            file_path=CORE_FILE,
            tool_name="Write",
            # A real INTENT: declaration, but NO TDD declaration --
            # the STRICT path must still reject this as NO_TDD.
            current_message_override="INTENT: Modify example_module.py to add f().",
            reasoning_summary_relaxed_text=None,
            reasoning_summary_intent_source="declaration",
        )
        assert result["approved"] is False
        assert result.get("tdd_failure") is True
        assert result["intent_source"] == "declaration"
        assert "TDD Required" in result["raw_feedback"]

    def test_declaration_source_approves_with_tdd_declared(self):
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            result = validate_intent_and_code(
                messages=[
                    "INTENT: Modify example_module.py to add f().\n"
                    "Test coverage: tests/test_x.py - test_f()"
                ],
                code="def f():\n    pass\n",
                file_path=CORE_FILE,
                tool_name="Write",
                current_message_override=(
                    "INTENT: Modify example_module.py to add f().\n"
                    "Test coverage: tests/test_x.py - test_f()"
                ),
                reasoning_summary_relaxed_text=None,
                reasoning_summary_intent_source="declaration",
            )
        assert result["approved"] is True
        assert result["intent_source"] == "declaration"
        # H2: the NORMAL Stage 2 prompt must be used -- no reasoning-summary
        # framing language.
        prompt = mock_reviewer.call_args.kwargs["prompt"]
        assert "auto-summarized" not in prompt.lower()

    def test_m1_empty_intent_falls_through_to_normal_no_branch_not_hard_block(self):
        """M1: reasoning_summary_relaxed_text=None + intent_source="none"
        (the resolver's own M1 output for a fully-empty anchor -- re-review
        follow-up: NOT "declaration", since there was no declaration and
        tagging it as one would contradict a no_visible_text=True blockage
        row and pad any count of compliant Opus turns) must behave EXACTLY
        like a non-exception model's empty current_message_override -- same
        NO-branch text, just tagged with its own distinct value."""
        result = validate_intent_and_code(
            messages=[],
            code="x = 1\n",
            file_path=NONCORE_FILE,
            tool_name="Write",
            current_message_override="",
            no_visible_text=True,
            reasoning_summary_relaxed_text=None,
            reasoning_summary_intent_source="none",
        )
        assert result["approved"] is False
        assert result["raw_feedback"].endswith(_EXPECTED_NO_BRANCH_RAW)
        assert "⛔ Your message had NO visible text" in result["raw_feedback"]
        assert result["intent_source"] == "none"

    def test_m1_fragmented_turn_rescue_still_works_for_exception_model(self):
        """M1's own repro: turn req_P = visible INTENT: Edit <file>... ;
        turn req_A = signature-only thinking + Edit. The exception model's
        empty own-turn intent must fall through to the prose-only n-back
        rescue (#140) and PASS, exactly like Sonnet would. The resolver
        cannot know in advance whether the n-back rescue will succeed, so
        it tags this "none" (the same value as a fully-empty M1 turn) --
        the rescued approval keeping "none" rather than being upgraded to
        "declaration" after the fact is an accepted, documented choice."""
        real_intent = "INTENT: Modify mod.py to add a helper function."
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            result = validate_intent_and_code(
                messages=[real_intent, ""],
                code="x = 1\n",
                file_path=NONCORE_FILE,
                tool_name="Edit",
                current_message_override="",
                stage1_fallback_messages=[real_intent, ""],
                reasoning_summary_relaxed_text=None,
                reasoning_summary_intent_source="none",
            )
        assert result["approved"] is True, result
        assert result["intent_source"] == "none"
        mock_reviewer.assert_called_once()

    def test_relaxed_reasoning_summary_source_passes_and_tagged(self):
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            result = validate_intent_and_code(
                messages=[],
                code="x = 1\n",
                file_path=NONCORE_FILE,
                tool_name="Write",
                current_message_override="",
                reasoning_summary_relaxed_text=REASONING_SUMMARY,
                reasoning_summary_intent_source="reasoning_summary",
            )
        assert result["approved"] is True
        assert result["intent_source"] == "reasoning_summary"
        prompt = mock_reviewer.call_args.kwargs["prompt"]
        assert REASONING_SUMMARY in prompt
        assert "auto-summarized" in prompt.lower()

    def test_recent_context_threaded_into_relaxed_prompt_only(self):
        """reasoning_summary_recent_context must reach the relaxed Stage 2
        prompt when the relaxed path is taken, via a real prior-plan
        excerpt naming the live repro's own phrasing."""
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            result = validate_intent_and_code(
                messages=[],
                code="x = 1\n",
                file_path=NONCORE_FILE,
                tool_name="Write",
                current_message_override="",
                reasoning_summary_relaxed_text="Now the SSH tests.",
                reasoning_summary_intent_source="visible_text",
                reasoning_summary_recent_context=[("", PRIOR_PLAN_SUMMARY)],
            )
        assert result["approved"] is True
        prompt = mock_reviewer.call_args.kwargs["prompt"]
        assert PRIOR_PLAN_SUMMARY in prompt
        assert "RECENT CONTEXT" in prompt

    def test_recent_context_ignored_on_strict_declaration_path(self):
        """H2: the STRICT path (real declaration, or non-exception model)
        must NEVER render a RECENT CONTEXT section, even when the caller
        supplies one -- byte-identical to before this follow-up."""
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            validate_intent_and_code(
                messages=[
                    "INTENT: Modify mod.py to add a helper.\n" "Test coverage: x"
                ],
                code="x = 1\n",
                file_path=NONCORE_FILE,
                tool_name="Write",
                current_message_override="INTENT: Modify mod.py to add a helper.",
                reasoning_summary_relaxed_text=None,
                reasoning_summary_intent_source="declaration",
                reasoning_summary_recent_context=[("", PRIOR_PLAN_SUMMARY)],
            )
        prompt = mock_reviewer.call_args.kwargs["prompt"]
        # NOTE: the NORMAL stage2_code_review.md template has its own,
        # unrelated, pre-existing "RECENT CONTEXT (last 2 messages):"
        # header -- so the assertion below checks for the NEW section's
        # own distinct label, not the ambiguous substring "RECENT CONTEXT".
        assert "context only" not in prompt.lower()
        assert PRIOR_PLAN_SUMMARY not in prompt

    def test_relaxed_visible_text_source_passes_and_tagged(self):
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            result = validate_intent_and_code(
                messages=[],
                code="x = 1\n",
                file_path=NONCORE_FILE,
                tool_name="Write",
                current_message_override="",
                reasoning_summary_relaxed_text="I'm going to add a helper here.",
                reasoning_summary_intent_source="visible_text",
            )
        assert result["approved"] is True
        assert result["intent_source"] == "visible_text"
        mock_reviewer.assert_called_once()
        assert (
            "I'm going to add a helper here."
            in mock_reviewer.call_args.kwargs["prompt"]
        )

    def test_relaxed_path_stage2_rejection_carries_intent_source_and_no_regex(self):
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("BLOCKED: scope creep", "test-reviewer"),
        ):
            result = validate_intent_and_code(
                messages=[],
                code="def validate():\n    pass\n",
                file_path=CORE_FILE,
                tool_name="Write",
                current_message_override="",
                reasoning_summary_relaxed_text="going to add validation logic now",
                reasoning_summary_intent_source="reasoning_summary",
            )
        assert result["approved"] is False
        assert result["intent_source"] == "reasoning_summary"
        assert "scope creep" in result["feedback"]

    def test_relaxed_path_core_path_note_and_tdd_classification_sets_tdd_failure(self):
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("BLOCKED: missing tests\n\nCLASSIFICATION: TDD", "r"),
        ):
            result = validate_intent_and_code(
                messages=[],
                code="def f():\n    pass\n",
                file_path=CORE_FILE,
                tool_name="Write",
                current_message_override="",
                reasoning_summary_relaxed_text=REASONING_SUMMARY,
                reasoning_summary_intent_source="reasoning_summary",
            )
        assert result["approved"] is False
        assert result.get("tdd_failure") is True
        assert result.get("clean_code_failure") is not True
        assert result.get("bug_failure") is not True


# ===========================================================================
# Group: manual-replay follow-up -- the core-path note's test-coverage
# wording must match _has_tdd_declaration's (normal-path) semantics, not a
# stricter "added or updated" reading. Repro (real reviewer replay):
#   R06 -- a docstring-only edit that NAMED an existing test file was
#          rejected because it wasn't "added or updated".
#   R17 -- a TDD green step (making an existing FAILING test pass) was
#          rejected for the same reason.
# ===========================================================================


def _load_core_path_note() -> str:
    return _load_template("reasoning_summary_core_path_note.md")


class TestCorePathNoteMatchesNormalPathSemantics:
    def test_still_accepts_added_or_updated_tests(self):
        """One of the three valid cases -- must not be lost."""
        note = _load_core_path_note()
        assert "added or updated" in note.lower()

    def test_accepts_existing_test_named_or_referred_to(self):
        """R06's repro: naming/referring to an EXISTING test that covers
        the change (not a NEW test) must count as satisfied, not a
        violation."""
        note = _load_core_path_note()
        assert "existing test" in note.lower()
        assert "named" in note.lower() or "referred to" in note.lower()

    def test_accepts_making_a_failing_test_pass(self):
        """R17's repro: a TDD green step (making a currently-failing test
        pass) must be explicitly called out as satisfying coverage, not a
        missing-coverage violation."""
        note = _load_core_path_note()
        assert "failing test" in note.lower()

    def test_rejects_negative_or_deferred_test_mentions_as_coverage(self):
        """Re-review follow-up: "no reference to tests at all" was itself
        a loophole -- a weak verifier could read "no tests needed" or
        "tests later" as not being a "reference to tests" and wrongly
        approve. The note must explicitly say that absent/skipped/
        deferred/not-needed/later mentions do NOT count as coverage,
        mirroring how `_has_tdd_declaration` requires a structured
        marker, `covered by <X>`, or quoted user permission -- never a
        bare mention that tests don't exist yet."""
        # Whitespace-normalized (single spaces) so a multi-word phrase
        # that happens to line-wrap in the .md source (a rendered LLM
        # prompt reads it as continuous prose regardless of source line
        # breaks) still matches.
        lowered = " ".join(_load_core_path_note().lower().split())
        assert "does not count" in lowered or "does not count as coverage" in lowered
        for word in ("absent", "skipped", "deferred", "not needed", "later"):
            assert word in lowered, f"missing negative-mention word: {word!r}"

    def test_classification_tdd_applies_unless_a_listed_case_matches(self):
        """The classification instruction must be phrased as "TDD unless
        one of the [listed] cases applies" -- not the old, loophole-prone
        "no reference to tests at all" framing."""
        lowered = " ".join(_load_core_path_note().lower().split())
        assert "unless one of the" in lowered

    def test_accepts_quoted_user_permission_to_skip_tests(self):
        """Parity with the normal path: `_has_tdd_declaration` accepts an
        explicit user permission to skip TDD (quoted from the user) as
        satisfying coverage -- the relaxed-path note must offer the same
        escape hatch, not just the three test-coverage cases."""
        note = _load_core_path_note()
        lowered = note.lower()
        assert "permission" in lowered
        assert "quoted" in lowered or "quote" in lowered
        assert "skip" in lowered

    def test_still_accepts_edit_itself_being_a_test(self):
        note = _load_core_path_note()
        assert "is a test" in note.lower() or "itself is a test" in note.lower()

    def test_still_uses_classification_tdd(self):
        assert "CLASSIFICATION: TDD" in _load_core_path_note()

    def test_kept_short_for_weak_verifiers(self):
        """Bug #87 principle: prompt text read by weak verifiers must stay
        short and unambiguous. Not a hard science, but a regression guard
        against the note ballooning back into a multi-paragraph essay."""
        note = _load_core_path_note()
        assert len(note) < 1200, (
            f"core-path note grew to {len(note)} chars -- keep it short "
            "for weak verifiers (#87)"
        )

    def test_rendered_into_relaxed_stage2_prompt_for_core_paths(self):
        """Sanity: the note actually reaches the real Stage 2 prompt when
        the target is a core path."""
        prompt = _build_stage2_prompt_reasoning_summary(
            REASONING_SUMMARY, "def f():\n    pass\n", CORE_FILE, True
        )
        assert "existing test" in prompt.lower()
        assert "failing test" in prompt.lower()


# ===========================================================================
# Group (round 3, CHANGE 2): the reviewer must be told when the intent
# came from the PREVIOUS message, not this one.
# ===========================================================================


class TestPriorReasoningSummaryPromptNotice:
    def test_default_omits_the_notice(self):
        """Byte-identical-by-default guarantee: every pre-existing caller
        that doesn't pass intent_source (or passes "reasoning_summary"/
        "visible_text"/"declaration") gets no notice at all."""
        prompt = _build_stage2_prompt_reasoning_summary(
            REASONING_SUMMARY, "x = 1\n", NONCORE_FILE, False
        )
        assert "PREVIOUS message" not in prompt

    def test_prior_reasoning_summary_source_adds_the_notice(self):
        prompt = _build_stage2_prompt_reasoning_summary(
            "prior turn's own text",
            "x = 1\n",
            NONCORE_FILE,
            False,
            intent_source="prior_reasoning_summary",
        )
        assert "prior turn's own text" in prompt
        assert "PREVIOUS" in prompt.upper()
        assert "no visible text" in prompt.lower() or "no reasoning" in prompt.lower()

    def test_reasoning_summary_source_does_not_add_the_notice(self):
        prompt = _build_stage2_prompt_reasoning_summary(
            REASONING_SUMMARY,
            "x = 1\n",
            NONCORE_FILE,
            False,
            intent_source="reasoning_summary",
        )
        assert "PREVIOUS message" not in prompt


# ===========================================================================
# Group L5: CLASSIFICATION: TDD recognized by the shared parser
# ===========================================================================


class TestL5ClassificationTdd:
    def test_classification_tdd_returns_tdd(self):
        assert _parse_stage2_classification("CLASSIFICATION: TDD") == "tdd"

    def test_classification_tdd_case_insensitive_and_anywhere(self):
        assert (
            _parse_stage2_classification("some feedback\nClassification: tdd\n")
            == "tdd"
        )

    def test_normal_path_unaffected_default_still_clean_code(self):
        assert _parse_stage2_classification("no classification line") == "clean_code"

    def test_normal_path_tdd_classification_falls_through_to_generic_category(self):
        """Re-review follow-up (softened docstring claim): the parser has
        no way to know which template produced a response, so a
        hypothetical normal-path reviewer emitting "CLASSIFICATION: TDD"
        (nothing instructs it to, but this isn't structurally prevented)
        is NOT special-cased -- the normal path's Stage 2 rejected branch
        never reads "tdd_failure" at all, only "clean_code_failure"/
        "bug_failure", both of which come back False here. hook.py's
        category chain would then fall through to the generic
        "intent_validation" category, not "intent_validation_tdd" -- a
        less-specific label, not a misclassification into the wrong
        specific category."""
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("BLOCKED: missing tests\n\nCLASSIFICATION: TDD", "r"),
        ):
            result = validate_intent_and_code(
                messages=[],
                code="def f():\n    pass\n",
                file_path=CORE_FILE,
                tool_name="Write",
                current_message_override=(
                    "INTENT: Modify example_module.py to add f().\n"
                    "Test coverage: tests/test_x.py - test_f()"
                ),
            )
        assert result["approved"] is False
        assert "tdd_failure" not in result
        assert result.get("clean_code_failure") is False
        assert result.get("bug_failure") is False

    def test_relaxed_template_response_format_offers_tdd_option(self):
        """Re-review follow-up: the relaxed template's own RESPONSE FORMAT
        "Choose EXACTLY one" list must offer CLASSIFICATION: TDD as a
        distinct option, since the core-path note asks the reviewer to
        emit exactly that line. The normal template must stay unchanged
        (never gains a TDD option -- it has no core-path-note mechanism at
        all)."""
        relaxed = _load_template("stage2_code_review_reasoning_summary.md")
        normal = _load_template("stage2_code_review.md")
        response_format_choice = relaxed[
            relaxed.rindex("RESPONSE FORMAT - Choose EXACTLY one:") :
        ]
        assert "CLASSIFICATION: TDD" in response_format_choice
        normal_response_format_choice = normal[
            normal.rindex("RESPONSE FORMAT - Choose EXACTLY one:") :
        ]
        assert "CLASSIFICATION: TDD" not in normal_response_format_choice


# ===========================================================================
# Group L3/L6: _build_stage2_prompt_reasoning_summary signature + cap
# ===========================================================================


class TestL3L6StageTwoPromptBuilder:
    def test_no_tool_name_param(self):
        sig = inspect.signature(_build_stage2_prompt_reasoning_summary)
        assert "tool_name" not in sig.parameters

    def test_intent_text_capped_at_3000_chars(self):
        long_text = "x" * 5000
        prompt = _build_stage2_prompt_reasoning_summary(
            long_text, "code", NONCORE_FILE, False
        )
        assert long_text not in prompt
        assert ("x" * 3000) in prompt

    def test_braces_in_intent_text_code_and_path_do_not_crash(self):
        prompt = _build_stage2_prompt_reasoning_summary(
            "intent with {braces} and {{double}}",
            "code = {'a': 1}\ndef f(x={}): pass\n",
            "src/{templated}/mod.py",
            True,
        )
        assert "{braces}" in prompt
        assert "{'a': 1}" in prompt


# ===========================================================================
# Group: live-test follow-up -- RECENT CONTEXT section (relaxed Stage 2
# prompt, Write/Edit only). Repro: an Opus 5.5 turn with terse visible text
# ("Now the SSH, git, tool-access and API-key test expectations.") and no
# thinking was rejected 3x at CHECK 0 by haiku, even though the PREVIOUS
# turn's reasoning summary held the full plan -- the relaxed path is
# anchor-only, with no history, so the reviewer never saw it.
# ===========================================================================

PRIOR_PLAN_SUMMARY = (
    "I'll update test_account_entry_points.py so failure rows assert "
    "placeholders instead of caller-supplied ids, SSH rows check for "
    "fingerprint-derived ids with no key name, and I'll add new RED tests "
    "covering raw-key-shaped ids…"
)
TERSE_CURRENT_VISIBLE = "Now the SSH, git, tool-access and API-key test expectations."


class TestBuildRecentContextSection:
    def test_empty_or_none_returns_empty_string(self):
        assert _build_recent_context_section(None) == ""
        assert _build_recent_context_section([]) == ""

    def test_turn_with_neither_visible_nor_thinking_is_skipped(self):
        assert _build_recent_context_section([("", "")]) == ""

    def test_single_turn_includes_visible_and_thinking(self):
        section = _build_recent_context_section([("quick note", PRIOR_PLAN_SUMMARY)])
        assert "quick note" in section
        assert PRIOR_PLAN_SUMMARY in section

    def test_thinking_only_turn_still_included(self):
        section = _build_recent_context_section([("", PRIOR_PLAN_SUMMARY)])
        assert PRIOR_PLAN_SUMMARY in section

    def test_multiple_turns_ordered_oldest_to_newest(self):
        section = _build_recent_context_section(
            [("oldest turn", ""), ("middle turn", ""), ("newest turn", "")]
        )
        assert section.index("oldest turn") < section.index("middle turn")
        assert section.index("middle turn") < section.index("newest turn")

    def test_header_is_clearly_labelled_context_only(self):
        section = _build_recent_context_section([("note", "")])
        assert "RECENT CONTEXT" in section
        assert "context only" in section.lower()
        assert "not the intent" in section.lower()

    def test_instructs_current_intent_still_governs(self):
        section = _build_recent_context_section([("note", "")])
        lowered = " ".join(section.lower().split())
        assert "current intent" in lowered
        assert "match" in lowered

    def test_instructs_never_approve_context_only_match(self):
        section = _build_recent_context_section([("note", "")])
        lowered = " ".join(section.lower().split())
        assert "never approve" in lowered
        assert "contradict" in lowered
        assert "unrelated" in lowered

    def test_cap_drops_oldest_turns_keeping_most_recent(self):
        turns = [(f"turn-{i} " + "x" * 900, "") for i in range(5)]
        section = _build_recent_context_section(turns, max_chars=2000)
        assert "turn-4" in section  # most recent always kept
        assert "turn-0" not in section  # oldest dropped first
        # The kept turns must still be within the cap (plus header/labels).
        # Margin widened from 700 to 900 (re-review follow-up: the shared
        # header note grew to state the explicit-intent guard against
        # RECENT CONTEXT overriding an explicit current intent -- see
        # TestRecentContextNeverOverridesExplicitIntent in
        # test_issue_153_edit_context.py). Measured header length is 878
        # chars; 900 gives headroom for minor future wording tweaks.
        assert len(section) < 2000 + 900  # body cap + the fixed static header

    def test_single_oversized_turn_alone_is_truncated_not_dropped(self):
        huge = "y" * 5000
        section = _build_recent_context_section([("", huge)], max_chars=2000)
        assert section != ""
        assert len(section) < 2000 + 900  # body cap + the fixed static header

    def test_single_oversized_thinking_turn_preserves_its_label(self):
        """Round 2 item 4: the truncation fallback previously blind-sliced
        the whole "Reasoning summary: ...huge text..." string from the
        right, which could chop the "Reasoning summary:" label itself off
        the front. The label must survive truncation."""
        huge = "y" * 5000
        section = _build_recent_context_section([("", huge)], max_chars=2000)
        assert "Reasoning summary:" in section

    def test_single_oversized_visible_turn_preserves_its_label(self):
        huge = "z" * 5000
        section = _build_recent_context_section([(huge, "")], max_chars=2000)
        assert "Visible text:" in section

    def test_single_oversized_turn_with_both_lines_preserves_both_labels(self):
        huge_visible = "v" * 3000
        huge_thinking = "t" * 3000
        section = _build_recent_context_section(
            [(huge_visible, huge_thinking)], max_chars=2000
        )
        assert "Visible text:" in section
        assert "Reasoning summary:" in section

    def test_no_rendered_tool_content_ever_appears(self):
        """#140 structural guarantee: this function only ever receives
        PROSE-ONLY text from its caller -- verify it does not itself
        introduce any tool-rendering."""
        section = _build_recent_context_section(
            [("[TOOL: Write]\nfile_path: x.py", "")]
        )
        # Passed through verbatim (the function trusts its caller's
        # prose-only contract) -- this test documents that contract lives
        # in the CALLER (get_last_n_messages_for_validation's
        # _with_prose=True), not here.
        assert "[TOOL: Write]" in section


class TestStageTwoPromptRecentContextParam:
    def test_recent_context_renders_into_prompt(self):
        prompt = _build_stage2_prompt_reasoning_summary(
            TERSE_CURRENT_VISIBLE,
            "def f():\n    pass\n",
            NONCORE_FILE,
            False,
            recent_context=[("", PRIOR_PLAN_SUMMARY)],
        )
        assert PRIOR_PLAN_SUMMARY in prompt
        assert "RECENT CONTEXT" in prompt

    def test_no_recent_context_omits_section(self):
        prompt = _build_stage2_prompt_reasoning_summary(
            TERSE_CURRENT_VISIBLE, "def f():\n    pass\n", NONCORE_FILE, False
        )
        # NOTE: CHECK 0 now ALWAYS mentions "RECENT CONTEXT" (round 2 item
        # 3 -- instructs the reviewer to use it WHEN present), so a bare
        # substring check would false-fail here. Check for the SECTION's
        # own distinct header instead.
        assert "RECENT CONTEXT (earlier turns" not in prompt
        assert "context only" not in prompt.lower()

    def test_no_recent_context_leaves_no_extra_blank_line(self):
        """Round 2 item 4 (cosmetic): when there is no RECENT CONTEXT
        section, the gap between the INTENT block and PROPOSED CODE must
        be a single blank line (matching the normal template's own
        spacing convention) -- not two."""
        prompt = _build_stage2_prompt_reasoning_summary(
            TERSE_CURRENT_VISIBLE, "def f():\n    pass\n", NONCORE_FILE, False
        )
        intent_idx = prompt.index("INTENT (auto-summarized")
        code_idx = prompt.index("PROPOSED CODE:")
        between = prompt[intent_idx:code_idx]
        assert "\n\n\n" not in between, (
            f"Expected a single blank line, found a double blank line in: "
            f"{between!r}"
        )


# ===========================================================================
# Group: round 2 item 3 -- CHECK 0 (relaxed template only, NOT M3-locked)
# must instruct the reviewer to judge specificity AS CLARIFIED by RECENT
# CONTEXT when present, so a weak verifier doesn't anchor on "too vague"
# for a terse current intent that continues an earlier-stated plan.
# ===========================================================================


class TestCheckZeroReferencesRecentContext:
    def test_check0_mentions_recent_context_clarification(self):
        template = _load_template("stage2_code_review_reasoning_summary.md")
        check0 = _extract_section(template, "CHECK 0: INTENT SPECIFICITY", ["CHECK 1:"])
        lowered = check0.lower()
        assert "recent context" in lowered
        assert "specificity" in lowered or "specific" in lowered

    def test_check0_addition_does_not_break_m3_lock(self):
        """Both templates have their OWN CHECK 0 (worded differently --
        "vague declarations" for the normal/strict path vs. "vague
        excerpts" for the relaxed path); CHECK 0 is explicitly NOT part of
        the M3 verbatim lock (only CHECK 1/CHECK 3/PARTIAL CONTEXT
        WARNING/CLASSIFICATION VALUES are). The new "recent context"
        clarification line must appear ONLY in the relaxed template's
        CHECK 0, never the normal template's -- the normal/strict path has
        no recent_context param at all."""
        normal = _load_template("stage2_code_review.md")
        normal_check0 = _extract_section(
            normal, "CHECK 0: INTENT SPECIFICITY", ["CHECK 1:"]
        )
        assert "recent context" not in normal_check0.lower()

        relaxed = _load_template("stage2_code_review_reasoning_summary.md")
        relaxed_check0 = _extract_section(
            relaxed, "CHECK 0: INTENT SPECIFICITY", ["CHECK 1:"]
        )
        assert "recent context" in relaxed_check0.lower()
        assert normal_check0.strip() != relaxed_check0.strip()


# ===========================================================================
# Group (round 3, CHANGE 1): RECENT CONTEXT terse-gating.
#
# Live evidence: three false blocks where the CURRENT intent was already
# detailed and named the file, yet a weak reviewer used an EARLIER turn's
# stale plan (surfaced via RECENT CONTEXT) to judge it instead:
#   - "Reverting A" judged against "create variant A"
#   - "Restoring B, then variant C" judged against "remove ... variant B"
#   - a detailed docstring-update intent judged against "delete
#     get_audit_logs"
# ===========================================================================


class TestShouldIncludeRecentContext:
    def test_constants_exist_with_expected_values(self):
        from pacemaker.constants import RECENT_CONTEXT_TERSE_MAX_CHARS

        assert RECENT_CONTEXT_TERSE_MAX_CHARS == 150

    def test_ssh_terse_line_gets_context(self):
        from pacemaker.intent_validator import _should_include_recent_context

        assert (
            _should_include_recent_context(TERSE_CURRENT_VISIBLE, NONCORE_FILE) is True
        )

    def test_f2_restoring_variant_c_gets_context_terse_restore_intent(self):
        """Issue #154 live-replay follow-up: the direction-word clause
        this test used to lock in (suppressing RECENT CONTEXT for any
        intent containing "restor...") was REMOVED. Live case F2 --
        "Restoring B, then variant C" -- is a TERSE restore intent that
        only makes sense with the EARLIER turns defining what "B" is;
        dropping RECENT CONTEXT for it made haiku block it at CHECK 0 as
        "too vague". The direction-misjudgment risk the clause guarded
        against is now handled by CHANGE 3's unified diff instead."""
        from pacemaker.intent_validator import _should_include_recent_context

        intent = "Restoring B, then variant C"
        assert _should_include_recent_context(intent, NONCORE_FILE) is True

    def test_f1_reverting_a_gets_context_terse_restore_intent(self):
        """Live case F1: "Reverting A" -- also terse, also needs the
        earlier turn's context to know what "A" refers to."""
        from pacemaker.intent_validator import _should_include_recent_context

        intent = "Reverting A"
        assert _should_include_recent_context(intent, NONCORE_FILE) is True

    def test_detailed_docstring_intent_naming_file_gets_none(self):
        from pacemaker.intent_validator import _should_include_recent_context

        intent = (
            "Update the docstring for check_group_access() in "
            "group_access_manager.py to document the new "
            "include_inherited parameter, explain how inherited group "
            "membership is resolved through the parent hierarchy, and "
            "note the performance characteristics of the recursive "
            "lookup so future maintainers understand the tradeoffs "
            "involved when enabling this flag on large org trees."
        )
        assert len(intent) >= 150
        assert (
            _should_include_recent_context(intent, "src/group_access_manager.py")
            is False
        )

    def test_long_intent_not_naming_file_gets_context(self):
        from pacemaker.intent_validator import _should_include_recent_context

        intent = (
            "Add a new helper function that validates the incoming "
            "request payload against the expected schema, returning a "
            "structured list of field-level errors instead of raising "
            "immediately, so the caller can decide how to surface "
            "multiple validation failures at once to the end user."
        )
        assert len(intent) >= 150
        assert (
            _should_include_recent_context(intent, "src/group_access_manager.py")
            is True
        )


# ===========================================================================
# Group M3: CHECK 1 / CHECK 3 / CLASSIFICATION VALUES sections must be
# VERBATIM identical between the normal and reasoning-summary templates.
# ===========================================================================


def _extract_section(text: str, header: str, next_headers: List[str]) -> str:
    start = text.index(header)
    end = len(text)
    for h in next_headers:
        try:
            idx = text.index(h, start + len(header))
        except ValueError:
            continue
        end = min(end, idx)
    section = text[start:end]
    # Trim trailing separator-only lines (a run of "═") and blank lines --
    # whichever next_header happens to be hit first (a section header vs.
    # the relaxed template's own {core_path_note} placeholder, which sits
    # BEFORE its own separator+header) shouldn't affect the comparison.
    lines = section.splitlines()
    while lines and not any(c.isalnum() for c in lines[-1]):
        # Drop trailing decorative-only lines: blank, a run of "═"
        # separator characters, or a dangling warning-emoji fragment left
        # behind when the next section's own header (e.g. "⚠️  NEW FILE
        # WARNING") is matched on a substring that doesn't include its
        # emoji prefix.
        lines.pop()
    return "\n".join(lines)


def _load_template(name: str) -> str:
    import pacemaker

    module_dir = os.path.dirname(pacemaker.__file__)
    path = os.path.join(module_dir, "prompts", "pre_tool_use", name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


class TestM3TemplateSectionsVerbatim:
    def test_check1_section_identical(self):
        normal = _load_template("stage2_code_review.md")
        relaxed = _load_template("stage2_code_review_reasoning_summary.md")
        normal_check1 = _extract_section(
            normal, "CHECK 1: CODE MATCHES INTENT", ["CHECK 2:"]
        )
        relaxed_check1 = _extract_section(
            relaxed, "CHECK 1: CODE MATCHES INTENT", ["CHECK 2:", "{core_path_note}"]
        )
        assert normal_check1.strip() == relaxed_check1.strip()

    def test_check3_section_identical(self):
        normal = _load_template("stage2_code_review.md")
        relaxed = _load_template("stage2_code_review_reasoning_summary.md")
        normal_check3 = _extract_section(
            normal, "CHECK 3: CLEAR BUG DETECTION", ["RESPONSE FORMAT"]
        )
        relaxed_check3 = _extract_section(
            relaxed, "CHECK 3: CLEAR BUG DETECTION", ["RESPONSE FORMAT"]
        )
        assert normal_check3.strip() == relaxed_check3.strip()

    def test_partial_context_warning_section_identical(self):
        """Re-review follow-up (item 3): the relaxed template lost the
        `exit "$CODE"` example (and the bullet-explanation dashes, and the
        warning-emoji header) when it was first forked -- lock this
        section against drift too, like CHECK 1/3/CLASSIFICATION VALUES."""
        normal = _load_template("stage2_code_review.md")
        relaxed = _load_template("stage2_code_review_reasoning_summary.md")
        normal_section = _extract_section(
            normal, "PARTIAL CONTEXT WARNING (Edit operations)", ["NEW FILE WARNING"]
        )
        relaxed_section = _extract_section(
            relaxed,
            "PARTIAL CONTEXT WARNING (Edit operations)",
            ["NEW FILE WARNING"],
        )
        assert normal_section.strip() == relaxed_section.strip()
        assert 'exit "$CODE"' in relaxed_section

    def test_classification_values_section_identical(self):
        normal = _load_template("stage2_code_review.md")
        relaxed = _load_template("stage2_code_review_reasoning_summary.md")
        normal_section = _extract_section(
            normal, "CLASSIFICATION VALUES", ["RESPONSE FORMAT - Choose"]
        )
        relaxed_section = _extract_section(
            relaxed, "CLASSIFICATION VALUES", ["RESPONSE FORMAT - Choose"]
        )
        assert normal_section.strip() == relaxed_section.strip()


# ===========================================================================
# Group: hook.py Write/Edit gate wiring, full pipeline
# ===========================================================================


def _write_edit_config(extra: Optional[dict] = None) -> dict:
    cfg = {
        "enabled": True,
        "intent_validation_enabled": True,
        "tdd_enabled": True,
        "danger_bash_enabled": False,
        "hook_model": "codex",
    }
    if extra:
        cfg.update(extra)
    return cfg


def _run_write_edit_hook(
    transcript_path: str,
    tool_name: str,
    file_path: str,
    tool_input: dict,
    config: Optional[dict] = None,
):
    from pacemaker.hook import run_pre_tool_hook

    stdin_payload = json.dumps(
        {
            "session_id": "test-151-write-edit",
            "transcript_path": transcript_path,
            "tool_name": tool_name,
            "tool_input": tool_input,
        }
    )
    with (
        patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
        patch(
            "pacemaker.hook.load_config",
            return_value=config if config is not None else _write_edit_config(),
        ),
    ):
        return run_pre_tool_hook()


class TestWriteEditGateOpusReasoningSummary:
    def test_opus_no_text_reasoning_summary_passes_through_real_hook(self, tmp_path):
        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0), model=OPUS_MODEL),
                _asst("req_A", _thinking_block(REASONING_SUMMARY, 1), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Edit",
                        {
                            "file_path": NONCORE_FILE,
                            "old_string": "pass",
                            "new_string": "pass  # guarded",
                        },
                        "toolu_A",
                        2,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            result = _run_write_edit_hook(
                transcript,
                "Edit",
                NONCORE_FILE,
                {
                    "file_path": NONCORE_FILE,
                    "old_string": "pass",
                    "new_string": "pass  # guarded",
                },
            )
        assert result == {"continue": True}, result
        mock_reviewer.assert_called_once()
        prompt = mock_reviewer.call_args.kwargs["prompt"]
        assert REASONING_SUMMARY in prompt

    def test_opus_visible_text_with_real_intent_uses_strict_path_h2(self, tmp_path):
        """H2 through the real hook: the anchor's own visible text has a
        real INTENT: marker on a CORE path with no TDD declaration -- the
        STRICT path must reject it as NO_TDD, not relax it."""
        transcript = _write_transcript(
            [
                _asst(
                    "req_A",
                    _text_block(
                        "INTENT: Modify example_module.py to add a validate() "
                        "function for input checks.",
                        0,
                    ),
                    model=OPUS_MODEL,
                ),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {
                            "file_path": CORE_FILE,
                            "content": "def validate():\n    pass\n",
                        },
                        "toolu_A",
                        1,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        result = _run_write_edit_hook(
            transcript,
            "Write",
            CORE_FILE,
            {"file_path": CORE_FILE, "content": "def validate():\n    pass\n"},
        )
        assert result.get("decision") == "block"
        assert "TDD Required" in result.get("reason", "")

    def test_sonnet_same_shape_blocked_exactly_as_today(self, tmp_path):
        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0), model=SONNET_MODEL),
                _asst(
                    "req_A", _thinking_block(REASONING_SUMMARY, 1), model=SONNET_MODEL
                ),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Edit",
                        {
                            "file_path": NONCORE_FILE,
                            "old_string": "pass",
                            "new_string": "pass  # guarded",
                        },
                        "toolu_A",
                        2,
                    ),
                    model=SONNET_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        result = _run_write_edit_hook(
            transcript,
            "Edit",
            NONCORE_FILE,
            {
                "file_path": NONCORE_FILE,
                "old_string": "pass",
                "new_string": "pass  # guarded",
            },
        )
        assert result.get("decision") == "block"
        assert "⛔ Your message had NO visible text" in result.get("reason", "")

    def test_opus_signature_only_thinking_blocked_with_no_visible_text_notice(
        self, tmp_path
    ):
        from pacemaker.hook import DEFAULT_DB_PATH

        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": "x = 1\n"},
                        "toolu_A",
                        1,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        result = _run_write_edit_hook(
            transcript,
            "Write",
            NONCORE_FILE,
            {"file_path": NONCORE_FILE, "content": "x = 1\n"},
        )
        assert result.get("decision") == "block"
        assert "⛔ Your message had NO visible text" in result.get("reason", "")

        # Re-review follow-up: this is the exact M1 fall-through scenario
        # (no visible text, no summary) for an exception-listed model --
        # the blockage row must carry BOTH no_visible_text=True AND
        # intent_source="none" (never "declaration", which would
        # contradict no_visible_text=True and pad any count of compliant
        # Opus turns), and the key must still be PRESENT (countable).
        conn = sqlite3.connect(DEFAULT_DB_PATH)
        try:
            rows = conn.execute(
                "SELECT details FROM blockage_events ORDER BY id DESC LIMIT 1"
            ).fetchall()
        finally:
            conn.close()
        details = json.loads(rows[0][0])
        assert details.get("no_visible_text") is True
        assert details.get("intent_source") == "none"

    def test_m1_fragmented_turn_write_edit_gate_passes(self, tmp_path):
        """M1's own repro at the FULL hook level: prior turn (req_P) has a
        real visible INTENT + an unrelated Read tool call; current turn
        (req_A) is signature-only thinking + Edit. Must PASS via the
        prose-only n-back rescue, exactly like Sonnet."""
        transcript = _write_transcript(
            [
                _asst(
                    "req_P",
                    _text_block(
                        f"INTENT: Edit {NONCORE_FILE} to add a helper function.",
                        0,
                    ),
                    model=OPUS_MODEL,
                ),
                _asst(
                    "req_P",
                    _tool_use_block("Read", {"file_path": NONCORE_FILE}, "toolu_P", 1),
                    model=OPUS_MODEL,
                ),
                _asst("req_A", _thinking_block("", 0), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Edit",
                        {
                            "file_path": NONCORE_FILE,
                            "old_string": "pass",
                            "new_string": "pass  # helper",
                        },
                        "toolu_A",
                        1,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            result = _run_write_edit_hook(
                transcript,
                "Edit",
                NONCORE_FILE,
                {
                    "file_path": NONCORE_FILE,
                    "old_string": "pass",
                    "new_string": "pass  # helper",
                },
            )
        assert result == {"continue": True}, (
            f"Today this blocks on Opus but passes on Sonnet -- M1 requires "
            f"parity; got: {result}"
        )
        mock_reviewer.assert_called_once()

    def test_stale_reissue_opus_relaxed_path_still_applies(self, tmp_path):
        """Hook-level stale-path coverage for the exception: a
        byte-identical re-issue (tool_result already recorded for the
        matched tool_use id) must still resolve via the relaxed path when
        the stale turn itself has no visible-text INTENT marker."""
        transcript = str(tmp_path / "t.jsonl")
        _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0), model=OPUS_MODEL),
                _asst("req_A", _thinking_block(REASONING_SUMMARY, 1), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": "x = 1\n"},
                        "toolu_A",
                        2,
                    ),
                    model=OPUS_MODEL,
                ),
                _tool_result_entry("ok", "toolu_A"),
            ],
            transcript,
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            result = _run_write_edit_hook(
                transcript,
                "Write",
                NONCORE_FILE,
                {"file_path": NONCORE_FILE, "content": "x = 1\n"},
            )
        assert result == {"continue": True}, result
        prompt = mock_reviewer.call_args.kwargs["prompt"]
        assert REASONING_SUMMARY in prompt

    def test_adversarial_intent_in_new_string_signature_only_still_blocks(
        self, tmp_path
    ):
        """Adversarial: an INTENT: string inside the Edit's own new_string
        (never real prose) must NOT satisfy the relaxed exception on a
        signature-only Opus turn."""
        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Edit",
                        {
                            "file_path": NONCORE_FILE,
                            "old_string": "pass",
                            "new_string": "# INTENT: fake declaration inside code\npass",
                        },
                        "toolu_A",
                        1,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        result = _run_write_edit_hook(
            transcript,
            "Edit",
            NONCORE_FILE,
            {
                "file_path": NONCORE_FILE,
                "old_string": "pass",
                "new_string": "# INTENT: fake declaration inside code\npass",
            },
        )
        assert result.get("decision") == "block"
        assert "⛔ Your message had NO visible text" in result.get("reason", "")

    def test_prior_turn_summary_now_deliberately_used_via_change2(self, tmp_path):
        """SUPERSEDED by issue #151 round 3 CHANGE 2 (live-replay
        follow-up): this test used to assert the OPPOSITE -- that a prior
        turn's reasoning summary must NEVER leak into a signature-only
        anchor's relaxed intent. CHANGE 2 deliberately REVERSES that: 84
        of 1316 historical Opus edits, and 4 live blocks in one day, had
        exactly this shape (anchor has neither visible text nor a
        summary, but the IMMEDIATELY PRECEDING turn does) and were false
        blocks. The prior turn's text is now USED as the intent
        (intent_source="prior_reasoning_summary"), with the reviewer
        explicitly told (via the "PREVIOUS message" notice) that it came
        from an earlier turn -- Stage 2's own CHECK 0/CHECK 1 judgment is
        the safety net against a genuinely unrelated/vague prior summary,
        not a code-level block."""
        transcript = _write_transcript(
            [
                _asst(
                    "req_P",
                    _thinking_block("An unrelated prior summary about foo.py", 0),
                    model=OPUS_MODEL,
                ),
                _asst(
                    "req_P",
                    _tool_use_block("Read", {"file_path": "foo.py"}, "toolu_P", 1),
                    model=OPUS_MODEL,
                ),
                _asst("req_A", _thinking_block("", 0), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": "x = 1\n"},
                        "toolu_A",
                        1,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            result = _run_write_edit_hook(
                transcript,
                "Write",
                NONCORE_FILE,
                {"file_path": NONCORE_FILE, "content": "x = 1\n"},
            )
        assert result == {"continue": True}, result
        prompt = mock_reviewer.call_args.kwargs["prompt"]
        assert "An unrelated prior summary about foo.py" in prompt
        assert "PREVIOUS" in prompt.upper()

    def test_malformed_config_never_crashes_sonnet_or_opus(self, tmp_path):
        """H1 through the real hook: a malformed config value must never
        crash (fail_closed_error) -- Sonnet gets its normal block, Opus
        with signature-only thinking ALSO gets the normal block (exception
        disabled, since the malformed value normalizes to [])."""
        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": "x = 1\n"},
                        "toolu_A",
                        1,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        cfg = _write_edit_config({"reasoning_summary_intent_models": None})
        result = _run_write_edit_hook(
            transcript,
            "Write",
            NONCORE_FILE,
            {"file_path": NONCORE_FILE, "content": "x = 1\n"},
            config=cfg,
        )
        assert result.get("decision") == "block"
        assert "System Error" not in result.get("reason", "")
        assert "⛔ Your message had NO visible text" in result.get("reason", "")

    def test_config_without_opus_in_list_restores_strict_behavior(self, tmp_path):
        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0), model=OPUS_MODEL),
                _asst("req_A", _thinking_block(REASONING_SUMMARY, 1), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": "x = 1\n"},
                        "toolu_A",
                        2,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        cfg = _write_edit_config({"reasoning_summary_intent_models": []})
        result = _run_write_edit_hook(
            transcript,
            "Write",
            NONCORE_FILE,
            {"file_path": NONCORE_FILE, "content": "x = 1\n"},
            config=cfg,
        )
        assert result.get("decision") == "block"
        assert "⛔ Your message had NO visible text" in result.get("reason", "")


class TestWriteEditGateRecentContextLiveRepro:
    """Live-test follow-up: reproduces the real haiku-vs-Opus-5.5 replay
    that motivated this section -- a terse current-turn visible text with
    no thinking, preceded by a turn whose reasoning summary held the full
    plan. The relaxed Stage 2 prompt must now surface that PRIOR turn as
    context, sourced structurally (prose-only, #140) so rendered tool
    params/INTENT:-in-content can never leak in."""

    def test_live_repro_prior_summary_appears_as_context_not_rendered(self, tmp_path):
        transcript = _write_transcript(
            [
                # Prior turn: full plan lives ONLY in the reasoning summary.
                _asst(
                    "req_PLAN",
                    _thinking_block(PRIOR_PLAN_SUMMARY, 0),
                    model=OPUS_MODEL,
                ),
                _asst(
                    "req_PLAN",
                    _tool_use_block(
                        "Edit",
                        {
                            "file_path": "tests/unit/server/audit/test_account_entry_points.py",
                            "old_string": "assert row.caller_id",
                            "new_string": "assert row.id == PLACEHOLDER",
                        },
                        "toolu_PLAN",
                        1,
                    ),
                    model=OPUS_MODEL,
                ),
                _tool_result_entry("ok", "toolu_PLAN"),
                # Current (anchor) turn: terse visible text, no thinking.
                _asst(
                    "req_A",
                    _text_block(TERSE_CURRENT_VISIBLE, 0),
                    model=OPUS_MODEL,
                ),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Edit",
                        {
                            "file_path": "tests/unit/server/audit/test_account_entry_points.py",
                            "old_string": "assert row.fingerprint is None",
                            "new_string": "assert row.fingerprint == expected_fp",
                        },
                        "toolu_A",
                        1,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            result = _run_write_edit_hook(
                transcript,
                "Edit",
                "tests/unit/server/audit/test_account_entry_points.py",
                {
                    "file_path": "tests/unit/server/audit/test_account_entry_points.py",
                    "old_string": "assert row.fingerprint is None",
                    "new_string": "assert row.fingerprint == expected_fp",
                },
            )
        assert result == {"continue": True}, result
        mock_reviewer.assert_called_once()
        prompt = mock_reviewer.call_args.kwargs["prompt"]

        # The prior turn's plan is present, labelled as context only.
        assert PRIOR_PLAN_SUMMARY in prompt
        assert "context only" in prompt.lower()
        assert "not the intent" in prompt.lower()
        # The current turn's OWN terse text remains the intent.
        assert TERSE_CURRENT_VISIBLE in prompt

        # #140 structural guarantee: no rendered tool parameters from the
        # PRIOR turn ever leak into the prompt via the RECENT CONTEXT
        # section (unaffected by issue #153 -- that guarantee is scoped to
        # the context section only, not the CURRENT edit's own view).
        assert "[TOOL:" not in prompt
        assert "assert row.id == PLACEHOLDER" not in prompt
        # Review low-priority fix: the PRIOR turn's own old_string must
        # ALSO never leak in -- it belongs to a DIFFERENT logical turn
        # (req_PLAN, not the current anchor req_A), so it is neither
        # RECENT CONTEXT (prose-only, #140) nor a sibling edit (siblings
        # are scoped to the anchor's OWN turn, #153) nor on-disk
        # surrounding context (this synthetic path doesn't exist on disk).
        assert "assert row.caller_id" not in prompt
        # Issue #153: the CURRENT edit's OWN old_string is now DELIBERATELY
        # shown (an OLD -> NEW diff-style view for the PROPOSED CODE
        # section), so Stage 2 can verify the direction of a change -- this
        # is the opposite of the pre-#153 assertion this test used to make.
        assert "assert row.fingerprint is None" in prompt
        assert "assert row.fingerprint == expected_fp" in prompt

    def test_prior_turn_intent_marker_inside_write_content_never_appears(
        self, tmp_path
    ):
        """A prior turn whose ONLY "INTENT:" text lives inside a Write's
        rendered `content` (never real prose) must never surface in the
        RECENT CONTEXT section -- the prose-only contract (#140) applies
        here exactly as it does to the anchor's own text."""
        transcript = _write_transcript(
            [
                _asst(
                    "req_PLAN",
                    _tool_use_block(
                        "Write",
                        {
                            "file_path": NONCORE_FILE,
                            "content": "# INTENT: fake declaration inside file content\nx = 1\n",
                        },
                        "toolu_PLAN",
                        0,
                    ),
                    model=OPUS_MODEL,
                ),
                _tool_result_entry("ok", "toolu_PLAN"),
                _asst("req_A", _thinking_block(REASONING_SUMMARY, 0), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": "x = 2\n"},
                        "toolu_A",
                        1,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            result = _run_write_edit_hook(
                transcript,
                "Write",
                NONCORE_FILE,
                {"file_path": NONCORE_FILE, "content": "x = 2\n"},
            )
        assert result == {"continue": True}, result
        prompt = mock_reviewer.call_args.kwargs["prompt"]
        assert "fake declaration inside file content" not in prompt
        assert "INTENT: fake declaration" not in prompt

    def test_cap_enforcement_through_real_hook(self, tmp_path):
        """End-to-end cap enforcement: several oversized prior turns must
        not blow the prompt up unboundedly -- the RECENT CONTEXT section
        stays capped even through the real hook wiring."""
        big = "z" * 900
        transcript = _write_transcript(
            [
                _asst("req_1", _thinking_block(f"turn1 {big}", 0), model=OPUS_MODEL),
                _asst(
                    "req_1",
                    _tool_use_block("Read", {"file_path": "a.py"}, "toolu_1", 1),
                    model=OPUS_MODEL,
                ),
                _asst("req_2", _thinking_block(f"turn2 {big}", 0), model=OPUS_MODEL),
                _asst(
                    "req_2",
                    _tool_use_block("Read", {"file_path": "b.py"}, "toolu_2", 1),
                    model=OPUS_MODEL,
                ),
                _asst("req_3", _thinking_block(f"turn3 {big}", 0), model=OPUS_MODEL),
                _asst(
                    "req_3",
                    _tool_use_block("Read", {"file_path": "c.py"}, "toolu_3", 1),
                    model=OPUS_MODEL,
                ),
                # The CURRENT turn needs its OWN non-empty visible text or
                # summary to take the RELAXED path directly (an empty
                # current turn would fall through to the M1 strict path
                # instead, per resolve_reasoning_summary_intent_source).
                _asst("req_A", _text_block("final turn note", 0), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": "x = 1\n"},
                        "toolu_A",
                        1,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            _run_write_edit_hook(
                transcript,
                "Write",
                NONCORE_FILE,
                {"file_path": NONCORE_FILE, "content": "x = 1\n"},
            )
        prompt = mock_reviewer.call_args.kwargs["prompt"]
        # turn3 (most recent of the three prior turns) must be kept;
        # turn1 (oldest) must be dropped once the cap is exceeded.
        assert "turn3" in prompt
        assert "turn1" not in prompt

    def test_sonnet_model_never_gets_recent_context_section(self, tmp_path):
        """Non-exception byte-identity: a Sonnet turn must never render a
        RECENT CONTEXT section, even with real prior-turn history that
        WOULD qualify for an exception model."""
        transcript = _write_transcript(
            [
                _asst(
                    "req_PLAN",
                    _thinking_block(PRIOR_PLAN_SUMMARY, 0),
                    model=SONNET_MODEL,
                ),
                _asst(
                    "req_PLAN",
                    _tool_use_block("Read", {"file_path": "a.py"}, "toolu_PLAN", 1),
                    model=SONNET_MODEL,
                ),
                _asst(
                    "req_A",
                    _text_block(
                        "INTENT: Modify mod.py to add a helper.\n"
                        "Test coverage: tests/test_mod.py - test_helper()",
                        0,
                    ),
                    model=SONNET_MODEL,
                ),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": "x = 1\n"},
                        "toolu_A",
                        1,
                    ),
                    model=SONNET_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            result = _run_write_edit_hook(
                transcript,
                "Write",
                NONCORE_FILE,
                {"file_path": NONCORE_FILE, "content": "x = 1\n"},
            )
        assert result == {"continue": True}, result
        prompt = mock_reviewer.call_args.kwargs["prompt"]
        assert "context only" not in prompt.lower()
        assert PRIOR_PLAN_SUMMARY not in prompt
        # This is the NORMAL stage2_code_review.md prompt -- confirms the
        # strict/normal path (with its own unrelated, pre-existing
        # "RECENT CONTEXT (last 2 messages):" header) was used, not the
        # relaxed template.
        assert "RECENT CONTEXT (last 2 messages)" in prompt

    def test_stale_path_context_excludes_anchor_includes_prior_turn(self, tmp_path):
        """Round 2 item 2: on the STALE path (a byte-identical re-issue
        whose tool_result already exists), RECENT CONTEXT must include the
        turn strictly BEFORE the anchor, and must NEVER duplicate the
        anchor's OWN reasoning summary into its own "recent context" --
        proving the anchor-relative redesign fixes the previous design's
        positional `prose[:-1]` bug (which sliced relative to the whole
        transcript, not the anchor, so a flushed stale re-issue's own
        thinking could show up as "context" for itself)."""
        anchor_own_summary = (
            "This is the anchor's OWN private reasoning about the "
            "stale re-issue; it must never duplicate into its own "
            "RECENT CONTEXT section."
        )
        transcript = _write_transcript(
            [
                _asst(
                    "req_PLAN", _thinking_block(PRIOR_PLAN_SUMMARY, 0), model=OPUS_MODEL
                ),
                _asst(
                    "req_PLAN",
                    _tool_use_block("Read", {"file_path": "a.py"}, "toolu_PLAN", 1),
                    model=OPUS_MODEL,
                ),
                _asst(
                    "req_A", _thinking_block(anchor_own_summary, 0), model=OPUS_MODEL
                ),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": "x = 1\n"},
                        "toolu_A",
                        1,
                    ),
                    model=OPUS_MODEL,
                ),
                _tool_result_entry("ok", "toolu_A"),
            ],
            str(tmp_path / "t.jsonl"),
        )

        # Fake monotonic clock: avoids paying the real 3.0s
        # _WRITE_EDIT_STALE_GRACE_SECONDS wait (mirrors
        # tests/test_issue_139_write_edit_stale_accept.py's own pattern).
        fake_now = [0.0]

        def fake_monotonic():
            return fake_now[0]

        def fake_sleep(seconds):
            fake_now[0] += seconds

        with (
            patch("pacemaker.transcript_reader.time.sleep", fake_sleep),
            patch("pacemaker.transcript_reader.time.monotonic", fake_monotonic),
            patch(
                "pacemaker.inference.resolve_and_call_with_reviewer",
                return_value=("APPROVED", "test-reviewer"),
            ) as mock_reviewer,
        ):
            result = _run_write_edit_hook(
                transcript,
                "Write",
                NONCORE_FILE,
                {"file_path": NONCORE_FILE, "content": "x = 1\n"},
            )
        assert result == {"continue": True}, result
        mock_reviewer.assert_called_once()
        prompt = mock_reviewer.call_args.kwargs["prompt"]

        # Turn before the anchor DOES appear, as context.
        assert PRIOR_PLAN_SUMMARY in prompt

        # The anchor's own summary appears exactly ONCE -- as the INTENT
        # itself, never a second time duplicated into RECENT CONTEXT.
        assert prompt.count(anchor_own_summary) == 1


# ===========================================================================
# Group (round 3, CHANGE 1 + CHANGE 2): hook.py wiring, full pipeline.
# ===========================================================================


class TestChange1RecentContextTerseGatingHookWiring:
    def test_terse_restore_intent_still_gets_recent_context(self, tmp_path):
        """Issue #154 live-replay follow-up: 'Restoring B, then variant
        C' (live case F2) is a TERSE restore intent that only makes
        sense with the EARLIER turn defining what "B" is. An earlier
        revision of this feature suppressed RECENT CONTEXT for any
        intent containing a direction word like "restor..." -- that
        clause was REMOVED after live replay showed it made haiku block
        exactly this shape at CHECK 0 as "too vague". Direction
        misjudgment is now handled by CHANGE 3's unified diff instead."""
        transcript = _write_transcript(
            [
                _asst(
                    "req_PLAN", _thinking_block(PRIOR_PLAN_SUMMARY, 0), model=OPUS_MODEL
                ),
                _asst(
                    "req_PLAN",
                    _tool_use_block("Read", {"file_path": "a.py"}, "toolu_PLAN", 1),
                    model=OPUS_MODEL,
                ),
                _asst(
                    "req_A",
                    _text_block("Restoring B, then variant C", 0),
                    model=OPUS_MODEL,
                ),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": "x = 1\n"},
                        "toolu_A",
                        1,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            result = _run_write_edit_hook(
                transcript,
                "Write",
                NONCORE_FILE,
                {"file_path": NONCORE_FILE, "content": "x = 1\n"},
            )
        assert result == {"continue": True}, result
        prompt = mock_reviewer.call_args.kwargs["prompt"]
        assert "Restoring B, then variant C" in prompt
        assert PRIOR_PLAN_SUMMARY in prompt
        assert "RECENT CONTEXT (earlier turns" in prompt

    def test_detailed_file_naming_current_intent_suppresses_recent_context(
        self, tmp_path
    ):
        """Live evidence shape: a detailed, file-naming current intent
        must never be overridden/clarified by an earlier turn's stale
        plan -- the false-block class this whole round exists to fix."""
        detailed_intent = (
            "Add input validation to the process_payload() function in "
            "mod.py so it rejects malformed JSON payloads early with a "
            "clear error message, instead of letting a downstream "
            "KeyError propagate up to the caller when a required field "
            "is missing from the request body."
        )
        assert len(detailed_intent) >= 150
        transcript = _write_transcript(
            [
                _asst(
                    "req_PLAN", _thinking_block(PRIOR_PLAN_SUMMARY, 0), model=OPUS_MODEL
                ),
                _asst(
                    "req_PLAN",
                    _tool_use_block("Read", {"file_path": "a.py"}, "toolu_PLAN", 1),
                    model=OPUS_MODEL,
                ),
                _asst("req_A", _text_block(detailed_intent, 0), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": "x = 1\n"},
                        "toolu_A",
                        1,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            result = _run_write_edit_hook(
                transcript,
                "Write",
                NONCORE_FILE,
                {"file_path": NONCORE_FILE, "content": "x = 1\n"},
            )
        assert result == {"continue": True}, result
        prompt = mock_reviewer.call_args.kwargs["prompt"]
        assert detailed_intent in prompt
        assert PRIOR_PLAN_SUMMARY not in prompt

    def test_recent_context_included_telemetry_true_when_shown(self, tmp_path):
        from pacemaker.hook import DEFAULT_DB_PATH

        transcript = _write_transcript(
            [
                _asst(
                    "req_PLAN", _thinking_block(PRIOR_PLAN_SUMMARY, 0), model=OPUS_MODEL
                ),
                _asst(
                    "req_PLAN",
                    _tool_use_block("Read", {"file_path": "a.py"}, "toolu_PLAN", 1),
                    model=OPUS_MODEL,
                ),
                _asst("req_A", _text_block(TERSE_CURRENT_VISIBLE, 0), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": "x = 1\n"},
                        "toolu_A",
                        1,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("BLOCKED: nope", "test-reviewer"),
        ):
            result = _run_write_edit_hook(
                transcript,
                "Write",
                NONCORE_FILE,
                {"file_path": NONCORE_FILE, "content": "x = 1\n"},
            )
        assert result.get("decision") == "block"
        conn = sqlite3.connect(DEFAULT_DB_PATH)
        try:
            rows = conn.execute(
                "SELECT details FROM blockage_events ORDER BY id DESC LIMIT 1"
            ).fetchall()
        finally:
            conn.close()
        details = json.loads(rows[0][0])
        assert details.get("recent_context_included") is True

    def test_recent_context_included_telemetry_false_when_suppressed(self, tmp_path):
        """Suppression now happens ONLY via the terse/file-naming rule --
        the direction-word clause was removed (issue #154 follow-up), so
        this uses a DETAILED, file-naming intent (the remaining
        suppression case) rather than a terse restore intent."""
        from pacemaker.hook import DEFAULT_DB_PATH

        detailed_intent = (
            "Add input validation to the process_payload() function in "
            "mod.py so it rejects malformed JSON payloads early with a "
            "clear error message, instead of letting a downstream "
            "KeyError propagate up to the caller when a required field "
            "is missing from the request body."
        )
        assert len(detailed_intent) >= 150
        transcript = _write_transcript(
            [
                _asst(
                    "req_PLAN", _thinking_block(PRIOR_PLAN_SUMMARY, 0), model=OPUS_MODEL
                ),
                _asst(
                    "req_PLAN",
                    _tool_use_block("Read", {"file_path": "a.py"}, "toolu_PLAN", 1),
                    model=OPUS_MODEL,
                ),
                _asst("req_A", _text_block(detailed_intent, 0), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": "x = 1\n"},
                        "toolu_A",
                        1,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("BLOCKED: nope", "test-reviewer"),
        ):
            result = _run_write_edit_hook(
                transcript,
                "Write",
                NONCORE_FILE,
                {"file_path": NONCORE_FILE, "content": "x = 1\n"},
            )
        assert result.get("decision") == "block"
        conn = sqlite3.connect(DEFAULT_DB_PATH)
        try:
            rows = conn.execute(
                "SELECT details FROM blockage_events ORDER BY id DESC LIMIT 1"
            ).fetchall()
        finally:
            conn.close()
        details = json.loads(rows[0][0])
        assert details.get("recent_context_included") is False


class TestChange2PriorReasoningSummaryHookWiring:
    def test_empty_anchor_with_prior_summary_approved_via_new_source(self, tmp_path):
        prior_summary = (
            "I'll add a new test in test_elevate_session.py verifying "
            "that calls missing a session key return missing_session_key."
        )
        transcript = _write_transcript(
            [
                _asst("req_PLAN", _thinking_block(prior_summary, 0), model=OPUS_MODEL),
                _asst(
                    "req_PLAN",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": "x = 0\n"},
                        "toolu_PLAN",
                        1,
                    ),
                    model=OPUS_MODEL,
                ),
                _tool_result_entry("ok", "toolu_PLAN"),
                # Anchor turn: NO visible text, NO thinking -- just the tool
                # call itself.
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": "x = 1\n"},
                        "toolu_A",
                        0,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            result = _run_write_edit_hook(
                transcript,
                "Write",
                NONCORE_FILE,
                {"file_path": NONCORE_FILE, "content": "x = 1\n"},
            )
        assert result == {"continue": True}, result
        mock_reviewer.assert_called_once()
        prompt = mock_reviewer.call_args.kwargs["prompt"]
        assert prior_summary in prompt
        assert "PREVIOUS" in prompt.upper()

        from pacemaker.hook import DEFAULT_DB_PATH

        conn = sqlite3.connect(DEFAULT_DB_PATH)
        try:
            rows = conn.execute(
                "SELECT feedback_text FROM governance_events "
                "WHERE event_type = 'RS' ORDER BY id DESC LIMIT 1"
            ).fetchall()
        finally:
            conn.close()
        assert rows, "expected an RS governance event to be recorded"
        assert "prior_reasoning_summary" in rows[0][0]
        assert "previous message's reasoning summary" in rows[0][0]

    def test_empty_anchor_with_empty_prior_turn_blocked_as_before(self, tmp_path):
        transcript = _write_transcript(
            [
                _asst(
                    "req_PLAN",
                    _tool_use_block("Read", {"file_path": "a.py"}, "toolu_PLAN", 0),
                    model=OPUS_MODEL,
                ),
                _tool_result_entry("ok", "toolu_PLAN"),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": "x = 1\n"},
                        "toolu_A",
                        0,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        result = _run_write_edit_hook(
            transcript,
            "Write",
            NONCORE_FILE,
            {"file_path": NONCORE_FILE, "content": "x = 1\n"},
        )
        assert result.get("decision") == "block"
        assert "NO visible text" in result.get("reason", "")

    def test_stale_path_empty_anchor_uses_prior_reasoning_summary(self, tmp_path):
        prior_summary = "The prior turn's own full plan for this edit."
        tool_input = {"file_path": NONCORE_FILE, "content": "x = 1\n"}
        transcript_path = str(tmp_path / "t.jsonl")
        transcript = _write_transcript(
            [
                _asst("req_PLAN", _thinking_block(prior_summary, 0), model=OPUS_MODEL),
                _asst(
                    "req_PLAN",
                    _tool_use_block("Read", {"file_path": "a.py"}, "toolu_PLAN", 1),
                    model=OPUS_MODEL,
                ),
                _asst(
                    "req_A",
                    _tool_use_block("Write", tool_input, "toolu_A", 0),
                    model=OPUS_MODEL,
                ),
                # The anchor's own tool_use ALREADY has a tool_result --
                # this is now STALE, forcing the stale-acceptance path.
                _tool_result_entry("ok", "toolu_A"),
            ],
            transcript_path,
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            result = _run_write_edit_hook(transcript, "Write", NONCORE_FILE, tool_input)
        assert result == {"continue": True}, result
        prompt = mock_reviewer.call_args.kwargs["prompt"]
        assert prior_summary in prompt


class TestWriteEditGateTelemetry:
    def test_blockage_details_carry_intent_source_reasoning_summary(self, tmp_path):
        from pacemaker.hook import DEFAULT_DB_PATH

        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0), model=OPUS_MODEL),
                _asst("req_A", _thinking_block(REASONING_SUMMARY, 1), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": "x = 1\n"},
                        "toolu_A",
                        2,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("BLOCKED: scope creep", "test-reviewer"),
        ):
            result = _run_write_edit_hook(
                transcript,
                "Write",
                NONCORE_FILE,
                {"file_path": NONCORE_FILE, "content": "x = 1\n"},
            )
        assert result.get("decision") == "block"

        conn = sqlite3.connect(DEFAULT_DB_PATH)
        try:
            rows = conn.execute(
                "SELECT details FROM blockage_events ORDER BY id DESC LIMIT 1"
            ).fetchall()
        finally:
            conn.close()
        details = json.loads(rows[0][0])
        assert details.get("intent_source") == "reasoning_summary"

    def test_l1_non_exception_model_blockage_details_never_has_intent_source_key(
        self, tmp_path
    ):
        from pacemaker.hook import DEFAULT_DB_PATH

        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0), model=SONNET_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": "x = 1\n"},
                        "toolu_A",
                        1,
                    ),
                    model=SONNET_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        result = _run_write_edit_hook(
            transcript,
            "Write",
            NONCORE_FILE,
            {"file_path": NONCORE_FILE, "content": "x = 1\n"},
        )
        assert result.get("decision") == "block"

        conn = sqlite3.connect(DEFAULT_DB_PATH)
        try:
            rows = conn.execute(
                "SELECT details FROM blockage_events ORDER BY id DESC LIMIT 1"
            ).fetchall()
        finally:
            conn.close()
        details = json.loads(rows[0][0])
        assert "intent_source" not in details

    def test_approval_via_reasoning_summary_records_activity_and_governance_event(
        self, tmp_path
    ):
        from pacemaker.hook import DEFAULT_DB_PATH

        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0), model=OPUS_MODEL),
                _asst("req_A", _thinking_block(REASONING_SUMMARY, 1), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": "x = 1\n"},
                        "toolu_A",
                        2,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ):
            result = _run_write_edit_hook(
                transcript,
                "Write",
                NONCORE_FILE,
                {"file_path": NONCORE_FILE, "content": "x = 1\n"},
            )
        assert result == {"continue": True}, result

        conn = sqlite3.connect(DEFAULT_DB_PATH)
        try:
            activity_rows = conn.execute(
                "SELECT status FROM activity_events WHERE event_code = 'RS'"
            ).fetchall()
            gov_rows = conn.execute(
                "SELECT feedback_text FROM governance_events WHERE event_type = 'RS'"
            ).fetchall()
        finally:
            conn.close()
        assert activity_rows, "expected an RS activity event for the approval"
        assert gov_rows, "expected an RS governance event for the approval"
        assert "reasoning_summary" in gov_rows[0][0]

    def test_declaration_source_approval_never_records_rs_telemetry(self, tmp_path):
        """H2: an Opus turn that ALREADY declared a real INTENT: (strict
        path, intent_source="declaration") must NOT be recorded as an RS
        (relaxed) approval -- it complied with the normal contract."""
        from pacemaker.hook import DEFAULT_DB_PATH

        transcript = _write_transcript(
            [
                _asst(
                    "req_A",
                    _text_block("INTENT: Modify mod.py to add a helper function.", 0),
                    model=OPUS_MODEL,
                ),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": "x = 1\n"},
                        "toolu_A",
                        1,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ):
            result = _run_write_edit_hook(
                transcript,
                "Write",
                NONCORE_FILE,
                {"file_path": NONCORE_FILE, "content": "x = 1\n"},
            )
        assert result == {"continue": True}, result

        conn = sqlite3.connect(DEFAULT_DB_PATH)
        try:
            activity_rows = conn.execute(
                "SELECT status FROM activity_events WHERE event_code = 'RS'"
            ).fetchall()
        finally:
            conn.close()
        assert not activity_rows, "declaration-path approval must NOT emit RS"

    def test_reasoning_summary_source_feedback_text_wording(self, tmp_path):
        """Live-test follow-up: the RS governance event's feedback_text
        must name the ACTUAL source, not a generic "reasoning-summary
        intent" phrase for every source -- the claude-usage monitor showed
        this exact wording for a `visible_text` approval, which was
        misleading (no reasoning summary was involved at all)."""
        from pacemaker.hook import DEFAULT_DB_PATH

        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0), model=OPUS_MODEL),
                _asst("req_A", _thinking_block(REASONING_SUMMARY, 1), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": "x = 1\n"},
                        "toolu_A",
                        2,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ):
            result = _run_write_edit_hook(
                transcript,
                "Write",
                NONCORE_FILE,
                {"file_path": NONCORE_FILE, "content": "x = 1\n"},
            )
        assert result == {"continue": True}, result

        conn = sqlite3.connect(DEFAULT_DB_PATH)
        try:
            gov_rows = conn.execute(
                "SELECT feedback_text FROM governance_events WHERE event_type = 'RS'"
            ).fetchall()
        finally:
            conn.close()
        assert gov_rows, "expected an RS governance event for the approval"
        feedback_text = gov_rows[0][0]
        assert feedback_text == (
            "[test-reviewer] Approved via relaxed intent: reasoning summary "
            "(intent_source=reasoning_summary)"
        ), feedback_text
        # #101 B2: RS governance feedback_text stays untagged.
        assert "[pace-maker" not in feedback_text

    def test_visible_text_source_feedback_text_wording(self, tmp_path):
        """Same fix, the `visible_text` source -- this is the exact case
        the live test flagged: a bare visible-text turn with no thinking
        at all must never be described as "reasoning-summary intent"."""
        from pacemaker.hook import DEFAULT_DB_PATH

        transcript = _write_transcript(
            [
                _asst("req_A", _text_block(TERSE_CURRENT_VISIBLE, 0), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {"file_path": NONCORE_FILE, "content": "x = 1\n"},
                        "toolu_A",
                        1,
                    ),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ):
            result = _run_write_edit_hook(
                transcript,
                "Write",
                NONCORE_FILE,
                {"file_path": NONCORE_FILE, "content": "x = 1\n"},
            )
        assert result == {"continue": True}, result

        conn = sqlite3.connect(DEFAULT_DB_PATH)
        try:
            gov_rows = conn.execute(
                "SELECT feedback_text FROM governance_events WHERE event_type = 'RS'"
            ).fetchall()
        finally:
            conn.close()
        assert gov_rows, "expected an RS governance event for the approval"
        feedback_text = gov_rows[0][0]
        assert feedback_text == (
            "[test-reviewer] Approved via relaxed intent: visible text "
            "without INTENT: marker (intent_source=visible_text)"
        ), feedback_text
        assert "reasoning summary" not in feedback_text.lower(), (
            f"a visible_text-source approval must never be described as "
            f"reasoning-summary; got: {feedback_text!r}"
        )


# ===========================================================================
# Group M2/M4: danger-bash gate wiring -- REAL rules (no load_rules/
# match_command mocking), M2 prompt reframing.
# ===========================================================================


def _danger_bash_config(extra: Optional[dict] = None) -> dict:
    cfg = {
        "enabled": True,
        "intent_validation_enabled": True,
        "danger_bash_enabled": True,
        "hook_model": "auto",
    }
    if extra:
        cfg.update(extra)
    return cfg


DANGER_COMMAND = "rm -rf /tmp/pacemaker_issue151_scratch/doomed"


def _run_danger_bash_hook(
    transcript_path: str,
    command: str = DANGER_COMMAND,
    tmp_path=None,
    config: Optional[dict] = None,
):
    from pacemaker.hook import run_pre_tool_hook

    stdin_payload = json.dumps(
        {
            "session_id": "test-151-bash",
            "transcript_path": transcript_path,
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
    )
    # M4: use REAL danger_bash_rules (bundled defaults) against a fresh,
    # nonexistent per-test rules-config path -- never the developer's real
    # ~/.claude-pace-maker/danger_bash_rules.yaml, and never a mock of
    # load_rules/match_command.
    rules_path = str(
        (tmp_path or __import__("pathlib").Path(".")) / "no_such_rules.yaml"
    )
    with (
        patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
        patch(
            "pacemaker.hook.load_config",
            return_value=config if config is not None else _danger_bash_config(),
        ),
        patch("pacemaker.hook.DEFAULT_DANGER_RULES_PATH", rules_path),
    ):
        return run_pre_tool_hook()


class TestDangerBashGateOpusReasoningSummary:
    def test_opus_no_text_reasoning_summary_reaches_phase2(self, tmp_path):
        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0), model=OPUS_MODEL),
                _asst("req_A", _thinking_block(REASONING_SUMMARY, 1), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": DANGER_COMMAND}, "toolu_A", 2),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            result = _run_danger_bash_hook(transcript, tmp_path=tmp_path)
        assert result == {"continue": True}, result
        mock_reviewer.assert_called_once()
        prompt = mock_reviewer.call_args.kwargs["prompt"]
        assert REASONING_SUMMARY in prompt

    def test_opus_signature_only_thinking_blocked_phase1(self, tmp_path):
        from pacemaker.hook import DEFAULT_DB_PATH

        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": DANGER_COMMAND}, "toolu_A", 1),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        result = _run_danger_bash_hook(transcript, tmp_path=tmp_path)
        assert result.get("decision") == "block"
        assert "⛔ Your message had NO visible text" in result.get("reason", "")

        # Re-review follow-up: same M1 fall-through invariant as the
        # Write/Edit gate -- "none", never "declaration", and the key
        # must still be present.
        conn = sqlite3.connect(DEFAULT_DB_PATH)
        try:
            rows = conn.execute(
                "SELECT details FROM blockage_events ORDER BY id DESC LIMIT 1"
            ).fetchall()
        finally:
            conn.close()
        details = json.loads(rows[0][0])
        assert details.get("no_visible_text") is True
        assert details.get("intent_source") == "none"

    def test_sonnet_same_shape_blocked_phase1_exactly_as_today(self, tmp_path):
        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0), model=SONNET_MODEL),
                _asst(
                    "req_A", _thinking_block(REASONING_SUMMARY, 1), model=SONNET_MODEL
                ),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": DANGER_COMMAND}, "toolu_A", 2),
                    model=SONNET_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        result = _run_danger_bash_hook(transcript, tmp_path=tmp_path)
        assert result.get("decision") == "block"
        assert "⛔ Your message had NO visible text" in result.get("reason", "")

    def test_h2_opus_visible_declaration_uses_strict_default_prompt(self, tmp_path):
        """H2 through danger-bash: a real INTENT: in the anchor's own
        visible text must use the STRICT (byte-identical) Phase 2 prompt,
        not the relaxed framing."""
        real_intent = f"INTENT: Run {DANGER_COMMAND} to clean up scratch files."
        transcript = _write_transcript(
            [
                _asst("req_A", _text_block(real_intent, 0), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": DANGER_COMMAND}, "toolu_A", 1),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            result = _run_danger_bash_hook(transcript, tmp_path=tmp_path)
        assert result == {"continue": True}, result
        prompt = mock_reviewer.call_args.kwargs["prompt"]
        assert "ASSISTANT MESSAGE (contains intent declaration)" in prompt
        assert "Does the INTENT: declaration SPECIFICALLY describe" in prompt

    def test_m2_reasoning_summary_phase2_prompt_consistent_framing(self, tmp_path):
        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0), model=OPUS_MODEL),
                _asst("req_A", _thinking_block(REASONING_SUMMARY, 1), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": DANGER_COMMAND}, "toolu_A", 2),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            _run_danger_bash_hook(transcript, tmp_path=tmp_path)
        prompt = mock_reviewer.call_args.kwargs["prompt"]
        assert "(contains intent declaration)" not in prompt
        assert "Does the INTENT: declaration SPECIFICALLY describe" not in prompt
        assert "declared intent" not in prompt.lower()
        assert "stated intent" in prompt.lower()

    def test_m2_visible_text_phase2_prompt_never_claims_declaration(self, tmp_path):
        transcript = _write_transcript(
            [
                _asst(
                    "req_A",
                    _text_block("cleaning up scratch files now", 0),
                    model=OPUS_MODEL,
                ),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": DANGER_COMMAND}, "toolu_A", 1),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            result = _run_danger_bash_hook(transcript, tmp_path=tmp_path)
        assert result == {"continue": True}, result
        prompt = mock_reviewer.call_args.kwargs["prompt"]
        assert "(contains intent declaration)" not in prompt
        assert "Does the INTENT: declaration SPECIFICALLY describe" not in prompt
        assert "declared intent" not in prompt.lower()
        assert "stated intent" in prompt.lower()

    def test_blockage_details_carry_intent_source_on_phase2_mismatch(self, tmp_path):
        from pacemaker.hook import DEFAULT_DB_PATH

        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0), model=OPUS_MODEL),
                _asst("req_A", _thinking_block(REASONING_SUMMARY, 1), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": DANGER_COMMAND}, "toolu_A", 2),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("BLOCKED: mismatch", "test-reviewer"),
        ):
            result = _run_danger_bash_hook(transcript, tmp_path=tmp_path)
        assert result.get("decision") == "block"

        conn = sqlite3.connect(DEFAULT_DB_PATH)
        try:
            rows = conn.execute(
                "SELECT details FROM blockage_events ORDER BY id DESC LIMIT 1"
            ).fetchall()
        finally:
            conn.close()
        details = json.loads(rows[0][0])
        assert details.get("intent_source") == "reasoning_summary"

    def test_l1_non_exception_bash_blockage_details_never_has_intent_source_key(
        self, tmp_path
    ):
        from pacemaker.hook import DEFAULT_DB_PATH

        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0), model=SONNET_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": DANGER_COMMAND}, "toolu_A", 1),
                    model=SONNET_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        result = _run_danger_bash_hook(transcript, tmp_path=tmp_path)
        assert result.get("decision") == "block"

        conn = sqlite3.connect(DEFAULT_DB_PATH)
        try:
            rows = conn.execute(
                "SELECT details FROM blockage_events ORDER BY id DESC LIMIT 1"
            ).fetchall()
        finally:
            conn.close()
        details = json.loads(rows[0][0])
        assert "intent_source" not in details

    def test_not_found_anchor_limits_unaffected_still_blocks(self, tmp_path):
        """Anchor limits (#93) are unchanged: not_found still fails closed
        even for an exception model."""
        transcript = str(tmp_path / "t.jsonl")
        _write_transcript([], transcript)
        result = _run_danger_bash_hook(transcript, tmp_path=tmp_path)
        assert result.get("decision") == "block"
        assert "transcript not ready" in result.get("reason", "").lower()

    def test_stale_reissue_opus_relaxed_still_applies(self, tmp_path):
        """Hook-level stale-path coverage: a byte-identical Bash re-issue
        must still resolve via the relaxed path."""
        transcript = str(tmp_path / "t.jsonl")
        _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0), model=OPUS_MODEL),
                _asst("req_A", _thinking_block(REASONING_SUMMARY, 1), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": DANGER_COMMAND}, "toolu_A", 2),
                    model=OPUS_MODEL,
                ),
                _tool_result_entry("ok", "toolu_A"),
            ],
            transcript,
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            result = _run_danger_bash_hook(transcript, tmp_path=tmp_path)
        assert result == {"continue": True}, result
        prompt = mock_reviewer.call_args.kwargs["prompt"]
        assert REASONING_SUMMARY in prompt

    def test_malformed_config_never_crashes(self, tmp_path):
        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": DANGER_COMMAND}, "toolu_A", 1),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        cfg = _danger_bash_config({"reasoning_summary_intent_models": "not-a-list"})
        result = _run_danger_bash_hook(transcript, tmp_path=tmp_path, config=cfg)
        assert result.get("decision") == "block"
        assert "System Error" not in result.get("reason", "")
        assert "⛔ Your message had NO visible text" in result.get("reason", "")


class TestDangerBashChange2PriorReasoningSummary:
    """Issue #154 item 2 (decided): the CHANGE 2 prior-turn fallback is
    restricted to Write/Edit only. The danger-bash gate shares
    resolve_reasoning_summary_intent_source with the Write/Edit gate, but
    passes allow_prior_turn_fallback=False explicitly -- an empty
    anchored Bash turn keeps its PRE-CHANGE-2 behavior (block with the
    no-visible-text notice) even when a usable prior turn exists,
    preserving #93's deliberate "Bash is anchor-only" tightening and
    #139's stale-path anchor-only rule."""

    def test_empty_bash_turn_with_prior_summary_still_blocks(self, tmp_path):
        prior_summary = "I'll clean up the doomed scratch directory next."
        transcript = _write_transcript(
            [
                _asst("req_PLAN", _thinking_block(prior_summary, 0), model=OPUS_MODEL),
                _asst(
                    "req_PLAN",
                    _tool_use_block("Read", {"file_path": "a.py"}, "toolu_PLAN", 1),
                    model=OPUS_MODEL,
                ),
                _tool_result_entry("ok", "toolu_PLAN"),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": DANGER_COMMAND}, "toolu_A", 0),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        result = _run_danger_bash_hook(transcript, tmp_path=tmp_path)
        assert result.get("decision") == "block"
        assert "⛔ Your message had NO visible text" in result.get("reason", "")
        assert prior_summary not in result.get("reason", "")

    def test_empty_bash_turn_with_empty_prior_turn_blocked_as_before(self, tmp_path):
        transcript = _write_transcript(
            [
                _asst(
                    "req_PLAN",
                    _tool_use_block("Read", {"file_path": "a.py"}, "toolu_PLAN", 0),
                    model=OPUS_MODEL,
                ),
                _tool_result_entry("ok", "toolu_PLAN"),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": DANGER_COMMAND}, "toolu_A", 0),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        result = _run_danger_bash_hook(transcript, tmp_path=tmp_path)
        assert result.get("decision") == "block"
        assert "⛔ Your message had NO visible text" in result.get("reason", "")


class TestDangerBashDestructiveScopeWording:
    """Round 3, CHANGE 4: live evidence -- `cp X backup && rm X && grep
    -rn refs src/ docs/ tests/` with intent "delete the partial template"
    was falsely BLOCKED because the reviewer flagged the read-only `grep`
    as an undeclared side effect, even though it correctly matched the
    `rm` against the intent. Alignment must be judged ONLY against the
    destructive/dangerous operations -- read-only or non-destructive
    extras (searches, listings, reads, printing, copying to a backup) are
    not themselves a mismatch just because the intent doesn't name them.
    """

    def test_strict_prompt_states_destructive_scope_rule(self, tmp_path):
        real_intent = f"INTENT: Run {DANGER_COMMAND} to clean up scratch files."
        transcript = _write_transcript(
            [
                _asst("req_A", _text_block(real_intent, 0), model=SONNET_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": DANGER_COMMAND}, "toolu_A", 1),
                    model=SONNET_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            _run_danger_bash_hook(transcript, tmp_path=tmp_path)
        prompt = mock_reviewer.call_args.kwargs["prompt"]
        lowered = prompt.lower()
        assert "destructive" in lowered or "dangerous" in lowered
        assert "read-only" in lowered or "non-destructive" in lowered

    def test_relaxed_prompt_states_destructive_scope_rule(self, tmp_path):
        transcript = _write_transcript(
            [
                _asst("req_A", _thinking_block("", 0), model=OPUS_MODEL),
                _asst("req_A", _thinking_block(REASONING_SUMMARY, 1), model=OPUS_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": DANGER_COMMAND}, "toolu_A", 2),
                    model=OPUS_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            _run_danger_bash_hook(transcript, tmp_path=tmp_path)
        prompt = mock_reviewer.call_args.kwargs["prompt"]
        lowered = prompt.lower()
        assert "destructive" in lowered or "dangerous" in lowered
        assert "read-only" in lowered or "non-destructive" in lowered

    def test_strict_wording_otherwise_unchanged(self, tmp_path):
        """The pre-existing fixed-string lock (TestDangerBash
        NonExceptionPhase2PromptFixedString) uses an `in`-substring check
        that ends right after "treat as mismatch.\\n\\n" -- appending new
        text after that point does not break it, so it needs no update.
        This test independently confirms the SAME prefix/VALIDATE block
        is still present verbatim alongside the new sentence."""
        real_intent = f"INTENT: Run {DANGER_COMMAND} to clean up scratch files."
        transcript = _write_transcript(
            [
                _asst("req_A", _text_block(real_intent, 0), model=SONNET_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": DANGER_COMMAND}, "toolu_A", 1),
                    model=SONNET_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            _run_danger_bash_hook(transcript, tmp_path=tmp_path)
        prompt = mock_reviewer.call_args.kwargs["prompt"]
        assert prompt.startswith(
            "You are validating if the declared intent matches "
            "what a Bash command will actually do.\n\n"
            "ASSISTANT MESSAGE (contains intent declaration):\n"
        )
        assert (
            "VALIDATE:\n"
            "1. Does the INTENT: declaration SPECIFICALLY describe "
            "what this command does?\n"
            "2. Does the command scope match the intent scope?\n"
            "3. Is the description field honest about the effect?\n"
            "4. Are there undeclared side effects?\n\n"
            "If the intent declaration appears to be for a DIFFERENT "
            "tool call earlier in the message, treat as mismatch.\n\n"
        ) in prompt
        assert "BLOCKED:" in prompt  # response format requirement kept


class TestDangerBashNonExceptionPhase2PromptFixedString:
    """M2: the non-exception Phase 2 prompt must be BYTE-IDENTICAL to the
    pre-#151 wording -- locked via a fixed expected string, not a
    before/after diff."""

    def test_fixed_phase2_prompt_string(self, tmp_path):
        real_intent = f"INTENT: Run {DANGER_COMMAND} to clean up scratch files."
        transcript = _write_transcript(
            [
                _asst("req_A", _text_block(real_intent, 0), model=SONNET_MODEL),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": DANGER_COMMAND}, "toolu_A", 1),
                    model=SONNET_MODEL,
                ),
            ],
            str(tmp_path / "t.jsonl"),
        )
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as mock_reviewer:
            _run_danger_bash_hook(transcript, tmp_path=tmp_path)
        prompt = mock_reviewer.call_args.kwargs["prompt"]
        assert prompt.startswith(
            "You are validating if the declared intent matches "
            "what a Bash command will actually do.\n\n"
            "ASSISTANT MESSAGE (contains intent declaration):\n"
        )
        assert (
            "VALIDATE:\n"
            "1. Does the INTENT: declaration SPECIFICALLY describe "
            "what this command does?\n"
            "2. Does the command scope match the intent scope?\n"
            "3. Is the description field honest about the effect?\n"
            "4. Are there undeclared side effects?\n\n"
            "If the intent declaration appears to be for a DIFFERENT "
            "tool call earlier in the message, treat as mismatch.\n\n"
        ) in prompt
