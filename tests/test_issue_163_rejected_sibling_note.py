"""
Bug #163 -- sibling Write/Edit calls blocked only because a same-agent,
same-file REJECTED edit just consumed their shared ``declare_intent``
declaration lead their block reason with a "declaration was used by a
rejected edit in this batch; declare again" note, instead of only the
misleading generic "NO visible text" notice.

Real ``run_pre_tool_hook`` and real SQLite stores (tmp paths from the conftest
guard); only the Stage 2 reviewer LLM call is mocked. Marker expiry is tested
with a fake clock at the store level and by aging the real row at hook level.
"""

import os
import sqlite3

import pytest

os.environ.setdefault("PACEMAKER_TEST_MODE", "1")

from pacemaker.constants import REJECTED_DECLARATION_MARKER_TTL_SECONDS  # noqa: E402
from pacemaker.intent_declarations.store import (  # noqa: E402
    IntentDeclarationStore,
    StoredIntent,
    resolve_db_path,
)
from declare_intent_harness import (  # noqa: E402
    Harness,
    SESSION,
    _anchor_not_found,
    _config,
)

BLOCKED = (
    "BLOCKED: the change does not match the declared intent.\n"
    "CLASSIFICATION: CLEAN_CODE",
    "test-reviewer",
)
NOTE_TAG = "[pace-maker · intent_validation_block]\n"
NOTE_FRAGMENT = "was used by a rejected edit in this same batch"
NO_TEXT_FRAGMENT = "Your message had NO visible text"


def _anchor_no_visible_text(
    transcript_path,
    tool_input=None,
    tool_name=None,
    _max_wait_seconds=30.0,
    _initial_sleep=0.25,
    _backoff_multiplier=2.0,
    _max_sleep=2.0,
    _diagnostics=None,
    _stale_grace_seconds=None,
):
    """Anchored turn found, but it has no visible text (the live shape)."""
    if _diagnostics is not None:
        _diagnostics["outcome"] = "found"
        _diagnostics["anchor_has_visible_text"] = False
    return ""


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


