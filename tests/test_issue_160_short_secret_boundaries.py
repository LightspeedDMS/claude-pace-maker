#!/usr/bin/env python3
"""
Bug #160 item 2: short stored secrets masked ordinary words in Langfuse traces
("raises_value_error" -> "raises_*** MASKED ***_error" for a stored "value").

User decision (token-boundary match): in Langfuse masking, a stored secret
SHORTER than 8 characters is masked only where it stands alone as a token --
not immediately preceded or followed by [A-Za-z0-9_]. Secrets of 8+ characters
keep match-anywhere behaviour. Short secrets are still accepted for storage.
Reviewer-prompt masking keeps its own min-length-8 rule (unchanged). The #157
prefilter must stay output-correct and the longest-first alternation semantics
must hold.

The reference oracle below is an independent, regex-free implementation of the
specified semantics; the fuzz test pins ``sanitize_trace`` (prefilter + pattern)
to it.
"""

import random

import pytest

from pacemaker.secrets.database import create_secret, get_all_secrets
from pacemaker.secrets.masking import (
    MASK_MARKER,
    SHORT_SECRET_TOKEN_BOUNDARY_BELOW,
    build_prefiltered_pattern,
    mask_text,
)
from pacemaker.secrets.sanitizer import sanitize_trace

WORD = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def reference_mask(text, secrets):
    """Regex-free statement of the contract (longest-first, first match wins)."""
    ordered = sorted((s for s in secrets if s), key=len, reverse=True)
    out, i, count = [], 0, 0
    while i < len(text):
        for secret in ordered:
            if not text.startswith(secret, i):
                continue
            if len(secret) < 8:
                before = text[i - 1] if i > 0 else ""
                end = i + len(secret)
                after = text[end] if end < len(text) else ""
                if before in WORD or after in WORD:
                    continue
            out.append(MASK_MARKER)
            i += len(secret)
            count += 1
            break
        else:
            out.append(text[i])
            i += 1
    return "".join(out), count


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "secrets.db")


def _store(db, values):
    for value in values:
        create_secret(db, "text", value)
    return get_all_secrets(db)


class TestShortSecretTokenBoundary:
    def test_threshold_is_eight(self):
        assert SHORT_SECRET_TOKEN_BOUNDARY_BELOW == 8

    def test_word_containing_a_short_secret_is_not_mangled(self):
        masked, count = mask_text("raises_value_error", ["value"])
        assert masked == "raises_value_error"
        assert count == 0

    @pytest.mark.parametrize(
        "text",
        ["values", "xvalue", "value_x", "_value", "value1", "9value", "avaluez"],
    )
    def test_adjacent_word_characters_prevent_masking(self, text):
        assert mask_text(text, ["value"]) == (text, 0)

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("value", MASK_MARKER),
            ("a value b", f"a {MASK_MARKER} b"),
            ("x=value", f"x={MASK_MARKER}"),
            ("key:value;", f"key:{MASK_MARKER};"),
            ("(value)", f"({MASK_MARKER})"),
            ("value-1", f"{MASK_MARKER}-1"),
            ("a.value.b", f"a.{MASK_MARKER}.b"),
            ('"value"', f'"{MASK_MARKER}"'),
            ("line1\nvalue\nline3", f"line1\n{MASK_MARKER}\nline3"),
            ("value value", f"{MASK_MARKER} {MASK_MARKER}"),
        ],
    )
    def test_standalone_short_secret_is_masked(self, text, expected):
        masked, _count = mask_text(text, ["value"])
        assert masked == expected

    def test_start_and_end_of_string_count_as_boundaries(self):
        assert mask_text("value x", ["value"])[0] == f"{MASK_MARKER} x"
        assert mask_text("x value", ["value"])[0] == f"x {MASK_MARKER}"

    def test_non_ascii_neighbour_is_not_a_word_character(self):
        # Boundary class is exactly [A-Za-z0-9_]
        assert mask_text("évalueé", ["value"])[0] == f"é{MASK_MARKER}é"

    def test_seven_chars_is_short_eight_is_long(self):
        seven, eight = "abcdefg", "abcdefgh"
        assert mask_text(f"x{seven}x", [seven]) == (f"x{seven}x", 0)
        assert mask_text(f"x{eight}x", [eight]) == (f"x{MASK_MARKER}x", 1)

    def test_long_secrets_keep_match_anywhere(self):
        text = "pre_abcdefghij_post and abcdefghij"
        masked, count = mask_text(text, ["abcdefghij"])
        assert masked == f"pre_{MASK_MARKER}_post and {MASK_MARKER}"
        assert count == 2

    def test_longest_first_still_wins_with_boundaries(self):
        # "abc" is preferred over its prefix "ab" where both would match
        assert mask_text("abc", ["ab", "abc"]) == (MASK_MARKER, 1)
        # "abc" fails its boundary here ("abcd" is one word); "ab" fails too
        assert mask_text("abcd", ["ab", "abc"]) == ("abcd", 0)
        # "abc" is not standalone but "ab" followed by "-" is
        assert mask_text("ab-c abc", ["abc", "ab"]) == (
            f"{MASK_MARKER}-c {MASK_MARKER}",
            2,
        )

    def test_long_secret_containing_a_short_one_masks_as_a_whole(self):
        masked, count = mask_text(
            "a raises_value_error b", ["value", "raises_value_error"]
        )
        assert masked == f"a {MASK_MARKER} b"
        assert count == 1

    def test_regex_metacharacters_in_short_secret(self):
        assert mask_text("x=a.b y", ["a.b"])[0] == f"x={MASK_MARKER} y"
        assert mask_text("xa.by", ["a.b"]) == ("xa.by", 0)
        assert mask_text("axb", ["a.b"]) == ("axb", 0)


