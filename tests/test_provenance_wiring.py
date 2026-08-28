#!/usr/bin/env python3
"""
Integration tests for Story #101 — Self-Identifying Pace-Maker Provenance
Tagging. Drives real hook/validator entry points and asserts the emitted
text carries the pace-maker provenance tag, per acceptance criteria AC1-AC5.

Mocking constraint (per tests/conftest.py's autouse guard): all codex/gemini/
claude CLI/SDK calls are mocked at the namespace the code imports from
(e.g. pacemaker.intent_validator._call_stage2_validation), never the
...registry submodule directly.
"""

import ast
import json
import os
import pathlib
import tempfile
from unittest.mock import patch

from pacemaker import intent_validator
from pacemaker.hook import _fail_closed_message, run_pre_tool_hook

_SRC_PACEMAKER_DIR = (
    pathlib.Path(__file__).resolve().parent.parent / "src" / "pacemaker"
)


def _iter_pacemaker_asts():
    """Yield (path, ast.Module) for every .py file under src/pacemaker/.

    Does NOT swallow SyntaxError — every file in this tree is production
    Python that must parse; a genuine parse failure indicates a real bug
    (or a scan-path bug) and must fail the test loudly (Messi Rule 13,
    anti-silent-failure), not silently skip the file and risk the
    inventory guard passing without having inspected it.
    """
    for path in _SRC_PACEMAKER_DIR.rglob("*.py"):
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _format_reviewer_relay_is_called_via_ast() -> bool:
    """AST-scan every .py file under src/pacemaker/ (including
    prompt_provenance.py itself) for a real Call node whose function name
    is format_reviewer_relay — not a raw text/regex search, which would
    false-positive on a comment, string literal, or the
    `def format_reviewer_relay(...)` definition line itself."""
    for _path, tree in _iter_pacemaker_asts():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name == "format_reviewer_relay":
                return True
    return False


def _channels_used_via_format_tag() -> set:
    """AST-scan every .py file under src/pacemaker/ for format_tag(...)
    call sites and collect the literal string value of each call's second
    positional argument (the channel name).

    Deliberately AST-based rather than a plain string/regex search: a
    naive literal-string search would either (a) trivially "find" every
    channel inside prompt_provenance.py's own CHANNELS frozenset
    declaration — which is NOT a call site, defeating the whole point of
    this guard — or (b), if that file is excluded wholesale, incorrectly
    report session_start_manifest/subagent_start_manifest as unwired,
    since their ONLY format_tag(...) call sites are the manifest builder
    functions inside that same file. AST call-node inspection finds real
    format_tag(body, "channel") calls wherever they occur, including
    inside prompt_provenance.py, without matching the frozenset literal.
    """
    used: set = set()
    for _path, tree in _iter_pacemaker_asts():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name != "format_tag" or len(node.args) < 2:
                continue
            arg = node.args[1]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                used.add(arg.value)
    return used


# ==============================================================================
# intent_validator.validate_intent_and_code — Stage 1 / Stage 2 / exception
# ==============================================================================


class TestValidateIntentAndCodeStage1PlainTag:
    """Stage 1 RegEx block reasons are pace-maker's own text — plain tag,
    channel "intent_validation_block", never reviewer-relay."""

    def test_missing_intent_feedback_carries_plain_tag(self):
        current_message = "Let me fix this bug now."
        result = intent_validator.validate_intent_and_code(
            messages=["previous", current_message],
            code="def foo(): pass",
            file_path="/path/to/auth.py",
            tool_name="Write",
        )
        assert not result["approved"]
        assert result["feedback"].startswith("[pace-maker · intent_validation_block]")
        # AC5: pre-existing substantive text still present verbatim.
        assert "Intent declaration required" in result["feedback"]

    def test_missing_tdd_feedback_carries_plain_tag(self):
        current_message = "INTENT: Modify src/auth.py to add validation"
        result = intent_validator.validate_intent_and_code(
            messages=[current_message],
            code="def foo(): pass",
            file_path="src/auth.py",
            tool_name="Write",
        )
        assert not result["approved"]
        assert result["feedback"].startswith("[pace-maker · intent_validation_block]")
        assert "TDD Required for Core Code" in result["feedback"]


