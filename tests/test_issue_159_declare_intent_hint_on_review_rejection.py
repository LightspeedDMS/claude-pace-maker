"""
Bug #159 -- every Write/Edit rejection whose intent did NOT come from a
``declare_intent`` declaration or chain carries the one-sentence
declare_intent hint, not only the Stage-1 NO / NO_TDD blocks.

Live evidence: Sonnet 5.5 subagents' first edit (no tool call, no visible
text) was reviewed through the #151 prior-turn reasoning-summary fallback,
rejected by Stage 2 as "Intent excerpt is too vague", and that rejection
carried NO hint -- a 30-50 s round wasted before a hinted Stage-1 block
finally led them to call the tool.

Real validator / real ``run_pre_tool_hook``; only the Stage 2 reviewer LLM
call is mocked (at ``pacemaker.inference.resolve_and_call_with_reviewer``).
"""

import os
from unittest.mock import patch

import pytest

os.environ.setdefault("PACEMAKER_TEST_MODE", "1")

from pacemaker.intent_validator import (  # noqa: E402
    build_declare_intent_consumed_note,
    build_declare_intent_deferred_hint,
    build_declare_intent_hint,
    build_declare_intent_review_hint,
    build_declare_intent_unavailable_consumed_note,
    build_reviewer_unavailable_message,
    validate_intent_and_code,
)
from pacemaker.prompt_provenance import format_tag  # noqa: E402
from declare_intent_harness import (  # noqa: E402
    Harness,
    _anchor_not_found,
    _config,
)

FILE = "/w/scratch/n.py"
REVIEWER_TEXT = (
    "BLOCKED: Intent excerpt is too vague to verify the change.\n"
    "CLASSIFICATION: CLEAN_CODE"
)
BLOCKED = (REVIEWER_TEXT, "test-reviewer")
HINT_TAG = "[pace-maker · intent_validation_block]\n"


def _validate(reviewer_response, **kwargs):
    kwargs.setdefault("messages", ["INTENT: Modify n.py to add x, so y is easier."])
    with patch(
        "pacemaker.inference.resolve_and_call_with_reviewer",
        return_value=reviewer_response,
    ):
        return validate_intent_and_code(
            code="x = 1",
            file_path=FILE,
            tool_name="Write",
            hook_model="codex",
            **kwargs,
        )


def _relaxed(reviewer_response, source="reasoning_summary", **kwargs):
    return _validate(
        reviewer_response,
        messages=[""],
        reasoning_summary_relaxed_text="I'll add x to n.py.",
        reasoning_summary_intent_source=source,
        **kwargs,
    )


class TestHintWording:
    def test_hint_does_not_point_at_text_below_it(self):
        """The hint is now also appended AFTER reviewer text, so it must not
        refer to an INTENT: line 'described below'."""
        assert "described below" not in build_declare_intent_hint(FILE)

    def test_hint_still_names_tool_file_and_fallback(self):
        hint = build_declare_intent_hint(FILE)
        assert "`declare_intent`" in hint
        assert FILE in hint
        assert "`INTENT:`" in hint
        assert "re-issue" in hint


class TestReviewHintWording:
    """Review M1: after a Stage 2 rejection the CODE was rejected, so the hint
    must say to address the review -- never the Stage-1 template's bare
    'then re-issue this call', which a weak model could read as "declare and
    re-issue the identical buggy call"."""

    def test_leads_with_address_the_review(self):
        hint = build_declare_intent_review_hint(FILE)
        assert hint.startswith("Address the review above.")

    def test_names_tool_file_fields_and_keeps_the_intent_fallback(self):
        hint = build_declare_intent_review_hint(FILE)
        assert "`declare_intent`" in hint
        assert FILE in hint
        assert "test_coverage" in hint
        assert "`INTENT:`" in hint

    def test_never_tells_the_agent_to_reissue_the_same_call(self):
        hint = build_declare_intent_review_hint(FILE)
        assert "re-issue this call" not in hint
        assert "re-issue" not in hint

    @pytest.mark.parametrize("path", ["/w/{{template}}.py", "/w/{x}/a.py", "/w/%s.py"])
    def test_placeholder_like_paths_never_crash(self, path):
        assert path in build_declare_intent_review_hint(path)

    def test_externalized_prompt_file_exists(self):
        import pacemaker

        path = os.path.join(
            os.path.dirname(pacemaker.__file__),
            "prompts",
            "common",
            "declare_intent_hint_review.md",
        )
        assert os.path.isfile(path)


