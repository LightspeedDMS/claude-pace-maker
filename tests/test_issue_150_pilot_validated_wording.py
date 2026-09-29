"""
Issue #150 regression tests: Opus 5.5 agents at xhigh reasoning effort loop
on no-visible-text intent blocks because (per a pilot documented in the
issue and in CLAUDE.md's "Issue #150" section) the corrective signal was
not salient enough -- it was a generic notice appended at the END of a
long template the model had already stopped reading, and the only
proactive instruction lived far away at SessionStart/SubagentStart.

The pilot's fix (validated empirically, not guessed):
1. SessionStart/SubagentStart guidance OPENS with a specific, pilot-tested
   "C text" block, verbatim, before all other guidance.
2. The no-visible-text block message LEADS (right after the provenance
   tag) with the same wording plus a ready-to-copy INTENT: example naming
   the ACTUAL file/command, instead of appending a generic notice at the
   end.

This module tests both changes directly against the real production
entry points -- no LLM call is on this path (Stage 1 is pure regex/string
logic), so nothing here needs mocking.
"""

import os

# PACEMAKER_TEST_MODE must be set before any pacemaker import.
os.environ.setdefault("PACEMAKER_TEST_MODE", "1")

from pacemaker.hook import display_intent_validation_guidance  # noqa: E402
from pacemaker.intent_validator import validate_intent_and_code  # noqa: E402

# The pilot-validated "C text" block, copied verbatim from GitHub issue
# #150's "Fix (decided from the pilot)" section. Character-exact --
# rewording this is explicitly forbidden (some rewordings are refused by
# the API's [reasoning_extraction] safeguard; see the issue's "Safeguard
# hazard" note and CLAUDE.md's "Issue #150" section).
PILOT_C_TEXT = (
    "IMPORTANT — intent declarations: before EVERY Write/Edit tool call, "
    "your response must contain normal visible text (not "
    "reasoning/thinking) starting with `INTENT:` that names the file, "
    "the change, and the goal — in the same response, immediately before "
    "the tool call. Your reasoning is invisible to the validator; an "
    "INTENT written only in your reasoning does not exist. A response "
    "that consists only of a tool call will be rejected."
)

# Distinctive lead sentence shared by every no-visible-text block message,
# regardless of the ready-to-copy example that follows it.
NO_VISIBLE_TEXT_LEAD = (
    "Your reasoning is invisible to the validator; an INTENT written "
    "only in your reasoning does not exist."
)

# Wording the pilot found gets refused by the API's [reasoning_extraction]
# safeguard -- must never appear anywhere in shipped guidance/block text.
REFUSED_WORDING = "write the INTENT: line as a visible sentence"


