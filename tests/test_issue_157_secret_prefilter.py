"""
Bug #157 -- the shared secret prefilter.

Every Langfuse push used to compile ONE regex from ALL stored secrets in every
hook process (765 secrets / 5.2 MB on the reporter's machine: ~8 s of CPU per
SubagentStart, which blew the 10 s hook timeout). The fix keeps only the
secrets that actually OCCUR in the payload before compiling
(``secrets.masking.build_prefiltered_pattern``), used by BOTH
``sanitizer.sanitize_trace`` and ``intent_validator._mask_reviewer_prompt``.

The masked output must be byte-identical to the old full-store masking for
ANY input -- these tests pin that against the old behaviour reproduced inline
(``mask_structure`` with a pattern built from the whole store).
"""

import random
import string
import time

import pytest

from pacemaker.secrets.database import create_secret
from pacemaker.secrets.masking import (
    _build_secrets_pattern,
    build_prefiltered_pattern,
    collect_strings,
    mask_structure,
)
from pacemaker.secrets.sanitizer import sanitize_trace


def old_full_store_masking(trace, secrets):
    """The pre-#157 behaviour: compile a pattern from the WHOLE store."""
    return mask_structure(trace, secrets, _build_secrets_pattern(secrets))


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "secrets.db")


def _store(db, values):
    for value in values:
        create_secret(db, "text", value)
    from pacemaker.secrets.database import get_all_secrets

    return get_all_secrets(db)


class TestCollectStrings:
    def test_collects_values_from_dicts_lists_and_tuples_but_not_keys(self):
        data = {"k-secret": ["a", ("b", {"x": "c"})], "n": 5, "none": None}
        assert sorted(collect_strings(data)) == ["a", "b", "c"]

    def test_plain_string_and_non_strings(self):
        assert collect_strings("abc") == ["abc"]
        assert collect_strings(5) == []
        assert collect_strings(None) == []


class TestBuildPrefilteredPattern:
    def test_keeps_only_secrets_that_occur_in_the_payload(self):
        relevant, pattern = build_prefiltered_pattern(
            ["alpha-secret", "beta-secret", "gamma-secret"],
            ["xx beta-secret yy"],
        )
        assert relevant == ["beta-secret"]
        assert pattern.search("beta-secret")
        assert not pattern.search("alpha-secret")

    def test_no_relevant_secret_means_no_pattern(self):
        assert build_prefiltered_pattern(["abc"], ["zzz"]) == ([], None)

    def test_no_secrets_or_no_texts(self):
        assert build_prefiltered_pattern([], ["abc"]) == ([], None)
        assert build_prefiltered_pattern(["abc"], []) == ([], None)

    def test_empty_secrets_are_ignored(self):
        assert build_prefiltered_pattern(["", "abc"], ["abc"])[0] == ["abc"]

    def test_original_order_is_preserved(self):
        relevant, _ = build_prefiltered_pattern(["ccc", "aaa", "bbb"], ["aaa bbb ccc"])
        assert relevant == ["ccc", "aaa", "bbb"]

    def test_duplicates_in_store_do_not_break_it(self):
        relevant, pattern = build_prefiltered_pattern(["abc", "abc"], ["abc"])
        assert pattern.sub("*", "abc") == "*"

    def test_secret_longer_than_every_text_is_skipped_cheaply(self):
        assert build_prefiltered_pattern(["x" * 100000], ["short"]) == ([], None)


