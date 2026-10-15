"""Regression tests for issue #179.

Databases created by older pace-maker versions have a ``usage_snapshots``
table with no ``id`` column. The batched cleanup used to delete via
``WHERE id IN (SELECT id ...)`` and raised ``no such column: id`` on every
hook, so old snapshots were never deleted. Cleanup now batches by SQLite's
implicit ``rowid``. Real SQLite, no mocks.
"""

import sqlite3
import time
from contextlib import closing

import pytest

import pacemaker.database as database
from pacemaker.database import cleanup_old_snapshots

_SECONDS_PER_DAY = 86400
_RETENTION_DAYS = 60
_OLD_AGE_DAYS = _RETENTION_DAYS + 30
_RECENT_AGE_DAYS = 1
_SMALL_BATCH_SIZE = 4
_OLD_ROWS_SPANNING_BATCHES = 10  # > 2 * _SMALL_BATCH_SIZE
_RECENT_ROWS = 3

# Live legacy shape (PRAGMA table_info on an affected install): no id, no
# created_at.
_LEGACY_USAGE_SNAPSHOTS_DDL = """
CREATE TABLE usage_snapshots (
    timestamp INTEGER NOT NULL,
    five_hour_util REAL,
    five_hour_resets_at INTEGER,
    seven_day_util REAL,
    seven_day_resets_at INTEGER,
    session_id TEXT
)
"""


def _seed(db_path: str, n_old: int, n_recent: int) -> None:
    now = int(time.time())
    old_ts = now - _OLD_AGE_DAYS * _SECONDS_PER_DAY
    recent_ts = now - _RECENT_AGE_DAYS * _SECONDS_PER_DAY
    rows = [(old_ts - i,) for i in range(n_old)]
    rows += [(recent_ts - i,) for i in range(n_recent)]
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(_LEGACY_USAGE_SNAPSHOTS_DDL)
        conn.executemany(
            "INSERT INTO usage_snapshots "
            "(timestamp, five_hour_util, seven_day_util, session_id) "
            "VALUES (?, 1.0, 1.0, 'legacy')",
            rows,
        )
        conn.commit()


def _count(db_path: str, where: str = "1=1") -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(
            f"SELECT COUNT(*) FROM usage_snapshots WHERE {where}"
        ).fetchone()[0]


@pytest.fixture
def legacy_db(tmp_path):
    return str(tmp_path / "legacy_usage.db")


class TestLegacySnapshotCleanup:
    def test_legacy_table_really_has_no_id_column(self, legacy_db):
        _seed(legacy_db, 1, 1)
        with closing(sqlite3.connect(legacy_db)) as conn:
            columns = [r[1] for r in conn.execute("PRAGMA table_info(usage_snapshots)")]
        assert "id" not in columns

    def test_deletes_old_rows_keeps_recent_and_returns_count(self, legacy_db):
        _seed(legacy_db, 5, _RECENT_ROWS)

        deleted = cleanup_old_snapshots(legacy_db, retention_days=_RETENTION_DAYS)

        assert deleted == 5
        assert _count(legacy_db) == _RECENT_ROWS
        cutoff = int(time.time()) - _RETENTION_DAYS * _SECONDS_PER_DAY
        assert _count(legacy_db, f"timestamp < {cutoff}") == 0

    def test_batches_across_multiple_deletes_on_legacy_shape(
        self, legacy_db, monkeypatch
    ):
        monkeypatch.setattr(database, "CLEANUP_BATCH_SIZE", _SMALL_BATCH_SIZE)
        _seed(legacy_db, _OLD_ROWS_SPANNING_BATCHES, _RECENT_ROWS)

        deleted = cleanup_old_snapshots(legacy_db, retention_days=_RETENTION_DAYS)

        assert deleted == _OLD_ROWS_SPANNING_BATCHES
        assert _count(legacy_db) == _RECENT_ROWS

    def test_returns_zero_when_nothing_old(self, legacy_db):
        _seed(legacy_db, 0, _RECENT_ROWS)

        assert cleanup_old_snapshots(legacy_db, retention_days=_RETENTION_DAYS) == 0
        assert _count(legacy_db) == _RECENT_ROWS
