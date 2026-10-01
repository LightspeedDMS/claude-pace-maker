"""
Story #155 -- hook-level behaviour of the tool-first declaration path.

Drives the REAL ``run_pre_tool_hook`` (Write/Edit gate) and ``run_hook``
(PostToolUse recorder) against the real SQLite declaration store (tmp path
from the conftest guard) and, for the transcript-fallback cases, real
transcript files. The ONLY thing mocked is the external reviewer LLM call, at
the namespace Stage 2 imports it from (``pacemaker.inference.
resolve_and_call_with_reviewer``) -- per this project's CLAUDE.md.

The transcript anchor is replaced by a spy that FAILS THE TEST if consulted,
wherever the story says the anchor wait must be skipped entirely (AC4).
"""

import contextlib
import json
import os
from typing import List
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("PACEMAKER_TEST_MODE", "1")

# The Harness and its config/anchor stubs live in tests/declare_intent_harness.py
# (shared with the Bug #159 tests).
from declare_intent_harness import (  # noqa: E402,F401
    APPROVED,
    SESSION,
    USER_TOOL,
    Harness,
    _anchor_must_not_run,
    _anchor_not_found,
    _config,
)


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


def _approved(result: dict) -> bool:
    return result.get("decision") != "block"


# ---------------------------------------------------------------------------
# AC3 -- PostToolUse recording through the real run_hook()
# ---------------------------------------------------------------------------


class TestPostToolUseRecording:
    def _run_post_tool_use(
        self, tmp_path, tool_name, tool_input, config, agent_id=None
    ):
        import pacemaker.hook as hook_mod

        payload = {
            "session_id": SESSION,
            "cwd": str(tmp_path),
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_response": "ok",
            "transcript_path": str(tmp_path / "t.jsonl"),
        }
        if agent_id:
            payload["agent_id"] = agent_id
        state = {
            "session_id": SESSION,
            "tool_execution_count": 0,
            "subagent_counter": 0,
            "in_subagent": False,
        }
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch("pacemaker.hook.load_config", return_value=config)
            )
            stack.enter_context(patch("pacemaker.hook.load_state", return_value=state))
            stack.enter_context(patch("pacemaker.hook.save_state"))
            stack.enter_context(
                patch(
                    "pacemaker.hook.pacing_engine.run_pacing_check",
                    return_value=MagicMock(should_delay=False, delay_seconds=0),
                )
            )
            stack.enter_context(
                patch(
                    "sys.stdin",
                    MagicMock(read=MagicMock(return_value=json.dumps(payload))),
                )
            )
            stack.enter_context(patch("sys.stdout", MagicMock()))
            return hook_mod.run_hook()

    def test_declare_call_is_stored_keyed_by_session_agent_and_path(self, h, tmp_path):
        self._run_post_tool_use(
            tmp_path,
            USER_TOOL,
            {"file_path": "src/a.py", "change": "c", "goal": "g", "test_coverage": "t"},
            _config(),
            agent_id="agent-7",
        )
        (row,) = h.store_rows("declarations")
        assert row["session_id"] == SESSION
        assert row["agent_key"] == "agent-7"
        assert row["file_path"] == os.path.realpath(str(tmp_path / "src" / "a.py"))
        assert row["change"] == "c"

    def test_main_thread_declaration_uses_main_key(self, h, tmp_path):
        self._run_post_tool_use(
            tmp_path,
            USER_TOOL,
            {"file_path": "/x/a.py", "change": "c", "goal": "g"},
            _config(),
        )
        assert h.store_rows("declarations")[0]["agent_key"] == "main"

    def test_plugin_tool_name_is_recorded_too(self, h, tmp_path):
        self._run_post_tool_use(
            tmp_path,
            "mcp__plugin_claude-pace-maker_pace-maker__declare_intent",
            {"file_path": "/x/a.py", "change": "c", "goal": "g"},
            _config(),
        )
        assert len(h.store_rows("declarations")) == 1

    def test_unrelated_tool_records_nothing(self, h, tmp_path):
        self._run_post_tool_use(tmp_path, "Read", {"file_path": "/x/a.py"}, _config())
        assert h.store_rows("declarations") == []

    def test_kill_switch_records_nothing(self, h, tmp_path):
        self._run_post_tool_use(
            tmp_path,
            USER_TOOL,
            {"file_path": "/x/a.py", "change": "c", "goal": "g"},
            _config(intent_declaration_tool_enabled=False),
        )
        assert h.store_rows("declarations") == []