def _count_markers(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM rejected_declarations").fetchone()[0]
    finally:
        conn.close()


def _age_markers(seconds):
    conn = sqlite3.connect(resolve_db_path())
    try:
        conn.execute(
            "UPDATE rejected_declarations SET created_at = created_at - ?", (seconds,)
        )
        conn.commit()
    finally:
        conn.close()


def _reject_edit_one(h, file_path=None, agent_id=None):
    """Declare, then send Edit 1, which Stage 2 rejects (consuming the
    declaration and deleting the chain)."""
    file_path = file_path or h.core_file
    assert h.declare(file_path, agent_id=agent_id) is True
    result = h.run("Edit", file_path, agent_id=agent_id, reviewer_response=BLOCKED)
    assert result["decision"] == "block"
    assert "does not match the declared intent" in result["reason"]
    return result


class TestParallelBatch:
    def test_siblings_of_a_rejected_edit_get_the_note_and_stay_blocked(self, h):
        _reject_edit_one(h)
        for new_string in ("x = 2", "x = 3"):
            result = h.run(
                "Edit",
                h.core_file,
                anchor=_anchor_no_visible_text,
                new_string=new_string,
            )
            assert result["decision"] == "block"
            reason = result["reason"]
            assert reason.startswith(NOTE_TAG)
            assert NOTE_FRAGMENT in reason
            assert "`declare_intent`" in reason
            assert os.path.basename(h.core_file) in reason
            # The generic notice stays, below the note.
            assert NO_TEXT_FRAGMENT in reason
            assert reason.index(NOTE_FRAGMENT) < reason.index(NO_TEXT_FRAGMENT)
        # Siblings were never reviewed on their merits (only Edit 1 was).
        assert len(h.reviewer_prompts) == 1

    def test_the_marker_is_recorded_once_per_agent_and_file(self, h):
        _reject_edit_one(h)
        (row,) = h.store_rows("rejected_declarations")
        assert row["session_id"] == SESSION
        assert row["agent_key"] == "main"
        assert row["file_path"] == os.path.realpath(h.core_file)

    def test_the_deferred_race_block_gets_the_note_too(self, h):
        _reject_edit_one(h)
        result = h.run("Edit", h.core_file, anchor=_anchor_not_found)
        assert result["decision"] == "block"
        assert "transcript timing race" in result["reason"]
        assert result["reason"].startswith(NOTE_TAG)
        assert NOTE_FRAGMENT in result["reason"]

    def test_redeclaring_clears_the_marker_and_the_retry_is_approved(self, h):
        _reject_edit_one(h)
        assert h.declare(h.core_file) is True
        assert h.store_rows("rejected_declarations") == []
        assert h.run("Edit", h.core_file).get("decision") != "block"

    def test_a_stage_one_rejection_of_a_declared_intent_also_marks(self, h):
        # Core path declared without test_coverage: Stage 1 NO_TDD rejects
        # it and the rejection consumes the declaration all the same.
        h.declare(h.core_file, test_coverage="")
        assert h.run("Edit", h.core_file)["decision"] == "block"
        assert len(h.store_rows("rejected_declarations")) == 1


class TestNoNoteWithoutARecentRejection:
    def test_no_marker_means_the_generic_block_is_unchanged(self, h):
        result = h.run("Edit", h.core_file, anchor=_anchor_no_visible_text)
        assert result["decision"] == "block"
        assert NOTE_FRAGMENT not in result["reason"]
        assert result["reason"].startswith(NOTE_TAG + "⛔ Your message had NO visible")
        assert h.store_rows("rejected_declarations") == []

    def test_a_transcript_path_rejection_leaves_no_marker(self, h):
        h.run("Edit", h.core_file, anchor=_anchor_no_visible_text)
        h.run("Edit", h.core_file, anchor=_anchor_no_visible_text)
        assert h.store_rows("rejected_declarations") == []

    def test_an_approved_edit_leaves_no_marker(self, h):
        h.declare(h.core_file)
        assert h.run("Edit", h.core_file).get("decision") != "block"
        assert h.store_rows("rejected_declarations") == []

    def test_an_expired_marker_adds_no_note(self, h):
        _reject_edit_one(h)
        _age_markers(REJECTED_DECLARATION_MARKER_TTL_SECONDS + 1)
        result = h.run("Edit", h.core_file, anchor=_anchor_no_visible_text)
        assert result["decision"] == "block"
        assert NOTE_FRAGMENT not in result["reason"]
        assert result["reason"].startswith(NOTE_TAG + "⛔ Your message had NO visible")

    def test_a_marker_just_inside_the_ttl_still_adds_the_note(self, h):
        _reject_edit_one(h)
        _age_markers(REJECTED_DECLARATION_MARKER_TTL_SECONDS - 30)
        result = h.run("Edit", h.core_file, anchor=_anchor_no_visible_text)
        assert NOTE_FRAGMENT in result["reason"]

    def test_a_different_file_gets_no_note(self, h):
        _reject_edit_one(h, file_path=h.core_file)
        result = h.run("Edit", h.core_file_b, anchor=_anchor_no_visible_text)
        assert result["decision"] == "block"
        assert NOTE_FRAGMENT not in result["reason"]

    def test_a_different_agent_gets_no_note(self, h):
        _reject_edit_one(h, agent_id="agent-1")
        other = h.run(
            "Edit", h.core_file, agent_id="agent-2", anchor=_anchor_no_visible_text
        )
        assert other["decision"] == "block"
        assert NOTE_FRAGMENT not in other["reason"]
        main = h.run("Edit", h.core_file, anchor=_anchor_no_visible_text)
        assert NOTE_FRAGMENT not in main["reason"]
        # ...while the agent that was rejected still gets it.
        same = h.run(
            "Edit", h.core_file, agent_id="agent-1", anchor=_anchor_no_visible_text
        )
        assert NOTE_FRAGMENT in same["reason"]


class TestKillSwitch:
    def test_off_records_no_marker_and_adds_no_note(self, h):
        off = _config(intent_declaration_tool_enabled=False)
        result = h.run("Edit", h.core_file, anchor=_anchor_no_visible_text, config=off)
        assert result["decision"] == "block"
        assert NOTE_FRAGMENT not in result["reason"]
        assert h.store_rows("rejected_declarations") == []

    def test_off_after_a_marker_exists_adds_no_note(self, h):
        _reject_edit_one(h)
        off = _config(intent_declaration_tool_enabled=False)
        result = h.run("Edit", h.core_file, anchor=_anchor_no_visible_text, config=off)
        assert result["decision"] == "block"
        assert NOTE_FRAGMENT not in result["reason"]


class TestStore:
    def _store(self, tmp_path, clock):
        return IntentDeclarationStore(str(tmp_path / "s.db"), clock=clock)

    def test_marker_expires_by_fake_clock(self, tmp_path):
        now = [1000.0]
        store = self._store(tmp_path, lambda: now[0])
        store.reject("s", "main", "/w/a.py")
        assert store.has_rejection_marker("s", "main", "/w/a.py") is True
        now[0] += REJECTED_DECLARATION_MARKER_TTL_SECONDS - 1
        assert store.has_rejection_marker("s", "main", "/w/a.py") is True
        now[0] += 2
        assert store.has_rejection_marker("s", "main", "/w/a.py") is False

    def test_expired_markers_are_purged_by_the_existing_cleanup(self, tmp_path):
        now = [1000.0]
        store = self._store(tmp_path, lambda: now[0])
        store.reject("s", "main", "/w/a.py")
        now[0] += REJECTED_DECLARATION_MARKER_TTL_SECONDS + 1
        store.reject("s", "main")  # any access purges
        assert _count_markers(str(tmp_path / "s.db")) == 0

    def test_reject_without_a_file_still_only_deletes_the_chain(self, tmp_path):
        store = self._store(tmp_path, lambda: 1000.0)
        store.approve("s", "main", StoredIntent("/w/a.py", "c", "g", ""))
        store.reject("s", "main")
        assert store.resolve("s", "main", "/w/a.py") is None
        assert store.has_rejection_marker("s", "main", "/w/a.py") is False

    def test_repeated_rejections_keep_one_marker_row(self, tmp_path):
        store = self._store(tmp_path, lambda: 1000.0)
        store.reject("s", "main", "/w/a.py")
        store.reject("s", "main", "/w/a.py")
        assert _count_markers(str(tmp_path / "s.db")) == 1

    def test_schema_is_additive_on_a_preexisting_database(self, tmp_path):
        db = str(tmp_path / "old.db")
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE declarations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "session_id TEXT NOT NULL, agent_key TEXT NOT NULL, file_path TEXT "
            "NOT NULL, change TEXT NOT NULL, goal TEXT NOT NULL, test_coverage "
            "TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL)"
        )
        conn.execute(
            "INSERT INTO declarations (session_id, agent_key, file_path, change, "
            "goal, created_at) VALUES ('s', 'main', '/w/a.py', 'c', 'g', 1000.0)"
        )
        conn.commit()
        conn.close()
        store = IntentDeclarationStore(db, clock=lambda: 1000.0)
        store.reject("s", "main", "/w/a.py")
        assert store.has_rejection_marker("s", "main", "/w/a.py") is True
        resolution = store.resolve("s", "main", "/w/a.py")
        assert resolution is not None and resolution.intent.change == "c"


