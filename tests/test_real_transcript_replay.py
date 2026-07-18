"""Real-transcript replay regression suite for pre-tool Stage-1 validation.

WHY THIS SUITE EXISTS
=====================
The intent/TDD pre-tool validator has repeatedly been "fixed" against
synthetic unit cases and then regressed on real conversations.  This suite
pins Stage-1 behavior to a corpus of REAL Write/Edit tool calls harvested
from actual Claude Code transcripts (~2,600 transcripts scanned across 23
projects), each fixture reconstructing the transcript EXACTLY as the
pre-tool hook saw it at call time.

Corpus (tests/fixtures/real_transcript_replay/):
  - 30 distinct real edits + pre-flush variants (34 cases total)
  - manifest.json carries per-case provenance (source transcript + line),
    the HISTORICAL outcome (BLOCKED_BY_HOOK / ALLOWED, recovered from the
    tool_result that followed), the spec-correct expected Stage-1 verdict,
    and an adjudication note for every non-obvious case.
  - Categories include: TEST FILE/TEST SCOPE declarations (historical
    false-rejects), version bumps incl. backticked __version__, skip-TDD
    permissions, Test coverage declarations, INTENT in the immediately
    preceding message (fragmented turns), pre-flush states (tool_use entry
    not yet appended to the transcript), intent-with-no-TDD-declaration
    (must block), "intent:" marker appearing only inside the edited file's
    code content (must block), true no-intent (must block), .md under src/
    (core by policy, must block without declaration), and non-core paths.

FIDELITY CONTRACT
=================
``_replay_stage1`` below MUST mirror the pre-tool hook's Stage-1 sequence in
``pacemaker/hook.py`` (see run_pre_tool_hook, step 6/6b/7) and
``validate_intent_and_code`` exactly:

    messages = get_last_n_messages_for_validation(transcript_path, n=2)
    override = get_current_turn_message_for_validation(transcript_path,
                   tool_input=tool_input, tool_name=tool_name, _max_wait_seconds=0.0)
    if override is None:
        return {"decision": "block", ...}   # fail-CLOSED (TOCTOU race / pre-flush), v2.33.2
    current  = override or extract_current_assistant_message(messages)
    verdict  = _regex_stage1_check(current, file_path, exclusions)

This helper now uses the SHIPPED tool-matched anchor path (bug #83 fix).
Flushed fixtures supply the real tool_input extracted from the fixture file;
pre-flush fixtures supply a sentinel that cannot match, producing None →
fail-CLOSED → "NO" (v2.33.2 — previously "YES" under the old fail-open
behavior; see tests/test_intent_validation_failclosed_race.py for the
dedicated Bug A regression suite covering this exact branch).  A future
Claude Code format change that breaks the tool-matched anchor (e.g. changes
the JSONL schema so inputs no longer match) will cause flushed fixtures with
expected NO/NO_TDD verdicts to return "NO" anyway (same verdict, masking the
break) — see ``test_flushed_fixtures_matcher_returns_non_none`` below, which
exists specifically to catch that case independently of the verdict string.

If the hook glue changes, update this helper IN THE SAME COMMIT so the
replay stays faithful to production behavior.

Exclusions are PINNED (mirroring the shipped excluded_paths defaults) so the
suite is deterministic and independent of the developer's live config.
"""

import json
import os
from typing import Optional

import pytest

# Must be set before any pacemaker import triggers logging.
os.environ.setdefault("PACEMAKER_TEST_MODE", "1")

from pacemaker.intent_validator import (  # noqa: E402
    _regex_stage1_check,
    extract_current_assistant_message,
)
from pacemaker.transcript_reader import (  # noqa: E402
    get_current_turn_message_for_validation,
    get_last_n_messages_for_validation,
)

FIXTURE_DIR = os.path.join(
    os.path.dirname(__file__), "fixtures", "real_transcript_replay"
)

# Pinned mirror of the shipped excluded_paths defaults — deterministic,
# never read from the developer's live ~/.claude-pace-maker config.
PINNED_EXCLUSIONS = [
    ".tmp/",
    "test/",
    "tests/",
    "fixtures/",
    "__pycache__/",
    "node_modules/",
    "vendor/",
    "dist/",
    "build/",
    ".git/",
]