# ---------------------------------------------------------------------------
# AC4 / AC9 -- declaration validates without the anchor; Stage 2 gets the intent
# ---------------------------------------------------------------------------


class TestDeclarationSkipsTranscriptAnchor:
    def test_declared_write_is_validated_without_consulting_the_anchor(self, h):
        assert h.declare(h.core_file) is True
        result = h.run("Write", h.core_file)
        assert _approved(result), result
        assert len(h.reviewer_prompts) == 1

    def test_declaration_is_consumed(self, h):
        h.declare(h.core_file)
        h.run("Write", h.core_file)
        assert h.store_rows("declarations") == []

    def test_declared_edit_is_validated_the_same_way(self, h):
        h.declare(h.core_file)
        assert _approved(h.run("Edit", h.core_file))

    def test_stage_two_receives_declared_change_and_goal_as_the_intent(self, h):
        h.declare(
            h.core_file,
            change="rename helper to compute_total",
            goal="match the public API naming",
        )
        h.run("Write", h.core_file)
        (prompt,) = h.reviewer_prompts
        assert "rename helper to compute_total" in prompt
        assert "match the public API naming" in prompt
        assert "INTENT:" in prompt

    def test_relative_declared_path_matches_absolute_write(self, h):
        h.declare("src/a.py")  # resolved against the payload cwd (proj root)
        assert _approved(h.run("Write", h.core_file))

    def test_subagent_declaration_is_validated_for_that_subagent_only(self, h):
        h.declare(h.core_file, agent_id="agent-9")
        # The main thread has no declaration of its own: transcript path.
        result = h.run("Write", h.core_file, anchor=_anchor_not_found)
        assert result["decision"] == "block"
        assert "transcript timing race" in result["reason"]
        # The subagent's own declaration is still there and works.
        assert _approved(h.run("Write", h.core_file, agent_id="agent-9"))


# ---------------------------------------------------------------------------
# AC9 -- TDD enforcement and Stage 2 run exactly as for a text declaration
# ---------------------------------------------------------------------------


class TestStageOneAndTwoStillApply:
    def test_core_path_declared_without_test_coverage_blocks_as_no_tdd(self, h):
        h.declare(h.core_file, test_coverage="")
        result = h.run("Write", h.core_file)
        assert result["decision"] == "block"
        assert "TDD Required" in result["reason"]
        assert h.reviewer_prompts == []
        assert [b["category"] for b in h.blockages()] == ["intent_validation_tdd"]

    @pytest.mark.parametrize(
        "goal",
        ["keep it covered by existing tests", "fix failing test: test_tabs"],
    )
    def test_tdd_wording_in_the_goal_does_not_satisfy_tdd(self, h, goal):
        """M2: only a non-blank structured test_coverage satisfies TDD."""
        h.declare(h.core_file, goal=goal, test_coverage="")
        result = h.run("Write", h.core_file)
        assert result["decision"] == "block"
        assert "TDD Required" in result["reason"]
        assert h.reviewer_prompts == []

    def test_version_bump_wording_does_not_exempt_a_core_tool_intent(self, h):
        path = str(h.root / "src" / "version_helper_2.py")
        h.declare(path, change="update the helper", test_coverage="")
        result = h.run("Write", path)
        assert result["decision"] == "block"
        assert "TDD Required" in result["reason"]

    def test_chain_reuse_keeps_the_structured_rule(self, h):
        h.declare(h.core_file, test_coverage="tests/test_a.py - test_x")
        assert _approved(h.run("Edit", h.core_file))
        assert _approved(h.run("Edit", h.core_file, new_string="x = 2"))

    def test_core_path_with_test_coverage_proceeds_to_stage_two(self, h):
        h.declare(h.core_file, test_coverage="tests/test_a.py - test_x")
        assert _approved(h.run("Write", h.core_file))
        assert len(h.reviewer_prompts) == 1

    def test_non_core_path_needs_no_test_coverage(self, h):
        h.declare(h.noncore_file, test_coverage="")
        assert _approved(h.run("Write", h.noncore_file))

    def test_stage_two_rejection_blocks_with_reviewer_feedback(self, h):
        h.declare(h.core_file)
        result = h.run(
            "Write",
            h.core_file,
            reviewer_response=("BLOCKED: code does not match intent", "codex-gpt5"),
        )
        assert result["decision"] == "block"
        assert "code does not match intent" in result["reason"]


