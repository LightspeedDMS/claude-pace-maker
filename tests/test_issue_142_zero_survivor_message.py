#!/usr/bin/env python3
"""
Tests for issue #142 — Write/Edit and danger-bash Stage 2 zero-survivor
handling.

Problem (bug #142): when zero reviewers respond (every verifier timed out
or raised ProviderError), run_mechanical()/resolve_and_call_with_reviewer()
correctly return ("", expression) — the documented fail-closed/fail-open
signal, unchanged by this fix. But the Write/Edit Stage-2 gate and the
danger-bash Phase 2 gate both relayed that "" as if it were genuine
reviewer feedback: wrapped in format_reviewer_relay(""), recorded under
"intent_validation_cleancode" (Write/Edit) or "intent_validation_dangerbash"
(danger-bash), producing a blank block and an empty governance
feedback_text ("[expr] " with nothing after it).

Fix: run_mechanical()/resolve_and_call_with_reviewer() populate the
existing _degradation out-param (issue #131 idiom) with
{"zero_survivors": True, "failed_providers": {model: reason, ...}} on
every "nobody answered at all" path. intent_validator.py and hook.py use
that to build a pace-maker-authored explanation via the public
build_reviewer_unavailable_message() helper (shared across module
boundaries by both gates, hence not `_`-prefixed), tagged with the
pre-existing "fail_closed_error" provenance channel (NOT reviewer-relay —
no reviewer said anything), and recorded under the new
"intent_validation_reviewer_unavailable" blockage category — distinct
from a genuine clean-code/bug/dangerbash rejection. The message wording
is deliberately neutral ("No reviewer responded (<provider>: <reason>;
...)") rather than assuming a timeout, since danger-bash routes through
the same helper and a failure there can be e.g. a missing CLI binary, not
a timeout. Both gates' record_blockage() details now also persist
failed_providers/zero_survivors (previously write-only), and the
Write/Edit gate's IV activity indicator now shows red for this category
(previously all of IV/TD/CC/BG showed green despite the block).

Mocking constraint (per tests/conftest.py's autouse guard): all
codex/gemini/claude CLI/SDK calls are mocked at the namespace the code
imports from (e.g. pacemaker.inference.competitive.get_provider,
pacemaker.inference.registry.get_provider,
pacemaker.intent_validator._call_stage2_validation,
pacemaker.inference.resolve_and_call_with_reviewer), never the
...registry submodule directly.
"""

import json
import sqlite3
from unittest.mock import MagicMock, patch

from pacemaker import database, intent_validator
from pacemaker.constants import BLOCKAGE_CATEGORIES, BLOCKAGE_CATEGORY_LABELS
from pacemaker.hook import run_pre_tool_hook
from pacemaker.inference.provider import ProviderError


# ==============================================================================
# constants.py — new blockage category
# ==============================================================================


class TestBlockageCategoryRegistered:
    def test_new_category_in_blockage_categories(self):
        assert "intent_validation_reviewer_unavailable" in BLOCKAGE_CATEGORIES

    def test_new_category_is_distinct_from_cleancode_and_dangerbash(self):
        assert "intent_validation_reviewer_unavailable" != "intent_validation_cleancode"
        assert (
            "intent_validation_reviewer_unavailable" != "intent_validation_dangerbash"
        )

    def test_new_category_has_nonempty_label(self):
        label = BLOCKAGE_CATEGORY_LABELS.get("intent_validation_reviewer_unavailable")
        assert label, "new category must have a human-readable label"


# ==============================================================================
# inference/competitive.py — run_mechanical zero-survivors degradation
# ==============================================================================