def _load_manifest():
    manifest_path = os.path.join(FIXTURE_DIR, "manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as f:
        return json.load(f)


MANIFEST = _load_manifest()


# ---------------------------------------------------------------------------
# Tool-input extraction helper
# ---------------------------------------------------------------------------


def _extract_tool_input_from_fixture(
    fixture_path: str, tool_name: str, file_path: str
) -> Optional[dict]:
    """Extract the last matching tool_use input from a fixture file.

    Scans the fixture forward and returns the input dict from the LAST
    assistant tool_use block whose ``name`` matches ``tool_name`` and, for
    Write/Edit, whose ``file_path`` matches ``file_path``.

    Returns None when no matching entry is found (expected for pre-flush
    fixtures where the current tool_use has not yet been appended).
    """
    last_match: Optional[dict] = None
    with open(fixture_path, "r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            entry = json.loads(raw)
            msg = entry.get("message", {})
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") != tool_name:
                    continue
                inp = block.get("input", {})
                if tool_name in ("Write", "Edit"):
                    if inp.get("file_path") == file_path:
                        last_match = inp
                elif tool_name == "Bash":
                    last_match = inp
                else:
                    last_match = inp
    return last_match


# ---------------------------------------------------------------------------
# Stage-1 replay helper — SHIPPED PATH (bug #83 fix)
# ---------------------------------------------------------------------------


def _replay_stage1(
    fixture_path: str,
    file_path: str,
    tool_name: str,
    tool_use_in_fixture: bool,
) -> str:
    """Mirror the pre-tool hook's Stage-1 path exactly (see module docstring).

    Uses the SHIPPED tool-matched anchor path introduced in bug #83:

    Flushed fixtures (tool_use_in_fixture=True):
        Extracts the actual tool_input from the fixture, passes it to
        get_current_turn_message_for_validation.  The matcher finds the
        exact current-turn tool_use and returns that turn's text (or "" if
        no INTENT marker in the turn's text).  Verdict proceeds normally.

    Pre-flush fixtures (tool_use_in_fixture=False):
        The current tool_use is NOT yet in the transcript.  A sentinel
        tool_input that cannot match any existing entry is supplied, causing
        get_current_turn_message_for_validation to return None → fail-CLOSED
        → "NO".  This matches hook.py's v2.33.2 behaviour (bug #83 TOCTOU
        guard now fails closed + re-issue, mirroring the danger-bash gate,
        instead of the old fail-open pass-through).

    _max_wait_seconds=0.0 is passed in both cases: fixtures are static
    files, re-reading them will never produce a different result.
    """
    messages = get_last_n_messages_for_validation(fixture_path, n=2)

    if tool_use_in_fixture:
        # Flushed fixture: extract the real tool_input from the fixture.
        tool_input = _extract_tool_input_from_fixture(
            fixture_path, tool_name, file_path
        )
        if tool_input is None:
            pytest.fail(
                f"Could not extract tool_input from flushed fixture {os.path.basename(fixture_path)!r} "
                f"for tool={tool_name!r} file={file_path!r}. "
                "A flushed fixture (tool_use_in_fixture=true) MUST contain the matching "
                "tool_use entry — this indicates a corpus quality issue."
            )
    else:
        # Pre-flush fixture: the current tool_use is not yet in the transcript.
        # Supply a sentinel that cannot match any existing entry so that
        # get_current_turn_message_for_validation returns None immediately
        # (same TOCTOU race the live hook observes).
        if tool_name == "Write":
            tool_input = {"file_path": file_path, "content": "__PREFLUSH_SENTINEL__"}
        elif tool_name == "Edit":
            tool_input = {"file_path": file_path, "new_string": "__PREFLUSH_SENTINEL__"}
        else:
            tool_input = {"command": "__PREFLUSH_SENTINEL__"}

    # Shipped path: _max_wait_seconds=0.0 avoids sleeping on static fixture files.
    override = get_current_turn_message_for_validation(
        fixture_path,
        tool_input=tool_input,
        tool_name=tool_name,
        _max_wait_seconds=0.0,
    )

    # Mimic hook.py ~2807 (v2.33.2): None → fail-CLOSED → {"decision": "block"} → "NO"
    if override is None:
        return "NO"

    current = override or extract_current_assistant_message(messages)
    return _regex_stage1_check(current, file_path, PINNED_EXCLUSIONS)


@pytest.mark.parametrize(
    "case", MANIFEST, ids=[c["fixture"].replace(".jsonl", "") for c in MANIFEST]
)
def test_replay_real_transcript_case(case):
    """Each harvested real tool call must produce its spec-correct verdict."""
    fixture_path = os.path.join(FIXTURE_DIR, case["fixture"])
    assert os.path.exists(fixture_path), f"missing fixture: {case['fixture']}"

    verdict = _replay_stage1(
        fixture_path,
        case["file_path"],
        case["tool_name"],
        case["tool_use_in_fixture"],
    )

    assert verdict == case["expected_stage1"], (
        f"\nfixture     : {case['fixture']}"
        f"\ncategory    : {case['category']}"
        f"\nfile_path   : {case['file_path']}"
        f"\ntool_name   : {case['tool_name']}"
        f"\ntool_use_in : {case['tool_use_in_fixture']}"
        f"\nexpected    : {case['expected_stage1']}  got: {verdict}"
        f"\nhistorical  : {case['historical_outcome']}"
        f"\nsource      : {case['source']} line {case['source_line']}"
        f"\nnote        : {case.get('note', '')}"
    )


# ---------------------------------------------------------------------------
# Corpus-quality guards: the suite is only as strong as its corpus.  These
# fail loudly if someone prunes the fixtures down to a toothless sample.
# ---------------------------------------------------------------------------


def test_corpus_has_at_least_twenty_distinct_real_edits():
    distinct = {(c["source"], c["source_line"]) for c in MANIFEST}
    assert len(distinct) >= 20, (
        f"corpus shrank to {len(distinct)} distinct real edits; "
        "the regression net requires at least 20"
    )


def test_corpus_covers_required_categories():
    required = {
        "testfile_sameturn",  # TEST FILE:/TEST SCOPE: format (historical false-rejects)
        "versionbump_sameturn",  # backticked __version__ bumps
        "skiptdd_sameturn",  # user permission to skip TDD
        "testcov_sameturn",  # Test coverage: declarations
        "intent_preceding",  # INTENT in the immediately-preceding message
        "no_intent_true",  # no INTENT anywhere -> must block
        "no_decl_marker_from_tool_content",  # marker only in edited code -> must block
        "non_core",  # non-core path -> allowed without TDD
    }
    present = {c["category"] for c in MANIFEST}
    missing = required - present
    assert not missing, f"corpus lost required categories: {sorted(missing)}"


def test_corpus_includes_preflush_variants():
    preflush = [c for c in MANIFEST if not c["tool_use_in_fixture"]]
    assert len(preflush) >= 3, (
        "corpus must keep pre-flush variants (transcript state BEFORE the "
        "tool_use entry is appended) — that is the state the live hook can "
        f"observe; found only {len(preflush)}"
    )


def test_corpus_includes_both_block_and_allow_history():
    history = {c["historical_outcome"] for c in MANIFEST}
    assert "BLOCKED_BY_HOOK" in history and "ALLOWED" in history, (
        "corpus must include both historically-blocked (false-reject "
        "candidates) and historically-allowed (regression-protection) cases; "
        f"found: {sorted(history)}"
    )


def test_corpus_includes_must_block_cases():
    blockers = [c for c in MANIFEST if c["expected_stage1"] in ("NO", "NO_TDD")]
    assert len(blockers) >= 4, (
        "corpus must keep cases proving the validator still BLOCKS "
        f"undeclared core edits; found only {len(blockers)}"
    )


def test_all_fixtures_are_valid_jsonl():
    for case in MANIFEST:
        path = os.path.join(FIXTURE_DIR, case["fixture"])
        with open(path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f):
                if not line.strip():
                    continue
                try:
                    json.loads(line)
                except json.JSONDecodeError as exc:  # pragma: no cover
                    pytest.fail(
                        f"{case['fixture']} line {line_num} is not valid JSON: {exc}"
                    )


# ---------------------------------------------------------------------------
# Shipped-path canary: verify the tool-matched anchor actually FINDS entries
# in flushed fixtures.  This test goes RED immediately if a Claude Code format
# change breaks the byte-match so the matcher always returns None — providing
# a loud early-warning before a silent fail-open regression accumulates.
# ---------------------------------------------------------------------------


def test_flushed_fixtures_matcher_returns_non_none():
    """Tool-matched anchor must find a result (not None) for every flushed fixture.

    If get_current_turn_message_for_validation returns None for a flushed
    fixture (tool_use_in_fixture=True), the matcher is broken — possibly due
    to a Claude Code JSONL format change that altered the tool_use schema.
    A broken matcher would silently fail-open all validations; this test
    trips RED immediately so the regression is caught before it accumulates.
    """
    flushed = [c for c in MANIFEST if c["tool_use_in_fixture"]]
    failures = []
    for case in flushed:
        fixture_path = os.path.join(FIXTURE_DIR, case["fixture"])
        tool_input = _extract_tool_input_from_fixture(
            fixture_path, case["tool_name"], case["file_path"]
        )
        if tool_input is None:
            failures.append(
                f"{case['fixture']}: _extract_tool_input_from_fixture returned None "
                f"(tool={case['tool_name']!r}, file={case['file_path']!r})"
            )
            continue
        override = get_current_turn_message_for_validation(
            fixture_path,
            tool_input=tool_input,
            tool_name=case["tool_name"],
            _max_wait_seconds=0.0,
        )
        if override is None:
            failures.append(
                f"{case['fixture']}: get_current_turn_message_for_validation returned "
                f"None for flushed fixture (tool={case['tool_name']!r}, "
                f"file={case['file_path']!r}) — matcher did not find the tool_use entry"
            )

    assert not failures, (
        f"Tool-matched anchor failed on {len(failures)} flushed fixture(s):\n"
        + "\n".join(f"  - {f}" for f in failures)
        + "\n\nThis means the matcher is NOT finding tool_use entries in flushed fixtures."
        "\nA Claude Code JSONL format change may have broken the byte-match used by "
        "get_current_turn_message_for_validation / _find_turn_matching_tool_input."
        "\nAll flushed validations would silently fail-open until this is fixed."
    )
