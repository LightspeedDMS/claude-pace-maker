"""
Bug #157 fix 2 -- ``handle_subagent_start`` must not parse the whole parent
transcript three times (``extract_task_tool_prompt``, ``extract_user_id`` and
``parse_session_metadata`` each read the full file: 1.45 s on a 52 MB parent).

The spawning Agent call is near EOF, so ``langfuse/subagent_context.py`` reads a
bounded HEAD window (legacy ``session_start`` / ``auth_profile`` entries live at
the start of a session) and a bounded TAIL window ONCE, in a single pass, and
only falls back to the old full scans for data the windows did not contain.
"""

import json
import time

import pytest

from pacemaker.langfuse import orchestrator
from pacemaker.langfuse.subagent_context import (
    SubagentStartContext,
    read_subagent_start_context,
)
from pacemaker.telemetry import jsonl_parser


def _line(entry) -> str:
    return json.dumps(entry) + "\n"


def _assistant_text(text, model=None):
    message = {"role": "assistant", "content": [{"type": "text", "text": text}]}
    if model:
        message["model"] = model
    return {"type": "assistant", "message": message}


def _agent_call(prompt, name="Agent", model="claude-opus-5-5"):
    message = {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": name,
                "input": {"subagent_type": "tdd-engineer", "prompt": prompt},
            }
        ],
    }
    if model:
        message["model"] = model
    return {"type": "assistant", "message": message}


def _write(path, entries):
    with open(path, "w") as handle:
        for entry in entries:
            handle.write(_line(entry))
    return str(path)


@pytest.fixture
def boom_full_parsers(monkeypatch):
    """The three old whole-file parsers must NOT be reached when the bounded
    windows already hold the data."""

    def boom(*args, **kwargs):
        raise AssertionError("full-transcript parse must not run")

    monkeypatch.setattr(orchestrator, "extract_task_tool_prompt", boom)
    monkeypatch.setattr(jsonl_parser, "parse_session_metadata", boom)
    monkeypatch.setattr(jsonl_parser, "extract_user_id", boom)


class TestReadSubagentStartContext:
    def test_prompt_model_and_user_from_a_small_transcript(self, tmp_path):
        path = _write(
            tmp_path / "t.jsonl",
            [
                {"type": "auth_profile", "profile": {"email": "me@example.com"}},
                _assistant_text("hi", model="claude-sonnet-5-5"),
                _agent_call("do the thing", model="claude-opus-5-5"),
            ],
        )
        ctx = read_subagent_start_context(path)
        assert ctx == SubagentStartContext(
            prompt="do the thing", model="claude-opus-5-5", user_id="me@example.com"
        )

    def test_last_agent_call_wins(self, tmp_path):
        path = _write(
            tmp_path / "t.jsonl",
            [_agent_call("first", name="Task"), _agent_call("second"), _agent_call("")],
        )
        assert read_subagent_start_context(path).prompt == "second"

    def test_task_and_agent_tool_names_both_count(self, tmp_path):
        path = _write(tmp_path / "t.jsonl", [_agent_call("via task", name="Task")])
        assert read_subagent_start_context(path).prompt == "via task"

    def test_no_agent_call_means_none_prompt(self, tmp_path):
        path = _write(tmp_path / "t.jsonl", [_assistant_text("x", model="m")])
        assert read_subagent_start_context(path).prompt is None

    def test_missing_model_is_the_old_default_unknown(self, tmp_path):
        path = _write(
            tmp_path / "t.jsonl", [{"type": "user", "message": {"content": "x"}}]
        )
        assert read_subagent_start_context(path).model == "unknown"

    def test_model_follows_parse_session_metadata_last_seen_semantics(self, tmp_path):
        path = _write(
            tmp_path / "t.jsonl",
            [
                _assistant_text("a", model="model-one"),
                {"type": "x", "model": "entry-level-model"},
                _assistant_text("b", model="model-two"),
            ],
        )
        assert read_subagent_start_context(path).model == "model-two"
        assert jsonl_parser.parse_session_metadata(path)["model"] == "model-two"

    def test_session_start_model_wins_and_stops_the_scan_like_the_old_parser(
        self, tmp_path
    ):
        entries = [
            {"type": "session_start", "model": "start-model"},
            _assistant_text("a", model="later-model"),
        ]
        path = _write(tmp_path / "t.jsonl", entries)
        assert read_subagent_start_context(path).model == "start-model"
        assert jsonl_parser.parse_session_metadata(path)["model"] == "start-model"

    def test_profile_email_variants(self, tmp_path):
        path = _write(tmp_path / "t.jsonl", [{"profile": {"email": "p@example.com"}}])
        assert read_subagent_start_context(path).user_id == "p@example.com"

    def test_no_email_in_the_transcript_is_none_here(self, tmp_path):
        path = _write(tmp_path / "t.jsonl", [_assistant_text("x", model="m")])
        assert read_subagent_start_context(path).user_id is None

    def test_missing_file_is_all_defaults_not_an_error(self, tmp_path):
        ctx = read_subagent_start_context(str(tmp_path / "nope.jsonl"))
        assert ctx == SubagentStartContext(prompt=None, model="unknown", user_id=None)

    def test_malformed_lines_are_skipped(self, tmp_path):
        path = tmp_path / "t.jsonl"
        path.write_text("{not json\n" + _line(_agent_call("ok")) + "[1,2]\n\n")
        assert read_subagent_start_context(str(path)).prompt == "ok"


