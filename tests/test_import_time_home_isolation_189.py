"""Test isolation: ``constants.DEFAULT_DB_PATH`` (and the other import-time
paths) are frozen when pacemaker is first imported. With story #187 that value
depends on the user's per-user config.json (``shared_data_dir``), so the suite
must never read the REAL home at import time: a developer who enabled shared
mode on the machine running the tests would otherwise silently redirect every
unit test that falls back to ``DEFAULT_DB_PATH`` to the real shared database.
"""

import os

import pytest

import real_home_guard_support as guard
from pacemaker import constants


def test_import_time_paths_do_not_point_into_the_real_home():
    real_dir = guard.real_pace_maker_dir()
    if real_dir is None:
        pytest.skip("current uid has no passwd entry: there is no real home")
    real_home = os.path.dirname(real_dir)
    assert not constants.DEFAULT_DB_PATH.startswith(real_home + os.sep)
    assert not constants.DEFAULT_CONFIG_PATH.startswith(real_home + os.sep)
    assert not constants.DEFAULT_STATE_PATH.startswith(real_home + os.sep)
