"""Telemetry canary tests for the Write/Edit fail-open (transcript-not-ready) branch.

Hardening item #2 from the bug-#83 architect review.

WHY THIS EXISTS
===============
When ``get_current_turn_message_for_validation`` returns ``None`` (the
transcript-flush race: Claude Code hasn't written the current tool_use to disk
yet), the Write/Edit gate silently fails open and returns ``{"continue": True}``.
Before this hardening, the only signal was a log_debug entry — invisible at the
default log level.  If a future Claude Code format change made the matcher always
return ``None``, validation would silently turn off with nothing observable.

WHAT THIS TESTS
===============
1. A WARNING-level log is emitted on the fail-open branch (visible at default
   log level and in log aggregators).
2. A ``record_blockage`` call is made with category
   ``"intent_validation_deferred"`` so the event appears in usage.db and
   blockage-stats dashboards.
3. The gate still returns ``{"continue": True}`` — fail-open semantics UNCHANGED.

MOCKING NOTE
============
Tests mock ``pacemaker.hook.get_current_turn_message_for_validation`` (the name
as imported into hook.py's namespace) to return ``None``, plus the minimum
infrastructure (config, transcript path, extension registry) to reach the
None-check without making real external calls.
"""

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# PACEMAKER_TEST_MODE must be set before any pacemaker import.
os.environ.setdefault("PACEMAKER_TEST_MODE", "1")

from pacemaker import database  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CORE_FILE = "/home/jsbattig/Dev/my_project/src/my_package/foo.py"
_NEW_STRING = "# replacement content\nresult = 42\n"


def _make_hook_stdin(
    tool_name: str, file_path: str, new_string: str, transcript_path: str
) -> str:
    """Craft the JSON stdin blob that run_pre_tool_hook reads."""
    if tool_name == "Write":
        tool_input = {"file_path": file_path, "content": new_string}
    else:
        tool_input = {"file_path": file_path, "new_string": new_string}
    return json.dumps(
        {
            "session_id": "test-session-deferred",
            "transcript_path": transcript_path,
            "tool_name": tool_name,
            "tool_input": tool_input,
        }
    )