class TestBoundedWindows:
    def test_big_transcript_is_answered_from_the_tail_without_full_parses(
        self, tmp_path, boom_full_parsers
    ):
        path = tmp_path / "big.jsonl"
        filler = _line(_assistant_text("x" * 4000, model="old-model"))
        with open(path, "w") as handle:
            handle.write(
                _line({"type": "auth_profile", "profile": {"email": "h@x.io"}})
            )
            for _ in range(6000):  # ~24 MB
                handle.write(filler)
            handle.write(_line(_agent_call("the spawning prompt", model="tail-model")))
        assert path.stat().st_size > 20_000_000
        start = time.monotonic()
        ctx = read_subagent_start_context(str(path))
        elapsed = time.monotonic() - start
        assert ctx.prompt == "the spawning prompt"
        assert ctx.model == "tail-model"
        assert ctx.user_id == "h@x.io"  # legacy email at the HEAD is still found
        assert elapsed < 0.5, f"took {elapsed:.3f}s"

    def test_reads_do_not_depend_on_file_size(self, tmp_path):
        small = tmp_path / "s.jsonl"
        big = tmp_path / "b.jsonl"
        _write(small, [_agent_call("p")])
        with open(big, "w") as handle:
            for _ in range(3000):
                handle.write(_line(_assistant_text("y" * 4000, model="m")))
            handle.write(_line(_agent_call("p")))
        read_subagent_start_context(str(big))  # warm
        t0 = time.monotonic()
        read_subagent_start_context(str(big))
        big_time = time.monotonic() - t0
        assert big_time < 0.3
        assert read_subagent_start_context(str(small)).prompt == "p"

    def test_partial_line_at_the_window_boundary_is_dropped_not_fatal(self, tmp_path):
        path = tmp_path / "t.jsonl"
        with open(path, "w") as handle:
            for i in range(200):
                handle.write(_line(_assistant_text(f"filler-{i}" * 20, model="m")))
            handle.write(_line(_agent_call("tail prompt")))
        ctx = read_subagent_start_context(str(path), head_bytes=512, tail_bytes=2048)
        assert ctx.prompt == "tail prompt"

    def test_prompt_outside_the_windows_falls_back_to_the_full_scan(self, tmp_path):
        path = tmp_path / "t.jsonl"
        with open(path, "w") as handle:
            handle.write(_line(_agent_call("early prompt")))
            for i in range(300):
                handle.write(_line(_assistant_text("z" * 200, model="m")))
        ctx = read_subagent_start_context(str(path), head_bytes=256, tail_bytes=2048)
        assert ctx.prompt == "early prompt"

    def test_model_outside_the_windows_falls_back_to_the_full_scan(self, tmp_path):
        path = tmp_path / "t.jsonl"
        with open(path, "w") as handle:
            handle.write(_line(_assistant_text("a", model="only-model-in-file")))
            for _ in range(300):
                handle.write(_line({"type": "user", "message": {"content": "q" * 200}}))
            handle.write(_line(_agent_call("p", model=None)))
        ctx = read_subagent_start_context(str(path), head_bytes=64, tail_bytes=2048)
        assert ctx.model == "only-model-in-file"

    def test_legacy_session_start_in_the_head_still_wins(self, tmp_path):
        path = tmp_path / "t.jsonl"
        with open(path, "w") as handle:
            handle.write(_line({"type": "session_start", "model": "head-model"}))
            for _ in range(300):
                handle.write(_line(_assistant_text("w" * 200, model="later")))
            handle.write(_line(_agent_call("p", model="newest")))
        ctx = read_subagent_start_context(str(path), head_bytes=1024, tail_bytes=4096)
        assert ctx.model == "head-model"