class TestValidateIntentAndCodeStage2ReviewerRelay:
    """Stage 2 feedback is a third-party reviewer's own text — reviewer-relay
    tag with model=<reviewer id>, never the plain tag. file_path is a
    non-core path ("utils.py", no leading src/) so Stage 1 requires no TDD
    declaration — matching the existing convention in
    tests/test_two_stage_validation.py's Stage 2 test fixtures."""

    def test_stage2_block_feedback_carries_reviewer_relay_tag(self):
        current_message = "INTENT: Modify utils.py to add helper function"
        with patch("pacemaker.intent_validator._call_stage2_validation") as mock_s2:
            mock_s2.return_value = (
                "Clean code violation: Bare except clause found",
                "codex-gpt5",
            )
            result = intent_validator.validate_intent_and_code(
                messages=[current_message],
                code="def f():\n    try:\n        pass\n    except:\n        pass",
                file_path="utils.py",
                tool_name="Write",
                hook_model="gpt-5",
            )
        assert not result["approved"]
        assert result["feedback"].startswith(
            "[pace-maker · reviewer-relay · model=codex-gpt5]"
        )
        # AC5: pre-existing substantive text still present verbatim.
        assert "Clean code violation: Bare except clause found" in result["feedback"]
        # AC4: advisory/third-party framing present.
        assert "advisory" in result["feedback"].lower()

    def test_stage2_threads_competitive_expression_as_model_id(self):
        current_message = "INTENT: Modify utils.py to add helper function"
        with patch("pacemaker.intent_validator._call_stage2_validation") as mock_s2:
            mock_s2.return_value = (
                "BLOCKED: mismatch found",
                "opus+gpt-5->haiku",
            )
            result = intent_validator.validate_intent_and_code(
                messages=[current_message],
                code="pass",
                file_path="utils.py",
                tool_name="Write",
                hook_model="opus+gpt-5->haiku",
            )
        assert not result["approved"]
        assert "model=opus+gpt-5->haiku" in result["feedback"]

    def test_stage2_approved_is_unaffected_by_tagging(self):
        """AC5: an APPROVED Stage 2 verdict is untouched — no tag applies to
        the approved path since there is no reason/feedback string emitted."""
        current_message = "INTENT: Modify utils.py to add helper function"
        with patch("pacemaker.intent_validator._call_stage2_validation") as mock_s2:
            mock_s2.return_value = ("APPROVED", "anthropic-sdk")
            result = intent_validator.validate_intent_and_code(
                messages=[current_message],
                code="pass",
                file_path="utils.py",
                tool_name="Write",
                hook_model="gpt-5",  # bypass SDK_AVAILABLE gate for Stage 2
            )
        assert result["approved"] is True
        assert result.get("reviewer") == "anthropic-sdk"


# ==============================================================================
# hook.py — _fail_closed_message (shared by Write/Edit + danger-bash gates)
# ==============================================================================


class TestFailClosedMessageTag:
    def test_fail_closed_message_carries_plain_tag(self):
        message = _fail_closed_message(RuntimeError("boom"))
        assert message.startswith("[pace-maker · fail_closed_error]")
        assert "unexpected internal error" in message.lower()


# ==============================================================================
# hook.py run_pre_tool_hook — Write/Edit TOCTOU-race deferred block
# ==============================================================================


