"""
Issue #93 regression tests: the danger-bash gate discarded its own
tool-matched anchor result (only checking `is None`) and instead built the
message it actually validated from a separate, UNANCHORED
``get_last_n_messages_for_validation(n=4)`` call. Combined with the anchor's
30s retry ceiling (shared with the Write/Edit gate) being spent on a wait
that, empirically, never resolved for Bash, every dangerous command that hit
the not-yet-flushed race paid a full 30s stall before hard-blocking with no
recovery path.

This module covers the issue #93 fix:

1. ``_find_turn_matching_tool_input`` / ``get_current_turn_message_for_validation``
   now disambiguate "no matching tool_use found" (not_found) from "matching
   tool_use found but already has its own tool_result" (stale) via an
   additive, backward-compatible ``_outcome``/``_diagnostics["outcome"]``
   channel — previously both cases collapsed into an indistinguishable
   ``None``.
2. The danger-bash gate now CONSUMES the anchor instead of discarding it:
   found -> anchor text is the validated message; stale -> the stale turn's
   own text is accepted (byte-identical command match makes this safe);
   not_found -> block (unchanged reason), now with a drastically reduced
   wait ceiling (3.0s, not 30s) and an ``outcome`` field in telemetry.
3. Security regression: a stale turn's INTENT belongs to the SAME command
   only, by construction of ``_tool_input_matches``'s byte-identical
   comparison. A stale/prior turn for a DIFFERENT command must never be
   accepted for the current command.

MOCKING RATIONALE (mirrors tests/test_intent_validation_failclosed_race.py)
============================================================================
Sections A/B exercise the REAL transcript_reader algorithm against real
JSONL fixtures (outcome disambiguation is a property of that algorithm).
Section C isolates the HOOK's reaction to each possible
(anchor-string, outcome) pair by mocking
``pacemaker.hook.get_current_turn_message_for_validation`` directly, plus a
couple of full end-to-end (real transcript_reader) tests for the security
regression and the reduced-ceiling requirement, which are best proven
against the real algorithm rather than a mock of it.
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


# ---------------------------------------------------------------------------
# JSONL transcript builder helpers (mirrors tests/test_transcript_staleness_fix.py)
# ---------------------------------------------------------------------------


def _asst(request_id: Optional[str], block: dict) -> dict:
    entry = {"message": {"role": "assistant", "content": [block]}}
    if request_id is not None:
        entry["requestId"] = request_id
    return entry


def _text_block(t: str) -> dict:
    return {"type": "text", "text": t}


def _tool_use_block(name: str, inp: dict, tool_id: str = "toolu_default") -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": inp}


def _tool_result_entry(text: str, tool_use_id: str = "toolu_default") -> dict:
    return {
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": text}
            ],
        }
    }


def _write_transcript(lines: List[dict], tmp_path) -> str:
    p = tmp_path / "transcript.jsonl"
    with open(str(p), "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return str(p)


BASH_CMD = "chmod 777 /tmp/sandbox/probe.txt"
BASH_INTENT = (
    "INTENT: Loosen permissions on the sandbox probe file for a local test.\n"
    "This affects only the throwaway sandbox file, not real project data."
)


# ---------------------------------------------------------------------------
# Section A: _find_turn_matching_tool_input outcome disambiguation
# ---------------------------------------------------------------------------


class TestOutcomeDisambiguation:
    """The `_outcome` dict must distinguish not_found / stale / found —
    previously all non-"found" cases collapsed into an identical None with
    no way to tell them apart (the ambiguity issue #93 calls out as having
    materially slowed the original investigation)."""

    def test_not_found_when_no_matching_tool_use(self, tmp_path):
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        transcript = _write_transcript(
            [_asst("req_A", _text_block("unrelated text, no Bash tool_use"))],
            tmp_path,
        )
        outcome: dict = {}
        result = _find_turn_matching_tool_input(
            transcript, {"command": BASH_CMD}, "Bash", _outcome=outcome
        )
        assert result is None
        assert outcome.get("outcome") == "not_found"
        assert "stale_text" not in outcome

    def test_not_found_when_file_missing(self, tmp_path):
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        missing = str(tmp_path / "does_not_exist.jsonl")
        outcome: dict = {}
        result = _find_turn_matching_tool_input(
            missing, {"command": BASH_CMD}, "Bash", _outcome=outcome
        )
        assert result is None
        assert outcome.get("outcome") == "not_found"

    def test_stale_when_matching_tool_use_already_has_tool_result(self, tmp_path):
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        transcript = _write_transcript(
            [
                _asst("req_A", _text_block(BASH_INTENT)),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": BASH_CMD}, "toolu_A"),
                ),
                _tool_result_entry("done", tool_use_id="toolu_A"),
            ],
            tmp_path,
        )
        outcome: dict = {}
        result = _find_turn_matching_tool_input(
            transcript, {"command": BASH_CMD}, "Bash", _outcome=outcome
        )
        assert result is None, "Stale match must still return None (unchanged contract)"
        assert outcome.get("outcome") == "stale"
        assert "INTENT:" in outcome.get("stale_text", "")
        assert "sandbox probe file" in outcome["stale_text"]

    def test_stale_text_has_no_intent_marker_when_turn_text_lacks_one(self, tmp_path):
        """A stale turn with NO INTENT text must never surface an INTENT
        marker via stale_text — hook.py's Phase 1 must see the real absence
        of INTENT, not a false positive. (Post-FIX-1: since this turn's TEXT
        has no INTENT marker, stale_text is gated to "" — see the
        intent-marker gate in the stale branch of
        ``_find_turn_matching_tool_input``.)"""
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        transcript = _write_transcript(
            [
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": BASH_CMD}, "toolu_A"),
                ),
                _tool_result_entry("done", tool_use_id="toolu_A"),
            ],
            tmp_path,
        )
        outcome: dict = {}
        result = _find_turn_matching_tool_input(
            transcript, {"command": BASH_CMD}, "Bash", _outcome=outcome
        )
        assert result is None
        assert outcome.get("outcome") == "stale"
        # stale_text must not itself carry an INTENT marker (there was none)
        import re

        assert not re.search(r"(?i)\bintent\s*:", outcome.get("stale_text", ""))

    def test_found_outcome_when_matching_frontier_tool_use(self, tmp_path):
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        transcript = _write_transcript(
            [
                _asst("req_A", _text_block(BASH_INTENT)),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": BASH_CMD}, "toolu_A"),
                ),
            ],
            tmp_path,
        )
        outcome: dict = {}
        result = _find_turn_matching_tool_input(
            transcript, {"command": BASH_CMD}, "Bash", _outcome=outcome
        )
        assert result is not None
        assert "INTENT:" in result
        assert outcome.get("outcome") == "found"
        assert "stale_text" not in outcome

    def test_outcome_none_default_no_error(self, tmp_path):
        """Backward compatibility: omitting _outcome must not error or change
        behavior (purely additive)."""
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        transcript = _write_transcript(
            [
                _asst("req_A", _text_block(BASH_INTENT)),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": BASH_CMD}, "toolu_A"),
                ),
            ],
            tmp_path,
        )
        result = _find_turn_matching_tool_input(
            transcript, {"command": BASH_CMD}, "Bash"
        )
        assert result is not None
        assert "INTENT:" in result


# ---------------------------------------------------------------------------
# Section B: get_current_turn_message_for_validation diagnostics threading
# ---------------------------------------------------------------------------


class TestDiagnosticsOutcomeThreading:
    """get_current_turn_message_for_validation's _diagnostics dict must
    surface the same outcome/stale_text info on give-up, so hook.py can act
    on it without re-implementing the retry loop."""

    def test_diagnostics_outcome_not_found_on_giveup(self, tmp_path):
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _write_transcript(
            [_asst("req_A", _text_block("nothing relevant here"))], tmp_path
        )
        diagnostics: dict = {}
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"command": BASH_CMD},
            tool_name="Bash",
            _max_wait_seconds=0.0,
            _diagnostics=diagnostics,
        )
        assert result is None
        assert diagnostics.get("outcome") == "not_found"

    def test_diagnostics_outcome_stale_on_giveup_with_stale_text(self, tmp_path):
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _write_transcript(
            [
                _asst("req_A", _text_block(BASH_INTENT)),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": BASH_CMD}, "toolu_A"),
                ),
                _tool_result_entry("done", tool_use_id="toolu_A"),
            ],
            tmp_path,
        )
        diagnostics: dict = {}
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"command": BASH_CMD},
            tool_name="Bash",
            _max_wait_seconds=0.0,
            _diagnostics=diagnostics,
        )
        assert result is None
        assert diagnostics.get("outcome") == "stale"
        assert "INTENT:" in diagnostics.get("stale_text", "")

    def test_diagnostics_outcome_found_on_success(self, tmp_path):
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _write_transcript(
            [
                _asst("req_A", _text_block(BASH_INTENT)),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": BASH_CMD}, "toolu_A"),
                ),
            ],
            tmp_path,
        )
        diagnostics: dict = {}
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"command": BASH_CMD},
            tool_name="Bash",
            _max_wait_seconds=0.0,
            _diagnostics=diagnostics,
        )
        assert result is not None
        assert diagnostics.get("outcome") == "found"


class TestTwoInterveningTurnsDocumentsUnboundedRounds:
    """Locks in the real (unbounded) limitation documented in hook.py's
    corrected comment (issue #93 code-review item 1, replacing a disproven
    "worst case ~3 rounds / Messi Rule 14" claim): the not_found ->
    re-issue -> stale-recovery round-trip only converges while the ORIGINAL
    blocked attempt's tool_use remains within the last
    LAST_N_TURNS_FOR_TOOL_MATCH (2) logical assistant turns by the time the
    byte-identical re-issue's own search runs. With 2 (or more) intervening
    assistant turns between the blocked attempt and the re-issue, the
    blocked attempt scrolls outside that window and the outcome is
    "not_found" again -- the cycle does not converge. Mirrors the
    document-the-gap pattern of TestMultiToolTurnFix2SemanticGapDocumented."""

    def test_two_intervening_turns_between_blocked_attempt_and_reissue_not_found(
        self, tmp_path
    ):
        from pacemaker.transcript_reader import get_current_turn_message_for_validation

        transcript = _write_transcript(
            [
                # The ORIGINAL blocked attempt: matching tool_use, already
                # has its own tool_result (simulating the earlier block --
                # this is what the "stale" path would key off of if it were
                # still within the search window).
                _asst("req_A", _text_block(BASH_INTENT)),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": BASH_CMD}, "toolu_old"),
                ),
                _tool_result_entry("blocked", tool_use_id="toolu_old"),
                # Two intervening assistant turns, unrelated to the Bash
                # command, that push the blocked attempt outside the last
                # LAST_N_TURNS_FOR_TOOL_MATCH (2) logical turns.
                _asst("req_B", _text_block("Unrelated intervening turn 1.")),
                _asst("req_C", _text_block("Unrelated intervening turn 2.")),
                # NOTE: the byte-identical re-issue's own tool_use is
                # deliberately NOT in this transcript -- it represents the
                # not-yet-flushed current attempt being searched for.
            ],
            tmp_path,
        )
        diagnostics: dict = {}
        result = get_current_turn_message_for_validation(
            transcript,
            tool_input={"command": BASH_CMD},
            tool_name="Bash",
            _max_wait_seconds=0.0,
            _diagnostics=diagnostics,
        )
        assert result is None
        assert diagnostics.get("outcome") == "not_found", (
            "With 2 intervening assistant turns between the blocked "
            "attempt and the byte-identical re-issue, the blocked "
            "attempt's tool_use falls outside the LAST_N_TURNS_FOR_TOOL_MATCH "
            f"window and must NOT be found as a stale match; got: {diagnostics}"
        )


# ---------------------------------------------------------------------------
# Section C: hook-level danger-bash gate consumption logic
# ---------------------------------------------------------------------------


def _config_enabled() -> dict:
    return {
        "enabled": True,
        "intent_validation_enabled": True,
        "danger_bash_enabled": True,
        "hook_model": "auto",
    }


def _hook_stdin(command: str, transcript_path: str, session_id: str = "s1") -> str:
    return json.dumps(
        {
            "session_id": session_id,
            "transcript_path": transcript_path,
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
    )


class _DbHarness:
    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "usage.db")
        self.transcript = os.path.join(self.tmp_dir, "transcript.jsonl")
        Path(self.transcript).write_text("")
        database.initialize_database(self.db_path)

    def teardown_method(self):
        import shutil

        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _rules_patch(self):
        return (
            patch(
                "pacemaker.danger_bash_rules.load_rules",
                return_value=[{"id": "SD-99", "description": "chmod 777"}],
            ),
            patch(
                "pacemaker.danger_bash_rules.match_command",
                return_value=[{"id": "SD-99", "description": "chmod 777"}],
            ),
        )


def _mock_anchor(return_value, outcome: str, stale_text: Optional[str] = None):
    """Build a side_effect callable for
    pacemaker.hook.get_current_turn_message_for_validation that populates
    _diagnostics exactly as the real function would on give-up/success,
    isolating the HOOK's reaction from the real retry-loop algorithm
    (already covered in Sections A/B and test_transcript_staleness_fix.py)."""

    def _fn(
        transcript_path,
        tool_input=None,
        tool_name=None,
        _max_wait_seconds=30.0,
        _initial_sleep=0.25,
        _backoff_multiplier=2.0,
        _max_sleep=2.0,
        _diagnostics=None,
    ):
        if _diagnostics is not None:
            _diagnostics["attempts"] = 1
            _diagnostics["elapsed_seconds"] = 0.01
            _diagnostics["outcome"] = outcome
            if stale_text is not None:
                _diagnostics["stale_text"] = stale_text
        return return_value

    return _fn


class TestNotFoundBlocksWithTelemetryAndReducedCeiling(_DbHarness):
    """not_found -> gate blocks, telemetry records outcome=not_found, and
    the call site passes a drastically reduced wait ceiling (not the 30s
    Write/Edit default)."""

    def test_not_found_blocks_and_records_outcome(self):
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _hook_stdin(BASH_CMD, self.transcript)
        rules_patches = self._rules_patch()

        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                side_effect=_mock_anchor(None, "not_found"),
            ),
            rules_patches[0],
            rules_patches[1],
        ):
            result = run_pre_tool_hook()

        assert result.get("decision") == "block"
        assert "transcript" in result.get("reason", "").lower()

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT details FROM blockage_events "
                "WHERE category = 'intent_validation_dangerbash'"
            ).fetchall()
        finally:
            conn.close()
        assert rows, "Expected an intent_validation_dangerbash blockage event"
        details = json.loads(rows[0][0])
        assert details.get("outcome") == "not_found"

    def test_call_site_passes_reduced_ceiling_not_30s(self):
        """Locks in requirement #3: the danger-bash call site overrides
        _max_wait_seconds to a small value, distinct from the Write/Edit
        gate's unmodified 30s default."""
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _hook_stdin(BASH_CMD, self.transcript)
        rules_patches = self._rules_patch()
        captured = {}

        def _capture(*args, **kwargs):
            captured.update(kwargs)
            if kwargs.get("_diagnostics") is not None:
                kwargs["_diagnostics"]["outcome"] = "not_found"
                kwargs["_diagnostics"]["attempts"] = 1
                kwargs["_diagnostics"]["elapsed_seconds"] = 0.01
            return None

        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                side_effect=_capture,
            ),
            rules_patches[0],
            rules_patches[1],
        ):
            run_pre_tool_hook()

        assert "_max_wait_seconds" in captured, (
            "Danger-bash call site must explicitly pass _max_wait_seconds "
            "(not rely on the Write/Edit gate's 30s default)"
        )
        assert 0 < captured["_max_wait_seconds"] < 10.0, (
            f"Expected a drastically reduced ceiling (well under the "
            f"Write/Edit gate's 30s default); got: {captured['_max_wait_seconds']}"
        )

    def test_bounded_by_reduced_ceiling_against_real_retry_loop(self):
        """Integration-level proof (real transcript_reader retry loop, not
        mocked) that the danger-bash gate's wait is genuinely bounded by the
        reduced ceiling rather than the Write/Edit gate's 30s default. Uses
        a fake monotonic clock (no real sleeping) — the attempts recorded in
        telemetry must be small, consistent with a ~3s ceiling and NOT the
        ~16 attempts a 30s ceiling would produce with the same backoff
        schedule."""
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _hook_stdin(BASH_CMD, self.transcript)  # no matching turn ever
        rules_patches = self._rules_patch()

        fake_now = [0.0]

        def fake_monotonic():
            return fake_now[0]

        def fake_sleep(seconds):
            fake_now[0] += seconds

        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch("pacemaker.transcript_reader.time.sleep", fake_sleep),
            patch("pacemaker.transcript_reader.time.monotonic", fake_monotonic),
            rules_patches[0],
            rules_patches[1],
        ):
            result = run_pre_tool_hook()

        assert result.get("decision") == "block"
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT details FROM blockage_events "
                "WHERE category = 'intent_validation_dangerbash'"
            ).fetchall()
        finally:
            conn.close()
        details = json.loads(rows[0][0])
        assert details.get("outcome") == "not_found"
        # A 30s ceiling with the 0.25/0.5/1.0/2.0(capped)... schedule takes
        # ~16 attempts; a drastically reduced (~3s) ceiling takes ~5. Using
        # 10 as the dividing line keeps this robust to minor schedule tuning
        # while still proving the ceiling was NOT left at 30s.
        assert details.get("attempts", 999) < 10, (
            f"Expected a small attempt count consistent with a reduced "
            f"ceiling; got {details.get('attempts')} (fake elapsed "
            f"{fake_now[0]:.2f}s)"
        )


