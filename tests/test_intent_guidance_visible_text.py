#!/usr/bin/env python3
"""
Regression tests for the "thinking-only INTENT declaration" guidance fix.

Problem (evidence): agents were repeatedly blocked for missing INTENT
because they wrote the INTENT: declaration inside their reasoning/thinking
and emitted no visible response text before the tool call. The
SessionStart/SubagentStart guidance (prompts/session_start/
intent_validation_guidance.md, loaded via
pacemaker.hook.display_intent_validation_guidance, shared by both hooks)
never told the agent the declaration must be VISIBLE response text rather
than reasoning. These tests drive the real loader/hook entry points (same
pattern as tests/test_provenance_wiring.py) and assert the new rule is
present in both the SessionStart stdout guidance and the SubagentStart
additionalContext JSON payload.
"""

import json
from unittest.mock import patch

from pacemaker.hook import display_intent_validation_guidance

# Distinctive substring of the new rule. Kept loose enough to survive minor
# wording edits but specific enough to only match the new rule, not the
# pre-existing (and differently scoped, post-block) THINKING_ONLY_NOTICE.
VISIBLE_TEXT_MARKER = "VISIBLE TEXT ONLY"


class TestGuidanceLoaderIncludesVisibleTextRule:
    """Direct loader test — the function the hook actually calls."""

    def test_guidance_contains_visible_text_marker(self):
        guidance = display_intent_validation_guidance()
        assert VISIBLE_TEXT_MARKER in guidance

    def test_guidance_explains_thinking_is_not_seen(self):
        guidance = display_intent_validation_guidance()
        assert "thinking" in guidance.lower()
        assert "reasoning" in guidance.lower()

    def test_guidance_still_contains_pre_existing_content(self):
        """Guard against accidentally clobbering unrelated guidance text."""
        guidance = display_intent_validation_guidance()
        assert "INTENT VALIDATION ENABLED" in guidance
        assert "TDD ENFORCEMENT" in guidance
        assert "Declare EXACTLY these 3 components" in guidance
        assert "Senior Coding Nanny" in guidance


class TestSessionStartEmitsVisibleTextRule:
    def test_session_start_stdout_contains_visible_text_marker(self, tmp_path, capsys):
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

        captured = capsys.readouterr()
        assert VISIBLE_TEXT_MARKER in captured.out


class TestSubagentStartEmitsVisibleTextRule:
    def test_subagent_start_additional_context_contains_visible_text_marker(
        self, tmp_path, capsys
    ):
        from pacemaker.hook import run_subagent_start_hook

        config_path = tmp_path / "config.json"
        state_path = tmp_path / "state.json"
        transcript_path = tmp_path / "main-session-202.jsonl"
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
            "session_id": "main-session-202",
            "agent_id": "agent-202",
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
        assert VISIBLE_TEXT_MARKER in context
