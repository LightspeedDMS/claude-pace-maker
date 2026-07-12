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


def _tool_use_block(name: str, inp: dict) -> dict:
    return {"type": "tool_use", "name": name, "input": inp}


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
            _max_retries=0,
            _retry_sleep=0.0,
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
            _max_retries=0,
            _retry_sleep=0.0,
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
            _max_retries=0,
            _retry_sleep=0.0,
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
            _max_retries=0,
            _retry_sleep=0.0,
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
            _max_retries=0,
            _retry_sleep=0.0,
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
            _max_retries=0,
            _retry_sleep=0.0,
        )
        assert result is not None
        assert "INTENT:" in result


# ---------------------------------------------------------------------------
# Test group 3: bounded retry (Messi Rule 14)
# ---------------------------------------------------------------------------


class TestBoundedRetry:
    """The retry loop MUST have provable termination: exactly _max_retries+1 reads."""

    def test_returns_none_after_retries_when_never_flushed(self, tmp_path):
        """After all retries with _retry_sleep=0, returns None (never hangs)."""
        import time

        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _lagged_write(tmp_path)
        t0 = time.monotonic()
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": TARGET, "content": CUR_CONTENT},
            tool_name="Write",
            _max_retries=5,
            _retry_sleep=0.0,
        )
        elapsed = time.monotonic() - t0

        assert result is None, "Must return None after exhausting retries"
        # With _retry_sleep=0 the total time should be negligible
        assert (
            elapsed < 5.0
        ), f"Retry loop took {elapsed:.2f}s — possible unbounded loop"

    def test_finds_match_on_first_attempt_when_flushed(self, tmp_path):
        """No wasted retries when turn is already in transcript."""
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _flushed_write(tmp_path)
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": TARGET, "content": CUR_CONTENT},
            tool_name="Write",
            _max_retries=10,
            _retry_sleep=0.0,
        )
        # Should return on first attempt, not retry
        assert result is not None
        assert "INTENT:" in result

    def test_zero_retries_single_attempt(self, tmp_path):
        """_max_retries=0 means exactly one attempt, then None."""
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _lagged_write(tmp_path)
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"file_path": TARGET, "content": CUR_CONTENT},
            tool_name="Write",
            _max_retries=0,
            _retry_sleep=0.0,
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
            _max_retries=0,
            _retry_sleep=0.0,
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
            _max_retries=0,
            _retry_sleep=0.0,
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
            _max_retries=0,
            _retry_sleep=0.0,
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
        integration check of the missing-file path; only the literal
        time.sleep wait is neutralized (stdlib timing call, not business
        logic) so the test stays instant despite the ~5s retry budget
        (coordinator refinement, v2.33.2 — see
        TestRetryDefaultsWidenedTo5Seconds below for dedicated timing
        coverage of the retry loop itself).
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

        v2.33.2: the bash call site no longer pins its own ``_max_retries``
        override — it now shares the same ~5s default budget as the
        Write/Edit gate (single source of truth). The real retry loop still
        runs (missing file → None on every attempt); only the literal
        time.sleep wait is neutralized so this test stays instant.
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
        ):
            mock_stdin.read.return_value = hook_data
            result = run_pre_tool_hook()

        assert (
            result.get("decision") == "block"
        ), f"git reset --hard must fail-CLOSED when transcript not ready; got: {result}"


# ---------------------------------------------------------------------------
# Test group 9: ~5s retry-window defaults (coordinator refinement, v2.33.2)
# ---------------------------------------------------------------------------