class TestStaleAcceptedWithIntent(_DbHarness):
    """stale + INTENT present in the stale turn -> gate ACCEPTS and proceeds
    to Phase 1/2 (never treated as 'transcript not ready')."""

    def test_stale_with_intent_proceeds_to_phase2_and_approves(self):
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _hook_stdin(BASH_CMD, self.transcript)
        rules_patches = self._rules_patch()

        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                side_effect=_mock_anchor(None, "stale", stale_text=BASH_INTENT),
            ),
            patch(
                "pacemaker.inference.resolve_and_call_with_reviewer",
                return_value=("APPROVED", "test-reviewer"),
            ) as mock_reviewer,
            rules_patches[0],
            rules_patches[1],
        ):
            result = run_pre_tool_hook()

        assert result == {"continue": True}, (
            f"Stale turn WITH INTENT must be accepted and approved (Phase 2 "
            f"mocked APPROVED); got: {result}"
        )
        mock_reviewer.assert_called_once()
        _, kwargs = mock_reviewer.call_args
        assert "sandbox probe file" in kwargs.get("prompt", ""), (
            "Phase 2 prompt must be built from the stale turn's own INTENT "
            "text (byte-identical command match makes this safe)."
        )

    def test_stale_with_intent_does_not_record_not_ready_telemetry(self):
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _hook_stdin(BASH_CMD, self.transcript)
        rules_patches = self._rules_patch()

        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                side_effect=_mock_anchor(None, "stale", stale_text=BASH_INTENT),
            ),
            patch(
                "pacemaker.inference.resolve_and_call_with_reviewer",
                return_value=("APPROVED", "test-reviewer"),
            ),
            rules_patches[0],
            rules_patches[1],
        ):
            run_pre_tool_hook()

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT reason FROM blockage_events "
                "WHERE category = 'intent_validation_dangerbash'"
            ).fetchall()
        finally:
            conn.close()
        not_ready = [r for r in rows if "not yet flushed" in (r[0] or "")]
        assert not not_ready, (
            "A stale match WITH INTENT must never be recorded as the "
            f"'transcript not yet flushed' race; got: {rows}"
        )


