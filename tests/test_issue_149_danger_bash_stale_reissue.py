"""
Bug #149: the danger-bash "no INTENT" block cannot be fixed by the retry it
tells the agent to make, because the byte-identical re-issue binds to the
earlier, INTENT-less attempt and the block then describes the wrong turn.

Live evidence (session 8db566bc, 2026-10-01, v2.37.3; usage.db + logs +
transcript):
  * attempt A: danger command, no INTENT  -> `danger_bash_deferred`
  * attempt B: byte-identical re-issue    -> `danger_bash_block`, 5 attempts /
    3.01 s "transcript turn never flushed within the retry window"
  * attempt C: command changed (`; true`) -> deferred, then the identical
    re-issue passed.
A Bash tool_use is written to the transcript only after PreToolUse returns, so
when B's own turn is evaluated the newest readable turn issuing the command is
A (stale: it already has its tool_result). The anchor search already prefers
the NEWEST matching turn (it scans from the end), so once B IS flushed (the
re-issue after B, or a flush during the wait) B's own text is what is judged.
What was wrong: with only A readable, the gate announced "no INTENT" (plus the
#148 "NO visible text" notice) as if that were a verdict on B.

Fix contract:
  * newest identical turn wins -- locked here against real transcripts;
  * a stale anchor whose text has no INTENT still blocks (fail closed; #93's
    text gate and "not_found fails closed" are unchanged), but the block says
    plainly that it describes the PREVIOUS attempt and that one more identical
    re-issue is judged against the current message;
  * a block on a turn that WAS found (the current one) carries no such note.

Real transcript_reader against real JSONL files; only the clock (no real
sleeping), the danger-rule table and the Phase 2 LLM reviewer are replaced.
"""

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, patch

os.environ.setdefault("PACEMAKER_TEST_MODE", "1")

from pacemaker import database  # noqa: E402
from pacemaker.transcript_reader import (  # noqa: E402
    _find_turn_matching_tool_input,
    get_current_turn_message_for_validation,
)

COMMAND = "git worktree remove /tmp/pm149/wt && git branch -d pm149-branch"
OTHER_COMMAND = "git worktree remove /tmp/pm149/other"
INTENT_B = (
    "INTENT: Remove the merged scratch worktree and its branch, nothing else, "
    "so the repo is tidy."
)
STALE_NOTE_LEAD = "belongs to your PREVIOUS attempt"
NO_VISIBLE_TEXT_LEAD = "Your reasoning is invisible to the validator"


def _asst(request_id: str, block: dict) -> dict:
    return {
        "requestId": request_id,
        "message": {"role": "assistant", "content": [block]},
    }


def _thinking(text: str) -> dict:
    return {"type": "thinking", "thinking": text}


def _text(text: str) -> dict:
    return {"type": "text", "text": text}


def _tool_use(command: str, tool_id: str) -> dict:
    return {
        "type": "tool_use",
        "id": tool_id,
        "name": "Bash",
        "input": {"command": command},
    }


def _result(tool_id: str, text: str = "blocked") -> dict:
    return {
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content": text}
            ],
        }
    }


def _attempt_without_intent(
    request_id: str, tool_id: str, command=COMMAND
) -> List[dict]:
    """Opus-style turn: reasoning only, no visible text, then the tool call;
    followed by the tool_result (the block) -- i.e. a STALE turn."""
    return [
        _asst(request_id, _thinking("I will remove the merged worktree and branch.")),
        _asst(request_id, _tool_use(command, tool_id)),
        _result(tool_id),
    ]


def _attempt_with_intent(
    request_id: str, tool_id: str, intent: str = INTENT_B, with_result: bool = False
) -> List[dict]:
    entries = [
        _asst(request_id, _thinking("")),
        _asst(request_id, _text(intent)),
        _asst(request_id, _tool_use(COMMAND, tool_id)),
    ]
    if with_result:
        entries.append(_result(tool_id))
    return entries


def _write(path: str, entries: List[dict], mode: str = "w") -> None:
    with open(path, mode) as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


class _FakeClock:
    """No real sleeping; `on_sleep(n)` runs on the n-th sleep (1-based)."""

    def __init__(self, on_sleep=None):
        self.now = 0.0
        self.sleeps = 0
        self.on_sleep = on_sleep

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        self.now += seconds
        if self.on_sleep:
            self.on_sleep(self.sleeps)

    def patches(self):
        return (
            patch("pacemaker.transcript_reader.time.sleep", self.sleep),
            patch("pacemaker.transcript_reader.time.monotonic", self.monotonic),
        )