class TestRunMechanicalZeroSurvivorsDegradation:
    def test_run_mechanical_zero_survivors_populates_degradation_with_failed_providers(
        self,
    ):
        from pacemaker.inference.competitive import run_mechanical

        v1 = MagicMock()
        v1.query.side_effect = ProviderError("timed out after 35s")
        v2 = MagicMock()
        v2.query.side_effect = ProviderError("timed out after 35s")

        def _get(model):
            return v1 if model == "gpt-5.6-luna" else v2

        degradation = {}
        with patch("pacemaker.inference.competitive.get_provider", side_effect=_get):
            response, label = run_mechanical(
                verifiers=["gpt-5.6-luna", "haiku"],
                synthesizer="codex-beast",
                prompt="p",
                system_prompt="",
                call_context="stage2_unified",
                _degradation=degradation,
            )

        # Verdict/return contract is UNCHANGED (issue #142 requirement 5).
        assert response == ""
        assert label == "gpt-5.6-luna+haiku->codex-beast"

        assert degradation["degraded"] is True
        assert degradation["zero_survivors"] is True
        assert degradation["context"] == "competitive"
        assert set(degradation["failed_providers"]) == {"gpt-5.6-luna", "haiku"}
        assert "timed out after 35s" in degradation["failed_providers"]["haiku"]

    def test_degradation_none_is_a_noop_backward_compatible(self):
        from pacemaker.inference.competitive import run_mechanical

        failing = MagicMock()
        failing.query.side_effect = ProviderError("down")

        with patch(
            "pacemaker.inference.competitive.get_provider", return_value=failing
        ):
            response, label = run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash"],
                synthesizer="sonnet",
                prompt="p",
                system_prompt="",
                call_context="stage2_unified",
            )

        assert response == ""
        assert label == "gpt-5.5+gemini-flash->sonnet"

    def test_partial_survivor_approval_behavior_unchanged(self):
        """#131 regression: an APPROVED-but-degraded review (at least one
        verifier responded) must NOT be marked zero_survivors — that flag is
        reserved for the "nobody answered at all" case."""
        from pacemaker.inference.competitive import run_mechanical
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.gemini_provider import GeminiProvider

        v1 = MagicMock(spec=CodexProvider)
        v1.query.return_value = "APPROVED"
        v2 = MagicMock(spec=GeminiProvider)
        v2.query.side_effect = ProviderError("timed out after 35s")

        def _get(model):
            return v1 if model == "gpt-5.5" else v2

        degradation = {}
        with patch("pacemaker.inference.competitive.get_provider", side_effect=_get):
            response, label = run_mechanical(
                verifiers=["gpt-5.5", "gemini-flash"],
                synthesizer="sonnet",
                prompt="p",
                system_prompt="",
                call_context="stage2_unified",
                _degradation=degradation,
            )

        assert response == "APPROVED"
        assert degradation["degraded"] is True
        assert "zero_survivors" not in degradation
        assert "gemini-flash" in degradation["failed_providers"]


# ==============================================================================
# inference/registry.py — resolve_and_call_with_reviewer zero-survivor
# degradation (single-model path)
# ==============================================================================


class TestResolveAndCallWithReviewerZeroSurvivorDegradation:
    def test_both_providers_fail_populates_zero_survivors_degradation(self):
        from pacemaker.inference.registry import resolve_and_call_with_reviewer

        mock_provider = MagicMock()
        mock_provider.query.side_effect = ProviderError("codex empty response")

        degradation = {}
        with patch(
            "pacemaker.inference.registry.get_provider", return_value=mock_provider
        ):
            with patch(
                "pacemaker.inference.anthropic_provider.AnthropicProvider",
                return_value=mock_provider,
            ):
                response, reviewer = resolve_and_call_with_reviewer(
                    "gpt-5",
                    "prompt",
                    "sys",
                    "stage2_unified",
                    4000,
                    _degradation=degradation,
                )

        assert response == ""
        assert reviewer == "unknown"
        assert degradation["degraded"] is True
        assert degradation["zero_survivors"] is True
        assert degradation["context"] == "single_model_fallback"
        assert "gpt-5" in degradation["failed_providers"]
        # Issue #142 code-review follow-up (item 6): keyed by the actual
        # provider identity that failed ("anthropic-sdk"), not the
        # model-resolution alias "auto".
        assert "anthropic-sdk" in degradation["failed_providers"]
        assert "auto" not in degradation["failed_providers"]

    def test_auto_model_failure_populates_zero_survivors_degradation(self):
        from pacemaker.inference.registry import resolve_and_call_with_reviewer

        mock_provider = MagicMock()
        mock_provider.query.side_effect = ProviderError("anthropic down")

        degradation = {}
        with patch(
            "pacemaker.inference.registry.get_provider", return_value=mock_provider
        ):
            response, reviewer = resolve_and_call_with_reviewer(
                "auto",
                "prompt",
                "sys",
                "stage2_unified",
                4000,
                _degradation=degradation,
            )

        assert response == ""
        assert reviewer == "unknown"
        assert degradation["degraded"] is True
        assert degradation["zero_survivors"] is True
        assert degradation["failed_providers"] == {"auto": "anthropic down"}

    def test_degradation_none_is_a_noop_on_both_fail_path(self):
        """Backward compatible: no _degradation passed -> no crash, contract
        unchanged (matches pre-existing
        test_both_fail_returns_empty_and_unknown_reviewer)."""
        from pacemaker.inference.registry import resolve_and_call_with_reviewer

        mock_provider = MagicMock()
        mock_provider.query.side_effect = ProviderError("failed")

        with patch(
            "pacemaker.inference.registry.get_provider", return_value=mock_provider
        ):
            with patch(
                "pacemaker.inference.anthropic_provider.AnthropicProvider",
                return_value=mock_provider,
            ):
                response, reviewer = resolve_and_call_with_reviewer(
                    "gpt-5", "prompt", "sys", "stage2_unified", 4000
                )

        assert response == ""
        assert reviewer == "unknown"