class TestStaleWithoutIntentBlocksAsOrdinaryPhase1(_DbHarness):
    """stale WITHOUT INTENT in the stale turn's text -> Phase 1 blocks with
    the ordinary 'no INTENT' reason, NOT 'transcript not ready'."""

    def test_stale_without_intent_blocks_with_phase1_reason(self):
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _hook_stdin(BASH_CMD, self.transcript)
        rules_patches = self._rules_patch()

        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                side_effect=_mock_anchor(None, "stale", stale_text="[TOOL: Bash]\n"),
            ),
            rules_patches[0],
            rules_patches[1],
        ):
            result = run_pre_tool_hook()

        assert result.get("decision") == "block"
        reason = result.get("reason", "")
        assert "no INTENT" in reason or "INTENT:" in reason
        assert "transcript" not in reason.lower(), (
            f"A stale turn WITHOUT INTENT must be a real Phase-1 rejection, "
            f"never the 'transcript not ready' message; got: {reason!r}"
        )

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute("SELECT category FROM blockage_events").fetchall()
        finally:
            conn.close()
        categories = {r[0] for r in rows}
        assert "intent_validation" in categories, (
            f"Expected the ordinary Phase-1 'intent_validation' category; "
            f"got: {categories}"
        )
        assert not any(c == "intent_validation_dangerbash" for c in categories), (
            "The dangerbash category is only used for the not-ready/Phase-2 "
            f"paths, neither of which applies here (Phase 1 blocked); got: "
            f"{categories}"
        )