OPUS = "claude-opus-5-5"
REVIEW_HINT_FRAGMENT = "Address the review above"
CONSUMED_NOTE_FRAGMENT = "was used by this rejected attempt"
VAGUE = (
    "BLOCKED: Intent excerpt is too vague to verify the change.\n"
    "CLASSIFICATION: CLEAN_CODE",
    "test-reviewer",
)
RELAXED_SOURCES = {
    # Exception model, no visible text: the anchored turn's own reasoning
    # summary is the intent...
    "reasoning_summary": {"summary": "I'll add x to a.py.", "recent": []},
    # ...or, with an empty anchor, the immediately preceding turn's summary.
    "prior_reasoning_summary": {
        "summary": "",
        "recent": [("", "I'll add x to a.py next.")],
    },
}


class _RelaxedAnchor:
    """Anchored turn of an exception model with NO visible text and the given
    reasoning summary / prior turns (the live #163 shape). Callable with the
    signature the hook gives ``get_current_turn_message_for_validation``."""

    def __init__(self, summary, recent):
        self._summary = summary
        self._recent = recent

    def __call__(
        self,
        transcript_path,
        tool_input=None,
        tool_name=None,
        _max_wait_seconds=30.0,
        _initial_sleep=0.25,
        _backoff_multiplier=2.0,
        _max_sleep=2.0,
        _diagnostics=None,
        _stale_grace_seconds=None,
    ):
        if _diagnostics is not None:
            _diagnostics["outcome"] = "found"
            _diagnostics["anchor_has_visible_text"] = False
            _diagnostics["anchor_model"] = OPUS
            _diagnostics["anchor_prose_text"] = ""
            _diagnostics["anchor_reasoning_summary"] = self._summary
            _diagnostics["anchor_recent_context"] = self._recent
        return ""


def _opus_config(**overrides):
    return _config(reasoning_summary_intent_models=[OPUS], **overrides)


def _governance_texts(h):
    conn = sqlite3.connect(h.db_path)
    try:
        return [
            r[0] for r in conn.execute("SELECT feedback_text FROM governance_events")
        ]
    finally:
        conn.close()


