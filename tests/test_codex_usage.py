"""
Unit tests for pacemaker.codex_usage module.

Tests cover:
- JSONL parsing (valid, no events, malformed, missing fields)
- SQLite DB read/write (write, overwrite, read empty)
- Edge cases (no sessions dir, multiple files — picks most recent)
"""

import json
import os
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ["PACEMAKER_TEST_MODE"] = "1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, events: list) -> None:
    """Write a list of event dicts as newline-delimited JSON to path."""
    with open(path, "w") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")


def _token_count_event(
    primary_pct: float = 12.0,
    secondary_pct: float = 36.0,
    primary_resets_at: int = 1775290732,
    secondary_resets_at: int = 1775752626,
    plan_type: str = "team",
) -> dict:
    """Return a realistic token_count event dict."""
    return {
        "timestamp": "2026-04-03T12:04:59.187Z",
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "rate_limits": {
                "limit_id": "codex",
                "primary": {
                    "used_percent": primary_pct,
                    "window_minutes": 300,
                    "resets_at": primary_resets_at,
                },
                "secondary": {
                    "used_percent": secondary_pct,
                    "window_minutes": 10080,
                    "resets_at": secondary_resets_at,
                },
                "plan_type": plan_type,
            },
        },
    }


# ---------------------------------------------------------------------------
# 1. test_parse_valid_session_file
# ---------------------------------------------------------------------------


def test_parse_valid_session_file(tmp_path):
    """Parser extracts correct values from a JSONL with a token_count event."""
    from pacemaker.codex_usage import get_latest_codex_usage

    today = time.strftime("%Y/%m/%d")
    session_dir = tmp_path / today
    session_dir.mkdir(parents=True)
    session_file = session_dir / "session.jsonl"
    _write_jsonl(
        session_file, [_token_count_event(primary_pct=12.0, secondary_pct=36.0)]
    )

    result = get_latest_codex_usage(sessions_dir=str(tmp_path))

    assert result is not None
    assert result["primary_used_pct"] == 12.0
    assert result["secondary_used_pct"] == 36.0
    assert result["primary_resets_at"] == 1775290732
    assert result["secondary_resets_at"] == 1775752626
    assert result["plan_type"] == "team"
    assert isinstance(result["timestamp"], float)


# ---------------------------------------------------------------------------
# 2. test_parse_no_token_count
# ---------------------------------------------------------------------------


def test_parse_no_token_count(tmp_path):
    """JSONL without any token_count events returns None."""
    from pacemaker.codex_usage import get_latest_codex_usage

    today = time.strftime("%Y/%m/%d")
    session_dir = tmp_path / today
    session_dir.mkdir(parents=True)
    session_file = session_dir / "session.jsonl"
    other_event = {"timestamp": "2026-04-03T12:00:00Z", "type": "other", "payload": {}}
    _write_jsonl(session_file, [other_event])

    result = get_latest_codex_usage(sessions_dir=str(tmp_path))

    assert result is None


# ---------------------------------------------------------------------------
# 3. test_parse_malformed_json
# ---------------------------------------------------------------------------


def test_parse_malformed_json(tmp_path):
    """Gracefully handles malformed lines — valid event after bad line is found."""
    from pacemaker.codex_usage import get_latest_codex_usage

    today = time.strftime("%Y/%m/%d")
    session_dir = tmp_path / today
    session_dir.mkdir(parents=True)
    session_file = session_dir / "session.jsonl"

    with open(session_file, "w") as f:
        f.write("this is not json\n")
        f.write(json.dumps(_token_count_event(primary_pct=25.0)) + "\n")

    result = get_latest_codex_usage(sessions_dir=str(tmp_path))

    assert result is not None
    assert result["primary_used_pct"] == 25.0


# ---------------------------------------------------------------------------
# 4. test_parse_missing_rate_limits
# ---------------------------------------------------------------------------