class TestFoundUsesAnchorNotUnanchoredNBack(_DbHarness):
    """found -> the anchor's own text is what gets validated; the unanchored
    n=4 backward search must not be consulted at all."""

    def test_found_uses_anchor_text_and_never_calls_nback(self):
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _hook_stdin(BASH_CMD, self.transcript)
        rules_patches = self._rules_patch()

        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                side_effect=_mock_anchor(BASH_INTENT, "found"),
            ),
            patch("pacemaker.hook.get_last_n_messages_for_validation") as mock_nback,
            patch(
                "pacemaker.inference.resolve_and_call_with_reviewer",
                return_value=("APPROVED", "test-reviewer"),
            ) as mock_reviewer,
            rules_patches[0],
            rules_patches[1],
        ):
            result = run_pre_tool_hook()

        assert result == {"continue": True}
        # The unanchored n=4 backward search must never be consulted when a
        # real anchor was found (issue #93).
        mock_nback.assert_not_called()
        _, kwargs = mock_reviewer.call_args
        assert "sandbox probe file" in kwargs.get("prompt", "")


class TestSecurityRegressionDifferentCommandNeverAccepted(_DbHarness):
    """The single most important test in this change (per issue #93): a
    prior turn's INTENT for a DIFFERENT Bash command must NEVER be accepted
    for the CURRENT command, even under the stale/not-found race handling.
    Exercised against the REAL transcript_reader algorithm (not mocked) —
    this is a property of _tool_input_matches's byte-identical comparison,
    not of the hook's branching, so a mock of the anchor function would not
    actually prove it."""

    def test_prior_intent_for_different_command_is_not_accepted(self):
        from pacemaker.hook import run_pre_tool_hook

        DIFFERENT_CMD = "ls -la /tmp"
        DANGEROUS_CMD = "chmod 777 /tmp/sandbox/probe.txt"

        # Prior (unrelated) turn: safe command, WITH an INTENT declaration,
        # already fully flushed (including its own tool_result).
        prior_transcript = self.transcript
        with open(prior_transcript, "w") as f:
            f.write(
                json.dumps(
                    _asst(
                        "req_PREV",
                        _text_block(
                            "INTENT: List files in /tmp for a sanity check.\n"
                            "Purely read-only, no side effects."
                        ),
                    )
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    _asst(
                        "req_PREV",
                        _tool_use_block(
                            "Bash", {"command": DIFFERENT_CMD}, "toolu_PREV"
                        ),
                    )
                )
                + "\n"
            )
            f.write(
                json.dumps(_tool_result_entry("ok", tool_use_id="toolu_PREV")) + "\n"
            )
            # The CURRENT (dangerous) command's own turn has NOT flushed.

        stdin_payload = _hook_stdin(DANGEROUS_CMD, prior_transcript)
        rules_patches = self._rules_patch()

        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch(
                "pacemaker.inference.resolve_and_call_with_reviewer"
            ) as mock_reviewer,
            rules_patches[0],
            rules_patches[1],
        ):
            result = run_pre_tool_hook()

        assert result.get("decision") == "block", (
            f"A different-command prior INTENT must never authorize the "
            f"current dangerous command; got: {result}"
        )
        # Phase 2 (LLM review) must never be reached — the mismatch must be
        # caught before ever considering the prior turn's INTENT.
        mock_reviewer.assert_not_called()

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT category, details FROM blockage_events"
            ).fetchall()
        finally:
            conn.close()
        assert rows, "Expected a blockage event to be recorded"
        # Must be the not-ready path (no match at all for the current,
        # different command), never a Phase-2 mismatch path that implies the
        # prior INTENT was actually considered against the current command.
        categories = {r[0] for r in rows}
        assert "intent_validation_dangerbash" in categories
        for category, details_json in rows:
            if category == "intent_validation_dangerbash":
                details = json.loads(details_json)
                assert details.get("outcome") == "not_found", (
                    f"A genuinely different, never-before-seen command must "
                    f"deterministically classify as not_found (never stale, "
                    f"since _tool_input_matches never matches it); "
                    f"got: {details}"
                )
                # Critically: the reviewer was never called, so no
                # mismatched approval could have happened.


