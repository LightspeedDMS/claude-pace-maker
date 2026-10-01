"""
Story #155 -- hook-side declaration recorder / resolver / outcome handling.

``pacemaker.intent_declarations.gate`` is what run_hook() (PostToolUse) and
run_pre_tool_hook() (Write/Edit gate) call. Real SQLite (tmp path from the
conftest guard env var), real Stage-1 regex check; nothing is mocked.
"""

import sqlite3

import pytest

from pacemaker.constants import DECLARE_INTENT_TOOL_NAMES
from pacemaker.intent_declarations import fields, gate
from pacemaker.intent_declarations.store import (
    IntentDeclarationStore,
    SOURCE_CHAIN,
    SOURCE_DECLARATION,
    StoredIntent,
    resolve_db_path,
)
from pacemaker.intent_validator import _regex_stage1_check

PLUGIN_TOOL = "mcp__plugin_claude-pace-maker_pace-maker__declare_intent"
USER_TOOL = "mcp__pace-maker__declare_intent"
ENABLED = {"intent_declaration_tool_enabled": True}
DISABLED = {"intent_declaration_tool_enabled": False}


def _declare_payload(
    tool_name=USER_TOOL,
    file_path="/w/src/a.py",
    change="add f",
    goal="fix bug",
    test_coverage="tests/test_a.py - test_f",
    session_id="s1",
    agent_id=None,
    cwd="/w",
):
    tool_input = {"file_path": file_path, "change": change, "goal": goal}
    if test_coverage is not None:
        tool_input["test_coverage"] = test_coverage
    payload = {
        "session_id": session_id,
        "cwd": cwd,
        "tool_name": tool_name,
        "tool_input": tool_input,
    }
    if agent_id:
        payload["agent_id"] = agent_id
    return payload


def _edit_payload(file_path="/w/src/a.py", session_id="s1", agent_id=None, cwd="/w"):
    payload = {
        "session_id": session_id,
        "cwd": cwd,
        "tool_name": "Edit",
        "tool_input": {"file_path": file_path},
    }
    if agent_id:
        payload["agent_id"] = agent_id
    return payload


def _rows(table):
    """Rows of ``table``; a store that was never touched has no tables yet,
    which is the same observable state as an empty one."""
    conn = sqlite3.connect(resolve_db_path())
    try:
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
        except sqlite3.OperationalError as exc:
            assert "no such table" in str(exc)
            return []
    finally:
        conn.close()


class TestEnabledFlag:
    def test_default_config_enables_the_tool_path(self):
        from pacemaker.constants import DEFAULT_CONFIG

        assert DEFAULT_CONFIG["intent_declaration_tool_enabled"] is True
        assert gate.intent_declaration_tool_enabled({}) is True

    def test_false_disables(self):
        assert gate.intent_declaration_tool_enabled(DISABLED) is False

    @pytest.mark.parametrize("bad", [None, 0, "false", "no", []])
    def test_anything_but_true_disables_when_key_present(self, bad):
        assert (
            gate.intent_declaration_tool_enabled(
                {"intent_declaration_tool_enabled": bad}
            )
            is False
        )