class TestSanitizeTraceEndToEnd:
    def test_langfuse_trace_keeps_words_and_masks_standalone_tokens(self, db):
        _store(db, ["value", "hunter2-correct-horse"])
        trace = [
            {
                "body": {
                    "input": "raises_value_error values value=1 hunter2-correct-horse",
                    "metadata": {"k": ["value"]},
                }
            }
        ]

        sanitized, count = sanitize_trace(trace, db)

        body = sanitized[0]["body"]
        assert body["input"] == (
            f"raises_value_error values {MASK_MARKER}=1 {MASK_MARKER}"
        )
        assert body["metadata"] == {"k": [MASK_MARKER]}
        assert count == 3

    def test_short_secrets_are_still_accepted_for_storage(self, db):
        assert create_secret(db, "text", "abc123") is not None
        assert get_all_secrets(db) == ["abc123"]

    def test_prefilter_keeps_a_short_secret_that_only_occurs_inside_a_word(self):
        relevant, pattern = build_prefiltered_pattern(["value"], ["raises_value_error"])
        assert relevant == ["value"]  # superset check: occurs as a substring
        assert pattern is not None
        # ...but the pattern itself will not mask it
        assert mask_text("raises_value_error", relevant, pattern)[0] == (
            "raises_value_error"
        )

    def test_reviewer_prompt_masking_rule_is_unchanged(self, db):
        from pacemaker.intent_validator import _mask_reviewer_prompt

        _store(db, ["value", "longer-secret-value"])
        masked = _mask_reviewer_prompt("a value and longer-secret-value b", db_path=db)
        assert "value and" in masked  # short secret: skipped (min length 8)
        assert "longer-secret-value" not in masked


class TestEquivalenceWithIndependentOracle:
    def test_fuzz_sanitize_trace_matches_reference_semantics(self, db):
        rng = random.Random(160)
        alphabet = "ab_-="
        values = {
            "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 10)))
            for _ in range(60)
        }
        secrets = _store(db, sorted(values))
        assert any(len(s) < 8 for s in secrets) and any(len(s) >= 8 for s in secrets)

        def random_text():
            return "".join(
                rng.choice(alphabet + "  .:") for _ in range(rng.randint(0, 45))
            )

        for _ in range(300):
            texts = [random_text() for _ in range(rng.randint(1, 3))]
            trace = {"x": texts, "y": {"z": random_text()}}

            sanitized, count = sanitize_trace(trace, db)

            expected_x = [reference_mask(t, secrets) for t in texts]
            expected_z = reference_mask(trace["y"]["z"], secrets)
            assert sanitized["x"] == [m for m, _ in expected_x]
            assert sanitized["y"]["z"] == expected_z[0]
            assert count == sum(c for _, c in expected_x) + expected_z[1]
