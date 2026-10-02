"""
Story #155 AC11 -- guidance and block messages prefer the declare_intent tool
while keeping the visible ``INTENT:`` text as the fallback, and the #150
"C text" is untouched.

Real prompt files, real entry points. The #150 character-exact opener lock
(tests/test_issue_150_pilot_validated_wording.py) is updated deliberately in
that file; this module pins the new composition itself.
"""

import os

os.environ.setdefault("PACEMAKER_TEST_MODE", "1")

import pytest  # noqa: E402

from pacemaker import hook as hook_module  # noqa: E402
from pacemaker.hook import display_intent_validation_guidance  # noqa: E402
from pacemaker.intent_validator import (  # noqa: E402
    build_declare_intent_hint,
    validate_intent_and_code,
)

TAG_HEADER = "[pace-maker · intent_validation_guidance]\n"

# Story #155's pilot-2 wording, character-exact (design section 6).
DECLARE_TOOL_PARAGRAPH = (
    "Before each Write or Edit, call the `declare_intent` tool first "
    "(file_path, change, goal, and for source files test_coverage as "
    "`<test file> - <test name>`). This is the preferred way to declare. "
    "Call it on its own, then make the Write or Edit. One declaration "
    "covers consecutive Write/Edit calls to the same file; a different "
    "file needs its own declaration."
)

# Bug #164: one sentence ADDED after the pilot paragraph (never rewording it).
PER_EDIT_SENTENCE = (
    "A reused declaration is checked as-is against each later edit, so "
    "declare only what the next Write/Edit does; when the next edit does "
    "something different, declare again, or make the whole change in a "
    "single edit."
)

# Issue #150's C text -- must stay character-exact, never reworded.
PILOT_C_TEXT = (
    "IMPORTANT — intent declarations: before EVERY Write/Edit tool call, "
    "your response must contain normal visible text (not "
    "reasoning/thinking) starting with `INTENT:` that names the file, "
    "the change, and the goal — in the same response, immediately before "
    "the tool call. Your reasoning is invisible to the validator; an "
    "INTENT written only in your reasoning does not exist. A response "
    "that consists only of a tool call will be rejected."
)


class TestGuidanceLeadsWithTheTool:
    def test_default_guidance_opens_with_declare_tool_paragraph(self):
        guidance = display_intent_validation_guidance()
        assert guidance.startswith(TAG_HEADER + DECLARE_TOOL_PARAGRAPH)

    def test_c_text_follows_immediately_as_the_fallback(self):
        guidance = display_intent_validation_guidance()
        body = guidance[len(TAG_HEADER) :]
        assert body.startswith(
            DECLARE_TOOL_PARAGRAPH + " " + PER_EDIT_SENTENCE + "\n\n" + PILOT_C_TEXT
        )

    def test_existing_visible_intent_instructions_are_kept(self):
        guidance = display_intent_validation_guidance()
        assert "VISIBLE TEXT ONLY" in guidance
        assert "INTENT VALIDATION ENABLED" in guidance
        assert "TDD ENFORCEMENT FOR CORE CODE" in guidance
        assert "Senior Coding Nanny" in guidance

    def test_c_text_is_not_reworded_anywhere(self):
        assert PILOT_C_TEXT in display_intent_validation_guidance()

    def test_kill_switch_restores_the_pre_155_opener_exactly(self):
        guidance = display_intent_validation_guidance(
            {"intent_declaration_tool_enabled": False}
        )
        assert "declare_intent" not in guidance
        assert guidance.startswith(TAG_HEADER + PILOT_C_TEXT)

    def test_missing_config_key_means_enabled(self):
        assert DECLARE_TOOL_PARAGRAPH in display_intent_validation_guidance({})

    def test_call_sites_pass_their_own_config(self):
        """SessionStart and SubagentStart both hand over the config they
        already loaded, so the kill switch reaches the guidance."""
        import inspect

        source = inspect.getsource(hook_module)
        assert source.count("display_intent_validation_guidance(config)") == 2

    def test_externalized_paragraph_file_exists(self):
        path = os.path.join(
            os.path.dirname(hook_module.__file__),
            "prompts",
            "session_start",
            "declare_intent_guidance.md",
        )
        assert os.path.isfile(path)
        assert (
            open(path).read().strip()
            == DECLARE_TOOL_PARAGRAPH + " " + PER_EDIT_SENTENCE
        )


class TestBuildDeclareIntentHint:
    def test_names_the_tool_the_file_and_keeps_the_intent_fallback(self):
        hint = build_declare_intent_hint("/w/src/a.py")
        assert "`declare_intent`" in hint
        assert "/w/src/a.py" in hint
        assert "INTENT:" in hint
        assert "re-issue" in hint

    def test_is_one_sentence_pair_not_a_wall_of_text(self):
        assert len(build_declare_intent_hint("/w/a.py")) < 600

    @pytest.mark.parametrize("path", ["/w/{{template}}.py", "/w/{x}/a.py", "/w/%s.py"])
    def test_placeholder_like_paths_never_crash(self, path):
        assert path in build_declare_intent_hint(path)

    def test_no_refused_reasoning_extraction_wording(self):
        hint = build_declare_intent_hint("/w/a.py").lower()
        for banned in ("reveal", "copy your reasoning", "write out"):
            assert banned not in hint


class TestValidatorBlockMessagesCarryTheHint:
    def _validate(self, message, **kwargs):
        return validate_intent_and_code(
            messages=[message],
            code="x = 1",
            file_path="/w/scratch/n.py",
            tool_name="Write",
            **kwargs,
        )

    def test_default_call_is_byte_identical_to_pre_155(self):
        result = self._validate("no declaration here")
        assert "declare_intent" not in result["feedback"]
        assert "declare_intent" not in result["raw_feedback"]

    def test_stage_one_no_gets_the_hint_when_enabled(self):
        result = self._validate("no declaration here", declare_intent_hint=True)
        assert not result["approved"]
        assert "`declare_intent`" in result["feedback"]
        assert "/w/scratch/n.py" in result["feedback"]
        assert "declare_intent" in result["raw_feedback"]
        # Existing fallback instructions are still there.
        assert "Intent declaration required" in result["feedback"]

    def test_stage_one_no_tdd_gets_the_hint_when_enabled(self):
        result = validate_intent_and_code(
            messages=["INTENT: Modify auth.py to add f, goal: fix. /w/src/auth.py"],
            code="x = 1",
            file_path="/w/src/auth.py",
            tool_name="Write",
            declare_intent_hint=True,
        )
        assert result.get("tdd_failure") is True
        assert "`declare_intent`" in result["feedback"]
        assert "TDD Required" in result["feedback"]

    def test_no_visible_text_notice_still_leads_and_hint_follows(self):
        result = self._validate("", no_visible_text=True, declare_intent_hint=True)
        raw = result["raw_feedback"]
        # #150: the notice still LEADS the block reason ...
        assert raw.startswith("⛔ Your message had NO visible text")
        # ... and the declare_intent hint comes after it, before the
        # generic template.
        assert raw.index("Your reasoning is invisible") < raw.index("declare_intent")
        assert raw.index("declare_intent") < raw.index("⛔ Intent declaration required")

    def test_hint_is_untagged_inside_the_existing_tagged_block(self):
        result = self._validate("nothing", declare_intent_hint=True)
        assert result["feedback"].startswith("[pace-maker · intent_validation_block]")
        assert result["feedback"].count("[pace-maker ·") == 1
