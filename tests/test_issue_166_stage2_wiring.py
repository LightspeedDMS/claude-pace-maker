"""
Bug #166 wiring -- the "SIGNATURES OF CALLED FUNCTIONS" section reaches the
Stage 2 prompt on both templates, from the real Write/Edit hook.

Mocking boundary (per CLAUDE.md): only the LLM provider
(``pacemaker.inference.resolve_and_call_with_reviewer``) is mocked.
"""

import json
import os
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, patch

os.environ.setdefault("PACEMAKER_TEST_MODE", "1")

from pacemaker.intent_validator import (  # noqa: E402
    _build_stage2_prompt,
    _build_stage2_prompt_reasoning_summary,
    validate_intent_and_code,
)
from pacemaker.secrets.database import create_secret  # noqa: E402
from pacemaker.stage2_signatures import (  # noqa: E402
    build_called_signatures_section,
)

HEADER = "SIGNATURES OF CALLED FUNCTIONS"
OPUS_MODEL = "claude-opus-5-5"
NONCORE_FILE = "scratch_module/mod.py"
VALID_INTENT = (
    "INTENT: Modify mod.py to add a helper.\nTest coverage: tests/test_mod.py"
)
SECTION = "\nSIGNATURES OF CALLED FUNCTIONS (marker):\n- x.py: def marker(a)\n"


def _load_template(name: str) -> str:
    import pacemaker

    path = os.path.join(
        os.path.dirname(pacemaker.__file__), "prompts", "pre_tool_use", name
    )
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_section(text: str, header: str, next_headers: List[str]) -> str:
    start = text.index(header)
    end = len(text)
    for h in next_headers:
        try:
            end = min(end, text.index(h, start + len(header)))
        except ValueError:
            continue
    return text[start:end].strip()


TEMPLATES = ["stage2_code_review.md", "stage2_code_review_reasoning_summary.md"]


class TestTemplates:
    def test_placeholder_sits_between_surrounding_context_and_siblings(self):
        for name in TEMPLATES:
            text = _load_template(name)
            surrounding = text.index("{surrounding_context_section}")
            called = text.index("{called_signatures_section}")
            sibling = text.index("{sibling_edits_section}")
            assert surrounding < called < sibling, name
            # Same line, no extra whitespace: empty section adds nothing.
            assert (
                "{surrounding_context_section}{called_signatures_section}"
                "{sibling_edits_section}"
            ) in text, name

    def test_placeholder_is_outside_the_m3_locked_sections(self):
        for name in TEMPLATES:
            text = _load_template(name)
            locked = [
                _extract_section(text, "CHECK 1:", ["CHECK 2:"]),
                _extract_section(text, "CHECK 3:", ["RESPONSE FORMAT"]),
                _extract_section(text, "CLASSIFICATION VALUES", ["RESPONSE FORMAT -"]),
                _extract_section(
                    text,
                    "PARTIAL CONTEXT WARNING (Edit operations)",
                    ["NEW FILE WARNING"],
                ),
            ]
            for section in locked:
                assert "{called_signatures_section}" not in section, name

    def test_check3_tells_the_reviewer_unseen_code_claims_are_uncertain(self):
        for name in TEMPLATES:
            check3 = _extract_section(
                _load_template(name),
                "CHECK 3: CLEAR BUG DETECTION",
                ["RESPONSE FORMAT"],
            )
            flat = " ".join(check3.split())
            assert "rests only on how unseen PROJECT code behaves" in flat, name
            for aspect in ("parameter names or order", "return shape", "output format"):
                assert aspect in flat, (name, aspect)
            assert "neither shown nor listed under SIGNATURES OF CALLED FUNCTIONS" in (
                flat
            ), name
            assert "do not reject on such a claim alone" in flat, name
            # ... and it must NOT relax anything else (review M2).
            assert "This does not relax anything else" in flat, name
            assert "bugs visible in the shown lines" in flat, name
            assert "misuse of standard-library or language behavior" in flat, name
            assert "an ignored failure from a call that can fail" in flat, name
            assert "are still rejected" in flat, name
            # The old, too-broad wording is gone.
            assert "depend on code not shown here" not in flat, name

    def test_check3_paragraph_has_no_format_braces(self):
        for name in TEMPLATES:
            check3 = _extract_section(
                _load_template(name),
                "CHECK 3: CLEAR BUG DETECTION",
                ["RESPONSE FORMAT"],
            )
            paragraph = check3[check3.index("A claim that rests only") :]
            paragraph = paragraph[: paragraph.index("Bugs to catch:")]
            assert "{" not in paragraph and "}" not in paragraph, name

    def test_check3_stays_identical_between_templates(self):
        normal, relaxed = (
            _extract_section(
                _load_template(n), "CHECK 3: CLEAR BUG DETECTION", ["RESPONSE FORMAT"]
            )
            for n in TEMPLATES
        )
        assert normal == relaxed