class TestWriteEditDeferredBlockTag:
    @patch("pacemaker.hook.load_config")
    @patch("pacemaker.extension_registry.load_extensions")
    @patch("pacemaker.extension_registry.is_source_code_file")
    @patch("pacemaker.hook.get_last_n_messages_for_validation")
    @patch("pacemaker.hook.get_current_turn_message_for_validation")
    @patch("sys.stdin")
    def test_deferred_block_reason_carries_plain_tag(
        self,
        mock_stdin,
        mock_get_override,
        mock_get_messages,
        mock_is_source,
        mock_load_ext,
        mock_load_config,
    ):
        hook_data = {
            "session_id": "test",
            "transcript_path": "/tmp/nonexistent-transcript.jsonl",
            "tool_name": "Write",
            "tool_input": {"file_path": "/path/to/test.py", "content": "code"},
        }
        mock_stdin.read.return_value = json.dumps(hook_data)
        mock_load_config.return_value = {"intent_validation_enabled": True}
        mock_load_ext.return_value = [".py"]
        mock_is_source.return_value = True
        mock_get_messages.return_value = ["some message"]
        mock_get_override.return_value = None  # simulate not-yet-flushed turn

        result = run_pre_tool_hook()

        assert result.get("decision") == "block"
        assert result["reason"].startswith("[pace-maker · intent_validation_deferred]")
        assert "transcript timing race" in result["reason"]


# ==============================================================================
# hook.py run_pre_tool_hook — Danger-Bash gate (deferred / Phase 1 / Phase 2)
# ==============================================================================


class TestDangerBashDeferredBlockTag:
    @patch("pacemaker.hook.load_config")
    @patch("pacemaker.danger_bash_rules.load_rules")
    @patch("pacemaker.danger_bash_rules.match_command")
    @patch("pacemaker.hook.get_current_turn_message_for_validation")
    @patch("sys.stdin")
    def test_not_ready_block_reason_carries_plain_tag(
        self,
        mock_stdin,
        mock_get_override,
        mock_match_command,
        mock_load_rules,
        mock_load_config,
    ):
        hook_data = {
            "session_id": "test",
            "transcript_path": "/tmp/nonexistent-transcript.jsonl",
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /tmp/x", "description": "cleanup"},
        }
        mock_stdin.read.return_value = json.dumps(hook_data)
        mock_load_config.return_value = {
            "enabled": True,
            "intent_validation_enabled": True,
            "danger_bash_enabled": True,
        }
        mock_load_rules.return_value = [{"id": "SD-01", "description": "rm -rf"}]
        mock_match_command.return_value = [{"id": "SD-01", "description": "rm -rf"}]
        mock_get_override.return_value = None  # not found, not stale

        result = run_pre_tool_hook()

        assert result.get("decision") == "block"
        assert result["reason"].startswith("[pace-maker · danger_bash_deferred]")
        assert "transcript not ready" in result["reason"]


class TestDangerBashPhase1BlockTag:
    @patch("pacemaker.hook.load_config")
    @patch("pacemaker.danger_bash_rules.load_rules")
    @patch("pacemaker.danger_bash_rules.match_command")
    @patch("pacemaker.hook.get_current_turn_message_for_validation")
    @patch("sys.stdin")
    def test_no_intent_block_reason_carries_plain_tag(
        self,
        mock_stdin,
        mock_get_override,
        mock_match_command,
        mock_load_rules,
        mock_load_config,
    ):
        hook_data = {
            "session_id": "test",
            "transcript_path": "/tmp/nonexistent-transcript.jsonl",
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /tmp/x", "description": "cleanup"},
        }
        mock_stdin.read.return_value = json.dumps(hook_data)
        mock_load_config.return_value = {
            "enabled": True,
            "intent_validation_enabled": True,
            "danger_bash_enabled": True,
        }
        mock_load_rules.return_value = [{"id": "SD-01", "description": "rm -rf"}]
        mock_match_command.return_value = [{"id": "SD-01", "description": "rm -rf"}]
        mock_get_override.return_value = "no intent declaration here"

        result = run_pre_tool_hook()

        assert result.get("decision") == "block"
        assert result["reason"].startswith("[pace-maker · danger_bash_block]")
        assert "no INTENT: declaration" in result["reason"]