def test_parse_missing_rate_limits(tmp_path):
    """token_count event without rate_limits field returns None."""
    from pacemaker.codex_usage import get_latest_codex_usage

    today = time.strftime("%Y/%m/%d")
    session_dir = tmp_path / today
    session_dir.mkdir(parents=True)
    session_file = session_dir / "session.jsonl"

    event_without_rate_limits = {
        "timestamp": "2026-04-03T12:00:00Z",
        "type": "event_msg",
        "payload": {"type": "token_count"},
    }
    _write_jsonl(session_file, [event_without_rate_limits])

    result = get_latest_codex_usage(sessions_dir=str(tmp_path))

    assert result is None


# ---------------------------------------------------------------------------
# 5. test_write_and_read_codex_usage
# ---------------------------------------------------------------------------


def test_write_and_read_codex_usage(tmp_path):
    """Write usage data to DB, read it back, verify all fields match."""
    from pacemaker.codex_usage import write_codex_usage, read_codex_usage
    from pacemaker.database import initialize_database

    db_path = str(tmp_path / "test.db")
    initialize_database(db_path)

    usage = {
        "primary_used_pct": 15.0,
        "secondary_used_pct": 42.0,
        "primary_resets_at": 1775290732,
        "secondary_resets_at": 1775752626,
        "plan_type": "team",
        "timestamp": time.time(),
    }

    write_codex_usage(db_path, usage)
    result = read_codex_usage(db_path)

    assert result is not None
    assert result["primary_used_pct"] == 15.0
    assert result["secondary_used_pct"] == 42.0
    assert result["primary_resets_at"] == 1775290732
    assert result["secondary_resets_at"] == 1775752626
    assert result["plan_type"] == "team"


# ---------------------------------------------------------------------------
# 6. test_write_overwrites_existing
# ---------------------------------------------------------------------------


def test_write_overwrites_existing(tmp_path):
    """Second write replaces the first (single deterministic record, id=1)."""
    from pacemaker.codex_usage import write_codex_usage, read_codex_usage
    from pacemaker.database import initialize_database

    db_path = str(tmp_path / "test.db")
    initialize_database(db_path)

    usage_first = {
        "primary_used_pct": 10.0,
        "secondary_used_pct": 20.0,
        "primary_resets_at": 1000000,
        "secondary_resets_at": 2000000,
        "plan_type": "pro",
        "timestamp": time.time(),
    }
    usage_second = {
        "primary_used_pct": 55.0,
        "secondary_used_pct": 77.0,
        "primary_resets_at": 3000000,
        "secondary_resets_at": 4000000,
        "plan_type": "team",
        "timestamp": time.time(),
    }

    write_codex_usage(db_path, usage_first)
    write_codex_usage(db_path, usage_second)
    result = read_codex_usage(db_path)

    assert result is not None
    assert result["primary_used_pct"] == 55.0
    assert result["secondary_used_pct"] == 77.0
    assert result["plan_type"] == "team"

    # Verify only one row exists
    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM codex_usage").fetchone()[0]
    conn.close()
    assert count == 1


# ---------------------------------------------------------------------------
# 7. test_read_empty_table
# ---------------------------------------------------------------------------


def test_read_empty_table(tmp_path):
    """Returns None when codex_usage table exists but has no records."""
    from pacemaker.codex_usage import read_codex_usage
    from pacemaker.database import initialize_database

    db_path = str(tmp_path / "test.db")
    initialize_database(db_path)

    result = read_codex_usage(db_path)

    assert result is None


# ---------------------------------------------------------------------------
# 8. test_no_sessions_dir
# ---------------------------------------------------------------------------


def test_no_sessions_dir(tmp_path):
    """Returns None when sessions directory doesn't exist."""
    from pacemaker.codex_usage import get_latest_codex_usage

    nonexistent_dir = str(tmp_path / "does_not_exist")

    result = get_latest_codex_usage(sessions_dir=nonexistent_dir)

    assert result is None


# ---------------------------------------------------------------------------
# 9. test_latest_file_selection
# ---------------------------------------------------------------------------


