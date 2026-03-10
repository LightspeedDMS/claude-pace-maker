"""
Global test fixtures — safety guards against production DB pollution.

This conftest.py ensures that NO test can accidentally write to the real
production database at ~/.claude-pace-maker/usage.db. All tests that
instantiate UsageModel without an explicit db_path will get a temp DB.
"""

import os

import pytest

# Enable test mode globally — skips fsync in SQLite for 20x faster DB operations.
# Must be set before any pacemaker imports to ensure all connections see it.
os.environ["PACEMAKER_TEST_MODE"] = "1"


@pytest.fixture(autouse=True)
def _guard_production_db(tmp_path, monkeypatch):
    """Prevent any test from touching the production database.

    Redirects the default UsageModel DB path to a temp directory so that
    even tests that forget to pass an explicit db_path won't pollute
    ~/.claude-pace-maker/usage.db.

    Also sets HOME to a temp dir to prevent any Path.home() based lookups
    from hitting real config/state files.
    """
    # Clear the DB initialization cache so each test gets a fresh state.
    # This prevents the _initialized_dbs set in database.py from carrying
    # over cached paths between tests (which would skip schema creation).
    try:
        from pacemaker.database import reset_initialized_dbs

        reset_initialized_dbs()
    except ImportError:
        pass

    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    fake_pace_maker_dir = fake_home / ".claude-pace-maker"
    fake_pace_maker_dir.mkdir()

    # Redirect HOME so Path.home() returns the fake home
    monkeypatch.setenv("HOME", str(fake_home))

    # Also patch the default DB path in usage_model module if it's imported
    try:
        import pacemaker.usage_model as um

        original_init = um.UsageModel.__init__

        def patched_init(self, db_path=None):
            if db_path is None:
                db_path = str(fake_pace_maker_dir / "usage.db")
            original_init(self, db_path=db_path)

        monkeypatch.setattr(um.UsageModel, "__init__", patched_init)
    except ImportError:
        pass

    # Guard the hook's DEFAULT_DB_PATH too
    try:
        import pacemaker.hook as hook
        from pacemaker.database import initialize_database

        if hasattr(hook, "DEFAULT_DB_PATH"):
            fake_db_path = str(fake_pace_maker_dir / "usage.db")
            monkeypatch.setattr(hook, "DEFAULT_DB_PATH", fake_db_path)
            initialize_database(fake_db_path)
    except ImportError:
        pass