class TestRecorder:
    @pytest.mark.parametrize("tool_name", sorted(DECLARE_INTENT_TOOL_NAMES))
    def test_both_tool_names_are_recorded(self, tool_name):
        stored = gate.record_declare_intent(
            _declare_payload(tool_name=tool_name), ENABLED
        )
        assert stored is True
        (row,) = _rows("declarations")
        assert row["session_id"] == "s1"
        assert row["agent_key"] == "main"
        assert row["file_path"] == "/w/src/a.py"
        assert row["change"] == "add f"
        assert row["goal"] == "fix bug"
        assert row["test_coverage"] == "tests/test_a.py - test_f"

    def test_subagent_declaration_is_keyed_by_agent_id(self):
        gate.record_declare_intent(_declare_payload(agent_id="agent-42"), ENABLED)
        (row,) = _rows("declarations")
        assert row["agent_key"] == "agent-42"

    def test_relative_path_resolved_against_payload_cwd(self, tmp_path):
        gate.record_declare_intent(
            _declare_payload(file_path="src/a.py", cwd=str(tmp_path)), ENABLED
        )
        (row,) = _rows("declarations")
        assert row["file_path"] == str(tmp_path / "src" / "a.py")

    def test_fields_are_stripped_and_test_coverage_optional(self):
        gate.record_declare_intent(
            _declare_payload(change="  add f ", goal=" g\n", test_coverage=None),
            ENABLED,
        )
        (row,) = _rows("declarations")
        assert (row["change"], row["goal"], row["test_coverage"]) == (
            "add f",
            "g",
            "",
        )

    def test_non_string_test_coverage_is_ignored(self):
        payload = _declare_payload()
        payload["tool_input"]["test_coverage"] = 7
        assert gate.record_declare_intent(payload, ENABLED) is True
        assert _rows("declarations")[0]["test_coverage"] == ""

    @pytest.mark.parametrize(
        "tool", ["Write", "Edit", "Bash", "mcp__other__declare_intent"]
    )
    def test_other_tools_are_ignored(self, tool):
        assert (
            gate.record_declare_intent(_declare_payload(tool_name=tool), ENABLED)
            is False
        )
        assert _rows("declarations") == []

    @pytest.mark.parametrize("field", ["file_path", "change", "goal"])
    @pytest.mark.parametrize("bad", [None, "", "   "])
    def test_incomplete_declaration_is_not_stored(self, field, bad):
        payload = _declare_payload()
        payload["tool_input"][field] = bad
        assert gate.record_declare_intent(payload, ENABLED) is False
        assert _rows("declarations") == []

    def test_kill_switch_stores_nothing(self):
        assert gate.record_declare_intent(_declare_payload(), DISABLED) is False
        assert _rows("declarations") == []

    def test_missing_session_id_is_not_stored_and_does_not_raise(self):
        payload = _declare_payload()
        del payload["session_id"]
        assert gate.record_declare_intent(payload, ENABLED) is False

    def test_non_dict_tool_input_does_not_raise(self):
        payload = _declare_payload()
        payload["tool_input"] = "garbage"
        assert gate.record_declare_intent(payload, ENABLED) is False

    def test_store_failure_never_raises_into_the_hook(self, tmp_path, monkeypatch):
        # A directory where the DB file should be: sqlite cannot open it.
        broken = tmp_path / "is_a_dir"
        broken.mkdir()
        monkeypatch.setenv("PACEMAKER_INTENT_DECLARATIONS_PATH", str(broken))
        assert gate.record_declare_intent(_declare_payload(), ENABLED) is False