class TestLatestWinsOutsideTheWindows:
    """Code-review M1: head entries feed ONLY user_id and a session_start
    model. prompt and the last model come from the TAIL; when the tail has
    none, one full streaming pass finds the LATEST occurrence (like the old
    parsers) -- never the oldest one sitting in the head window."""

    HEAD, TAIL = 1024, 4096

    def _file(self, path, *, head_entries, middle_entries, padding_entry, pad=400):
        with open(path, "w") as handle:
            for entry in head_entries:
                handle.write(_line(entry))
            for _ in range(pad):
                handle.write(_line(padding_entry))
            for entry in middle_entries:
                handle.write(_line(entry))
            for _ in range(pad):
                handle.write(_line(padding_entry))
        return str(path)

    def _padding(self):
        return {"type": "user", "message": {"content": "p" * 200}}

    def test_newest_prompt_in_the_middle_beats_an_older_one_in_the_head(self, tmp_path):
        path = self._file(
            tmp_path / "t.jsonl",
            head_entries=[_agent_call("OLDEST head prompt")],
            middle_entries=[_agent_call("NEWEST middle prompt")],
            padding_entry=self._padding(),
        )
        ctx = read_subagent_start_context(
            path, head_bytes=self.HEAD, tail_bytes=self.TAIL
        )
        assert ctx.prompt == "NEWEST middle prompt"
        assert orchestrator_old_prompt(path) == "NEWEST middle prompt"

    def test_reviewers_exact_shape_two_megabytes_of_padding(self, tmp_path):
        path = tmp_path / "t.jsonl"
        pad = _line({"type": "user", "message": {"content": "q" * 3900}})
        with open(path, "w") as handle:
            handle.write(_line(_agent_call("head prompt")))
            handle.write(_line(_agent_call("most recent in the middle")))
            for _ in range(520):  # ~2 MB
                handle.write(pad)
        assert path.stat().st_size > 2_000_000
        ctx = read_subagent_start_context(str(path))
        assert ctx.prompt == "most recent in the middle"

    def test_prompt_in_the_tail_still_wins_without_a_full_pass(
        self, tmp_path, boom_full_parsers, monkeypatch
    ):
        from pacemaker.langfuse import subagent_context

        def no_stream(path):
            raise AssertionError("no full streaming pass expected")

        monkeypatch.setattr(subagent_context, "_stream_whole_file", no_stream)
        path = tmp_path / "t.jsonl"
        with open(path, "w") as handle:
            handle.write(_line(_agent_call("head prompt")))
            for _ in range(400):
                handle.write(_line(self._padding()))
            handle.write(_line(_agent_call("tail prompt", model="tail-model")))
        ctx = read_subagent_start_context(
            str(path), head_bytes=self.HEAD, tail_bytes=self.TAIL
        )
        assert (ctx.prompt, ctx.model) == ("tail prompt", "tail-model")

    def test_mid_session_model_switch_is_not_lost_when_the_tail_has_no_model(
        self, tmp_path
    ):
        path = self._file(
            tmp_path / "t.jsonl",
            head_entries=[_assistant_text("a", model="head-model")],
            middle_entries=[_assistant_text("b", model="switched-model")],
            padding_entry=self._padding(),
        )
        ctx = read_subagent_start_context(
            path, head_bytes=self.HEAD, tail_bytes=self.TAIL
        )
        assert ctx.model == "switched-model"
        assert jsonl_parser.parse_session_metadata(path)["model"] == "switched-model"

    def test_head_model_is_ignored_when_the_tail_has_one(self, tmp_path):
        path = tmp_path / "t.jsonl"
        with open(path, "w") as handle:
            handle.write(_line(_assistant_text("a", model="head-model")))
            for _ in range(400):
                handle.write(_line(self._padding()))
            handle.write(_line(_assistant_text("z", model="tail-model")))
        ctx = read_subagent_start_context(
            str(path), head_bytes=self.HEAD, tail_bytes=self.TAIL
        )
        assert ctx.model == "tail-model"

    def test_head_still_supplies_user_id_and_a_session_start_model(self, tmp_path):
        path = self._file(
            tmp_path / "t.jsonl",
            head_entries=[
                {"type": "auth_profile", "profile": {"email": "head@example.com"}},
                {"type": "session_start", "model": "start-model"},
            ],
            middle_entries=[_assistant_text("m", model="later-model")],
            padding_entry=self._padding(),
        )
        ctx = read_subagent_start_context(
            path, head_bytes=self.HEAD, tail_bytes=self.TAIL
        )
        assert ctx.user_id == "head@example.com"
        assert ctx.model == "start-model"

    def test_no_prompt_anywhere_after_the_full_pass_is_none(self, tmp_path):
        path = self._file(
            tmp_path / "t.jsonl",
            head_entries=[_assistant_text("a", model="m")],
            middle_entries=[],
            padding_entry=self._padding(),
        )
        ctx = read_subagent_start_context(
            path, head_bytes=self.HEAD, tail_bytes=self.TAIL
        )
        assert ctx.prompt is None