def test_latest_file_selection(tmp_path):
    """When multiple JSONL files exist today, picks most recently modified."""
    from pacemaker.codex_usage import get_latest_codex_usage

    today = time.strftime("%Y/%m/%d")
    session_dir = tmp_path / today
    session_dir.mkdir(parents=True)

    older_file = session_dir / "older.jsonl"
    newer_file = session_dir / "newer.jsonl"

    _write_jsonl(older_file, [_token_count_event(primary_pct=10.0, secondary_pct=20.0)])
    time.sleep(0.05)
    _write_jsonl(newer_file, [_token_count_event(primary_pct=88.0, secondary_pct=99.0)])

    # Touch newer_file to ensure mtime is later
    newer_file.touch()

    result = get_latest_codex_usage(sessions_dir=str(tmp_path))

    assert result is not None
    assert result["primary_used_pct"] == 88.0
    assert result["secondary_used_pct"] == 99.0


# ---------------------------------------------------------------------------
# 10. test_default_sessions_dir (covers line 50 — None sessions_dir)
# ---------------------------------------------------------------------------


def test_default_sessions_dir_nonexistent(tmp_path, monkeypatch):
    """When sessions_dir is None and ~/.codex/sessions doesn't exist, returns None."""
    from pacemaker.codex_usage import get_latest_codex_usage

    # Point HOME to a temp dir where ~/.codex/sessions does not exist
    monkeypatch.setenv("HOME", str(tmp_path))

    result = get_latest_codex_usage(sessions_dir=None)

    assert result is None


# ---------------------------------------------------------------------------
# 11. test_empty_sessions_root — no date dirs at all (covers line 58)
# ---------------------------------------------------------------------------


def test_empty_sessions_root(tmp_path):
    """Sessions root exists but has no YYYY/MM/DD subdirectories → None."""
    from pacemaker.codex_usage import get_latest_codex_usage

    # Root exists but is completely empty
    result = get_latest_codex_usage(sessions_dir=str(tmp_path))

    assert result is None


# ---------------------------------------------------------------------------
# 12. test_date_dir_exists_but_no_jsonl — covers line 62
# ---------------------------------------------------------------------------


def test_date_dir_exists_but_no_jsonl(tmp_path):
    """Today's date dir exists but contains no JSONL files → None."""
    from pacemaker.codex_usage import get_latest_codex_usage

    today = time.strftime("%Y/%m/%d")
    session_dir = tmp_path / today
    session_dir.mkdir(parents=True)
    # No JSONL files placed here

    result = get_latest_codex_usage(sessions_dir=str(tmp_path))

    assert result is None


# ---------------------------------------------------------------------------
# 13. test_historical_fallback — uses past date dir when today has no files
# ---------------------------------------------------------------------------


def test_historical_fallback(tmp_path):
    """When today's dir has no JSONL, falls back to most recent historical dir."""
    from pacemaker.codex_usage import get_latest_codex_usage

    # Create a historical date dir (yesterday or any past date)
    past_dir = tmp_path / "2026" / "01" / "15"
    past_dir.mkdir(parents=True)
    _write_jsonl(past_dir / "old.jsonl", [_token_count_event(primary_pct=33.0)])

    result = get_latest_codex_usage(sessions_dir=str(tmp_path))

    assert result is not None
    assert result["primary_used_pct"] == 33.0


# ---------------------------------------------------------------------------
# 14. test_blank_lines_in_jsonl — covers blank line skip (line 128)
# ---------------------------------------------------------------------------


def test_blank_lines_in_jsonl(tmp_path):
    """JSONL with blank lines between events processes correctly."""
    from pacemaker.codex_usage import get_latest_codex_usage

    today = time.strftime("%Y/%m/%d")
    session_dir = tmp_path / today
    session_dir.mkdir(parents=True)
    session_file = session_dir / "session.jsonl"

    with open(session_file, "w") as f:
        f.write("\n")  # blank line
        f.write(json.dumps(_token_count_event(primary_pct=44.0)) + "\n")
        f.write("\n")  # trailing blank line

    result = get_latest_codex_usage(sessions_dir=str(tmp_path))

    assert result is not None
    assert result["primary_used_pct"] == 44.0


