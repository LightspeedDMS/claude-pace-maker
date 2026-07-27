"""
Bug #83 regression tests: PreToolUse intent validation evaluates the
PREVIOUS turn's message when the current turn is not yet flushed
(transcript flush race / TOCTOU).

Coverage:
1. Write/Edit gate — tool-matched anchor returns None when turn unflushed
2. Write/Edit gate — tool-matched anchor returns correct message when flushed
3. Stale same-file INTENT does not false-pass (content differs → None)
4. Bounded retry terminates within provable cap (Messi Rule 14)
5. Danger-Bash gate — Bash tool_use matching (same fix)
6. extract_current_assistant_message hardening via file_path
7. Backward compat — callers without tool_input retain old behavior
"""

import itertools
import json
from typing import List, Optional


# ---------------------------------------------------------------------------
# JSONL transcript builder helpers
# ---------------------------------------------------------------------------


def _asst(request_id: Optional[str], block: dict) -> dict:
    entry = {"message": {"role": "assistant", "content": [block]}}
    if request_id is not None:
        entry["requestId"] = request_id
    return entry


def _user(text: str) -> dict:
    return {"message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def _text_block(t: str) -> dict:
    return {"type": "text", "text": t}


def _tool_use_block(name: str, inp: dict, tool_id: str = "toolu_default") -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": inp}


def _write_transcript(lines: List[dict], tmp_path) -> str:
    p = tmp_path / "transcript.jsonl"
    with open(str(p), "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return str(p)


# ---------------------------------------------------------------------------
# Shared fixtures / constants
# ---------------------------------------------------------------------------

TARGET = "/project/src/pacemaker/foo.py"
PREV_CONTENT = "# old content\nprint('prev')\n"
CUR_CONTENT = "# new content\nprint('current')\n"
PREV_INTENT = (
    "INTENT: Modify foo.py to fix old bug.\n"
    "Test coverage: tests/test_foo.py::test_old"
)
CUR_INTENT = (
    "INTENT: Modify foo.py to add new feature.\n"
    "Test coverage: tests/test_foo.py::test_new"
)


def _lagged_write(tmp_path) -> str:
    """Previous Write turn flushed; current Write turn NOT yet in transcript."""
    return _write_transcript(
        [
            _asst("req_PREV", _text_block(PREV_INTENT)),
            _asst(
                "req_PREV",
                _tool_use_block(
                    "Write", {"file_path": TARGET, "content": PREV_CONTENT}
                ),
            ),
            _user("Please update foo.py again with new content"),
            # current turn (INTENT + Write CUR_CONTENT) NOT YET FLUSHED
        ],
        tmp_path,
    )


def _flushed_write(tmp_path) -> str:
    """Both previous Write and current Write turns in transcript."""
    return _write_transcript(
        [
            _asst("req_PREV", _text_block(PREV_INTENT)),
            _asst(
                "req_PREV",
                _tool_use_block(
                    "Write", {"file_path": TARGET, "content": PREV_CONTENT}
                ),
            ),
            _user("Please update foo.py again with new content"),
            _asst("req_CUR", _text_block(CUR_INTENT)),
            _asst(
                "req_CUR",
                _tool_use_block("Write", {"file_path": TARGET, "content": CUR_CONTENT}),
            ),
        ],
        tmp_path,
    )


# ---------------------------------------------------------------------------
# Test group 1: tool-matched anchor — Write gate
# ---------------------------------------------------------------------------


class TestToolMatchedAnchorWrite:
    """Core fix: get_current_turn_message_for_validation with tool_input."""

    def test_unflushed_returns_none_not_prev_turn(self, tmp_path):
        """BUG #83: when current Write turn is NOT in transcript, return None.

        Old code would select the previous Write turn (wrong) and return its
        text. New code must return None (transcript-not-ready sentinel).
        """
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _lagged_write(tmp_path)
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": TARGET, "content": CUR_CONTENT},
            tool_name="Write",
            _max_wait_seconds=0.0,
        )
        assert result is None, (
            f"Expected None (transcript-not-ready) but got: {result!r}\n"
            "Old bug: would return previous turn text instead of None."
        )

    def test_flushed_returns_current_intent(self, tmp_path):
        """When current Write IS in transcript, return its message containing INTENT."""
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _flushed_write(tmp_path)
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": TARGET, "content": CUR_CONTENT},
            tool_name="Write",
            _max_wait_seconds=0.0,
        )
        assert result is not None, "Should find the flushed current turn"
        assert "INTENT:" in result, f"Expected INTENT: in result, got: {result!r}"
        assert "new feature" in result, f"Expected CUR_INTENT text, got: {result!r}"

    def test_prev_content_write_not_matched_for_cur_content(self, tmp_path):
        """False-pass prevention: previous Write (same file, different content) must NOT match.

        Scenario: both previous and current writes target same file_path, but
        PREV_CONTENT != CUR_CONTENT. The anchor must reject PREV_CONTENT
        when looking for CUR_CONTENT.
        """
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _lagged_write(tmp_path)
        # Current turn has CUR_CONTENT but is NOT in transcript yet.
        # Previous turn has PREV_CONTENT and IS in transcript.
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": TARGET, "content": CUR_CONTENT},
            tool_name="Write",
            _max_wait_seconds=0.0,
        )
        assert result is None, (
            f"Same-file Write with different content must return None, not match "
            f"previous turn. Got: {result!r}"
        )

    def test_correct_content_matched_when_multiple_writes_to_same_file(self, tmp_path):
        """Multiple Write turns to same file → anchor on the LAST matching content."""
        transcript = _write_transcript(
            [
                _asst(
                    "req_A",
                    _text_block("INTENT: First write.\nTest coverage: tests/t1.py::t1"),
                ),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write", {"file_path": TARGET, "content": PREV_CONTENT}
                    ),
                ),
                _user("Write again"),
                _asst("req_B", _text_block(CUR_INTENT)),
                _asst(
                    "req_B",
                    _tool_use_block(
                        "Write", {"file_path": TARGET, "content": CUR_CONTENT}
                    ),
                ),
            ],
            tmp_path,
        )
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": TARGET, "content": CUR_CONTENT},
            tool_name="Write",
            _max_wait_seconds=0.0,
        )
        assert result is not None
        assert (
            "new feature" in result
        ), "Should select the LAST (req_B) write, not req_A"
        assert "First write" not in result, "Must not select req_A intent"


# ---------------------------------------------------------------------------
# Test group 2: tool-matched anchor — Edit gate
# ---------------------------------------------------------------------------


class TestToolMatchedAnchorEdit:
    def test_edit_unflushed_returns_none(self, tmp_path):
        """Edit with current new_string not found in transcript → None."""
        transcript = _write_transcript(
            [
                _asst(
                    "req_PREV",
                    _text_block(
                        "INTENT: Edit foo.py old bug.\nTest coverage: tests/test_foo.py::t_old"
                    ),
                ),
                _asst(
                    "req_PREV",
                    _tool_use_block(
                        "Edit",
                        {
                            "file_path": TARGET,
                            "old_string": "old",
                            "new_string": "prev-new",
                        },
                    ),
                ),
                _user("Edit again"),
            ],
            tmp_path,
        )
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={
                "file_path": TARGET,
                "old_string": "X",
                "new_string": "cur-new",
            },
            tool_name="Edit",
            _max_wait_seconds=0.0,
        )
        assert result is None

    def test_edit_flushed_returns_intent(self, tmp_path):
        """Edit with matching file_path+new_string in transcript → message with INTENT."""
        transcript = _write_transcript(
            [
                _asst(
                    "req_PREV",
                    _text_block(
                        "INTENT: Edit foo.py old bug.\nTest coverage: tests/test_foo.py::t_old"
                    ),
                ),
                _asst(
                    "req_PREV",
                    _tool_use_block(
                        "Edit",
                        {
                            "file_path": TARGET,
                            "old_string": "old",
                            "new_string": "prev-new",
                        },
                    ),
                ),
                _user("Edit again"),
                _asst(
                    "req_CUR",
                    _text_block(CUR_INTENT),
                ),
                _asst(
                    "req_CUR",
                    _tool_use_block(
                        "Edit",
                        {
                            "file_path": TARGET,
                            "old_string": "X",
                            "new_string": "cur-new",
                        },
                    ),
                ),
            ],
            tmp_path,
        )
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={
                "file_path": TARGET,
                "old_string": "X",
                "new_string": "cur-new",
            },
            tool_name="Edit",
            _max_wait_seconds=0.0,
        )
        assert result is not None
        assert "INTENT:" in result


