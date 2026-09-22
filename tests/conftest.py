"""
Global test fixtures — safety guards against production DB pollution.

This conftest.py ensures that NO test can accidentally write to the real
production database at ~/.claude-pace-maker/usage.db. All tests that
instantiate UsageModel without an explicit db_path will get a temp DB.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

# Ensure THIS checkout's own src/ resolves FIRST, ahead of any stale
# editable-install (`pip install -e`) .pth entry pointing at a different
# checkout. A machine-wide editable install's .pth file always points at
# whichever checkout it was `pip install -e`'d from -- on a machine with
# multiple worktrees of this repo, that silently shadows every OTHER
# worktree's own src/pacemaker for any test that imports pacemaker
# in-process (subprocess-based tests that build their own PYTHONPATH from
# this test file's own location are unaffected). Must run before any
# `import pacemaker` anywhere in the suite, including below.
_REPO_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _REPO_SRC in sys.path:
    sys.path.remove(_REPO_SRC)
sys.path.insert(0, _REPO_SRC)

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

    # Guard the session registry DB path — prevents tests from writing to
    # ~/.claude-pace-maker/session_registry.db (the production registry).
    # db.py raises RuntimeError when PACEMAKER_TEST_MODE=1 and this is unset,
    # so setting it here satisfies that safety check for all tests.
    monkeypatch.setenv(
        "PACEMAKER_SESSION_REGISTRY_PATH",
        str(fake_pace_maker_dir / "session_registry.db"),
    )

    # Guard the version status DB path (Story #66) — prevents tests from writing to
    # ~/.claude-pace-maker/version_status.db (the production version status DB).
    # version_status_db.py raises RuntimeError when PACEMAKER_TEST_MODE=1 and this
    # is unset, so setting it here satisfies that safety check for all tests.
    monkeypatch.setenv(
        "PACEMAKER_VERSION_STATUS_PATH",
        str(fake_pace_maker_dir / "version_status.db"),
    )

    # Guard PACEMAKER_CENTRAL_BASE — prevents memory_localization tests from
    # touching the real ~/.claude/projects/ directory.
    # core.py raises RuntimeError when PACEMAKER_TEST_MODE=1 and this is unset.
    fake_central = tmp_path / "fake-central"
    fake_central.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PACEMAKER_CENTRAL_BASE", str(fake_central))

    # Guard core_paths.yaml / excluded_paths.yaml / source_code_extensions.json
    # (issue #92) — prevents the new core_paths migration (which WRITES to
    # disk) and any excluded-paths/extension-registry read from touching the
    # real ~/.claude-pace-maker/ config files when a test calls
    # validate_intent_and_code() end-to-end. These constants are read via
    # local (call-time) imports in intent_validator.py, so patching the
    # constants module attribute here (rather than the DEFAULT_* names
    # already bound in modules that imported them at load time) is
    # sufficient and effective. fake_pace_maker_dir is defined above.
    try:
        import pacemaker.constants as constants_module

        monkeypatch.setattr(
            constants_module,
            "DEFAULT_CORE_PATHS_PATH",
            str(fake_pace_maker_dir / "core_paths.yaml"),
        )
        monkeypatch.setattr(
            constants_module,
            "DEFAULT_EXCLUDED_PATHS_PATH",
            str(fake_pace_maker_dir / "excluded_paths.yaml"),
        )
        monkeypatch.setattr(
            constants_module,
            "DEFAULT_EXTENSION_REGISTRY_PATH",
            str(fake_pace_maker_dir / "source_code_extensions.json"),
        )
    except ImportError:
        pass


_BLOCKED_CLI_NAMES = {"codex", "gemini", "claude"}
_REAL_SUBPROCESS_RUN = subprocess.run


class _ExternalCallGuard:
    """Records every leaked real-external-call attempt, then raises loudly.

    Extracted out of `_block_real_external_cli_calls` (issue #144
    code-review follow-up #4) so the detection logic is directly
    unit-testable (see tests/test_external_cli_guard.py) without needing a
    nested pytest process, and so a leak can be caught at fixture
    TEARDOWN via `check()` even when the immediate RuntimeError raised by
    `guarded_run`/`guarded_sdk_query` gets silently swallowed by the
    caller's own exception handling -- which is exactly what
    `AnthropicProvider._query_async`'s broad `except Exception` does
    (converts it into an empty response, then a ProviderError). Relying
    on the immediate raise alone let a leaked, unmocked SDK call pass
    undetected; `check()` is the reliable signal.
    """

    def __init__(self):
        self.leaked_calls = []

    def guarded_run(self, cmd, *args, **kwargs):
        argv0 = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else cmd
        name = os.path.basename(str(argv0)) if argv0 else ""
        if name in _BLOCKED_CLI_NAMES:
            self.leaked_calls.append(f"subprocess.run(cmd={cmd!r})")
            raise RuntimeError(
                f"Real external CLI call in a non-e2e test (cmd={cmd!r}). "
                f"Mock the provider/inference call instead of hitting {name}."
            )
        return _REAL_SUBPROCESS_RUN(cmd, *args, **kwargs)

    async def guarded_sdk_query(self, *args, **kwargs):
        self.leaked_calls.append("claude_agent_sdk.query(...)")
        raise RuntimeError(
            "Real external claude_agent_sdk.query() call in a non-e2e "
            "test. Mock AnthropicProvider.query (or "
            "pacemaker.inference.resolve_and_call_with_reviewer) instead "
            "of letting execution reach the real Claude Agent SDK."
        )
        yield  # pragma: no cover - unreachable; makes this an async generator

    def check(self):
        """Fail loudly if any real external call was attempted during
        this guard's lifetime, regardless of whether the caller's own
        exception handling swallowed the immediate raise above."""
        if self.leaked_calls:
            pytest.fail(
                "Real external CLI/SDK call(s) leaked past mocking in a "
                f"non-e2e test: {self.leaked_calls}. A provider's own "
                "exception handling may have swallowed the guard's "
                "immediate RuntimeError (e.g. AnthropicProvider._query_async's "
                "broad `except Exception`), so this teardown check is the "
                "reliable signal -- do not remove it.",
                pytrace=False,
            )


@pytest.fixture(autouse=True)
def _block_real_external_cli_calls(request, monkeypatch):
    """Fail fast if a test makes a real external CLI call (codex/gemini/claude).

    These must be mocked — a real call (often in a background reviewer thread)
    blocks on a network timeout and Python waits for the lingering thread at exit,
    making the suite slow. e2e tests are exempt (they may use real systems).

    Two separate spawn paths are guarded:
      - `subprocess.run` — used by CodexProvider/GeminiProvider/AgyProvider.
      - `claude_agent_sdk.query` — used by AnthropicProvider, which spawns the
        real `claude` CLI via `anyio.open_process` deep inside the SDK's own
        transport module, entirely bypassing `subprocess.run`. A test whose
        code path reaches `AnthropicProvider.query()` unmocked previously made
        a real, unauthenticated CLI call instead of being caught here.

    See `_ExternalCallGuard.check()` for why this is a generator fixture
    with a teardown check, not just the immediate raise inside the guarded
    functions.
    """
    # e2e tests may legitimately hit real systems — don't guard those.
    if os.sep + "e2e" + os.sep in str(request.node.fspath):
        yield
        return

    guard = _ExternalCallGuard()
    monkeypatch.setattr(subprocess, "run", guard.guarded_run)

    try:
        import claude_agent_sdk

        monkeypatch.setattr(claude_agent_sdk, "query", guard.guarded_sdk_query)
    except ImportError:
        pass

    yield

    guard.check()


# Files that legitimately test perform_session_start_version_check()'s OWN
# logic -- they mock subprocess.run themselves, at the correct lower level,
# so the blanket stub below (which no-ops the whole function) must not
# apply to them or it would silently defeat what they're testing.
#
# CAVEAT (issue #144 code-review follow-up #5): this match is by BASENAME
# only, not by any marker or introspection of what the file actually
# tests. If a future test file is added that also legitimately exercises
# perform_session_start_version_check() (e.g. a differently-named split
# of test_version_check_integration.py, or a new regression file for a
# future version-check bug), it will be silently stubbed by the autouse
# fixture below unless someone remembers to add its basename here too --
# there is no automated signal that would catch the omission. If this
# set grows past two entries, or a rename/split becomes likely, consider
# switching to a pytest marker (e.g. `@pytest.mark.exercises_version_check`
# read via `request.node.get_closest_marker(...)`) instead of a basename
# set, so the exemption travels with the test itself rather than living
# in a second file that must be kept in sync by hand.
_VERSION_CHECK_EXEMPT_FILES = {
    "test_claude_code_version.py",
    "test_version_check_integration.py",
}


@pytest.fixture(autouse=True)
def _stub_session_start_version_probe(request, monkeypatch):
    """No-op perform_session_start_version_check() for every test except
    the two that specifically test it (see _VERSION_CHECK_EXEMPT_FILES)
    and e2e tests (which may legitimately hit real systems).

    run_session_start_hook() unconditionally calls
    perform_session_start_version_check(), which does a real
    `subprocess.run(["claude", "--version"], timeout=5)`. Every test file
    that calls run_session_start_hook() directly for unrelated reasons
    (CSA schema gating, provenance tagging, stdin handling, intel
    injection, subagent reminders, ...) was making this real external
    call on every run -- invisible because
    perform_session_start_version_check()'s own fail-open exception
    handling silently swallowed any error, including the
    _block_real_external_cli_calls guard's immediate RuntimeError, until
    that guard's teardown check (`_ExternalCallGuard.check()`, issue #144
    code-review follow-up #4) started catching it for real.
    """
    fspath = str(request.node.fspath)
    if os.path.basename(fspath) in _VERSION_CHECK_EXEMPT_FILES:
        yield
        return
    if os.sep + "e2e" + os.sep in fspath:
        yield
        return

    # pacemaker.version_check is this project's OWN internal module
    # (unlike the optional claude_agent_sdk dependency guarded elsewhere
    # in this file) -- it must always be importable, so a failure here is
    # a real setup problem, not something to silently swallow.
    import pacemaker.version_check as version_check

    monkeypatch.setattr(
        version_check,
        "perform_session_start_version_check",
        lambda *args, **kwargs: None,
    )

    yield


# ---------------------------------------------------------------------------
# Memory-localization shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ml_central_base(tmp_path, monkeypatch):
    """Isolated central-base directory with PACEMAKER_CENTRAL_BASE set."""
    base = tmp_path / "ml-central"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PACEMAKER_CENTRAL_BASE", str(base))
    return base


@pytest.fixture
def ml_repo(tmp_path):
    """A real git repository at tmp_path/ml-repo."""
    path = tmp_path / "ml-repo"
    path.mkdir(parents=True)
    subprocess.run(["git", "init", str(path)], capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(path),
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(path),
        capture_output=True,
        check=True,
    )
    return path


@pytest.fixture
def ml_enc_dir(ml_central_base):
    """Encoded project directory inside ml_central_base."""
    enc = ml_central_base / "enc123"
    enc.mkdir()
    return enc


@pytest.fixture
def ml_transcript_path(ml_enc_dir):
    """Fake transcript .jsonl inside ml_enc_dir."""
    transcript = ml_enc_dir / "session.jsonl"
    transcript.write_text("{}")
    return transcript


@pytest.fixture
def ml_local_memory(ml_repo):
    """.claude-memory directory inside ml_repo."""
    local = ml_repo / ".claude-memory"
    local.mkdir()
    return local