class TestDeferredHintWording:
    """Review L1: the deferred block already says RE-ISSUE THE IDENTICAL call;
    the hint must read as a faster alternative, not a competing instruction."""

    def test_leads_with_faster_alternative_that_avoids_the_race(self):
        hint = build_declare_intent_deferred_hint(FILE)
        assert hint.startswith(
            "Faster alternative that avoids this timing race entirely:"
        )
        assert "`declare_intent`" in hint
        assert FILE in hint
        assert "re-issue the same call" in hint
        assert "Preferred:" not in hint

    @pytest.mark.parametrize("path", ["/w/{{template}}.py", "/w/{x}/a.py", "/w/%s.py"])
    def test_placeholder_like_paths_never_crash(self, path):
        assert path in build_declare_intent_deferred_hint(path)


class TestConsumedNoteWording:
    """Review L2: a rejected tool-declared attempt consumed its declaration
    (and ended the chain), so the retry needs a fresh one."""

    def test_says_the_declaration_was_used_and_to_declare_again(self):
        note = build_declare_intent_consumed_note()
        assert note == (
            "Your declare_intent declaration was used by this rejected attempt; "
            "address the review above, then call `declare_intent` again before "
            "retrying. Declare only what the next edit does."
        )

    def test_fits_a_rejection_of_the_intent_not_only_of_the_code(self):
        note = build_declare_intent_consumed_note()
        assert "address the review above" in note
        assert "fix the code" not in note


class TestUnavailableConsumedNoteWording:
    """Reviewer-unavailable (empty response) of a tool/chain intent: nothing
    was reviewed, so there is no review to address -- just declare again."""

    def test_says_the_declaration_was_used_and_to_declare_again(self):
        note = build_declare_intent_unavailable_consumed_note()
        assert note == (
            "Your declare_intent declaration was used by this attempt; call "
            "`declare_intent` again before re-issuing."
        )

    def test_does_not_point_at_a_review_that_does_not_exist(self):
        assert "review above" not in build_declare_intent_unavailable_consumed_note()


UNAVAILABLE = ("", "unknown")


class TestReviewerUnavailableConsumedNote:
    """A reviewer-unavailable block of a tool/chain intent also consumed the
    declaration and ended the chain, yet only says "re-issue". Append the
    declare-again note -- and nothing else changes."""

    def _unavailable(self, **kwargs):
        return _validate(
            UNAVAILABLE,
            declare_intent_hint=True,
            reasoning_summary_intent_source="declare_intent",
            **kwargs,
        )

    def test_tool_intent_gets_the_note_after_the_unchanged_message(self):
        result = self._unavailable(intent_from_tool=True)
        assert result["reviewer_unavailable_failure"] is True
        original = format_tag(
            build_reviewer_unavailable_message(result["degradation"]),
            "fail_closed_error",
        )
        assert result["feedback"] == (
            original
            + "\n\n"
            + HINT_TAG
            + build_declare_intent_unavailable_consumed_note()
        )

    def test_governance_raw_feedback_is_the_unchanged_message(self):
        result = self._unavailable(intent_from_tool=True)
        assert result["raw_feedback"] == build_reviewer_unavailable_message(
            result["degradation"]
        )
        assert "declare_intent" not in result["raw_feedback"]

    def test_no_note_for_a_transcript_sourced_intent(self):
        result = self._unavailable(intent_from_tool=False)
        assert "declare_intent" not in result["feedback"]

    def test_no_note_when_the_tool_path_is_off(self):
        result = _validate(
            UNAVAILABLE,
            declare_intent_hint=False,
            intent_from_tool=True,
            reasoning_summary_intent_source="declare_intent",
        )
        assert result["reviewer_unavailable_failure"] is True
        assert "declare_intent" not in result["feedback"]

    def test_other_note_wordings_are_not_used_here(self):
        feedback = self._unavailable(intent_from_tool=True)["feedback"]
        assert build_declare_intent_consumed_note() not in feedback
        assert build_declare_intent_review_hint(FILE) not in feedback
        assert "address the review" not in feedback.lower()

    def test_externalized_prompt_file_exists(self):
        import pacemaker

        path = os.path.join(
            os.path.dirname(pacemaker.__file__),
            "prompts",
            "common",
            "declare_intent_consumed_note_unavailable.md",
        )
        assert os.path.isfile(path)