# ---------------------------------------------------------------------------
# AC5 / AC6 -- chains
# ---------------------------------------------------------------------------


class TestChains:
    def test_approved_edit_chains_consecutive_edits_of_the_same_file(self, h):
        h.declare(h.core_file)
        assert _approved(h.run("Edit", h.core_file))
        # No new declaration, anchor forbidden: the chain validates it.
        assert _approved(h.run("Edit", h.core_file, new_string="x = 2"))
        assert _approved(h.run("Write", h.core_file))
        assert len(h.reviewer_prompts) == 3
        assert len(h.store_rows("chains")) == 1

    def test_chain_reuse_feeds_the_same_intent_to_stage_two(self, h):
        h.declare(h.core_file, change="add the retry loop", goal="survive flakes")
        h.run("Edit", h.core_file)
        h.run("Edit", h.core_file, new_string="x = 2")
        assert all("add the retry loop" in p for p in h.reviewer_prompts)

    def test_other_file_deletes_chain_and_returns_to_transcript_path(self, h):
        h.declare(h.core_file)
        h.run("Edit", h.core_file)
        other = h.run("Edit", h.core_file_b, anchor=_anchor_not_found)
        assert other["decision"] == "block"  # transcript path, no declaration
        assert h.store_rows("chains") == []

    def test_returning_to_first_file_needs_a_new_declaration(self, h):
        h.declare(h.core_file)
        h.run("Edit", h.core_file)
        h.run("Edit", h.core_file_b, anchor=_anchor_not_found)
        back = h.run("Edit", h.core_file, anchor=_anchor_not_found)
        assert back["decision"] == "block"
        h.declare(h.core_file)
        assert _approved(h.run("Edit", h.core_file))

    def test_rejected_edit_deletes_the_chain(self, h):
        h.declare(h.core_file)
        h.run("Edit", h.core_file)  # approved -> chain
        rejected = h.run(
            "Edit",
            h.core_file,
            new_string="x = 3",
            reviewer_response=("BLOCKED: nope", "codex-gpt5"),
        )
        assert rejected["decision"] == "block"
        assert h.store_rows("chains") == []
        retry = h.run("Edit", h.core_file, new_string="x = 3", anchor=_anchor_not_found)
        assert retry["decision"] == "block"
        assert "transcript timing race" in retry["reason"]

    def test_rejected_declaration_leaves_no_chain_and_needs_redeclaration(self, h):
        h.declare(h.core_file)
        h.run(
            "Edit",
            h.core_file,
            reviewer_response=("BLOCKED: nope", "codex-gpt5"),
        )
        assert h.store_rows("chains") == []
        assert h.store_rows("declarations") == []

    def test_stage_one_rejection_deletes_the_chain_too(self, h):
        h.declare(h.core_file)
        h.run("Edit", h.core_file)  # chain exists
        # A fresh declaration WITHOUT test coverage for the same core file:
        h.declare(h.core_file, test_coverage="")
        blocked = h.run("Edit", h.core_file)
        assert blocked["decision"] == "block"
        assert h.store_rows("chains") == []

    def test_reviewer_unavailable_deletes_the_chain(self, h):
        h.declare(h.core_file)
        h.run("Edit", h.core_file)
        h.run("Edit", h.core_file, reviewer_response=("", "unknown"))
        assert h.store_rows("chains") == []

    def test_chains_are_per_agent(self, h):
        h.declare(h.core_file, agent_id="agent-1")
        h.run("Edit", h.core_file, agent_id="agent-1")
        main = h.run("Edit", h.core_file, anchor=_anchor_not_found)
        assert main["decision"] == "block"
        assert _approved(h.run("Edit", h.core_file, agent_id="agent-1"))