# ---------------------------------------------------------------------------
# Test group 3: bounded retry (Messi Rule 14)
# ---------------------------------------------------------------------------


class TestBoundedRetry:
    """The retry loop MUST have provable termination: bounded by real
    (monotonic) elapsed time reaching _max_wait_seconds, never unbounded."""

    def test_returns_none_after_retries_when_never_flushed(self, tmp_path):
        """After the ceiling is reached, returns None (never hangs)."""
        import time

        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _lagged_write(tmp_path)
        t0 = time.monotonic()
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": TARGET, "content": CUR_CONTENT},
            tool_name="Write",
            _max_wait_seconds=0.05,
        )
        elapsed = time.monotonic() - t0

        assert result is None, "Must return None after exhausting the wait ceiling"
        # A small ceiling (0.05s) proves the loop is bounded, not unbounded.
        assert (
            elapsed < 1.0
        ), f"Retry loop took {elapsed:.2f}s — possible unbounded loop"

    def test_finds_match_on_first_attempt_when_flushed(self, tmp_path):
        """No wasted retries when turn is already in transcript."""
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _flushed_write(tmp_path)
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": TARGET, "content": CUR_CONTENT},
            tool_name="Write",
            _max_wait_seconds=10.0,
        )
        # Should return on first attempt, not retry
        assert result is not None
        assert "INTENT:" in result

    def test_zero_retries_single_attempt(self, tmp_path):
        """_max_wait_seconds=0.0 means exactly one attempt before rejecting."""
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _lagged_write(tmp_path)
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": TARGET, "content": CUR_CONTENT},
            tool_name="Write",
            _max_wait_seconds=0.0,
        )
        assert result is None


# ---------------------------------------------------------------------------
# Test group 4: danger-bash gate (Bash tool_use matching)
# ---------------------------------------------------------------------------

BASH_CMD = "git reset --hard HEAD"
BASH_INTENT = (
    "INTENT: Revert uncommitted changes in /tmp/sandbox.\n"
    "This reverts only the working directory, not committed history."
)


def _bash_lagged(tmp_path) -> str:
    return _write_transcript(
        [
            _asst(
                "req_PREV", _text_block("Zero footprint confirmed — no files written.")
            ),
            _user("Now run git reset to clean up"),
            # Current Bash turn NOT flushed
        ],
        tmp_path,
    )


def _bash_flushed(tmp_path) -> str:
    return _write_transcript(
        [
            _asst(
                "req_PREV", _text_block("Zero footprint confirmed — no files written.")
            ),
            _user("Now run git reset to clean up"),
            _asst("req_CUR", _text_block(BASH_INTENT)),
            _asst("req_CUR", _tool_use_block("Bash", {"command": BASH_CMD})),
        ],
        tmp_path,
    )


class TestDangerBashAnchor:
    def test_bash_unflushed_returns_none(self, tmp_path):
        """Bash turn not in transcript → None (fail-open for Phase 1)."""
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _bash_lagged(tmp_path)
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"command": BASH_CMD},
            tool_name="Bash",
            _max_wait_seconds=0.0,
        )
        assert result is None

    def test_bash_flushed_returns_intent(self, tmp_path):
        """Bash turn in transcript → message with INTENT."""
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _bash_flushed(tmp_path)
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"command": BASH_CMD},
            tool_name="Bash",
            _max_wait_seconds=0.0,
        )
        assert result is not None
        assert "INTENT:" in result
        assert "Revert" in result

    def test_different_bash_command_not_matched(self, tmp_path):
        """Transcript has a DIFFERENT Bash command → does not match current command."""
        transcript = _write_transcript(
            [
                _asst(
                    "req_PREV",
                    _text_block(
                        "INTENT: Run ls safely.\nTest coverage: N/A (non-destructive)"
                    ),
                ),
                _asst("req_PREV", _tool_use_block("Bash", {"command": "ls /tmp"})),
                _user("Now run something dangerous"),
            ],
            tmp_path,
        )
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"command": "rm -rf /tmp/foo"},
            tool_name="Bash",
            _max_wait_seconds=0.0,
        )
        assert (
            result is None
        ), f"Different command in transcript must not match; got: {result!r}"


# ---------------------------------------------------------------------------
# Test group 5: extract_current_assistant_message hardening
# ---------------------------------------------------------------------------


class TestExtractCurrentAssistantMessageHardening:
    """Defense-in-depth: file_path check in extract_current_assistant_message."""

    def test_wrong_file_in_last_message_returns_empty(self):
        """When selected message mentions a different file, return '' to prevent false-pass."""
        from pacemaker.intent_validator import extract_current_assistant_message

        messages = [
            "Some earlier message",
            (
                "INTENT: Modify other_file.py to fix bug.\n"
                "Test coverage: tests/test_other.py::test_fix\n\n"
                "[TOOL: Write]\nfile_path: /project/src/other_file.py\ncontent: x"
            ),
        ]
        result = extract_current_assistant_message(
            messages, file_path="/project/src/target.py"
        )
        assert (
            result == ""
        ), f"Expected '' when selected message mentions wrong file; got: {result!r}"

    def test_correct_file_in_last_message_returned(self):
        """When selected message mentions the target file, return it normally."""
        from pacemaker.intent_validator import extract_current_assistant_message

        messages = [
            "Some earlier message",
            (
                "INTENT: Modify target.py to add feature.\n"
                "Test coverage: tests/test_target.py::test_feat\n\n"
                "[TOOL: Write]\nfile_path: /project/src/target.py\ncontent: ..."
            ),
        ]
        result = extract_current_assistant_message(
            messages, file_path="/project/src/target.py"
        )
        assert "INTENT:" in result
        assert result != ""

    def test_no_file_path_unchanged_behavior(self):
        """Without file_path argument, behavior is unchanged."""
        from pacemaker.intent_validator import extract_current_assistant_message

        messages = [
            "INTENT: Modify other.py to fix bug.",
            "[TOOL: Write]\nfile_path: other.py\ncontent: x",
        ]
        # No file_path → no filtering, returns the message
        result = extract_current_assistant_message(messages)
        assert result != "", "Without file_path, old behavior must be preserved"

    def test_empty_messages_with_file_path_returns_empty(self):
        """Empty message list → '' regardless of file_path."""
        from pacemaker.intent_validator import extract_current_assistant_message

        result = extract_current_assistant_message([], file_path="/project/src/foo.py")
        assert result == ""

    def test_single_message_correct_file(self):
        """Single message mentioning target file → returned as-is."""
        from pacemaker.intent_validator import extract_current_assistant_message

        msg = "INTENT: Modify foo.py to fix bug.\n[TOOL: Write]\nfile_path: foo.py"
        result = extract_current_assistant_message(
            [msg], file_path="/project/src/foo.py"
        )
        assert "INTENT:" in result

    def test_single_message_wrong_file_returns_empty(self):
        """Single message NOT mentioning target file → '' (stale wrong-file turn)."""
        from pacemaker.intent_validator import extract_current_assistant_message

        msg = "INTENT: Modify bar.py to fix bug.\n[TOOL: Write]\nfile_path: bar.py"
        result = extract_current_assistant_message(
            [msg], file_path="/project/src/foo.py"
        )
        assert result == ""

    def test_one_back_intent_correct_file_still_merged(self):
        """1-back INTENT path: when prev message has intent+correct file, still merged."""
        from pacemaker.intent_validator import extract_current_assistant_message

        messages = [
            (
                "INTENT: Modify foo.py to add feature.\n"
                "Test coverage: tests/test_foo.py::test_feature"
            ),
            "[TOOL: Write]\nfile_path: foo.py\ncontent: new code",
        ]
        result = extract_current_assistant_message(
            messages, file_path="/project/src/foo.py"
        )
        assert "INTENT:" in result, f"1-back merge should work; got: {result!r}"