class TestChange1GuidanceOpensWithPilotText:
    """The guidance rendered by display_intent_validation_guidance() (shared
    by both SessionStart and SubagentStart -- hook.py calls the exact same
    function at both call sites) must open with the pilot C text as its own
    paragraph, immediately after the provenance tag header."""

    def test_guidance_body_starts_with_exact_pilot_block(self):
        guidance = display_intent_validation_guidance()
        tag_header = "[pace-maker · intent_validation_guidance]\n"
        assert guidance.startswith(
            tag_header
        ), f"Provenance tag must stay first; got: {guidance[:80]!r}"
        body = guidance[len(tag_header) :]
        assert body.startswith(PILOT_C_TEXT), (
            "Guidance body must open with the pilot-validated C text, "
            f"character-exact; got: {body[: len(PILOT_C_TEXT) + 40]!r}"
        )

    def test_pilot_block_precedes_existing_guidance(self):
        guidance = display_intent_validation_guidance()
        assert guidance.index(PILOT_C_TEXT) < guidance.index(
            "INTENT VALIDATION ENABLED"
        )

    def test_existing_guidance_content_preserved(self):
        """Regression guard: the rest of the guidance (including the
        pre-existing VISIBLE TEXT ONLY paragraph) must be unchanged."""
        guidance = display_intent_validation_guidance()
        assert "VISIBLE TEXT ONLY" in guidance
        assert "TDD ENFORCEMENT FOR CORE CODE" in guidance
        assert "Senior Coding Nanny" in guidance

    def test_no_refused_wording_anywhere_in_guidance(self):
        guidance = display_intent_validation_guidance()
        assert REFUSED_WORDING not in guidance

    def test_subagent_start_uses_the_same_shared_function(self, tmp_path, capsys):
        """SubagentStart must open with the same block -- confirmed by
        driving the real hook entry point end-to-end (not just asserting
        both call sites reference the same function name). Mirrors
        tests/test_provenance_wiring.py's
        TestSubagentStartGuidanceAndManifestTagged pattern."""
        import json
        from unittest.mock import patch

        from pacemaker.hook import run_subagent_start_hook

        config_path = tmp_path / "config.json"
        state_path = tmp_path / "state.json"
        transcript_path = tmp_path / "main-session-150.jsonl"
        transcript_path.write_text("")
        config_path.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "langfuse_enabled": False,
                    "intent_validation_enabled": True,
                }
            )
        )
        state_path.write_text(json.dumps({"in_subagent": False, "subagent_counter": 0}))
        hook_data = {
            "hook_event_name": "SubagentStart",
            "session_id": "main-session-150",
            "agent_id": "agent-150",
            "transcript_path": str(transcript_path),
            "agent_type": "code-reviewer",
        }

        with (
            patch("pacemaker.hook.DEFAULT_CONFIG_PATH", str(config_path)),
            patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path)),
            patch("sys.stdin.read", return_value=json.dumps(hook_data)),
        ):
            run_subagent_start_hook()

        captured = capsys.readouterr()
        output_line = next(
            line for line in captured.out.splitlines() if '"hookSpecificOutput"' in line
        )
        parsed = json.loads(output_line)
        context = parsed["hookSpecificOutput"]["additionalContext"]
        assert PILOT_C_TEXT in context