class TestDangerBashPhase2ReviewerRelayTag:
    @patch("pacemaker.hook.load_config")
    @patch("pacemaker.danger_bash_rules.load_rules")
    @patch("pacemaker.danger_bash_rules.match_command")
    @patch("pacemaker.hook.get_current_turn_message_for_validation")
    @patch("pacemaker.inference.resolve_and_call_with_reviewer")
    @patch("sys.stdin")
    def test_mismatch_block_wraps_only_reviewer_text_in_relay_tag(
        self,
        mock_stdin,
        mock_resolve,
        mock_get_override,
        mock_match_command,
        mock_load_rules,
        mock_load_config,
    ):
        hook_data = {
            "session_id": "test",
            "transcript_path": "/tmp/nonexistent-transcript.jsonl",
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /tmp/x", "description": "cleanup"},
        }
        mock_stdin.read.return_value = json.dumps(hook_data)
        mock_load_config.return_value = {
            "enabled": True,
            "intent_validation_enabled": True,
            "danger_bash_enabled": True,
        }
        mock_load_rules.return_value = [{"id": "SD-01", "description": "rm -rf"}]
        mock_match_command.return_value = [{"id": "SD-01", "description": "rm -rf"}]
        mock_get_override.return_value = "INTENT: delete a temp directory"
        mock_resolve.return_value = (
            "BLOCKED: the command scope is broader than declared",
            "codex-gpt5",
        )

        result = run_pre_tool_hook()

        assert result.get("decision") == "block"
        reason = result["reason"]
        # B3 (issue #101 review): pace-maker's OWN framing head must be
        # wrapped in the plain pace-maker tag on the same channel Phase 1
        # uses ("danger_bash_block") — symmetry with Phase 1, and exactly
        # the class of unattributed pace-maker message this story exists
        # to fix. The reviewer-relay tag nests INSIDE it, wrapping only the
        # reviewer's own text.
        outer_header = "[pace-maker · danger_bash_block]"
        inner_header = "[pace-maker · reviewer-relay · model=codex-gpt5]"
        assert reason.startswith(
            outer_header
        ), f"framing head must be wrapped in the outer pace-maker tag; got: {reason!r}"
        assert "Matched danger rules" in reason
        assert "Reviewer: codex-gpt5" in reason
        # AC4: the reviewer's own response text IS wrapped distinctly, and
        # nested AFTER (inside) the outer framing tag — not the other way
        # around.
        assert inner_header in reason
        assert reason.index(outer_header) < reason.index(inner_header), (
            "outer pace-maker tag must come before the nested reviewer-relay "
            f"tag; got: {reason!r}"
        )
        assert "the command scope is broader than declared" in reason


# ==============================================================================
# hook.py run_pre_tool_hook — Write/Edit governance-event feedback_text must
# stay UNTAGGED (B2, issue #101 review comment). The pace-maker provenance
# tag (and reviewer-relay wrapper) belongs ONLY on the Claude-facing block
# `reason` — never on the claude-usage governance feed's feedback_text,
# which uses its own pre-existing single-bracket `[expression]` reviewer-tag
# scheme (documented in CLAUDE.md under "Reviewer Identity Tracking").
# Duplicating the pace-maker tag there breaks that display (display.py's
# regex only strips the first bracket group).
# ==============================================================================