class TestMessageRendering:
    def test_message_shape_with_test_coverage(self):
        intent = StoredIntent("/w/src/a.py", "add f", "fix bug", "t.py - test_f")
        assert gate.render_intent_message(intent, "/w/src/a.py") == (
            "INTENT: add f in /w/src/a.py — goal: fix bug\n"
            "Test coverage: t.py - test_f"
        )

    def test_message_without_test_coverage_has_no_coverage_line(self):
        intent = StoredIntent("/w/src/a.py", "add f", "fix bug", "")
        assert gate.render_intent_message(intent, "/w/src/a.py") == (
            "INTENT: add f in /w/src/a.py — goal: fix bug"
        )

    def test_multiline_fields_are_collapsed_to_one_line_each(self):
        intent = StoredIntent("/w/a.py", "add\n  f", "fix\r\nbug", "t.py -\n test_f")
        message = gate.render_intent_message(intent, "/w/a.py")
        assert message.splitlines() == [
            "INTENT: add f in /w/a.py — goal: fix bug",
            "Test coverage: t.py - test_f",
        ]

    def test_message_uses_the_gates_own_file_path(self):
        intent = StoredIntent("/real/a.py", "c", "g", "")
        assert "/as/given/a.py" in gate.render_intent_message(intent, "/as/given/a.py")

    def test_core_path_without_test_coverage_is_no_tdd_in_stage_1(self):
        """AC9 (first half): TDD enforcement runs exactly as for text."""
        intent = StoredIntent("/w/src/a.py", "add f", "fix bug", "")
        message = gate.render_intent_message(intent, "/w/src/a.py")
        assert _regex_stage1_check(message, "/w/src/a.py", []) == "NO_TDD"

    def test_core_path_with_test_coverage_passes_stage_1(self):
        intent = StoredIntent("/w/src/a.py", "add f", "fix bug", "t.py - test_f")
        message = gate.render_intent_message(intent, "/w/src/a.py")
        assert _regex_stage1_check(message, "/w/src/a.py", []) == "YES"

    # -- M2: for tool-sourced intents only the STRUCTURED test_coverage counts --

    @pytest.mark.parametrize(
        "goal",
        [
            "keep it covered by existing tests",
            "fix failing test: test_tabs",
            "TEST FILE: tests/test_x.py is where this is verified",
            "User permission to skip TDD: they said so",
        ],
    )
    def test_tdd_wording_in_goal_never_satisfies_tdd_for_tool_intents(self, goal):
        intent = StoredIntent("/w/src/a.py", "add f", goal, "")
        message = gate.render_intent_message(intent, "/w/src/a.py")
        # Text path (unchanged): the wording DOES satisfy the regex ...
        assert _regex_stage1_check(message, "/w/src/a.py", []) == "YES"
        # ... but a tool-sourced intent with no structured coverage is NO_TDD.
        assert (
            _regex_stage1_check(message, "/w/src/a.py", [], tool_declared_tdd=False)
            == "NO_TDD"
        )

    def test_version_bump_wording_or_path_never_exempts_a_tool_intent(self):
        path = "/w/src/version_helper_2.py"
        intent = StoredIntent(path, "update the helper", "bump coverage", "")
        message = gate.render_intent_message(intent, path)
        assert _regex_stage1_check(message, path, []) == "YES"  # text path
        assert (
            _regex_stage1_check(message, path, [], tool_declared_tdd=False) == "NO_TDD"
        )

    def test_structured_test_coverage_satisfies_tdd_for_tool_intents(self):
        intent = StoredIntent("/w/src/a.py", "add f", "g", "t.py - test_f")
        message = gate.render_intent_message(intent, "/w/src/a.py")
        assert (
            _regex_stage1_check(message, "/w/src/a.py", [], tool_declared_tdd=True)
            == "YES"
        )

    def test_tool_flag_does_not_weaken_the_other_stage_one_checks(self):
        # No INTENT: marker / file not mentioned still reject.
        assert (
            _regex_stage1_check("hello", "/w/src/a.py", [], tool_declared_tdd=True)
            == "NO"
        )
        assert (
            _regex_stage1_check(
                "INTENT: x in /w/other.py", "/w/src/a.py", [], tool_declared_tdd=True
            )
            == "NO"
        )

    def test_non_core_path_ignores_the_tool_flag(self):
        assert (
            _regex_stage1_check(
                "INTENT: x in /w/scratch/n.py",
                "/w/scratch/n.py",
                ["scratch/"],
                tool_declared_tdd=False,
            )
            == "YES"
        )

    def test_non_core_path_needs_no_test_coverage(self):
        intent = StoredIntent("/w/scratch/a.py", "add f", "fix bug", "")
        message = gate.render_intent_message(intent, "/w/scratch/a.py")
        assert _regex_stage1_check(message, "/w/scratch/a.py", ["scratch/"]) == "YES"