class TestChange2NoVisibleTextNoticeLeadsBlockMessage:
    """The Write/Edit Stage 1 block (intent_validator.validate_intent_and_code)
    must lead with the new notice, with a ready-to-copy INTENT: example
    naming the ACTUAL file_path, whenever no_visible_text=True."""

    FILE_PATH = "/tmp/pacemaker_issue150_scratch/example.py"

    def _run_no_branch(self, tool_name, no_visible_text):
        return validate_intent_and_code(
            messages=[],
            code="x = 1\n",
            file_path=self.FILE_PATH,
            tool_name=tool_name,
            current_message_override="",
            no_visible_text=no_visible_text,
        )

    def test_write_no_visible_text_leads_with_notice_naming_actual_file(self):
        result = self._run_no_branch("Write", True)
        assert result["approved"] is False
        feedback = result["feedback"]
        tag_header = "[pace-maker · intent_validation_block]\n"
        assert feedback.startswith(tag_header)
        body = feedback[len(tag_header) :]
        assert body.startswith("⛔ Your message had NO visible text"), (
            f"Block message must LEAD with the new notice, right after "
            f"the provenance tag; got: {body[:120]!r}"
        )
        assert NO_VISIBLE_TEXT_LEAD in feedback
        assert (
            f"INTENT: Write {self.FILE_PATH} to <change>, so that <goal>." in feedback
        )
        # The old generic template may still follow afterwards.
        assert "⛔ Intent declaration required" in feedback

    def test_edit_no_visible_text_leads_with_notice_naming_actual_file(self):
        result = self._run_no_branch("Edit", True)
        feedback = result["feedback"]
        assert NO_VISIBLE_TEXT_LEAD in feedback
        assert f"INTENT: Edit {self.FILE_PATH} to <change>, so that <goal>." in feedback

    def test_write_no_visible_text_false_is_byte_identical_to_pre_fix(self):
        """When no_visible_text is False, the message must be exactly the
        pre-existing generic template -- no leading notice at all."""
        result = self._run_no_branch("Write", False)
        feedback = result["feedback"]
        assert NO_VISIBLE_TEXT_LEAD not in feedback
        expected_raw = """⛔ Intent declaration required

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
        assert feedback == "[pace-maker · intent_validation_block]\n" + expected_raw

    def test_raw_feedback_carries_notice_untagged(self):
        """Story #101 AC5 discipline: raw_feedback (governance-feed
        consumer) must carry the SAME notice text with no provenance tag."""
        result = self._run_no_branch("Write", True)
        raw_feedback = result["raw_feedback"]
        assert NO_VISIBLE_TEXT_LEAD in raw_feedback
        assert "[pace-maker" not in raw_feedback


def _run_danger_bash_hook(command: str, tmp_path, rule_id: str = "SD-150") -> str:
    """Drive the REAL danger-bash Phase 1 gate end-to-end (real
    ``run_pre_tool_hook``, real transcript_reader anchor resolution --
    only ``danger_bash_rules`` matching is mocked, mirroring
    tests/test_issue_141_thinking_only_notice.py's ``_rules_patch``
    pattern) with a bare ``tool_use`` (no thinking, no text block at all)
    so Phase 1 fires with ``no_visible_text=True``. Returns the block's
    "reason" string, built entirely by hook.py's own code -- never a
    pre-truncated value handed in by the test (code-review follow-up,
    item 4: the previous version of this test file was circular)."""
    import json
    from unittest.mock import MagicMock, patch

    from pacemaker import database
    from pacemaker.hook import run_pre_tool_hook

    db_path = str(tmp_path / "usage.db")
    transcript = str(tmp_path / "t.jsonl")
    database.initialize_database(db_path)
    with open(transcript, "w") as f:
        f.write(
            json.dumps(
                {
                    "requestId": "req_A",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_A",
                                "name": "Bash",
                                "input": {"command": command},
                                "apiBlockIndex": 0,
                            }
                        ],
                    },
                }
            )
            + "\n"
        )

    stdin_payload = json.dumps(
        {
            "session_id": "test-150-bash",
            "transcript_path": transcript,
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
    )
    rule = {"id": rule_id, "description": "test fixture"}
    with (
        patch("pacemaker.danger_bash_rules.load_rules", return_value=[rule]),
        patch("pacemaker.danger_bash_rules.match_command", return_value=[rule]),
        patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
        patch(
            "pacemaker.hook.load_config",
            return_value={
                "enabled": True,
                "intent_validation_enabled": True,
                "danger_bash_enabled": True,
                "hook_model": "auto",
            },
        ),
        patch("pacemaker.hook.DEFAULT_DB_PATH", db_path),
    ):
        result = run_pre_tool_hook()

    assert result.get("decision") == "block", f"Expected a block, got: {result}"
    return result.get("reason", "")


class TestChange2DangerBashNoticeLeadsAndCapsThroughRealHook:
    """Code-review follow-up (item 4): replaces the old circular test
    (which called build_no_visible_text_notice() directly with an
    ALREADY hand-truncated preview, never exercising hook.py's own
    truncation code at all). These tests drive the REAL Bash danger-bash
    gate end-to-end and assert the rendered reason (a) LEADS right after
    the provenance tag header, (b) names the ACTUAL command/preview, and
    (c) applies the 60-char cap via hook.py's OWN
    ``_build_bash_command_preview()`` -- not a value the test computed
    itself and merely echoed back."""

    def test_short_command_leads_and_names_command_verbatim(self, tmp_path):
        command = "rm -rf /tmp/pacemaker_issue150_scratch/doomed"
        reason = _run_danger_bash_hook(command, tmp_path)
        tag_header = "[pace-maker · danger_bash_block]\n"
        assert reason.startswith(tag_header)
        body = reason[len(tag_header) :]
        assert body.startswith("⛔ Your message had NO visible text"), (
            f"Notice must LEAD the reason right after the provenance "
            f"header; got: {body[:120]!r}"
        )
        assert NO_VISIBLE_TEXT_LEAD in reason
        assert f"INTENT: Run {command} to <goal>." in reason

    def test_long_command_capped_to_60_chars_by_hooks_own_code(self, tmp_path):
        command = "rm -rf " + ("a" * 100)
        reason = _run_danger_bash_hook(command, tmp_path)
        expected_preview = command[:60] + "..."
        example_line = next(
            line for line in reason.splitlines() if line.startswith("INTENT: Run ")
        )
        assert example_line == f"INTENT: Run {expected_preview} to <goal>."
        # The example line itself (not the old template's separate,
        # untruncated "Command:" line further down) must not contain the
        # full 107-char command.
        assert command not in example_line

    def test_heredoc_command_preview_is_first_line_only(self, tmp_path):
        command = "cat <<'EOF' > out.txt\nsome body text here\nEOF"
        reason = _run_danger_bash_hook(command, tmp_path)
        example_line = next(
            line for line in reason.splitlines() if line.startswith("INTENT: Run ")
        )
        # The rendered example must be a single line -- no embedded
        # newline from the original heredoc leaked through, and the
        # dropped body/closing lines must not appear in it.
        assert example_line == "INTENT: Run cat <<'EOF' > out.txt... to <goal>."
        assert "some body text here" not in example_line

    def test_leading_blank_line_command_preview_uses_first_non_blank_line(
        self, tmp_path
    ):
        """Second-review follow-up (item 2): a command whose FIRST line is
        blank (e.g. ``"\\nrm -rf x"``) previously produced an empty, or
        "..."-only, preview -- ``lines[0]`` was blank, so the collapsed
        preview was "" and the truncation flag (len(lines) > 1) still
        appended "...", yielding just "...". Must use the first
        NON-blank line instead."""
        command = "\nrm -rf /tmp/pacemaker_issue150_scratch/doomed"
        reason = _run_danger_bash_hook(command, tmp_path, rule_id="SD-150-BLANK1")
        example_line = next(
            line for line in reason.splitlines() if line.startswith("INTENT: Run ")
        )
        assert (
            example_line
            == "INTENT: Run rm -rf /tmp/pacemaker_issue150_scratch/doomed... to <goal>."
        )
        assert example_line != "INTENT: Run ... to <goal>."

    def test_whitespace_only_first_line_command_preview_uses_next_line(self, tmp_path):
        command = "   \nrm -rf /tmp/pacemaker_issue150_scratch/doomed"
        reason = _run_danger_bash_hook(command, tmp_path, rule_id="SD-150-BLANK2")
        example_line = next(
            line for line in reason.splitlines() if line.startswith("INTENT: Run ")
        )
        assert (
            example_line
            == "INTENT: Run rm -rf /tmp/pacemaker_issue150_scratch/doomed... to <goal>."
        )


class TestChange2DangerBashFalseIsByteIdenticalToPreFix:
    """Code-review follow-up (item 4): when no_visible_text is False, the
    Bash Phase-1 "no INTENT" block must be byte-identical to the
    pre-#150 template -- no leading notice at all."""

    def test_no_visible_text_false_matches_pre_fix_template_exactly(self, tmp_path):
        import json
        from unittest.mock import MagicMock, patch

        from pacemaker import database
        from pacemaker.hook import run_pre_tool_hook

        command = "rm -rf /tmp/pacemaker_issue150_scratch/doomed"
        db_path = str(tmp_path / "usage.db")
        transcript = str(tmp_path / "t.jsonl")
        database.initialize_database(db_path)
        with open(transcript, "w") as f:
            f.write(
                json.dumps(
                    {
                        "requestId": "req_A",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Cleaning up now.",
                                    "apiBlockIndex": 0,
                                }
                            ],
                        },
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "requestId": "req_A",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "toolu_A",
                                    "name": "Bash",
                                    "input": {"command": command},
                                    "apiBlockIndex": 1,
                                }
                            ],
                        },
                    }
                )
                + "\n"
            )

        stdin_payload = json.dumps(
            {
                "session_id": "test-150-bash-false",
                "transcript_path": transcript,
                "tool_name": "Bash",
                "tool_input": {"command": command},
            }
        )
        rule = {"id": "SD-150-FALSE", "description": "test fixture"}
        with (
            patch("pacemaker.danger_bash_rules.load_rules", return_value=[rule]),
            patch("pacemaker.danger_bash_rules.match_command", return_value=[rule]),
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch(
                "pacemaker.hook.load_config",
                return_value={
                    "enabled": True,
                    "intent_validation_enabled": True,
                    "danger_bash_enabled": True,
                    "hook_model": "auto",
                },
            ),
            patch("pacemaker.hook.DEFAULT_DB_PATH", db_path),
        ):
            result = run_pre_tool_hook()

        assert result.get("decision") == "block"
        reason = result.get("reason", "")
        assert NO_VISIBLE_TEXT_LEAD not in reason
        matched_ids = ["SD-150-FALSE"]
        expected_body = (
            "⛔ Dangerous Bash command detected — no INTENT: declaration\n\n"
            f"Matched danger rules: {matched_ids}\n"
            f"Command: {command[:300]}\n\n"
            "You must declare INTENT: specifying exactly what this command "
            "will do before executing dangerous Bash operations."
        )
        assert reason == "[pace-maker · danger_bash_block]\n" + expected_body


