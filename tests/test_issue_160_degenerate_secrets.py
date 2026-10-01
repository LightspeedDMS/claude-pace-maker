#!/usr/bin/env python3
"""
Bug #160 item 1: a fragment of the mask marker stored as a secret.

A masked string ("... *** MASKED ***") that gets declared as a secret stores a
value that is a substring of the marker itself. Every masked value then
"contains a secret" (84 false hits in one leak audit). Contract:

- the store refuses degenerate values (empty/whitespace, substrings of the mask
  marker, text made only of marker repetitions): warning logged, no raise,
  nothing stored;
- masking ignores such values even if they are already in the database (the
  user's real rows are NOT modified - cleanup is the user's decision);
- the CLI can still show them (flagged) so they can be removed by id.
"""

import sqlite3
from unittest.mock import patch

import pytest

from pacemaker.secrets.database import (
    create_secret,
    get_all_secrets,
    list_secrets,
)
from pacemaker.secrets.masking import MASK_MARKER, is_degenerate_secret
from pacemaker.secrets.parser import parse_assistant_response
from pacemaker.secrets.sanitizer import sanitize_trace

REAL_SECRET = "hunter2-correct-horse-battery"
# The shape of the real offender: an 11-char fragment of "*** MASKED ***"
MARKER_FRAGMENT = MASK_MARKER[3:]  # " MASKED ***" (11 chars)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "usage.db")


def _insert_raw(db_path, secret_type, value):
    """Simulate a row that predates the fix (bypasses create_secret)."""
    create_secret(db_path, "text", REAL_SECRET)  # ensures schema exists
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO secrets (type, value) VALUES (?, ?)", (secret_type, value)
    )
    conn.commit()
    conn.close()


class TestIsDegenerateSecret:
    @pytest.mark.parametrize(
        "value",
        [
            "",
            "   ",
            "\n\t",
            MASK_MARKER,
            MARKER_FRAGMENT,
            "MASKED ***",
            "*** MASKED",
            "MASKED",
            "ASKED",
            "***",
            "*",
            " *** ",
            MASK_MARKER + " " + MASK_MARKER,
            MASK_MARKER + MASK_MARKER + MASK_MARKER,
        ],
    )
    def test_degenerate_values(self, value):
        assert is_degenerate_secret(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            REAL_SECRET,
            "abc123",
            "masked",  # case differs from the marker: an ordinary word
            "MASKED_BY_ADMIN",
            "token *** MASKED *** tail",  # real content around a marker
            "p@ss *",
        ],
    )
    def test_ordinary_values_are_not_degenerate(self, value):
        assert is_degenerate_secret(value) is False


class TestStoreRefusesDegenerateValues:
    def test_create_secret_refuses_marker_fragment(self, db_path):
        # the sink under test is pace-maker's own logger (stdlib logging has no
        # handler in hook processes, so a stdlib warning would be lost)
        with patch("pacemaker.secrets.database.log_warning") as warn:
            result = create_secret(db_path, "text", MARKER_FRAGMENT)

        assert result is None
        assert list_secrets(db_path) == []
        warn.assert_called_once()
        component, message = warn.call_args[0][:2]
        assert component == "secrets"
        assert "degenerate" in message.lower()
        # the refusal is reported, the value never is
        assert MARKER_FRAGMENT.strip() not in message
        assert "MASKED" not in message

    def test_create_secret_refuses_empty_and_whitespace(self, db_path):
        assert create_secret(db_path, "text", "") is None
        assert create_secret(db_path, "file", "   \n") is None
        assert list_secrets(db_path) == []

    def test_create_secret_still_stores_real_values(self, db_path):
        assert create_secret(db_path, "text", REAL_SECRET) is not None
        assert [s["value"] for s in list_secrets(db_path)] == [REAL_SECRET]

    def test_declaring_a_masked_string_stores_nothing(self, db_path):
        """The original path: a masked string pasted into a SECRET_TEXT line."""
        response = f"🔐 SECRET_TEXT: {MARKER_FRAGMENT}\n🔐 SECRET_TEXT: {REAL_SECRET}\n"

        stored = parse_assistant_response(response, db_path)

        assert [s["type"] for s in stored] == ["text"]
        assert [s["value"] for s in list_secrets(db_path)] == [REAL_SECRET]


class TestMaskingIgnoresStoredDegenerateValues:
    def test_get_all_secrets_excludes_degenerate_rows(self, db_path):
        _insert_raw(db_path, "text", MARKER_FRAGMENT)
        _insert_raw(db_path, "text", "  ")

        assert get_all_secrets(db_path) == [REAL_SECRET]

    def test_list_secrets_still_returns_them_for_cleanup(self, db_path):
        """Existing data is never modified or hidden from the owner."""
        _insert_raw(db_path, "text", MARKER_FRAGMENT)

        values = [s["value"] for s in list_secrets(db_path)]

        assert MARKER_FRAGMENT in values

    def test_sanitize_trace_does_not_remask_marker_or_ordinary_text(self, db_path):
        _insert_raw(db_path, "text", MARKER_FRAGMENT)
        trace = {"output": f"key={REAL_SECRET} and an already {MASK_MARKER} value"}

        sanitized, count = sanitize_trace(trace, db_path)

        assert sanitized["output"] == (
            f"key={MASK_MARKER} and an already {MASK_MARKER} value"
        )
        assert count == 1  # only the real secret; the marker was not re-masked

    def test_trace_with_only_marker_text_is_left_untouched(self, db_path):
        _insert_raw(db_path, "text", MARKER_FRAGMENT)
        trace = {"output": f"x {MASK_MARKER} y"}

        sanitized, count = sanitize_trace(trace, db_path)

        assert sanitized == trace
        assert count == 0


class TestSecretsCli:
    def test_list_flags_degenerate_rows(self, db_path):
        from pacemaker.user_commands import _execute_secrets

        _insert_raw(db_path, "text", MARKER_FRAGMENT)

        result = _execute_secrets(db_path, "list")

        assert result["success"] is True
        assert "degenerate" in result["message"].lower()

    def test_add_refuses_degenerate_value_with_clear_error(self, db_path):
        from pacemaker.user_commands import _execute_secrets

        with patch("getpass.getpass", return_value=MARKER_FRAGMENT):
            result = _execute_secrets(db_path, "add")

        assert result["success"] is False
        assert "degenerate" in result["message"].lower()
        assert list_secrets(db_path) == []