class TestIdenticalOutputToTheOldFullStoreMasking:
    def _check(self, db, store_values, trace):
        secrets = _store(db, store_values)
        old = old_full_store_masking(trace, secrets)
        new = sanitize_trace(trace, db)
        assert new == old
        return new

    def test_simple_secret_in_nested_trace(self, db):
        trace = [
            {
                "id": "t",
                "body": {
                    "input": "key=hunter2xyz ok",
                    "metadata": {"a": ["hunter2xyz"]},
                },
            }
        ]
        masked, count = self._check(db, ["hunter2xyz", "unrelated-secret"], trace)
        assert "hunter2xyz" not in str(masked)
        assert count == 2

    def test_prefix_and_overlapping_secrets_longest_wins(self, db):
        store = ["abc", "abcdef", "abcdefgh", "defgh", "cdefg"]
        trace = {"a": "xx abcdefgh yy abcdef zz abc cdefg", "b": ["defgh abcdefghabc"]}
        self._check(db, store, trace)

    @pytest.mark.parametrize(
        "store",
        [
            ["abcdefgh", "abc", "abcdef"],
            ["abc", "abcdef", "abcdefgh"],
            ["abcdef", "abcdefgh", "abc"],
        ],
    )
    def test_store_order_does_not_matter(self, db, store):
        trace = {"a": "abcdefgh abcdef abc abcdefghabc"}
        self._check(db, store, trace)

    def test_secret_spanning_two_fields_is_not_masked_in_either_version(self, db):
        trace = {"first": "pass", "second": "word-1234", "joined_elsewhere": "no"}
        masked, count = self._check(db, ["password-1234"], trace)
        assert count == 0
        assert masked == trace

    def test_secret_only_in_a_dict_key_is_not_masked_in_either_version(self, db):
        trace = {"my-secret-key": "value"}
        masked, count = self._check(db, ["my-secret-key"], trace)
        assert count == 0
        assert "my-secret-key" in masked

    def test_regex_metacharacters_in_secrets(self, db):
        store = ["a.b*c+d?", "(x|y)[z]{2}", "back\\slash", "^start$"]
        trace = {"t": "a.b*c+d? and (x|y)[z]{2} and back\\slash and ^start$ axb"}
        self._check(db, store, trace)

    def test_unicode_and_multiline_secrets(self, db):
        store = ["pässwörd→✓", "line1\nline2"]
        trace = {"t": "x pässwörd→✓ y\nline1\nline2 z"}
        self._check(db, store, trace)

    def test_non_string_values_tuples_and_none_pass_through(self, db):
        trace = {
            "n": 5,
            "f": 1.5,
            "b": True,
            "none": None,
            "tup": ("sekret-1", 3),
            "l": [None, "sekret-1"],
        }
        self._check(db, ["sekret-1"], trace)

    def test_user_id_is_still_protected(self, db):
        trace = [
            {
                "id": "e",
                "body": {"userId": "user@example.com", "input": "user@example.com"},
            }
        ]
        secrets = _store(db, ["user@example.com"])
        masked, _ = sanitize_trace(trace, db)
        assert masked[0]["body"]["userId"] == "user@example.com"
        assert "user@example.com" not in masked[0]["body"]["input"]
        # Apart from the protected userId restore, identical to old masking.
        old, _ = old_full_store_masking(trace, secrets)
        old[0]["body"]["userId"] = "user@example.com"
        assert masked == old

    def test_no_secrets_stored(self, db):
        trace = {"a": "anything"}
        assert sanitize_trace(trace, db) == (trace, 0)

    def test_clean_trace_with_a_populated_store_is_untouched(self, db):
        self._check(db, ["sekret-aaa", "sekret-bbb"], {"a": ["nothing", {"b": "here"}]})

    def test_repeated_occurrences_are_all_masked_and_counted(self, db):
        masked, count = self._check(
            db, ["tok-123456"], {"a": "tok-123456 tok-123456", "b": "tok-123456"}
        )
        assert count == 3

    def test_secret_that_is_a_substring_of_another_stored_secret(self, db):
        # "abc" occurs in the payload only INSIDE "abcdef": the old pattern
        # (longest first) masks the longer one; the subset must too.
        self._check(db, ["abc", "abcdef"], {"a": "xx abcdef yy"})

    def test_fuzz_random_small_alphabet_stores_and_traces(self, db):
        rng = random.Random(157)
        alphabet = "abc.*"
        values = {
            "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 6)))
            for _ in range(60)
        }
        secrets = _store(db, sorted(values))
        for _ in range(150):
            trace = {
                "x": [
                    "".join(
                        rng.choice(alphabet + "  ") for _ in range(rng.randint(0, 40))
                    )
                    for _ in range(rng.randint(1, 4))
                ],
                "y": {
                    "z": "".join(
                        rng.choice(alphabet) for _ in range(rng.randint(0, 25))
                    )
                },
            }
            assert sanitize_trace(trace, db) == old_full_store_masking(trace, secrets)

    def test_langfuse_masking_has_no_minimum_secret_length(self, db):
        """Unlike reviewer-prompt masking (min 8, issue #153), Langfuse
        masking keeps masking short secrets, exactly as before."""
        masked, count = self._check(db, ["abc"], {"a": "xx abc yy"})
        assert count == 1