class TestDeclaredFieldCaps:
    """L4: declared text is capped consistently with the transcript path's
    MAX_MESSAGE_LENGTH (truncation, documented in CLAUDE.md)."""

    def test_message_cap_matches_the_transcript_readers(self):
        from pacemaker.constants import (
            DECLARATION_MAX_FIELD_CHARS,
            DECLARATION_MAX_MESSAGE_LENGTH,
        )
        from pacemaker.transcript_reader import MAX_MESSAGE_LENGTH

        assert DECLARATION_MAX_MESSAGE_LENGTH == MAX_MESSAGE_LENGTH
        assert 3 * DECLARATION_MAX_FIELD_CHARS < DECLARATION_MAX_MESSAGE_LENGTH

    def test_recorded_fields_are_truncated_to_the_field_cap(self):
        from pacemaker.constants import DECLARATION_MAX_FIELD_CHARS

        payload = _declare_payload(
            change="c" * 50000, goal="g" * 50000, test_coverage="t" * 50000
        )
        assert gate.record_declare_intent(payload, ENABLED) is True
        (row,) = _rows("declarations")
        for column in ("change", "goal", "test_coverage"):
            assert len(row[column]) == DECLARATION_MAX_FIELD_CHARS
            assert row[column].endswith("…")

    def test_short_fields_are_untouched(self):
        gate.record_declare_intent(_declare_payload(change="short"), ENABLED)
        assert _rows("declarations")[0]["change"] == "short"

    def test_rendered_message_never_exceeds_the_message_cap(self):
        from pacemaker.constants import DECLARATION_MAX_MESSAGE_LENGTH

        intent = StoredIntent("/w/" + "p" * 30000 + ".py", "c" * 9000, "g" * 9000, "")
        message = gate.render_intent_message(intent, intent.file_path)
        assert len(message) == DECLARATION_MAX_MESSAGE_LENGTH
        assert message.startswith("INTENT: ")

    def test_same_message_declaration_fields_are_truncated(self):
        from pacemaker.constants import DECLARATION_MAX_FIELD_CHARS

        candidates = [{"file_path": "/w/a.py", "change": "c" * 9000, "goal": "g"}]
        declared = gate.same_message_declared_intent(candidates, "/w/a.py", "/w")
        assert len(declared.intent.change) == DECLARATION_MAX_FIELD_CHARS


class TestResolver:
    def test_recorded_declaration_resolves_and_is_consumed(self):
        gate.record_declare_intent(_declare_payload(), ENABLED)
        declared = gate.resolve_declared_intent(_edit_payload(), "/w/src/a.py", ENABLED)
        assert declared is not None
        assert declared.source == SOURCE_DECLARATION
        assert declared.intent.change == "add f"
        assert _rows("declarations") == []
        assert (
            gate.resolve_declared_intent(_edit_payload(), "/w/src/a.py", ENABLED)
            is None
        )

    def test_declared_relative_path_matches_absolute_edit(self, tmp_path):
        gate.record_declare_intent(
            _declare_payload(file_path="src/a.py", cwd=str(tmp_path)), ENABLED
        )
        edit = _edit_payload(
            file_path=str(tmp_path / "src" / "a.py"), cwd=str(tmp_path)
        )
        declared = gate.resolve_declared_intent(
            edit, str(tmp_path / "src" / "a.py"), ENABLED
        )
        assert declared is not None

    def test_subagent_cannot_consume_main_threads_declaration(self):
        gate.record_declare_intent(_declare_payload(), ENABLED)
        assert (
            gate.resolve_declared_intent(
                _edit_payload(agent_id="agent-1"), "/w/src/a.py", ENABLED
            )
            is None
        )
        assert len(_rows("declarations")) == 1

    def test_kill_switch_skips_lookup_and_leaves_rows_alone(self):
        store = IntentDeclarationStore(resolve_db_path())
        store.record("s1", "main", "/w/src/a.py", "c", "g", "")
        assert (
            gate.resolve_declared_intent(_edit_payload(), "/w/src/a.py", DISABLED)
            is None
        )
        assert len(_rows("declarations")) == 1

    def test_chain_reuse_then_different_file_deletes_chain(self):
        gate.record_declare_intent(_declare_payload(), ENABLED)
        first = gate.resolve_declared_intent(_edit_payload(), "/w/src/a.py", ENABLED)
        gate.record_outcome(
            _edit_payload(), "/w/src/a.py", first, approved=True, config=ENABLED
        )
        again = gate.resolve_declared_intent(_edit_payload(), "/w/src/a.py", ENABLED)
        assert again.source == SOURCE_CHAIN
        other = gate.resolve_declared_intent(
            _edit_payload(file_path="/w/src/b.py"), "/w/src/b.py", ENABLED
        )
        assert other is None
        assert _rows("chains") == []
        # Returning to the first file needs a NEW declaration.
        assert (
            gate.resolve_declared_intent(_edit_payload(), "/w/src/a.py", ENABLED)
            is None
        )

    def test_missing_session_id_resolves_to_none(self):
        payload = _edit_payload()
        del payload["session_id"]
        assert gate.resolve_declared_intent(payload, "/w/src/a.py", ENABLED) is None

    def test_store_failure_resolves_to_none_for_transcript_fallback(
        self, tmp_path, monkeypatch
    ):
        broken = tmp_path / "is_a_dir"
        broken.mkdir()
        monkeypatch.setenv("PACEMAKER_INTENT_DECLARATIONS_PATH", str(broken))
        assert (
            gate.resolve_declared_intent(_edit_payload(), "/w/src/a.py", ENABLED)
            is None
        )