class TestGovernanceFeedbackUntagged:
    @patch("pacemaker.intent_validator.validate_intent_and_code")
    @patch("pacemaker.hook.get_current_turn_message_for_validation")
    @patch("pacemaker.hook.get_last_n_messages_for_validation")
    @patch("pacemaker.extension_registry.is_source_code_file")
    @patch("pacemaker.extension_registry.load_extensions")
    @patch("pacemaker.hook.load_config")
    @patch("sys.stdin")
    def test_write_edit_gate_governance_feedback_is_untagged_and_not_duplicated(
        self,
        mock_stdin,
        mock_load_config,
        mock_load_ext,
        mock_is_source,
        mock_get_messages,
        mock_get_override,
        mock_validate,
        tmp_path,
    ):
        import sqlite3

        from pacemaker import database

        db_path = str(tmp_path / "usage.db")
        database.initialize_database(db_path)

        hook_data = {
            "session_id": "test-b2",
            "transcript_path": "/tmp/nonexistent-transcript.jsonl",
            "tool_name": "Write",
            "tool_input": {"file_path": "/path/to/auth.py", "content": "code"},
        }
        mock_stdin.read.return_value = json.dumps(hook_data)
        mock_load_config.return_value = {"intent_validation_enabled": True}
        mock_load_ext.return_value = [".py"]
        mock_is_source.return_value = True
        mock_get_messages.return_value = ["INTENT: fix auth.py"]
        mock_get_override.return_value = "INTENT: fix auth.py"

        raw_reviewer_text = "Clean code violation: Bare except clause found"
        tagged_reviewer_text = intent_validator.format_reviewer_relay(
            raw_reviewer_text, "codex-gpt5"
        )
        mock_validate.return_value = {
            "approved": False,
            "reviewer": "codex-gpt5",
            "clean_code_failure": True,
            "feedback": tagged_reviewer_text,
            "raw_feedback": raw_reviewer_text,
        }

        with patch("pacemaker.hook.DEFAULT_DB_PATH", db_path):
            result = run_pre_tool_hook()

        assert result.get("decision") == "block"
        # Claude-facing reason keeps the reviewer-relay tag (AC4/AC5).
        assert result["reason"].startswith(
            "[pace-maker · reviewer-relay · model=codex-gpt5]"
        )

        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT feedback_text FROM governance_events"
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 1, f"Expected exactly one governance event; got {rows}"
        feedback_text = rows[0][0]
        # No pace-maker provenance tag leaking into the governance feed.
        assert "[pace-maker" not in feedback_text, (
            "governance-event feedback_text must stay untagged/raw; "
            f"got: {feedback_text!r}"
        )
        # Pre-existing single-bracket reviewer-tag scheme, not duplicated.
        assert feedback_text.count("[codex-gpt5]") == 1, (
            "reviewer id must appear exactly once (existing [expression] "
            f"scheme), not duplicated; got: {feedback_text!r}"
        )
        assert feedback_text.startswith("[codex-gpt5]")
        assert raw_reviewer_text in feedback_text


# ==============================================================================
# hook.py — PostToolUse emissions (subagent-delegation reminder, secrets nudge)
# ==============================================================================


class TestPostToolUseEmissionsTagged:
    def test_subagent_reminder_carries_plain_tag(self):
        from pacemaker.hook import inject_subagent_reminder

        message = inject_subagent_reminder({})
        assert message.startswith("[pace-maker · subagent_delegation_reminder]")

    def test_secrets_nudge_carries_plain_tag_for_post_tool_use(self):
        from pacemaker.hook import get_secrets_nudge

        message = get_secrets_nudge("post_tool_use")
        if message is not None:
            assert message.startswith("[pace-maker · secrets_nudge]")

    def test_secrets_nudge_carries_plain_tag_for_session_start(self):
        from pacemaker.hook import get_secrets_nudge

        message = get_secrets_nudge("session_start")
        if message is not None:
            assert message.startswith("[pace-maker · secrets_nudge]")


# ==============================================================================
# hook.py run_user_prompt_submit — § intel nudge
# ==============================================================================