class TestValidatorStage2Hint:
    @pytest.mark.parametrize(
        "source", ["reasoning_summary", "visible_text", "prior_reasoning_summary"]
    )
    def test_relaxed_path_rejection_carries_review_hint(self, source):
        result = _relaxed(BLOCKED, source=source, declare_intent_hint=True)
        assert not result["approved"]
        feedback = result["feedback"]
        assert feedback.count(build_declare_intent_review_hint(FILE)) == 1
        # Never the Stage-1 / deferred wording.
        assert build_declare_intent_hint(FILE) not in feedback
        assert build_declare_intent_deferred_hint(FILE) not in feedback
        assert result["intent_source"] == source

    def test_normal_path_rejection_carries_review_hint(self):
        result = _validate(BLOCKED, declare_intent_hint=True)
        assert not result["approved"]
        feedback = result["feedback"]
        assert feedback.count(build_declare_intent_review_hint(FILE)) == 1
        assert build_declare_intent_hint(FILE) not in feedback

    @pytest.mark.parametrize("classification", ["CLEAN_CODE", "BUG", "TDD"])
    def test_hint_is_not_gated_on_classification(self, classification):
        reply = (
            f"BLOCKED: Intent excerpt is too vague.\nCLASSIFICATION: {classification}",
            "r",
        )
        result = _relaxed(reply, declare_intent_hint=True)
        assert build_declare_intent_review_hint(FILE) in result["feedback"]

    @pytest.mark.parametrize("relaxed", [False, True])
    def test_reviewer_text_is_never_altered_and_hint_follows_it(self, relaxed):
        run = _relaxed if relaxed else _validate
        result = run(BLOCKED, declare_intent_hint=True)
        feedback = result["feedback"]
        # The reviewer-relay segment is the verbatim reviewer text...
        assert feedback.startswith("[pace-maker · reviewer-relay · model=")
        relay_end = feedback.index(REVIEWER_TEXT) + len(REVIEWER_TEXT)
        # ...and the hint is separate, pace-maker-authored, tagged text after.
        tail = feedback[relay_end:]
        assert tail == "\n\n" + HINT_TAG + build_declare_intent_review_hint(FILE)

    @pytest.mark.parametrize("relaxed", [False, True])
    def test_raw_feedback_stays_the_untagged_reviewer_text(self, relaxed):
        run = _relaxed if relaxed else _validate
        result = run(BLOCKED, declare_intent_hint=True)
        assert result["raw_feedback"] == REVIEWER_TEXT

    @pytest.mark.parametrize("relaxed", [False, True])
    def test_default_is_byte_identical_to_before(self, relaxed):
        run = _relaxed if relaxed else _validate
        result = run(BLOCKED)
        assert "declare_intent" not in result["feedback"]
        assert result["feedback"].count("[pace-maker ·") == 1  # relay tag only

    def test_tool_declared_intent_gets_the_consumed_note_not_the_hint(self):
        result = _validate(
            BLOCKED,
            declare_intent_hint=True,
            intent_from_tool=True,
            reasoning_summary_intent_source="declare_intent",
        )
        assert not result["approved"]
        feedback = result["feedback"]
        tail_start = feedback.index(REVIEWER_TEXT) + len(REVIEWER_TEXT)
        assert feedback[tail_start:] == (
            "\n\n" + HINT_TAG + build_declare_intent_consumed_note()
        )
        assert build_declare_intent_review_hint(FILE) not in feedback
        assert build_declare_intent_hint(FILE) not in feedback
        assert result["raw_feedback"] == REVIEWER_TEXT
        assert result["intent_source"] == "declare_intent"

    def test_consumed_note_needs_the_tool_path_enabled(self):
        result = _validate(
            BLOCKED,
            declare_intent_hint=False,
            intent_from_tool=True,
            reasoning_summary_intent_source="declare_intent",
        )
        assert "declare_intent" not in result["feedback"]

    @pytest.mark.parametrize("relaxed", [False, True])
    def test_reviewer_unavailable_gets_no_hint(self, relaxed):
        """No reviewer answered: an infrastructure failure the agent fixes by
        re-issuing unchanged -- a declaration hint would misattribute it."""
        run = _relaxed if relaxed else _validate
        result = run(("", "unknown"), declare_intent_hint=True)
        assert result["reviewer_unavailable_failure"] is True
        assert "declare_intent" not in result["feedback"]

    @pytest.mark.parametrize("relaxed", [False, True])
    def test_approval_is_untouched(self, relaxed):
        run = _relaxed if relaxed else _validate
        result = run(("APPROVED", "test-reviewer"), declare_intent_hint=True)
        assert result["approved"] is True
        assert "feedback" not in result

    def test_stage_one_block_is_not_hinted_twice(self):
        result = _validate(
            BLOCKED, messages=["no declaration here"], declare_intent_hint=True
        )
        assert result["reviewer"] == "RegEx"
        assert result["feedback"].count(build_declare_intent_hint(FILE)) == 1
        assert result["feedback"].count("[pace-maker ·") == 1