class TestOutcomeHandling:
    def _declared(self):
        gate.record_declare_intent(_declare_payload(), ENABLED)
        return gate.resolve_declared_intent(_edit_payload(), "/w/src/a.py", ENABLED)

    def test_approved_declaration_creates_chain(self):
        declared = self._declared()
        gate.record_outcome(
            _edit_payload(), "/w/src/a.py", declared, approved=True, config=ENABLED
        )
        (chain,) = _rows("chains")
        assert chain["file_path"] == "/w/src/a.py"
        assert chain["change"] == "add f"
        assert chain["agent_key"] == "main"

    def test_approved_without_tool_intent_creates_no_chain(self):
        gate.record_outcome(
            _edit_payload(), "/w/src/a.py", None, approved=True, config=ENABLED
        )
        assert _rows("chains") == []

    def test_rejected_deletes_chain_even_for_a_transcript_sourced_edit(self):
        declared = self._declared()
        gate.record_outcome(
            _edit_payload(), "/w/src/a.py", declared, approved=True, config=ENABLED
        )
        gate.record_outcome(
            _edit_payload(), "/w/src/a.py", None, approved=False, config=ENABLED
        )
        assert _rows("chains") == []
        assert (
            gate.resolve_declared_intent(_edit_payload(), "/w/src/a.py", ENABLED)
            is None
        )

    def test_rejected_chain_reuse_requires_fresh_declaration(self):
        declared = self._declared()
        gate.record_outcome(
            _edit_payload(), "/w/src/a.py", declared, approved=True, config=ENABLED
        )
        chained = gate.resolve_declared_intent(_edit_payload(), "/w/src/a.py", ENABLED)
        gate.record_outcome(
            _edit_payload(), "/w/src/a.py", chained, approved=False, config=ENABLED
        )
        assert (
            gate.resolve_declared_intent(_edit_payload(), "/w/src/a.py", ENABLED)
            is None
        )

    def test_kill_switch_makes_outcome_a_noop(self):
        declared = self._declared()
        gate.record_outcome(
            _edit_payload(), "/w/src/a.py", declared, approved=True, config=DISABLED
        )
        assert _rows("chains") == []

    def test_outcome_store_failure_never_raises(self, tmp_path, monkeypatch):
        broken = tmp_path / "is_a_dir"
        broken.mkdir()
        monkeypatch.setenv("PACEMAKER_INTENT_DECLARATIONS_PATH", str(broken))
        gate.record_outcome(
            _edit_payload(),
            "/w/src/a.py",
            gate.DeclaredIntent(
                SOURCE_DECLARATION, StoredIntent("/w/src/a.py", "c", "g", "")
            ),
            approved=True,
            config=ENABLED,
        )

    def test_subagent_chain_is_isolated_from_main(self):
        gate.record_declare_intent(_declare_payload(agent_id="agent-1"), ENABLED)
        declared = gate.resolve_declared_intent(
            _edit_payload(agent_id="agent-1"), "/w/src/a.py", ENABLED
        )
        gate.record_outcome(
            _edit_payload(agent_id="agent-1"),
            "/w/src/a.py",
            declared,
            approved=True,
            config=ENABLED,
        )
        assert (
            gate.resolve_declared_intent(_edit_payload(), "/w/src/a.py", ENABLED)
            is None
        )
        assert (
            gate.resolve_declared_intent(
                _edit_payload(agent_id="agent-1"), "/w/src/a.py", ENABLED
            ).source
            == SOURCE_CHAIN
        )