# ---------------------------------------------------------------------------
# FIX 1 (code-review remediation, security regression): the stale path must
# gate stale_text on the turn's own TEXT INTENT marker, mirroring the found
# path's existing gate (`re.search(r"(?i)\bintent\s*:", merged["text"])`).
# Before this fix, stale_text was set to
# `_format_message_with_tools(merged)` UNCONDITIONALLY -- and that helper
# renders every tool_use's `file_path`/`content`/`old_string`/`new_string`
# fields (see `_format_message_with_tools`), NOT just the assistant's actual
# prose text. `_has_intent_marker` (intent_validator.py) is a bare regex over
# the WHOLE formatted string this function returns, with no text/tool
# distinction.
#
# Note: Bash's own `command` field is intentionally NOT in
# `_format_message_with_tools`'s rendered-key list
# (`["file_path", "content", "old_string", "new_string"]`) -- so an
# INTENT:-looking string placed directly in the Bash `command` field being
# validated is NEVER rendered into stale_text, with or without this fix. The
# actually reachable exploit vector for the danger-bash gate is a SIBLING
# tool_use (e.g. Write) sharing the SAME requestId as the anchor Bash
# command -- `_merge_anchor_turn` merges ALL tool_use entries in the logical
# turn, so a sibling Write's `content` field DOES get rendered, and can leak
# an INTENT:-looking string into stale_text even though the turn's actual
# TEXT has no such marker. That is the scenario reproduced below.
# ---------------------------------------------------------------------------