class TestNewestIdenticalTurnWins:
    """Regression lock: with several turns issuing the identical command the
    NEWEST readable one is the anchor, so the re-issue's own INTENT is what
    gets evaluated as soon as that turn is in the transcript."""

    def test_flushed_reissue_with_intent_beats_stale_intentless_attempt(self, tmp_path):
        transcript = str(tmp_path / "t.jsonl")
        _write(transcript, _attempt_without_intent("req_A", "toolu_A"))
        _write(transcript, _attempt_with_intent("req_B", "toolu_B"), mode="a")

        outcome: dict = {}
        text = _find_turn_matching_tool_input(
            transcript, {"command": COMMAND}, "Bash", _outcome=outcome
        )

        assert outcome["outcome"] == "found"
        assert text and "Remove the merged scratch worktree" in text
        assert outcome["anchor_has_visible_text"] is True

    def test_newest_of_three_identical_turns_is_the_anchor(self, tmp_path):
        transcript = str(tmp_path / "t.jsonl")
        _write(transcript, _attempt_without_intent("req_A", "toolu_A"))
        _write(
            transcript,
            _attempt_with_intent("req_B", "toolu_B", "INTENT: second try.", True),
            mode="a",
        )
        _write(
            transcript,
            _attempt_with_intent("req_C", "toolu_C", "INTENT: third try."),
            mode="a",
        )

        outcome: dict = {}
        text = _find_turn_matching_tool_input(
            transcript, {"command": COMMAND}, "Bash", _outcome=outcome
        )

        assert outcome["outcome"] == "found"
        assert "third try" in text and "second try" not in text

    def test_reissue_flushed_during_the_wait_is_evaluated_not_the_stale_turn(
        self, tmp_path
    ):
        transcript = str(tmp_path / "t.jsonl")
        _write(transcript, _attempt_without_intent("req_A", "toolu_A"))

        def flush_reissue(sleep_number: int) -> None:
            if sleep_number == 2:
                _write(transcript, _attempt_with_intent("req_B", "toolu_B"), mode="a")

        clock = _FakeClock(on_sleep=flush_reissue)
        diagnostics: dict = {}
        p_sleep, p_mono = clock.patches()
        with p_sleep, p_mono:
            text = get_current_turn_message_for_validation(
                transcript,
                tool_input={"command": COMMAND},
                tool_name="Bash",
                _max_wait_seconds=3.0,
                _diagnostics=diagnostics,
            )

        assert diagnostics["outcome"] == "found"
        assert text and "Remove the merged scratch worktree" in text

    def test_a_turn_for_a_different_command_is_never_the_anchor(self, tmp_path):
        transcript = str(tmp_path / "t.jsonl")
        _write(
            transcript,
            [
                _asst("req_A", _text(INTENT_B)),
                _asst("req_A", _tool_use(OTHER_COMMAND, "toolu_A")),
            ],
        )

        outcome: dict = {}
        text = _find_turn_matching_tool_input(
            transcript, {"command": COMMAND}, "Bash", _outcome=outcome
        )

        assert text is None and outcome["outcome"] == "not_found"


class _GateHarness:
    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "usage.db")
        self.transcript = os.path.join(self.tmp_dir, "transcript.jsonl")
        Path(self.transcript).write_text("")
        database.initialize_database(self.db_path)

    def teardown_method(self):
        import shutil

        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def run_gate(self, clock: Optional[_FakeClock] = None, reviewer=None):
        """Run the real PreToolUse gate for COMMAND. Returns (result, reviewer
        mock)."""
        from pacemaker.hook import run_pre_tool_hook

        clock = clock or _FakeClock()
        reviewer = reviewer or MagicMock(return_value=("APPROVED", "test-reviewer"))
        rule = {"id": "WD-149", "description": "worktree removal (test fixture)"}
        payload = json.dumps(
            {
                "session_id": "s-149",
                "transcript_path": self.transcript,
                "tool_name": "Bash",
                "tool_input": {"command": COMMAND},
            }
        )
        config = {
            "enabled": True,
            "intent_validation_enabled": True,
            "danger_bash_enabled": True,
            "hook_model": "auto",
        }
        p_sleep, p_mono = clock.patches()
        with (
            p_sleep,
            p_mono,
            patch("sys.stdin", MagicMock(read=lambda: payload)),
            patch("pacemaker.hook.load_config", return_value=config),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch("pacemaker.danger_bash_rules.load_rules", return_value=[rule]),
            patch("pacemaker.danger_bash_rules.match_command", return_value=[rule]),
            patch("pacemaker.inference.resolve_and_call_with_reviewer", reviewer),
        ):
            return run_pre_tool_hook(), reviewer

    def phase1_details(self) -> dict:
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT details FROM blockage_events WHERE category = 'intent_validation'"
            ).fetchall()
        finally:
            conn.close()
        assert rows, "expected a Phase 1 (no INTENT) blockage event"
        return json.loads(rows[-1][0])