def _config_enabled() -> dict:
    return {
        "enabled": True,
        "intent_validation_enabled": True,
        "tdd_enabled": True,
        "danger_bash_enabled": False,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDeferredCanary:
    """The fail-open branch must emit an observable signal (WARNING + blockage)."""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "usage.db")
        self.transcript = os.path.join(self.tmp_dir, "transcript.jsonl")
        # Write a minimal (empty) transcript so the file exists.
        Path(self.transcript).write_text("")
        database.initialize_database(self.db_path)

    def teardown_method(self):
        import shutil

        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _run_hook_with_none_override(self, tool_name: str, tmp_db_path: str) -> dict:
        """Run run_pre_tool_hook with get_current_turn_message_for_validation returning None."""
        from pacemaker.hook import run_pre_tool_hook

        stdin_payload = _make_hook_stdin(
            tool_name, _CORE_FILE, _NEW_STRING, self.transcript
        )

        with (
            patch("sys.stdin", MagicMock(read=lambda: stdin_payload)),
            patch("pacemaker.hook.load_config", return_value=_config_enabled()),
            patch("pacemaker.hook.DEFAULT_DB_PATH", tmp_db_path),
            # get_current_turn_message_for_validation returns None → fail-open branch
            patch(
                "pacemaker.hook.get_current_turn_message_for_validation",
                return_value=None,
            ),
            # get_last_n_messages_for_validation: only called before the None-check
            # so it doesn't matter what it returns here; avoid real transcript reads.
            patch(
                "pacemaker.hook.get_last_n_messages_for_validation",
                return_value=[],
            ),
            # Stub extension registry: mark the file as source code so the gate
            # continues past the "is_source" check and reaches the None-check.
            patch(
                "pacemaker.extension_registry.load_extensions",
                return_value=set(),
            ),
            patch(
                "pacemaker.extension_registry.is_source_code_file",
                return_value=True,
            ),
        ):
            return run_pre_tool_hook()

    def test_fail_open_returns_continue_true_for_edit(self):
        """Fail-open must still return {continue: True} — behaviour UNCHANGED."""
        result = self._run_hook_with_none_override("Edit", self.db_path)
        assert (
            result.get("continue") is True
        ), f"Expected {{continue: True}} from fail-open branch, got: {result}"

    def test_fail_open_returns_continue_true_for_write(self):
        """Fail-open must still return {continue: True} for Write tool too."""
        result = self._run_hook_with_none_override("Write", self.db_path)
        assert result.get("continue") is True

    def test_fail_open_emits_warning_log(self):
        """Fail-open branch must log at WARNING level (visible at default log_level)."""
        with patch("pacemaker.hook.log_warning") as mock_warn:
            self._run_hook_with_none_override("Edit", self.db_path)

        # At least one call should mention transcript-not-flushed / fail-open
        warning_calls = [str(c) for c in mock_warn.call_args_list]
        matching = [
            c
            for c in warning_calls
            if "deferred" in c.lower()
            or "transcript" in c.lower()
            or "flushed" in c.lower()
        ]
        assert matching, (
            "Expected a WARNING log mentioning transcript-not-flushed / deferred on fail-open; "
            f"got calls: {warning_calls}"
        )

    def test_fail_open_records_deferred_blockage_event(self):
        """Fail-open branch must record intent_validation_deferred in usage.db."""
        self._run_hook_with_none_override("Edit", self.db_path)

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT category, hook_type FROM blockage_events "
                "WHERE category = 'intent_validation_deferred'"
            ).fetchall()
        finally:
            conn.close()

        assert rows, (
            "Expected an 'intent_validation_deferred' blockage event in usage.db "
            "when Write/Edit gate hits the None / fail-open branch, but found none."
        )
        categories = {r[0] for r in rows}
        assert "intent_validation_deferred" in categories

    def test_fail_open_blockage_event_has_correct_hook_type(self):
        """Deferred blockage event must have hook_type='pre_tool_use'."""
        self._run_hook_with_none_override("Edit", self.db_path)

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT hook_type FROM blockage_events "
                "WHERE category = 'intent_validation_deferred'"
            ).fetchall()
        finally:
            conn.close()

        assert rows, "Expected deferred blockage event."
        hook_types = {r[0] for r in rows}
        assert (
            "pre_tool_use" in hook_types
        ), f"Expected hook_type='pre_tool_use', got: {hook_types}"

    def test_fail_open_blockage_event_for_write_too(self):
        """Deferred blockage event is recorded for Write tool, not only Edit."""
        self._run_hook_with_none_override("Write", self.db_path)

        conn = sqlite3.connect(self.db_path)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM blockage_events "
                "WHERE category = 'intent_validation_deferred'"
            ).fetchone()[0]
        finally:
            conn.close()

        assert (
            count >= 1
        ), "Expected at least one 'intent_validation_deferred' event for Write tool."


class TestDeferredCategoryInConstants:
    """intent_validation_deferred must be a recognized BLOCKAGE_CATEGORIES entry."""

    def test_category_in_blockage_categories(self):
        from pacemaker.constants import BLOCKAGE_CATEGORIES

        assert "intent_validation_deferred" in BLOCKAGE_CATEGORIES, (
            "'intent_validation_deferred' must be in BLOCKAGE_CATEGORIES "
            "so record_blockage does not fall back to 'other'."
        )

    def test_category_has_label(self):
        from pacemaker.constants import BLOCKAGE_CATEGORY_LABELS

        assert "intent_validation_deferred" in BLOCKAGE_CATEGORY_LABELS, (
            "'intent_validation_deferred' must have a human-readable label in "
            "BLOCKAGE_CATEGORY_LABELS for CLI display."
        )

    def test_category_label_is_non_empty_string(self):
        from pacemaker.constants import BLOCKAGE_CATEGORY_LABELS

        label = BLOCKAGE_CATEGORY_LABELS.get("intent_validation_deferred", "")
        assert (
            isinstance(label, str) and label.strip()
        ), "BLOCKAGE_CATEGORY_LABELS['intent_validation_deferred'] must be a non-empty string."