class TestSecurityFix1StaleTextGatedOnActualText:
    """Section A (real algorithm): proves `stale_text` is gated on the
    turn's TEXT, not on the raw formatted string including sibling tool
    content."""

    def test_stale_text_empty_when_only_sibling_tool_content_has_intent(self, tmp_path):
        from pacemaker.transcript_reader import _find_turn_matching_tool_input

        transcript = _write_transcript(
            [
                _asst("req_A", _text_block("Running cleanup script now.")),
                _asst(
                    "req_A",
                    _tool_use_block("Bash", {"command": BASH_CMD}, "toolu_bash"),
                ),
                _asst(
                    "req_A",
                    _tool_use_block(
                        "Write",
                        {
                            "file_path": "/tmp/x.py",
                            "content": (
                                "def f():\n"
                                "    # INTENT: fake-marker-in-content, do "
                                "nothing risky\n"
                                "    pass\n"
                            ),
                        },
                        "toolu_write",
                    ),
                ),
                _tool_result_entry("done", tool_use_id="toolu_bash"),
            ],
            tmp_path,
        )
        outcome: dict = {}
        result = _find_turn_matching_tool_input(
            transcript, {"command": BASH_CMD}, "Bash", _outcome=outcome
        )
        assert result is None
        assert outcome.get("outcome") == "stale"
        assert outcome.get("stale_text") == "", (
            "stale_text must be gated on the turn's ACTUAL TEXT INTENT "
            "marker (there is none here) -- a sibling Write tool_use's "
            "rendered `content` containing an INTENT:-looking string must "
            f"NOT leak through; got: {outcome.get('stale_text')!r}"
        )