def orchestrator_old_prompt(path):
    """The pre-#157 behaviour: the old full-file parser (last prompt wins)."""
    from pacemaker.langfuse.orchestrator import extract_task_tool_prompt

    return extract_task_tool_prompt(path)


class TestHandleSubagentStartUsesIt:
    CONFIG = {
        "langfuse_enabled": True,
        "langfuse_base_url": "https://langfuse.example.com",
        "langfuse_public_key": "pk",
        "langfuse_secret_key": "sk",
    }

    def _run(self, tmp_path, monkeypatch, transcript, user_email="oauth@example.com"):
        pushed = []

        def fake_push(base_url, public, secret, batch, timeout=None):
            pushed.append(batch)
            return True, 1

        monkeypatch.setattr(orchestrator.push, "push_batch_events", fake_push)
        monkeypatch.setattr(jsonl_parser, "get_user_email", lambda: user_email)
        monkeypatch.setattr(
            orchestrator, "get_project_context", lambda: {"project_name": "p"}
        )
        config = dict(self.CONFIG, db_path=str(tmp_path / "usage.db"))
        trace_id = orchestrator.handle_subagent_start(
            config=config,
            parent_session_id="parent-1",
            subagent_session_id="subagent-a1",
            subagent_name="tdd-engineer",
            parent_transcript_path=transcript,
            state_dir=str(tmp_path / "state"),
        )
        return trace_id, pushed

    def test_trace_carries_prompt_model_and_user_without_full_parses(
        self, tmp_path, monkeypatch, boom_full_parsers
    ):
        path = _write(
            tmp_path / "t.jsonl",
            [
                {"type": "auth_profile", "profile": {"email": "t@example.com"}},
                _assistant_text("x", model="claude-sonnet-5-5"),
                _agent_call("review the parser", model="claude-opus-5-5"),
            ],
        )
        trace_id, pushed = self._run(tmp_path, monkeypatch, path)
        assert trace_id
        body = pushed[0][0]["body"]
        assert body["input"] == "review the parser"
        assert body["userId"] == "t@example.com"
        assert body["metadata"]["model"] == "claude-opus-5-5"

    def test_oauth_email_is_used_when_the_transcript_has_none(
        self, tmp_path, monkeypatch, boom_full_parsers
    ):
        path = _write(tmp_path / "t.jsonl", [_agent_call("p")])
        _, pushed = self._run(tmp_path, monkeypatch, path)
        assert pushed[0][0]["body"]["userId"] == "oauth@example.com"

    def test_no_user_anywhere_is_unknown_as_before(
        self, tmp_path, monkeypatch, boom_full_parsers
    ):
        path = _write(tmp_path / "t.jsonl", [_agent_call("p")])
        _, pushed = self._run(tmp_path, monkeypatch, path, user_email=None)
        assert pushed[0][0]["body"]["userId"] == "unknown"

    def test_missing_prompt_is_the_empty_string_as_before(self, tmp_path, monkeypatch):
        path = _write(tmp_path / "t.jsonl", [_assistant_text("x", model="m")])
        _, pushed = self._run(tmp_path, monkeypatch, path)
        assert pushed[0][0]["body"]["input"] == ""