# ---------------------------------------------------------------------------
# Test group 6: backward compatibility — callers without tool_input
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Existing callers that don't pass tool_input must get the old str behavior."""

    def test_no_tool_input_returns_str_not_none_for_flushed(self, tmp_path):
        """Without tool_input, function returns str (never None)."""
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _flushed_write(tmp_path)
        result = get_current_turn_message_for_validation(transcript)
        # Old behavior: returns str (may be "" or message)
        assert isinstance(
            result, str
        ), f"Without tool_input, must return str not {type(result)}"

    def test_no_tool_input_returns_empty_when_no_write_in_transcript(self, tmp_path):
        """Without tool_input and no Write/Edit in transcript → ''."""
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _write_transcript(
            [
                _asst("req_A", _text_block("Just text, no Write tool")),
                _user("ok"),
            ],
            tmp_path,
        )
        result = get_current_turn_message_for_validation(transcript)
        assert result == ""


# ---------------------------------------------------------------------------
# Test group 7: hook-level fail-CLOSED — Write/Edit gate (v2.33.2)
# ---------------------------------------------------------------------------


class TestHookLevelFailClosed:
    """Hook-level: when the current tool_use is absent from the transcript,
    the Write/Edit gate now mirrors the danger-bash gate (group 8 below) and
    BLOCKS (fail-closed) with a re-issue message, instead of silently
    continuing.

    v2.33.2 change: this branch previously failed OPEN (continue=True),
    which meant intent validation enforced NOTHING for any edit that raced
    the transcript flush — confirmed live via the intent_validation_deferred
    telemetry canary. The danger-bash gate already proved fail-closed +
    re-issue works in practice: the agent re-issues the IDENTICAL tool call,
    the re-issue's turn is then flushed, the tool-matched anchor binds to it,
    and validation proceeds normally on the second attempt. This brings the
    Write/Edit gate in line with that proven pattern.
    """

    def _make_config(self, tmp_path) -> str:
        cfg = str(tmp_path / "config.json")
        with open(cfg, "w") as f:
            json.dump({"intent_validation_enabled": True, "enabled": True}, f)
        return cfg

    def _make_db(self, tmp_path) -> str:
        from pacemaker.database import initialize_database

        db = str(tmp_path / "test.db")
        initialize_database(db)
        return db

    def test_write_gate_fails_closed_when_transcript_not_ready(self, tmp_path):
        """Write gate returns decision=block (fail-closed) when the matching
        tool_use is absent from the transcript (TOCTOU race), and still
        records the intent_validation_deferred telemetry event.

        Uses a non-existent transcript path to trigger the fail-fast sentinel
        (file missing → immediate None on every retry attempt — no entry will
        ever appear). The real retry loop runs (not mocked) so this is a true
        integration check of the missing-file path. The loop is bounded by
        REAL (monotonic) elapsed time (issue #91), so mocking only
        time.sleep no longer makes it instant — time.monotonic is also
        faked (jumping 100s per call) so the 30s ceiling trips after exactly
        one read, keeping the test fast while still exercising the real
        missing-file code path (see
        TestRetryDefaultsWidenedTo30SecondsWithBackoff below for dedicated
        timing coverage of the retry loop itself).
        """
        from pacemaker.hook import run_pre_tool_hook
        import sqlite3
        from unittest.mock import patch

        config_path = self._make_config(tmp_path)
        db_path = self._make_db(tmp_path)
        # Non-existent transcript — fail-fast returns None on every attempt.
        missing_transcript = str(tmp_path / "no_such_transcript.jsonl")

        hook_data = json.dumps(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/src/target.py", "content": "x = 1\n"},
                "session_id": "test-failclosed",
                "transcript_path": missing_transcript,
            }
        )

        with (
            patch("pacemaker.hook.DEFAULT_CONFIG_PATH", config_path),
            patch("pacemaker.hook.DEFAULT_DB_PATH", db_path),
            patch("sys.stdin") as mock_stdin,
            patch("pacemaker.transcript_reader.time.sleep"),
            patch(
                "pacemaker.transcript_reader.time.monotonic",
                side_effect=itertools.count(0, 100),
            ),
        ):
            mock_stdin.read.return_value = hook_data
            result = run_pre_tool_hook()

        # 1. Must fail-CLOSED (block, not continue) — the v2.33.2 behavior
        #    change this test suite exists to lock in.
        assert (
            result.get("decision") == "block"
        ), f"Write gate must fail-CLOSED when transcript not ready; got: {result}"
        assert (
            result.get("continue") is not True
        ), f"Fail-closed must NOT also signal continue=True; got: {result}"
        reason = result.get("reason", "").lower()
        assert "transcript" in reason and "re-issue" in reason, (
            f"Block reason must explain the transcript-timing race and "
            f"instruct re-issuing the identical tool call; got: {result.get('reason')!r}"
        )

        # 2. intent_validation_deferred telemetry must still be recorded —
        #    this is how the race is surfaced in usage.db / the monitor.
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM blockage_events "
            "WHERE category = 'intent_validation_deferred'"
        )
        deferred_count = cursor.fetchone()[0]
        conn.close()
        assert deferred_count >= 1, (
            "Expected an intent_validation_deferred blockage event when the "
            "Write gate fails closed on the transcript-flush race."
        )

    def test_edit_gate_fails_closed_when_transcript_not_ready(self, tmp_path):
        """Edit gate returns decision=block (fail-closed) and records the
        deferred telemetry event when the matching tool_use is absent
        (symmetry check for Edit tool)."""
        from pacemaker.hook import run_pre_tool_hook
        import sqlite3
        from unittest.mock import patch

        config_path = self._make_config(tmp_path)
        db_path = self._make_db(tmp_path)
        missing_transcript = str(tmp_path / "no_such_transcript.jsonl")

        hook_data = json.dumps(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "/src/target.py",
                    "old_string": "old",
                    "new_string": "new",
                },
                "session_id": "test-failclosed-edit",
                "transcript_path": missing_transcript,
            }
        )

        with (
            patch("pacemaker.hook.DEFAULT_CONFIG_PATH", config_path),
            patch("pacemaker.hook.DEFAULT_DB_PATH", db_path),
            patch("sys.stdin") as mock_stdin,
            patch("pacemaker.transcript_reader.time.sleep"),
            patch(
                "pacemaker.transcript_reader.time.monotonic",
                side_effect=itertools.count(0, 100),
            ),
        ):
            mock_stdin.read.return_value = hook_data
            result = run_pre_tool_hook()

        assert (
            result.get("decision") == "block"
        ), f"Edit gate must fail-CLOSED when transcript not ready; got: {result}"
        assert result.get("continue") is not True

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM blockage_events "
            "WHERE category = 'intent_validation_deferred'"
        )
        deferred_count = cursor.fetchone()[0]
        conn.close()
        assert deferred_count >= 1, (
            "Expected an intent_validation_deferred blockage event when the "
            "Edit gate fails closed on the transcript-flush race."
        )


# ---------------------------------------------------------------------------
# Test group 8: hook-level danger-bash fail-CLOSED (requirement #3)
# ---------------------------------------------------------------------------