# ==============================================================================
# intent_validator.build_reviewer_unavailable_message
# ==============================================================================


class TestBuildReviewerUnavailableMessage:
    def test_public_name_is_importable(self):
        """Issue #142 code-review follow-up (item 5): renamed from the
        private _build_reviewer_unavailable_message to the public
        build_reviewer_unavailable_message, since hook.py imports it across
        a module boundary — a `_`-prefixed name should never be a real
        cross-module API."""
        assert hasattr(intent_validator, "build_reviewer_unavailable_message")
        assert not hasattr(intent_validator, "_build_reviewer_unavailable_message")

    def test_build_reviewer_unavailable_message_names_each_failed_provider(self):
        degradation = {
            "degraded": True,
            "zero_survivors": True,
            "failed_providers": {
                "gpt-5.6-luna": "timed out after 35s",
                "haiku": "timed out after 35s",
            },
            "context": "competitive",
        }
        message = intent_validator.build_reviewer_unavailable_message(degradation)

        assert message
        assert "gpt-5.6-luna" in message
        assert "haiku" in message
        assert "timed out after 35s" in message
        assert "re-issue" in message.lower()
        assert "not a rejection" in message.lower()
        assert "infrastructure failure" in message.lower()

    def test_wording_is_neutral_not_timeout_specific(self):
        """Issue #142 code-review follow-up (item 5): the old wording "No
        code reviewer responded within the time budget" wrongly implied
        every failure was a timeout (e.g. a missing CLI binary is not a
        timeout) and was Write/Edit-specific phrasing reused verbatim by
        danger-bash. A non-timeout reason must render faithfully, not be
        contradicted by the surrounding sentence."""
        degradation = {
            "failed_providers": {"codex-beast": "CLI not found"},
        }
        message = intent_validator.build_reviewer_unavailable_message(degradation)

        assert "No reviewer responded" in message
        assert "CLI not found" in message
        assert "within the time budget" not in message

    def test_build_reviewer_unavailable_message_handles_missing_degradation_info(
        self,
    ):
        """degradation=None must still produce a non-empty, actionable
        message rather than raising or returning blank text."""
        message = intent_validator.build_reviewer_unavailable_message(None)

        assert message
        assert "No reviewer responded" in message

    def test_build_reviewer_unavailable_message_handles_empty_dict(self):
        message = intent_validator.build_reviewer_unavailable_message({})

        assert message
        assert "No reviewer responded" in message


# ==============================================================================
# intent_validator.validate_intent_and_code — Stage 2 zero-survivor wiring
# ==============================================================================


def _fake_stage2_zero_survivors(expression, failed_providers):
    def _side_effect(prompt, hook_model="auto", _degradation=None, _deadline=None):
        if _degradation is not None:
            _degradation["degraded"] = True
            _degradation["zero_survivors"] = True
            _degradation["failed_providers"] = dict(failed_providers)
            _degradation["context"] = "competitive"
        return "", expression

    return _side_effect