class TestChange2NoTddBranchNoticeLeadsAndNamesFile:
    """Code-review follow-up (item 4): dedicated regression coverage for
    the NO_TDD branch (previously only the NO branch had a "leads and
    names the file" test in this file)."""

    def test_no_tdd_notice_leads_and_names_core_file(self):
        result = validate_intent_and_code(
            messages=[],
            code="def validate():\n    pass\n",
            file_path="src/auth_example.py",
            tool_name="Write",
            current_message_override=(
                "INTENT: Modify src/auth_example.py to add a validate() "
                "function for input checks.\n"
            ),
            no_visible_text=True,
        )
        assert result["approved"] is False
        assert result["tdd_failure"] is True
        feedback = result["feedback"]
        tag_header = "[pace-maker · intent_validation_block]\n"
        assert feedback.startswith(tag_header)
        body = feedback[len(tag_header) :]
        assert body.startswith(
            "⛔ Your message had NO visible text"
        ), f"NO_TDD notice must LEAD the reason; got: {body[:120]!r}"
        assert NO_VISIBLE_TEXT_LEAD in feedback
        assert (
            "INTENT: Write src/auth_example.py to <change>, so that <goal>." in feedback
        )


class TestChange3TestCoverageLineOnCorePathsOnly:
    """Code-review follow-up (item 3): the example must include a
    ``Test coverage:`` line whenever file_path is a core path -- in the
    NO branch too, not just NO_TDD -- since a missing INTENT on a core
    path resolves to "NO", not "NO_TDD" (see _regex_stage1_check's
    docstring). Kept out for non-core paths."""

    def test_no_branch_non_core_path_omits_test_coverage_line(self, tmp_path):
        """Code-review follow-up (second review): the old hardcoded
        ``/tmp/pacemaker_issue150_scratch/example.py`` path made this test
        depend on the real contents of ``/tmp`` -- if a marker file
        (package.json, pyproject.toml, ...) ever ended up in an ancestor
        directory of that fixed path, Layer 2's marker walk would flip it
        to core and this test would fail confusingly. ``tmp_path`` is a
        FRESH, guaranteed-marker-free pytest temp directory, and the
        precondition is asserted explicitly first so a future regression
        (e.g. an accidental marker file dropped by another fixture) fails
        with a clear message instead of silently passing/failing on the
        wrong assertion."""
        from pacemaker.intent_validator import _is_core_path
        from pacemaker.core_paths import get_default_paths
        from pacemaker.excluded_paths import get_default_exclusions
        from pacemaker.extension_registry import get_default_extensions

        file_path = str(tmp_path / "example.py")
        assert (
            _is_core_path(
                file_path,
                get_default_paths(),
                get_default_exclusions(),
                get_default_extensions(),
            )
            is False
        ), f"Precondition failed: {file_path} unexpectedly resolved as a core path"

        result = validate_intent_and_code(
            messages=[],
            code="x = 1\n",
            file_path=file_path,
            tool_name="Write",
            current_message_override="",
            no_visible_text=True,
        )
        assert "Test coverage:" not in result["feedback"]

    def test_no_branch_core_path_includes_test_coverage_line(self):
        result = validate_intent_and_code(
            messages=[],
            code="x = 1\n",
            file_path="src/auth_example.py",
            tool_name="Write",
            current_message_override="",
            no_visible_text=True,
        )
        assert (
            "INTENT: Write src/auth_example.py to <change>, so that <goal>.\n"
            "Test coverage: <test file> - <test name>"
        ) in result["feedback"]

    def test_no_tdd_branch_includes_test_coverage_line(self):
        result = validate_intent_and_code(
            messages=[],
            code="def validate():\n    pass\n",
            file_path="src/auth_example.py",
            tool_name="Edit",
            current_message_override=(
                "INTENT: Modify src/auth_example.py to add validate().\n"
            ),
            no_visible_text=True,
        )
        assert result["tdd_failure"] is True
        assert "Test coverage: <test file> - <test name>" in result["feedback"]