class TestUserPromptSubmitIntelNudgeTagged:
    @patch("pacemaker.hook.user_commands")
    @patch("sys.stdin")
    def test_intel_nudge_carries_plain_tag(self, mock_stdin, mock_user_commands):
        mock_stdin.read.return_value = json.dumps(
            {"session_id": "test", "prompt": "hello"}
        )
        mock_user_commands.handle_user_prompt.return_value = {
            "intercepted": False,
            "output": "",
        }
        from pacemaker.hook import run_user_prompt_submit

        printed_lines = []
        with patch("builtins.print") as mock_print:
            mock_print.side_effect = lambda *args, **kwargs: printed_lines.append(
                args[0] if args else ""
            )
            try:
                run_user_prompt_submit()
            except SystemExit:
                pass

        # json.dumps escapes the U+00B7 middle dot as · by default, so
        # parse the JSON and check the decoded field rather than substring
        # matching the raw printed text.
        output_line = next(
            line for line in printed_lines if '"hookSpecificOutput"' in line
        )
        parsed = json.loads(output_line)
        context = parsed["hookSpecificOutput"]["additionalContext"]
        assert context.startswith("[pace-maker · intel_nudge]")


# ==============================================================================
# hook.py run_stop_hook — tempo block reason + continuation nudge
# ==============================================================================


class TestStopHookMessagesTagged:
    def setup_method(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

        self.temp_config = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w"
        )
        self.config_path = self.temp_config.name
        json.dump({"enabled": True, "tempo_mode": "on"}, self.temp_config)
        self.temp_config.close()

        self.temp_state = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w"
        )
        self.state_path = self.temp_state.name
        json.dump(
            {
                "session_id": "test-session-provenance",
                "subagent_counter": 0,
                "in_subagent": False,
            },
            self.temp_state,
        )
        self.temp_state.close()

        from pacemaker import database

        database.initialize_database(self.db_path)

    def teardown_method(self):
        for path in (self.db_path, self.config_path, self.state_path):
            os.unlink(path)

    def test_tempo_block_reason_carries_plain_tag(self):
        from pacemaker.hook import run_stop_hook

        transcript_path = tempfile.NamedTemporaryFile(
            suffix=".jsonl", delete=False, mode="w"
        ).name
        with open(transcript_path, "w") as f:
            f.write(
                json.dumps(
                    {
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Let me check..."}],
                        }
                    }
                )
                + "\n"
            )

        try:
            hook_data = {
                "session_id": "test-session-provenance",
                "transcript_path": transcript_path,
            }
            with (
                patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path),
                patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
                patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path),
                patch("sys.stdin.read", return_value=json.dumps(hook_data)),
                patch(
                    "pacemaker.hook.get_transcript_path",
                    return_value=transcript_path,
                ),
                patch("pacemaker.langfuse.orchestrator.handle_stop_finalize"),
                patch("pacemaker.intent_validator.validate_intent") as mock_validate,
            ):
                mock_validate.return_value = {
                    "decision": "block",
                    "reason": "Work appears incomplete",
                }
                result = run_stop_hook()

            assert result.get("decision") == "block"
            assert result["reason"].startswith("[pace-maker · stop_tempo_block]")
            assert "Work appears incomplete" in result["reason"]
        finally:
            os.unlink(transcript_path)

    def test_continuation_nudge_carries_plain_tag(self):
        from pacemaker.hook import run_stop_hook

        transcript_path = tempfile.NamedTemporaryFile(
            suffix=".jsonl", delete=False, mode="w"
        ).name
        with open(transcript_path, "w") as f:
            f.write(
                json.dumps(
                    {
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "name": "Bash",
                                    "input": {"command": "ls"},
                                }
                            ],
                        }
                    }
                )
                + "\n"
            )

        try:
            hook_data = {
                "session_id": "test-session-provenance",
                "transcript_path": transcript_path,
            }
            with (
                patch("pacemaker.hook.DEFAULT_CONFIG_PATH", self.config_path),
                patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
                patch("pacemaker.hook.DEFAULT_STATE_PATH", self.state_path),
                patch("sys.stdin.read", return_value=json.dumps(hook_data)),
                patch(
                    "pacemaker.hook.get_transcript_path",
                    return_value=transcript_path,
                ),
                patch("pacemaker.langfuse.orchestrator.handle_stop_finalize"),
                patch(
                    "pacemaker.hook.is_context_exhaustion_detected"
                ) as mock_exhaustion,
                patch(
                    "pacemaker.transcript_reader.detect_silent_tool_stop",
                    return_value=True,
                ),
            ):
                mock_exhaustion.return_value = False
                result = run_stop_hook()

            assert result.get("decision") == "block"
            assert result["reason"].startswith("[pace-maker · stop_continuation_nudge]")
        finally:
            os.unlink(transcript_path)