# ---------------------------------------------------------------------------
# Real run_pre_tool_hook
# ---------------------------------------------------------------------------

SONNET = "claude-sonnet-5-5"
SUMMARY = "I'll add x to n.py so that callers get a default value."


def _relaxed_config(**overrides):
    return _config(reasoning_summary_intent_models=[SONNET], **overrides)


def _relaxed_anchor(summary=SUMMARY, visible=""):
    """Anchor stub: a FOUND turn with no visible text, reviewed through the
    #151 reasoning-summary path (what a Sonnet 5.5 first edit looks like)."""

    def anchor(transcript_path, tool_input=None, tool_name=None, **kwargs):
        diag = kwargs.get("_diagnostics")
        if diag is not None:
            diag.update(
                {
                    "attempts": 1,
                    "elapsed_seconds": 0.0,
                    "outcome": "found",
                    "anchor_model": SONNET,
                    "anchor_prose_text": visible,
                    "anchor_reasoning_summary": summary,
                    "anchor_has_visible_text": bool(visible),
                    "anchor_has_thinking": bool(summary),
                    "anchor_recent_context": [],
                }
            )
        return visible

    return anchor


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


class TestHookRelaxedPathHint:
    def test_relaxed_stage2_rejection_carries_hint_for_the_edited_file(self, h):
        result = h.run(
            "Write",
            h.noncore_file,
            anchor=_relaxed_anchor(),
            reviewer_response=BLOCKED,
            config=_relaxed_config(),
        )
        assert result["decision"] == "block"
        reason = result["reason"]
        assert "Intent excerpt is too vague" in reason
        assert build_declare_intent_review_hint(h.noncore_file) in reason
        assert build_declare_intent_hint(h.noncore_file) not in reason
        # Reviewer text came first, hint after it.
        assert reason.index("too vague") < reason.index("`declare_intent`")
        assert h.blockages()[-1]["category"] == "intent_validation_cleancode"

    def test_relaxed_rejection_without_hint_when_kill_switch_off(self, h):
        result = h.run(
            "Write",
            h.noncore_file,
            anchor=_relaxed_anchor(),
            reviewer_response=BLOCKED,
            config=_relaxed_config(intent_declaration_tool_enabled=False),
        )
        assert result["decision"] == "block"
        assert "declare_intent" not in result["reason"]

    def test_governance_feedback_text_is_untagged_and_unhinted(self, h):
        import sqlite3

        h.run(
            "Write",
            h.noncore_file,
            anchor=_relaxed_anchor(),
            reviewer_response=BLOCKED,
            config=_relaxed_config(),
        )
        conn = sqlite3.connect(h.db_path)
        try:
            rows = conn.execute(
                "SELECT feedback_text FROM governance_events"
            ).fetchall()
        finally:
            conn.close()
        assert rows, "a governance event must still be recorded"
        text = rows[-1][0]
        assert text == f"[test-reviewer] {REVIEWER_TEXT}"
        assert "declare_intent" not in text
        assert "[pace-maker" not in text

    def test_normal_path_stage2_rejection_carries_hint(self, h):
        """A non-exception model with a real visible INTENT rejected by Stage
        2 (the strict path) is hinted too."""
        anchor_text = f"INTENT: Modify {h.noncore_file} to add x, so y is easier."

        def anchor(transcript_path, tool_input=None, tool_name=None, **kwargs):
            return anchor_text

        result = h.run(
            "Write",
            h.noncore_file,
            anchor=anchor,
            reviewer_response=BLOCKED,
            config=_config(),
        )
        assert result["decision"] == "block"
        assert build_declare_intent_review_hint(h.noncore_file) in result["reason"]

    def test_tool_declared_intent_rejection_gets_consumed_note_only(self, h):
        h.declare(h.noncore_file, test_coverage="")
        result = h.run(
            "Write",
            h.noncore_file,
            reviewer_response=BLOCKED,
            config=_relaxed_config(),
        )
        assert result["decision"] == "block"
        reason = result["reason"]
        assert "Intent excerpt is too vague" in reason
        assert build_declare_intent_consumed_note() in reason
        assert build_declare_intent_review_hint(h.noncore_file) not in reason
        # The note is true: the declaration really was consumed.
        assert h.store_rows("declarations") == []

    def test_chain_intent_rejection_gets_consumed_note_only(self, h):
        h.declare(h.noncore_file, test_coverage="")
        approved = h.run("Write", h.noncore_file, config=_relaxed_config())
        assert approved.get("decision") != "block"
        assert [r["file_path"] for r in h.store_rows("chains")] == [h.noncore_file]
        result = h.run(
            "Write",
            h.noncore_file,
            reviewer_response=BLOCKED,
            config=_relaxed_config(),
        )
        assert result["decision"] == "block"
        assert build_declare_intent_consumed_note() in result["reason"]
        assert build_declare_intent_review_hint(h.noncore_file) not in result["reason"]
        # The note is true: the chain ended with this rejection.
        assert h.store_rows("chains") == []

    def test_tool_declared_rejection_has_no_note_when_kill_switch_off(self, h):
        h.declare(h.noncore_file, test_coverage="")
        result = h.run(
            "Write",
            h.noncore_file,
            reviewer_response=BLOCKED,
            config=_relaxed_config(intent_declaration_tool_enabled=False),
        )
        assert result["decision"] == "block"
        assert "declare_intent" not in result["reason"]

    def test_reviewer_unavailable_has_no_hint(self, h):
        result = h.run(
            "Write",
            h.noncore_file,
            anchor=_relaxed_anchor(),
            reviewer_response=("", "unknown"),
            config=_relaxed_config(),
        )
        assert result["decision"] == "block"
        assert "declare_intent" not in result["reason"]
        assert h.blockages()[-1]["category"] == (
            "intent_validation_reviewer_unavailable"
        )