class TestDangerBashFailClosed:
    """Hook-level: when a dangerous Bash command can't be matched in the
    transcript, the danger-bash gate must BLOCK (fail-closed) — not allow
    the command through silently.  A spurious block is recoverable; running
    rm -rf / git reset --hard unvalidated is not."""

    def _make_config(self, tmp_path) -> str:
        cfg = str(tmp_path / "config.json")
        with open(cfg, "w") as f:
            json.dump({"intent_validation_enabled": True, "enabled": True}, f)
        return cfg

    def _make_db(self, tmp_path) -> str:
        from pacemaker.database import initialize_database

        db = str(tmp_path / "test.db")
        initialize_database(db)
        return db

    def test_bash_gate_fails_closed_when_transcript_not_ready(self, tmp_path):
        """Dangerous Bash command with missing transcript → BLOCK (fail-closed).

        Uses a non-existent transcript so the fail-fast path returns None
        immediately. The danger-bash gate must not pass the command through.

        The bash call site does not pin its own retry-param overrides — it
        shares the same 30s exponential-backoff ceiling as the Write/Edit
        gate (single source of truth, issue #91). The real retry loop still
        runs (missing file → None on every attempt); the loop is bounded by
        REAL (monotonic) elapsed time, so time.monotonic is faked (jumping
        100s per call) alongside time.sleep so this test stays instant.
        """
        from pacemaker.hook import run_pre_tool_hook
        from unittest.mock import patch

        config_path = self._make_config(tmp_path)
        db_path = self._make_db(tmp_path)
        missing_transcript = str(tmp_path / "no_such_transcript.jsonl")

        # rm -rf matches the SD (System Destruction) danger rules
        dangerous_cmd = "rm -rf /tmp/test_cleanup_dir"

        hook_data = json.dumps(
            {
                "tool_name": "Bash",
                "tool_input": {"command": dangerous_cmd},
                "session_id": "test-bash-failclosed",
                "transcript_path": missing_transcript,
            }
        )

        with (
            patch("pacemaker.hook.DEFAULT_CONFIG_PATH", config_path),
            patch("pacemaker.hook.DEFAULT_DB_PATH", db_path),
            patch("sys.stdin") as mock_stdin,
            patch("pacemaker.transcript_reader.time.sleep"),
            patch(
                "pacemaker.transcript_reader.time.monotonic",
                side_effect=itertools.count(0, 100),
            ),
        ):
            mock_stdin.read.return_value = hook_data
            result = run_pre_tool_hook()

        # Must BLOCK — not pass through — when dangerous + transcript missing
        assert (
            result.get("decision") == "block"
        ), f"Dangerous Bash must fail-CLOSED when transcript not ready; got: {result}"
        reason = result.get("reason", "")
        assert (
            "transcript" in reason.lower() or "re-run" in reason.lower()
        ), f"Block reason must mention transcript race or re-run; got: {reason!r}"

    def test_bash_gate_blocks_git_reset_when_transcript_not_ready(self, tmp_path):
        """git reset --hard with missing transcript → BLOCK (WD danger rule)."""
        from pacemaker.hook import run_pre_tool_hook
        from unittest.mock import patch

        config_path = self._make_config(tmp_path)
        db_path = self._make_db(tmp_path)
        missing_transcript = str(tmp_path / "no_such_transcript.jsonl")

        hook_data = json.dumps(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "git reset --hard HEAD"},
                "session_id": "test-bash-gitresetclosed",
                "transcript_path": missing_transcript,
            }
        )

        with (
            patch("pacemaker.hook.DEFAULT_CONFIG_PATH", config_path),
            patch("pacemaker.hook.DEFAULT_DB_PATH", db_path),
            patch("sys.stdin") as mock_stdin,
            patch("pacemaker.transcript_reader.time.sleep"),
            patch(
                "pacemaker.transcript_reader.time.monotonic",
                side_effect=itertools.count(0, 100),
            ),
        ):
            mock_stdin.read.return_value = hook_data
            result = run_pre_tool_hook()

        assert (
            result.get("decision") == "block"
        ), f"git reset --hard must fail-CLOSED when transcript not ready; got: {result}"


# ---------------------------------------------------------------------------
# Test group 9: 30s hard-ceiling exponential-backoff retry defaults (#91 v2)
# ---------------------------------------------------------------------------


class TestRetryDefaultsWidenedTo30SecondsWithBackoff:
    """Issue #91 (second pass): the original fix replaced the fixed
    21-attempt/0.25s-interval retry schedule (~5.25s nominal ceiling) with
    exponential backoff hard-ceiled at a real (monotonic) 15s elapsed-time
    budget. Live evidence (172 intent_validation_dangerbash race blocks over
    14 days, recurring in clusters on a real 324MB/26,626-line transcript)
    proved the 15s ceiling still insufficient — direct measurement showed
    ``_find_turn_matching_tool_input``'s full-file re-parse cost 3.067s per
    attempt on that transcript, consuming nearly the entire 15s budget on
    scan cost rather than real waiting. Combined with the fixed-cost tail
    read (see TestFixedCostTailRead below — a v2 simplification that
    replaced an interim growing-window design once that design was itself
    measured to make the not-found case slower still), which makes each
    attempt cheap regardless of file size, the ceiling is now widened to a
    real (monotonic) 30s elapsed-time budget so it is spent mostly on
    genuine waiting. The schedule still sleeps 0.25s, 0.5s, 1.0s, 2.0s,
    2.0s, ... (capped at _max_sleep=2.0s), each individual sleep further
    clamped to never overshoot the 30s ceiling.

    Both the Write/Edit gate and the danger-bash gate call
    ``get_current_turn_message_for_validation`` without overriding these
    parameters, so changing the function defaults covers both gates
    uniformly — single source of truth, no drift between the two call sites.
    """

    def test_default_max_wait_seconds_is_30(self):
        import inspect

        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        sig = inspect.signature(get_current_turn_message_for_validation)
        assert sig.parameters["_max_wait_seconds"].default == 30.0

    def test_default_initial_sleep_is_quarter_second(self):
        import inspect

        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        sig = inspect.signature(get_current_turn_message_for_validation)
        assert sig.parameters["_initial_sleep"].default == 0.25

    def test_default_backoff_multiplier_is_2(self):
        import inspect

        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        sig = inspect.signature(get_current_turn_message_for_validation)
        assert sig.parameters["_backoff_multiplier"].default == 2.0

    def test_default_max_sleep_is_2_seconds(self):
        import inspect

        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        sig = inspect.signature(get_current_turn_message_for_validation)
        assert sig.parameters["_max_sleep"].default == 2.0

    def test_early_return_on_match_does_not_sleep(self, tmp_path, monkeypatch):
        """When the matching turn IS already flushed, the NEW default params
        must not sleep at all — the 30s ceiling is a MAX wait on the
        not-yet-flushed path, never a fixed per-edit delay."""
        import time as time_module

        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        sleep_calls = []
        monkeypatch.setattr(time_module, "sleep", lambda s: sleep_calls.append(s))

        transcript = _flushed_write(tmp_path)
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": TARGET, "content": CUR_CONTENT},
            tool_name="Write",
            # No override — exercises the REAL 30s-ceiling defaults to prove
            # early-return holds there too.
        )
        assert result is not None
        assert "INTENT:" in result
        assert (
            sleep_calls == []
        ), f"Expected zero sleep calls on first-attempt match; got {sleep_calls}"

    def test_backoff_schedule_and_hard_ceiling_bound_iteration_count(
        self, tmp_path, monkeypatch
    ):
        """Proves both halves of the #91 fix together using a fake monotonic
        clock (avoids wall-clock flakiness): sleep durations are
        non-decreasing until capped at _max_sleep, and the loop terminates
        (returns None) once the 30s ceiling is reached rather than looping
        forever."""
        import time as time_module

        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _lagged_write(tmp_path)  # current turn never flushes

        fake_now = [0.0]

        def fake_monotonic():
            return fake_now[0]

        sleep_calls = []

        def fake_sleep(seconds):
            sleep_calls.append(seconds)
            fake_now[0] += seconds

        monkeypatch.setattr(time_module, "monotonic", fake_monotonic)
        monkeypatch.setattr(time_module, "sleep", fake_sleep)

        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": TARGET, "content": CUR_CONTENT},
            tool_name="Write",
            _max_wait_seconds=30.0,
            _initial_sleep=0.25,
            _backoff_multiplier=2.0,
            _max_sleep=2.0,
        )

        assert result is None, "Must reject once the 30s ceiling is reached"
        assert len(sleep_calls) < 25, (
            f"Expected a small, bounded number of sleeps, got "
            f"{len(sleep_calls)}: {sleep_calls}"
        )
        # All sleeps except possibly the last must be non-decreasing until
        # capped at _max_sleep; the LAST sleep may be clamped smaller than
        # its uncapped backoff value because it is trimmed to fit exactly
        # within the remaining budget before the ceiling (never overshoot).
        body, last = sleep_calls[:-1], sleep_calls[-1]
        for prev, cur in zip(body, body[1:]):
            assert cur >= prev or cur == 2.0, (
                f"Sleep durations must be non-decreasing until capped at "
                f"_max_sleep=2.0; got {sleep_calls}"
            )
        assert 0 < last <= 2.0, (
            f"Final (remaining-clamped) sleep must be positive and never "
            f"exceed _max_sleep=2.0; got {sleep_calls}"
        )
        assert (
            max(sleep_calls) <= 2.0
        ), f"No sleep may exceed _max_sleep; got {sleep_calls}"
        assert sum(sleep_calls) <= 30.0 + 1e-9, (
            f"Total sleep time must not exceed the 30s ceiling; got "
            f"{sum(sleep_calls)}"
        )