class TestChange1CorePathCheckIsLazy:
    """Second-review follow-up (item 1): ``_is_core_path()`` was
    previously computed unconditionally before Stage 1, even though its
    result is only consumed inside the ``no_visible_text`` branches --
    the Layer 2 marker-file walk was measured at ~49ms for a ``/tmp``
    path, wasted on every single validation call regardless of whether
    ``no_visible_text`` is True. It must now run ONLY inside those two
    branches. Spies on the REAL ``_is_core_path`` (``wraps=``) so its
    actual behavior is exercised unmodified -- only the CALL COUNT is
    asserted, never a replaced return value."""

    def test_is_core_path_not_called_when_no_visible_text_false(self, tmp_path):
        from unittest.mock import patch

        from pacemaker import intent_validator

        with patch.object(
            intent_validator, "_is_core_path", wraps=intent_validator._is_core_path
        ) as spy:
            result = validate_intent_and_code(
                messages=[],
                code="x = 1\n",
                file_path=str(tmp_path / "example.py"),
                tool_name="Write",
                current_message_override="",
                no_visible_text=False,
            )
        assert result["approved"] is False
        spy.assert_not_called()

    def test_is_core_path_called_exactly_once_when_no_visible_text_true(self, tmp_path):
        from unittest.mock import patch

        from pacemaker import intent_validator

        with patch.object(
            intent_validator, "_is_core_path", wraps=intent_validator._is_core_path
        ) as spy:
            result = validate_intent_and_code(
                messages=[],
                code="x = 1\n",
                file_path=str(tmp_path / "example.py"),
                tool_name="Write",
                current_message_override="",
                no_visible_text=True,
            )
        assert result["approved"] is False
        spy.assert_called_once()