# ---------------------------------------------------------------------------
# 15. test_oserror_reading_file — covers OSError path (lines 143-144)
# ---------------------------------------------------------------------------


def test_oserror_reading_file(tmp_path):
    """OSError when reading JSONL file returns None gracefully."""
    from pacemaker.codex_usage import _parse_last_token_count

    # Pass a path to a file that doesn't exist → triggers OSError
    nonexistent = tmp_path / "ghost.jsonl"

    result = _parse_last_token_count(nonexistent)

    assert result is None


# ---------------------------------------------------------------------------
# 16. test_write_codex_usage_missing_required_key — covers ValueError (line 179)
# ---------------------------------------------------------------------------


def test_write_codex_usage_missing_required_key(tmp_path):
    """write_codex_usage raises ValueError when required key is missing."""
    from pacemaker.codex_usage import write_codex_usage
    from pacemaker.database import initialize_database

    db_path = str(tmp_path / "test.db")
    initialize_database(db_path)

    incomplete_usage = {
        "primary_used_pct": 10.0,
        # missing secondary_used_pct and timestamp
    }

    with pytest.raises(ValueError, match="missing required keys"):
        write_codex_usage(db_path, incomplete_usage)


# ---------------------------------------------------------------------------
# 17. test_read_codex_usage_db_error — covers except path in read_codex_usage
# ---------------------------------------------------------------------------


def test_read_codex_usage_db_error(tmp_path):
    """read_codex_usage returns None when DB path is invalid."""
    from pacemaker.codex_usage import read_codex_usage

    # Pass a path to a non-SQLite file to trigger sqlite3.Error
    bad_db = tmp_path / "bad.db"
    bad_db.write_text("not a sqlite database")

    result = read_codex_usage(str(bad_db))

    assert result is None


# ---------------------------------------------------------------------------
# 18. test_write_codex_usage_db_error — covers except path (lines 208-209)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 19. test_migrate_codex_usage_schema_adds_limit_id_column
# ---------------------------------------------------------------------------