# ---------------------------------------------------------------------------
# Test group 10: Bug #90 — re-issue fail-loop (stale byte-identical match)
# ---------------------------------------------------------------------------
#
# Root cause: when a command/edit is BLOCKED (e.g. no INTENT) and then
# re-issued with IDENTICAL tool_input, the transcript already contains the
# earlier blocked turn's tool_use (byte-identical content) plus its
# tool_result feedback by the time the re-issue's PreToolUse hook fires. If
# the re-issued (current) turn has not yet flushed, the LAST-match-wins scan
# finds the STALE earlier turn (which genuinely lacks INTENT — that's why it
# was blocked) and immediately returns "" instead of None, causing Stage 1 to
# reject with "no INTENT: declaration" even though the re-issued message
# DOES carry one. The fix: a matched turn is only trusted when it is the
# frontier of the transcript (nothing — of any role — follows it). A
# tool_result/user entry following the anchor proves the transcript has
# moved past that turn, so the match is stale and the function must signal
# "not found yet" (None) so the retry loop waits for the genuinely current
# turn instead.


BASH_REISSUE_INTENT = (
    "INTENT: Kill stalled pytest workers holding the DB lock.\n"
    "This targets only this session's own test processes."
)


def _tool_result_entry(text: str, tool_use_id: str = "toolu_default") -> dict:
    """A user-role tool_result entry — the feedback the harness appends
    after a tool call (blocked or executed) completes."""
    return {
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": text}
            ],
        }
    }


def _bash_reissue_lagged(tmp_path) -> str:
    """Turn A (blocked, NO INTENT) is flushed along with its tool_result
    feedback; Turn B (the re-issue, WITH INTENT) has NOT yet flushed."""
    return _write_transcript(
        [
            _asst("req_A", _tool_use_block("Bash", {"command": BASH_CMD}, "toolu_A")),
            _tool_result_entry(
                "BLOCKED: no INTENT: declaration found", tool_use_id="toolu_A"
            ),
            # Turn B (re-issue, WITH INTENT) NOT YET FLUSHED.
        ],
        tmp_path,
    )


def _bash_reissue_flushed(tmp_path) -> str:
    """Both Turn A (stale, blocked, no INTENT) and Turn B (current re-issue,
    WITH INTENT) are present — Turn B's own tool_use has NOT been executed
    yet (no tool_result carries its id), so it is not stale."""
    return _write_transcript(
        [
            _asst("req_A", _tool_use_block("Bash", {"command": BASH_CMD}, "toolu_A")),
            _tool_result_entry(
                "BLOCKED: no INTENT: declaration found", tool_use_id="toolu_A"
            ),
            _asst("req_B", _text_block(BASH_REISSUE_INTENT)),
            _asst("req_B", _tool_use_block("Bash", {"command": BASH_CMD}, "toolu_B")),
        ],
        tmp_path,
    )


WRITE_REISSUE_INTENT = (
    "INTENT: Modify foo.py to add new feature (re-issued after block).\n"
    "Test coverage: tests/test_foo.py::test_new"
)


def _write_reissue_lagged(tmp_path) -> str:
    """Turn A (blocked Write, NO INTENT, byte-identical content to the
    re-issue) is flushed with its tool_result; Turn B (re-issue, WITH
    INTENT) has NOT yet flushed."""
    return _write_transcript(
        [
            _asst(
                "req_A",
                _tool_use_block(
                    "Write", {"file_path": TARGET, "content": CUR_CONTENT}, "toolu_A"
                ),
            ),
            _tool_result_entry(
                "BLOCKED: no INTENT: declaration found", tool_use_id="toolu_A"
            ),
            # Turn B (re-issue, WITH INTENT) NOT YET FLUSHED.
        ],
        tmp_path,
    )


def _write_reissue_flushed(tmp_path) -> str:
    return _write_transcript(
        [
            _asst(
                "req_A",
                _tool_use_block(
                    "Write", {"file_path": TARGET, "content": CUR_CONTENT}, "toolu_A"
                ),
            ),
            _tool_result_entry(
                "BLOCKED: no INTENT: declaration found", tool_use_id="toolu_A"
            ),
            _asst("req_B", _text_block(WRITE_REISSUE_INTENT)),
            _asst(
                "req_B",
                _tool_use_block(
                    "Write", {"file_path": TARGET, "content": CUR_CONTENT}, "toolu_B"
                ),
            ),
        ],
        tmp_path,
    )


class TestReissueStaleMatchBug90:
    """Bug #90: a re-issued command/edit must never be validated against a
    STALE earlier attempt's byte-identical, INTENT-less turn."""

    def test_bash_reissue_stale_turn_not_matched_while_current_unflushed(
        self, tmp_path
    ):
        """RED reproduction: Turn A (stale, no INTENT) is the only match
        present; Turn B (current re-issue, WITH INTENT) has not flushed.

        Old buggy behavior: returns "" (matches stale Turn A, no INTENT in
        its text) -> Stage 1 falsely rejects with "no INTENT: declaration"
        even though the re-issued message DOES carry one.

        Fixed behavior: Turn A is followed by a tool_result entry, so it is
        NOT the transcript frontier -> the match is stale -> return None
        (transcript-not-ready), which correctly defers to the retry loop /
        fail-closed re-issue contract instead of a false Stage-1 reject.
        """
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _bash_reissue_lagged(tmp_path)
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"command": BASH_CMD},
            tool_name="Bash",
            _max_wait_seconds=0.0,
        )
        assert result is None, (
            f"Expected None (stale match must not be trusted) but got: {result!r}\n"
            "Old bug: matches the stale, INTENT-less prior blocked attempt "
            "and returns '' (empty), causing a false 'no INTENT' Stage-1 "
            "reject on the re-issued command."
        )

    def test_bash_reissue_finds_current_turn_once_flushed(self, tmp_path):
        """Once Turn B (the re-issue, WITH INTENT) has flushed and become
        the transcript frontier, it — not stale Turn A — must be returned."""
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _bash_reissue_flushed(tmp_path)
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"command": BASH_CMD},
            tool_name="Bash",
            _max_wait_seconds=0.0,
        )
        assert result is not None
        assert "INTENT:" in result, f"Expected INTENT: in result, got: {result!r}"
        assert (
            "stalled pytest workers" in result
        ), f"Expected Turn B's INTENT text, not stale Turn A; got: {result!r}"

    def test_write_reissue_stale_turn_not_matched_while_current_unflushed(
        self, tmp_path
    ):
        """Write/Edit gate variant of the same re-issue race."""
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _write_reissue_lagged(tmp_path)
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": TARGET, "content": CUR_CONTENT},
            tool_name="Write",
            _max_wait_seconds=0.0,
        )
        assert (
            result is None
        ), f"Write gate: stale re-issue match must not be trusted; got: {result!r}"

    def test_write_reissue_finds_current_turn_once_flushed(self, tmp_path):
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _write_reissue_flushed(tmp_path)
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": TARGET, "content": CUR_CONTENT},
            tool_name="Write",
            _max_wait_seconds=0.0,
        )
        assert result is not None
        assert "INTENT:" in result
        assert "re-issued after block" in result

    def test_genuinely_never_flushed_reissue_still_fails_closed_after_retries(
        self, tmp_path, monkeypatch
    ):
        """Reverse-timing case: the stale Turn A is present, but no NEW
        matching turn EVER appears within the bounded retry window (the
        re-issue genuinely never flushes in time). Must still return None —
        never silently fall back to the stale entry as a consolation match
        (that would just reproduce the bug under a different timing)."""
        import time as time_module

        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        monkeypatch.setattr(time_module, "sleep", lambda s: None)

        transcript = _bash_reissue_lagged(tmp_path)
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"command": BASH_CMD},
            tool_name="Bash",
            _max_wait_seconds=0.05,
        )
        assert result is None, (
            f"Must fail-closed (None) when nothing new ever flushes, never "
            f"fall back to the stale match; got: {result!r}"
        )

    def test_bash_reissue_recovers_within_retry_window_when_flushed_mid_wait(
        self, tmp_path, monkeypatch
    ):
        """End-to-end proof of the documented 2-attempt recovery contract:
        the bounded retry loop bridges the gap between the stale Turn A
        being present and Turn B (current re-issue, WITH INTENT) flushing
        partway through the wait window."""
        import pacemaker.transcript_reader as tr_mod
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _bash_reissue_lagged(tmp_path)
        calls = {"n": 0}

        def fake_sleep(_seconds):
            calls["n"] += 1
            if calls["n"] == 1:
                with open(transcript, "a") as f:
                    f.write(
                        json.dumps(_asst("req_B", _text_block(BASH_REISSUE_INTENT)))
                        + "\n"
                    )
                    f.write(
                        json.dumps(
                            _asst(
                                "req_B", _tool_use_block("Bash", {"command": BASH_CMD})
                            )
                        )
                        + "\n"
                    )

        monkeypatch.setattr(tr_mod.time, "sleep", fake_sleep)

        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"command": BASH_CMD},
            tool_name="Bash",
            _max_wait_seconds=5.0,
        )
        assert result is not None, "Must recover once Turn B flushes mid-wait"
        assert "INTENT:" in result
        assert "stalled pytest workers" in result
        assert calls["n"] >= 1, "Retry loop must have actually waited at least once"

    def test_single_occurrence_no_intent_still_blocks_immediately(self, tmp_path):
        """Non-regression: a SINGLE (non-duplicate) turn with no INTENT and
        nothing following it (genuine frontier) must still return ''
        immediately — the frontier check must not turn every no-INTENT match
        into a forced wait. This mirrors the real single-turn no-INTENT case
        (e.g. tests/test_intent_validation_failclosed_race.py's subagent
        no-INTENT case) which must remain an immediate Stage-1 block, not a
        multi-second retry-then-block."""
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _write_transcript(
            [
                _asst(
                    "req_ONLY",
                    _tool_use_block("Bash", {"command": BASH_CMD}),
                ),
            ],
            tmp_path,
        )
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"command": BASH_CMD},
            tool_name="Bash",
            _max_wait_seconds=0.0,
        )
        assert result == "", (
            f"Single genuinely-frontier no-INTENT turn must return '' "
            f"immediately (real Stage-1 block, not a stale-match false "
            f"reject); got: {result!r}"
        )