class TestLiveSequenceThroughTheGate(_GateHarness):
    def test_reissue_that_is_flushed_is_judged_on_its_own_intent(self):
        _write(self.transcript, _attempt_without_intent("req_A", "toolu_A"))
        _write(self.transcript, _attempt_with_intent("req_B", "toolu_B"), mode="a")

        result, reviewer = self.run_gate()

        assert result == {"continue": True}
        reviewer.assert_called_once()
        assert (
            "Remove the merged scratch worktree" in reviewer.call_args.kwargs["prompt"]
        )

    def test_reissue_flushed_during_the_wait_is_judged_on_its_own_intent(self):
        _write(self.transcript, _attempt_without_intent("req_A", "toolu_A"))

        def flush_reissue(sleep_number: int) -> None:
            if sleep_number == 1:
                _write(
                    self.transcript,
                    _attempt_with_intent("req_B", "toolu_B"),
                    mode="a",
                )

        result, reviewer = self.run_gate(clock=_FakeClock(on_sleep=flush_reissue))

        assert result == {"continue": True}
        assert (
            "Remove the merged scratch worktree" in reviewer.call_args.kwargs["prompt"]
        )

    def test_stale_intent_bearing_previous_turn_is_still_accepted(self):
        """The recovery that already worked (#93): B was blocked but carried the
        INTENT; the next identical re-issue binds to B (stale, with INTENT)."""
        _write(
            self.transcript,
            _attempt_with_intent("req_B", "toolu_B", with_result=True),
        )

        result, reviewer = self.run_gate()

        assert result == {"continue": True}
        reviewer.assert_called_once()


class TestStaleIntentlessAnchorIsNotPassedOffAsTheCurrentTurn(_GateHarness):
    """Only A (stale, no INTENT) is readable: B is not flushed yet."""

    def _only_stale_attempt(self):
        _write(self.transcript, _attempt_without_intent("req_A", "toolu_A"))

    def test_block_says_it_describes_the_previous_attempt(self):
        self._only_stale_attempt()

        result, reviewer = self.run_gate()

        assert result["decision"] == "block"
        reason = result["reason"]
        assert STALE_NOTE_LEAD in reason
        assert "INTENT" in reason  # still the Phase 1 block, still fails closed
        reviewer.assert_not_called()

    def test_block_tells_a_visible_intent_to_re_issue_once_more_not_to_change_the_command(
        self,
    ):
        self._only_stale_attempt()

        reason = self.run_gate()[0]["reason"]

        assert "re-issue the identical command once more" in reason
        assert "change the command" not in reason.lower()

    def test_no_visible_text_notice_is_still_given(self):
        # The #148/#150 notice is what teaches a thinking-only model to write
        # visible text; in the live evidence the current turn had none either.
        self._only_stale_attempt()

        assert NO_VISIBLE_TEXT_LEAD in self.run_gate()[0]["reason"]

    def test_blockage_details_record_the_stale_anchor(self):
        self._only_stale_attempt()

        self.run_gate()

        assert self.phase1_details()["stale_anchor"] is True

    def test_visible_text_without_intent_in_the_stale_turn_gets_the_note_too(self):
        _write(
            self.transcript,
            [
                _asst("req_A", _text("Cleaning up the worktree now.")),
                _asst("req_A", _tool_use(COMMAND, "toolu_A")),
                _result("toolu_A"),
            ],
        )

        reason = self.run_gate()[0]["reason"]

        assert STALE_NOTE_LEAD in reason
        assert NO_VISIBLE_TEXT_LEAD not in reason

    def test_genuinely_current_intentless_turn_gets_no_stale_note(self):
        # Found (not stale): the turn being judged IS the current one.
        _write(
            self.transcript,
            [
                _asst("req_A", _thinking("remove the worktree")),
                _asst("req_A", _tool_use(COMMAND, "toolu_A")),
            ],
        )

        result, _ = self.run_gate()

        assert result["decision"] == "block"
        assert STALE_NOTE_LEAD not in result["reason"]
        assert self.phase1_details()["stale_anchor"] is False

    def test_not_found_still_fails_closed_with_the_deferral(self):
        result, reviewer = self.run_gate()

        assert result["decision"] == "block"
        assert "transcript not ready" in result["reason"]
        assert STALE_NOTE_LEAD not in result["reason"]
        reviewer.assert_not_called()
