"""
Unit tests verifying hook.run_session_start_hook()'s Cross-Session
Awareness block gates its session_registry.db schema creation behind
`cross_session_awareness_enabled`, not just the master `enabled` switch
(issue #105).

The system under test is hook.py's "Cross-Session Awareness" block inside
run_session_start_hook() (immediately before the memory-localization block).
As of this test file's creation, that block calls
pacemaker.session_registry.db.init_schema(csa_db_path) UNCONDITIONALLY once
the master `enabled` gate (checked much earlier in the function) has
passed — it never checks `cross_session_awareness_enabled` at all. That is
the issue #105 bug (same "gate incompleteness" class as #94/#97/#99, but
confined to file/schema creation rather than a data write): with
`enabled: true, cross_session_awareness_enabled: false`, an empty
session_registry.db file (with schema, zero rows) still gets created on
disk.

Every session_registry.db read this file performs (`Path.exists()`) is
against a real filesystem path — nothing about the DB is mocked. Only
run_session_start_hook()'s own config/state file paths are pointed at
tmp_path, matching the existing precedent in
tests/test_provenance_wiring.py::TestSessionStartGuidanceAndManifestTagged.

The registry DB path used here is the SAME one tests/conftest.py's autouse
`_guard_production_db` fixture already redirects
PACEMAKER_SESSION_REGISTRY_PATH to (`<tmp_path>/fake_home/.claude-pace-maker/
session_registry.db`) — computed independently here (not re-read from the
env var) so a future conftest refactor that changes the guard's redirect
mechanism without updating the literal path would surface as a spurious
failure here rather than silently validating nothing.

Tests:
- test_csa_disabled_does_not_create_registry_db: the bug — enabled=True,
  cross_session_awareness_enabled=False must NOT create the registry DB
  file at all.
- test_csa_enabled_creates_registry_db: enabled=True,
  cross_session_awareness_enabled=True must still create the file
  (regression guard — the working case must not break).
- test_csa_key_absent_defaults_creates_db: cross_session_awareness_enabled
  omitted entirely (defaults True per _csa._is_enabled) must still create
  the file (regression guard for the default-on behavior).
"""

import json
from unittest.mock import patch


def _registry_db_path(tmp_path):
    """Compute the same path tests/conftest.py's autouse guard redirects
    PACEMAKER_SESSION_REGISTRY_PATH to."""
    return tmp_path / "fake_home" / ".claude-pace-maker" / "session_registry.db"


def _run_session_start(tmp_path, config: dict):
    """Invoke the real hook.run_session_start_hook() with the given config,
    matching the pattern used by
    tests/test_provenance_wiring.py::TestSessionStartGuidanceAndManifestTagged.
    """
    from pacemaker import hook

    config_path = tmp_path / "config.json"
    state_path = tmp_path / "state.json"
    config_path.write_text(json.dumps(config))
    state_path.write_text(
        json.dumps(
            {"session_id": "test-105", "subagent_counter": 0, "in_subagent": False}
        )
    )
    with (
        patch("pacemaker.hook.DEFAULT_CONFIG_PATH", str(config_path)),
        patch("pacemaker.hook.DEFAULT_STATE_PATH", str(state_path)),
        patch("sys.stdin.read", return_value=""),
        # run_session_start_hook() does `from .version_check import
        # perform_session_start_version_check` as a LOCAL import inside
        # its own try/except, so the patch target is version_check's own
        # module attribute (where the local import resolves it at call
        # time), not a nonexistent pacemaker.hook.perform_session_start_
        # version_check. Unmocked, this real function does
        # `subprocess.run(["claude", "--version"], timeout=5)` -- a real
        # external CLI call this test has nothing to do with (it's
        # testing CSA schema gating), caught by
        # tests/conftest.py::_ExternalCallGuard's teardown check.
        #
        # NOTE (issue #144 code-review follow-up #5): this local patch is
        # now REDUNDANT with tests/conftest.py's global autouse
        # `_stub_session_start_version_probe` fixture, added after this
        # one -- this file's basename is not in that fixture's
        # `_VERSION_CHECK_EXEMPT_FILES`, so the global fixture already
        # stubs the same function for every test in this file. Left in
        # place deliberately (belt-and-suspenders / local
        # self-documentation of exactly why this mock exists here), not
        # an oversight -- do not "clean it up" without checking the
        # global fixture is still in effect.
        patch("pacemaker.version_check.perform_session_start_version_check"),
    ):
        hook.run_session_start_hook()


def test_csa_disabled_does_not_create_registry_db(tmp_path):
    """enabled=True, cross_session_awareness_enabled=False must NOT create
    the session_registry.db file at all (issue #105 regression)."""
    _run_session_start(
        tmp_path,
        {"enabled": True, "cross_session_awareness_enabled": False},
    )

    db_path = _registry_db_path(tmp_path)
    assert not db_path.exists(), (
        "run_session_start_hook() must NOT create session_registry.db when "
        "cross_session_awareness_enabled is False (issue #105)"
    )


def test_csa_enabled_creates_registry_db(tmp_path):
    """enabled=True AND cross_session_awareness_enabled=True must still
    create the registry DB file (regression guard)."""
    _run_session_start(
        tmp_path,
        {"enabled": True, "cross_session_awareness_enabled": True},
    )

    db_path = _registry_db_path(tmp_path)
    assert db_path.exists(), (
        "run_session_start_hook() must create session_registry.db when both "
        "gates are enabled"
    )


def test_csa_key_absent_defaults_creates_db(tmp_path):
    """cross_session_awareness_enabled omitted entirely defaults to True
    (per _csa._is_enabled) and must still create the registry DB file."""
    _run_session_start(tmp_path, {"enabled": True})

    db_path = _registry_db_path(tmp_path)
    assert db_path.exists(), (
        "run_session_start_hook() must create session_registry.db when "
        "cross_session_awareness_enabled is omitted (defaults to True)"
    )