class TestChange1PlaceholderLikeTextDoesNotCrash:
    """Code-review follow-up (item 1, MEDIUM-HIGH): PromptLoader's
    ``variables=`` substitution rescans the SUBSTITUTED result for
    unreplaced ``{{word}}`` placeholders and raises ValueError. When the
    ACTUAL file_path/command itself contains a ``{{...}}``-shaped
    substring (a templated path, or a sed/awk script's own placeholder
    marker), the old design re-triggered that scan on its own text and
    crashed -- on the Write/Edit gate as an "Intent Validation System
    Error", and on the danger-bash gate as a fail-closed message telling
    the agent to ask the user to disable intent validation entirely."""

    def test_write_edit_path_containing_double_braces_does_not_crash(self):
        file_path = "/tmp/pacemaker_issue150_scratch/{{name}}/notes.txt"
        result = validate_intent_and_code(
            messages=[],
            code="x = 1\n",
            file_path=file_path,
            tool_name="Write",
            current_message_override="",
            no_visible_text=True,
        )
        assert result["approved"] is False
        assert "Unreplaced placeholders" not in result["feedback"]
        assert "System Error" not in result["feedback"]
        assert file_path in result["feedback"]

    def test_danger_bash_command_containing_double_braces_does_not_crash(
        self, tmp_path
    ):
        command = "sed -i s/{{VERSION}}/1.2/ f"
        reason = _run_danger_bash_hook(command, tmp_path, rule_id="SD-150-BRACE")
        assert "Unreplaced placeholders" not in reason
        assert "ask the user to run: pace-maker intent-validation off" not in reason
        assert command in reason