class TestSecurityFix1Phase2NotCalledOnLeakedStaleText(_DbHarness):
    """Section C-style (real, UNMOCKED transcript_reader + real hook.py
    branching): proves the FULL pipeline -- Phase 1 blocks with the ordinary
    'no INTENT' reason (not 'transcript not ready'), and Phase 2
    (`resolve_and_call_with_reviewer`) is never invoked, when the only
    INTENT:-looking text in the merged turn comes from a sibling tool's
    rendered content rather than the assistant's own prose."""

    def test_phase2_not_called_when_sibling_write_content_has_fake_intent(self):
        from pacemaker.hook import run_pre_tool_hook

        with open(self.transcript, "w") as f:
            f.write(
                json.dumps(_asst("req_A", _text_block("Running cleanup script now.")))
                + "\n"
            )
            f.write(
                json.dumps(
                    _asst(
                        "req_A",
                        _tool_use_block("Bash", {"command": BASH_CMD}, "toolu_bash"),
                    )
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    _asst(
                        "req_A",
                        _tool_use_block(
                            "Write",
                            {
                                "file_path": "/tmp/x.py",
                                "content": (
                                    "def f():\n"
                                    "    # INTENT: fake-marker-in-content\n"
                                    "    pass\n"
                                ),
                            },
                            "toolu_write",
                        ),
                    )
                )
                + "\n"
            )
            f.write(
                json.dumps(_tool_result_entry("done", tool_use_id="toolu_bash")) + "\n"
            )

        stdin_payload = _hook_stdin(BASH_CMD, self.transcript)
        rules_patches = self._rules_patch()

        # Fake monotonic clock: the retry loop cannot ever get a non-None
        # result for a genuinely "stale" outcome (the fixture is static), so
        # it retries through the full ceiling. Faking time avoids a real
        # multi-second sleep in the test suite.
        fake_now = [0.0]

        def fake_monotonic():
            return fake_now[0]

        def fake_sleep(seconds):
            fake_now[0] += seconds

        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch("pacemaker.transcript_reader.time.sleep", fake_sleep),
            patch("pacemaker.transcript_reader.time.monotonic", fake_monotonic),
            patch(
                "pacemaker.inference.resolve_and_call_with_reviewer"
            ) as mock_reviewer,
            rules_patches[0],
            rules_patches[1],
        ):
            result = run_pre_tool_hook()

        assert result.get("decision") == "block"
        reason = result.get("reason", "")
        assert "no INTENT" in reason or "INTENT:" in reason
        assert "transcript" not in reason.lower(), (
            "Must be blocked as an ordinary Phase-1 rejection, not the "
            f"'transcript not ready' race message; got: {reason!r}"
        )
        mock_reviewer.assert_not_called()