class TestRelaxedPathSiblings:
    @pytest.mark.parametrize("source", sorted(RELAXED_SOURCES))
    def test_relaxed_sibling_stage2_rejection_leads_with_the_note(self, h, source):
        _reject_edit_one(h)
        result = h.run(
            "Edit",
            h.core_file,
            anchor=_RelaxedAnchor(**RELAXED_SOURCES[source]),
            reviewer_response=VAGUE,
            config=_opus_config(),
        )
        assert result["decision"] == "block"
        reason = result["reason"]
        # The sibling really took the relaxed path and was reviewed (Stage 2).
        assert "Intent excerpt is too vague" in reason
        assert len(h.reviewer_prompts) == 2
        last = h.blockages()[-1]
        assert last["category"] == "intent_validation_cleancode"
        assert last["details"]["intent_source"] == source
        # The note leads; the reviewer-relay segment and the #159 review hint
        # stay below it, unchanged.
        assert reason.startswith(NOTE_TAG)
        assert NOTE_FRAGMENT in reason
        assert reason.index(NOTE_FRAGMENT) < reason.index(
            "[pace-maker · reviewer-relay"
        )
        assert reason.index("Intent excerpt is too vague") < reason.index(
            REVIEW_HINT_FRAGMENT
        )
        assert CONSUMED_NOTE_FRAGMENT not in reason

    def test_only_the_message_changes_governance_text_is_untouched(self, h):
        _reject_edit_one(h)
        h.run(
            "Edit",
            h.core_file,
            anchor=_RelaxedAnchor(**RELAXED_SOURCES["reasoning_summary"]),
            reviewer_response=VAGUE,
            config=_opus_config(),
        )
        texts = _governance_texts(h)
        assert len(texts) == 2
        assert all(NOTE_FRAGMENT not in text for text in texts)
        assert "Intent excerpt is too vague" in texts[-1]

    def test_no_marker_leaves_the_relaxed_rejection_unchanged(self, h):
        result = h.run(
            "Edit",
            h.core_file,
            anchor=_RelaxedAnchor(**RELAXED_SOURCES["reasoning_summary"]),
            reviewer_response=VAGUE,
            config=_opus_config(),
        )
        assert result["decision"] == "block"
        assert NOTE_FRAGMENT not in result["reason"]
        assert result["reason"].startswith("[pace-maker · reviewer-relay")
        assert REVIEW_HINT_FRAGMENT in result["reason"]

    def test_kill_switch_off_leaves_the_relaxed_rejection_unchanged(self, h):
        _reject_edit_one(h)
        result = h.run(
            "Edit",
            h.core_file,
            anchor=_RelaxedAnchor(**RELAXED_SOURCES["reasoning_summary"]),
            reviewer_response=VAGUE,
            config=_opus_config(intent_declaration_tool_enabled=False),
        )
        assert result["decision"] == "block"
        assert NOTE_FRAGMENT not in result["reason"]
        assert REVIEW_HINT_FRAGMENT not in result["reason"]

    def test_expired_marker_leaves_the_relaxed_rejection_unchanged(self, h):
        _reject_edit_one(h)
        _age_markers(REJECTED_DECLARATION_MARKER_TTL_SECONDS + 1)
        result = h.run(
            "Edit",
            h.core_file,
            anchor=_RelaxedAnchor(**RELAXED_SOURCES["reasoning_summary"]),
            reviewer_response=VAGUE,
            config=_opus_config(),
        )
        assert NOTE_FRAGMENT not in result["reason"]
        assert REVIEW_HINT_FRAGMENT in result["reason"]

    def test_tool_declared_stage2_rejection_gets_only_the_consumed_note(self, h):
        _reject_edit_one(h)  # leaves a marker for this agent and file...
        # ...which the re-declaration clears; reject the declared edit again.
        assert h.declare(h.core_file) is True
        result = h.run("Edit", h.core_file, reviewer_response=BLOCKED)
        assert result["decision"] == "block"
        assert CONSUMED_NOTE_FRAGMENT in result["reason"]
        assert NOTE_FRAGMENT not in result["reason"]
        assert result["reason"].count("[pace-maker · intent_validation_block]") == 1

    def test_reviewer_unavailable_block_gets_no_note(self, h):
        _reject_edit_one(h)
        result = h.run(
            "Edit",
            h.core_file,
            anchor=_RelaxedAnchor(**RELAXED_SOURCES["reasoning_summary"]),
            reviewer_response=("", "test-reviewer"),
            config=_opus_config(),
        )
        assert result["decision"] == "block"
        assert NOTE_FRAGMENT not in result["reason"]
        assert h.blockages()[-1]["category"] == (
            "intent_validation_reviewer_unavailable"
        )