class TestHookReviewerUnavailableConsumedNote:
    def test_tool_declared_intent_gets_the_declare_again_note(self, h):
        h.declare(h.noncore_file, test_coverage="")
        result = h.run(
            "Write",
            h.noncore_file,
            reviewer_response=UNAVAILABLE,
            config=_relaxed_config(),
        )
        assert result["decision"] == "block"
        reason = result["reason"]
        assert reason.startswith("[pace-maker · fail_closed_error]")
        assert "No reviewer responded" in reason
        assert reason.endswith(
            "\n\n" + HINT_TAG + build_declare_intent_unavailable_consumed_note()
        )
        assert h.blockages()[-1]["category"] == (
            "intent_validation_reviewer_unavailable"
        )
        # The note is true: declaration consumed.
        assert h.store_rows("declarations") == []

    def test_chain_intent_gets_the_declare_again_note_and_chain_is_ended(self, h):
        h.declare(h.noncore_file, test_coverage="")
        approved = h.run("Write", h.noncore_file, config=_relaxed_config())
        assert approved.get("decision") != "block"
        assert len(h.store_rows("chains")) == 1
        result = h.run(
            "Write",
            h.noncore_file,
            reviewer_response=UNAVAILABLE,
            config=_relaxed_config(),
        )
        assert result["decision"] == "block"
        assert build_declare_intent_unavailable_consumed_note() in result["reason"]
        assert h.store_rows("chains") == []

    def test_no_note_when_the_kill_switch_is_off(self, h):
        h.declare(h.noncore_file, test_coverage="")
        result = h.run(
            "Write",
            h.noncore_file,
            reviewer_response=UNAVAILABLE,
            config=_relaxed_config(intent_declaration_tool_enabled=False),
        )
        assert result["decision"] == "block"
        assert "declare_intent" not in result["reason"]

    def test_governance_event_text_is_unchanged(self, h):
        import sqlite3

        h.declare(h.noncore_file, test_coverage="")
        h.run(
            "Write",
            h.noncore_file,
            reviewer_response=UNAVAILABLE,
            config=_relaxed_config(),
        )
        conn = sqlite3.connect(h.db_path)
        try:
            rows = conn.execute(
                "SELECT feedback_text FROM governance_events"
            ).fetchall()
        finally:
            conn.close()
        assert rows
        assert "declare_intent" not in rows[-1][0]