class TestReviewerPromptMaskingUsesTheSharedHelper:
    def test_output_matches_old_behaviour_and_keeps_the_min_length_rule(self, db):
        from pacemaker.intent_validator import _mask_reviewer_prompt

        _store(db, ["abc", "longer-secret-value", "another-secret-9"])
        prompt = "see abc and longer-secret-value and another-secret-9 end"
        masked = _mask_reviewer_prompt(prompt, db_path=db)
        assert "longer-secret-value" not in masked
        assert "another-secret-9" not in masked
        assert "abc" in masked  # shorter than 8 chars: never masked here

    def test_it_calls_build_prefiltered_pattern(self, db, monkeypatch):
        import pacemaker.secrets.masking as masking
        from pacemaker.intent_validator import _mask_reviewer_prompt

        calls = []
        real = masking.build_prefiltered_pattern

        def spy(secrets, texts):
            calls.append((list(secrets), list(texts)))
            return real(secrets, texts)

        monkeypatch.setattr(masking, "build_prefiltered_pattern", spy)
        _store(db, ["longer-secret-value"])
        _mask_reviewer_prompt("x longer-secret-value y", db_path=db)
        assert len(calls) == 1
        assert calls[0][1] == ["x longer-secret-value y"]


class TestPerformanceOnARealShapedStore:
    """The reporter's store: 765 secrets, ~5.2 MB total, 29 values over 50 KB
    (SECRET_FILE stores whole files). Full-store compile took ~8 s; with the
    prefilter a clean ~6 KB trace must be well under 0.5 s."""

    @pytest.fixture(scope="class")
    def big_db(self, tmp_path_factory):
        """Built ONCE for the class, with one bulk insert (765 separate
        create_secret() connections alone cost ~30 s)."""
        import sqlite3

        from pacemaker.secrets.database import _init_database

        db = str(tmp_path_factory.mktemp("bigstore") / "secrets.db")
        rng = random.Random(7)
        chars = string.ascii_letters + string.digits + "+/=-_ "
        # 29 large "file" secrets (100-250 KB), the bulk of ~5 MB ...
        big = [
            "".join(rng.choices(chars, k=rng.randint(100_000, 250_000)))
            for _ in range(29)
        ]
        # ... plus 736 ordinary ones.
        small = ["".join(rng.choices(chars, k=rng.randint(12, 90))) for _ in range(736)]
        _init_database(db)
        conn = sqlite3.connect(db)
        try:
            conn.executemany(
                "INSERT INTO secrets (type, value) VALUES (?, ?)",
                [("file", v) for v in big] + [("text", v) for v in small],
            )
            conn.commit()
        finally:
            conn.close()
        return db

    def test_store_has_the_real_shape(self, big_db):
        from pacemaker.secrets.database import get_all_secrets

        secrets = get_all_secrets(big_db)
        assert len(secrets) == 765
        assert sum(len(s) for s in secrets) > 4_000_000
        assert sum(1 for s in secrets if len(s) > 50_000) == 29

    def test_clean_6kb_trace_sanitizes_well_under_half_a_second(self, big_db):
        trace = [
            {
                "id": "t",
                "type": "trace-create",
                "body": {
                    "input": "Implement the fix for the parser. " * 180,
                    "metadata": {"project_path": "/x", "tags": ["a", "b"]},
                },
            }
        ]
        sanitize_trace(trace, big_db)  # warm sqlite/page cache like a real run
        start = time.monotonic()
        masked, count = sanitize_trace(trace, big_db)
        elapsed = time.monotonic() - start
        assert count == 0 and masked == trace
        assert elapsed < 0.5, f"sanitize_trace took {elapsed:.3f}s"

    def test_planted_secrets_are_still_masked_fast(self, big_db):
        from pacemaker.secrets.database import get_all_secrets

        secrets = get_all_secrets(big_db)
        small = next(s for s in secrets if len(s) < 100)
        large = next(s for s in secrets if len(s) > 50_000)
        trace = [
            {"id": "t", "body": {"input": f"a {small} b {large} c", "m": {"k": small}}}
        ]
        start = time.monotonic()
        masked, count = sanitize_trace(trace, big_db)
        assert time.monotonic() - start < 2.0
        assert count == 3
        assert small not in str(masked) and large not in str(masked)
        # (Equality with the old whole-store masking is pinned by the small
        # equivalence tests above; recompiling the whole 5 MB store here would
        # cost the ~8 s this fix removes.)