# ---------------------------------------------------------------------------
# AC8 -- transcript fallback unchanged, plus the same-message declare call
# ---------------------------------------------------------------------------


def _asst(request_id: str, *blocks: dict) -> dict:
    return {
        "requestId": request_id,
        "message": {"role": "assistant", "content": list(blocks)},
    }


def _text(t: str) -> dict:
    return {"type": "text", "text": t}


def _tool_use(name: str, tool_input: dict, tool_id: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


def _write_transcript(path: str, entries: List[dict]) -> None:
    with open(path, "w") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")


class TestTranscriptFallback:
    def test_no_declaration_no_chain_runs_the_existing_transcript_path(self, h):
        """The anchor IS consulted when nothing was declared."""
        calls: List[int] = []

        def anchor(*args, **kwargs):
            calls.append(1)
            return _anchor_not_found(*args, **kwargs)

        result = h.run("Write", h.noncore_file, anchor=anchor)
        assert calls == [1]
        assert result["decision"] == "block"
        assert [b["category"] for b in h.blockages()] == ["intent_validation_deferred"]

    def test_visible_intent_text_in_transcript_still_validates(self, h):
        content = "x = 1\n"
        _write_transcript(
            h.transcript,
            [
                _asst(
                    "r1",
                    _text(f"INTENT: write n.py to set x, goal: tidy. {h.noncore_file}"),
                    _tool_use(
                        "Write",
                        {"file_path": h.noncore_file, "content": content},
                        "toolu_1",
                    ),
                )
            ],
        )
        result = h.run("Write", h.noncore_file, anchor=None, content=content)
        assert _approved(result), result

    def test_same_message_declare_tool_use_is_accepted(self, h):
        """AC8: no stored declaration, no INTENT text -- but the anchored turn
        itself carries a declare_intent tool_use naming the file."""
        content = "x = 1\n"
        _write_transcript(
            h.transcript,
            [
                _asst(
                    "r1",
                    _tool_use(
                        USER_TOOL,
                        {
                            "file_path": h.noncore_file,
                            "change": "set x to one",
                            "goal": "tidy the scratch module",
                        },
                        "toolu_decl",
                    ),
                    _tool_use(
                        "Write",
                        {"file_path": h.noncore_file, "content": content},
                        "toolu_write",
                    ),
                )
            ],
        )
        result = h.run("Write", h.noncore_file, anchor=None, content=content)
        assert _approved(result), result
        (prompt,) = h.reviewer_prompts
        assert "set x to one" in prompt
        assert "tidy the scratch module" in prompt

    def test_same_message_declare_for_another_file_is_not_accepted(self, h):
        content = "x = 1\n"
        _write_transcript(
            h.transcript,
            [
                _asst(
                    "r1",
                    _tool_use(
                        USER_TOOL,
                        {
                            "file_path": "/elsewhere/other.py",
                            "change": "c",
                            "goal": "g",
                        },
                        "toolu_decl",
                    ),
                    _tool_use(
                        "Write",
                        {"file_path": h.noncore_file, "content": content},
                        "toolu_write",
                    ),
                )
            ],
        )
        result = h.run("Write", h.noncore_file, anchor=None, content=content)
        assert result["decision"] == "block"
        assert h.reviewer_prompts == []

    def test_same_message_core_declaration_without_coverage_blocks_as_no_tdd(self, h):
        content = "x = 1\n"
        _write_transcript(
            h.transcript,
            [
                _asst(
                    "r1",
                    _tool_use(
                        USER_TOOL,
                        {"file_path": h.core_file, "change": "c", "goal": "g"},
                        "toolu_decl",
                    ),
                    _tool_use(
                        "Write",
                        {"file_path": h.core_file, "content": content},
                        "toolu_write",
                    ),
                )
            ],
        )
        result = h.run("Write", h.core_file, anchor=None, content=content)
        assert result["decision"] == "block"
        assert "TDD Required" in result["reason"]


# ---------------------------------------------------------------------------
# AC10 -- kill switch restores today's behaviour exactly
# ---------------------------------------------------------------------------


class TestKillSwitch:
    def test_stored_declaration_is_ignored_and_transcript_path_runs(self, h):
        h.declare(h.core_file)  # recorded while enabled
        off = _config(intent_declaration_tool_enabled=False)
        result = h.run("Write", h.core_file, anchor=_anchor_not_found, config=off)
        assert result["decision"] == "block"
        assert "transcript timing race" in result["reason"]
        assert len(h.store_rows("declarations")) == 1  # never consumed

    def test_no_chain_is_created_or_deleted_when_off(self, h):
        off = _config(intent_declaration_tool_enabled=False)
        h.run("Write", h.noncore_file, anchor=_anchor_not_found, config=off)
        assert h.store_rows("chains") == []

    def test_block_message_has_no_declare_intent_hint_when_off(self, h):
        off = _config(intent_declaration_tool_enabled=False)
        _write_transcript(
            h.transcript,
            [
                _asst(
                    "r1",
                    _tool_use(
                        "Write",
                        {"file_path": h.noncore_file, "content": "x = 1\n"},
                        "t1",
                    ),
                )
            ],
        )
        result = h.run("Write", h.noncore_file, anchor=None, config=off)
        assert result["decision"] == "block"
        assert "declare_intent" not in result["reason"]


# ---------------------------------------------------------------------------
# Telemetry -- intent_source in blockage details, log_info path record
# ---------------------------------------------------------------------------


class TestTelemetry:
    def test_declaration_sourced_block_records_intent_source(self, h):
        h.declare(h.core_file)
        h.run("Write", h.core_file, reviewer_response=("BLOCKED: no", "codex-gpt5"))
        (blockage,) = h.blockages()
        assert blockage["details"]["intent_source"] == "declare_intent"

    def test_chain_sourced_block_records_chain_intent_source(self, h):
        h.declare(h.core_file)
        h.run("Edit", h.core_file)
        h.run(
            "Edit",
            h.core_file,
            new_string="x = 2",
            reviewer_response=("BLOCKED: no", "codex-gpt5"),
        )
        (blockage,) = h.blockages()
        assert blockage["details"]["intent_source"] == "declare_intent_chain"

    def test_transcript_sourced_block_has_no_declare_intent_source(self, h):
        h.run("Write", h.noncore_file, anchor=_anchor_not_found)
        (blockage,) = h.blockages()
        assert "intent_source" not in blockage["details"]

    def test_path_used_is_logged_at_info_level(self, h):
        h.declare(h.core_file)
        with patch("pacemaker.hook.log_info") as log_info:
            h.run("Write", h.core_file)
        messages = " | ".join(
            str(c.args[1]) for c in log_info.call_args_list if len(c.args) > 1
        )
        assert "declare_intent" in messages
        assert h.core_file in messages


# ---------------------------------------------------------------------------
# AC11 -- block messages prefer the tool, keep the INTENT: fallback
# ---------------------------------------------------------------------------


class TestBlockMessagesPreferTheTool:
    def _block(self, h, file_path):
        _write_transcript(
            h.transcript,
            [
                _asst(
                    "r1",
                    _tool_use(
                        "Write", {"file_path": file_path, "content": "x = 1\n"}, "t1"
                    ),
                )
            ],
        )
        return h.run("Write", file_path, anchor=None)

    def test_stage_one_no_block_mentions_declare_intent_and_keeps_intent_line(self, h):
        result = self._block(h, h.noncore_file)
        assert result["decision"] == "block"
        reason = result["reason"]
        assert "declare_intent" in reason
        assert h.noncore_file in reason
        assert "INTENT:" in reason  # the existing fallback instructions

    def test_stage_one_no_tdd_block_mentions_declare_intent(self, h):
        _write_transcript(
            h.transcript,
            [
                _asst(
                    "r1",
                    _text(f"INTENT: write a.py ({h.core_file}) to set x, goal: tidy."),
                    _tool_use(
                        "Write", {"file_path": h.core_file, "content": "x = 1\n"}, "t1"
                    ),
                )
            ],
        )
        result = h.run("Write", h.core_file, anchor=None)
        assert result["decision"] == "block"
        assert "TDD Required" in result["reason"]
        assert "declare_intent" in result["reason"]

    def test_no_visible_text_notice_block_mentions_declare_intent(self, h):
        result = self._block(h, h.noncore_file)
        assert "Your reasoning is invisible to the validator" in result["reason"]
        assert "declare_intent" in result["reason"]


# ---------------------------------------------------------------------------
# Interaction with the #151 reasoning-summary exception (Opus 5.5)
# ---------------------------------------------------------------------------


class TestFailClosedHandlerEndsTheChain:
    """L2: an unexpected internal error is a fail-CLOSED block, and (like any
    rejection) it must end the agent's chain -- guarded so it can never raise."""

    def _boom(self):
        return patch(
            "pacemaker.intent_validator.validate_intent_and_code",
            side_effect=RuntimeError("boom"),
        )

    def test_internal_error_deletes_the_chain_of_a_chain_sourced_edit(self, h):
        h.declare(h.core_file)
        assert _approved(h.run("Edit", h.core_file))  # builds the chain
        assert len(h.store_rows("chains")) == 1
        with self._boom():
            result = h.run("Edit", h.core_file, new_string="x = 2")
        assert result["decision"] == "block"
        assert h.store_rows("chains") == []

    def test_internal_error_after_a_declaration_leaves_no_chain(self, h):
        h.declare(h.core_file)
        with self._boom():
            result = h.run("Edit", h.core_file)
        assert result["decision"] == "block"
        assert h.store_rows("chains") == []
        assert h.store_rows("declarations") == []  # consumed, as for any attempt

    def test_a_failing_record_outcome_cannot_break_the_fail_closed_block(self, h):
        h.declare(h.core_file)
        with (
            self._boom(),
            patch(
                "pacemaker.hook.declaration_gate.record_outcome",
                side_effect=RuntimeError("store exploded"),
            ),
        ):
            result = h.run("Edit", h.core_file)
        assert result["decision"] == "block"
        assert "boom" in result["reason"] or "error" in result["reason"].lower()

    def test_internal_error_without_a_tool_intent_still_just_blocks(self, h):
        with self._boom():
            result = h.run("Write", h.noncore_file, anchor=_anchor_not_found)
        assert result["decision"] == "block"


class TestExceptionModelDeclarationTakesTheStrictPath:
    def _opus_turn(self, h, file_path, *, with_declare):
        blocks = [_text("Now the edit.")]  # visible text, NO INTENT: marker
        if with_declare:
            blocks.append(
                _tool_use(
                    USER_TOOL,
                    {
                        "file_path": file_path,
                        "change": "set x to one",
                        "goal": "tidy the module",
                    },
                    "toolu_decl",
                )
            )
        blocks.append(
            _tool_use(
                "Write", {"file_path": file_path, "content": "x = 1\n"}, "toolu_w"
            )
        )
        entry = _asst("r1", *blocks)
        entry["message"]["model"] = "claude-opus-5-5"
        _write_transcript(h.transcript, [entry])

    def test_same_message_declaration_beats_the_relaxed_summary_path(self, h):
        """Without the declaration this Opus turn takes the #151 RELAXED path
        (visible text, no marker). With one, the declaration is the intent:
        the strict path runs and the result is tagged declare_intent."""
        self._opus_turn(h, h.noncore_file, with_declare=True)
        result = h.run(
            "Write",
            h.noncore_file,
            anchor=None,
            reviewer_response=("BLOCKED: no", "codex-gpt5"),
        )
        assert result["decision"] == "block"
        (prompt,) = h.reviewer_prompts
        assert "set x to one" in prompt
        assert "auto-summarized" not in prompt  # relaxed template not used
        (blockage,) = h.blockages()
        assert blockage["details"]["intent_source"] == "declare_intent"

    def test_without_a_declaration_the_relaxed_path_still_applies(self, h):
        self._opus_turn(h, h.noncore_file, with_declare=False)
        h.run("Write", h.noncore_file, anchor=None)
        (prompt,) = h.reviewer_prompts
        assert "auto-summarized" in prompt

    def test_stored_declaration_also_wins_over_an_opus_turn(self, h):
        self._opus_turn(h, h.noncore_file, with_declare=False)
        h.declare(h.noncore_file, test_coverage="")
        result = h.run("Write", h.noncore_file)  # anchor forbidden
        assert _approved(result), result
        assert "auto-summarized" not in h.reviewer_prompts[0]