class TestHookDeferredBlockHint:
    """The transcript-not-flushed block is a Write/Edit block for a
    transcript-sourced intent; declare_intent is the cure for that race too
    (the gate reads the declaration BEFORE any transcript work)."""

    def test_deferred_block_carries_hint_once_under_one_tag(self, h):
        result = h.run("Write", h.noncore_file, anchor=_anchor_not_found)
        assert result["decision"] == "block"
        reason = result["reason"]
        assert "transcript timing race" in reason
        # Pinned by the existing deferred tests: the block still tells the
        # agent to re-issue, and names the tool.
        assert "RE-ISSUE THE IDENTICAL Write TOOL CALL" in reason
        assert reason.count(build_declare_intent_deferred_hint(h.noncore_file)) == 1
        assert build_declare_intent_hint(h.noncore_file) not in reason
        assert build_declare_intent_review_hint(h.noncore_file) not in reason
        # The hint comes AFTER the instruction it is an alternative to.
        assert reason.index("RE-ISSUE THE IDENTICAL") < reason.index(
            "Faster alternative that avoids this timing race entirely"
        )
        assert reason.count("[pace-maker ·") == 1
        assert reason.startswith("[pace-maker · intent_validation_deferred]")

    def test_deferred_block_has_no_hint_when_kill_switch_off(self, h):
        result = h.run(
            "Write",
            h.noncore_file,
            anchor=_anchor_not_found,
            config=_config(intent_declaration_tool_enabled=False),
        )
        assert "transcript timing race" in result["reason"]
        assert "declare_intent" not in result["reason"]