class TestRetryDefaultsWidenedTo5Seconds:
    """The default retry window was widened from ~1s (10 x 0.1s) to ~5s
    (20 x 0.25s, 21 reads total) so more in-window transcript flushes are
    caught before the gate falls back to fail-closed + re-issue. Fewer,
    longer-spaced reads avoid hammering large (~27MB on busy sessions)
    transcripts that get re-read on every attempt.

    Both the Write/Edit gate and the danger-bash gate call
    ``get_current_turn_message_for_validation`` without overriding these
    parameters (the bash gate's old explicit ``_max_retries=10`` override was
    removed in v2.33.2), so changing the function default covers both gates
    uniformly — single source of truth, no drift between the two call sites.
    """

    def test_default_max_retries_is_20(self):
        import inspect

        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        sig = inspect.signature(get_current_turn_message_for_validation)
        assert sig.parameters["_max_retries"].default == 20

    def test_default_retry_sleep_is_quarter_second(self):
        import inspect

        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        sig = inspect.signature(get_current_turn_message_for_validation)
        assert sig.parameters["_retry_sleep"].default == 0.25

    def test_default_total_wait_is_5_seconds(self):
        """20 retries * 0.25s sleep = 5.0s total max wait (Messi Rule 14:
        provable termination bound, well under the 60s hook timeout)."""
        import inspect

        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        sig = inspect.signature(get_current_turn_message_for_validation)
        max_retries = sig.parameters["_max_retries"].default
        retry_sleep = sig.parameters["_retry_sleep"].default
        total_wait = max_retries * retry_sleep
        assert total_wait == 5.0, (
            f"Expected exactly 5.0s total max wait, got {total_wait}s "
            f"({max_retries} retries x {retry_sleep}s sleep)"
        )

    def test_default_total_reads_is_21(self):
        """range(_max_retries + 1) => 21 attempts with the new defaults —
        fewer reads than a naive 50x0.1s scheme would require, per the
        coordinator's explicit guidance to avoid hammering large transcripts."""
        import inspect

        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        sig = inspect.signature(get_current_turn_message_for_validation)
        max_retries = sig.parameters["_max_retries"].default
        assert max_retries + 1 == 21

    def test_early_return_on_match_does_not_sleep(self, tmp_path, monkeypatch):
        """When the matching turn IS already flushed, the NEW default params
        must not sleep at all — the 5s figure is a MAX wait on the
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
            # No _max_retries/_retry_sleep override — exercises the REAL
            # ~5s-ceiling defaults to prove early-return holds there too.
        )
        assert result is not None
        assert "INTENT:" in result
        assert (
            sleep_calls == []
        ), f"Expected zero sleep calls on first-attempt match; got {sleep_calls}"


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


def _tool_result_entry(text: str) -> dict:
    """A user-role tool_result entry — the feedback the harness appends
    after a tool call (blocked or executed) completes."""
    return {
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "content": text}],
        }
    }


def _bash_reissue_lagged(tmp_path) -> str:
    """Turn A (blocked, NO INTENT) is flushed along with its tool_result
    feedback; Turn B (the re-issue, WITH INTENT) has NOT yet flushed."""
    return _write_transcript(
        [
            _asst("req_A", _tool_use_block("Bash", {"command": BASH_CMD})),
            _tool_result_entry("BLOCKED: no INTENT: declaration found"),
            # Turn B (re-issue, WITH INTENT) NOT YET FLUSHED.
        ],
        tmp_path,
    )


def _bash_reissue_flushed(tmp_path) -> str:
    """Both Turn A (stale, blocked, no INTENT) and Turn B (current re-issue,
    WITH INTENT) are present — Turn B is the transcript frontier."""
    return _write_transcript(
        [
            _asst("req_A", _tool_use_block("Bash", {"command": BASH_CMD})),
            _tool_result_entry("BLOCKED: no INTENT: declaration found"),
            _asst("req_B", _text_block(BASH_REISSUE_INTENT)),
            _asst("req_B", _tool_use_block("Bash", {"command": BASH_CMD})),
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
                _tool_use_block("Write", {"file_path": TARGET, "content": CUR_CONTENT}),
            ),
            _tool_result_entry("BLOCKED: no INTENT: declaration found"),
            # Turn B (re-issue, WITH INTENT) NOT YET FLUSHED.
        ],
        tmp_path,
    )


def _write_reissue_flushed(tmp_path) -> str:
    return _write_transcript(
        [
            _asst(
                "req_A",
                _tool_use_block("Write", {"file_path": TARGET, "content": CUR_CONTENT}),
            ),
            _tool_result_entry("BLOCKED: no INTENT: declaration found"),
            _asst("req_B", _text_block(WRITE_REISSUE_INTENT)),
            _asst(
                "req_B",
                _tool_use_block("Write", {"file_path": TARGET, "content": CUR_CONTENT}),
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
            _max_retries=0,
            _retry_sleep=0.0,
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
            _max_retries=0,
            _retry_sleep=0.0,
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
            _max_retries=0,
            _retry_sleep=0.0,
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
            _max_retries=0,
            _retry_sleep=0.0,
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
            _max_retries=5,
            _retry_sleep=0.0,
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
            _max_retries=3,
            _retry_sleep=0.0,
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
            _max_retries=0,
            _retry_sleep=0.0,
        )
        assert result == "", (
            f"Single genuinely-frontier no-INTENT turn must return '' "
            f"immediately (real Stage-1 block, not a stale-match false "
            f"reject); got: {result!r}"
        )