# ==============================================================================
# hook.py run_session_start_hook — guidance tag + manifest
# ==============================================================================


class TestSessionStartGuidanceAndManifestTagged:
    def _run_session_start(self, tmp_path):
        from pacemaker import hook

        config_path = tmp_path / "config.json"
        state_path = tmp_path / "state.json"
        config_path.write_text(
            json.dumps({"enabled": True, "intent_validation_enabled": True})
        )
        state_path.write_text(
            json.dumps(
                {"session_id": "test", "subagent_counter": 0, "in_subagent": False}
            )
        )
        with (
            patch("pacemaker.hook.DEFAULT_CONFIG_PATH", str(config_path)),
            patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path)),
        ):
            hook.run_session_start_hook()

    def test_intent_validation_guidance_carries_plain_tag(self, tmp_path, capsys):
        self._run_session_start(tmp_path)
        captured = capsys.readouterr()
        assert "[pace-maker · intent_validation_guidance]" in captured.out

    def test_session_start_manifest_appears(self, tmp_path, capsys):
        self._run_session_start(tmp_path)
        captured = capsys.readouterr()
        assert "[pace-maker · session_start_manifest]" in captured.out
        # Closed channel enumeration + never-list markers must be present.
        assert "csa_sibling_banner" in captured.out
        assert "danger_bash_block" in captured.out
        assert "reviewer-relay" in captured.out
        assert "exfiltrat" in captured.out.lower()
        assert "conceal" in captured.out.lower()


# ==============================================================================
# hook.py run_subagent_start_hook — guidance tag + abbreviated manifest
# ==============================================================================


class TestSubagentStartGuidanceAndManifestTagged:
    def test_manifest_and_guidance_appear_in_additional_context(self, tmp_path, capsys):
        from pacemaker.hook import run_subagent_start_hook

        config_path = tmp_path / "config.json"
        state_path = tmp_path / "state.json"
        transcript_path = tmp_path / "main-session-101.jsonl"
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
            "session_id": "main-session-101",
            "agent_id": "agent-101",
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
        assert "[pace-maker · intent_validation_guidance]" in context
        assert "[pace-maker · subagent_start_manifest]" in context


# ==============================================================================
# Inventory guard — no declared-but-unwired channel (the REVIEWER: precedent)
# ==============================================================================


class TestNoUntaggedEmitterRemains:
    """Testing Requirements: 'an inventory-style test asserting that the
    set of emission sites calling the shared formatter matches the
    manifest's declared channel list, so a future new channel cannot
    silently ship untagged.' This is the guard against this repo's
    documented `REVIEWER:` precedent (CLAUDE.md) — a tag format that was
    documented as shipped for months but never actually landed at every
    call site."""

    def test_every_plain_channel_has_a_format_tag_call_site(self):
        from pacemaker import prompt_provenance as pp

        used = _channels_used_via_format_tag()
        missing = [
            channel
            for channel in pp.CHANNELS
            if channel != pp.REVIEWER_RELAY_CHANNEL and channel not in used
        ]
        assert not missing, (
            f"channel(s) declared in CHANNELS but never used as a real "
            f"format_tag(...) call-site argument anywhere in "
            f"src/pacemaker/: {missing}"
        )

    def test_reviewer_relay_formatter_is_actually_called(self):
        assert _format_reviewer_relay_is_called_via_ast(), (
            "format_reviewer_relay is declared in prompt_provenance.py but "
            "never called (as a real AST Call node) from any emission site"
        )