# ---------------------------------------------------------------------------
# FIX 2 (code-review remediation, comment correction): three comment
# locations claimed a stale/found match is "BY CONSTRUCTION" a re-issue
# whose INTENT "describes exactly the command being validated now". This is
# false when a single logical turn (one requestId) issues SEVERAL Bash
# tool_use calls: `_merge_anchor_turn` merges the turn's TEXT regardless of
# which specific tool_use matched, so the accepted INTENT may in fact
# describe a SIBLING command, not the one whose content happens to
# byte-match. This test documents/locks in the CURRENT (correctly
# documented, post-fix-comment) behavior: Phase 1 (regex-only) cannot
# distinguish which sibling command an INTENT describes, so it passes;
# semantic alignment is Phase 2's (the LLM's) job.
# ---------------------------------------------------------------------------


class TestMultiToolTurnFix2SemanticGapDocumented(_DbHarness):
    def test_sibling_command_intent_passes_phase1_reaches_phase2(self):
        from pacemaker.hook import run_pre_tool_hook

        DESCRIBED_CMD = "rm -rf /tmp/scratch"
        SIBLING_CMD = "rm -rf /home/jsbattig/Dev/project"
        INTENT_TEXT = (
            "INTENT: Delete only the throwaway scratch directory /tmp/scratch "
            "used for this test run. Safe and reversible, no project data "
            "affected."
        )

        with open(self.transcript, "w") as f:
            f.write(json.dumps(_asst("req_A", _text_block(INTENT_TEXT))) + "\n")
            f.write(
                json.dumps(
                    _asst(
                        "req_A",
                        _tool_use_block("Bash", {"command": DESCRIBED_CMD}, "toolu_1"),
                    )
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    _asst(
                        "req_A",
                        _tool_use_block("Bash", {"command": SIBLING_CMD}, "toolu_2"),
                    )
                )
                + "\n"
            )

        stdin_payload = _hook_stdin(SIBLING_CMD, self.transcript)
        rules_patches = self._rules_patch()

        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch(
                "pacemaker.inference.resolve_and_call_with_reviewer",
                return_value=("APPROVED", "test-reviewer"),
            ) as mock_reviewer,
            rules_patches[0],
            rules_patches[1],
        ):
            result = run_pre_tool_hook()

        assert result == {"continue": True}, (
            "Phase 1 passes because the TURN carries an INTENT: marker -- "
            "structurally it cannot tell which sibling command the INTENT "
            f"describes; got: {result}"
        )
        mock_reviewer.assert_called_once()
        _, kwargs = mock_reviewer.call_args
        assert SIBLING_CMD in kwargs.get("prompt", "")
        assert "scratch directory" in kwargs.get("prompt", ""), (
            "Phase 2 receives the WHOLE turn's INTENT text (describing the "
            "DESCRIBED sibling, not the SIBLING command being validated) -- "
            "this is exactly the semantic gap only Phase 2 (the LLM) can "
            "catch; Phase 1 (regex-only) cannot."
        )


# ---------------------------------------------------------------------------
# FIX 3 (code-review remediation): the not_found block message must
# explicitly instruct the agent to re-issue a BYTE-IDENTICAL command AS ITS
# VERY NEXT TOOL CALL, so the stale-recovery path (the only path that
# converges -- see TestTwoInterveningTurnsDocumentsUnboundedRounds and
# hook.py's corrected comment above the danger-bash anchor call) is actually
# reachable in practice: a rephrased command changes the byte string and
# produces not_found again, and any intervening tool call risks pushing the
# blocked attempt outside the LAST_N_TURNS_FOR_TOOL_MATCH window.
# ---------------------------------------------------------------------------


class TestFix3NotFoundMessageInstructsIdenticalReissue(_DbHarness):
    def test_not_found_reason_instructs_byte_identical_reissue(self):
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _hook_stdin(BASH_CMD, self.transcript)
        rules_patches = self._rules_patch()

        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", self.db_path),
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                side_effect=_mock_anchor(None, "not_found"),
            ),
            rules_patches[0],
            rules_patches[1],
        ):
            result = run_pre_tool_hook()

        assert result.get("decision") == "block"
        reason = result.get("reason", "")
        assert "IDENTICAL" in reason, (
            "not_found block message must instruct the agent to re-issue "
            f"an IDENTICAL command; got: {reason!r}"
        )
        assert "byte-identical" in reason.lower(), (
            "not_found block message must explicitly say byte-identical "
            f"(mirrors the Write/Edit gate's message); got: {reason!r}"
        )
        assert "very next tool call" in reason.lower(), (
            "not_found block message must instruct the agent to re-issue "
            "as its VERY NEXT tool call -- this is the only path the "
            "stale-recovery mechanism can resolve (0-1 intervening turns); "
            f"got: {reason!r}"
        )
        assert "intervenes" in reason.lower(), (
            "not_found block message must warn that an intervening tool "
            f"call will cause the same block to recur; got: {reason!r}"
        )