# ---------------------------------------------------------------------------
# Test group 11: Bug #90 v2 — multi-tool-call turn regression
# ---------------------------------------------------------------------------
#
# The v1 fix for bug #90 (staleness = "is this the literal last line of the
# transcript") was itself broken for a real, common case: a single message
# making MULTIPLE tool calls (e.g. two Edits) sharing one requestId. The
# FIRST tool's tool_result legitimately appears in the transcript before the
# SECOND tool's own PreToolUse hook fires (the harness processes tool calls
# within a batch sequentially), which the v1 frontier check misread as
# staleness for the second tool's still-unexecuted, INTENT-carrying match —
# producing a permanent, unrecoverable "transcript not ready" for that
# second tool (it can never become the literal frontier again, since the
# first tool's result is already and forever ahead of it). The v2 fix scopes
# staleness to the SPECIFIC matched tool_use's own id: only a tool_result
# carrying THAT exact id proves staleness; a sibling tool's result does not.


class TestMultiToolCallTurnBug90V2:
    """Bug #90 v2: a second (or later) tool call in a multi-tool-call turn
    must validate correctly even after an earlier sibling tool in the same
    turn has already completed and appended its own tool_result."""

    def test_second_tool_in_shared_turn_validates_after_first_completes(self, tmp_path):
        """RED reproduction: one assistant turn makes two Edit calls (Edit A,
        Edit B) sharing one requestId. Edit A has already executed (its
        tool_result is present). Edit B's PreToolUse hook now fires to
        validate the SAME turn's INTENT-carrying message for Edit B's own
        content.

        v1 bug: any trailing entry (Edit A's tool_result) marks the whole
        turn stale -> returns None forever for Edit B, an unrecoverable
        block despite a valid INTENT in the very same, current turn.

        v2 fix: staleness is scoped to Edit B's own tool_use id (no
        tool_result exists for it yet) -> returns the turn's message with
        INTENT intact.
        """
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _write_transcript(
            [
                _asst(
                    "req_multi",
                    _text_block(
                        "INTENT: Modify a.py and b.py to add logging calls.\n"
                        "Test coverage: tests/test_logging.py::test_both_files"
                    ),
                ),
                _asst(
                    "req_multi",
                    _tool_use_block(
                        "Edit",
                        {
                            "file_path": "a.py",
                            "old_string": "old_a",
                            "new_string": "new_a",
                        },
                        "toolu_editA",
                    ),
                ),
                _asst(
                    "req_multi",
                    _tool_use_block(
                        "Edit",
                        {
                            "file_path": "b.py",
                            "old_string": "old_b",
                            "new_string": "new_b",
                        },
                        "toolu_editB",
                    ),
                ),
                # Edit A already executed; its tool_result trails the turn.
                _tool_result_entry("ok", tool_use_id="toolu_editA"),
            ],
            tmp_path,
        )

        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={
                "file_path": "b.py",
                "old_string": "old_b",
                "new_string": "new_b",
            },
            tool_name="Edit",
            _max_wait_seconds=0.0,
        )
        assert result is not None and result != "", (
            f"Edit B must validate against its own (current, INTENT-carrying) "
            f"turn even though sibling Edit A's tool_result already trails "
            f"it in the transcript; got: {result!r}"
        )
        assert "INTENT:" in result
        assert "logging calls" in result

    def test_stale_reissue_still_correctly_rejected_alongside_multi_tool_fix(
        self, tmp_path
    ):
        """Non-regression: the v2 fix must not accidentally weaken the
        original bug #90 stale-reissue detection. A tool_use whose OWN id
        already has a tool_result must still be treated as stale, even when
        other unrelated tool_use/tool_result pairs exist earlier in the same
        transcript."""
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _write_transcript(
            [
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": BASH_CMD}, "toolu_stale"),
                ),
                _tool_result_entry(
                    "BLOCKED: no INTENT: declaration found", tool_use_id="toolu_stale"
                ),
                # Turn B (re-issue, WITH INTENT) NOT YET FLUSHED.
            ],
            tmp_path,
        )

        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"command": BASH_CMD},
            tool_name="Bash",
            _max_wait_seconds=0.0,
        )
        assert result is None, (
            f"A tool_use whose own id already has a tool_result must still "
            f"be rejected as stale; got: {result!r}"
        )


# ---------------------------------------------------------------------------
# Test group 12: Issue #91 (second pass, v2 simplification) — fixed-cost
# tail read replaces window-growth tail read
# ---------------------------------------------------------------------------
#
# Root cause (confirmed by direct measurement on a real 324MB/26,626-line
# incident transcript): the ORIGINAL implementation did a full sequential
# re-open + re-parse of the ENTIRE transcript on every single retry attempt,
# measured at 3.067s/attempt on that file. A first fix attempt replaced the
# full scan with a tail-read whose window DOUBLED (up to the full file size)
# whenever the anchor wasn't found in it. That "fix" was ITSELF measured to
# make the NOT-FOUND case WORSE than the original full scan (~8s/attempt on
# the same transcript) -- precisely because "not found" (the current turn
# hasn't flushed yet) is the case that exhausts the growing window on every
# single retry attempt. It also could never actually help: the current
# turn's tool_use is always at (or extremely near) the tail of the
# transcript -- if it isn't there yet, it hasn't been written, full stop, no
# amount of scanning further back changes that.
#
# The v2 design (this test group) is deliberately simpler: read a SINGLE
# fixed-size window from EOF (TAIL_READ_BYTES, never grown) and restrict the
# anchor search to the last LAST_N_TURNS_FOR_TOOL_MATCH logical assistant
# turns found in it. Cost is O(window size), flat regardless of total file
# size, for BOTH the found and not-found cases -- the not-found case (the
# one that was actually broken) is now just as cheap as the found case.


def _build_large_transcript_anchor_near_eof(
    tmp_path, num_padding: int = 15000, padding_size: int = 2000
) -> str:
    """~30MB synthetic transcript with the target Write tool_use near EOF.

    This mirrors the scale of the real incident transcript closely enough
    that a full-file scan takes a clearly measurable amount of time (the
    original full-scan implementation was benchmarked at ~0.18s on this
    exact fixture size during this investigation -- consistent with the
    live issue #91 evidence of ~9ms/MB), while staying fast enough to build
    and run in a unit test.
    """
    p = tmp_path / "large_transcript_near_eof.jsonl"
    padding_text = "x" * padding_size
    with open(str(p), "w") as f:
        for i in range(num_padding):
            f.write(json.dumps(_asst(f"req_pad_{i}", _text_block(padding_text))) + "\n")
        f.write(
            json.dumps(
                _asst(
                    "req_TARGET",
                    _text_block(
                        "INTENT: Modify large_file.py to add a feature.\n"
                        "Test coverage: tests/test_large.py::test_feature"
                    ),
                )
            )
            + "\n"
        )
        f.write(
            json.dumps(
                _asst(
                    "req_TARGET",
                    _tool_use_block(
                        "Write",
                        {
                            "file_path": "/project/large_file.py",
                            "content": "print('large')",
                        },
                    ),
                )
            )
            + "\n"
        )
    return str(p)