class TestValidateIntentAndCodeZeroSurvivorStage2:
    def test_zero_survivor_blocks_with_reviewer_unavailable_category(self):
        current_message = "INTENT: Modify utils.py to add helper function"
        failed = {
            "gpt-5.6-luna": "timed out after 35s",
            "haiku": "timed out after 35s",
        }
        with patch(
            "pacemaker.intent_validator._call_stage2_validation",
            side_effect=_fake_stage2_zero_survivors(
                "gpt-5.6-luna+haiku->codex-beast", failed
            ),
        ):
            result = intent_validator.validate_intent_and_code(
                messages=[current_message],
                code="def f(): pass",
                file_path="utils.py",
                tool_name="Write",
                hook_model="gpt-5.6-luna+haiku->codex-beast",
            )

        assert result["approved"] is False
        assert result["reviewer_unavailable_failure"] is True
        assert not result.get("clean_code_failure", False)
        assert not result.get("bug_failure", False)

        # Pace-maker's own explanation, NOT a reviewer-relay (no reviewer
        # actually said anything).
        assert result["feedback"].startswith("[pace-maker · fail_closed_error]")
        assert "gpt-5.6-luna" in result["feedback"]
        assert "haiku" in result["feedback"]
        assert "reviewer-relay" not in result["feedback"]

        # raw_feedback (governance/telemetry) must be non-empty too — this
        # is the exact field that produced the blank "[expr] " governance
        # entries in the bug report.
        assert result["raw_feedback"]
        assert "gpt-5.6-luna" in result["raw_feedback"]

    def test_genuine_blocked_verdict_still_relayed_as_reviewer_relay(self):
        """Regression: a genuine non-empty BLOCKED response from a
        competitive expression must still take the pre-existing
        reviewer-relay path, not the new reviewer-unavailable path."""
        current_message = "INTENT: Modify utils.py to add helper function"
        with patch(
            "pacemaker.intent_validator._call_stage2_validation",
            return_value=(
                "BLOCKED: mismatch found",
                "gpt-5.6-luna+haiku->codex-beast",
            ),
        ):
            result = intent_validator.validate_intent_and_code(
                messages=[current_message],
                code="def f(): pass",
                file_path="utils.py",
                tool_name="Write",
                hook_model="gpt-5.6-luna+haiku->codex-beast",
            )

        assert result["approved"] is False
        assert not result.get("reviewer_unavailable_failure", False)
        assert result["feedback"].startswith(
            "[pace-maker · reviewer-relay · model=gpt-5.6-luna+haiku->codex-beast]"
        )
        assert "mismatch found" in result["feedback"]

    def test_partial_survivor_approval_unaffected(self):
        """#131 regression via the Stage 2 wiring: a degraded-but-approved
        review (at least one verifier responded) is unaffected by the new
        zero-survivor handling."""
        current_message = "INTENT: Modify utils.py to add helper function"

        def _side_effect(prompt, hook_model="auto", _degradation=None, _deadline=None):
            if _degradation is not None:
                _degradation["degraded"] = True
                _degradation["failed_providers"] = {"haiku": "timed out after 35s"}
                _degradation["context"] = "competitive"
            return "APPROVED", "gpt-5.6-luna+haiku->codex-beast"

        with patch(
            "pacemaker.intent_validator._call_stage2_validation",
            side_effect=_side_effect,
        ):
            result = intent_validator.validate_intent_and_code(
                messages=[current_message],
                code="def f(): pass",
                file_path="utils.py",
                tool_name="Write",
                hook_model="gpt-5.6-luna+haiku->codex-beast",
            )

        assert result["approved"] is True
        assert result["degradation"]["degraded"] is True
        assert "zero_survivors" not in result["degradation"]


# ==============================================================================
# hook.py run_pre_tool_hook — Write/Edit gate zero-survivor block
# ==============================================================================