class TestBuilders:
    def test_normal_path_omits_the_section_by_default(self):
        prompt = _build_stage2_prompt(["m1"], "x = 1\n", NONCORE_FILE, "Write")
        assert "SIGNATURES OF CALLED FUNCTIONS (" not in prompt
        between = prompt[
            prompt.index("PROPOSED CODE:") : prompt.index("PARTIAL CONTEXT WARNING")
        ]
        assert between == "PROPOSED CODE:\nx = 1\n\n\n⚠️  "

    def test_relaxed_path_omits_the_section_by_default(self):
        prompt = _build_stage2_prompt_reasoning_summary(
            "intent", "x = 1\n", NONCORE_FILE, False
        )
        assert "SIGNATURES OF CALLED FUNCTIONS (" not in prompt

    def test_normal_path_renders_it_after_surrounding_before_siblings(self):
        prompt = _build_stage2_prompt(
            ["m1"],
            "code",
            NONCORE_FILE,
            "Edit",
            surrounding_context_section="\nSURROUND-MARK\n",
            called_signatures_section=SECTION,
            sibling_edits_section="\nSIBLING-MARK\n",
        )
        assert (
            prompt.index("SURROUND-MARK")
            < prompt.index("def marker(a)")
            < prompt.index("SIBLING-MARK")
        )

    def test_relaxed_path_renders_it(self):
        prompt = _build_stage2_prompt_reasoning_summary(
            "intent",
            "code",
            NONCORE_FILE,
            False,
            surrounding_context_section="\nSURROUND-MARK\n",
            called_signatures_section=SECTION,
            sibling_edits_section="\nSIBLING-MARK\n",
        )
        assert (
            prompt.index("SURROUND-MARK")
            < prompt.index("def marker(a)")
            < prompt.index("SIBLING-MARK")
        )

    def test_braces_in_the_section_cannot_crash_the_template(self):
        section = "\nSIGNATURES OF CALLED FUNCTIONS (x):\n- a.py: def f(a={})  # {x}\n"
        prompt = _build_stage2_prompt(
            ["m"], "c", NONCORE_FILE, "Edit", called_signatures_section=section
        )
        assert "def f(a={})  # {x}" in prompt


class TestValidateThreading:
    def test_normal_path(self):
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as reviewer:
            result = validate_intent_and_code(
                messages=[VALID_INTENT],
                code="new_code",
                file_path=NONCORE_FILE,
                tool_name="Edit",
                current_message_override=VALID_INTENT,
                called_signatures_section=SECTION,
            )
        assert result["approved"] is True
        assert "def marker(a)" in reviewer.call_args.kwargs["prompt"]

    def test_relaxed_path(self):
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as reviewer:
            result = validate_intent_and_code(
                messages=[],
                code="new_code",
                file_path=NONCORE_FILE,
                tool_name="Edit",
                current_message_override="",
                reasoning_summary_relaxed_text="auto-summarized intent",
                reasoning_summary_intent_source="reasoning_summary",
                called_signatures_section=SECTION,
            )
        assert result["approved"] is True
        assert "def marker(a)" in reviewer.call_args.kwargs["prompt"]

    def test_default_adds_nothing_for_existing_callers(self):
        with patch(
            "pacemaker.inference.resolve_and_call_with_reviewer",
            return_value=("APPROVED", "test-reviewer"),
        ) as reviewer:
            validate_intent_and_code(
                messages=[VALID_INTENT],
                code="new_code",
                file_path=NONCORE_FILE,
                tool_name="Edit",
                current_message_override=VALID_INTENT,
            )
        assert "SIGNATURES OF CALLED FUNCTIONS (" not in (
            reviewer.call_args.kwargs["prompt"]
        )


