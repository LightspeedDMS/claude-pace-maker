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
    override = get_current_turn_message_for_validation(transcript_path)
    current  = override or extract_current_assistant_message(messages)
    verdict  = _regex_stage1_check(current, file_path, exclusions)

If the hook glue changes, update this helper IN THE SAME COMMIT so the
replay stays faithful to production behavior.

Exclusions are PINNED (mirroring the shipped excluded_paths defaults) so the
suite is deterministic and independent of the developer's live config.
"""

import json
import os

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


def _replay_stage1(fixture_path: str, file_path: str) -> str:
    """Mirror the pre-tool hook's Stage-1 path exactly (see module docstring)."""
    messages = get_last_n_messages_for_validation(fixture_path, n=2)
    override = get_current_turn_message_for_validation(fixture_path)
    current = override or extract_current_assistant_message(messages)
    return _regex_stage1_check(current, file_path, PINNED_EXCLUSIONS)


@pytest.mark.parametrize(
    "case", MANIFEST, ids=[c["fixture"].replace(".jsonl", "") for c in MANIFEST]
)
def test_replay_real_transcript_case(case):
    """Each harvested real tool call must produce its spec-correct verdict."""
    fixture_path = os.path.join(FIXTURE_DIR, case["fixture"])
    assert os.path.exists(fixture_path), f"missing fixture: {case['fixture']}"

    verdict = _replay_stage1(fixture_path, case["file_path"])

    assert verdict == case["expected_stage1"], (
        f"\nfixture     : {case['fixture']}"
        f"\ncategory    : {case['category']}"
        f"\nfile_path   : {case['file_path']}"
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