def _build_large_transcript_no_match(
    tmp_path, num_padding: int = 15000, padding_size: int = 2000
) -> str:
    """Same ~30MB scale as _build_large_transcript_anchor_near_eof, but the
    target tool_use is NEVER present anywhere in the file -- the "current
    turn hasn't flushed yet" scenario that the original window-growth design
    made catastrophically slow (it doubled the window all the way to the
    full file size on every retry attempt, since a match could never be
    found)."""
    p = tmp_path / "large_transcript_no_match.jsonl"
    padding_text = "x" * padding_size
    with open(str(p), "w") as f:
        for i in range(num_padding):
            f.write(json.dumps(_asst(f"req_pad_{i}", _text_block(padding_text))) + "\n")
    return str(p)


class TestFixedCostTailRead:
    """Issue #91 (second pass, v2 simplification): _find_turn_matching_tool_input
    reads a SINGLE fixed-size tail window (never grown) and restricts the
    anchor search to the last LAST_N_TURNS_FOR_TOOL_MATCH logical assistant
    turns. This proves the fast path stays fast regardless of file size for
    BOTH the found-near-EOF case and the not-found case (the one the
    window-growth design actually broke), and documents the deliberate
    tradeoff that a match placed beyond the fixed window is no longer
    recoverable by growing (it simply isn't found -- by design, since that
    scenario doesn't occur in practice: the anchor is always near EOF)."""

    def test_near_eof_anchor_completes_fast_regardless_of_file_size(self, tmp_path):
        """Real-incident scale: a ~30MB transcript with the target tool_use
        near EOF. The original full-scan implementation measured ~0.18s on
        this exact fixture size (benchmarked directly during this
        investigation) -- consistent with the 3.067s/324MB ratio reported
        in the live issue #91 evidence. The fixed-cost tail-read
        implementation must complete in a small fraction of that time."""
        import time as time_module

        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        transcript = _build_large_transcript_anchor_near_eof(tmp_path)
        target_input = {
            "file_path": "/project/large_file.py",
            "content": "print('large')",
        }

        t0 = time_module.perf_counter()
        result = _find_turn_matching_tool_input(transcript, target_input, "Write")
        elapsed = time_module.perf_counter() - t0

        assert (
            result is not None and result != ""
        ), f"Expected the near-EOF anchor's INTENT text; got: {result!r}"
        assert "add a feature" in result
        assert elapsed < 0.1, (
            f"Fixed-cost tail-read must complete well under 100ms regardless "
            f"of file size when the anchor is near EOF; took {elapsed:.4f}s "
            f"(original full-scan implementation measured ~0.18s on this "
            f"exact fixture size)"
        )

    def test_not_found_case_completes_fast_regardless_of_file_size(self, tmp_path):
        """THE case that was actually broken by the window-growth design: no
        matching tool_use exists anywhere in a ~30MB transcript (current
        turn genuinely hasn't flushed). Direct measurement of the OLD
        (pre-this-fix, growing-window) implementation on this exact fixture
        scale showed ~0.56s -- because "not found" is exactly the scenario
        that exhausts the doubling window all the way to the full file size
        on every single call, and the live issue #91 incident showed this
        scaling to ~8s/attempt on a real 324MB transcript. The fixed-cost
        design must stay well under 100ms here too, since it never grows the
        window regardless of whether a match is found."""
        import time as time_module

        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        transcript = _build_large_transcript_no_match(tmp_path)
        target_input = {
            "file_path": "/project/never_written.py",
            "content": "this content never appears in the transcript",
        }

        t0 = time_module.perf_counter()
        result = _find_turn_matching_tool_input(transcript, target_input, "Write")
        elapsed = time_module.perf_counter() - t0

        assert result is None, f"Expected no match; got: {result!r}"
        assert elapsed < 0.1, (
            f"Fixed-cost tail-read must complete well under 100ms on the "
            f"NOT-FOUND path regardless of file size; took {elapsed:.4f}s "
            f"(the growing-window design this replaces measured ~0.56s on "
            f"this exact fixture scale, and ~8s/attempt on a real 324MB "
            f"transcript -- this was the actual perf bug this rewrite fixes)"
        )

    def test_anchor_beyond_tail_window_deliberately_not_found(self, tmp_path):
        """Documents the deliberate tradeoff: unlike the old growing-window
        design (which would keep doubling until it found a match anywhere in
        the file), the fixed-cost design does NOT grow. A match placed near
        the START of a transcript, with enough padding after it to push it
        beyond the fixed tail window, is simply not found (None) -- by
        design, since this scenario is not believed to occur in practice
        (the anchor -- the turn currently being validated -- is always near
        EOF; if it were genuinely further back, the transcript has moved on
        and the retry loop should keep waiting for the REAL current turn,
        not resurrect an old one)."""
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        p = tmp_path / "beyond_window_transcript.jsonl"
        padding_text = "y" * 2000
        target_input = {"command": "echo rare-anchor-case"}
        with open(str(p), "w") as f:
            # Target anchor near the START of the file.
            f.write(
                json.dumps(
                    _asst(
                        "req_RARE",
                        _text_block(
                            "INTENT: Run a diagnostic echo command.\n"
                            "This is a read-only diagnostic, no side effects."
                        ),
                    )
                )
                + "\n"
            )
            f.write(
                json.dumps(_asst("req_RARE", _tool_use_block("Bash", target_input)))
                + "\n"
            )
            # ~2MB of padding AFTER the anchor pushes it well beyond the
            # fixed TAIL_READ_BYTES window -- this is now expected to be
            # NOT FOUND, a deliberate behavior change from the old
            # growing-window design.
            for i in range(1100):
                f.write(
                    json.dumps(_asst(f"req_pad_{i}", _text_block(padding_text))) + "\n"
                )

        result = _find_turn_matching_tool_input(p.as_posix(), target_input, "Bash")

        assert result is None, (
            f"Fixed-cost design deliberately does NOT grow the window to "
            f"find an anchor beyond it (unlike the old growing-window "
            f"design); got: {result!r}"
        )

    def test_small_transcript_unaffected_by_tail_read_change(self, tmp_path):
        """Non-regression sanity: for a small transcript (well under
        TAIL_READ_BYTES), behavior is unchanged from the original full scan."""
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _flushed_write(tmp_path)
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": TARGET, "content": CUR_CONTENT},
            tool_name="Write",
            _max_wait_seconds=0.0,
        )
        assert result is not None
        assert "INTENT:" in result
        assert "new feature" in result

    def test_staleness_gate_safe_when_anchor_in_tail_window(self, tmp_path):
        """Invariant check (confirms existing correct behavior, not a bug):
        transcripts are append-only, so a tool_result for the anchor's own
        tool_use id is always at a HIGHER byte offset than the tool_use
        itself -- if the anchor is inside the fixed tail window (which
        always extends to true EOF), its tool_result is too. Seeds a stale
        tool_result for the anchor's id and confirms the match is still
        correctly detected as stale (returns None) even though both entries
        are comfortably inside the fixed-size window."""
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        p = tmp_path / "stale_in_window_transcript.jsonl"
        target_input = {"command": "echo stale-check"}

        with open(str(p), "w") as f:
            f.write(
                json.dumps(
                    _asst(
                        "req_STALE",
                        _text_block("INTENT: Run a diagnostic echo command."),
                    )
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    _asst(
                        "req_STALE",
                        _tool_use_block("Bash", target_input, tool_id="toolu_stale"),
                    )
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "message": {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_stale",
                                    "content": "stale-check\n",
                                }
                            ],
                        }
                    }
                )
                + "\n"
            )

        result = _find_turn_matching_tool_input(p.as_posix(), target_input, "Bash")

        assert result is None, (
            "A tool_result already present for the anchor's own tool_use id "
            "means it was already executed once -- must return None (stale) "
            f"even though the whole turn is inside the tail window; got: "
            f"{result!r}"
        )