# ---------------------------------------------------------------------------
# Real hook runs
# ---------------------------------------------------------------------------


def _asst(request_id, block, model: Optional[str] = None) -> dict:
    message: dict = {"role": "assistant", "content": [block]}
    if model is not None:
        message["model"] = model
    return {"message": message, "requestId": request_id}


def _text(t: str, idx: int = 0) -> dict:
    return {"type": "text", "text": t, "apiBlockIndex": idx}


def _thinking(t: str, idx: int) -> dict:
    return {"type": "thinking", "thinking": t, "apiBlockIndex": idx}


def _tool_use(name: str, inp: dict, tool_id: str, idx: int) -> dict:
    return {
        "type": "tool_use",
        "id": tool_id,
        "name": name,
        "input": inp,
        "apiBlockIndex": idx,
    }


def _transcript(lines: List[dict], path: Path) -> str:
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return str(path)


def _config() -> dict:
    return {
        "enabled": True,
        "intent_validation_enabled": True,
        "tdd_enabled": True,
        "danger_bash_enabled": False,
        "hook_model": "codex",
    }


def _run_hook(transcript: str, tool_name: str, tool_input: dict):
    from pacemaker.hook import run_pre_tool_hook

    payload = json.dumps(
        {
            "session_id": "test-166-wiring",
            "transcript_path": transcript,
            "tool_name": tool_name,
            "tool_input": tool_input,
        }
    )
    with (
        patch("sys.stdin", MagicMock(read=lambda: payload)),
        patch("pacemaker.hook.load_config", return_value=_config()),
    ):
        return run_pre_tool_hook()


def _intent_for(path: Path) -> str:
    return (
        f"INTENT: Modify {path.name} to fix a bug, per the plan.\n"
        "Test coverage: existing tests cover this."
    )


UTIL_SOURCE = (
    "def compute(x, y, scale=1):\n"
    '    """Combine two values."""\n'
    "    return x + y\n"
)


def _edit_with_visible_intent(tmp_path: Path, name: str = "mod.py"):
    """An Edit of tmp_path/name whose new_string calls util_mod.compute."""
    (tmp_path / "util_mod.py").write_text(UTIL_SOURCE)
    target = tmp_path / name
    target.write_text("from util_mod import compute\nOLD_LINE = 1\n")
    tool_input = {
        "file_path": str(target),
        "old_string": "OLD_LINE = 1",
        "new_string": "NEW_LINE = compute(1, 2)",
    }
    transcript = _transcript(
        [
            _asst("req_A", _text(_intent_for(target))),
            _asst("req_A", _tool_use("Edit", tool_input, "toolu_A", 1)),
        ],
        tmp_path / "t.jsonl",
    )
    return transcript, tool_input


def _run_with_reviewer(transcript, tool_name, tool_input):
    with patch(
        "pacemaker.inference.resolve_and_call_with_reviewer",
        return_value=("APPROVED", "test-reviewer"),
    ) as reviewer:
        result = _run_hook(transcript, tool_name, tool_input)
    assert result == {"continue": True}, result
    return reviewer.call_args.kwargs["prompt"]


def _visible_intent_transcript(tmp_path: Path, target: Path, tool_name, tool_input):
    return _transcript(
        [
            _asst("req_A", _text(_intent_for(target))),
            _asst("req_A", _tool_use(tool_name, tool_input, "toolu_A", 1)),
        ],
        tmp_path / "t.jsonl",
    )


def _run_spying(transcript, tool_name, tool_input):
    """Run the hook with spies (real behavior) on the signatures builder and
    on its file reader; returns (prompt, builder_spy, reader_spy)."""
    from pacemaker.intent_validator import _read_target_file_for_review as real_read

    with (
        patch(
            "pacemaker.stage2_signatures.build_called_signatures_section",
            wraps=build_called_signatures_section,
        ) as builder,
        patch(
            "pacemaker.stage2_signatures._read_target_file_for_review",
            wraps=real_read,
        ) as reader,
    ):
        prompt = _run_with_reviewer(transcript, tool_name, tool_input)
    return prompt, builder, reader


