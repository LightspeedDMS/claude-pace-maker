# Testing

Read this before running or writing tests in this repo.

## Running the suite

- **Never run the suite as one pytest process** (`python -m pytest tests/`): SQLite WAL contention makes it hang. Use `./scripts/run_tests.sh`, which runs each file in its own process (120 s per file, 15 s per test; `PACEMAKER_TEST_TIMEOUT`). `--tb` shows tracebacks.
- `--quick` skips only the files physically under `tests/e2e/`, because it matches a directory glob, not an `_e2e` suffix. `tests/test_*_e2e.py` files still run.
- Setup: `pip install -r requirements.txt -r requirements-dev.txt`. The dev file adds pytest, pytest-timeout and `responses`. Without `responses`, the Langfuse provisioner tests fail at collection.
- Interpreter: `run_tests.sh` (`resolve_test_python()`) picks one that has both `claude_agent_sdk` and pytest. Override with `PACEMAKER_TEST_PYTHON`. On the dev machine that is python3.11. `python3` is 3.9 and lacks the SDK, so Stage-2 tests fail closed under it ("SDK not available"), which is not a regression.
- `PACEMAKER_TEST_MODE=1` is set by `tests/conftest.py` and turns on `PRAGMA synchronous=OFF`.
- `tests/e2e/` has its own, larger budgets (900 s per file, 90 s per test; `PACEMAKER_E2E_TEST_TIMEOUT` / `PACEMAKER_E2E_PYTEST_TIMEOUT`). Do not add `@pytest.mark.timeout` there, because a marker always overrides `--timeout`. Slow real-`install.sh` suites belong in `tests/e2e/` (#144). `tests/e2e/conftest.py` sets `PACEMAKER_SKIP_MCP_REGISTRATION=1` for the `test_install*` suites, so they never call the real `claude mcp`.

## Isolation rules

- **No real external calls in unit tests.** An autouse guard in `tests/conftest.py` blocks real `codex`/`gemini`/`claude` calls. A call that leaked into a reviewer thread once caused ~30 s interpreter-exit hangs that pytest's own timer never saw. Mock at the namespace the code imports from (`pacemaker.inference.resolve_and_call_with_reviewer`, `pacemaker.inference.competitive.get_provider`), not at `...registry`.
- **Production-path isolation**: `tests/conftest.py::_guard_production_db` (autouse) sets `HOME` to a fake dir and points every DB/config path at tmp:
  - env vars: `PACEMAKER_SESSION_REGISTRY_PATH`, `PACEMAKER_VERSION_STATUS_PATH`, `PACEMAKER_INTENT_DECLARATIONS_PATH`;
  - module attributes: `hook.DEFAULT_DB_PATH`, `constants.DEFAULT_CORE_PATHS_PATH` / `DEFAULT_EXCLUDED_PATHS_PATH` / `DEFAULT_EXTENSION_REGISTRY_PATH`.

  Each DB module raises `RuntimeError` when `PACEMAKER_TEST_MODE=1` and its env var is unset (memory localization: `PACEMAKER_CENTRAL_BASE`). Follow the same pattern for any new DB.
- Never let code under test import `constants.DEFAULT_DB_PATH` fresh: that value is frozen at import time and is not the attribute conftest patches. Thread `hook.DEFAULT_DB_PATH` through explicitly (see [stage2-review-prompts.md](stage2-review-prompts.md#secret-masking)).

## Same-commit contracts

- `tests/test_real_transcript_replay.py` (`_replay_stage1` plus `fixtures/real_transcript_replay/manifest.json`) mirrors Stage-1 logic. Update it in the same commit as any Stage-1 change.
- If you change `database.py`'s `SCHEMA`, bump `SCHEMA_VERSION` and update the pinned hash in `tests/test_database_lock_contention_145.py`.
- If you remove FORMAT A from the Stop-hook prompt, `tests/test_stop_hook_prompt_async_wait.py` asserts the literal `E2E TEST COMPLETION REPORT`.

## End-to-end philosophy

- **Do not add new scripted/automated E2E tests.** They cost too much time and the user doesn't want them.
- In this project, E2E means Claude runs the hooks/CLI, watches pace-maker's real behavior, and reports the output it observed. The Stop-hook prompt accepts this as **FORMAT C** (an ad-hoc `| # | Test | Command | Captured Output | Result |` table). What it rejects is a claim without output, or results from mocked systems. The prompt is therefore not in conflict with this philosophy; don't strip its FORMAT A/B on that basis (an earlier revision of these docs wrongly said to).
- Legacy scripted E2E files still exist and run (`tests/e2e/*`, `tests/test_clean_code_rules_e2e.py`, `tests/test_langfuse_provisioner_e2e.py`, `tests/test_subagent_output_correlation_e2e.py`). Leave them alone unless a task covers them.
- `tests/e2e/test_install_old_bloated.py::TestHookConflictDetection` is the only coverage of global-vs-local hook-conflict warnings, so do not delete it.