class TestLastNLogicalTurnsScoping:
    """Proves the "last N logical turns, not raw lines" semantics explicitly
    (user-mandated design point): grouping is by requestId (contiguous
    same-requestId entries = one logical turn), and the search is scoped to
    the last LAST_N_TURNS_FOR_TOOL_MATCH=2 such turns -- not simply the last
    2 raw JSONL lines."""

    def test_match_in_turn_three_back_is_not_found(self, tmp_path):
        """A match that exists ONLY in the 3rd-most-recent logical turn
        (with 2 more recent, non-matching turns after it) must NOT be found
        -- proving the search is genuinely scoped to the last 2 logical
        turns, not "keep looking until something matches"."""
        transcript = _write_transcript(
            [
                _asst(
                    "req_OLD",
                    _text_block("INTENT: The old, three-turns-back write."),
                ),
                _asst(
                    "req_OLD",
                    _tool_use_block(
                        "Write", {"file_path": TARGET, "content": "old-content"}
                    ),
                ),
                _asst(
                    "req_MID",
                    _text_block("INTENT: An unrelated middle turn."),
                ),
                _asst(
                    "req_MID",
                    _tool_use_block("Bash", {"command": "echo unrelated-middle-turn"}),
                ),
                _asst(
                    "req_LAST",
                    _text_block("INTENT: An unrelated most-recent turn."),
                ),
                _asst(
                    "req_LAST",
                    _tool_use_block("Bash", {"command": "echo unrelated-last-turn"}),
                ),
            ],
            tmp_path,
        )
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": TARGET, "content": "old-content"},
            tool_name="Write",
            _max_wait_seconds=0.0,
        )
        assert result is None, (
            f"A match only in the 3rd-most-recent logical turn must not be "
            f"found (search is scoped to the last 2 logical turns); got: "
            f"{result!r}"
        )

    def test_match_in_second_most_recent_turn_is_found(self, tmp_path):
        """Symmetric positive case: a match in the SECOND-most-recent
        logical turn (with one more recent, non-matching turn after it)
        MUST still be found -- N=2, not N=1."""
        transcript = _write_transcript(
            [
                _asst(
                    "req_TARGET",
                    _text_block("INTENT: Modify foo.py via the second-to-last turn."),
                ),
                _asst(
                    "req_TARGET",
                    _tool_use_block(
                        "Write", {"file_path": TARGET, "content": "target-content"}
                    ),
                ),
                _asst(
                    "req_LAST",
                    _text_block("INTENT: An unrelated most-recent turn."),
                ),
                _asst(
                    "req_LAST",
                    _tool_use_block("Bash", {"command": "echo unrelated-last-turn"}),
                ),
            ],
            tmp_path,
        )
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": TARGET, "content": "target-content"},
            tool_name="Write",
            _max_wait_seconds=0.0,
        )
        assert result is not None and result != "", (
            f"A match in the second-most-recent logical turn must be found "
            f"(N=2, not N=1); got: {result!r}"
        )
        assert "second-to-last turn" in result

    def test_grouping_is_by_requestid_not_raw_line_count(self, tmp_path):
        """A single logical turn spanning MANY raw JSONL lines (thinking +
        multiple tool_use blocks all sharing one requestId) must still count
        as exactly ONE of the "last 2" turns -- proving grouping is by
        requestId, not by counting raw lines."""
        transcript = _write_transcript(
            [
                _asst("req_SOLO", _text_block("INTENT: A single sprawling turn.")),
                _asst(
                    "req_SOLO",
                    _tool_use_block(
                        "Bash", {"command": "echo first-tool-in-turn"}, "toolu_first"
                    ),
                ),
                _asst(
                    "req_SOLO",
                    _tool_use_block(
                        "Bash", {"command": "echo second-tool-in-turn"}, "toolu_second"
                    ),
                ),
                _asst(
                    "req_SOLO",
                    _tool_use_block(
                        "Write",
                        {"file_path": TARGET, "content": "solo-turn-content"},
                        "toolu_third",
                    ),
                ),
            ],
            tmp_path,
        )
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": TARGET, "content": "solo-turn-content"},
            tool_name="Write",
            _max_wait_seconds=0.0,
        )
        assert result is not None and result != ""
        assert "sprawling turn" in result


class TestRetryLoopCeilingDoesNotOvershootOnLargeTranscript:
    """Requirement (c): proves the full retry loop respects its 30s ceiling
    in the guaranteed-not-found case even on a large transcript, and does
    NOT overshoot to 33+ seconds the way the FIRST rewrite attempt
    (growing-window tail read) did. That overshoot happened because a
    single slow attempt (window doubled all the way to full file size, ~8s
    on a real 324MB transcript) could push wall-clock time past the ceiling
    by however long that one attempt took -- the elapsed check only runs
    BETWEEN attempts, not during one. With the fixed-cost design, every
    attempt is cheap regardless of file size, so this overshoot risk is
    eliminated: uses a fake monotonic clock (like the existing ceiling test)
    so the ceiling-tripping logic is exercised deterministically, while
    independently measuring REAL wall-clock time around the whole call to
    prove the large file's actual read cost stays negligible."""

    def test_large_transcript_not_found_respects_ceiling_without_overshoot(
        self, tmp_path, monkeypatch
    ):
        import time as time_module

        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _build_large_transcript_no_match(tmp_path)
        target_input = {
            "file_path": "/project/never_written.py",
            "content": "this content never appears anywhere",
        }

        fake_now = [0.0]

        def fake_monotonic():
            return fake_now[0]

        def fake_sleep(seconds):
            fake_now[0] += seconds

        monkeypatch.setattr(time_module, "monotonic", fake_monotonic)
        monkeypatch.setattr(time_module, "sleep", fake_sleep)

        # perf_counter is never monkeypatched (only monotonic/sleep are
        # above), so this measures true wall-clock time.
        real_start = time_module.perf_counter()
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input=target_input,
            tool_name="Write",
            _max_wait_seconds=30.0,
            _initial_sleep=0.25,
            _backoff_multiplier=2.0,
            _max_sleep=2.0,
        )
        real_elapsed = time_module.perf_counter() - real_start

        assert result is None, "Must reject once the 30s (fake) ceiling is reached"
        # The fake clock says ~30s elapsed logically, but REAL wall-clock
        # time consumed must stay tiny -- proving no single attempt's read
        # cost can push the loop past its ceiling, unlike the growing-window
        # design this replaces (which measured actual multi-second overshoot
        # on a real 324MB transcript).
        assert real_elapsed < 1.0, (
            f"Real wall-clock time for the whole retry loop on a large "
            f"not-found transcript must stay well under 1s (proving each "
            f"attempt is cheap and bounded regardless of file size); took "
            f"{real_elapsed:.3f}s real time (fake ceiling was 30s)"
        )


class TestDiagnosticsObservability:
    """Issue #91: when the retry loop gives up (returns None), attempt-count
    and real elapsed-seconds must be surfaced via the optional _diagnostics
    dict so a future incident shows real numbers in usage.db instead of
    requiring someone to manually benchmark the transcript file after the
    fact (as was done during this investigation)."""

    def test_diagnostics_populated_when_giving_up(self, tmp_path):
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _lagged_write(tmp_path)  # current turn never flushes
        diagnostics: dict = {}

        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": TARGET, "content": CUR_CONTENT},
            tool_name="Write",
            _max_wait_seconds=0.0,
            _diagnostics=diagnostics,
        )

        assert result is None
        assert diagnostics.get("attempts") == 1
        assert isinstance(diagnostics.get("elapsed_seconds"), float)
        assert diagnostics["elapsed_seconds"] >= 0.0

    def test_diagnostics_populated_on_success(self, tmp_path):
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _flushed_write(tmp_path)
        diagnostics: dict = {}

        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": TARGET, "content": CUR_CONTENT},
            tool_name="Write",
            _max_wait_seconds=0.0,
            _diagnostics=diagnostics,
        )

        assert result is not None
        assert diagnostics.get("attempts") == 1
        assert isinstance(diagnostics.get("elapsed_seconds"), float)

    def test_diagnostics_none_by_default_no_error(self, tmp_path):
        """When _diagnostics is not passed (the default None), the function
        must behave exactly as before -- no error, no side effect."""
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _flushed_write(tmp_path)
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": TARGET, "content": CUR_CONTENT},
            tool_name="Write",
            _max_wait_seconds=0.0,
        )
        assert result is not None