def _tool(name, tool_input, tool_id="t"):
    return {"id": tool_id, "name": name, "input": tool_input}


class TestDeclareInputsFromTools:
    def test_keeps_only_declare_calls_in_order(self):
        tools = [
            _tool("Read", {"file_path": "/x"}),
            _tool(USER_TOOL, {"file_path": "/w/a.py", "change": "1", "goal": "g"}),
            _tool("Write", {"file_path": "/w/a.py", "content": "c"}),
            _tool(PLUGIN_TOOL, {"file_path": "/w/b.py", "change": "2", "goal": "g"}),
        ]
        assert [d["change"] for d in fields.declare_inputs_from_tools(tools)] == [
            "1",
            "2",
        ]

    def test_skips_calls_whose_input_is_not_an_object(self):
        tools = [_tool(USER_TOOL, "garbage"), _tool(USER_TOOL, None)]
        assert fields.declare_inputs_from_tools(tools) == []

    def test_empty_and_garbage_tools(self):
        assert fields.declare_inputs_from_tools([]) == []
        assert fields.declare_inputs_from_tools(None) == []

    def test_transcript_reader_does_not_load_the_gate_or_the_store(self):
        """L6: transcript_reader depends on the stdlib leaf only -- never on
        the gate/store (sqlite, logger-coupled hook wiring)."""
        import subprocess
        import sys
        from pathlib import Path

        src = str(Path(__file__).resolve().parent.parent / "src")
        code = (
            "import sys; import pacemaker.transcript_reader; "
            "print('pacemaker.intent_declarations.gate' in sys.modules, "
            "'pacemaker.intent_declarations.store' in sys.modules)"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={"PYTHONPATH": src, "PATH": "/usr/bin:/bin"},
            timeout=60,
        )
        assert proc.stdout.strip() == "False False", proc.stderr


class TestTranscriptReaderSurfacesDeclareCalls:
    """The anchored turn's declare_intent tool_use inputs reach the hook via
    the additive ``anchor_declare_intents`` diagnostic (found AND stale)."""

    def _transcript(self, tmp_path, with_result):
        import json

        entries = [
            {
                "requestId": "r1",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_d",
                            "name": USER_TOOL,
                            "input": {
                                "file_path": "/w/a.py",
                                "change": "c",
                                "goal": "g",
                            },
                        },
                        {
                            "type": "tool_use",
                            "id": "toolu_w",
                            "name": "Write",
                            "input": {"file_path": "/w/a.py", "content": "x"},
                        },
                    ],
                },
            }
        ]
        if with_result:
            entries.append(
                {
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_w",
                                "content": "ok",
                            }
                        ],
                    }
                }
            )
        path = tmp_path / "t.jsonl"
        path.write_text("".join(json.dumps(e) + "\n" for e in entries))
        return str(path)

    def test_found_anchor_exposes_declare_inputs(self, tmp_path):
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        outcome = {}
        _find_turn_matching_tool_input(
            self._transcript(tmp_path, with_result=False),
            {"file_path": "/w/a.py", "content": "x"},
            "Write",
            _outcome=outcome,
        )
        assert outcome["outcome"] == "found"
        assert outcome["anchor_declare_intents"] == [
            {"file_path": "/w/a.py", "change": "c", "goal": "g"}
        ]

    def test_stale_anchor_exposes_declare_inputs(self, tmp_path):
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        outcome = {}
        _find_turn_matching_tool_input(
            self._transcript(tmp_path, with_result=True),
            {"file_path": "/w/a.py", "content": "x"},
            "Write",
            _outcome=outcome,
        )
        assert outcome["outcome"] == "stale"
        assert len(outcome["anchor_declare_intents"]) == 1

    def test_retry_loop_copies_the_field_into_diagnostics(self, tmp_path):
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        diagnostics = {}
        get_current_turn_message_for_validation(
            self._transcript(tmp_path, with_result=False),
            tool_input={"file_path": "/w/a.py", "content": "x"},
            tool_name="Write",
            _diagnostics=diagnostics,
            _max_wait_seconds=0.0,
        )
        assert len(diagnostics["anchor_declare_intents"]) == 1

    def test_turn_without_declare_calls_yields_empty_list(self, tmp_path):
        import json
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        path = tmp_path / "t2.jsonl"
        path.write_text(
            json.dumps(
                {
                    "requestId": "r1",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_w",
                                "name": "Write",
                                "input": {"file_path": "/w/a.py", "content": "x"},
                            }
                        ],
                    },
                }
            )
            + "\n"
        )
        outcome = {}
        _find_turn_matching_tool_input(
            str(path),
            {"file_path": "/w/a.py", "content": "x"},
            "Write",
            _outcome=outcome,
        )
        assert outcome["anchor_declare_intents"] == []


