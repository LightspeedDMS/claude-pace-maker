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
    """Regex-free statement of the contract.

    Phase 1 masks the 8+ char secrets anywhere (leftmost, longest first). Phase 2
    masks the <8 char secrets only inside the text BETWEEN phase-1 matches (a
    phase-1 match is an impenetrable, non-word separator), as standalone tokens:
    the lookbehind applies only if the secret's first char is a word char, the
    lookahead only if its last char is one (so punctuation edges need none).
    """
    ordered = sorted((s for s in secrets if s), key=len, reverse=True)
    long_secrets = [s for s in ordered if len(s) >= 8]
    short_secrets = [s for s in ordered if len(s) < 8]

    segments, current, i, count = [], [], 0, 0
    while i < len(text):
        for secret in long_secrets:
            if text.startswith(secret, i):
                segments.append("".join(current))
                current = []
                i += len(secret)
                count += 1
                break
        else:
            current.append(text[i])
            i += 1
    segments.append("".join(current))

    masked_segments = []
    for seg in segments:
        out, i = [], 0
        while i < len(seg):
            for secret in short_secrets:
                if not seg.startswith(secret, i):
                    continue
                end = i + len(secret)
                before = seg[i - 1] if i > 0 else ""
                after = seg[end] if end < len(seg) else ""
                if secret[0] in WORD and before in WORD:
                    continue
                if secret[-1] in WORD and after in WORD:
                    continue
                out.append(MASK_MARKER)
                i = end
                count += 1
                break
            else:
                out.append(seg[i])
                i += 1
        masked_segments.append("".join(out))
    return MASK_MARKER.join(masked_segments), count


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


class TestLongSecretsAreNeverUnderMasked:
    """Review finding M1: a short alternative that fails its boundary must never
    let another short secret match across the start of an 8+ char secret."""

    SECRETS = ["z.a", "a-k", "k.LONGSECRETVALUE"]
    TEXT = "wz.a-k.LONGSECRETVALUE end"

    def test_reviewer_repro_does_not_expose_the_long_secret(self):
        masked, count = mask_text(self.TEXT, self.SECRETS)

        assert "LONGSECRETVALUE" not in masked
        assert masked == f"wz.a-{MASK_MARKER} end"
        assert count == 1

    def test_repro_through_sanitize_trace(self, db):
        _store(db, self.SECRETS)

        sanitized, count = sanitize_trace({"o": self.TEXT}, db)

        assert "LONGSECRETVALUE" not in sanitized["o"]
        assert (sanitized["o"], count) == reference_mask(self.TEXT, self.SECRETS)

    def test_short_secret_can_not_span_into_a_masked_long_secret(self):
        # "d***" would match "d" + the marker the first pass inserted
        masked, _ = mask_text("dLONGSECRETVALUE", ["d***", "LONGSECRETVALUE"])
        assert masked == f"d{MASK_MARKER}"

    def test_short_secret_next_to_a_long_one_is_still_masked_standalone(self):
        masked, count = mask_text("tok=LONGSECRETVALUE;", ["tok", "LONGSECRETVALUE"])
        assert masked == f"{MASK_MARKER}={MASK_MARKER};"
        assert count == 2

    def test_mask_structure_uses_the_same_two_pass_semantics(self):
        from pacemaker.secrets.masking import mask_structure

        masked, count = mask_structure({"a": [self.TEXT]}, self.SECRETS)

        assert "LONGSECRETVALUE" not in masked["a"][0]
        assert (masked["a"][0], count) == reference_mask(self.TEXT, self.SECRETS)


class TestPunctuationEdgesNeedNoBoundary:
    """Review finding L1: the lookbehind applies only when the secret's first
    char is a word char, the lookahead only when its last char is one."""

    def test_leading_punctuation_secret_matches_after_a_word_char(self):
        assert mask_text("x-abc", ["-abc"]) == (f"x{MASK_MARKER}", 1)

    def test_trailing_punctuation_secret_matches_before_a_word_char(self):
        assert mask_text("abc-x", ["abc-"]) == (f"{MASK_MARKER}x", 1)

    def test_word_edge_of_the_same_secret_is_still_protected(self):
        # "-abc" ends in a word char: followed by "d" it is part of a longer token
        assert mask_text("x-abcd", ["-abc"]) == ("x-abcd", 0)
        # "abc-" starts with a word char: preceded by "x" it is not standalone
        assert mask_text("xabc-", ["abc-"]) == ("xabc-", 0)

    def test_fully_punctuation_secret_is_masked_anywhere(self):
        assert mask_text("a.=.b", [".=."]) == (f"a{MASK_MARKER}b", 1)


class TestPunctuationAndAdjacencyFuzz:
    def test_fuzz_matches_reference_and_never_leaves_a_long_secret(self, db):
        rng = random.Random(1603)
        alphabet = "ab_-.="
        values = {
            "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 12)))
            for _ in range(70)
        }
        secrets = _store(db, sorted(values))
        long_secrets = [s for s in secrets if len(s) >= 8]
        assert long_secrets and any(len(s) < 8 for s in secrets)

        def occurrences(text):
            spans = []
            for secret in long_secrets:
                start = text.find(secret)
                while start != -1:
                    spans.append((start, start + len(secret)))
                    start = text.find(secret, start + 1)
            return spans

        def long_occurrences_overlap(text):
            spans = sorted(occurrences(text))
            return any(a[1] > b[0] for a, b in zip(spans, spans[1:]))

        def adjacent_text():
            # secrets glued to each other, to filler and to random characters
            pieces = []
            for _ in range(rng.randint(1, 6)):
                pieces.append(
                    rng.choice(secrets)
                    if rng.random() < 0.6
                    else "".join(rng.choice(alphabet + " :") for _ in range(2))
                )
            return "".join(pieces)

        checked_invariant = 0
        for _ in range(600):
            text = adjacent_text()

            sanitized, count = sanitize_trace({"t": text}, db)

            assert (sanitized["t"], count) == reference_mask(text, secrets)
            if occurrences(text) and not long_occurrences_overlap(text):
                checked_invariant += 1
                for secret in long_secrets:
                    assert secret not in sanitized["t"], (text, secret)
        assert checked_invariant > 50  # the invariant was really exercised
