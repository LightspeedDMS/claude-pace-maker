"""
Bug #164 -- guidance steers models to declare only what each single
Write/Edit does (or make the whole declared change in one edit), so Stage 2
stops rejecting partial edits as "missing functionality".

Real prompt files, real entry points; no mocks. The pilot-validated #155
paragraph and the #150 "C text" are kept character-exact: the new sentence
is ADDED after them, never rewording them.
"""

import os

os.environ.setdefault("PACEMAKER_TEST_MODE", "1")

from pathlib import Path  # noqa: E402

import pacemaker  # noqa: E402
from pacemaker.hook import display_intent_validation_guidance  # noqa: E402
from pacemaker.intent_validator import (  # noqa: E402
    build_declare_intent_consumed_note,
    build_declare_intent_deferred_hint,
    build_declare_intent_hint,
    build_declare_intent_review_hint,
    build_declare_intent_unavailable_consumed_note,
)

PROMPTS = Path(pacemaker.__file__).parent / "prompts"

# The pilot-validated #155 paragraph, unchanged (see
# tests/test_issue_155_guidance_and_hints.py).
PILOT_155_PARAGRAPH = (
    "Before each Write or Edit, call the `declare_intent` tool first "
    "(file_path, change, goal, and for source files test_coverage as "
    "`<test file> - <test name>`). This is the preferred way to declare. "
    "Call it on its own, then make the Write or Edit. One declaration "
    "covers consecutive Write/Edit calls to the same file; a different "
    "file needs its own declaration."
)

PILOT_C_TEXT = (
    "IMPORTANT — intent declarations: before EVERY Write/Edit tool call, "
    "your response must contain normal visible text (not "
    "reasoning/thinking) starting with `INTENT:` that names the file, "
    "the change, and the goal — in the same response, immediately before "
    "the tool call. Your reasoning is invisible to the validator; an "
    "INTENT written only in your reasoning does not exist. A response "
    "that consists only of a tool call will be rejected."
)

PER_EDIT_SENTENCE = (
    "A reused declaration is checked as-is against each later edit, so "
    "declare only what the next Write/Edit does; when the next edit does "
    "something different, declare again, or make the whole change in a "
    "single edit."
)

SHORT_PHRASE = "only what the next edit does"

BANNED_EXTRACTION_WORDS = ("write out", "reveal", "copy your", "your reasoning")


class TestSessionStartGuidance:
    def test_guidance_file_is_pilot_paragraph_plus_per_edit_sentence(self):
        text = (
            (PROMPTS / "session_start" / "declare_intent_guidance.md")
            .read_text()
            .strip()
        )
        assert text.startswith(PILOT_155_PARAGRAPH)
        assert text == PILOT_155_PARAGRAPH + " " + PER_EDIT_SENTENCE

    def test_rendered_guidance_has_both_then_the_c_text_unchanged(self):
        guidance = display_intent_validation_guidance()
        body = guidance.split("\n", 1)[1]
        assert body.startswith(
            PILOT_155_PARAGRAPH + " " + PER_EDIT_SENTENCE + "\n\n" + PILOT_C_TEXT
        )

    def test_kill_switch_off_has_no_per_edit_sentence(self):
        guidance = display_intent_validation_guidance(
            {"intent_declaration_tool_enabled": False}
        )
        assert "that one Write/Edit does" not in guidance


class TestToolDescription:
    def test_description_is_pilot_wording_plus_per_edit_sentence(self):
        text = (
            (PROMPTS / "mcp" / "declare_intent_tool_description.md").read_text().strip()
        )
        assert text.endswith(
            "One declaration covers consecutive Write/Edit calls to the same "
            "file; a different file needs its own declaration. " + PER_EDIT_SENTENCE
        )


class TestHints:
    def test_review_hint_carries_the_short_phrase(self):
        assert SHORT_PHRASE in build_declare_intent_review_hint("/w/a.py")

    def test_consumed_note_carries_the_short_phrase(self):
        assert SHORT_PHRASE in build_declare_intent_consumed_note()

    def test_hints_that_do_not_fit_are_unchanged(self):
        # Stage-1 / deferred hints and the reviewer-unavailable note are about
        # missing/lost declarations, not scope; the sentence would not fit.
        assert SHORT_PHRASE not in build_declare_intent_hint("/w/a.py")
        assert SHORT_PHRASE not in build_declare_intent_deferred_hint("/w/a.py")
        assert SHORT_PHRASE not in build_declare_intent_unavailable_consumed_note()


class TestWordingConstraints:
    def test_added_text_is_one_short_sentence(self):
        assert len(PER_EDIT_SENTENCE) < 240
        assert PER_EDIT_SENTENCE.count(".") == 1

    def test_appended_sentence_reconciles_with_the_reuse_rule(self):
        """Review M3: 'One declaration covers consecutive Write/Edit calls to
        the same file' (pilot paragraph) next to 'declare each edit
        separately' contradicted itself. The appended sentence must say how
        the two fit: a reused declaration is checked as-is against each
        later edit."""
        assert "A reused declaration is checked as-is" in PER_EDIT_SENTENCE
        assert "declare each edit separately" not in PER_EDIT_SENTENCE
        guidance = display_intent_validation_guidance()
        assert "One declaration covers consecutive Write/Edit calls" in guidance
        assert "declare each edit separately" not in guidance

    def test_no_reasoning_extraction_phrasing_in_added_text(self):
        for text in (PER_EDIT_SENTENCE, SHORT_PHRASE):
            lowered = text.lower()
            for banned in BANNED_EXTRACTION_WORDS:
                assert banned not in lowered