class TestSameMessageFallback:
    """L8: the gate-side decision (hook.py is wiring only)."""

    DIAG = {
        "anchor_declare_intents": [
            {"file_path": "/w/src/a.py", "change": "c", "goal": "g"}
        ]
    }

    def _fb(
        self, override="", diagnostics=None, config=ENABLED, file_path="/w/src/a.py"
    ):
        return gate.same_message_fallback(
            override,
            self.DIAG if diagnostics is None else diagnostics,
            file_path,
            {"cwd": "/w"},
            config,
        )

    def test_empty_override_with_matching_declare_call_is_accepted(self):
        declared = self._fb()
        assert declared.source == SOURCE_DECLARATION
        assert declared.intent.change == "c"

    def test_a_turn_with_its_own_intent_text_is_left_alone(self):
        assert self._fb(override="INTENT: x in a.py") is None

    def test_kill_switch_off_means_no_fallback(self):
        assert self._fb(config=DISABLED) is None

    def test_no_diagnostic_or_no_candidates_means_none(self):
        assert self._fb(diagnostics={}) is None
        assert self._fb(diagnostics={"anchor_declare_intents": None}) is None
        assert self._fb(diagnostics={"anchor_declare_intents": []}) is None

    def test_other_file_means_none(self):
        assert self._fb(file_path="/w/src/zzz.py") is None


class TestSameMessageDeclaration:
    def test_matching_complete_declare_call_is_accepted(self):
        candidates = [
            {
                "file_path": "/w/src/a.py",
                "change": "c",
                "goal": "g",
                "test_coverage": "t",
            }
        ]
        declared = gate.same_message_declared_intent(candidates, "/w/src/a.py", "/w")
        assert declared.source == SOURCE_DECLARATION
        assert declared.intent == StoredIntent("/w/src/a.py", "c", "g", "t")

    def test_relative_declared_path_is_normalized_against_cwd(self, tmp_path):
        candidates = [{"file_path": "src/a.py", "change": "c", "goal": "g"}]
        target = str(tmp_path / "src" / "a.py")
        declared = gate.same_message_declared_intent(candidates, target, str(tmp_path))
        assert declared is not None
        assert declared.intent.file_path == target

    def test_other_file_is_not_accepted(self):
        candidates = [{"file_path": "/w/src/b.py", "change": "c", "goal": "g"}]
        assert (
            gate.same_message_declared_intent(candidates, "/w/src/a.py", "/w") is None
        )

    def test_incomplete_call_is_not_accepted(self):
        candidates = [{"file_path": "/w/src/a.py", "change": "c", "goal": " "}]
        assert (
            gate.same_message_declared_intent(candidates, "/w/src/a.py", "/w") is None
        )

    def test_newest_matching_call_wins(self):
        candidates = [
            {"file_path": "/w/src/a.py", "change": "old", "goal": "g"},
            {"file_path": "/w/src/a.py", "change": "new", "goal": "g"},
        ]
        declared = gate.same_message_declared_intent(candidates, "/w/src/a.py", "/w")
        assert declared.intent.change == "new"

    @pytest.mark.parametrize("candidates", [None, [], "x", [None, 3, "y"]])
    def test_garbage_candidates_yield_none(self, candidates):
        assert (
            gate.same_message_declared_intent(candidates, "/w/src/a.py", "/w") is None
        )