def test_migrate_codex_usage_schema_adds_limit_id_column(tmp_path):
    """migrate_codex_usage_schema adds limit_id column to old-schema DB."""
    from pacemaker.codex_usage import migrate_codex_usage_schema

    # Create DB with OLD schema (no limit_id column)
    db_path = str(tmp_path / "old_schema.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE codex_usage (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                primary_used_pct REAL NOT NULL,
                secondary_used_pct REAL NOT NULL,
                primary_resets_at INTEGER,
                secondary_resets_at INTEGER,
                plan_type TEXT,
                timestamp REAL NOT NULL
            )
            """
        )
        conn.commit()

    # Verify column does NOT exist before migration
    with sqlite3.connect(db_path) as conn:
        cols_before = [row[1] for row in conn.execute("PRAGMA table_info(codex_usage)")]
    assert "limit_id" not in cols_before

    # Run migration
    migrate_codex_usage_schema(db_path)

    # Verify column EXISTS after migration
    with sqlite3.connect(db_path) as conn:
        cols_after = [row[1] for row in conn.execute("PRAGMA table_info(codex_usage)")]
    assert "limit_id" in cols_after


# ---------------------------------------------------------------------------
# 20. test_migrate_codex_usage_schema_is_idempotent
# ---------------------------------------------------------------------------


def test_migrate_codex_usage_schema_is_idempotent(tmp_path):
    """Running migrate_codex_usage_schema twice does not raise."""
    from pacemaker.codex_usage import migrate_codex_usage_schema
    from pacemaker.database import initialize_database

    db_path = str(tmp_path / "test.db")
    initialize_database(db_path)

    # First call: column already exists in new schema
    migrate_codex_usage_schema(db_path)
    # Second call: must not raise
    migrate_codex_usage_schema(db_path)

    # Verify column still present
    with sqlite3.connect(db_path) as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(codex_usage)")]
    assert "limit_id" in cols


def test_write_codex_usage_db_error(tmp_path, monkeypatch):
    """write_codex_usage calls log_warning and does not raise when DB is corrupt."""
    import pacemaker.codex_usage as codex_module
    from pacemaker.codex_usage import write_codex_usage

    warning_calls = []
    monkeypatch.setattr(
        codex_module, "log_warning", lambda *args, **kwargs: warning_calls.append(args)
    )

    # A non-SQLite file triggers sqlite3.DatabaseError
    bad_db = tmp_path / "bad.db"
    bad_db.write_text("not a sqlite database")

    usage = {
        "primary_used_pct": 10.0,
        "secondary_used_pct": 20.0,
        "primary_resets_at": 1000000,
        "secondary_resets_at": 2000000,
        "plan_type": "team",
        "timestamp": time.time(),
    }

    # Should not raise — exception is caught and log_warning called
    write_codex_usage(str(bad_db), usage)

    assert len(warning_calls) == 1
    assert "Failed to write codex usage" in warning_calls[0][1]


# ---------------------------------------------------------------------------
# 21. test_hook_runs_migration_before_write_on_old_schema
# ---------------------------------------------------------------------------


def test_hook_runs_migration_before_write_on_old_schema(tmp_path, monkeypatch):
    """SubagentStop hook migrates old-schema DB before writing codex usage.

    Creates an old-schema DB without limit_id, sets hook_model to a codex
    model, mocks get_latest_codex_usage to return a usage dict, then runs
    run_subagent_stop_hook. Verifies limit_id column exists in DB afterward,
    proving migrate_codex_usage_schema is wired before write_codex_usage.
    """
    import json
    import pacemaker.hook as hook_module
    from pacemaker.hook import run_subagent_stop_hook

    # Create old-schema DB (no limit_id column)
    db_path = str(tmp_path / "old_schema.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE codex_usage (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                primary_used_pct REAL NOT NULL,
                secondary_used_pct REAL NOT NULL,
                primary_resets_at INTEGER,
                secondary_resets_at INTEGER,
                plan_type TEXT,
                timestamp REAL NOT NULL
            )
            """
        )
        conn.commit()

    # Patch DEFAULT_DB_PATH to point at our old-schema DB
    monkeypatch.setattr(hook_module, "DEFAULT_DB_PATH", db_path)

    # Patch load_config to return a config with a codex model
    monkeypatch.setattr(
        hook_module,
        "load_config",
        lambda path: {"enabled": True, "hook_model": "codex-model"},
    )

    # Patch get_latest_codex_usage to return a valid usage dict
    fake_usage = {
        "primary_used_pct": 10.0,
        "secondary_used_pct": 20.0,
        "primary_resets_at": 1000000,
        "secondary_resets_at": 2000000,
        "plan_type": "team",
        "limit_id": "codex",
        "timestamp": time.time(),
    }
    import pacemaker.codex_usage as codex_module

    monkeypatch.setattr(codex_module, "get_latest_codex_usage", lambda: fake_usage)

    # Reset the module-level migration flag so migration runs fresh
    monkeypatch.setattr(hook_module, "_codex_migration_done", False)

    # Minimal hook_data for SubagentStop
    hook_data = json.dumps(
        {
            "hook_event_name": "SubagentStop",
            "session_id": "test-session",
            "agent_id": "test-agent",
        }
    )

    # Patch load_state and save_state to avoid filesystem side effects
    monkeypatch.setattr(hook_module, "load_state", lambda path: {})
    monkeypatch.setattr(hook_module, "save_state", lambda state, path: None)

    import io

    with patch("sys.stdin", io.StringIO(hook_data)):
        run_subagent_stop_hook()

    # Verify limit_id column now exists — migration was called
    with sqlite3.connect(db_path) as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(codex_usage)")]
    assert "limit_id" in cols