class TestHookWiring:
    def test_edit_on_python_file_gets_the_section(self, tmp_path):
        transcript, tool_input = _edit_with_visible_intent(tmp_path)
        prompt = _run_with_reviewer(transcript, "Edit", tool_input)
        assert "SIGNATURES OF CALLED FUNCTIONS (" in prompt
        assert "def compute(x, y, scale=1)" in prompt
        assert "best-effort" in prompt
        # Docstrings of OTHER modules are not copied into the prompt.
        assert "Combine two values." not in prompt

    def test_section_comes_after_the_surrounding_context(self, tmp_path):
        transcript, tool_input = _edit_with_visible_intent(tmp_path)
        prompt = _run_with_reviewer(transcript, "Edit", tool_input)
        assert prompt.index("CURRENT FILE CONTENT AROUND THE EDIT (on disk") < (
            prompt.index("SIGNATURES OF CALLED FUNCTIONS (")
        )

    def test_write_of_a_new_python_file_gets_the_section(self, tmp_path):
        (tmp_path / "util_mod.py").write_text(UTIL_SOURCE)
        target = tmp_path / "fresh.py"
        tool_input = {
            "file_path": str(target),
            "content": "from util_mod import compute\nVALUE = compute(1, 2)\n",
        }
        transcript = _transcript(
            [
                _asst("req_A", _text(_intent_for(target))),
                _asst("req_A", _tool_use("Write", tool_input, "toolu_A", 1)),
            ],
            tmp_path / "t.jsonl",
        )
        prompt = _run_with_reviewer(transcript, "Write", tool_input)
        assert "def compute(x, y, scale=1)" in prompt

    def test_relaxed_path_gets_the_section(self, tmp_path):
        (tmp_path / "util_mod.py").write_text(UTIL_SOURCE)
        target = tmp_path / "mod.py"
        target.write_text("from util_mod import compute\nOLD_LINE = 1\n")
        tool_input = {
            "file_path": str(target),
            "old_string": "OLD_LINE = 1",
            "new_string": "NEW_LINE = compute(1, 2)",
        }
        transcript = _transcript(
            [
                _asst(
                    "req_A",
                    _thinking(
                        "The user wants OLD_LINE replaced with NEW_LINE in mod.py, "
                        "to fix a bug.",
                        0,
                    ),
                    model=OPUS_MODEL,
                ),
                _asst(
                    "req_A",
                    _tool_use("Edit", tool_input, "toolu_A", 1),
                    model=OPUS_MODEL,
                ),
            ],
            tmp_path / "t.jsonl",
        )
        prompt = _run_with_reviewer(transcript, "Edit", tool_input)
        assert "def compute(x, y, scale=1)" in prompt

    def test_non_python_source_file_gets_no_section(self, tmp_path):
        target = tmp_path / "mod.js"
        target.write_text("OLD_LINE\n")
        tool_input = {
            "file_path": str(target),
            "old_string": "OLD_LINE",
            "new_string": "compute(1, 2)",
        }
        transcript = _transcript(
            [
                _asst("req_A", _text(_intent_for(target))),
                _asst("req_A", _tool_use("Edit", tool_input, "toolu_A", 1)),
            ],
            tmp_path / "t.jsonl",
        )
        prompt = _run_with_reviewer(transcript, "Edit", tool_input)
        assert "SIGNATURES OF CALLED FUNCTIONS (" not in prompt

    def test_unresolvable_calls_add_no_section(self, tmp_path):
        target = tmp_path / "mod.py"
        target.write_text("OLD_LINE = 1\n")
        tool_input = {
            "file_path": str(target),
            "old_string": "OLD_LINE = 1",
            "new_string": "NEW_LINE = mystery(1)",
        }
        transcript = _transcript(
            [
                _asst("req_A", _text(_intent_for(target))),
                _asst("req_A", _tool_use("Edit", tool_input, "toolu_A", 1)),
            ],
            tmp_path / "t.jsonl",
        )
        prompt = _run_with_reviewer(transcript, "Edit", tool_input)
        assert "SIGNATURES OF CALLED FUNCTIONS (" not in prompt

    def test_gate_deadline_and_db_path_are_passed_to_the_builder(self, tmp_path):
        import pacemaker.hook as hook

        transcript, tool_input = _edit_with_visible_intent(tmp_path)
        with patch(
            "pacemaker.stage2_signatures.build_called_signatures_section",
            wraps=build_called_signatures_section,
        ) as spy:
            _run_with_reviewer(transcript, "Edit", tool_input)
        assert spy.call_count == 1
        kwargs = spy.call_args.kwargs
        assert kwargs["_db_path"] == hook.DEFAULT_DB_PATH
        assert isinstance(kwargs["_deadline"], float)
        # The ADDED code, not the old text, is what gets scanned.
        assert spy.call_args.args[1] == "NEW_LINE = compute(1, 2)"

    def test_edit_passes_the_hooks_own_read_of_the_target(self, tmp_path):
        transcript, tool_input = _edit_with_visible_intent(tmp_path)
        prompt, builder, reader = _run_spying(transcript, "Edit", tool_input)
        assert "def compute(x, y, scale=1)" in prompt
        assert builder.call_args.kwargs["_target_content"] == (
            "from util_mod import compute\nOLD_LINE = 1\n"
        )
        read_paths = [c.args[0] for c in reader.call_args_list]
        assert tool_input["file_path"] not in read_paths  # not read a 2nd time
        assert str(tmp_path / "util_mod.py") in read_paths

    def test_write_passes_its_new_content(self, tmp_path):
        (tmp_path / "util_mod.py").write_text(UTIL_SOURCE)
        target = tmp_path / "fresh.py"
        target.write_text("OLD = 1\n")
        content = "from util_mod import compute\nVALUE = compute(1, 2)\n"
        tool_input = {"file_path": str(target), "content": content}
        transcript = _visible_intent_transcript(tmp_path, target, "Write", tool_input)
        prompt, builder, reader = _run_spying(transcript, "Write", tool_input)
        assert "def compute(x, y, scale=1)" in prompt
        assert builder.call_args.kwargs["_target_content"] == content
        assert str(target) not in [c.args[0] for c in reader.call_args_list]

    def test_failed_hook_read_falls_back_to_a_disk_read(self, tmp_path):
        """old_string is not on disk (a sibling edit creates it): the hook's
        read yields no content, so the builder reads the file itself."""
        (tmp_path / "util_mod.py").write_text(UTIL_SOURCE)
        target = tmp_path / "mod.py"
        target.write_text("from util_mod import compute\nOTHER = 1\n")
        tool_input = {
            "file_path": str(target),
            "old_string": "OLD_LINE = 1",
            "new_string": "NEW_LINE = compute(1, 2)",
        }
        transcript = _visible_intent_transcript(tmp_path, target, "Edit", tool_input)
        prompt, builder, _reader = _run_spying(transcript, "Edit", tool_input)
        assert builder.call_args.kwargs["_target_content"] is None
        assert "def compute(x, y, scale=1)" in prompt

    def test_a_stored_secret_in_a_signature_is_masked_at_the_provider(self, tmp_path):
        import pacemaker.hook as hook

        # String DEFAULTS are redacted before masking (review L1), so the
        # stored secret sits in an annotation: the only remaining way for
        # arbitrary text to reach the section, and exactly what the
        # whole-prompt masking at the provider boundary must still cover.
        create_secret(hook.DEFAULT_DB_PATH, "text", "sk-sigsecretvalue789")
        (tmp_path / "util_mod.py").write_text(
            "def compute(x: 'sk-sigsecretvalue789', y=1):\n    return x\n"
        )
        target = tmp_path / "mod.py"
        target.write_text("from util_mod import compute\nOLD_LINE = 1\n")
        tool_input = {
            "file_path": str(target),
            "old_string": "OLD_LINE = 1",
            "new_string": "NEW_LINE = compute(1)",
        }
        transcript = _transcript(
            [
                _asst("req_A", _text(_intent_for(target))),
                _asst("req_A", _tool_use("Edit", tool_input, "toolu_A", 1)),
            ],
            tmp_path / "t.jsonl",
        )
        prompt = _run_with_reviewer(transcript, "Edit", tool_input)
        assert "SIGNATURES OF CALLED FUNCTIONS (" in prompt
        assert "def compute(x: " in prompt
        assert "sk-sigsecretvalue789" not in prompt
        assert "MASKED" in prompt