class TestWriteEditGateReviewerUnavailableCategory:
    @patch("pacemaker.intent_validator.validate_intent_and_code")
    @patch("pacemaker.hook.get_current_turn_message_for_validation")
    @patch("pacemaker.hook.get_last_n_messages_for_validation")
    @patch("pacemaker.extension_registry.is_source_code_file")
    @patch("pacemaker.extension_registry.load_extensions")
    @patch("pacemaker.hook.load_config")
    @patch("sys.stdin")
    def test_zero_survivor_block_records_new_category_and_nonempty_governance(
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
        db_path = str(tmp_path / "usage.db")
        database.initialize_database(db_path)

        hook_data = {
            "session_id": "test-142-write",
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

        failed_providers = {
            "gpt-5.6-luna": "timed out after 35s",
            "haiku": "timed out after 35s",
        }
        raw = (
            "⛔ No reviewer responded (gpt-5.6-luna: timed out after 35s; "
            "haiku: timed out after 35s). This is a reviewer "
            "infrastructure failure, NOT a rejection of your intent or "
            "code — re-issue the identical tool call."
        )
        mock_validate.return_value = {
            "approved": False,
            "reviewer": "gpt-5.6-luna+haiku->codex-beast",
            "reviewer_unavailable_failure": True,
            "feedback": intent_validator.format_tag(raw, "fail_closed_error"),
            "raw_feedback": raw,
            # Issue #142 code-review follow-up (item 4): this is what
            # validate_intent_and_code() now returns so hook.py can persist
            # it into record_blockage()'s details.
            "degradation": {
                "degraded": True,
                "zero_survivors": True,
                "failed_providers": failed_providers,
                "context": "competitive",
            },
        }

        with patch("pacemaker.hook.DEFAULT_DB_PATH", db_path):
            result = run_pre_tool_hook()

        assert result.get("decision") == "block"
        assert result["reason"].startswith("[pace-maker · fail_closed_error]")
        assert "No reviewer responded" in result["reason"]

        conn = sqlite3.connect(db_path)
        try:
            blockage_rows = conn.execute(
                "SELECT category, reason, details FROM blockage_events"
            ).fetchall()
            gov_rows = conn.execute(
                "SELECT feedback_text FROM governance_events"
            ).fetchall()
            activity_rows = conn.execute(
                "SELECT event_code, status FROM activity_events"
            ).fetchall()
        finally:
            conn.close()

        assert len(blockage_rows) == 1, blockage_rows
        assert blockage_rows[0][0] == "intent_validation_reviewer_unavailable"
        assert blockage_rows[0][1], "blockage reason must be non-empty"
        assert "No reviewer responded" in blockage_rows[0][1]

        # Issue #142 code-review follow-up (item 4): failed_providers/
        # zero_survivors must be persisted, not left write-only.
        details = json.loads(blockage_rows[0][2])
        assert details["failed_providers"] == failed_providers
        assert details["zero_survivors"] is True

        assert len(gov_rows) == 1, gov_rows
        feedback_text = gov_rows[0][0]
        # This is exactly the shape reported as broken in the bug: the
        # governance entry must NOT be just the bracketed expression with
        # nothing after it.
        assert feedback_text.strip() != "[gpt-5.6-luna+haiku->codex-beast]"
        assert "No reviewer responded" in feedback_text

        # Issue #142 code-review follow-up (item 1): IV must show red — the
        # call WAS blocked, even though no specific TD/CC/BG check fired.
        activity_by_code = dict(activity_rows)
        assert activity_by_code["IV"] == "red"
        assert activity_by_code["TD"] == "green"
        assert activity_by_code["CC"] == "green"
        assert activity_by_code["BG"] == "green"


# ==============================================================================
# hook.py run_pre_tool_hook — Danger-Bash Phase 2 zero-survivor block
# ==============================================================================


class TestDangerBashPhase2ZeroSurvivor:
    @patch("pacemaker.hook.load_config")
    @patch("pacemaker.danger_bash_rules.load_rules")
    @patch("pacemaker.danger_bash_rules.match_command")
    @patch("pacemaker.hook.get_current_turn_message_for_validation")
    @patch("pacemaker.inference.resolve_and_call_with_reviewer")
    @patch("sys.stdin")
    def test_zero_survivor_blocks_with_reviewer_unavailable_category(
        self,
        mock_stdin,
        mock_resolve,
        mock_get_override,
        mock_match_command,
        mock_load_rules,
        mock_load_config,
        tmp_path,
    ):
        db_path = str(tmp_path / "usage.db")
        database.initialize_database(db_path)

        hook_data = {
            "session_id": "test-142-bash",
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

        failed_providers = {
            "gpt-5.6-luna": "timed out after 35s",
            "haiku": "timed out after 35s",
        }
        _bash_reviewer = "gpt-5.6-luna+haiku->codex-beast"

        def _resolve_side_effect(**kwargs):
            _degradation = kwargs.get("_degradation")
            if _degradation is not None:
                _degradation["degraded"] = True
                _degradation["zero_survivors"] = True
                _degradation["failed_providers"] = dict(failed_providers)
                _degradation["context"] = "competitive"
            return "", _bash_reviewer

        mock_resolve.side_effect = _resolve_side_effect

        with patch("pacemaker.hook.DEFAULT_DB_PATH", db_path):
            result = run_pre_tool_hook()

        assert result.get("decision") == "block"
        reason = result["reason"]
        assert reason.startswith("[pace-maker · fail_closed_error]")
        assert "reviewer-relay" not in reason
        assert "No reviewer responded" in reason
        assert "gpt-5.6-luna" in reason
        assert "haiku" in reason

        conn = sqlite3.connect(db_path)
        try:
            blockage_rows = conn.execute(
                "SELECT category, reason, details FROM blockage_events"
            ).fetchall()
            gov_rows = conn.execute(
                "SELECT feedback_text FROM governance_events"
            ).fetchall()
        finally:
            conn.close()

        assert len(blockage_rows) == 1, blockage_rows
        assert blockage_rows[0][0] == "intent_validation_reviewer_unavailable"
        assert blockage_rows[0][0] != "intent_validation_dangerbash"
        assert blockage_rows[0][1], "blockage reason must be non-empty"

        # Issue #142 code-review follow-up (item 4): failed_providers/
        # zero_survivors must be persisted, not left write-only on
        # _bash_degradation.
        details = json.loads(blockage_rows[0][2])
        assert details["failed_providers"] == failed_providers
        assert details["zero_survivors"] is True

        assert len(gov_rows) == 1, gov_rows
        feedback_text = gov_rows[0][0]
        assert feedback_text.strip() != ""
        assert "No reviewer responded" in feedback_text
        # Issue #142 code-review follow-up (item 3): prefixed with the
        # reviewer identity, matching the sibling genuine-mismatch branch.
        assert feedback_text.startswith(f"[{_bash_reviewer}] ")

    @patch("pacemaker.hook.load_config")
    @patch("pacemaker.danger_bash_rules.load_rules")
    @patch("pacemaker.danger_bash_rules.match_command")
    @patch("pacemaker.hook.get_current_turn_message_for_validation")
    @patch("pacemaker.inference.resolve_and_call_with_reviewer")
    @patch("sys.stdin")
    def test_genuine_mismatch_still_uses_reviewer_relay(
        self,
        mock_stdin,
        mock_resolve,
        mock_get_override,
        mock_match_command,
        mock_load_rules,
        mock_load_config,
        tmp_path,
    ):
        """Regression: a genuine non-empty BLOCKED response must still take
        the pre-existing reviewer-relay path (locked already by
        tests/test_provenance_wiring.py; re-asserted here alongside the new
        zero-survivor path for the same gate). DB is isolated to a tmp path
        like every other hook-level test in this file — record_blockage()/
        record_governance_event() must never touch the real
        ~/.claude-pace-maker/usage.db from a test run."""
        db_path = str(tmp_path / "usage.db")
        database.initialize_database(db_path)

        hook_data = {
            "session_id": "test-142-bash-genuine",
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

        with patch("pacemaker.hook.DEFAULT_DB_PATH", db_path):
            result = run_pre_tool_hook()

        assert result.get("decision") == "block"
        reason = result["reason"]
        assert "[pace-maker · reviewer-relay · model=codex-gpt5]" in reason
        assert "the command scope is broader than declared" in reason

        conn = sqlite3.connect(db_path)
        try:
            blockage_rows = conn.execute(
                "SELECT category FROM blockage_events"
            ).fetchall()
        finally:
            conn.close()
        assert len(blockage_rows) == 1
        assert blockage_rows[0][0] == "intent_validation_dangerbash"
