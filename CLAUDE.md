# Claude Pace Maker - Development Knowledge

## ABSOLUTE PROHIBITION - Intent Validation

**I (Claude) am ABSOLUTELY FORBIDDEN from disabling intent validation. EVER.**

- I built this system - that gives me ZERO special privileges to bypass it
- `pace-maker intent-validation off` is OFF LIMITS to me
- Even when working on the intent validation code itself, I must find another way
- If I need to modify validation logic, I must ask the USER to disable it temporarily
- I must NEVER disable it myself under any circumstances
- This rule has NO exceptions, NO workarounds, NO "just this once"

**If I ever disable intent validation, I am violating a direct order.**

---

## End-to-End Testing Philosophy

**This project does not write NEW scripted/automated end-to-end tests** — they consume too much time and are explicitly unwanted here. (This was previously stated as an absolute "does NOT use" — that was false: five legacy scripted E2E files still exist and still run. See the bullets below.)

**What "end-to-end" means in this project**: Claude itself executes and inspects what pace-maker is doing when finishing agentic work — run the hooks/CLI, observe pace-maker's actual behavior, and report the real observed evidence. E2E verification here is **agentic/manual (performed by Claude)**, never a test script.

**Therefore:**
- Prefer agentic/manual E2E. Do NOT add NEW scripted/automated E2E test files for this project.
- Legacy scripted E2E files DO still exist and still run: `tests/e2e/test_secrets_e2e.py`, `tests/e2e/test_install_e2e.py`, `tests/e2e/test_install_old_bloated.py`, `tests/e2e/test_install.py`, `tests/e2e/test_install_local_mode.py`, `tests/test_clean_code_rules_e2e.py`, `tests/test_langfuse_provisioner_e2e.py`, `tests/test_subagent_output_correlation_e2e.py`. Leave them alone unless a task specifically covers them.
- **`test_install.py` and `test_install_local_mode.py` moved into `tests/e2e/` in issue #144.** The original #144 commit added a class-level `@pytest.mark.timeout(60)` to each (they did NOT pre-exist) to survive `run_tests.sh`'s default per-test `--timeout=15`, but their AGGREGATE per-file cost (each test does 1-2 genuinely distinct, non-reducible real `install.sh` invocations, ~20-45s each measured) still exceeded the per-file budget under load — `test_install.py` alone measured 147.84s. A code-review follow-up moved both out of `--quick` (issue #144's own explicitly sanctioned alternative remedy to a per-test timeout raise) into `tests/e2e/`, which `scripts/run_tests.sh` now gives its own, more generous per-test `--timeout` default (`PACEMAKER_E2E_PYTEST_TIMEOUT`) — and REMOVED the `@pytest.mark.timeout(60)` markers again, because a marker always wins over `--timeout` regardless of value, so leaving the 60s marker in place would have silently overridden the new, more generous e2e default.
- **`test_install_old_bloated.py` moved into `tests/e2e/` in issue #144 too.** Its `TestHookConflictDetection` class (global-vs-local install hook-conflict warnings) is NOT covered anywhere else in the suite — do not treat this file as pure legacy duplication of `test_install.py`/`test_install_local_mode.py` and delete it. `TestInstallScript`'s per-assertion tests (one real `install.sh` run per test) largely overlap `test_install.py`'s leaner consolidated coverage, but were left as-is rather than deduplicated (out of scope for #144's test-speed goal).
- **Correction (issue #144): `--quick` only skips files actually under `tests/e2e/`.** `scripts/run_tests.sh --quick`'s exclusion is a directory glob (`tests/e2e/test_*.py`), not a filename-suffix match — an earlier revision of this line claimed all 5 files above were skipped by `--quick`, which was only ever true for the ones physically living in `tests/e2e/`. `test_install_e2e.py` was moved from `tests/test_install_e2e.py` into `tests/e2e/` in #144 (its ~10 real `install.sh` invocations across 7 tests, ~20-40s each, are exactly the "genuinely slow e2e" case that belongs behind the `--quick` exclusion) and now matches the documented behavior. `test_clean_code_rules_e2e.py`, `test_langfuse_provisioner_e2e.py`, and `test_subagent_output_correlation_e2e.py` are still directly under `tests/` and are therefore NOT skipped by `--quick` despite their `_e2e.py` filenames — they run every time via the plain `tests/test_*.py` glob. That mismatch is left as-is (out of scope for #144); do not assume `--quick` excludes them.
- When end-to-end verification is needed, run pace-maker and inspect its behavior directly, then report the real observed output.

**How the stop-hook gate enforces this (it is NOT in conflict — issue #98).** `src/pacemaker/prompts/stop/stop_hook_validator_prompt.md` accepts **three** evidence shapes, not one:

- **FORMAT A** — `E2E TEST COMPLETION REPORT` (CHANGED CODE COVERAGE / REGRESSION COVERAGE / OVERALL VERDICT), the heavyweight standards format.
- **FORMAT B** — evidence table aligned to numbered acceptance criteria.
- **FORMAT C** — **ad-hoc evidence table**, explicitly "when there are no explicit acceptance criteria (bug fixes, refactors, exploratory tasks, infrastructure changes)": `| # | Test | Command | Captured Output | Result |`.

**FORMAT C is exactly the agentic evidence this project produces** — the prompt's own ✅ example is *"Ran: pace-maker status --verbose, observed terminal output"*. What the prompt rejects is not agentic verification but *claims without output*: "Tests are passing", "worked as expected", results from mocked/stubbed systems. That is the same standard this section argues for.

An earlier revision of this file claimed the prompt contradicted the philosophy and told readers to strip the scripted-E2E language. **That was wrong** — it read FORMAT A as the only accepted shape and missed FORMAT B/C. Do not "fix" the prompt on that basis.

Two real notes if you do edit that prompt:
- `tests/test_stop_hook_prompt_async_wait.py::test_e2e_evidence_requirement_preserved` asserts the literal string `E2E TEST COMPLETION REPORT` is present — removing FORMAT A requires updating that test in the same commit.
- FORMAT A is currently listed FIRST. The bug-#87 design note below says this prompt is engineered for the weakest verifier and that "weak models anchor on the first emphatic rule — it must be the permissive one". Listing the heaviest format first works against that principle; C → B → A would be more consistent. Untested hypothesis, not a known defect.

**Related — unit tests must never make real external calls**: all `codex`/`gemini`/`claude` CLI/SDK calls in tests MUST be mocked. An autouse guard in `tests/conftest.py` blocks real ones — a real call that leaked into a `ThreadPoolExecutor` reviewer thread caused ~30s interpreter-exit hangs (invisible to pytest's own timer, which made the suite appear fast while wall-clock was ~6x longer). Mock at the namespace the code imports from (e.g. `pacemaker.inference.resolve_and_call_with_reviewer`, `pacemaker.inference.competitive.get_provider`), NOT the `...registry` submodule.

---

## Related Codebase: Claude Usage Reporting

**IMPORTANT**: When the user says "claude usage" or "claude-usage", they mean the **claude-usage-reporting** codebase located at:
- `/home/jsbattig/Dev/claude-usage-reporting`

This is a separate tool that displays usage metrics in a monitor/dashboard format. It has a "Pacing Status" column where pace-maker integration features should be displayed.

- `pace-maker status` = CLI command from THIS repo (claude-pace-maker)
- `claude-usage` = Monitor tool from claude-usage-reporting repo

## Cross-Process Data Access Pattern (Pace-Maker → claude-usage Monitor)

The `claude-usage-reporting` monitor (a.k.a. "claude-console") reads pace-maker's SQLite DBs directly. This is the **canonical pattern** for any new cross-process reader — follow it exactly when adding new panels, columns, or data consumers on the monitor side.

### Architecture

- **Producer**: `claude-pace-maker` writes SQLite DBs under `~/.claude-pace-maker/` from hook processes, using `execute_with_retry()` (`MAX_RETRIES=3`). See `src/pacemaker/database.py:442-482`. **Correction — the retry budget is smaller than it looks**: with `MAX_RETRIES=3` the loop sleeps only at attempt 0 (100ms) and attempt 1 (200ms); attempt 2 re-raises without sleeping. **The 400ms step is unreachable** — total added latency is ~300ms, not ~700ms. The code's own comment at `database.py:474` repeats the "100/200/400" error; treat the code as authoritative over both comments, and do not size timeouts assuming a 400ms tier exists.
- **Consumer**: `claude-usage-reporting/claude_usage/code_mode/pacemaker_integration.py` opens **blocking read connections with a 5-second timeout** — NO retry loop on the reader side. The timeout IS the circuit breaker.
- **Two databases, identical access pattern**: `usage.db` (heavily read) and `session_registry.db` — **also actively read, NOT reserved**: `get_active_agent_tree()` / `get_active_agent_tree_cached()` at `pacemaker_integration.py:1184-1236` query the `agents` and `agent_actions` tables (own TTL: `AGENT_TREE_CACHE_TTL_SECONDS = 2`, staleness cutoff `AGENT_STALE_SECONDS = 1200`). This matters for the "When Adding New Tables / Columns" rules below — the consumer tolerates missing columns but not missing tables, so those two tables are a live cross-process contract.
- **Hardcoded base path**: monitor uses `Path.home() / ".claude-pace-maker"` (no env var override in consumer, unlike producer's `PACEMAKER_SESSION_REGISTRY_PATH`).

### Canonical Read Idioms

**Pattern A — single-row read** (use for per-agent / per-session lookups):
```python
if not self.db_path.exists():
    return None
try:
    with sqlite3.connect(str(self.db_path), timeout=5.0) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT col1, col2 FROM table WHERE id = ?", (ID,))
        row = cursor.fetchone()
    if row is None:
        return None
    return {
        "col1": row["col1"],
        "col2": row["col2"] if "col2" in row.keys() else None,  # optional col
    }
except (sqlite3.Error, OSError) as e:
    logging.debug("Failed: %s", e)
    return None
```

**Pattern B — aggregate with time window** (use for panel feeds):
```python
if not self.db_path.exists():
    return None
try:
    cutoff = time.time() - WINDOW_SECONDS
    conn = sqlite3.connect(str(self.db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT col1, SUM(col2) FROM t WHERE ts >= ? GROUP BY col1",
            (cutoff,),
        )
        return {r[0]: r[1] for r in cursor.fetchall()}
    finally:
        conn.close()
except (sqlite3.Error, OSError) as e:
    logging.debug("Failed: %s", e)
    return None
```

**Pattern B is prescriptive, not descriptive — copy the snippet, NOT the live function.** `get_blockage_stats()` (`pacemaker_integration.py:567-630`) deviates from it in three ways: it gates on `is_installed()` first, it hardcodes a literal `timeout=5.0` instead of using the `DB_TIMEOUT` constant, and it closes the connection **only on the success path** — there is no `try/finally`, so an exception raised mid-query leaks the connection. New readers must follow the snippet above.

### Mandatory Rules for All Cross-Process Readers

1. **`.exists()` check before `sqlite3.connect()`** — DB file may not exist yet on fresh install.
2. **`timeout=5.0`** — use the `DB_TIMEOUT` constant in `pacemaker_integration.py:28`.
3. **`PRAGMA journal_mode=WAL`** — matches producer, avoids lock contention.
4. **Optional columns via `"col" in row.keys()`** — producer adds columns via additive `ALTER TABLE` (see `codex_usage`'s `limit_id`); consumers must tolerate missing columns.
5. **Catch `(sqlite3.Error, OSError)` broadly** — includes lock timeout, missing table, permission errors.
6. **Return `None` on any failure** — never raise to caller. `logging.debug()` the reason.
7. **Check `row is None` after `fetchone()`** — empty result is legal.
8. **No retry logic in consumer** — the 5s timeout already covers writer contention; retries would compound.
9. **No schema version checks** — defensive reads (point 4) replace migrations on the consumer side.
10. **For caching, use manual TTL** — see `get_blockage_stats_cached()` at `pacemaker_integration.py:653-680` (5s TTL with `_*_cache_time` sentinel).

### Beyond SQLite

- **JSON reads**: `config.json` read via `_read_config()` at `pacemaker_integration.py:507-516` (same defensive pattern).
- **Dynamic imports**: Monitor adds pace-maker's `src/` to `sys.path` via `_get_pacemaker_src_path()` (reads `~/.claude-pace-maker/install_source`, lines 157-197) then calls `UsageModel.get_current_usage()` **in-process**. Import-based calls are NOT cross-process — the function runs in the monitor's Python interpreter against the shared SQLite file. **This is a DELIBERATE, UNCHANGED exception to the issue #146 hook-process fix below** — `_get_pacemaker_src_path()` still resolves to the Dev `src/` tree via `install_source`, on purpose; #146 only changed what pace-maker's OWN `src/hooks/*.sh` scripts do, not this separate consumer's own dynamic-import path. See "Deployment After Code Changes" → "Known exceptions" for the full list and why each one is intentionally left importing from `src/`.
- **No Unix sockets, no subprocess CLI calls, no HTTP — *on the pace-maker channel specifically*.** SQLite + JSON files + dynamic imports are the only IPC surfaces the monitor uses **to reach pace-maker**. The monitor is not HTTP-free or subprocess-free in general: it makes HTTP calls (`claude_usage/api.py:62,98`) and shells out (`claude_usage/auth.py:24`) for other concerns. Scope any "we never do X" reasoning to this channel.

### Read Cadence

- **No background polling** — all reads are reactive (called per-tick by the TUI / API layer).
- **Caller controls cadence** — the monitor's main render loop decides refresh interval, readers are stateless.
- **Caching is opt-in** — individual readers implement TTL caches where needed (see blockage stats).

### Key File References

| Concern | File | Lines |
|---------|------|-------|
| Consumer timeout constant | `claude-usage-reporting/claude_usage/code_mode/pacemaker_integration.py` | 28 |
| Consumer DB path | same | 148-151 |
| Consumer install-source discovery | same | 157-197 |
| Consumer stale-data handling | same | 391-443 |
| Consumer single-row read (Pattern A) | same | 475-505 (`_read_codex_usage`) |
| Consumer windowed aggregate (Pattern B) | same | 559-622 (`get_blockage_stats`) |
| Consumer TTL cache example | same | 653-680 |
| Producer retry helper | `src/pacemaker/database.py` | 442-482 (`execute_with_retry`) |
| Producer WAL + timeout setup | `src/pacemaker/database.py` | 26-28, 427-440 |
| Registry DB setup | `src/pacemaker/session_registry/db.py` | 114-146 |

### When Adding New Tables / Columns

- **Additive `ALTER TABLE` only** — never drop or rename columns; the consumer tolerates missing columns but does not tolerate missing tables well (returns `None` for the whole read). If you must remove a table, coordinate a migration on both sides in the same release.
- **Idempotent migrations** — use `ALTER TABLE` inside try/except for `OperationalError: duplicate column name`. Example: `migrate_codex_usage_schema()` in `hook.py` SubagentStop handler.
- **Document the contract** in this CLAUDE.md section when adding a new table the monitor will read.
- **If you change `database.py`'s `SCHEMA` string, bump `SCHEMA_VERSION` in the same change, and update the pinned hash in `tests/test_database_lock_contention_145.py::TestSchemaVersionPinnedToHash._EXPECTED_SCHEMA_HASHES`.** `initialize_database()` (issue #145) checks `PRAGMA user_version` against `SCHEMA_VERSION` and skips the `CREATE`/`ALTER` statements entirely once they already match — it never diffs the stored schema against the current `SCHEMA` text. A `SCHEMA` change with no matching `SCHEMA_VERSION` bump means every database already at the current version silently never receives the new table/column, since the fast path trusts the version number, not the content. `TestSchemaVersionPinnedToHash` pins `sha256(SCHEMA)` to `SCHEMA_VERSION` precisely to catch this: it fails loudly ("SCHEMA changed: bump SCHEMA_VERSION and record new hash") the moment `SCHEMA` changes without a corresponding version bump.

---

## Version Bumping

**When bumping the version**, ALWAYS update ALL THREE files:
- `src/pacemaker/__init__.py` — the Python package version
- `.claude-plugin/plugin.json` — the Claude Code plugin manifest version
- `pyproject.toml` — the packaging metadata version

These MUST always match. Forgetting `plugin.json` has happened before. `pyproject.toml` is
easy to miss too — `tests/test_plugin_hooks_config.py::TestPluginJson::test_plugin_json_version_matches_pyproject`
asserts `plugin.json`'s version equals `pyproject.toml`'s version, so a two-file bump (missing
`pyproject.toml`) fails that test even though `__init__.py` and `plugin.json` agree with each other.

**Enforcement gap — only 2 of the 3 files are machine-checked.** That test compares `plugin.json` ↔ `pyproject.toml` and nothing else. **No test asserts `src/pacemaker/__init__.py` matches either of them**, so forgetting the `__version__` bump ships a stale package version with a fully green suite. The `__init__.py` bump is enforced by this document and your own diligence only — verify it by hand on every bump.

---

## Claude Code Compatibility Policy

**Backwards compatibility is the contract.** When Claude Code introduces a breaking change to its transcript format, hook payload schema, or hook event lifecycle, pace-maker ADAPTS to handle both the old and new behavior. We do NOT bump the minimum supported Claude Code version to force users to upgrade.

**Why**: Forcing every pace-maker user to upgrade Claude Code on every breaking change would brick their install at the worst possible time. Backwards-compat code in pace-maker is annoying to maintain but invisible to users — that's the right tradeoff.

**Minimum supported Claude Code version**: `2.1.39`

This is the floor pace-maker explicitly tests against and guarantees. The hook code can technically read pre-2.1.39 layouts via fallback paths (see `src/pacemaker/hook.py:2487-2490, 2520-2524`), but anything below `2.1.39` is best-effort, not supported.

**The minimum IS an enforced runtime gate (wired in #96, v2.34.6; notice added in #100).** `perform_session_start_version_check()` is called from `run_session_start_hook()` (`src/pacemaker/hook.py:389`), and `state["version_block_active"]` is checked at hook entry by both `run_stop_hook()` (`hook.py:2171`) and `run_pre_tool_hook()` (`hook.py:2527`) — both return `{"continue": True}` immediately when set, before reading stdin. See the "Minimum Claude Code Version Check (Story #66)" section below for the full current picture, including the issue #100 user-visible notice.

**Adding new compatibility shims** when Claude Code ships a breaking change in a future version:
1. Add an entry to the "Tracked breaking changes" list below — version, what changed, what we adapted, where the shim lives
2. Add the shim/fallback code with a comment naming the Claude Code version that introduced the change
3. Add tests that exercise both old and new behaviors
4. **Do NOT bump `min_claude_version`** in config — we support both old and new. Bumping the minimum is reserved for cases where the old behavior is truly unrecoverable (no shim possible).

**Tracked breaking changes**:

| Claude Code version | What changed | Pace-maker adaptation | Tests |
|---------------------|--------------|----------------------|-------|
| `2.1.39` | Subagent transcripts moved from `<project>/agent-*.jsonl` to `<project>/<session-id>/subagents/agent-*.jsonl` | Hook glob searches both locations (`src/pacemaker/hook.py:2487-2490, 2520-2524`) | `tests/unit/test_subagent_transcript_path.py` |

---

## Danger Bash Validation

**Two-phase validation** for dangerous Bash commands in the PreToolUse hook:

- **Gate preconditions (undocumented until now)**: the whole danger-bash path runs ONLY when config has `enabled` AND `intent_validation_enabled` AND `danger_bash_enabled` (`hook.py:2579-2583`). Any one of the three being false skips danger-bash entirely — `danger_bash_enabled` alone is not sufficient.
- **Before Phase 1 — anchor resolution**: the gate first resolves a tool-matched transcript anchor with a **3.0s** ceiling (`_DANGER_BASH_MAX_WAIT_SECONDS`, issue #93 — NOT the Write/Edit gate's 30s). This can produce a *different* block with a "transcript not ready" message on `not_found`, or silently ACCEPT a `stale` byte-identical re-issue. See the issue #93 section below before touching it.
- **Phase 1 (Regex Gate)**: When a Bash tool call matches the **merged** ruleset (55 bundled defaults **minus `deleted_rules`, plus user additions** — not a fixed 55) and the anchored message contains no `INTENT:` declaration, the command is blocked immediately with no LLM call. This is a fast-reject path. **Issue #151 exception**: for a configured model (default `claude-opus-5-5`) whose anchor's own VISIBLE text has no `INTENT:` marker, this becomes "blocked unless the combined visible-text+reasoning-summary intent is non-empty" instead — see the "Issue #151" section below for the full decision table (`resolve_reasoning_summary_intent_source`). Non-exception models, and an exception model that already declared a real `INTENT:`, are unaffected.
- **Phase 2 (LLM Validation)**: When `INTENT:` is present, an LLM validates that the declared intent aligns with the actual Bash command. **This is a SEPARATE code path from Write/Edit Stage 2, not "the same flow"**: the prompt is built inline at `hook.py:2760-2783` (there is no `prompts/pre_tool_use/` template for it) and the verdict comes from `verdict.verdict_passes()` at `hook.py:2803`. The two gates share only the reviewer resolver and the verdict primitive. **Keep them in sync deliberately — issue #94 was caused by exactly this pair silently diverging.**

**Rule categories**: 25 Work Destruction (WD) rules (git checkout --, git reset --hard, git stash drop, branch deletion, etc.) and 30 System Destruction (SD) rules (rm -rf, kill -9, chmod 777, mkfs, dd, etc.).

**Configuration**: Rules are customizable via `~/.claude-pace-maker/danger_bash_rules.yaml` using the same merge strategy as clean code rules (user config stores only additions and deletion markers, defaults loaded from bundled YAML at runtime).

**Blockage category**: `intent_validation_dangerbash` with label "Danger Bash" in telemetry and blockage stats.

**Key files**:
- `src/pacemaker/danger_bash_rules_default.yaml` — 55 bundled default rules
- `src/pacemaker/danger_bash_rules.py` — loader, merger, matcher module
- `src/pacemaker/hook.py` lines 2575-2882 (`# 2a. Danger Bash validation`) — PreToolUse Bash tool handling. (Was documented as `~2149`, which is CSA session-end code in the **Stop** hook — wrong file region entirely.)

---

## Core-Path Detection (Issue #92)

`_is_core_path()` in `src/pacemaker/intent_validator.py` decides whether a Write/Edit target requires a TDD test-coverage declaration. It replaced a hardcoded 7-word regex (`src|lib|code|core|source|libraries|kernel`) with a 3-layer algorithm, driven by an exhaustive Neo-Production MCP survey of 301 non-Terraform repos across 9 org categories — full evidence trail in `.analysis/core_paths_survey_by_class.md` (master tally) and `.analysis/core_paths_survey_<category>.md` (one per category, repo-by-repo detail).

**Algorithm** (see `_is_core_path(file_path, core_path_segments, exclusions, extensions)`):

- **Layer 0 — universal negative signals, run FIRST, before any positive match**: `is_excluded_path()` (excluded dir) → not core; `is_source_code_file()` (non-source extension, e.g. `.md`/`.yaml`/`.tf`) → not core; `core_path_markers.matches_test_filename_pattern()` (e.g. `*_test.go`, which has no directory-level signal at all) → not core. This ordering is load-bearing: an earlier draft put the word-list match first, so `internal/foo_test.go` matched `internal/` before the suffix check ever ran — caught by a Codex pressure-test, regression-locked by `TestLayer0TestFilenamePrecedenceOverLayer1` in `tests/unit/test_is_core_path_story92.py`.
- **Layer 1 — bare-segment word-list match**, now config-driven via `core_paths.load_paths_with_migration()` (previously hardcoded and never actually read from `core_paths.yaml` — see "Wiring fix" below). Default 11-entry list: the original 7 words plus `app/`, `routes/`, `services/`, `internal/` (evidence: `app` — 7 repos, `routes` — 5 repos incl. the org's FastAPI-serverless template, `services` — 2 root-level repos, `internal` — 2 Go repos, Go-compiler-enforced). **Explicitly and deliberately rejected**: `apps`/`packages` (real code sits one level deeper at `apps/*/src/`, already caught by `src`), `utils`, `worker`/`processor`, `robot`, `libs`/`models`/`jobs`/`common`/`scripts`/`vite-plugins` (single-repo evidence only) — regression-locked in `TestLayer1RegressionLockRejectedWords`.
- **Layer 2 (+2c) — structural marker-file fallback**, only reached when Layer 1 misses (`src/pacemaker/core_path_markers.py`, `has_core_marker()`/`find_project_marker()`): an uncapped upward directory walk from `dirname(file_path)` to the filesystem root, looking for `*.csproj`/`*.sln` (.NET), `pyproject.toml`/`setup.py` (Python), `package.json` (Node), `build.gradle`/`build.gradle.kts`/`pom.xml` (Java/Kotlin), `go.mod` (Go), `Cargo.toml` (Rust). Closes a structural gap no word list can ever cover: 47 of the 301 surveyed repos (15.6%) have real production code whose source-root directory name IS the project name (~9 .NET solutions, ~20 Python flat-layout packages) — a bare-directory-name check can never match a string that's different every time by construction. **No depth cap** — termination (Messi Rule 14) is proven by construction (a real filesystem path has a finite number of ancestors, and the walk strictly climbs toward the root each iteration), not by an arbitrary number; see `TestFindProjectMarkerTermination` (200-level-deep synthetic tree).
- **Layer 2c — test-project marker exclusion**: a found marker whose OWN filename matches `*.Tests.csproj`/`*.Test.csproj`/`*Tests.sln` denotes a dedicated test project, not production code — `has_core_marker()` returns `False` even though a marker was found. Needed because `.NET` test projects (`MyProject.Tests/MyProject.Tests.csproj`) aren't caught by the pre-existing `tests/`/`test/`-only exclusion (case-sensitive, directory-substring only — `.Tests/` with a capital T and no trailing `/tests/` never matched).

**`excluded_paths.py` matcher extension**: `is_excluded_path()` now accepts `*`-prefixed filename-suffix patterns (`matches_filename_pattern()`) in addition to its original directory-substring patterns — needed because Go's `_test.go` has no directory-level signal at all (test files sit in the SAME directory as production code, under the SAME `go.mod`). `core_path_markers.py` reuses this matcher for both Layer 0's `matches_test_filename_pattern()` and Layer 2c's `matches_test_project_marker()` rather than duplicating suffix logic. `get_default_exclusions()` is unchanged (no `*` pattern in the defaults) — the capability is opt-in.

**Wiring fix — `core_paths.yaml` is now live.** Before this story, `_is_core_path()` ignored `core_paths.yaml` entirely; the YAML was only read by `generate_validation_prompt`, unreachable from the real PreToolUse hook path — customizing via `pace-maker core-paths add/remove` had zero effect on actual TDD gating. `validate_intent_and_code()` now loads `core_path_segments` via `core_paths.load_paths_with_migration(DEFAULT_CORE_PATHS_PATH)` and `extensions` via `extension_registry.load_extensions(DEFAULT_EXTENSION_REGISTRY_PATH)`, threading both into `_regex_stage1_check()` → `_is_core_path()`. Plain `core_paths.load_paths()` (used by the CLI) is deliberately left untouched — it does NOT run migration, so a CLI `list`/`add`/`remove` never has the disk-write side effect described next.

**One-time migration** (`core_paths.migrate_if_needed()`, called by `load_paths_with_migration()` on every real hook load): since the YAML was dead code, existing customized files never benefited from anything saved in them — but they also never picked up new code defaults. On first load after this fix, if `core_paths.yaml` exists and is missing any of the 4 new words, they're appended (existing entries — including prior customizations — left untouched, never reordered/removed) and a `_migrated_story_92: true` marker is set in the YAML so a later manual removal of a migrated word is never silently re-added. No-ops safely on a missing file, an already-migrated file, an explicitly-empty `paths:` list (leaves `load_paths()`'s existing defaults-fallback intact rather than synthesizing a partial 4-word list), or malformed YAML. Verified against a real local file found during spec review (`~/.claude-pace-maker/core_paths.yaml`: 6 of 7 pre-story defaults, missing `lib/`, plus `myapp/`/`custom/` custom entries) — primary fixture in `tests/unit/test_core_paths_migration.py`.

**CWD-relative-path caveat (test-isolation gotcha, not a production bug)**: `find_project_marker()` requires an absolute `file_path` and returns `None` immediately for a relative one — it does NOT resolve relative paths via `os.path.abspath()` against the process's CWD. Production is unaffected (Claude Code's Write/Edit `tool_input.file_path` is always absolute), but the first implementation used `abspath()` and broke ~19 pre-existing unit tests that use bare relative shorthand like `"utils.py"`/`"helpers/utils.py"` as `file_path` — these resolved against the test runner's own CWD (this repo's root, which has a real `pyproject.toml`), spuriously finding a marker. If you ever "simplify" `find_project_marker()` back to `os.path.abspath()`, you will reintroduce this exact class of flaky-by-CWD failures across the test suite.

**Test-isolation guard**: `tests/conftest.py`'s `_guard_production_db` autouse fixture monkeypatches `pacemaker.constants.DEFAULT_CORE_PATHS_PATH`/`DEFAULT_EXCLUDED_PATHS_PATH`/`DEFAULT_EXTENSION_REGISTRY_PATH` to fake tmp_path locations — these constants are read via local (call-time) imports in `intent_validator.py`, so patching the module attribute is effective. Without this, the migration's disk WRITE would hit the developer's real `~/.claude-pace-maker/core_paths.yaml` on every test run that exercises `validate_intent_and_code()` end-to-end.

**Deliberate behavior change — `.md`/non-source files under `src/` no longer require TDD.** Layer 0's extension gate runs before Layer 1, so a `.md` file under `src/` (previously flagged `NO_TDD` by the old regex-only check, since it never looked at extension) is now `YES` (no declaration required) — this is the correct, spec-mandated consequence of "no core-path word can bypass a non-source-extension file", not a regression. `tests/fixtures/real_transcript_replay/manifest.json`'s `case_23`/`case_24_md_under_src` fixtures were updated in the same commit (their `expected_stage1` flipped `NO_TDD` → `YES`) per `tests/test_real_transcript_replay.py`'s own "update this helper/manifest IN THE SAME COMMIT" contract.

**Known/accepted blast radius — this repo's own scripts become core paths (issue #92 review finding F-4).** Layer 2's marker-fallback walk means any source file that shares a directory tree with a project marker gets flagged core even with no matching Layer 1 word. In THIS repo specifically, `pyproject.toml` at the repo root is itself a project marker, so `scripts/run_tests.sh`, `install.sh`, `docs/*.py`, and `examples/*.py` all resolve to core paths under Layer 2 (there is already a marker-adjacent test that needed a `chdir` trick to avoid tripping over this in the test suite itself). This is spec-faithful — the marker-fallback design was explicitly discussed and deliberately left this way pending real usage data — not a defect to "fix" by narrowing the walk. If you hit an unexpected TDD-required prompt on a script/docs file in this or another repo, this is why.

**Key files**:
- `src/pacemaker/intent_validator.py` — `_is_core_path()` (3-layer algorithm), `_regex_stage1_check()` (wiring, optional `core_path_segments`/`extensions` params default to pure in-memory `get_default_paths()`/`get_default_extensions()` for tests), `validate_intent_and_code()` (real hook wiring)
- `src/pacemaker/core_path_markers.py` — Layer 2/2c: `find_project_marker()`, `has_core_marker()`, `matches_test_project_marker()`, `matches_test_filename_pattern()`, `_find_marker_in_dir()` (candidate-name-first via `os.scandir()`, deterministic fixed-priority multi-marker selection — issue #92 review findings F-2/F-7)
- `src/pacemaker/core_paths.py` — `get_default_paths()` (11 entries), `migrate_if_needed()`, `load_paths_with_migration()`, `NEW_STORY_92_WORDS`, `MIGRATION_MARKER_KEY`, `add_path()` (rejects degenerate slash-only segments — F-5), `_write_paths()` (read-modify-write, preserves non-`paths` top-level keys like the migration marker — F-1)
- `src/pacemaker/excluded_paths.py` — `matches_filename_pattern()`, extended `is_excluded_path()`, `add_exclusion()` (skips trailing-slash normalization for `*`-prefixed suffix patterns — F-6)
- `tests/unit/test_is_core_path_story92.py` (27 tests), `tests/unit/test_core_path_markers.py` (33 tests), `tests/unit/test_core_paths_migration.py` (22 tests), `tests/unit/test_core_paths.py` (23 tests), `tests/test_excluded_paths_suffix_patterns.py` (25 tests), `tests/test_core_paths_cli_wiring_story92.py` (2 tests — real CLI-to-gate round-trip, F-3)

---

## Cross-Session Awareness Registry

**Story #64**: Prevents rogue-agent hallucinations by giving each session factual evidence of sibling sessions.

### Architecture
- **Storage**: `~/.claude-pace-maker/session_registry.db` (separate from `usage.db`), WAL mode, 2s busy_timeout
- **Env override**: `PACEMAKER_SESSION_REGISTRY_PATH` overrides DB path (REQUIRED in test mode)
- **Test-mode enforcement**: `db.py` raises `RuntimeError` if `PACEMAKER_TEST_MODE=1` and `PACEMAKER_SESSION_REGISTRY_PATH` is unset
- **Workspace key**: `git rev-parse --show-toplevel` at SessionStart (source=startup/resume only), fallback `os.path.realpath(os.getcwd())`
- **Session identity**: Root session `session_id` from `hook_data["session_id"]`; subagents reuse parent's session_id
- **Purge**: Records with `last_seen < now() - 20min` purged on every `heartbeat_and_purge` call

### Key Files
- `src/pacemaker/session_registry/db.py` — SQLite schema, connection mgmt, test-mode path enforcement
- `src/pacemaker/session_registry/registry.py` — `register_session`, `heartbeat_and_purge`, `list_siblings`, `unregister_session`
- `src/pacemaker/session_registry/workspace.py` — `resolve_workspace_root(cwd)` git + fallback resolver
- `src/pacemaker/session_registry/nudges.py` — `build_start_banner`, `build_periodic_reminder`, `build_danger_bash_warning`
- `src/pacemaker/session_registry/_csa.py` — Hook integration: `on_session_start`, `on_subagent_start`, `on_heartbeat`, `on_pre_tool_use`, `on_session_end`
- `src/pacemaker/hook.py` — Hook wiring to `_csa`: `~382` (SessionStart), `~661` (SubagentStart), `~723` (SubagentStop), `~1032`/`~1046` (PostToolUse heartbeat + `record_action` — **see the Config Gate warning below; `~1046` bypasses `_csa` entirely**), `~1397` (UserPromptSubmit heartbeat), `~2133` (Stop), `~2555` (PreToolUse). (The previously documented `~1946`/`~2305` point at no CSA code at all.)

### CLI
```bash
pace-maker sessions list   # Show active registry sessions (filters out >20min stale rows)
```

### Nudge Channels
1. **SessionStart banner** — fired on source=startup/resume when siblings found
2. **SubagentStart banner** — via `hookSpecificOutput.additionalContext`
3. **Periodic reminder** — every 5th PreToolUse per agent_id
4. **Danger_bash warning** — injected into Stage 2 LLM context when Bash matches a danger rule

### Config Gate
```json
{ "cross_session_awareness_enabled": true }
```
**⚠️ The gate is INCOMPLETE — `false` does NOT stop all registry writes (issue #97).** What the gate actually covers: every `_csa.*` entry point returns early, so there are no banners, no periodic reminders, no danger-bash sibling warning, and no session-table writes via `_csa`. What it does **NOT** cover: the PostToolUse `record_action` / `update_agent_heartbeat` block at **`hook.py:1046-1066` calls `session_registry.registry` DIRECTLY, bypassing `_csa`, and is not gated at all**. So with `cross_session_awareness_enabled: false` the DB still receives `agent_actions` INSERTs and `agents.last_seen` UPDATEs on every tool call. Do not describe this config key as a full kill switch.

### State Schema (namespaced under `cross_session_awareness`, keyed by session_id)
```json
{
  "cross_session_awareness": {
    "<session_id_A>": {
      "workspace_root": "/path/to/repoA",
      "seen_agent_ids": ["root", "abc123"],
      "tool_use_counter": {"root": 0, "abc123": 0}
    },
    "<session_id_B>": {
      "workspace_root": "/path/to/repoB",
      "seen_agent_ids": ["root"],
      "tool_use_counter": {"root": 0}
    }
  }
}
```

**CRITICAL — why this must be keyed by session_id**: `~/.claude-pace-maker/state.json` is a SINGLE global file shared across all concurrent Claude Code sessions on the machine (pace-maker's existing architecture uses one file for all sessions, with `session_id` as a top-level field updated by whichever session wrote last). The original story-#64 design used a flat `cross_session_awareness` block without session_id scoping, which caused catastrophic cross-workspace pollution: session A's `workspace_root` cache would be overwritten by session B's SessionStart, and session A's subsequent sibling queries would then use session B's workspace_root, leaking cross-repository sibling info. The fix (v2.19.1) keys every CSA entry by `session_id` so each session strictly reads and writes only its own sub-dict. `on_session_end` garbage-collects the session's sub-dict to prevent unbounded growth. See `src/pacemaker/session_registry/_csa.py::_get_cs(state, session_id)` and `tests/test_session_registry_csa_session_scoping.py`.

### Test Isolation
- `tests/conftest.py` sets `PACEMAKER_SESSION_REGISTRY_PATH` to a tmp path in the autouse `_guard_production_db` fixture (`pytest.ini` plays no part in this)
- Tests that need registry isolation use `monkeypatch.setenv("PACEMAKER_SESSION_REGISTRY_PATH", str(tmp_path / "sessions.db"))`
- E2E tests use synthetic sibling seeding (direct SQLite INSERT + verify nudge responses)

---

## Minimum Claude Code Version Check (Story #66)

> **STATUS: WIRED (issue #96, v2.34.6) — plus a user-visible notice (issue #100).**
>
> This section read "NOT WIRED — THIS FEATURE DOES NOTHING AT RUNTIME" from the moment it was written in commit `5901676` (2026-08-10 09:30:21) until bug #100 corrected it here today (2026-08-29) — roughly 19 days, not the "two months" an earlier version of this note claimed (that figure had been copied from an unrelated paragraph about issue #94 elsewhere in this file). The underlying code was actually fixed far sooner than the doc: `8b88701` landed the wiring the same day, 2h35m later (2026-08-10 12:05:47), so the code was correct for nearly the entire 19-day window while this doc still said otherwise. Corrected here (bug #100 review finding F-1) against the code as it exists today — verify current line numbers yourself before citing them again, they drift.
>
> - `perform_session_start_version_check()` **is called** from `run_session_start_hook()` at `src/pacemaker/hook.py:389`.
> - `state["version_block_active"]` **is read** by both downstream gates: `run_stop_hook()` at `hook.py:2171` and `run_pre_tool_hook()` at `hook.py:2527`. Both return `{"continue": True}` immediately when the flag is set, before stdin is even read (so no CSA/danger-bash/intent-validation work runs at all on a blocked session).
> - **PostToolUse (`run_hook()`, `hook.py:1045`) has NO version guard, by design** — it keeps running normally under a version block (Langfuse pushes, CSA registry writes, `usage.db` telemetry, pacing all still fire). PostToolUse is not the only hook this applies to, though: the guard exists at exactly two call sites, `run_stop_hook()` and `run_pre_tool_hook()` — so four other hook entry points are equally unguarded by design: `run_subagent_start_hook()`, `run_subagent_stop_hook()`, `run_hook()` (PostToolUse), and `run_user_prompt_submit()`. This matches the guard comments at `hook.py:2166-2170` and `hook.py:2522-2526`, which correctly scope the guard to "PreToolUse and Stop ONLY".
> - **Issue #100 adds a user-visible notice.** SessionStart prints `state["version_block_message"]` to stdout, wrapped in the `version_block_notice` provenance tag via `prompt_provenance.format_tag()` (`hook.py:404-413`). SessionStart cannot be blocked via a non-zero exit code — confirmed against Claude Code's own hooks documentation during #100's review — so `additionalContext` (plain stdout) is the only reliably-surfaced channel; the pre-existing stderr-only `_BLOCK_MESSAGE` is not reliably visible (transcript/debug mode only).

### Architecture

- **Version probe**: `subprocess.run(["claude", "--version"], timeout=5)` — any failure (FileNotFoundError, TimeoutExpired, non-zero exit, parse error) returns `None` and the check fails-open (no block).
- **Minimum value**: `_FALLBACK_MIN_VERSION = "2.1.39"` in `src/pacemaker/version_check.py:32` (shifted from line 20 by #100's docstring expansion — verify the current line before citing it, it has already moved once). `DEFAULT_CONFIG["min_claude_version"] = "2.1.39"` **does exist**, at `src/pacemaker/constants.py:36` — the prior claim that no such key exists was wrong; setting `min_claude_version` in `config.json` overrides the fallback via `config.get("min_claude_version", _FALLBACK_MIN_VERSION)` (`version_check.py:121`).
- **Block flag**: `state["version_block_active"]` is set `True` when the installed version is below minimum, `False` on every other path (parse failure, probe failure, version OK, or an unexpected exception caught by the outer fail-open wrapper).
- **`version_block_message`** (issue #100): set to an actionable plain-text notice body (`_CONTEXT_NOTICE.format(...)`) only on the blocked path; explicitly set to `None` on every other path — parse failure, probe failure, version OK, AND the outer exception handler — so a stale notice from an earlier blocked check can never leak into a later clean session (e.g. after the user upgrades). See the docstring at `version_check.py:1-23` for the full rationale, including why this is a separate channel from the stderr-only `_BLOCK_MESSAGE`.
- **Fail-open is adversarially verified**, not just claimed: bug #100's review forced an `ImportError` in the check path together with a malformed on-disk `version_block_message` simultaneously, and the session still proceeded cleanly with empty stderr and no block.
- **Downstream hooks**: PreToolUse and Stop check `version_block_active` at entry and return `{"continue": True}` before any other work — see the Stop/PreToolUse line citations above.

### Key Files

- `src/pacemaker/claude_code_version.py` — `ClaudeCodeVersion` dataclass: `parse()`, `compare()`, `is_below()`, `probe_installed_version()`
- `src/pacemaker/version_status_db.py` — SQLite DB following session_registry pattern: `resolve_db_path()`, `record_status()`, `read_status()`
- `src/pacemaker/version_check.py` — `perform_session_start_version_check(state, config, stderr)` with full fail-open wrapper; `_FALLBACK_MIN_VERSION` at line 32; `version_block_message` set/cleared on every path (issue #100).
- `src/pacemaker/hook.py` — SessionStart caller (`:389`) and additionalContext notice emission (`:404-413`); Stop guard (`:2171`); PreToolUse guard (`:2527`); PostToolUse (`run_hook()`, `:1045`) has no guard, by design.

### Version Status DB

Follows the session_registry pattern exactly:
- **Env override**: `PACEMAKER_VERSION_STATUS_PATH` overrides DB path
- **Test-mode enforcement**: raises `RuntimeError` if `PACEMAKER_TEST_MODE=1` and env var is unset
- **Single-row upsert**: `INSERT ... ON CONFLICT(id) DO UPDATE SET` — always id=1
- **Fail-open reads**: `read_status()` catches all exceptions at DEBUG level, returns `None`
- **Named constant**: `_READ_TIMEOUT_SECONDS = 5.0`

**No CLI, no status line.** `pace-maker min-claude-version` **does not exist** (command patterns in `user_commands.py` stop at 26; there is no `_execute_min_claude_version()`), and `pace-maker status` has **no "Claude Code:" line**. Both were previously documented here and were removed as fiction — do not re-add them without the implementation. (Verified still true as of this correction.)

### Test Isolation

`tests/conftest.py` sets `PACEMAKER_VERSION_STATUS_PATH` to a tmp path in `_guard_production_db` fixture, preventing tests from writing to `~/.claude-pace-maker/version_status.db`.

### Test Files

- `tests/test_claude_code_version.py` — **48** unit tests (parse, compare, is_below, probe, DB, plus issue #100's 3 SessionStart notice-emission tests under "issue #100: SessionStart user-visible signal").
- `tests/test_version_check_integration.py` — **14** component tests, including issue #100's 5 `version_block_message` tests under "issue #100: `state["version_block_message"]`". `TestBlockedHooksEarlyReturn`'s 2 tests are **no longer vacuous**: they were rewritten (see the class docstring, `tests/test_version_check_integration.py:314-320`) to assert the exact `{"continue": True}` early-return payload AND that stdin was never read, so removing the guard now makes them fail for real.

### A note for future edits to this feature

Read the current code (the line citations above, and the module docstrings) before changing anything here — they have already drifted once (#96 wired the feature the same day this doc was written claiming it wasn't, yet this doc kept calling it "NOT WIRED" for ~19 days until #100 corrected it) and the constants/line numbers move as the file changes. There is no "unwired" state to worry about anymore; this is ordinary maintenance discipline, not a prohibition.

---

## Langfuse Trace Attribution — Bug #158

**Symptom**: with several concurrent Claude Code sessions, subagent tool spans landed in another session's (even another project's) subagent trace, and a subagent's own spans landed in its parent's main trace. Surfaced once #157 (v2.37.1) made SubagentStart register subagent traces.

**Root cause (reproduced at unit level, `tests/test_issue_158_langfuse_attribution.py`)**: `orchestrator.handle_post_tool_use()` chose the trace from the GLOBAL `~/.claude-pace-maker/state.json` fields `in_subagent` / `current_subagent_trace_id` / `current_subagent_agent_id`. That file is one shared slot for every session on the machine ("most recently registered subagent wins"; any SubagentStop pops it). So any session's PostToolUse — main thread or subagent — was redirected to whichever subagent registered last anywhere, and a subagent whose slot was overwritten/cleared wrote to its parent's main trace. A second instance: `run_subagent_stop_hook()` fell back to that same slot when its own `agent_id` was not in `subagent_traces`, finalizing another agent's trace.

**Rule — attribution comes ONLY from the hook payload identity**:
- `hook.run_hook()` passes the payload's `agent_id` (None for the main thread) to `handle_post_tool_use(..., agent_id=...)`.
- `agent_id` present → that agent's own state file `langfuse_state/subagent-<agent_id>.json` (trace_id + last_pushed_line). No such state/trace → the trace-bound work (intel push, span, state update) is SKIPPED and the call returns False — never redirected to the parent or any other trace. The session-level steps still run: `🔐` secret-declaration collection and the parent's `pending_trace` flush (they would otherwise be lost, since the flush may only ever happen on a subagent's call).
- `agent_id` absent → the session's main trace. The global `state.json` is never read for attribution (`DEFAULT_STATE_PATH` is no longer imported by the orchestrator).
- SubagentStop's legacy single-slot fallback is used only when the payload has no `agent_id` or the slot's agent matches it.
- **Compatibility note**: a Claude Code that does not send `agent_id` on PostToolUse for subagent calls cannot be told apart from the main thread, so such calls land in the SAME session's main trace (safe, never cross-session). Do not re-add a global-state heuristic to "recover" them — that is the bug.
- `state.json` `in_subagent` / `current_subagent_*` are still written (counter, reminder gating, SubagentStop backward-compat) but are NOT an attribution source.

## Secrets Store Hygiene and Langfuse State Cleanup — Bug #160

**Item 1 — mask-marker fragment stored as a secret.** A masked string (`... *** MASKED ***`) pasted into a `🔐 SECRET_TEXT:` line stores a fragment of the marker as a "secret"; every masked value then "contains a secret" (84 false hits in one leak audit) and the fragment keeps re-masking ordinary text. Rules:
- `secrets/masking.py::is_degenerate_secret()` (with the shared `MASK_MARKER` constant) is the one definition: empty/whitespace-only, a case-sensitive substring of `*** MASKED ***`, or text made only of marker repetitions. Real content that merely surrounds a marker is NOT degenerate.
- **Store**: `database.create_secret()` refuses degenerate values — logs a warning (never the value), returns `None`, never raises into the hook. `parse_assistant_response()` therefore stores nothing for them; `pace-maker secrets add/addfile` give an explicit error.
- **Masking**: `database.get_all_secrets()` (the read path used by `sanitize_trace` and reviewer-prompt masking) EXCLUDES degenerate rows, so already-stored ones stop polluting traces/audits immediately.
- **Existing data is never modified by code.** `list_secrets()` still returns degenerate rows and `pace-maker secrets list` flags them ("degenerate: ignored by masking; remove with 'pace-maker secrets remove <id>'"). Removing the real offender (id 97 on the dev machine) is the user's decision.
- Leak-audit scripts that read the `secrets` table directly should use `list_secrets()`/`get_all_secrets()` semantics (skip degenerate rows) or they will keep reporting false hits.

**Item 3 — subagent state files "never cleaned up".** Verified facts: a 7-day cleanup already existed (`StateManager.cleanup_stale_files`, called from `run_session_start_hook()`) and works; the 242 `langfuse_state/subagent-*.json` files were ALL younger than 7 days (oldest 6.94 d) — TTL not yet elapsed, not a missed cleanup. The real gap: a subagent's state is dead once it stops, but kept for 7 days (~35 files/day). Now:
- `cleanup_stale_files(max_age_days, subagent_max_age_days=None)` — `subagent-*.json` use `SUBAGENT_STATE_MAX_AGE_DAYS` (2; generous because mtime refreshes on every tool call); session files keep 7 days.
- `maybe_cleanup_stale_files()` runs it at most once per `CLEANUP_MIN_INTERVAL_SECONDS` (24 h), throttled by the mtime of `langfuse_state/.last_cleanup` (no `.json` suffix, never matched by the glob); otherwise one `stat()`. Never raises. SessionStart calls this version.
- Bug #158 interplay: a subagent whose state file was expired and then resumes gets its spans skipped (never misattributed).

**Item 2 — short secrets masked ordinary words in Langfuse traces** (`"raises_value_error"` → `"raises_*** MASKED ***_error"` for a stored `value`). User decision: **token-boundary match for short secrets**.
- **Two passes, one builder.** `masking._build_secrets_pattern()` (behind `mask_text`, `mask_structure`, `build_prefiltered_pattern`, `sanitize_trace`) returns a `SecretsMasker` (use `.mask(content)`; it is NOT a `re.Pattern`). Pass 1 masks every secret of 8+ chars (`SHORT_SECRET_TOKEN_BOUNDARY_BELOW`) anywhere, longest first. Pass 2 masks the shorter secrets only as standalone tokens, only in the text BETWEEN pass-1 matches (a masked span is an impenetrable non-word separator: a short secret can't run into it or be re-masked inside it).
- **Why two passes (review M1, security):** with ONE combined alternation, a short alternative that fails its boundary stops consuming, so another short secret could match later and overlap the start of a long secret, leaving it exposed (`["z.a","a-k","k.LONGSECRETVALUE"]` on `"wz.a-k.LONGSECRETVALUE end"`). Invariant: an 8+ char secret never survives in the output (barring two long secrets that overlap each other in the input, which is pre-existing behaviour).
- **Boundary = `\b`-style (review L1):** the lookbehind `(?<![A-Za-z0-9_])` is added only when the short secret's FIRST char is a word char, the lookahead only when its LAST char is. `-abc` is masked in `x-abc`; `abc` is not masked in `xabc`. Standalone means string start/end, spaces, `=`, `:`, `.`, quotes, etc. around a word-char edge.
- **ASCII-only word class (review L4, documented, not changed):** `é` etc. count as boundaries. That can only over-mask (a short secret next to an accented letter is masked) — the safe direction.
- Short secrets are still ACCEPTED for storage (`create_secret` only refuses degenerate values).
- The #157 prefilter is unchanged: `secret in text` is still a valid superset check (a short secret occurring only inside a word is kept in the subset and simply never matches; pass-2 pieces are always substrings of the original text, so no new matches can appear), so output stays identical to full-store masking from the same builder. `intent_validator._mask_reviewer_prompt` keeps its own min-length-8 skip (`_MIN_REVIEWER_MASK_SECRET_LENGTH`), unchanged — it only ever feeds ≥8-char secrets, i.e. pass 1 only.
- Refusal warnings use pace-maker's `log_warning("secrets", …)` (never stdlib `logging`, which has no handler in hook processes), and never contain the value.
- Tests: `tests/test_issue_160_short_secret_boundaries.py` (regex-free two-phase reference oracle, punctuation/adjacency fuzz, no-long-secret-survives invariant, the M1 repro), `tests/test_issue_157_secret_prefilter.py` (mixed short/long fuzz).

**Prune predicate for data cleanup (one-off, owner-run)**: `pacemaker.secrets.masking.is_degenerate_secret(value: str) -> bool`; table `secrets(id INTEGER PRIMARY KEY, type TEXT, value TEXT, created_at INTEGER)` in the secrets DB (`usage.db`, `~/.claude-pace-maker/`). Select rows with `SELECT id, type, value FROM secrets`, apply the predicate in Python, `DELETE FROM secrets WHERE id = ?` for matches (or `pace-maker secrets remove <id>`). Code never prunes automatically.

## Reviewer Identity Tracking

When intent validation runs Stage 2 (LLM code review), the reviewer identity is tracked end-to-end:

1. `resolve_and_call_with_reviewer()` in `inference/registry.py` returns `(response, reviewer_name)` tuple
2. `intent_validator.py` threads reviewer through validation result dict (`"reviewer": reviewer`)
3. `hook.py` records reviewer in blockage_events details JSON
4. Governance event `feedback_text` is prefixed with the **bare label in brackets** — `[codex-gpt5]`, `[anthropic-sdk]`, `[gem-flash]`. See `hook.py:3106-3107` and `hook.py:2817-2818`. **There is NO `REVIEWER:` prefix**; this file previously documented `[REVIEWER:xxx]`, which never shipped and contradicted the Competitive Review Pipeline section's own `[expression]` tag format. Any consumer parsing for `REVIEWER:` will match nothing.

The reviewer tag enables the claude-usage monitor to display colored reviewer identity in the governance event feed.

---

## Codex PAYG Billing Handling

The `codex_usage.py` module handles both subscription and PAYG (Pay-As-You-Go) Codex billing:

- **PAYG detection lives in the CONSUMER, not here**: `codex_usage.py:160` only stores `limit_id` **verbatim** — it makes no PAYG judgement. The `limit_id == "premium"` → PAYG interpretation is implemented in `claude-usage-reporting/claude_usage/code_mode/display.py:1079`. Change detection logic there, not in this repo.
- **Null handling**: `_parse_last_token_count()` gracefully handles null `primary`/`secondary` fields that Codex returns for PAYG billing (no usage percentages available)
- **`limit_id` column**: Added to `codex_usage` SQLite table via idempotent `ALTER TABLE` migration in `migrate_codex_usage_schema()`
- **SubagentStop wiring**: Migration is called in `hook.py` SubagentStop handler before writing codex usage data

---

## Codex Profile Provider

**argv includes `--skip-git-repo-check`** — both branches in `CodexProvider.query()` pass this flag immediately after `exec`:

```
# Profile mode
["codex", "exec", "--skip-git-repo-check", "-", "--profile", <name>, "-s", "read-only"]

# Non-profile mode
["codex", "exec", "--skip-git-repo-check", "-", "-m", <model>, "-s", "read-only"]
```

**Why this is necessary**: codex 0.139 introduced a trusted-directory guard that causes exit 1 with the message "Not inside a trusted directory and --skip-git-repo-check was not specified." when codex is invoked from a working directory that is not in codex's trust list. This guard fires when the pace-maker hook runs from a user's project directory — causing `ProviderError` and silent fallback to the Anthropic SDK, making the configured codex/gpt-5.5 reviewer silently downgrade to `anthropic-sdk`.

**Why it is safe**: pace-maker already invokes codex with `-s read-only` (codex's own sandbox flag). The git-repo guard provides no additional protection when the sandbox is active — codex cannot write or execute anything regardless. `--skip-git-repo-check` is codex's own prescribed remedy for this exact scenario.

**Key file**: `src/pacemaker/inference/codex_provider.py`

---

## Running Tests

**NEVER run tests as a single pytest process** (`python -m pytest tests/`). SQLite WAL contention causes hangs when multiple test files create DBs concurrently in the same process.

**Always use the independent test runner:**

```bash
./scripts/run_tests.sh          # Run all tests (each file independently)
./scripts/run_tests.sh --quick  # Skip slow e2e tests
./scripts/run_tests.sh --tb     # Show failure tracebacks
```

**Why:** Each test file gets its own pytest process with a 30s timeout, avoiding WAL lock contention between concurrent DB teardown/setup cycles.

**Test mode optimization:** `PACEMAKER_TEST_MODE=1` is set automatically by `conftest.py`, enabling `PRAGMA synchronous=OFF` for 20x faster DB operations in tests.

**Dev/test setup (issue #144 code-review follow-up #4):** `requirements.txt` only pins the hooks' own runtime deps (`requests`, `pyyaml`, `claude-agent-sdk`) — it is NOT sufficient to run the suite. `requirements-dev.txt` additionally pins `pytest`, `pytest-timeout`, and `responses` (hard-imported by `tests/unit/test_langfuse_provisioner.py` / `test_langfuse_provision_command.py` to mock the Langfuse HTTP API). A clean checkout needs both:

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

Without `responses` installed, those two files collection-error on any interpreter. `run_tests.sh` detects this per-file and prints a hint (`pip install -r requirements-dev.txt`) alongside the file in the ERRORED list.

**Which interpreter runs the tests:** `run_tests.sh`'s `resolve_test_python()` auto-picks an interpreter that has both `claude_agent_sdk` and `pytest` importable (preference order: `PACEMAKER_TEST_PYTHON` override → active `$VIRTUAL_ENV/bin/python` → `python`/`python3.11`/`python3.10`/`python3`), falling back to a pytest-only candidate (with a loud stderr warning — SDK spawn-guard tests won't exercise the real SDK path) or, as a last resort, the first existing candidate at all (louder warning still) if nothing has pytest. Override with `PACEMAKER_TEST_PYTHON=/path/to/python` if auto-detection picks the wrong one.

---

## Deployment After Code Changes

**CRITICAL**: After completing code changes to hook logic (`src/pacemaker/`), you MUST run the installer to deploy:

```bash
./install.sh
```

**Why:**
- Hooks are installed in `~/.claude/hooks/` (not the project directory)
- Code changes in `src/pacemaker/` won't take effect until hooks are reinstalled
- The installer copies updated Python modules, hook scripts, and prompt templates to the active location

**`./install.sh` is now the ONLY action that makes code live for hook processes (issue #146).** Before the fix, every generated hook script's non-pipx branch did `export PYTHONPATH="$SOURCE_DIR/src:$PYTHONPATH"` — so every hook process on the machine imported `pacemaker` straight from this Dev working tree, live, on every invocation. A half-finished edit (missing symbol, syntax error) in `src/pacemaker/` crashed every hook machine-wide until the edit was completed or reverted (26 `NameError` crashes were observed in `hook_debug.log` from exactly this). Running `./install.sh` was **not** the step that deployed code — it was almost incidental, since the live import bypassed the snapshot it copies. **This claim is scoped to hook processes specifically** — see "Known exceptions" below for the other places pace-maker code still legitimately (or hazardously) resolves to the Dev tree.

Fixed: all 7 templates in `src/hooks/*.sh` now default to importing `pacemaker` from the **installed snapshot** — the directory the deployed hook script itself lives in (`~/.claude/hooks/`, where `install_hook_modules()` already copies `pacemaker/` and always has). `HOOK_SELF_PATH="$(readlink -f "$0" 2>/dev/null || echo "$0")"` resolves the script's own real path (symlink-safe), `HOOK_SCRIPT_DIR="$(cd "$(dirname "$HOOK_SELF_PATH")" && pwd)"` computes that directory at hook-run time, and `export PYTHONPATH="$HOOK_SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"` is what gets exported (the `${PYTHONPATH:+:$PYTHONPATH}` guard avoids appending a bare trailing `:` when `PYTHONPATH` starts empty — a bare `:` is an empty-string sys.path entry, which Python treats as the current working directory). The Dev tree (`$SOURCE_DIR/src`, from `~/.claude-pace-maker/install_source`) is no longer on `PYTHONPATH` by default, so uncommitted/half-finished edits there cannot crash live hooks — only an explicit `./install.sh` run copies them in.

**Round 2 hardening (same issue #146, code-review follow-up)** — two additional hazards, both closed:
1. **`python -m` prepends the current working directory to `sys.path` AHEAD of `PYTHONPATH`, regardless of what `PYTHONPATH` contains.** Running an installed hook script with `cwd` set to this repo's own `src/` directory (which itself contains a real `pacemaker/` package) imported the Dev tree anyway, no matter how correctly `PYTHONPATH` was built. Fixed by exporting `PYTHONSAFEPATH=1` unconditionally, in every branch, right before the final invocation (Python 3.11+) — this tells `python -m` not to prepend `cwd` at all. **On Python 3.10 the variable is ignored, so the cwd-shadowing protection is ABSENT there**: a 3.10-only machine running a hook from a directory that contains `pacemaker/` still imports that copy. The hooks select python3.11 (with `claude_agent_sdk`) first, so this machine is protected. **Symlink note:** `HOOK_SCRIPT_DIR` is resolved with `readlink -f "$0"`. If someone hand-symlinks `~/.claude/hooks/*.sh` back to `Dev/.../src/hooks/*.sh`, the directory resolves to `src/hooks`, which contains no `pacemaker/`, so those hooks skip every run with a loud `hook_debug.log` line. They used to work in that setup. `install.sh` always copies, never symlinks, so only hand-made setups are affected.
2. **Silent fallback to the Dev tree when the installed snapshot is simply missing.** If `$HOOK_SCRIPT_DIR/pacemaker/__init__.py` doesn't exist (a broken/partial deploy, or a hook script placed without ever running `install_hook_modules()`), Python would otherwise silently resolve `import pacemaker` to whatever else is on `sys.path` — in practice, a leftover editable install (see "Known exceptions" below) pointing straight back at the Dev tree, with no error and no warning. Fixed with a fail-open guard, placed right before `PYTHONPATH` is set in the default branch: if `$HOOK_SCRIPT_DIR/pacemaker/__init__.py` is missing, a loud line is appended to `~/.claude-pace-maker/hook_debug.log` (script name, expected path, instruction to run `./install.sh`) and the script `exit 0`s immediately — matching the exit style of the pre-existing `enabled` config guard at the top of every hook script, never reaching `python -m pacemaker.hook` at all.

**Opt-in live-dev escape hatch**: set `PACEMAKER_DEV_LIVE_SRC=1` in your shell before invoking hooks (or export it in a dev-only wrapper) to restore the old behavior and import directly from `$SOURCE_DIR/src` — useful for iterating on hook code without reinstalling on every change, but this is **never** the default and must be set explicitly. The pipx-install branch and the "no `install_source` marker" branch are unchanged (pipx already resolves to a real installed venv package; no-marker never set `PYTHONPATH` at all).

**Not touched by this fix — different distribution mechanism**: `scripts/hook.sh` (the Claude Code *plugin* entry point, invoked via `$CLAUDE_PLUGIN_ROOT`) has an analogous-looking `SOURCE_DIR/src` branch, but for a real plugin install `$CLAUDE_PLUGIN_ROOT` already points at the plugin manager's own managed checkout — there is no separate "install.sh copies a snapshot" step in that flow, so importing from `$PLUGIN_ROOT/src` there is the intended model, not the bug this section describes. Only a developer who sideloads the plugin pointing directly at this Dev clone recreates the live-edit hazard, and that is an explicit, self-inflicted dev choice (analogous to `PACEMAKER_DEV_LIVE_SRC=1` above), not a default-install failure mode. If `scripts/hook.sh` ever needs the same treatment, do it as its own change — it shares `~/.claude-pace-maker/install_source` with the classic installer, so the two are related but not identical.

**Known exceptions — places pace-maker code still resolves to the Dev `src/` tree even after this fix, all deliberate or explicitly opt-in, none of them hook processes:**
1. **The claude-usage monitor's dynamic import.** `claude-usage-reporting/claude_usage/code_mode/pacemaker_integration.py`'s `_get_pacemaker_src_path()` reads `~/.claude-pace-maker/install_source` and adds `<install_source>/src` to `sys.path`, then calls `UsageModel.get_current_usage()` **in-process** inside the monitor's own Python interpreter — a completely separate consumer/code path from the hook scripts this issue fixes. This is unchanged by #146 and stays that way; see "Beyond SQLite → Dynamic imports" above.
2. **The editable pip install (`pip install -e`).** `~/.local/lib/python3.11/site-packages/__editable__.claude_pace_maker-*.pth` puts this Dev repo's `src/` on **every** `python3.11` process's `sys.path` via `.pth`-based site processing — this happens at interpreter startup, unconditionally, regardless of `PYTHONSAFEPATH` (which only stops `cwd`/script-dir prepending, not `site.py`'s own `.pth` handling) and regardless of `PYTHONPATH` ordering. It does not normally win over the installed snapshot (Python resolves the FIRST matching `sys.path` entry, and `PYTHONPATH` entries are consulted before `site-packages` entries) — but it is exactly what silently serves `import pacemaker` if the installed-snapshot guard above ever finds `pacemaker/__init__.py` missing and DIDN'T fail open first. This `.pth` file is not pace-maker's to remove (it's this developer machine's own `pip install -e .` artifact) — it is documented here as a known hazard, not fixed.
3. **`PACEMAKER_DEV_LIVE_SRC=1`.** The explicit opt-in described above, by design.

**When to Deploy:**
- After any changes to `src/pacemaker/*.py` files
- After refactoring hook logic or intent validation
- After bug fixes in the pacing engine
- After modifying validation prompts in `src/pacemaker/prompts/`
- Before testing hook behavior changes

**Deployment Workflow:**
1. Make code changes in `src/pacemaker/`
2. Write/update tests (ensure >90% coverage)
3. If modifying intent validation logic: ASK USER to run `pace-maker intent-validation off`
4. **Run `./install.sh` to deploy** ← CRITICAL STEP
5. If user disabled validation: ASK USER to run `pace-maker intent-validation on`
6. Test the deployed hooks with manual verification

**NOTE**: Claude must NEVER disable intent validation directly. Only the user can do this.

Without running the installer, your code changes remain undeployed and inactive for hook processes — this is now literally true again for hooks specifically, not just aspirational: the default `PYTHONPATH` points at the last-installed snapshot, not at whatever is currently on disk in `src/pacemaker/`. It is NOT true for the claude-usage monitor or for a session with `PACEMAKER_DEV_LIVE_SRC=1` set — see "Known exceptions" above.

**Key files for this fix**: `src/hooks/*.sh` (7 templates — `HOOK_SCRIPT_DIR`/`PACEMAKER_DEV_LIVE_SRC`/`PYTHONSAFEPATH`/missing-snapshot-guard logic in the non-pipx branch), `tests/test_hook_shell_pythonpath_snapshot.py` (regression coverage: default-uses-snapshot, opt-in-restores-dev-src, pipx-branch-unaffected, cwd-decoy-not-shadowing, missing-snapshot-guard-fails-open-loudly, real-interpreter-resolves-installed-package — one parametrized case per script per class).

## Intent Validation Development

**Bootstrapping Problem**: When modifying intent validation code while validation is enabled, you create a circular dependency where the validator blocks changes to itself.

**Solution**: The USER (not Claude) must temporarily disable intent validation:

```bash
# USER runs this command (Claude must NEVER run this):
pace-maker intent-validation off

# Claude makes changes to:
# - src/pacemaker/intent_validator.py
# - src/pacemaker/prompts/pre_tool_use/*.md
# - src/pacemaker/hook.py (pre-tool validation logic)

# Deploy changes
./install.sh

# USER re-enables validation:
pace-maker intent-validation on

# Test that validation works correctly
```

**CRITICAL**: Claude must ASK the user to disable validation. Claude must NEVER disable it directly. See the ABSOLUTE PROHIBITION section at the top of this file.

This applies to:
- Intent validation Python code (`intent_validator.py`, `hook.py`)
- Validation prompt templates (`prompts/pre_tool_use/`)
- Clean code rules and core paths configuration
- Any code that affects the pre-tool validation hook

### Transcript-flush race (TOCTOU) — bug #83

**Symptom**: At PreToolUse fire time, Claude Code has NOT yet flushed the current assistant turn (the INTENT: text + tool_use) to the transcript JSONL. Any transcript-based extractor therefore selects the PREVIOUS turn, causing:
- **False-rejects**: current INTENT missed → Stage 1 RegEx blocks a valid edit
- **False-passes**: stale same-file INTENT from prior turn accepted → Stage 2 validates current code against wrong intent → wrongly APPROVES

**Root cause**: `get_current_turn_message_for_validation()` anchored on the LAST Write/Edit tool_use in transcript (regardless of content), and `extract_current_assistant_message()` picked `messages[-1]` (previous turn when current is unflushed).

**Fix (bug #83, landed in intent validation code)**:

1. **Tool-matched anchor** — `get_current_turn_message_for_validation(transcript_path, tool_input, tool_name)` now locates the transcript tool_use whose `input` EXACTLY matches the current hook's `tool_input` (Write: `file_path`+`content`, Edit: `file_path`+`new_string`, Bash: `command`). Never matches a prior tool_use with different content.

2. **Bounded retry (issue #91)** — if no match found (turn not yet flushed), re-reads the transcript with exponential backoff (`_initial_sleep=0.25s`, `_backoff_multiplier=2.0`, capped at `_max_sleep=2.0s` per sleep: 0.25, 0.5, 1.0, 2.0, 2.0, ...), hard-ceiled at `_max_wait_seconds` of REAL (`time.monotonic()`) elapsed time (Messi Rule 14: provable termination — each iteration either returns or sleeps a strictly positive, ceiling-clamped duration, so elapsed strictly increases every iteration). Replaced the original fixed 21-attempt/0.25s-interval schedule (~5.25s nominal ceiling, itself widened from ≤1s/10 reads in v2.33.2) after live transcript evidence (issue #91) showed busy/large-transcript sessions regularly exceeding that ceiling by 1.4-2s — the per-attempt cost of re-reading a tens-of-MB transcript in full counts against the ceiling too, not just the sleeps, which is why elapsed is tracked via `time.monotonic()` rather than assumed from the sleep schedule. **Correction (superseded by issue #93)** — the gates no longer share one ceiling: **only the Write/Edit gate uses the 30.0s default.** The danger-bash gate deliberately overrides `_max_wait_seconds` to `_DANGER_BASH_MAX_WAIT_SECONDS = 3.0` (`hook.py:64`), because for Bash the current turn is not readable during the hook's window at all, so a long wait is pure latency before an inevitable block. The function defaults are NOT the single source of truth for both gates — see the issue #93 section below. The loop returns the INSTANT a match is found — the ceiling is a MAX wait on the not-yet-flushed path, never a fixed per-edit delay. **Superseded — the ceiling default is now 30.0s (raised from an initial 15.0 attempt), see the "issue #91, second pass" section immediately below for why 15s proved insufficient and what changed.**

3. **Fail-CLOSED on not-ready (v2.33.2)** — if still no match after retries, returns `None` (not `""`). Both the Write/Edit gate AND the danger-bash gate interpret `None` as "transcript-not-ready" and BLOCK (`{"decision": "block", "reason": "..."}`) with a message instructing the agent to re-issue the IDENTICAL tool call — never evaluates the previous turn. **This was changed from fail-OPEN (`{"continue": True}`) to fail-CLOSED** after live observation showed the Write/Edit gate's fail-open branch let raced edits through completely unvalidated (intent validation enforced nothing for any edit that hit this race). The danger-bash gate already proved fail-closed + re-issue works in practice: the agent re-issues the identical command, the re-issue's turn is then flushed, the tool-matched anchor binds to it, and validation proceeds normally on the second attempt (Messi Rule 14: bounded to 2 turns). The `intent_validation_deferred` blockage category and telemetry (activity event + governance event) are still recorded on this path so the race remains observable in `usage.db` / the claude-usage monitor.

   **🚨 SUPERSEDED BY ISSUES #93 AND #139 — neither gate blocks on every `None` any more.** Both gates now block only when the outcome is `not_found`, and both ACCEPT `stale` (Write/Edit since #139 — see "Issue #139" below; the sentence that follows describes the #93-era state where only danger-bash did). At #93 time: the Write/Edit gate still blocked on every `None`, but the **danger-bash gate blocks only when the outcome is `not_found`** — a `stale` outcome is **ACCEPTED**, because `_tool_input_matches` requires the Bash `command` to be byte-identical, so a stale match is provably a re-issue of exactly this command. **Do NOT "restore symmetry" by deleting the `elif _bash_outcome == "stale":` branch at `hook.py:2649`.** That branch is the entire fix for #93; removing it re-creates the deadlock where EVERY Bash command was refused with "transcript not ready" after burning the full ceiling, with no recovery on re-issue.

4. **Defense-in-depth** — `extract_current_assistant_message(messages, file_path=file_path)` cross-checks the selected message via `_mentions_file`. If it carries an INTENT: marker but mentions a different file → discards and returns `""`, preventing false-passes from wrong-file stale turns. Messages without an INTENT: marker are returned as-is (no silent discard — their Stage-1 rejection log is preserved).

**Issue #91, second pass — 15s ceiling proved insufficient**: usage.db showed 172 `intent_validation_dangerbash` race blocks over 14 days, still recurring in clusters of 3-4 on a real 324MB/26,626-line session transcript. Direct measurement identified the root cause: `_find_turn_matching_tool_input()` did a full sequential re-open + re-parse of the ENTIRE transcript from line 1 on EVERY retry attempt — benchmarked at **3.067s/attempt** on that exact transcript. With a 15s ceiling, only ~4-5 attempts fit and nearly the whole budget was consumed by scan cost rather than real waiting for the flush.

A first fix attempt replaced the full scan with a tail-read (last 512KB via `_read_transcript_tail_window()`) that DOUBLED the window (up to the full file size) whenever no anchor match was found — reasoning that the anchor is virtually always near EOF, so growth would only be needed in a rare fallback case. **That attempt was itself measured to make the NOT-FOUND case WORSE than the original full scan**: ~8s/attempt on the real 324MB transcript, because "not found" (the current turn hasn't flushed yet) is exactly the case that exhausts the growing window all the way to full-file-size on every single retry — the fallback path was not rare at all, it was the common case for exactly the race this fix exists to handle. It was also provably unable to help: if the anchor genuinely isn't near EOF, it hasn't been written yet, and no amount of scanning further back changes that.

**Issue #91, v2 simplification — fixed-cost read, no window growth (current design)**: replaced the growing-window tail-read with a SINGLE fixed-size read from EOF (`TAIL_READ_BYTES`, 512KB, never grown) via `_read_tail_raw_entries()`, and restricted the anchor search to the last `LAST_N_TURNS_FOR_TOOL_MATCH` (2) LOGICAL assistant turns found in that window (`_last_n_assistant_turn_keys()` — grouped by requestId, same technique as `get_last_n_messages_for_validation`, not raw JSONL line count). This makes both the found AND not-found cases flat-cost regardless of file size:
- **Found-near-EOF, 30MB synthetic fixture**: ~3.6ms (vs ~181ms for the original full scan, ~4-8s/attempt for the real 324MB transcript under the old designs).
- **Not-found, 30MB synthetic fixture** (the case that was actually broken): ~2.6ms (vs ~560ms measured directly against the pre-v2 growing-window code on the same fixture scale, and ~8s/attempt on the real 324MB incident transcript).
- **30s ceiling retained** — `get_current_turn_message_for_validation()`'s `_max_wait_seconds` default stays 30.0 (raised from 15.0 in the first #91 pass), now genuinely spent on waiting rather than scan cost in either direction.
- **Observability retained** — `_diagnostics: Optional[dict]` kwarg still populated with `attempts`/`elapsed_seconds` on every return path; both pre-tool gates thread this into `record_blockage()`'s `details` dict on the give-up path.
- Malformed/partial-line tolerance (skip-and-continue) is retained in `_read_tail_raw_entries()` since a transcript actively being appended to can still have a genuinely partial trailing line — but there is no window-growth machinery left to reason about.

**Key files**:
- `src/pacemaker/transcript_reader.py` — `_tool_input_matches()`, `_read_tail_raw_entries()`, `_last_n_assistant_turn_keys()`, `_find_turn_matching_tool_input()` (fixed-cost tail read + last-N-logical-turns scoping, `TAIL_READ_BYTES`/`LAST_N_TURNS_FOR_TOOL_MATCH` constants), updated `get_current_turn_message_for_validation()` (default `_max_wait_seconds`=30.0/`_initial_sleep`/`_backoff_multiplier`/`_max_sleep`/`_diagnostics`, issue #91)
- `src/pacemaker/intent_validator.py` — `extract_current_assistant_message(file_path="")` hardening; `validate_intent_and_code` threads `file_path` through
- `src/pacemaker/hook.py` — Write/Edit gate (~lines 2949-2956, `if current_message_override is None:`): threads `tool_input`/`tool_name`/`_diagnostics`, fails CLOSED on `not_found` (v2.33.2), ACCEPTS `stale` after `_WRITE_EDIT_STALE_GRACE_SECONDS` (3.0s) anchor-only (issue #139), 30.0s ceiling for not_found; Danger-bash gate (~line 2590): **DOES override the retry params** — `_max_wait_seconds=_DANGER_BASH_MAX_WAIT_SECONDS` (3.0s, `hook.py:64`) — and additionally consumes `_diagnostics["outcome"]` / `_diagnostics["stale_text"]` to distinguish `stale` (accept) from `not_found` (block). It does **not** share the Write/Edit ceiling (issue #93).
- `src/pacemaker/constants.py` — `BLOCKAGE_CATEGORIES["intent_validation_deferred"]` comment reflects fail-closed (v2.33.2)

**Tests**:
- `tests/test_transcript_staleness_fix.py` — 52 tests: groups 1-6 (21 tests) cover lagged/flushed/stale-same-file scenarios, bounded retry, Bash gate matching, `extract_current_assistant_message` hardening (anchor/matching logic, UNCHANGED across v2.33.2 and both #91 tail-read designs); group 7 `TestHookLevelFailClosed` (2 tests, renamed from `TestHookLevelFailOpen` in v2.33.2) locks in the Write/Edit gate's fail-closed behavior; group 8 `TestDangerBashFailClosed` (2 tests) confirms the danger-bash gate's pre-existing fail-closed behavior is unaffected; group 9 `TestRetryDefaultsWidenedTo30SecondsWithBackoff` (6 tests) covers the 30s hard-ceiling exponential-backoff defaults, early-return-on-match preservation, and a fake-monotonic-clock test proving the backoff schedule and hard ceiling together; `TestReissueStaleMatchBug90` (7 tests) and `TestMultiToolCallTurnBug90V2` (2 tests) cover the bug #90/#90v2 stale-reissue and multi-tool-call-turn scenarios, all passing unchanged under the v2 fixed-cost design; `TestFixedCostTailRead` (5 tests, v2 simplification) proves a near-EOF anchor AND a genuinely-not-found anchor both resolve in well under 100ms regardless of file size (~30MB fixture, covering the case the growing-window design actually broke), documents the deliberate no-growth tradeoff (a match beyond the fixed window is simply not found, unlike the old growing-window fallback), and locks in the staleness-gate append-only invariant; `TestLastNLogicalTurnsScoping` (3 tests, v2 simplification) proves the search is scoped to the last 2 LOGICAL turns (grouped by requestId) — not raw JSONL lines, and not "keep looking until something matches" — with explicit 2-back-found / 3-back-not-found / multi-line-single-turn cases; `TestRetryLoopCeilingDoesNotOvershootOnLargeTranscript` (1 test) proves the full retry loop's real wall-clock cost on a large not-found transcript stays under 1s even though the (faked) ceiling logic ticks through a full 30s budget, directly proving the overshoot-to-33+s failure mode of the first #91 fix attempt cannot recur; `TestDiagnosticsObservability` (3 tests) covers the `_diagnostics` dict being populated on both success and give-up, and its absence (None default) being a no-op.
- `tests/test_intent_validation_failclosed_race.py` (new in v2.33.2) — hook-level Bug A core-regression suite: `None` override → block + deferred telemetry; valid-string override → proceeds to Stage 1/2 (not the not-ready path); empty-string override (turn found, no INTENT) → Stage-1 block (not the not-ready path); subagent transcript (`.../subagents/agent-X.jsonl`) → validated correctly for both no-INTENT (block) and INTENT-present (pass) cases.
- `tests/test_intent_validation_deferred_canary.py` — updated in v2.33.2: asserts `decision: "block"` (was `continue: True`); WARNING log + blockage-event + category-constant assertions unchanged.
- `tests/test_real_transcript_replay.py` + `tests/fixtures/real_transcript_replay/manifest.json` — the `_replay_stage1` fidelity-mirror helper's `None`-branch and the 4 pre-flush fixtures' `expected_stage1` flipped from `"YES"` to `"NO"` in v2.33.2 (per the module's own "update this helper IN THE SAME COMMIT" contract). Unaffected by either #91 tail-read design (all fixtures pass `_max_wait_seconds=0.0` and are well under the fixed 512KB tail window).

### Issue #93 — the danger-bash gate deadlocked on an anchor it then threw away (landed v2.34.4)

> **Superseded in part by issue #94 (v2.34.5).** The Phase 2 verdict check described below was, at the time of #93, still strict `response.strip().upper() == "APPROVED"`. It now uses `verdict_passes()`, and the Phase 2 prompt requires rejections to begin with `BLOCKED:`. See "Canonical Verdict-Normalization Primitive → Gate convergence" for the current behaviour. Nothing else in this section changed.

**Symptom**: EVERY Bash command reaching the danger gate was refused with "transcript not ready", after burning the full 30s ceiling, with no recovery on re-issue. **~116** such blocks in `usage.db` (a re-query today counts 115 — the figure is an approximate snapshot, not an exact reproducible count). Write/Edit was unaffected and worked in the same session minutes apart.

**Root cause (two defects compounding)**:
1. The gate called `get_current_turn_message_for_validation()` into `_bash_anchor` and **discarded the value** — its only use was `if _bash_anchor is None:` → block. The INTENT it actually validated came from a SEPARATE unanchored `get_last_n_messages_for_validation(n=4)` call. So the 30s wait and the fail-closed refusal gated a value that was then thrown away.
2. For Bash, the current turn is not readable from the transcript during the hook's window at all. The wait could never succeed, so the ceiling was pure latency before an inevitable block.

**The fix — outcome disambiguation**: `_find_turn_matching_tool_input()` previously returned `None` for BOTH "no match found" and "match found but STALE". These are now distinguished via an optional `_outcome` dict (`"found"` / `"stale"` / `"not_found"`, plus `_outcome["stale_text"]`). The `None`/`""`/`str` **return contract is deliberately unchanged** so the Write/Edit gate is provably unaffected.

**Danger-bash gate behaviour**:
- `found` → use the anchor's own text as the Phase 1/Phase 2 message.
- `stale` → **ACCEPT it.** `_tool_input_matches` requires the Bash `command` to be byte-identical, so a stale match is a re-issue of exactly this command. **Precise claim**: the accepted INTENT always comes from a turn that itself issued this exact command — it does NOT prove the turn's merged TEXT is about that specific tool_use, because `_merge_anchor_turn` merges text across the whole requestId group (a multi-tool turn's INTENT may describe a sibling call). Final intent-to-command alignment is Phase 2's job. Do not restate this as a stronger guarantee.
- `not_found` → block fast via `_DANGER_BASH_MAX_WAIT_SECONDS = 3.0` (Write/Edit keeps the 30s default), recording `outcome` in `blockage_events.details`.

**CRITICAL — `stale_text` MUST stay gated on the turn's TEXT.** A first-pass implementation set `stale_text = _format_message_with_tools(merged)` with no INTENT-marker gate, while the found path gates on `merged["text"]`. Since `_format_message_with_tools` renders `content`/`old_string`/`new_string` and `_has_intent_marker` is a bare regex, an `INTENT:` **inside a written file's content** satisfied Phase 1. Caught in review, proven end-to-end, fixed. If you ever touch the stale path, keep it symmetric with the found path.

**Termination is NOT a hard round bound.** A round-trip resolves only while the blocked attempt stays inside the last `LAST_N_TURNS_FOR_TOOL_MATCH` (2) logical turns. Measured: 0-1 intervening assistant turns → `stale` → recovers; **2+ → `not_found` → the cycle repeats**. The per-attempt wait is bounded (~3s); the number of rounds is not. Mitigated by the block message telling the agent to re-issue as its VERY NEXT tool call. **Do NOT raise `LAST_N_TURNS_FOR_TOOL_MATCH`** to "fix" this — it is bug #90's staleness scope and widening it re-opens stale-anchor acceptance globally.

**Deliberate gate asymmetry (Bash vs Write/Edit)**: Bash is now **anchor-only** — the unanchored `n=4` backward search was removed (locked by `TestFoundUsesAnchorNotUnanchoredNBack`). Write/Edit still uses n-back, which exists to rescue an INTENT declared in the immediately preceding assistant message when a turn fragments across requestIds. **Bash no longer has that rescue**: an INTENT in a prior requestId now yields a Phase-1 block. This is an intentional security tightening (the old n=4 could accept an INTENT from any of the last four turns for a command it never named), not an oversight.

**Shared regex**: `INTENT_MARKER_PATTERN` in `transcript_reader.py` is now the single definition, imported by `intent_validator._has_intent_marker`. It had been copy-pasted at 4 sites — and the security defect above existed precisely because one copy-site lacked the check the others had. Keep it single-sourced.

**Live verification (2026-08-09, intent validation ENABLED)**: attempt 1 → block at `5 attempts / 3.005s` with `outcome: not_found` (was `19 / 30.004s`, outcome blank); byte-identical re-issue as the very next tool call → **executed**; deliberately mismatched INTENT → still **blocked** by Phase 2. The successful re-issue correctly recorded no blockage row.

**Known cosmetic defect (not fixed)**: the retry loop logs `WARNING ... gave up after N attempt(s)` even on the SUCCESSFUL stale path, because `_find_turn_matching_tool_input` returns `None` for stale and the loop exhausts its ceiling before the caller reads `_outcome`. A "gave up" warning in the log does NOT imply the gate blocked — check `blockage_events` for a matching row before concluding it did.

**Key files**: `src/pacemaker/transcript_reader.py` (`_outcome` threading, `_merge_anchor_turn`, `INTENT_MARKER_PATTERN`), `src/pacemaker/hook.py` (danger-bash gate ~2600-2700, `_DANGER_BASH_MAX_WAIT_SECONDS`), `src/pacemaker/intent_validator.py` (imports the shared pattern), `tests/test_issue_93_danger_bash_anchor.py` (22 tests).

### Issue #139 — the same deadlock, in the Write/Edit gate

**Symptom**: 5 back-to-back `intent_validation_deferred` blocks, each taking the full 30s (`attempts: 19`), on a one-line Edit in code-indexer-master (Claude Code 2.1.278). The agent never recovered. Across that session, 55 Write/Edit calls passed and 8 were deferred.

**Root cause (proven by replaying the real transcript)**:
- The matcher was correct: every attempt resolved to `found` once its line was written to disk.
- Sometimes Claude Code writes nothing to the transcript during the whole PreToolUse window, which is the Bash finding from #93 now seen for Write/Edit. The first attempt is then `not_found`.
- The identical re-issue that the block message asks for resolves to `stale`, because the earlier attempt is one turn back.
- The Write/Edit gate blocked on every `None`, so the recovery path could never succeed. #93 had fixed this only for danger-bash.

**Fix (Write/Edit gate, `hook.py` stale branch)**:
- **`stale` is accepted.** `stale_text` becomes `current_message_override` and the edit goes through normal Stage 1 and Stage 2.
- **`not_found` still blocks** fail-closed. `outcome` is now recorded in the blockage details.
- **An empty `stale_text` never falls back to the older messages.** It sets `messages = []`, so Stage 1 blocks.
  - Without this, `validate_intent_and_code`'s `override or extract_current_assistant_message(messages)` would read the n-back messages.
  - Those messages include rendered tool parameters, so an `INTENT:` inside written file content, or an INTENT from `messages[-2]`, would pass. Review proved this end-to-end.
  - On the stale path both gates are now anchor-only.
- **The Edit match is byte-exact.** `_tool_input_matches` now compares `file_path`, `old_string`, `new_string` and `bool(replace_all)`.
  - Before, it compared only `file_path` and `new_string`. A deletion (`new_string=""`) of `critical_auth()` could then reuse an earlier "delete `foo()`" INTENT.
  - Stage 2 cannot catch that, because for Edit it sees only `new_string`.
  - **Do not loosen this match.**
- **Stale grace period.** The new opt-in `_stale_grace_seconds` in `get_current_turn_message_for_validation` defaults to `None`, which keeps the old behaviour. The Write/Edit gate passes `_WRITE_EDIT_STALE_GRACE_SECONDS = 3.0`.
  - Once the outcome is `stale` and the grace period has passed, the retry loop returns early rather than running to the full 30s. A `found` before then still wins.
  - The `not_found` ceiling is unchanged.
- **Logging.** A stale acceptance is logged with `log_info` and `outcome=stale_accepted`.

**Known edge case (accepted)**: if the original attempt had no INTENT and the re-issue adds one, the re-issue can hit a Stage-1 "missing INTENT" block. The reason text is misleading, and the next re-issue recovers. The verdict is never more permissive than waiting would have been.

**Fixed by #140 (v2.35.x, code-review-corrected).** The gap this section originally flagged — "on the found path, an empty anchor text still falls back to n-back, which renders tool parameters, so INTENT inside file content can satisfy Stage 1 there" — is closed. The FIRST attempt at this fix added `strip_rendered_tool_params()`, a naive `"[TOOL: "` string-splitter. Code review found three problems with it, all fixed by the design actually shipped:

1. **TDD-declaration/version-bump scanning tool content.** `_regex_stage1_check` sliced `current_message[intent_match.end():]` for `_has_tdd_declaration`/`_is_version_bump` — that slice still included rendered tool params after a marker-stripped MATCH position, so a real prose INTENT followed by a Write whose `content` happened to contain `"# test: foo"` or `"version = 2"` wrongly satisfied Stage 1.
2. **`_mentions_file()` trivially satisfied.** `_format_message_with_tools` always renders the tool's own `file_path: <target>` field — the SAME file being validated, by construction — so a real INTENT one turn back that named a DIFFERENT (wrong) file still passed the file-mention check.
3. **False-positive truncation.** Splitting on the literal substring `"[TOOL: "` is unsafe against prose that legitimately QUOTES that text (e.g. "the log showed `` `[TOOL: Bash]` ``." followed by a real INTENT) — the quoted text was found first and the real INTENT after it was truncated away, producing a NEW false block that didn't exist before any #140 fix.

**The shipped fix carries prose as STRUCTURED data, never by string-splitting a rendered blob:**
- `transcript_reader.get_last_n_messages_for_validation` gained a keyword-only `_with_prose: bool = False` param — when True, it returns a `(rendered, prose)` TUPLE computed from a SINGLE parse/grouping pass; `prose` is every message (including the most recent) as `msg["text"]` only, never rendered with tool parameters. Default `False` preserves the exact prior single-list contract for every other caller (Stage 2's prompt, and the many tests depending on the rendered form).
- `_find_turn_matching_tool_input` additionally populates `_outcome["anchor_prose_text"] = merged["text"]` on both the found and stale branches; `_copy_anchor_shape_flags` copies it too.
- `intent_validator.validate_intent_and_code` gained an optional `stage1_fallback_messages` param: Stage 1's n-back fallback searches it (prose-only) instead of `messages` (still full-rendered, for Stage 2) when supplied.
- `hook.py`'s Write/Edit gate makes a SINGLE `get_last_n_messages_for_validation(..., _with_prose=True)` call, unpacking both lists from one parse, and substitutes `anchor_prose_text` for `current_message_override` whenever it's truthy (found-with-intent OR accepted-stale) — fixing the ANCHOR-FOUND case, not just the n-back fallback.
- `extract_current_assistant_message`/`_regex_stage1_check` were REVERTED to plain `_has_intent_marker(text)` checks — no string-splitting anywhere; the guarantee that Stage 1 never sees rendered tool content is now structural (enforced by the callers), not string-heuristic.
- `strip_rendered_tool_params()`/`TOOL_RENDER_MARKER` were REMOVED once they had zero callers (Messi Rule 12).

**Re-review findings (both fixed in the same story):**
- **Finding 1 (MEDIUM)**: `anchor_prose_text = merged["text"]` was originally the LAST statement inside the try/except wrapping `_turn_has_thinking()` — a raise there left the key entirely UNSET in the per-attempt `_outcome`. `_copy_anchor_shape_flags` then copies it into the caller-visible `_diagnostics` via a bare `.get("anchor_prose_text")` (no default), which explicitly sets `diagnostics["anchor_prose_text"] = None` — so the key becomes PRESENT with value `None`. hook.py's original `.get("anchor_prose_text", current_message_override)` is a no-op default fallback only when the key is ABSENT; with the key present-but-`None`, it returned `None` directly — a false "transcript not ready" block on the found path, or an incorrect n-back fallback on the stale path (violating the ANCHOR-ONLY invariant, #93/#139). Fixed by moving `_outcome["anchor_prose_text"] = merged["text"]` to a plain dict/string read BEFORE the try (found and stale branches both), and by changing hook.py's two substitution sites to `isinstance(_p, str) and _p` instead of `dict.get(key, default)`. Regression-locked directly at the transcript_reader level by extending the pre-existing `tests/test_issue_141_thinking_only_notice.py::TestAnchorShapeExceptionLogsWarning` tests (which already fault-inject `_turn_has_thinking`) with an `anchor_prose_text` assertion; hook.py's own guard is separately covered in `tests/test_issue_140_stage1_prose_only_intent.py`'s `TestProbeLHookGuardFallsBackWhenAnchorProseTextIsNone`, which mocks only hook.py's declared `get_current_turn_message_for_validation` dependency (never a transcript_reader internal) against a deliberately EMPTY transcript, so a broken guard is observable as a block, not just an unasserted pass.
- **Finding 2 (LOW, performance)**: the first re-review-era design used a separate `_prose_only` boolean requiring hook.py to call `get_last_n_messages_for_validation` TWICE per Write/Edit — each independently re-reading and re-parsing the WHOLE transcript (~0.86s measured on a real 99.7MB file, ~2.8s extrapolated to 324MB), eating the pre-tool gate's anchor budget for no benefit. Fixed by the `_with_prose=True` single-pass tuple design above — one parse, one call.

Danger-bash is unaffected (it never calls these n-back helpers at all; anchor-only per #93/#139). See `tests/test_issue_140_stage1_prose_only_intent.py` (probes B/C/D/E mirror the code review's own numbered findings, as real synthetic-transcript hook-level tests). `tests/test_real_transcript_replay.py`'s `_replay_stage1` fidelity-mirror helper was updated in the same commit per its own contract, twice (once per review round). One real-corpus fixture (`case_26_no_intent.jsonl`, category `no_decl_marker_from_tool_content`) flips `NO_TDD` → `YES`: its old verdict came from a fake `INTENT:` match inside the CURRENT turn's own rendered Edit content, which skipped the 1-back merge entirely; the fix correctly rescues a REAL, on-topic INTENT+Test-coverage declaration one turn back instead — the reviewer independently verified `YES` is correct for this fixture — see the manifest's note for the full trace.

**Tests**: `tests/test_issue_139_write_edit_stale_accept.py`, `tests/test_issue_139_code_review_followup.py`, `tests/test_issue_140_stage1_prose_only_intent.py`, `tests/test_issue_141_thinking_only_notice.py::TestAnchorShapeExceptionLogsWarning` (extended).

---

### Issue #141 / #148 — no-visible-text block notice

**Superseded by issue #150 — read that section for the CURRENT behavior.** This section is left in place for its historical detection rationale (the three transcript shapes, the `anchor_has_visible_text`/`anchor_has_thinking` flags), which is UNCHANGED. But `THINKING_ONLY_NOTICE` no longer exists (removed, zero callers, Messi Rule 12) and nothing is "appended" any more — issue #150 replaced it with `intent_validator.build_no_visible_text_notice()`, which LEADS the block reason instead. Every sentence below that describes `THINKING_ONLY_NOTICE` being appended, or "still imported... at 4 sites", describes the PRE-#150 state only.

When an anchored Write/Edit or danger-bash Bash turn has NO visible text block at all, the Stage-1 "missing declaration" block reason gets `transcript_reader.THINKING_ONLY_NOTICE` appended, explaining that a declaration written only in reasoning (or omitted entirely) doesn't count — otherwise the agent believes it already declared INTENT and loops. At the time this was written, thinking was NEVER an INTENT source for any model; **issue #151 removed that rule** for a configurable list of models (default: Opus 5.5) — see the "Issue #151" section below. For every model NOT in that list, the statement below is still exactly true; the notice's role (explaining an existing block, never changing whether one occurs) is otherwise unchanged.

**#141 (original)** gated the notice on `anchor_has_thinking` being true AND `anchor_has_visible_text` being false — i.e. only the "wrote INTENT in reasoning" shape.

**#148 (this fix)** broadens the gate to fire whenever `anchor_has_visible_text is False`, full stop, regardless of `anchor_has_thinking`. Three real shapes exist (all gap-free by `apiBlockIndex`): (a) non-empty `thinking` + `tool_use`, (b) an EMPTY `thinking: ""` + `tool_use`, (c) `tool_use` alone with no `thinking` block at all. #141 only covered (a); (b) and (c) got no notice at all. `transcript_reader._turn_has_thinking`/`_content_has_thinking` were already correct for all three shapes (empty thinking → False) — the bug was purely in the DOWNSTREAM gates (`hook.py`) requiring thinking to be truthy too.

**Renamed for honesty**: `thinking_only` → `no_visible_text` throughout — `hook.py`'s `_bash_thinking_only`/`_write_edit_thinking_only` locals, `intent_validator.validate_intent_and_code`'s `thinking_only` parameter, and the `blockage_events.details` `"thinking_only"` key. No external consumer (claude-usage-reporting) reads this key (verified by grep), so no backward-compat shim was added. `THINKING_ONLY_NOTICE`'s constant NAME is unchanged (still imported from `transcript_reader` at 4 sites); only its wording changed to cover all three shapes ("declarations ... must be written as visible response text, in the same message before the tool call, not only in reasoning/thinking (if any) and not omitted entirely").

**Mixed-key telemetry, by design**: the rename is a code-level change only, not a data migration. `blockage_events` rows recorded BEFORE this fix landed (roughly 519 rows, per a live `usage.db` sample) carry the OLD `"thinking_only"` key in their `details` JSON; every row recorded AFTER carries `"no_visible_text"`. Any consumer reading `blockage_events.details` for this signal (there is none today besides ad-hoc SQL) must check BOTH keys to cover the full history.

**Tests**: `tests/test_issue_141_thinking_only_notice.py` (extended in place — file name kept since it's still #141's home, with #148's classes named `TestIssue148*`).

---

### Issue #150 — pilot-validated wording (lead-with-the-fix, not append-at-the-end)

**#141/#148 detected the right thing but didn't fix Opus 5.5's behavior.** Volume kept climbing after #148 shipped (23→39→127→202→158 `no_visible_text` blocks/day over 5 days in late Sept 2026; one session alone hit 389 blocks across 4 Opus subagents), including agents that gave up and routed around the gate entirely by writing files via Bash. Root cause was UX, not detection: `THINKING_ONLY_NOTICE` was appended at the END of a long generic template the model had already stopped reading, and the only proactive instruction lived far away at SessionStart/SubagentStart.

**The trigger is xhigh reasoning effort, not the model per se.** A headless pilot (`claude -p --model claude-opus-5-5`, 14-step task, ~16 Write/Edit calls, intent validation OFF so measurement is transcript-based: does a visible `INTENT:` text block precede each Write/Edit in the same requestId group) isolated this by varying ONLY reasoning effort and delivery:

| Effort | Delivery | Sessions | Visible INTENT | Turns with thinking |
|---|---|---|---|---|
| medium | none (control) | 2 | 0/33 | ~7% |
| medium | current pace-maker guidance (verbatim) | 2 | 34/34 | ~7% |
| high | current guidance | 2 | 33/33 | ~8% |
| **xhigh** | **current guidance** | 2 | **3/31** | **~48%** |
| xhigh | new "C text" alone at SessionStart | 6 | 97/101 | ~12% |
| xhigh | C text FIRST + current guidance | 4 | 58/64 | ~18% |
| xhigh | C text + PostToolUse reminder after every tool | 2 | 24/32 | ~24% (WORSE than C-alone) |
| xhigh | C text in the task prompt itself | 2 | 26/34 | ~23% |

At medium/high effort the pre-existing guidance already worked (33-34/33-34). At xhigh it collapsed to 3/31 — thinking-block usage roughly doubles (48% vs ~7-8%), consistent with production evidence: session `41d5247d` showed Opus 5.5 subagents failing from their FIRST edit (not degrading over time) with thinking present on 53% of turns, while Sonnet 5 subagents in the SAME session complied ~80%.

**Correction — "C text first + guidance" did NOT beat "C text alone".** An earlier revision of this section claimed the shipped layout scored higher than C-alone. It did not: by raw score, "C text alone" (97/101, 96%) is slightly AHEAD of "C text first + current guidance" (58/64, 91%) — the gap is within run-to-run noise given the small sample sizes (4-6 sessions per variant), not a clear win for either layout. The shipped layout (C text first, THEN the full pre-existing guidance) was chosen to PRESERVE the rest of the guidance's content — TDD enforcement, the "Senior Coding Nanny" naming convention, the per-Write/Edit-declaration rules, etc. — not because it measurably outperformed C-alone. A session that received C-alone would lose all of that other guidance, which the pilot did not attempt to evaluate independently.

**Verbatim-block rule (do not reword either block — pilot-tested wording only)**:
- SessionStart/SubagentStart guidance (`src/pacemaker/prompts/session_start/intent_validation_guidance.md`, loaded via `hook.py::display_intent_validation_guidance()`, the SAME function both hooks call) now OPENS with the "C text" block, character-exact, as its own paragraph, immediately after the `[pace-maker · intent_validation_guidance]` provenance header and before every other line of guidance (the pre-existing "VISIBLE TEXT ONLY" paragraph etc. is unchanged, just pushed down).
- The no-visible-text block message (Write/Edit Stage 1 in `intent_validator.validate_intent_and_code`'s NO/NO_TDD branches; danger-bash Phase 1 in `hook.py`) now LEADS the block reason (right after the provenance tag) with the same core wording plus a ready-to-copy `INTENT:` example naming the ACTUAL target — file_path for Write/Edit (plus a `Test coverage: <test file> - <test name>` line when file_path is a core path — code-review follow-up, item 3, `intent_validator._write_edit_no_visible_text_example()` — so a core-path edit with no visible text doesn't get blocked twice, once for the missing INTENT and again for the missing test declaration), a single-line, whitespace-collapsed, 60-char-capped command preview for Bash (`hook.py::_build_bash_command_preview()`, item 2 — takes the FIRST LINE only so a heredoc/multi-line command can't break the rendered example across lines) — instead of appending a generic notice at the end. Built by `intent_validator.build_no_visible_text_notice(intent_example)`, which loads `src/pacemaker/prompts/common/no_visible_text_notice.md` (Messi Rule 11) as STATIC text (no `{{...}}` template substitution — item 1, see below) and appends `intent_example` in code afterward. The old generic template still follows afterwards, unchanged. When `no_visible_text` is `False`, the message is byte-identical to before this fix.
- **Item 1 fix — crash on a literal `{{word}}` in the actual file_path/command.** The first shipped version passed `intent_example` through `PromptLoader.load_prompt(..., variables={"intent_example": intent_example})`, which substitutes the placeholder and then RESCANS THE SUBSTITUTED RESULT for `\{\{(\w+)\}\}` and raises `ValueError` if any remain. A file_path or Bash command that itself legitimately contains a `{{...}}`-shaped substring (a templated path, or a `sed`/`awk` script's own placeholder marker) re-triggered that scan on ITS OWN text and crashed with "Unreplaced placeholders" — which on the Write/Edit gate surfaced as an "Intent Validation System Error", and on the danger-bash gate fell into `_fail_closed_message()`, which tells the agent to ask the user to disable intent validation entirely (the worst possible failure mode for this exact fix). Fixed by removing the `.md` file's own `{{intent_example}}` placeholder (the template now ends at "for example:") and appending `intent_example` in code, AFTER the static template is loaded — nothing ever passes through PromptLoader's rescan.
- **Unpiloted wording, by necessity.** The pilot table above measured ONLY the SessionStart/SubagentStart guidance's C text. The no-visible-text block-notice wording rendered by `build_no_visible_text_notice()` reuses the same core sentence deliberately (for consistency, and to avoid inventing new unvetted phrasing) but was NOT itself run through the pilot — there was no gate to observe it against (intent validation was disabled for the whole story). Its live effect on recovery is unproven until the issue's own step 4 (re-run the xhigh pilot against the real gate once the user re-enables intent validation).
- `transcript_reader.THINKING_ONLY_NOTICE` (issue #141/#148's constant, appended at the end) was REMOVED once it had zero remaining callers (Messi Rule 12) — both call sites (Write/Edit NO/NO_TDD branches, danger-bash Phase 1) now call `build_no_visible_text_notice()` instead. The gating booleans (`anchor_has_visible_text`/`anchor_has_thinking`, `_bash_no_visible_text`/`_write_edit_no_visible_text`, the `no_visible_text` parameter, the `blockage_events.details` `"no_visible_text"` key) are UNCHANGED by this — only the block TEXT and its POSITION moved.

**Story #155 update (v2.37.0) — the guidance now OPENS with the declare_intent paragraph, then the C text.** With `intent_declaration_tool_enabled` (default on), `display_intent_validation_guidance(config)` prepends `prompts/session_start/declare_intent_guidance.md` ("Before each Write or Edit, call the `declare_intent` tool first …") and the C text follows immediately, still character-exact and unreworded. The "guidance body starts with the exact C text" lock in `tests/test_issue_150_pilot_validated_wording.py` was therefore updated deliberately (it now asserts that opener with the kill switch OFF, plus a new test that the C text follows the tool paragraph by default) — see "Story #155" below. The block-message "no visible text" notice still LEADS its block reason: the #155 `declare_intent` hint is inserted after it.

**Safeguard hazard — do NOT reword the C text.** A differently-worded variant — "Before every Edit/Write, write the INTENT: line as a visible sentence right before the tool call." — was refused by the Claude API in 2/2 pilot subagents with `[reasoning_extraction]` (the API's own safeguard against instructions that read as asking the model to reveal/copy its internal reasoning verbatim). The shipped C text caused ZERO refusals across 20 pilot sessions. Any future edit to either the guidance opener or the no-visible-text notice must avoid phrasing that asks the model to "write out", "reveal", or "copy" its reasoning/thinking — describe the REQUIREMENT (visible text before the tool call) without describing it as an act of exposing reasoning.

**Why there's no PostToolUse reminder.** It was in the pilot matrix ("C text + PostToolUse reminder after every tool") and measured WORSE than C-text-alone at xhigh: 24/32 visible INTENT and ~24% thinking-block usage, vs 97/101 and ~12% for C-text-alone. A reminder fired after every tool call apparently reinforces the exact pattern it's trying to interrupt at high reasoning effort. This story deliberately does NOT add one — the exclusion comes from the GitHub issue's own "Fix (decided from the pilot)" **item 3** ("NOT doing: the PostToolUse reminder. It made results worse at xhigh."), not from any "Explicitly out of scope" list (an earlier revision of this section misattributed it there; the issue's actual "Out of scope" note is a separate, unrelated item about agents bypassing the Write/Edit gate via Bash). Do not re-add a PostToolUse nudge for this failure mode without new pilot evidence.

**Verification is a separate, later step (not done by this story).** Intent validation was disabled by the user during this fix (see the ABSOLUTE PROHIBITION at the top of this file — Claude never re-enables it). The issue's own item 4: after the user re-enables it, rerun the xhigh pilot against the REAL gate (not the transcript-only measurement above), including a subagent, and measure block rate and recovery.

**Key files**: `src/pacemaker/prompts/session_start/intent_validation_guidance.md` (opens with the C text), `src/pacemaker/prompts/common/no_visible_text_notice.md` (Messi Rule 11 — static text ending at "for example:", no template placeholder — see item 1 above), `src/pacemaker/intent_validator.py` (`build_no_visible_text_notice()`, `_write_edit_no_visible_text_example()` — Test coverage: line on core paths, `_is_core` computed once and threaded into both the NO and NO_TDD branches), `src/pacemaker/hook.py` (`_build_bash_command_preview()`, danger-bash Phase 1 wiring, ~line 3005), `src/pacemaker/transcript_reader.py` (`THINKING_ONLY_NOTICE` removed).

**Tests**: `tests/test_issue_150_pilot_validated_wording.py` (character-exact guidance-opening assertion via both `display_intent_validation_guidance()` directly and a real `run_subagent_start_hook()` drive; Write/Edit/Bash example-naming-actual-target assertions; no_visible_text=False byte-identical-to-before assertion; refused-wording regression guard). `tests/test_issue_141_thinking_only_notice.py` updated in the same commit (its own contract, like `test_real_transcript_replay.py`'s) — `THINKING_ONLY_NOTICE` imports/assertions replaced with a local `NO_VISIBLE_TEXT_LEAD` substring constant (the lead sentence shared by every rendered example), same presence/absence semantics preserved.

---

### Issue #151 — the Opus 5.5 reasoning-summary intent exception

**#150 made the block message actionable; it did not make compliance possible.** #150's own pilot found the trigger is xhigh reasoning effort, not the model per se — but production evidence (session `41d5247d`) showed Opus 5.5 subagents at that effort routinely emit Write/Edit/Bash turns shaped `thinking("", signature-only), thinking(<summary>), tool_use`, with NO visible text block at all, and some agents gave up and routed around the gate entirely via Bash scripts. Across 638 such turns, 83% have a readable reasoning summary (p50 239 chars; 51% name the target file, 50% mention tests) — the model IS stating a real intent, just never in a channel Stage 1 could read. **User decision (2026-09-27): remove the "reasoning/thinking is never an INTENT source" rule and add an end-to-end exception mechanism, scoped to a configurable model list.**

**This section describes the CODE-REVIEWED design.** The first pass had defects (malformed config crashing every model, an already-compliant Opus turn getting relaxed instead of strict enforcement, hard-blocking instead of falling through to the n-back rescue, a self-contradictory danger-bash prompt) — all fixed below. Do not re-derive the first-pass design from old commit messages; read this section.

**Config gate — `reasoning_summary_intent_models`, defensively normalized.** New `DEFAULT_CONFIG` key (`src/pacemaker/constants.py`), default `["claude-opus-5-5"]`. The exception applies ONLY when the anchored turn's assistant `message.model` is a member of this list, AFTER `hook._normalize_reasoning_summary_intent_models(raw)` runs on it. **Never trust the raw config value's shape** — a hand-edited config.json can hold `null`/`false` (a bare `model in cfg_value` raises `TypeError` on a non-iterable, which the outer exception handler turns into a fail-closed error for EVERY model, telling the agent to ask the user to disable intent validation — the worst failure mode for a malformed value) or a bare STRING (Python's `in` does SUBSTRING matching on a string, so `"claude-opus-5-5" in "claude-opus-5-5-and-more"` is `True` with no list involved at all). The normalizer returns `[]` (exception disabled for every model) for anything that isn't a list, and silently drops non-string list entries — both paths logged at WARNING. An empty list (the normalizer's own safe default, or an explicit `[]`) restores strict (pre-#151) behavior for every model.

**`transcript_reader.py` — two new additive `_outcome`/`_diagnostics` fields, same idiom as `anchor_prose_text`/`anchor_has_thinking` (#140/#141):**
- `anchor_model` — `raw_entries[anchor_index].get("model")` (the assistant record's own `message.model`, e.g. `"claude-opus-5-5"`). Added to `_read_tail_raw_entries`'s per-entry dict (`"model": message.get("model") if role == "assistant" else None`) and read as a plain, zero-exception-risk dict access — placed BEFORE the try/except that wraps `_turn_has_thinking()`, exactly like `anchor_prose_text`, so a raise inside that try can never leave it unset.
- `anchor_reasoning_summary` — the anchor turn's non-empty `thinking` block texts, concatenated in block order with a blank-line separator (`_extract_thinking_texts()` collects them, `_merge_anchor_reasoning_summary()` scopes the merge to the SAME requestId grouping `_merge_anchor_turn`/`_turn_has_thinking` already use — a reasoning summary from a different turn can never leak in). Computed INSIDE the `_turn_has_thinking` try/except (same fail-safe wrapping). Populated on BOTH the "found" and "stale" branches, mirroring every other anchor-shape flag. `_copy_anchor_shape_flags()` copies both fields into the caller-visible `_diagnostics` dict with plain `.get()`.
- Both fields are populated UNCONDITIONALLY for every model, not just exception-listed ones — `transcript_reader` is a pure, unconditional extractor; the model-list GATING happens downstream in `hook.py`, never here.

**The shared decision function — `intent_validator.resolve_reasoning_summary_intent_source(reasoning_summary_intent, anchor_visible_text, anchor_reasoning_summary)`.** Both gates (Write/Edit and danger-bash) call this ONE function to decide STRICT vs RELAXED — the fix for the code review's H2/M1 findings:

| Input | Returns `(intent_source, relaxed_text)` | Path taken |
|---|---|---|
| `reasoning_summary_intent=False` (non-exception model) | `(None, None)` | STRICT, untagged (byte-identical) |
| Anchor's own **visible** text already matches `INTENT_MARKER_PATTERN` | `("declaration", None)` | STRICT, tagged `"declaration"` |
| No visible marker, reasoning summary non-empty | `("reasoning_summary", <combined text>)` | RELAXED |
| No visible marker, reasoning summary empty, visible text non-empty | `("visible_text", <visible text>)` | RELAXED |
| No visible marker, BOTH visible text and summary empty (M1) | `("none", None)` | STRICT, tagged `"none"` |

**H2 — an already-compliant Opus turn is never relaxed.** The marker check is scoped to the anchor's own VISIBLE text ONLY — a reasoning summary that happens to contain the substring `"INTENT:"` must never short-circuit into the declaration path, since the summary is not a formal declaration channel and #93's own security lesson (never let rendered/incidental text satisfy a marker check) applies here too. When the anchor's visible text already carries a real `INTENT:` declaration, the turn takes the IDENTICAL strict pipeline a Sonnet turn would (TDD enforcement included), just tagged `"declaration"` for telemetry.

**M1 — an empty-intent exception-model turn is never hard-blocked; it falls through to the SAME strict path.** The first pass's `_validate_reasoning_summary_path()` short-circuited on empty `intent_text` with its own bespoke block message, which meant an Opus turn with signature-only thinking on THIS turn but a real `INTENT:` on the immediately preceding turn (a fragmented multi-turn edit) could never reach the prose-only n-back rescue (#140) that a Sonnet turn in the identical shape gets — "today this blocks on Opus but passes on Sonnet" was the literal repro. Fixed: the resolver returns `("none", None)` for this case, and the caller (`validate_intent_and_code`) falls through to the EXACT SAME `_validate_normal_path()` a non-exception model uses, including its n-back rescue. `_validate_reasoning_summary_path()` is now ONLY ever called with a proven-non-empty `intent_text` — its old empty-check block was DELETED (Messi Rule 12, dead code) since it became unreachable. **Re-review follow-up: this case is tagged `"none"`, NOT `"declaration"`** — there is no declaration when the anchor has neither visible text nor a summary, and tagging it `"declaration"` would contradict a `no_visible_text=True` blockage row and silently pad any count of "compliant Opus turns". The key is still populated (never omitted), so these turns stay individually countable. If the n-back rescue subsequently succeeds, the result stays tagged `"none"` — the resolver cannot know in advance whether the rescue will succeed, and retroactively upgrading the tag is out of scope.

**`intent_validator.py`'s internal structure (issue #151 refactor):**
- `_validate_normal_path(messages, code, file_path, tool_name, hook_model, current_message_override, _deadline, no_visible_text, stage1_fallback_messages, exclusions, core_path_segments, extensions)` — the pre-#151 Stage 1/2 pipeline, extracted VERBATIM (same lines, same order) out of `validate_intent_and_code()` so it can be shared by non-exception-model calls AND by the two STRICT cases above. No behavior change — this is provably the same code executing for a non-exception model.
- `validate_intent_and_code()`'s new params: `reasoning_summary_relaxed_text: Optional[str] = None` (non-`None` means "take the relaxed path with THIS intent text" — set by the resolver, never computed inside `validate_intent_and_code` itself) and `reasoning_summary_intent_source: Optional[str] = None` (tags the result's `"intent_source"` key whenever the model is in the exception list, regardless of which path was taken). Defaults preserve exact pre-#151 behavior for every caller that omits them.
- `_validate_reasoning_summary_path(intent_text, intent_source, code, file_path, hook_model, _deadline, exclusions, core_path_segments, extensions)` — the RELAXED path only (no `tool_name` param — it was unused, dropped per code review L3). No `INTENT:`/TDD/version-bump regex at all, since `intent_text` is guaranteed non-empty by the caller.

**Stage 2 (Write/Edit), RELAXED path only — dedicated externalized template (Messi Rule 11), verbatim-locked against drift:**
- `src/pacemaker/prompts/pre_tool_use/stage2_code_review_reasoning_summary.md` — same four-check structure as the normal Stage 2 prompt, framing `intent_text` as "an auto-summarized excerpt... not a formal declaration" (CHECK 0's specificity requirement still applies — a vague excerpt is still rejected).
- **M3 — CHECK 1 / CHECK 3 / CLASSIFICATION VALUES are VERBATIM identical to `stage2_code_review.md`'s**, not independently reworded copies — the #94 lesson (two independently maintained copies of the same judgement criteria drift) applies here just as much as it did to the danger-bash Phase 1/2 divergence. Locked by `TestM3TemplateSectionsVerbatim` in the test file, which extracts and diffs those three sections between both templates. If you edit CHECK 1/3/CLASSIFICATION VALUES in ONE template, you MUST edit the other identically, or that test fails.
- `src/pacemaker/prompts/pre_tool_use/reasoning_summary_core_path_note.md` — a standing "CHECK 1B" section, injected into `{core_path_note}` ONLY when `_is_core_path(file_path, ...)` is True (computed LAZILY, per branch — inside `_validate_reasoning_summary_path` only when reached, mirroring #150's own lazy `_is_core` computation in the Write/Edit NO/NO_TDD branches; it is NOT computed once and shared across branches, contrary to an earlier draft of this doc). Instructs the reviewer to add `CLASSIFICATION: TDD` (not `CLASSIFICATION: CLEAN_CODE`) for a missing-test-coverage violation — recognized by `_parse_stage2_classification()` (now returns `"tdd"` for that line; purely additive for the normal `stage2_code_review.md` path, which never instructs a reviewer to emit it — but the parser cannot tell which template produced a response, so IF a normal-path reviewer ever emitted it anyway, `_validate_normal_path`'s rejected branch simply never reads `"tdd_failure"`, so it would fall through to the generic `intent_validation` category, not an incorrect specific one) and threaded into `"tdd_failure"`, mapping to the SAME `intent_validation_tdd` blockage category Stage 1's regex-based NO_TDD block already uses.
  - **Manual-replay follow-up (real reviewer, codex-beast): the note's ORIGINAL wording was stricter than the normal path's `_has_tdd_declaration` regex and produced two false rejections.** `_has_tdd_declaration` accepts ANY `Test coverage: <text>` declaration, including one naming an EXISTING test — it never requires the test to be NEW. The note's first wording only offered "tests being added or updated," which does not cover (a) a docstring-only edit that names an existing test file (repro R06), or (b) a TDD green step that refers to making an existing FAILING test pass (repro R17) — codex-beast rejected both as CLASSIFICATION: TDD under the old wording. Fixed: the note now lists FOUR independent qualifying cases (tests added/updated; an EXISTING test named or referred to, explicitly INCLUDING making a failing test pass; the edit itself being a test; an explicit, quoted user permission to skip tests). The note **accepts the same POSITIVE forms `_has_tdd_declaration` accepts** (a structured declaration, `covered by <X>`, or quoted user permission) — it is NOT a claim that the note's overall permissiveness "matches" the regex's, since the two mechanisms differ (a regex on structured text vs. an LLM judging free-form context).
  - **Second re-review follow-up: the FIRST fix's classification gate ("classify TDD only when the intent excerpt makes no reference to tests at all") was ITSELF a loophole** — a weak verifier could read "no tests needed" or "tests later" as not being a "reference to tests" at all, and wrongly approve an untested core change (something `_has_tdd_declaration`'s structured-marker requirement would never allow). Fixed: the note now explicitly REJECTS negative/deferred mentions — "tests are absent, skipped, deferred, not needed, or will be written later does NOT count as coverage" — and phrases the classification instruction as "TDD unless one of the [four] cases above applies," never as an absence-of-reference test. Kept intentionally short (bug #87's "weak verifiers read this" principle — regression-locked at <1200 chars in the test file; current length ~1114 chars).
- **`intent_text` is capped at 3000 chars** inside `_build_stage2_prompt_reasoning_summary()`, matching the danger-bash gate's own `current_message[:3000]` cap (code review L6).
- Built via plain `str.format()`, never `PromptLoader.load_prompt(..., variables=...)` — `.format()` only scans the TEMPLATE for `{name}` placeholders once, never re-scanning substituted VALUES, so `intent_text`/`code` containing literal `{`/`}` can never trigger PromptLoader's rescan crash (the #150 code-review item-1 hazard). Same safe pattern `_build_stage2_prompt()` already uses.
- The SDK-unavailable fail-closed message is now a SINGLE shared string, `intent_validator._sdk_unavailable_message()`, used by both `_validate_normal_path` and `_validate_reasoning_summary_path` (code review L2 — was two independently maintained copies of the exact same text).
- Verdict parsing is UNCHANGED — `verdict_passes(stage2_feedback)`.

**Danger-bash, RELAXED path only — the prompt is now internally consistent (M2 fix):**
- Phase 1: for a STRICT-path turn (declaration present, or non-exception model, or M1's empty fallthrough), `current_message` is NEVER overwritten — it stays exactly what the found/stale gate set, and the marker check is the byte-identical `_has_intent_marker(current_message)`. Only for a RELAXED-path turn (`intent_source` in `("reasoning_summary", "visible_text")`) is `current_message` replaced with the resolver's combined text, and the pass condition becomes `bool(current_message.strip())`.
- Phase 2 prompt: the FIRST pass reframed only the assistant-message label, leaving the prompt internally contradictory — "You are validating if the **declared** intent..." followed later by "Does the **INTENT: declaration**..." even when the label said there was no declaration. Fixed: `build_danger_bash_relaxed_intent_wording()` (loads `src/pacemaker/prompts/common/danger_bash_relaxed_intent_wording.md`, Messi Rule 11) returns THREE paragraphs — the intro sentence, VALIDATE item 1, and the DIFFERENT-tool-call mismatch line — used TOGETHER whenever `intent_source` is RELAXED, so the whole prompt says "the **stated** intent" consistently. The assistant-message label ALSO now has a THIRD variant: `build_danger_bash_visible_text_label()` (new `danger_bash_visible_text_label.md`) for the `visible_text` source, which — unlike the first pass — no longer reuses the STRICT label's `"(contains intent declaration)"` wording (that phrase is false for a marker-less turn). The STRICT wording (declaration present, or non-exception model) stays inline in `hook.py`, byte-identical to before #151 — locked by a fixed-string test, not a before/after diff.
- **Anchor limits (#93) are explicitly UNCHANGED** — `not_found` still fails closed with the standard "transcript not ready, re-issue" block, for every model including exception-listed ones.

**Telemetry:**
- `blockage_events.details["intent_source"]` — added ONLY for exception-model turns (gated on the model-membership boolean, NOT on `result.get("intent_source")` truthiness) — `"declaration"` | `"reasoning_summary"` | `"visible_text"` | `"none"` (M1's fully-empty fall-through — see the decision table above). A non-exception-model block's `details` dict never has this key at all (no `"intent_source": null` — code review L1), keeping it byte-identical to before #151.
- **`RS` activity/governance event** (alongside `DG`) — `hook.py::_record_reasoning_summary_telemetry(intent_source, reviewer, session_id)`, mirroring `_record_degraded_review_telemetry`'s shape. **Fires ONLY for `intent_source in ("reasoning_summary", "visible_text")`** — an exception-model turn tagged `"declaration"` already complied with the normal strict contract and must NOT be recorded as a relaxed approval (this is the telemetry-side consequence of H2). Registered in `record_activity_event()`'s docstring (`database.py`) and the Activity Indicators table in `docs/ARCHITECTURE.md`.
  - **Live-test follow-up (wording fix):** the governance event's `feedback_text` originally used ONE generic phrase — `"[reviewer] Approved via reasoning-summary intent (intent_source=<x>)"` — for BOTH sources. The claude-usage monitor showed this exact text for a `visible_text` approval, which is misleading: no reasoning summary was involved at all. Fixed to name the actual source: `"[reviewer] Approved via relaxed intent: reasoning summary (intent_source=reasoning_summary)"` vs. `"[reviewer] Approved via relaxed intent: visible text without INTENT: marker (intent_source=visible_text)"`. The `[reviewer]` bracket prefix and the `intent_source=` token are unchanged (a monitor may parse them); the string stays untagged (issue #101 B2 — this is governance/telemetry text, not the Claude-facing block reason). The RS activity event itself carries no text (just the `"green"` status code), so nothing there needed changing. Tests: `tests/test_issue_151_reasoning_summary_intent.py::TestWriteEditGateTelemetry::test_reasoning_summary_source_feedback_text_wording` / `::test_visible_text_source_feedback_text_wording`.
- **Cross-repo follow-up, NOT done by this story** (same status as `DG` — see issue #131's own note above): the claude-usage monitor's `display.py` hardcodes its own `_ACTIVITY_GROUPS`/icon map and does not yet include `RS`, so the approval rate is recorded in `usage.db` but is NOT yet visible in the monitor's live activity line. Do not claim otherwise; this is a `claude-usage-reporting` change, out of scope here.

**Accepted risk (documented, not fixed):** for the RELAXED path, Stage 1 passes on ANY non-empty visible-text-or-summary — a turn whose only text is `"ok"` passes Stage 1. The only backstop is Stage 2's CHECK 0 (intent specificity), which is an LLM judgement call, not a structural guarantee the way the `INTENT:` marker regex is for the strict path. This is an intentional, evidence-based tradeoff (the issue's own decision), not an oversight.

**Deliberately out of scope (per the issue):** the Bash-script bypass of the Write/Edit gate (#150's own "Out of scope" note); changing the anchor/timing logic (#91/#93/#139).

**Key files**: `src/pacemaker/constants.py` (`reasoning_summary_intent_models` default), `src/pacemaker/transcript_reader.py` (`_extract_thinking_texts()`, `_merge_anchor_reasoning_summary()`, `anchor_model`/`anchor_reasoning_summary`), `src/pacemaker/intent_validator.py` (`resolve_reasoning_summary_intent_source()`, `_validate_normal_path()`, `_validate_reasoning_summary_path()`, `_reasoning_summary_intent_text()`, `_build_stage2_prompt_reasoning_summary()`, `_sdk_unavailable_message()`, `build_danger_bash_reasoning_summary_label()`, `build_danger_bash_visible_text_label()`, `build_danger_bash_relaxed_intent_wording()`, `_parse_stage2_classification()`'s `"tdd"` value), `src/pacemaker/hook.py` (`_normalize_reasoning_summary_intent_models()`, Write/Edit gate wiring, danger-bash Phase 1/2 wiring, `_record_reasoning_summary_telemetry()`), `src/pacemaker/prompts/pre_tool_use/stage2_code_review_reasoning_summary.md`, `src/pacemaker/prompts/pre_tool_use/reasoning_summary_core_path_note.md`, `src/pacemaker/prompts/common/danger_bash_reasoning_summary_label.md`, `src/pacemaker/prompts/common/danger_bash_visible_text_label.md`, `src/pacemaker/prompts/common/danger_bash_relaxed_intent_wording.md`.

**Tests**: `tests/test_issue_151_reasoning_summary_intent.py` (100 tests as of the live-test follow-up above — grew across three re-review rounds) — config normalization (null/false/string/mixed-list), the shared resolver's full decision table, non-exception-model FIXED-STRING regression locks (not a tautological before/after comparison), H2 (declaration-path TDD enforcement, no RS telemetry), M1 (the fragmented-turn repro, both at the `validate_intent_and_code` level and through the real hook), M2/M3 template consistency, L5's `CLASSIFICATION: TDD` mapping, L6's 3000-char cap, adversarial cases (`INTENT:` inside `new_string`/`content` never satisfies the exception; a DIFFERENT prior turn's summary never leaks in), stale-reissue coverage for both gates, and malformed-config-never-crashes for both gates. Danger-bash tests use the REAL bundled default rules (`DEFAULT_DANGER_RULES_PATH` pointed at a fresh nonexistent tmp path) — no mocking of `danger_bash_rules.load_rules`/`match_command`.

**Manual verification (not done by this story, per the issue's own step):** replay ≥30 real blocked Opus turns through the real Stage 2 reviewer, then a live xhigh run with the user re-enabling intent validation.

**Live-test follow-up — RECENT CONTEXT section (deployed as 2.36.0).** The manual verification above found a real false-reject: with `hook_model=haiku`, an Opus 5.5 subagent's terse current-turn visible text ("Now the SSH, git, tool-access and API-key test expectations.", no thinking) was rejected 3x at CHECK 0 by haiku ("reads as a section header or transition statement"), even though the PREVIOUS turn's reasoning summary held the full plan ("I'll update test_account_entry_points.py so failure rows assert placeholders instead of caller-supplied ids..."). The relaxed path is deliberately ANCHOR-ONLY (#93/#139's invariant, and this story's own original deviation #2) — the reviewer never saw the prior turn at all.

**Fix — Write/Edit relaxed path ONLY, additive context, never a relaxation of the current turn's own intent:**
- `intent_validator._build_recent_context_section(recent_context, max_chars=2000)` builds a clearly labelled "RECENT CONTEXT (earlier turns — context only, NOT the intent)" section from up to 3 PRIOR turns' `(visible_text, thinking_text)` tuples, oldest to newest. Static header/instructions loaded from `reasoning_summary_recent_context_note.md` (Messi Rule 11) and state four rules: the current turn's text/summary remains the intent; recent context may clarify what a terse current intent refers to; the code must still match the CURRENT intent as clarified by that context; never approve code that only matches the context and contradicts or is unrelated to the current intent.
- Capped at ~2000 chars, keeping the MOST RECENT turns (walks newest→oldest, drops oldest first; a single oversized turn alone is truncated to its TAIL rather than dropped).
- Threaded through `_validate_reasoning_summary_path()` → `_build_stage2_prompt_reasoning_summary(..., recent_context=...)` → a NEW `{recent_context_section}` placeholder in `stage2_code_review_reasoning_summary.md`, positioned between the INTENT block and PROPOSED CODE. `None`/empty (the default) omits the section entirely.
- CHECK 0 (relaxed template only, NOT part of the M3 verbatim lock) has an extra bullet telling the reviewer to judge specificity AS CLARIFIED by RECENT CONTEXT when present — without it, a weak verifier anchors on "too vague" for a terse current intent that continues an earlier-stated plan. The normal/strict template's own, differently-worded CHECK 0 is untouched.

**Round 2 fix (performance + correctness) — anchor-relative data source, no second transcript parse.** The FIRST cut above sourced RECENT CONTEXT via a SECOND `transcript_reader.get_last_n_messages_for_validation(transcript_path, n=4, _with_prose=True)` call in `hook.py` (using a now-removed `_with_thinking` option on that function — see the Messi Rule 12 cleanup note below), on top of the pre-existing n=2 call. Measured on a real 99.7MB transcript: **0.824s for the second call, on top of 1.216s for the first** — nearly 2 extra seconds eating the shared `_gate_deadline` before Stage 2 could even run, on the RELAXED path, which is the COMMON path for Opus at xhigh. It also directly contradicted that removed option's own docstring ("never re-parse a second time"). Separately, its stale-path slicing (`prose[:-1]`, positional relative to the WHOLE transcript) could surface the anchor's OWN text as "context" for itself once a stale re-issue's thinking was flushed.

- **Fix**: `_find_turn_matching_tool_input` now computes recent context itself, from the SAME fixed-cost tail window (`_read_tail_raw_entries`/`TAIL_READ_BYTES`) it already reads for anchor resolution — zero additional file I/O. `_ordered_assistant_turn_keys(raw_entries)` returns every LOGICAL assistant turn key in chronological order (unlike `_last_n_assistant_turn_keys`, an UNORDERED set capped to the last N); `_build_prior_turns_context(raw_entries, anchor_index, anchor_request_id, max_turns=RECENT_CONTEXT_MAX_TURNS)` locates the anchor's position in that ordered list and returns up to `RECENT_CONTEXT_MAX_TURNS` (3) turns immediately BEFORE it — oldest to newest, as `(visible_text, thinking_text)` tuples — reusing the exact same generic per-turn helpers (`_merge_anchor_turn`/`_merge_anchor_reasoning_summary`) the anchor's own text/reasoning-summary fields already use, just called with each PRIOR turn's own `(index, request_id)`. The anchor's own key is never included — context is always relative to whichever turn is CURRENTLY anchored (found or stale), so the anchor's own text can never leak into its own context, by construction. Fewer than `max_turns` prior turns within the tail window (including zero) yields a shorter/empty list; this is expected, not an error.
- **Wiring**: `_outcome["anchor_recent_context"]` is set on BOTH the "found" and "stale" branches, in its OWN try/except placed BEFORE the pre-existing `anchor_has_visible_text`/`anchor_has_thinking`/`anchor_reasoning_summary` try (the #140 finding-1 lesson: a caller-visible field's presence must never depend on an unrelated computation's success) — a failure defaults to `[]`, never crashes the retry loop. `_copy_anchor_shape_flags` copies it into the caller-visible `_diagnostics` dict the same way as the other anchor-shape fields. `hook.py`'s Write/Edit gate just reads `_write_edit_diagnostics.get("anchor_recent_context")` (gated on `isinstance(..., list)`) when the relaxed path applies — no second transcript read, no `get_last_n_messages_for_validation(n=4, ...)` call at all.
- **Computed unconditionally, not lazily — and that's fine**: unlike the removed second full-transcript parse, this computation is just in-memory list-walking over data already read into `raw_entries` (bounded by `TAIL_READ_BYTES`, 512KB) — cheap enough that it no longer needs the "only compute it when the relaxed path applies" lazy-gating precedent (`_is_core_path`, #150) the first cut relied on for cost control. `hook.py` still gates its OWN read of the diagnostics dict behind `_write_edit_relaxed_text is not None` (a trivial `.get()`, not a re-parse), matching the "never for non-exception models, never for the strict path" intent.
- **Measured cost** (30.4MB synthetic transcript, 3 real prior turns with thinking text immediately before the anchor, ~15,000 padding turns before that): **~5ms total** for the ENTIRE anchor resolution including recent-context extraction — see `tests/test_issue_151_reasoning_summary_intent.py::TestAnchorRecentContextTiming`.
- **Stale-path correctness fix, for free**: `tests/test_issue_151_reasoning_summary_intent.py::TestWriteEditGateRecentContextLiveRepro::test_stale_path_context_excludes_anchor_includes_prior_turn` drives a real STALE re-issue through the hook and asserts the anchor's own reasoning summary appears exactly ONCE in the prompt (as the INTENT itself, never duplicated into RECENT CONTEXT) while the turn before it DOES appear as context.
- **Danger-bash and the strict/normal path are completely unaffected** — `reasoning_summary_recent_context` is still a new, independently-defaulted (`None`) param on `validate_intent_and_code()`, read ONLY inside the relaxed branch.

**Key files (additions)**: `src/pacemaker/transcript_reader.py` (`RECENT_CONTEXT_MAX_TURNS`, `_ordered_assistant_turn_keys()`, `_build_prior_turns_context()`, `anchor_recent_context` on `_find_turn_matching_tool_input`'s `_outcome`/`_copy_anchor_shape_flags`), `src/pacemaker/intent_validator.py` (`_build_recent_context_section()` — label-preserving truncation via internal `(label, text)` pairs rather than blind string-slicing, so a single oversized turn's "Visible text:"/"Reasoning summary:" label always survives truncation; leading `\n` moved into this function's own return value rather than a hardcoded blank line in the template, so the no-context case has exactly one blank line, not two — `recent_context` params on `_build_stage2_prompt_reasoning_summary()`/`_validate_reasoning_summary_path()`, `reasoning_summary_recent_context` param on `validate_intent_and_code()`), `src/pacemaker/hook.py` (Write/Edit gate reads `anchor_recent_context` straight from the already-populated diagnostics dict), `src/pacemaker/prompts/pre_tool_use/reasoning_summary_recent_context_note.md`, `src/pacemaker/prompts/pre_tool_use/stage2_code_review_reasoning_summary.md` (CHECK 0's recent-context bullet; `{recent_context_section}` placeholder spacing).

**Re-review follow-up (Messi Rule 12, orphan code) — `_with_thinking` removed entirely.** Once `hook.py`'s second `get_last_n_messages_for_validation(n=4, ...)` call was deleted above, `_with_thinking` (its third `@overload`, the triple-return branch, and the `"thinking"` merge inside the requestId-grouping loop) had no remaining caller — the anchor-relative path (`_build_prior_turns_context`) never used it; it always read `thinking` straight off raw JSONL content via `_extract_thinking_texts`/`_merge_anchor_reasoning_summary`, never through `_extract_message_parts`. The `"thinking"` key `_extract_message_parts()` added for this now-dead path was ALSO removed (it had exactly one reader: the merge this option performed) — `_extract_message_parts()` is back to returning only `{"text", "tools"}`, its pre-#151 shape. `TestGetLastNMessagesWithThinking` was deleted (its 4 tests only existed to cover the removed option). Per the re-review that flagged this cleanup, the anchor-relative recent-context computation adds ~0.1ms on the real 99.7MB transcript — confirming it was never the removed option's triple-return machinery doing the work, just the pre-existing tail-window read (matching this repo's own ~5ms measurement on a smaller 30.4MB synthetic transcript, see `TestAnchorRecentContextTiming`).

**Tests (additions, same file, 108 tests total as of round 2)**: `TestBuildRecentContextSection` (empty/skip cases, ordering, cap enforcement including the single-oversized-turn edge case with label preservation, the four instructional rules present), `TestStageTwoPromptRecentContextParam` (including the no-double-blank-line cosmetic check), `TestCheckZeroReferencesRecentContext` (CHECK 0's new bullet present in the relaxed template only, normal template's own differently-worded CHECK 0 untouched), `TestAnchorRecentContextField` and `TestAnchorRecentContextTiming` in `transcript_reader`'s own test group (found/stale branches, 0/1/3+-prior-turn cases, ordering, anchor-exclusion, ~5ms on a 30MB synthetic transcript), `TestWriteEditGateRecentContextLiveRepro` (the exact live repro through the REAL hook — prior summary appears as context and is labelled as such, the current turn's own terse text remains the intent, no rendered tool params/INTENT-in-content ever leak in, cap enforcement end-to-end, the stale-path anchor-exclusion proof above, and a Sonnet-model regression lock proving the strict/normal path — with its own unrelated pre-existing "RECENT CONTEXT (last 2 messages):" header — never gains this section).

---

### Issue #153 — Stage 2 Edit review sees only `new_string`, and secret exposure via unreviewed on-disk content

**Problem.** For Edit tool calls, Stage 2 saw only `new_string` — never `old_string` — so it could not verify the DIRECTION of a change (an intent to "remove X"/"revert Y" could be approved above code that ADDS X; live replay proved this 6/6). Separately, a partial-fragment Edit could be false-BLOCKED as "incomplete" when the real function body sat just below it on disk (`mock_remove_with_transaction`), or when a SIBLING Edit in the same assistant message completed exactly what looked missing (the OIDC split-multi-edit case — false-BLOCKED twice, and the file on disk still has the OLD tail at validation time, so on-disk context alone cannot help there; only the sibling turn record can).

**Scope — Edit reviews ONLY**, on both `stage2_code_review.md` (normal/strict path) and `stage2_code_review_reasoning_summary.md` (#151 relaxed path). Write is completely unaffected (still shows `content` as `code`, gets neither new section). A non-Edit/Write tool (Bash, Read, …) never reaches this code at all — unchanged.

**1. OLD → NEW view.** `intent_validator._build_edit_diff_view(old_string, new_string, max_chars=6000)` replaces `proposed_code` for Edit (Write keeps `content` unchanged) with a labelled two-block view, capped (each block truncated to its own TAIL with an explicit marker):
```
OLD (being replaced — context only):
<old_string>

NEW (proposed code under review):
<new_string>
```
Code review MUST-FIX 2: the labels are deliberately unambiguous — CHECK 2 (clean code) and CHECK 3 (bug detection) apply ONLY to NEW; OLD exists solely to verify direction/scope. The SAME sentence ("CHECK 2 and CHECK 3 apply only to the NEW code; OLD is shown only to verify the direction and scope of the change.") is in the M3-locked PARTIAL CONTEXT WARNING section of BOTH templates, identically.

**2. Surrounding on-disk file context.** `intent_validator._build_edit_surrounding_context_section(file_path, old_string, replace_all, context_lines=15, max_chars=4000, _deadline=None, _db_path=None)` reads the target file ONCE, locates `old_string`, and shows ±15 lines labelled "CURRENT FILE CONTENT AROUND THE EDIT … BEFORE this edit is applied", with the edited region marked `>>> EDITED REGION (old_string) START/END <<<`. Fail-safe (a short note only, never a crash, never the real content) when: the gate's `_deadline` has passed, `old_string` is empty, the file doesn't exist/can't be read/exceeds 2MB, `old_string` isn't found (e.g. not yet written by a prior sibling), `old_string` is ambiguous without `replace_all`, the path is secret-like, or the content matches a stored `SECRET_FILE` secret (see MUST-FIX 1 below). Review low-priority fixes: reads with `newline=""` (a multi-line `old_string` carrying an embedded `\r\n`, exactly as Claude Code reads it from a CRLF file, would never match `content.count(old_string)` under universal-newline translation); the after-window strips exactly one leading `\r\n`/`\n`/`\r` line-terminator artifact before `.splitlines()` (previously showed N-1 lines instead of N when `old_string` ended exactly at a line boundary — the terminator produced a leading phantom-empty `.splitlines()` entry that silently consumed one context slot).

**3. Sibling edits.** `transcript_reader._build_sibling_edits_data(tools, anchor_tool_id)` — wired into `_find_turn_matching_tool_input`'s `_outcome`/`_diagnostics` exactly like `anchor_recent_context` (both found and stale branches, its own try/except placed before the has_visible_text/has_thinking try, zero extra transcript parse) — returns `{"position": k, "total": n, "siblings": [...]}` where `n` counts ALL Write/Edit tool_use calls in the anchored turn (non-Write/Edit calls like Read are never counted/shown) and `k` is the reviewed call's own 1-based position. Code review MUST-FIX 3: the reviewed call is identified by the ANCHOR'S OWN `tool_use.id` (already resolved by the anchor-scan loop), NEVER by re-matching content — two byte-identical Edits in the same turn are indistinguishable by content, so content-matching would always resolve to the FIRST one, silently mislabeling which call is under review whenever the SECOND (or later) of two identical calls is the one actually being reviewed. Each sibling dict carries its OWN absolute `"position"` (never renumbered 1..len(siblings)), so `intent_validator._build_sibling_edits_section()` can label each one `#1`, `#3`, … — this is what makes "a LATER sibling completes this fragment" visible (the OIDC case: reviewed is #2, and sibling **#3** — not "sibling #2 of 2" — completes it):
```
OTHER EDITS IN THE SAME MESSAGE (applied in order; the reviewed edit is #2 of 3):
============================================================
#1 — [Edit] src/routes.py
Old: ...
New: ...

#3 — [Edit] src/routes.py
Old: ...
New: ...
============================================================
```

**4. Prompt instructions.** Both templates (identically, M3-locked): CHECK 1 gained "When OLD → NEW is shown, verify the direction: if the intent says remove/revert/rename/delete/replace and NEW does the opposite … REJECT. If the surrounding context shows the edited function ends right after the fragment and no sibling edit completes it, treat a missing return or branch as incomplete." PARTIAL CONTEXT WARNING's benefit-of-the-doubt paragraph is now conditioned: "If OLD → NEW, surrounding file context, and sibling edits above are all absent, … prefer APPROVED …" followed by "When ANY … IS shown above, judge the fragment on that evidence instead of giving the benefit of the doubt …" (review item A — a live replay had approved an inverted "remove X" intent 6/6 partly because the benefit-of-the-doubt language applied unconditionally). Review MUST-FIX 3a (template wording): the paragraph previously said "When shown **below**", but the two new sections render ABOVE it (PROPOSED CODE → surrounding context → sibling edits → PARTIAL CONTEXT WARNING) — fixed to "shown **above**" throughout.

**5. Secret exposure fix (code review MUST-FIX 1) — Stage 2 prompts were NEVER masked before this fix.** Only Langfuse traces were masked (`secrets.sanitizer.sanitize_trace()`); #153 added ~30 lines of UNREVIEWED on-disk file content per Edit, so a hard-coded key in `settings.py`/`config.js` would otherwise leak to a (possibly hosted) reviewer.
   - **1(a) Whole-prompt masking, one choke point.** `intent_validator._mask_reviewer_prompt(prompt, db_path=None)` — reuses the SAME machinery `sanitize_trace` already uses (`secrets.database.get_all_secrets()` + `secrets.masking.mask_text()`) — is called inside `_call_stage2_validation()`, the single function BOTH `_validate_normal_path` and `_validate_reasoning_summary_path` route through, so one choke point covers both Write/Edit paths. The danger-bash Phase 2 prompt does NOT go through `_call_stage2_validation` (it's built inline in `hook.py` and calls `resolve_and_call_with_reviewer` directly), so it masks itself at its own call site with the same helper.
   - **`db_path=None` is a DELIBERATE, load-bearing no-op — never a hidden default.** `pacemaker.constants.DEFAULT_DB_PATH` is computed ONCE, at first module import, from whatever `$HOME` was at that moment — this repo's `tests/conftest.py::_guard_production_db` autouse fixture patches `hook.DEFAULT_DB_PATH` specifically (the already-imported, mutable attribute on the `hook` module), NOT the frozen `constants.py` value. If `_mask_reviewer_prompt`/`_content_matches_stored_secret_file` did their own fresh `from .constants import DEFAULT_DB_PATH`, EVERY test in the suite that exercises the Edit gate would silently read (and via `_init_database`, potentially write schema into) the REAL developer's `~/.claude-pace-maker/usage.db` — a serious test-isolation violation. Instead, `db_path`/`_db_path` is threaded EXPLICITLY as a plain parameter all the way from `hook.py` (`validate_intent_and_code(..., stage2_db_path=DEFAULT_DB_PATH)` → `_validate_normal_path`/`_validate_reasoning_summary_path` → `_call_stage2_validation` → `_mask_reviewer_prompt`; and `_build_edit_surrounding_context_section(..., _db_path=DEFAULT_DB_PATH)` → `_content_matches_stored_secret_file`), always resolving to hook.py's OWN patchable `DEFAULT_DB_PATH` at the real call sites, and to `None` (skip, zero DB access) for every existing test/caller that predates this fix and never passes it.
   - **1(b) Skip surrounding-context for secret-like paths/content, independent of masking.** `_is_secret_like_path(file_path)` (pure, no I/O) matches the basename case-insensitively against `*.env*`, `*secret*`, `*credential*`, `*.pem`, `*.key`, `*.p12`, `id_rsa*` — checked BEFORE any file read. `_content_matches_stored_secret_file(content, db_path)` returns True when `content` EXACTLY equals a stored `type="file"` secret's value (i.e. this exact file was previously declared via `🔐 SECRET_FILE:` — see `secrets/parser.py::parse_file_secret`, which stores the file's FULL CONTENT as the value) — a `type="text"` (SECRET_TEXT) secret never matches here, since that's a substring secret, not a whole-file declaration. This is defense-in-depth ON TOP OF masking: an unregistered hard-coded secret in an obviously secret-NAMED file is skipped by path heuristic even with no stored VALUE yet to mask, and a whole declared secret FILE is skipped outright rather than shown with only its known substrings masked (which could still reveal structure).
   - **Fail-safe everywhere**: any DB read failure (missing/corrupt file, permission error) logs a WARNING and returns the ORIGINAL prompt/False — never raises, never blocks validation.

**Key files**: `src/pacemaker/intent_validator.py` (`_build_edit_diff_view()`, `_build_edit_surrounding_context_section()`, `_build_sibling_edits_section()`, `_is_secret_like_path()`, `_content_matches_stored_secret_file()`, `_mask_reviewer_prompt()`, `_call_stage2_validation()`'s masking choke point, `stage2_db_path`/`_db_path` threaded through `validate_intent_and_code()`/`_validate_normal_path()`/`_validate_reasoning_summary_path()`), `src/pacemaker/transcript_reader.py` (`_build_sibling_edits_data()`, `anchor_sibling_edits` on `_find_turn_matching_tool_input`'s `_outcome`/`_copy_anchor_shape_flags`), `src/pacemaker/hook.py` (Write/Edit gate's `proposed_code` computation for Edit, `_write_edit_surrounding_context_section`/`_write_edit_sibling_edits_section` construction gated on `tool_name == "Edit"`, `stage2_db_path=DEFAULT_DB_PATH` passthrough, danger-bash Phase 2's own `_mask_reviewer_prompt` call), `src/pacemaker/prompts/pre_tool_use/stage2_code_review.md` and `stage2_code_review_reasoning_summary.md` (`{surrounding_context_section}`/`{sibling_edits_section}` placeholders, CHECK 1's direction-check sentence, PARTIAL CONTEXT WARNING's rewording — all identical between the two templates, M3-locked).

**Tests**: `tests/test_issue_153_edit_context.py` (new file) — unit tests for all builder/check functions (diff view, surrounding context incl. CRLF/off-by-one/secret-skip, sibling section incl. per-sibling absolute numbering, path-pattern and stored-file-content secret checks, whole-prompt masking incl. fail-safe-on-broken-db-path and none-db-path-is-a-true-no-op), `transcript_reader` wiring tests (found/stale `anchor_sibling_edits`, byte-identical-Edits-disambiguated-by-id), template wiring/M3-lock-extension tests (new placeholders, CHECK 1 direction check present+identical, PARTIAL CONTEXT WARNING's scope-clarification/conditional-benefit-of-doubt/above-not-below wording present+identical), and hook-level integration tests mirroring all three live-evidence cases (OIDC 3-edit sibling completion, `mock_remove_with_transaction` signature-only on-disk body, secret masking at the provider boundary for `new_string`/surrounding-context/sibling sources, secret-like-path skip). `tests/test_issue_151_reasoning_summary_intent.py`'s live-repro test and `tests/test_pre_tool_hook.py`'s args-passed-to-`validate_intent_and_code` test were updated in place (their old assertions encoded the PRE-#153 `new_string`-only behavior as correct; both now assert the new OLD/NEW view and, for the #151 test, that a DIFFERENT prior turn's `old_string` still never leaks in).

### Issue #153 re-review round 2 — masking performance, minimum secret length, benefit-of-doubt condition, explicit-intent guard

**6. Masking performance (re-review MUST-FIX 1, HIGH).** **[Bug #157, v2.37.1: the pre-filter described below NO LONGER LIVES in `_mask_reviewer_prompt()`. It moved to the shared `secrets.masking.build_prefiltered_pattern(secrets, texts)`, which both `_mask_reviewer_prompt()` and Langfuse's `sanitizer.sanitize_trace()` call -- see "Bug #157" below. `_mask_reviewer_prompt()` now only applies its own min-length-8 rule (`_MIN_REVIEWER_MASK_SECRET_LENGTH`) and hands the eligible secrets to the helper; the "~5ms on a 763-secret store" claim and the output are unchanged.]** `_mask_reviewer_prompt()`'s first shipped version called `mask_text()` (→ `_build_secrets_pattern()`, `re.escape()` + one combined regex compile) over the FULL stored-secrets list on every Stage 2 / danger-bash Phase 2 prompt. Against a real developer store's shape (763 secrets, ~5.2M total chars, 29 values over 50KB — `SECRET_FILE` stores whole file contents, not just a token) this measured **~6.5s per call** — on the PreToolUse critical path, eating directly into the `PRE_TOOL_REVIEW_BUDGET_SECONDS` budget documented under "Competitive Review Pipeline" above.

Fixed with a pre-filter, BEFORE the pattern is ever built: `_mask_reviewer_prompt()` now does a cheap `secret in prompt` substring check for every stored secret and keeps only the (typically tiny) subset that actually occurs in this specific prompt — `[s for s in secrets if s and len(s) >= _MIN_REVIEWER_MASK_SECRET_LENGTH and len(s) <= len(prompt) and s in prompt]` — and calls `mask_text()`/`_build_secrets_pattern()` on ONLY that filtered list. `secrets/masking.py` itself was not touched; the fix is entirely in what gets passed to it.

**Measured after the fix** (`tests/test_issue_153_edit_context.py::TestMaskReviewerPrompt::test_masking_stays_fast_with_a_large_real_shaped_secrets_store`, a synthetic store built to the same shape: 700 ordinary secrets + 29 60KB+ values + 32 misc padding + 2 secrets that actually appear in the prompt = 763 total): **~5ms** (measured 0.0050s in a standalone run, comfortably under the test's 100ms ceiling), with exactly the 2 relevant secrets kept out of 763 and masked — down from the ~6.5s/763-secret baseline that motivated the fix. This makes the cost bounded by construction (proportional to how many secrets actually appear in THIS prompt, not to store size) rather than by an explicit deadline — no `_deadline` parameter was added to `_mask_reviewer_prompt()`, since the pre-filter alone keeps it fast regardless of store size.

**7. Minimum secret length for reviewer-prompt masking (re-review MUST-FIX 2, MEDIUM).** A real store had 31 secrets shorter than 8 characters; two 5-character ones each matched 8+ times inside ordinary ambient code text (ordinary short substrings coincidentally equal to a stored short secret), producing spurious `*** MASKED ***` noise in the reviewer's prompt with no security benefit (a 5-char "secret" is not meaningfully protectable via substring masking anyway). `_MIN_REVIEWER_MASK_SECRET_LENGTH = 8` (module constant in `intent_validator.py`) is applied in the SAME pre-filter loop as the performance fix above — a secret shorter than 8 chars is skipped regardless of whether it appears in the prompt. Skipped secrets are never named or valued in any log: exactly ONE `log_warning` call per `_mask_reviewer_prompt()` invocation reports only the COUNT (`"Skipped {skipped_short} stored secret(s) shorter than {_MIN_REVIEWER_MASK_SECRET_LENGTH} chars during reviewer-prompt masking (too short to mask safely; values never logged)"`), never per-secret.

**Scope note — this rule applies ONLY to reviewer-prompt masking (`_mask_reviewer_prompt`), not to Langfuse trace sanitization.** `secrets/sanitizer.py::sanitize_trace()` was deliberately left untouched — it has no minimum-length filter and will still mask (and can still spuriously match on) secrets shorter than 8 chars in pushed traces. This is a known, explicitly out-of-scope gap for issue #153; if short-secret noise in Langfuse traces becomes a problem, it needs its own fix in `sanitizer.py`, not a reuse of `_MIN_REVIEWER_MASK_SECRET_LENGTH`.

**Known false-skip paths — `_is_secret_like_path()` is a basename `fnmatch` heuristic, not a content scan, so it both under- and over-matches by design.** Files whose basename does NOT match any of `_SECRET_LIKE_PATH_PATTERNS` (`*.env*`, `*secret*`, `*credential*`, `*.pem`, `*.key`, `*.p12`, `id_rsa*`) get NO path-based skip even if they hold real secrets — e.g. `settings.py`, `config.js`, `application.yaml` are not caught by the path heuristic at all (masking is still the primary defense there, per item 5 above). Conversely, files that merely contain one of these substrings in their NAME are skipped even when they hold no secrets — `secret_manager.py` (contains "secret"), `credentials_service.py` (contains "credential"), `my_secrets.py` (contains "secret"), and `foo.envelope.py` (matches `*.env*` — the pattern matches the substring `.env` anywhere in the basename, not just a literal `.env` extension, so `.envelope.py` collides) all skip surrounding-context rendering even for wholly ordinary, non-secret source code. This is an accepted false-positive tradeoff (skip-too-much is safe; skip-too-little leaks), not a bug to silently "fix" by narrowing the patterns without re-running the review that chose them.

**8. Follow-up A condition corrected (re-review MUST-FIX 3, MEDIUM).** Round 2's first version of the PARTIAL CONTEXT WARNING conditioned the benefit-of-the-doubt paragraph on "OLD → NEW, surrounding file context, and sibling edits above are all absent" — but since #153 made OLD → NEW ALWAYS shown for Edit (item 1 above), that condition could never be true for the "surrounding context was omitted for a real reason (deadline, secret-like path, ambiguous old_string) and no sibling completes it" case — exactly the PARTIAL CONTEXT WARNING's own core use case, silently deprived of the benefit of the doubt it was designed to protect. Fixed: the condition now looks ONLY at whether surrounding file context is shown and whether a sibling edit is present — OLD → NEW's presence/absence plays no part in it. An "omitted (…)" note (ambiguous/missing `old_string`, deadline reached, secret-like path, etc.) explicitly counts as "NOT shown" for this purpose, not as a partial win. Reworded identically (M3-locked) in both templates:
```
If CURRENT FILE CONTENT AROUND THE EDIT is NOT shown above — whether ABSENT
entirely or replaced by an "omitted (…)" note (ambiguous/missing old_string,
deadline reached, secret-like path, etc. all count as NOT shown) — AND no
sibling edit covers the gap, keep the benefit of the doubt for context you
cannot see: prefer APPROVED over a false rejection when genuinely uncertain
whether a required pattern exists elsewhere in the file. A missed issue is
recoverable; a false block wastes developer time and erodes trust in the
review system.

Otherwise — CURRENT FILE CONTENT AROUND THE EDIT IS shown above, or a sibling
edit IS present — judge the fragment on that evidence instead of giving the
benefit of the doubt: ...
```
Regression-locked by `tests/test_issue_153_edit_context.py::TestPartialContextWarningM3LockExtended` (`test_prefer_approved_condition_based_on_surrounding_and_siblings_only`, `test_omitted_note_explicitly_counts_as_not_shown`, `test_condition_no_longer_requires_old_new_absent` — these replaced the earlier, now-wrong `test_prefer_approved_limited_to_all_sections_absent`).

**9. Negative-control discipline (X1/X2) and the CHECK 1 current-turn-only guard.** Manual replay with the real haiku reviewer found two false-APPROVE cases the automated tests didn't cover:
- **X1**: a head fragment left the function genuinely incomplete, with nothing after it and no sibling edit — must still BLOCK even with the item-8 fix, since neither surrounding context nor a sibling is present in this case (the "otherwise" branch never applies; the benefit-of-the-doubt branch applies, but CHECK 1/CHECK 3 must still catch a CLEARLY incomplete fragment on its own terms).
- **X2**: current intent said "remove the SSH fingerprint assertions"; OLD → NEW clearly showed NEW *adding* `_fingerprint_id(...)` assertions — a direction violation CHECK 1's new sentence (item 4 above) is designed to catch on its own, without needing surrounding context or siblings.

Both are exercised as negative controls in `tests/test_issue_153_edit_context.py` alongside the three original false-block live-evidence cases (OIDC sibling completion, `mock_remove_with_transaction`, secret masking) — the fix for item 8 does not regress any of the three, and CHECK 1's direction-check sentence gained an explicit scoping clause, identical in both templates (M3-locked): "This check is always evaluated against the CURRENT turn's own words, never against RECENT CONTEXT or any earlier-turn text." — added directly to prevent RECENT CONTEXT (relaxed-path only, see item 10) from being used to launder a direction violation.

**10. RECENT CONTEXT must never override an EXPLICIT current intent (relaxed/#151 path only, `stage2_code_review_reasoning_summary.md` + `reasoning_summary_recent_context_note.md`).** Manual replay with real haiku falsely approved X2 (see item 9) 3 out of 3 times specifically on the RECENT CONTEXT-bearing relaxed path — haiku's own stated reasoning was that an EARLIER turn's plan (add the assertions) satisfied CHECK 0's "AS CLARIFIED by RECENT CONTEXT" language, overriding the CURRENT turn's explicit contradicting instruction to remove them. RECENT CONTEXT was only ever meant to disambiguate a TERSE/VAGUE current intent (e.g. "continue with the plan"), never to relitigate an EXPLICIT current instruction. Fixed with matching sentences in two places, both identical in wording to each other:
  - `reasoning_summary_recent_context_note.md` (the note itself, prepended when RECENT CONTEXT is non-empty): "RECENT CONTEXT may only fill in what a terse or vague current intent refers to. It must NEVER override, reinterpret, or replace a current intent that is EXPLICIT — including any use of remove / revert / delete / undo / rename / replace — even if an earlier turn planned the opposite."
  - CHECK 0's bullet list in `stage2_code_review_reasoning_summary.md` (relaxed template only — NOT M3-locked, since CHECK 0 deliberately differs per template): "RECENT CONTEXT may only fill in what a terse or vague current intent refers to; it must NEVER override, reinterpret, or replace a current intent that is EXPLICIT (remove/revert/delete/undo/rename/replace all count as explicit), even if an earlier turn planned the opposite."

  The normal (non-relaxed) template has no RECENT CONTEXT section at all and needed no change beyond keeping its own M3-locked CHECK 1 sentence (item 9) identical to the relaxed template's copy. Regression-locked by `tests/test_issue_153_edit_context.py::TestRecentContextNeverOverridesExplicitIntent` (3 tests: the note states the guard, CHECK 0's bullet states the guard, the normal template's CHECK 0 is unaffected).

**Key files (additions)**: `_MIN_REVIEWER_MASK_SECRET_LENGTH` and the rewritten `_mask_reviewer_prompt()` pre-filter in `src/pacemaker/intent_validator.py`; `src/pacemaker/prompts/pre_tool_use/reasoning_summary_recent_context_note.md` (explicit-intent guard sentence); both `stage2_code_review*.md` templates' PARTIAL CONTEXT WARNING (item 8 rewording, M3-locked) and CHECK 1 (item 9 current-turn-only sentence, M3-locked); `stage2_code_review_reasoning_summary.md`'s CHECK 0 bullet (item 10, relaxed-only).

**Tests (additions)**: `tests/test_issue_153_edit_context.py` — `TestMaskReviewerPrompt::test_masking_stays_fast_with_a_large_real_shaped_secrets_store` and `::test_short_secrets_skipped_with_single_warning_never_logs_value`; `TestPartialContextWarningM3LockExtended`'s three item-8 tests; `TestCheck1DirectionCheck::test_check1_direction_check_evaluated_against_current_turn_only`; `TestRecentContextNeverOverridesExplicitIntent` (3 tests).

---

## Issue #151 round 3 / #154 (live-replay follow-up 2.36.2) — RECENT CONTEXT terse-gating, prior-turn reasoning-summary fallback, real unified diff, danger-bash destructive scope

Four changes landed together in v2.36.2, all from live replay evidence against real haiku/Opus 5.5 traffic. **Issue #154 is the code-review follow-up on this same round** — it changed CHANGE 1 (removed the direction-word clause), CHANGE 2 (restricted the prior-turn fallback to Write/Edit only), and CHANGE 3 (fixed a Write-diff cap regression and made the NEW FILE WARNING wording conditional). See the "Issue #154 code-review follow-up" section below the four CHANGE write-ups for the corrections — the paragraphs immediately below describe the AS-SHIPPED (#154-corrected) behavior, not the original 2.36.2 design.

### CHANGE 1 — RECENT CONTEXT shown only for a TERSE current intent

**Problem.** Three live false blocks: a DETAILED, file-naming current intent was judged against an EARLIER turn's stale plan surfaced via RECENT CONTEXT — "Reverting A" judged against "create variant A", "Restoring B, then variant C" judged against "remove … variant B", a detailed docstring-update intent judged against "delete get_audit_logs". The prompt's own wording guards (from #151's original round) were not enough — this needed a CODE decision, made before the prompt is even built, not left to a weak reviewer's own judgment.

**Rule, as shipped after issue #154's follow-up** (`intent_validator._should_include_recent_context(intent_text, file_path) -> bool`): include RECENT CONTEXT only when the CURRENT intent is "terse" — `len(intent_text) < RECENT_CONTEXT_TERSE_MAX_CHARS` (150, `constants.py`) OR it does not mention the target file's basename/stem (`_mentions_file_basename_or_stem`, case-insensitive, distinct from the pre-existing `_mentions_file` which checks the full path case-sensitively for bug #83's unrelated stale-anchor guard) — either condition alone is enough. That is the WHOLE rule.

**Removed by issue #154's follow-up — a direction-word clause.** The originally-shipped 2.36.2 version ALSO withheld RECENT CONTEXT whenever the intent contained an explicit direction word (`remove/revert/restore/undo/delete/rename/replace`, matched as word stems so conjugated forms counted too), on the theory that such an intent already states its own self-contained goal. A second live replay showed this was wrong: cases F1 ("Reverting A…") and F2 ("Restoring B, then variant C") are TERSE restore intents that only make sense with the EARLIER turns defining what "A"/"B" refer to — withholding RECENT CONTEXT for them made haiku block both at CHECK 0 as "too vague". The stem-based regex was also demonstrably too loose: `\bundo\w*\b` matched "**undo**cumented" (an unrelated word, not the verb "undo"), and `remov`/`replac`/`restor` matched inside plain nouns like "**removal**", "**replacement**", and "**restored**" regardless of whether the CURRENT intent was actually asserting that direction as its own action. The direction-misjudgment risk the clause existed to guard against is handled by CHANGE 3's unified diff instead (explicit `-`/`+` markers make direction unambiguous to the reviewer without a code-level suppression). `RECENT_CONTEXT_DIRECTION_WORD_STEMS` and `_RECENT_CONTEXT_DIRECTION_WORD_PATTERN` were deleted (Messi Anti-Orphan-Code), along with their tests.

**Wiring (`hook.py`, Write/Edit gate only — danger-bash never renders RECENT CONTEXT at all, so this gating is N/A there)**: the raw anchor-relative prior-turns list is read out BEFORE resolving intent source (needed for CHANGE 2 below too); after resolving, the candidate context (minus the last turn if it was consumed by CHANGE 2's fallback — see below) is attached to the prompt only when `_should_include_recent_context(final_intent_text, file_path)` is True. A new `recent_context_included: bool` is recorded in `blockage_events.details`, guarded by `_write_edit_reasoning_summary_intent` (exception-model turns only, same guard as `intent_source`).

### CHANGE 2 — empty anchor turn falls back to the PREVIOUS turn's reasoning summary

**Problem.** 4 live blocks in one day, and 84 of 1316 historical Opus edits, had an anchored turn with NEITHER visible text NOR a reasoning summary, while the IMMEDIATELY PRECEDING turn's own reasoning summary described the edit (e.g. "I'll add a new test in `test_elevate_session.py` verifying that calls missing a session key return `missing_session_key`…").

**Rule** (`intent_validator.resolve_reasoning_summary_intent_source`, new optional `recent_context` param): when the anchor's own combined text is empty, consult ONLY the LAST element of `recent_context` (the single immediately-preceding turn — never an earlier one, even if that one is itself empty, which falls through to `"none"` exactly as if `recent_context` had never been supplied). If that prior turn's own combined text is non-empty, return `("prior_reasoning_summary", <prior turn's text>)`.

**Restricted to Write/Edit only by issue #154 item 2 (decided).** This is a function SHARED by both gates, so the first-shipped version let danger-bash get the fallback "for free" once it also threaded `recent_context` through — but that relaxed #93's deliberate "Bash is anchor-only" tightening and #139's stale-path anchor-only rule for an empty anchored Bash turn, and the user's approval for CHANGE 2 was based on Edit evidence only. Fixed with an EXPLICIT `allow_prior_turn_fallback: bool = True` parameter on the resolver — never an implicit tool-name check buried inside it. The Write/Edit gate passes `allow_prior_turn_fallback=True` explicitly; the danger-bash gate passes `allow_prior_turn_fallback=False` explicitly, restoring its pre-CHANGE-2 behavior (an empty anchored Bash turn still blocks with the no-visible-text notice, even when a usable prior turn exists). `build_danger_bash_prior_reasoning_summary_label()` and its template (`danger_bash_prior_reasoning_summary_label.md`) became fully orphaned once this landed and were REMOVED (Messi Anti-Orphan-Code) — danger-bash can never reach `intent_source == "prior_reasoning_summary"` any more, so `_RELAXED_BASH_INTENT_SOURCES` in `hook.py` is back to its original 2-tuple (`"reasoning_summary"`, `"visible_text"`).

**Reviewer notice (Write/Edit only)**: since the text shown as INTENT is now from a DIFFERENT (earlier) message, the reviewer is told explicitly — never silently — via a new `{prior_turn_note}` placeholder (loaded from `reasoning_summary_prior_turn_note.md`, only non-empty when `intent_source == "prior_reasoning_summary"`), inserted immediately before `{intent_text}` in `stage2_code_review_reasoning_summary.md`. `_build_stage2_prompt_reasoning_summary()` gained an `intent_source: Optional[str] = None` param threading this.

**Telemetry**: `_record_reasoning_summary_telemetry()` (`hook.py`, shared by both gates) accepts `"prior_reasoning_summary"` in its guard tuple and records `feedback_text = "[<reviewer>] Approved via relaxed intent: previous message's reasoning summary (intent_source=prior_reasoning_summary)"` — reachable only via the Write/Edit gate now.

**Deliberate behavior reversal — a pre-existing regression test was rewritten, not just extended.** `tests/test_issue_151_reasoning_summary_intent.py::TestWriteEditGateOpusReasoningSummary::test_adversarial_prior_turn_summary_does_not_satisfy_current_turn` used to assert the OPPOSITE of CHANGE 2 — "a DIFFERENT prior turn's reasoning summary must never leak into the current (signature-only) turn's relaxed intent" — written before this round existed, when that WAS the correct invariant. CHANGE 2 deliberately reverses it. The test is renamed `test_prior_turn_summary_now_deliberately_used_via_change2` with an explicit "SUPERSEDED by issue #151 round 3 CHANGE 2" docstring, now mocking the reviewer (the old version relied on the code path never reaching a real reviewer call — running it unmodified after CHANGE 2 landed leaked a REAL `codex exec` subprocess call past the test's mocking guard, caught by `tests/conftest.py`'s autouse leak-detection fixture) and asserting the prior turn's text IS now used, with the "PREVIOUS" notice present. Stage 2's own CHECK 0 (vagueness)/CHECK 1 (mismatch) judgment is the safety net against a genuinely unrelated/vague prior summary now — not a code-level block.

**Key files**: `src/pacemaker/constants.py` (`RECENT_CONTEXT_TERSE_MAX_CHARS`), `src/pacemaker/intent_validator.py` (`_mentions_file_basename_or_stem()`, `_should_include_recent_context()`, `resolve_reasoning_summary_intent_source()`'s `recent_context`/`allow_prior_turn_fallback` params, `_build_stage2_prompt_reasoning_summary()`'s `intent_source` param), `src/pacemaker/hook.py` (Write/Edit gate's terse-gating + `recent_context_included` telemetry + `allow_prior_turn_fallback=True`, danger-bash gate's `recent_context` threading + `allow_prior_turn_fallback=False` + narrowed `_RELAXED_BASH_INTENT_SOURCES`, `_record_reasoning_summary_telemetry()`'s new source), `src/pacemaker/prompts/pre_tool_use/reasoning_summary_prior_turn_note.md`.

**Tests**: `tests/test_issue_151_reasoning_summary_intent.py` — `TestShouldIncludeRecentContext` (the SSH terse line, F1/F2 live restore-intent cases now getting context, the `group_access_manager.py`-naming detailed intent, a long file-silent intent), `TestChange2PriorReasoningSummaryFallback` (unit tests on the resolver), `TestAllowPriorTurnFallbackParam` (3 tests — default allows it, `False` disables it even with a usable prior turn, `False` never affects the anchor's own non-empty text), `TestPriorReasoningSummaryPromptNotice` (3 tests), `TestChange1RecentContextTerseGatingHookWiring` (4 hook-level tests incl. the `recent_context_included` telemetry true/false pair and the F2 terse-restore-gets-context case), `TestChange2PriorReasoningSummaryHookWiring` (3 hook-level tests incl. the stale path), `TestDangerBashChange2PriorReasoningSummary` (2 tests, rewritten for issue #154 item 2 — an empty anchored Bash turn with a usable prior summary now BLOCKS, same as with an empty one).

### CHANGE 3 — Edit (and Write-over-an-existing-file) render a REAL unified diff, not an OLD/NEW block-pair view

**Problem.** A live false block: deleting `get_audit_logs` was done with `old_string` = "\<last line of the previous method\>\n\n\<the whole get_audit_logs method\>", `new_string` = "\<the SAME last line alone\>". Haiku read the #153 OLD/NEW block-pair view (`_build_edit_diff_view`, now REMOVED) as "the method was replaced by unrelated code", because splitting old/new into two separate blocks hid that their FIRST line was IDENTICAL. A real diff makes that shared line appear ONCE, unmarked (context), with only the method's real body rendered as `-` (removed) and no corresponding `+` (added) line for it at all.

**Design — single file read, shared between the diff and the surrounding-context section.** `intent_validator._read_target_file_for_review(file_path, _deadline=None, _db_path=None) -> (content, note)` is a NEW shared low-level reader (deadline → secret-like-path → isfile/size → read → secret-content-match, the SAME fail-safe order as #153's original inline logic) used by BOTH `_read_edit_target_file` (Edit, layers `old_string`-empty/occurrence checks on top, in the SAME original order) and `_build_write_diff_section` (Write, no `old_string` at all). `_build_edit_surrounding_context_section()` was refactored into a thin renderer over `_read_edit_target_file`'s `(content, idx, note)` tuple — accepts an optional `_precomputed` param so a caller-supplied read is reused, never a second file read; rendered output is BYTE-IDENTICAL to before this refactor for every pre-existing test/caller. `hook.py`'s Edit branch reads the file ONCE via `_read_edit_target_file` and passes that SAME tuple to both `_build_edit_diff_section` (for `proposed_code`) and `_build_edit_surrounding_context_section` (for the surrounding-context section) via `_precomputed`.

- **`_build_edit_diff_section(file_path, old_string, new_string, replace_all, max_chars=6000, _deadline=None, _db_path=None, _precomputed=None) -> str`**: when the file is available, diffs the FULL on-disk content against `content.replace(old_string, new_string[, 1])` (one occurrence, or all if `replace_all`, exactly matching Edit's own semantics) via `difflib.unified_diff(..., n=3, lineterm="")`, rendered `-`/`+`/unmarked with real line numbers from the hunk headers. **Fallback** (file unreadable/too-large/secret-like, or `old_string` not-found/ambiguous-without-`replace_all` — the EXACT SAME conditions the surrounding-context section already gates on): diffs `old_string`'s own lines against `new_string`'s own lines directly — this still surfaces the get_audit_logs shared-line problem even without the file, since `difflib` still renders an identical leading line as unmarked context rather than a spurious `+` line.
- **`_build_write_diff_section(file_path, new_content, max_chars=6000, _deadline=None, _db_path=None) -> (rendered, mode)`**: when Write targets a file that ALREADY EXISTS on disk (`os.path.isfile(file_path)` checked in `hook.py` before choosing which code path to take), renders a unified diff of current-on-disk vs. the new `content` when it FITS within `max_chars` (`mode="diff"`). A genuinely NEW file is UNAFFECTED — `hook.py` keeps showing `content` in full for it, exactly as always.
- Both diff builders share `_render_unified_diff()` (the `difflib` call, lines diffed WITHOUT trailing newlines via `splitlines()`/`lineterm=""` so every yielded line — headers included — is newline-free and joins safely with a single `"\n"`).

**Template wording (both templates, M3-locked, identical)**: PARTIAL CONTEXT WARNING's intro now says PROPOSED CODE is a UNIFIED DIFF (`-`/`+`/unmarked), not "shows ONLY the changed fragment (old string → new string)". "CHECK 2 and CHECK 3 apply only to the NEW code" became "apply only to added (`+`) lines — the NEW code introduced by this edit; unmarked context and removed (`-`) lines (the OLD code being replaced) are shown only to verify direction/scope". The closing direction-check sentence changed from "Use the OLD → NEW view above…" to "Use the diff's `-`/`+` markers above…", and CHECK 1's own direction-check sentence changed from "When OLD → NEW is shown…NEW does the opposite" to "When the diff is shown…the diff instead ADDS (`+`) what the intent says to remove — rather than REMOVING (`-`) it". `_build_edit_diff_view` (dead code after this change) was REMOVED from `intent_validator.py` along with its tests (`TestBuildEditDiffView`, Messi Anti-Orphan-Code).

**The NEW FILE WARNING wording deviation flagged here at 2.36.2 ship time was fixed by issue #154 item 1 — see that section below** for the conditional wording (`_build_write_file_warning_body()`, `write_case`) and the Write-cap regression fix (`_build_write_diff_section()`'s tuple return, full-content fallback instead of a truncated diff).

**Key files**: `src/pacemaker/intent_validator.py` (`_read_target_file_for_review()`, `_read_edit_target_file()` refactor, `_build_edit_surrounding_context_section()`'s `_precomputed` param, `_build_edit_diff_section()`, `_build_write_diff_section()`, `_render_unified_diff()`, `_cap_diff_text()`, `_EDIT_DIFF_MAX_CHARS`, `_UNIFIED_DIFF_HEADER`), `src/pacemaker/hook.py` (step 5's Write/Edit branching, `_edit_file_read` single-read reuse), both `stage2_code_review*.md` templates (M3-locked rewording).

**Tests**: `tests/test_issue_153_edit_context.py` — `TestBuildEditDiffSection` (incl. the live `get_audit_logs` shape, `replace_all`, not-found/unreadable fallback, cap, forged-tag-harmless, precomputed-read-reuse), `TestReadTargetFileForReview`, `TestBuildWriteDiffSection` (updated for issue #154 item 1's tuple return) — plus regression fixes to `test_check2_check3_scope_clarification_present` (new wording) and `tests/test_pre_tool_hook.py::test_passes_correct_args_to_validate_intent_and_code` (new diff-format expected string). `TestBuildEditSurroundingContextSection`'s existing tests pass UNCHANGED, proving byte-identical rendering through the refactor.

### CHANGE 4 — danger-bash Phase 2 judges only the destructive/dangerous part of a command

**Problem.** A live false block on `cp X backup && rm X && grep -rn refs src/ docs/ tests/` with the intent "delete the partial template". The reviewer correctly matched the `rm` against the intent, then BLOCKED anyway because the read-only `grep` wasn't mentioned in the intent.

**Fix**: a new externalized note, `build_danger_bash_destructive_scope_note()` (loading `danger_bash_destructive_scope_note.md`, Messi Rule 11, kept short per the #87 weak-verifier principle), inserted into the SHARED `bash_prompt` f-string in `hook.py` (right after `MATCHED DANGER RULES:`, before `VALIDATE:`) — since this part of the prompt is common code shared by BOTH the strict and relaxed wording variants (only `_bash_intro`/`_bash_validate_item1`/`_bash_mismatch_line`/`_bash_assistant_label` differ between them), a single insertion point covers both without duplicating the sentence into two independently-maintained copies. Text: "Judge alignment ONLY against the destructive or dangerous operations in this command — the ones that matched the danger rules above, or that modify or delete state. Extra read-only or non-destructive steps in the same command (searches, listings, reads, printing, copying to a backup) are NOT a mismatch just because the intent doesn't mention them." The `BLOCKED:` response-format requirement is unchanged.

**Fixed-string lock test needed NO update.** `TestDangerBashNonExceptionPhase2PromptFixedString::test_fixed_phase2_prompt_string` uses `in`-substring checks (a `.startswith()` on the intro/label, and an `in` check ending right after `"...treat as mismatch.\n\n"`), never an exact end-of-string/equality match — appending new content AFTER that point (which is exactly where the new note is inserted) does not break it. Verified: still passes unchanged.

**Key files**: `src/pacemaker/prompts/common/danger_bash_destructive_scope_note.md`, `src/pacemaker/intent_validator.py` (`build_danger_bash_destructive_scope_note()`), `src/pacemaker/hook.py` (insertion into the shared `bash_prompt` f-string).

**Tests**: `tests/test_issue_151_reasoning_summary_intent.py::TestDangerBashDestructiveScopeWording` (3 tests: strict variant states the rule, relaxed variant states the rule, strict wording otherwise unchanged incl. explicit `BLOCKED:` presence check).

### Issue #154 code-review follow-up (same 2.36.2 release)

Four items from the code review of the round above. Item 2 (restrict CHANGE 2 to Write/Edit) and the direction-word-clause removal are documented inline in CHANGE 1/CHANGE 2 above; items 1 and 4 are documented here.

**Item 1 — Write over an existing file was losing review coverage (MEDIUM).** Before #154, Write sent the full `content`, uncapped. CHANGE 3 (2.36.2's first pass) changed that to a diff capped to `_EDIT_DIFF_MAX_CHARS` (6000 chars, TAIL-kept like every other #153 cap) — but a reviewer probe planting a swallowed-exception violation at the TOP of a 22KB rewrite found it was NOT visible to the reviewer once the diff's tail-only truncation cut the head off.

- **Fix — fall back to the full content, not a truncated diff.** `_build_write_diff_section()` now returns `(rendered, mode)` where `mode` is `"diff"` (the diff fits within `max_chars`) or `"full_content"` (the file read was unavailable, OR the diff exceeds `max_chars`). When over-cap, it returns the FULL, UNCAPPED `new_content` — exactly the pre-#153 behavior — instead of `_cap_diff_text()`'s truncation. Diff mode applies ONLY when the diff actually fits; there is no partial/truncated-diff state any more for Write. (Edit's own diff builder, `_build_edit_diff_section()`, is UNCHANGED — see item 4.)
- **Conditional NEW FILE WARNING wording.** The section unconditionally said the file "does NOT exist on disk yet" and that PROPOSED CODE is its "COMPLETE, final content" — false for a Write over an existing file. Fixed with a `write_case: Optional[str]` parameter threaded end-to-end: `hook.py` computes it in step 5 (`"new_file"` when `not os.path.isfile(file_path)`; else `"existing_diff"` or `"existing_full_content"` from `_build_write_diff_section`'s `mode`) → `validate_intent_and_code(write_case=...)` → `_validate_normal_path`/`_validate_reasoning_summary_path` → `_build_stage2_prompt`/`_build_stage2_prompt_reasoning_summary` → `_build_write_file_warning_body(write_case)`, which loads one of three externalized snippets (Messi Rule 11): `write_file_warning_new_file.md` (the original text, default/`None`/`"new_file"`), `write_file_warning_existing_diff.md` ("ALREADY EXISTS on disk... a UNIFIED DIFF... NOT a new file being created"), `write_file_warning_existing_full_content.md` ("ALREADY EXISTS on disk... normally a diff — but this change is large enough... COMPLETE, final new content that will REPLACE the existing file's current content").
- **M3-lock-safe template surgery.** The NEW FILE WARNING section's header/separator lines (`⚠️  NEW FILE WARNING (Write operations)` / `════...`) stay STATIC and unconditional in both templates — only the inner body was replaced with a `{write_file_warning_body}` placeholder. This preserves the `_extract_section(..., ["NEW FILE WARNING"])` boundary every M3-lock test (in both `tests/test_issue_151_reasoning_summary_intent.py` and `tests/test_issue_153_edit_context.py`) relies on to find where the PARTIAL CONTEXT WARNING section ENDS. Both templates now render byte-identical wording for this section (the relaxed template's header previously lacked the `⚠️` emoji and ended on "...the intent excerpt above" instead of "...the declared intent" — both cosmetic pre-existing asymmetries, closed as a side effect of sharing one body source).
- **`build_danger_bash_prior_reasoning_summary_label()` and `write_file_warning_*` are unrelated** — the former was removed by item 2's Bash restriction (see CHANGE 2 above); the latter three are new files created by this item.

**Item 4 (optional, low) — the Edit diff's 6000-char TAIL-only cap can hide EARLY hunks of a large `replace_all` Edit.** `_build_edit_diff_section()` was deliberately left UNCHANGED by this round — it still calls `_cap_diff_text()`, which truncates an over-cap diff to its last `max_chars` characters with a marker, exactly as it did at 2.36.2 ship time. For an Edit whose `old_string`/`new_string` pair produces a diff with MANY scattered `replace_all` hunks (each occurrence of `old_string` replaced independently), a violation in one of the EARLIEST hunks is at risk of being silently cut off, the same class of problem item 1 just fixed for Write. This is a KNOWN, ACCEPTED gap, not a defect to silently patch — the user explicitly asked for a documentation note only ("Don't change behaviour"). If this surfaces as a real false-negative (analogous to item 1's reviewer probe, but for Edit), the fix would likely mirror item 1's: return `(rendered, mode)` from `_build_edit_diff_section()` too, falling back to the `old_string`/`new_string`-only fallback diff (already the existing not-found/unreadable fallback path) rather than a truncated on-disk diff — but that fallback loses the on-disk-file diff's context entirely, so the right tradeoff isn't obvious and deserves its own review round rather than a rushed fix here.

**Key files (item 1 additions)**: `src/pacemaker/intent_validator.py` (`_build_write_diff_section()`'s tuple return, `_build_write_file_warning_body()`), `src/pacemaker/prompts/common/write_file_warning_new_file.md`, `write_file_warning_existing_diff.md`, `write_file_warning_existing_full_content.md`, `src/pacemaker/hook.py` (`_write_case` computation in step 5, `write_case=` passthrough), both `stage2_code_review*.md` templates (`{write_file_warning_body}` placeholder).

**Tests (item 1)**: `tests/test_issue_153_edit_context.py` — `TestBuildWriteDiffSection` (rewritten for the tuple return: diff-fits case, over-cap-falls-back-to-full-uncapped-content with a planted "PLANTED_VIOLATION_AT_TOP" marker proving it survives, secret-like-path fallback), `TestBuildWriteFileWarningBody` (4 tests: default/`None`, `"new_file"`, `"existing_diff"`, `"existing_full_content"`), `TestHookWiringWriteOverExistingFile` (3 hook-level tests: small diff shows `existing_diff` wording, big rewrite exceeding the cap shows the planted-violation text AND the `existing_full_content` wording, new file keeps the default wording).

---

## Story #155 — Tool-first intent declaration: the `declare_intent` MCP tool (v2.37.0)

**Why.** On Claude 5.x models (Fable 5.1, Opus 5.5, Sonnet 5.5) a pre-edit note "longer than a sentence or two" becomes a progress-update `thinking` block — EMPTY under the default `display: "omitted"`, and subagents never get summaries — so a visible `INTENT:` line is often simply not there (live Sonnet 5.5: 0/10 at high, 0/11 at xhigh). Separately the current Write/Edit tool_use is frequently not flushed to the transcript when PreToolUse fires (#83/#91/#93/#139). The tool path avoids both: the agent calls a real tool, pace-maker's hooks record it, and the Write/Edit gate takes the intent from the store **before** any transcript work. Pilot 2 (guidance via SessionStart/SubagentStart only): 0 blocks in 16/16 runs.

**Moving parts**

| Piece | File | Role |
|---|---|---|
| MCP server | `src/pacemaker/intent_mcp/server.py` (`python -m pacemaker.intent_mcp`) | Stdlib-only newline-delimited JSON-RPC 2.0 over stdio. `initialize`/`tools/list`/`tools/call`/`ping`, ignores notifications; malformed/non-object messages get -32700/-32600, bad params/arguments -32602, unexpected errors -32603 — never a crash. **Pure acknowledger: stores nothing** (the hooks have `session_id` + `agent_id`; the server does not). Tool description: `prompts/mcp/declare_intent_tool_description.md` (Messi #11). Acks are tagged with the new `declare_intent_result` provenance channel. |
| Shared field rules | `src/pacemaker/intent_declarations/fields.py` (stdlib-only leaf) | `REQUIRED_FIELDS = (file_path, change, goal)`, `missing_required_fields()` (None/blank/non-string = missing), `normalize_file_path()` (abs realpath; relative resolved against the hook payload's `cwd`, else the process cwd), `truncate_text()`, `intent_declaration_tool_enabled()` + `load_user_config()` (the kill switch, read by both the hooks and the server), and `declare_inputs_from_tools()` (so `transcript_reader` imports this leaf and never the gate/store/sqlite — review L6, locked by a subprocess test). Used by BOTH the server and the recorder, so a call the server rejects is never stored. |
| Store | `src/pacemaker/intent_declarations/store.py` | `~/.claude-pace-maker/intent_declarations.db`, WAL, 2s busy timeout, `PACEMAKER_INTENT_DECLARATIONS_PATH` override, `RuntimeError` in test mode when unset (`tests/conftest.py::_guard_production_db` sets it — AC12). Tables `declarations(id, session_id, agent_key, file_path, change, goal, test_coverage, created_at)` and `chains(session_id, agent_key PRIMARY KEY pair, file_path, change, goal, test_coverage, updated_at)`. **Every access** (record/resolve/approve/reject) first deletes rows older than `INTENT_DECLARATION_TTL_SECONDS` (60 min). Each op is one `BEGIN IMMEDIATE` transaction, so two concurrent gates can never consume the same declaration. NOT read by the claude-usage monitor (no cross-process contract). |
| Hook wiring helpers | `src/pacemaker/intent_declarations/gate.py` | `record_declare_intent` (PostToolUse), `resolve_declared_intent` (Write/Edit gate), `record_outcome`, `same_message_fallback` (the whole same-message decision, so hook.py stays wiring-only — review L8), `same_message_declared_intent`, `render_intent_message`; re-exports `intent_declaration_tool_enabled` from `fields`. Store failures are logged at WARNING and mean "use the transcript path" — the one exception that is deliberately NOT swallowed is the test-mode `RuntimeError` from `resolve_db_path()`. |
| Names | `constants.py` | `DECLARE_INTENT_TOOL_NAMES` — ONE set: `mcp__pace-maker__declare_intent` (user-scope registration) and `mcp__plugin_claude-pace-maker_pace-maker__declare_intent` (plugin). Both verified live against Claude Code 2.1.286 (`claude -p --mcp-config` and a hook-free throwaway `--plugin-dir`). |

**`agent_key`** = hook `agent_id`, or `"main"` when absent (verified: PreToolUse AND PostToolUse payloads carry `agent_id` for subagent calls only). Subagents share the parent's `session_id`, so `agent_key` is what keeps main/subagent/sibling-subagent declarations apart.

**Recording** (`hook.run_hook()`, PostToolUse): right after stdin is parsed — before the slow pacing/Langfuse work — `record_declare_intent(hook_data, config)` inserts a `declarations` row when `tool_name` is one of the declare names and the required fields are non-blank. Never raises for a store failure.

**Gate order** (`hook.run_pre_tool_hook()`, Write/Edit, intent validation enabled, source files only — step "5b", between the proposed-code extraction and the transcript read), evaluated per `(session_id, agent_key)`, BEFORE the anchor wait:

1. **Declaration** for the normalized `file_path` → take the NEWEST as the intent and delete **every** declaration row for that session+agent+file (consume — review L1: a leftover older duplicate would otherwise satisfy a later "return to this file"), validate with it.
2. **Chain**: `chains[(session_id, agent_key)].file_path` equals the file → reuse that intent.
3. **Different file**: a chain for another file is deleted, whatever the outcome (different files need different declarations; returning to the first file needs a new one).
4. **Fallback**: the existing transcript path, unchanged — plus it now also accepts a `declare_intent` tool_use for the same file inside the anchored turn (the same-message case; surfaced by transcript_reader's additive `anchor_declare_intents` diagnostic, resolved in hook.py with the payload `cwd`).

Steps 1–2 skip BOTH `get_last_n_messages_for_validation` and `get_current_turn_message_for_validation`: no transcript read, no anchor wait. **Where the intent text comes from is the only thing that changes**: the declaration becomes `current_message_override` (and the sole entry of `messages`/the prose list, which is what Stage 2's prompt renders) as

```
INTENT: <change> in <file_path> — goal: <goal>
Test coverage: <test_coverage>        (only when given)
```

(each field collapsed to one line — `gate.render_intent_message`), so Stage 2, telemetry and every other check run exactly as for a text declaration. **TDD is the one deliberate difference (review M2):** for a tool-sourced intent Stage 1 must NOT scan the synthesized text — free-form change/goal wording ("covered by existing tests", "fix failing test: x") would satisfy `_has_tdd_declaration`, and the always-injected absolute path plus a bump verb could trip `_is_version_bump`. `validate_intent_and_code(tool_declared_tdd=…)` → `_regex_stage1_check(tool_declared_tdd=…)` is `None` for a text declaration (unchanged regex scan) and a bool for a tool intent: on a core path the verdict is decided ONLY by it — `True` iff a non-blank structured `test_coverage` was declared (any non-blank value, as with the text form; Stage 2 judges its substance) — and no version-bump exemption exists for tool intents (a version bump on a core path declares `test_coverage` like any other edit). The message still carries the `Test coverage:` line for Stage 2's benefit.

**Caps (review L4).** Each free-text field (`change`/`goal`/`test_coverage`) is truncated to `DECLARATION_MAX_FIELD_CHARS` (3000) at record time, and the rendered message to `DECLARATION_MAX_MESSAGE_LENGTH` (10000 — pinned equal to `transcript_reader.MAX_MESSAGE_LENGTH` by a test); truncation ends in `…`, nothing is rejected. A tool-sourced declaration always takes the STRICT path (`reasoning_summary_relaxed_text` forced to `None`) even for an exception-listed (#151) model.

**Outcome** (`gate.record_outcome`, called right after `validate_intent_and_code`): approved + tool-sourced → upsert the chain (this file + intent); **any** rejection (Stage 1, Stage 2, reviewer unavailable; transcript-sourced or not) → delete the agent's chain, so the retry needs a fresh declaration (the consumed declaration is already gone). Approved + transcript-sourced leaves the chain alone. An unexpected internal exception (the outer fail-closed handler) ALSO ends the chain when a tool/chain intent was in use (review L2: `_declared_intent`/`_declared_ctx` are declared before the `try:`; the call is guarded so the handler always returns its block).

**Telemetry**: blockage `details["intent_source"]` ∈ `declare_intent` | `declare_intent_chain` when the tool path was used (absent otherwise — transcript-sourced details stay byte-identical); `log_info("hook", "Intent validation: approved|rejected <tool> on <file> via declare_intent|declare_intent_chain|transcript")` records the path for every edit. The claude-usage monitor does not display the new values (out of scope).

**Kill switch**: `intent_declaration_tool_enabled` (DEFAULT_CONFIG, default `true`; only a real `true` counts — absent = on; one implementation, `fields.intent_declaration_tool_enabled`). **The MCP server honours it too (review M1)**: `tools/call` re-reads `~/.claude-pace-maker/config.json` with stdlib json on EVERY call (no restart needed; the home dir is resolved at call time, the same file the hooks read) and, when off, returns `isError` "declare_intent is disabled — write the INTENT: line in your response instead." (`prompts/mcp/declare_intent_disabled.md`, still tagged `declare_intent_result`) instead of "Intent recorded…"; `tools/list` keeps the tool listed but describes it as disabled (evaluated at connect time). A missing config means enabled; an unreadable/malformed one is reported on stderr and ALSO means enabled (the hooks fall back the same way). `false`: the recorder stores nothing, the gate skips steps 1–3 and the same-message fallback, block messages carry no hint, and the guidance is byte-identical to pre-#155. Danger-bash (Bash) is out of scope and unchanged.

**Guidance and block messages (AC11)**
- `hook.display_intent_validation_guidance(config)` (SessionStart + SubagentStart; both call sites pass their config; `None` = shipped defaults, deliberately NOT a hidden read of the real config file) PREPENDS `prompts/session_start/declare_intent_guidance.md` (the story's pilot-2 wording, character-exact) when the tool path is on; the #150 "C text" follows immediately after it, character-exact and NOT reworded (reasoning-extraction hazard — see "Issue #150"). `tests/test_issue_150_pilot_validated_wording.py` was updated deliberately: the "guidance opens with the C text" lock now asserts it with the kill switch OFF, plus a new test that the C text follows the tool paragraph by default.
- Stage-1 NO / NO_TDD block messages get `intent_validator.build_declare_intent_hint(file_path)` (`prompts/common/declare_intent_hint.md`, static text + `str.replace` of `<file_path>` — never PromptLoader `variables=`, whose `{{word}}` rescan crashes on real paths; the #150 item-1 hazard) when `validate_intent_and_code(declare_intent_hint=True)` — hook.py passes the kill-switch value. It is inserted BEFORE the #150 no-visible-text notice is prepended, so that notice still LEADS. Direct callers default to `False` (byte-identical).
- **Bug #159 — the hint now also rides every non-tool Write/Edit rejection, not only Stage 1.** Live: 4 of 5 Sonnet 5.5 subagents' first edit (no tool call, no visible text) was reviewed via the #151 `prior_reasoning_summary` fallback, rejected by Stage 2 as "Intent excerpt is too vague", and that rejection carried no hint — a 30–50 s round wasted before a hinted Stage-1 block led them to the tool. Now (`intent_validator._append_declare_intent_hint`, gated on the same `declare_intent_hint` flag = kill switch) there are THREE static templates in `prompts/common/` (Messi #11; built by `_load_static_hint`, `str.replace` of `<file_path>`, never `variables=`), each worded for what the agent must do next:
  - **Stage 1 (NO / NO_TDD) — `declare_intent_hint.md`**, "…then re-issue this call" (nothing was reviewed, re-issuing with a declaration is the fix). Unchanged except the "the `INTENT:` line described below" back-reference was dropped.
  - **Stage 2 rejection of a transcript-sourced intent — `declare_intent_hint_review.md`** (`build_declare_intent_review_hint`): "Address the review above. When you retry, declare the edit's intent first: …". The CODE was rejected, so the Stage-1 wording could be read as "declare, then re-issue the identical buggy call" (code review M1). Applies on the relaxed path (`reasoning_summary`/`visible_text`/`prior_reasoning_summary`) and the strict path, regardless of the reviewer's CLASSIFICATION (the live "too vague" blocks were CLEAN_CODE). Appended AFTER the reviewer-relay segment as its own `intent_validation_block`-tagged block; the reviewer's text is never altered and `raw_feedback`/governance `feedback_text` stay untagged and unhinted.
  - **Stage 2 rejection of a tool/chain-declared intent (`validate_intent_and_code(intent_from_tool=…)`, hook passes `_declared_intent is not None`) — `declare_intent_consumed_note.md`** (`build_declare_intent_consumed_note`): "Your declare_intent declaration was used by this rejected attempt; address the review above, then call `declare_intent` again before retrying." ("address the review above" rather than "fix the code" so it also fits a rejection of the intent itself.) True by #155's rules: the declaration was consumed and the chain deleted on any rejection, so the retry needs a fresh one (code review L2). Same tag/placement as the review hint.
  - **Reviewer-unavailable (empty response) of a tool/chain intent — `declare_intent_consumed_note_unavailable.md`** (`build_declare_intent_unavailable_consumed_note`): "Your declare_intent declaration was used by this attempt; call `declare_intent` again before re-issuing." The declaration is consumed and the chain ended there too, but nothing was reviewed, so there is no review to address. Appended (tagged `intent_validation_block`) after the unchanged `fail_closed_error` "No reviewer responded" message only when `intent_from_tool` AND the kill switch are on; `raw_feedback`/governance text untouched.
  - **Deferred "transcript timing race" block (`hook.py`) — `declare_intent_hint_deferred.md`** (`build_declare_intent_deferred_hint`): "Faster alternative that avoids this timing race entirely: …, then re-issue the same call." Worded as an alternative to that block's own "RE-ISSUE THE IDENTICAL…" (code review L1); a declaration is the cure because the gate reads declarations before any transcript work. Appended inside the block's single `intent_validation_deferred` tag.
  **No hint at all, by design:** reviewer-unavailable of a TRANSCRIPT-sourced intent, SDK-unavailable and internal-error blocks (infrastructure failures fixed by re-issuing unchanged, so a declaration hint would misattribute them), the kill switch off, and danger-bash. Stage-1 hints are otherwise unchanged (a tool-declared core-path intent missing `test_coverage` still gets the Stage-1 NO_TDD hint — it points at `test_coverage`). Tests: `tests/test_issue_159_declare_intent_hint_on_review_rejection.py` (asserts which wording each rejection kind gets); the hook-level `Harness` it shares with the #155 tests now lives in `tests/declare_intent_harness.py` (a flat, non-`test_` sibling module — the same import convention as `tests/test_external_cli_guard.py`'s `from conftest import …`).

**Installation**
- `install.sh` (`register_intent_mcp_server`, after `register_hooks`; skipped in plugin mode; NON-FATAL — without the tool the transcript path still validates every edit): runs `PYTHONPATH="$HOOKS_DIR" PYTHONSAFEPATH=1 <hook python> -m pacemaker.intent_mcp.registration add --python <hook python> --snapshot-dir "$HOOKS_DIR"` — the helper from the **installed snapshot** (issue #146 rules), with the interpreter chosen by `find_hook_python` (same preference order as the hook scripts' `find_python`, absolute path). `PACEMAKER_SKIP_MCP_REGISTRATION=1` skips it.
- `intent_mcp/registration.py`: `claude mcp add --scope user pace-maker -e PYTHONPATH=<snapshot> -e PYTHONSAFEPATH=1 -- <python> -m pacemaker.intent_mcp`. The server name comes BEFORE `-e` (variadic in the real CLI — otherwise the name is swallowed as an env value). Idempotent by remove-then-add: `claude mcp add` on an existing name FAILS, `claude mcp remove` of a missing name fails with "No MCP server named ..." (the only remove failure treated as success). Timeout-bounded; refuses a relative interpreter path or a snapshot without `pacemaker/__init__.py`. Verified against the real CLI in a throwaway HOME (`claude mcp get` → `✔ Connected`).
- `install.sh` also now copies `intent_mcp/` and `intent_declarations/` into the snapshot (`_copy_subdir`); `tests/test_intent_mcp_registration.py::TestInstallScriptWiring` guards that every `src/pacemaker/*/` package is in that list (the pre-existing, never-deployed legacy stub `hooks/` is the one listed exception) — a missing package would make the server crash on import from the snapshot.
- `migrate-to-plugin.sh` removes the user-scope registration AND the permission rule below (before it deletes the snapshot the helpers live in).
- **Allow-listing (review M3).** Without a `permissions.allow` rule, a non-bypass interactive/`-p` session gets a permission prompt for the tool (a subagent a denial) and every edit silently falls back to the transcript path — probed live against Claude Code 2.1.286 in a hook-free throwaway session ("Claude requested permissions to use mcp__pace-maker__declare_intent, but you haven't granted it yet"; with the rule it runs). `install.sh` (`allow_intent_mcp_tool`, right after the registration; non-fatal; skipped by `PACEMAKER_SKIP_MCP_REGISTRATION=1`) runs `python -m pacemaker.intent_mcp.permissions add --settings-file "$SETTINGS_FILE"` from the snapshot — the SAME settings file the hooks were registered in (`~/.claude/settings.json`, or the project's in local mode). `intent_mcp/permissions.py` appends `mcp__pace-maker__declare_intent` to `permissions.allow` idempotently, preserving every other key/entry, atomically (temp + `os.replace`, mode kept), and REFUSES (file untouched) anything not the expected shape (invalid JSON, non-object, `permissions` not an object, `allow` not a list); `remove` also prunes an `allow`/`permissions` it emptied, so add-then-remove restores the document. Tested on tmp files only (`tests/test_intent_mcp_permissions.py`).
- **A PLUGIN cannot ship the permission** (verified live: a plugin-level `settings.json` with `permissions.allow` and a `permissions` key in `plugin.json` are both ignored → denied; a USER settings rule works). So plugin users must add `mcp__plugin_claude-pace-maker_pace-maker__declare_intent` to their own `permissions.allow` (or approve the prompt once). Plugin-mode `install.sh` deliberately does not touch settings.json, so this is documentation, not automation.
- **Test isolation of install**: `tests/e2e/conftest.py` sets `PACEMAKER_SKIP_MCP_REGISTRATION=1` for the `test_install*.py` suites (review L7), so they never call the real `claude mcp` CLI.
- **Plugin**: declared INLINE in `.claude-plugin/plugin.json` `mcpServers` (`python3 -m pacemaker.intent_mcp`, `PYTHONPATH=${CLAUDE_PLUGIN_ROOT}/src`, `PYTHONSAFEPATH=1`) — NOT a repo-root `.mcp.json`: the repo root is also a Claude Code *project* root, where a root `.mcp.json` would be read as a project-scope server with an unresolvable `${CLAUDE_PLUGIN_ROOT}`.

**Known edges (accepted)**
- Edits to non-source files (the gate's step-4 bypass) never reach the declaration lookup: a declaration for such a file lingers until its TTL, and such an edit neither consumes nor deletes the agent's chain.
- **Deviation (e), accepted:** on the tool/chain path Stage 2 sees only the declaration — no prior conversation context and, because no anchor is read, no #153 sibling-edit section, so the split-multi-Edit false-block mitigation (a later sibling completing a fragment) does not apply there. (The #153 on-disk surrounding-context section still does: it comes from the file, not the transcript.) A "User permission to skip TDD" quote has to travel in `test_coverage`.
- **L5, accepted:** a same-message `declare_intent` tool_use is accepted from the transcript even if that call was itself denied or errored (the transcript fallback sees only the tool_use block, not its result); Stage 1/Stage 2 still review the edit against it.
- **L3, accepted** (also above): non-source-file edits neither consume declarations nor delete the chain.
- A chain reuses the OLD intent text for later edits of the same file; if Stage 2 judges a different change against it and rejects, the chain is deleted and the agent must re-declare (by design).
- Relative declared paths without a payload `cwd` resolve against the hook process's cwd.

**Tests**: `tests/test_intent_declaration_store.py`, `test_intent_declaration_gate.py`, `test_intent_mcp_server.py` (real server process over stdio), `test_intent_mcp_registration.py` (fake recording `claude` script — NOT named `claude`, the conftest guard — plus real server-from-snapshot and plugin-manifest handshakes), `test_issue_155_declare_intent_hook.py` (real `run_pre_tool_hook`/`run_hook`, only the Stage 2 reviewer mocked at `pacemaker.inference.resolve_and_call_with_reviewer`; the anchor is a spy that fails the test when it must not be consulted), `test_issue_155_guidance_and_hints.py`, `test_issue_155_edge_cases.py` (in-process server/store/gate edges), `test_intent_mcp_permissions.py` (allow-list merge on tmp settings files + install/migrate wiring + the e2e skip guard).

---

## Bug #157 — SubagentStart cancelled at its 10 s timeout: secret-regex compile, bounded Langfuse, guaranteed guidance (v2.37.1)

**Symptom.** SubagentStart was cancelled at its 10 s timeout in 69 of 85 runs since 9/30, so subagents got NO pace-maker `additionalContext` (no intent guidance, no `declare_intent` instructions, no provenance manifest). **Root cause (profiled on a copy of the real state):** every Langfuse push compiled ONE regex from ALL stored secrets in every hook process — 765 secrets / 5.23 MB (29 values over 50 KB: `SECRET_FILE` stores whole files) → ~7.5 s of CPU (`_build_secrets_pattern` = 92% of a cProfile). The module-level cache in `sanitizer.py` never survived from one hook process to the next. Same code path in SubagentStop (~8 s with a stored trace) and PostToolUse (~9 s of CPU per tool call). Control: SubagentStart with Langfuse off = 1.05 s.

**Fix 1 — one shared prefilter (`secrets/masking.py`).** `collect_strings(data)` (the string VALUES `mask_structure` would mask — dict keys are never masked) and `build_prefiltered_pattern(secrets, texts) -> (relevant, pattern)`: keep only the secrets that occur (`in`) in some payload string, in their original order, then build/compile from that subset. Output is byte-identical to full-store masking *by construction*: a secret that occurs in no payload string can never match, and dropping a never-matching alternative changes neither which alternative wins at any position (survivors keep their relative longest-first order) nor any count. Used by BOTH `sanitizer.sanitize_trace` (every Langfuse path: SubagentStart/Stop, PostToolUse, Stop, `flush_pending_trace`, backfill) and `intent_validator._mask_reviewer_prompt` (which keeps its OWN min-length-8 rule and calls the helper — the old inline copy is gone, anti-duplication; see "Issue #153 item 6" above). **No minimum secret length for Langfuse masking** — short secrets are still masked there exactly as before. The dead module-level pattern cache (`_get_cached_pattern`) was removed. Measured on the real-shaped store: 8.64 s → 0.002 s. `tests/test_issue_157_secret_prefilter.py`: equivalence vs the old full-store masking (overlapping/prefix secrets in every store order, secrets split across nested fields — masked in neither version —, regex metacharacters, unicode/multiline, tuples/None/scalars, protected `userId`, a seeded fuzz of 150 random traces), and a perf test on a synthetic 765-secret / ~5 MB / 29-over-50 KB store (clean ~6 KB trace well under 0.5 s). The store is built ONCE per class with one bulk insert (765 `create_secret()` connections alone cost ~30 s and blew the 120 s per-file limit).

**Fix 2 — no 3x full parse of the parent transcript (`langfuse/subagent_context.py`).** `orchestrator.handle_subagent_start` used `extract_task_tool_prompt`, `jsonl_parser.extract_user_id` and `parse_session_metadata`, each reading the whole file (1.45 s on a 52 MB parent). `read_subagent_start_context(path)` reads ONE bounded HEAD window (64 KB) and ONE bounded TAIL window (1 MB — the spawning Agent call is near EOF), parses each line once, and answers all three in a single pass with the OLD parsers' "latest wins" semantics — **the head may only answer `user_id` (legacy `auth_profile`) and a `session_start` model (review M1); the prompt and the last model come from the TAIL, and when the tail has none one full streaming pass finds the LATEST occurrence** (an older spawn prompt/model in the head must never beat a newer one outside both windows — realistic when the spawning Agent tool_use is not flushed yet at SubagentStart) (prompt = last non-empty Task/Agent prompt; model = last `message.model`/`entry.model`, a `session_start` model wins and stops the scan, `"unknown"` if none; user = first `auth_profile`/`profile` email, then — as before — the OAuth API via `jsonl_parser.get_user_email()`). Fallback: when the file is bigger than both windows and the prompt or model is still missing, ONE streaming pass over the whole file fills the gap (one parse instead of three). **No full-file fallback for user_id** (real transcripts carry no `auth_profile`; scanning for it would be a guaranteed cost for nothing) — the head window still covers the legacy layout. The old full-file functions remain (other callers). Tests: `tests/test_issue_157_subagent_start_context.py`.

**Fix 3 — guaranteed guidance delivery.** SubagentStart can still be cancelled, so: (a) as the VERY LAST step of `run_subagent_start_hook`, after its output was written, `subagent_guidance.record_start_completed(hook_data)` records "SubagentStart completed" for `(session_id, agent_id)`; a cancelled hook never reaches it. (b) In `run_hook` (PostToolUse), when the payload carries an `agent_id`, `_prepare_late_subagent_guidance` first does a CHEAP check — `agent_id` present, then a read-only "already completed or delivered?" (`IntentDeclarationStore.is_late_guidance_settled`: plain read, no write lock, no purge, a missing DB/table = unsettled), and only if delivery is still needed builds the text; so every subagent tool call after the first pays neither the text build nor a write transaction. **Nothing is claimed up front** (review L1): the atomic `claim_late_guidance` (True iff no completion record AND no earlier late delivery; recorded in the same transaction, so concurrent hooks of one agent cannot both claim) is called IMMEDIATELY BEFORE the single output is printed — a pacing delay (≤ 350 s) or a slow Langfuse push in between can no longer spend the one delivery without the output going out (a hook killed before the print leaves it unclaimed, so the agent's next tool call delivers; losing the claim race to a concurrent PostToolUse of the same agent means that one prints it, we print no duplicate). When claimed, the output prepends the SAME text SubagentStart would have sent — `_subagent_guidance_parts(config)`, the shared builder (abbreviated manifest always; intent guidance when `intent_validation_enabled`; the #155 kill switch changes it exactly as in SubagentStart) — to PostToolUse's single `additionalContext`. The CSA sibling banner is NOT part of it (needs session CSA state). The main thread (no `agent_id`) is never touched and never recorded.
- **Known limits (accepted, review L3/L4).** (L3) Late delivery fires on the first tool call that actually RAN: PostToolUse does not fire for a PreToolUse-BLOCKED call, so a subagent whose first action is a Write/Edit the intent gate blocks gets the block message (with its `declare_intent` hint) first, and the guidance on its next tool call that runs. (L4) There is a millisecond window between `record_start_completed` and process exit where a harness cancel would lose SubagentStart's output while the completion is already recorded — that agent then never gets a late delivery. The completion record is written last precisely to keep that window as small as possible.
- **Where the record lives:** table `subagent_guidance(session_id, agent_key, start_completed_at, delivered_at, updated_at; PK (session_id, agent_key))` in the **#155 declaration store's DB** (`intent_declarations.db`, `IntentDeclarationStore.mark_subagent_start_completed` / `claim_late_guidance`, thin wrappers in `intent_declarations/subagent_guidance.py`). **Why reuse it:** it already gives exactly what is needed — keys by (session, agent), a TTL purge on EVERY access, WAL + busy timeout for concurrent hook processes, the `PACEMAKER_INTENT_DECLARATIONS_PATH` override and the test-mode `RuntimeError` guard (so no new DB/env var/conftest line). **Why not the CSA registry** (`session_registry.db`): it is gated by `cross_session_awareness_enabled` (this delivery must work with it off), it is a live cross-process contract with the claude-usage monitor (not to be widened for this), and its agent rows are purged after 20 minutes. The table is NOT governed by `intent_declaration_tool_enabled`. Rows are purged after `SUBAGENT_GUIDANCE_TTL_SECONDS` = 24 h (deliberately ≫ the 60-minute declaration TTL: a long-lived subagent must not look "never started" and repeat the delivery).
- **Failure policy:** a store failure is logged at WARNING; `claim` → "do not inject", `record` → "not recorded" (worst cases: one duplicate guidance block, or one missed late delivery — never a crash, never an injection on every tool call). Config gates are respected: `enabled: false` → PostToolUse returns before anything; `intent_validation_enabled: false` → manifest only.
- Tests: `tests/test_issue_157_late_guidance.py` (store methods incl. TTL purge and 8-thread exactly-once; the record is written AFTER the output; a cancelled start records nothing; delivered once when start did not complete; not when it did; per-agent; main thread never; config gates; store failure; combined with other PostToolUse context in ONE JSON object).

**Fix 4 — time budget, defence in depth.** `bounded_call.run_with_deadline(fn, timeout, *args)` runs `fn` in a DAEMON thread and waits at most `timeout`: `(True, result)`, or `(False, None)` with the worker ABANDONED (Python cannot kill threads; it never blocks interpreter exit). `hook.SUBAGENT_HOOK_LANGFUSE_BUDGET_SECONDS` = 5.0 s from the start of the hook function bounds the Langfuse step of SubagentStart (`_handle_langfuse_subagent_start`) and SubagentStop (`_finalize_langfuse_trace`: trace finalize + parent pending-trace flush; the state cleanup that follows still always runs), because that step's own network timeouts (10 s trace push, 3 s OAuth profile) exceed the budget by themselves. 5 s leaves room for what it does not cover: the wrapper's `find_python` (~0.72 s, imports `claude_agent_sdk` — NOT changed in this bug), interpreter + imports (~0.5 s), state/CSA work and the output. SubagentStart's guidance output never depends on Langfuse. Accepted trade-off: a bounded hook may leave a Langfuse trace unpushed/unfinalized (and an abandoned worker may still finish writing its per-file state after the hook returned) — never a subagent without its guidance. PostToolUse (360 s budget) is deliberately NOT bounded. Tests: `tests/test_issue_157_time_budget.py`.

**Measured** (copy of the real state: 765 secrets / 5.23 MB, parent transcript 52 MB, fake local Langfuse, `HOME` = throwaway copy, `PYTHONUSERBASE=/home/jsbattig/.local`; python only — the wrapper adds ~0.72 s; "before" = the deployed v2.37.0 snapshot, "after" = this tree; no load):

| Hook | Before | After |
|---|---|---|
| SubagentStart | 9.70–9.87 s | 0.78–0.96 s |
| SubagentStop (stored trace) | 7.96 s | 0.49–0.51 s |
| PostToolUse (per tool call) | 9.29–9.93 s | 1.42–1.76 s |
| SubagentStart, Langfuse that NEVER answers (black hole) | would wait for the 10 s push timeout | 5.47 s, manifest + guidance delivered |

**Reading the evidence later:** a `WARNING ... exceeded its 5.0s budget` line in `pace-maker-*.log` means the Langfuse step was bounded (trace skipped), not that the hook failed; a `delivering subagent guidance late` INFO line means a SubagentStart did not complete and PostToolUse covered for it. Run the affected tests with the interpreter `run_tests.sh` picks (python3.11 here — `python3` is 3.9 without `claude_agent_sdk`, so Stage-2 tests fail-closed with "SDK not available" there; not a regression).

---

## Stop-Hook Validator Prompt — Async-Wait Design (Bug #87)

`src/pacemaker/prompts/stop/stop_hook_validator_prompt.md` is engineered for the WEAKEST verifier model in a competitive expression (haiku, codex-beast/gpt-oss-20b) — with `verifier1+verifier2->synth`, EITHER weak verifier failing blocks the stop, so prompt robustness matters more than eloquence.

**Structural invariants** (locked by `tests/test_stop_hook_prompt_async_wait.py`, 16 tests):
- **CORE PRINCIPLE section comes FIRST** (before the demoted "genuine still-running fallacy" section): waiting for ANY async mechanism (background task, subagent, scheduled wakeup) that re-awakens Claude → APPROVED. Weak models anchor on the first emphatic rule — it must be the permissive one.
- **Semantic rule, not phrase matching**: the signal-phrase lists are explicitly "illustrative examples (non-exhaustive)". Never make literal phrase matching the mechanism again — weak verifiers miss natural rephrasings ("awaiting validation results from the dual-validator" ≠ list entry "awaiting results").
- **Awaiting-user-input allowance** in WHEN TO ALLOW, with explicit tiebreak: genuinely user-owned choices (destructive/irreversible, ambiguous scope, approval gates) → ALLOW; deferring actionable work as a question ("shall I fix it?") → analysis-paralysis rule wins → BLOCK. Its justification is its own (user must reply before progress), NOT the async auto-rewake rationale.
- Preserved: tempo liveliness check, analysis-paralysis detection, unrecoverable-loop detection, E2E-evidence requirements (which defer to the async exception), `{conversation_context}` placeholder, APPROVED/COMPLETE:/BLOCKED: response formats.

**E2E verified live** (2026-07-11, full `codex-beast+haiku->codex-beast` pipeline, stop_hook context): async-wait ALLOWED, user-decision ALLOWED, genuine-unfinished BLOCKED, paralysis-as-question BLOCKED (haiku caught it after codex-beast false-approved — the dual-verifier design compensating for a flaky weak model).

---

## Competitive Review Pipeline

**Syntax**: `hook_model = "m1+m2[+m3]->synthesizer"` (2-3 verifiers + 1 synthesizer)

**Supported models**: **any token accepted by `inference/model_aliases.is_known_model()`** — not the short list this file used to print (which omitted every agy and codex-profile token and contradicted the Story #74/#75 sections below). That means:
- the full `KNOWN_MODELS` set: `auto`, `sonnet`, `opus`, `haiku`, `fable`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.5`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gemini-flash`, `gemini-pro`, plus the 11 `agy-*` tokens;
- the short aliases: `gpt-5`, `gpt`, `codex`, `gem-flash`, `gem-pro`;
- any dynamic `codex-<profile>` token (shape-validated only — see the Codex Profile Provider section).

**Short aliases**: gem-flash→gemini-flash, gem-pro→gemini-pro (accepted at CLI, stored canonically)

**Key file**: `src/pacemaker/inference/competitive.py`

**Wiring**: `resolve_and_call_with_reviewer()` in `registry.py` detects `+` in hook_model and delegates to `run_mechanical()` (Story #77: renamed from `run_competitive`)

### Story #77 (B2) — Mechanical N-verifier runner

**Decision computed in code, not by LLM**: `run_mechanical()` replaces the old `run_competitive()`. The synthesizer is demoted to a message-only formatter and can NEVER flip the verdict.

**Algorithm**:
1. Dispatch all verifiers in parallel via `_dispatch_reviewers()`.
2. Zero survivors → return `("", expression)` — gate semantics handle fail-open/closed (stop: fail-open to avoid infinite loop; pre-tool: fail-closed via `verdict_passes("")=False`).
3. Evaluate each survivor with `verdict_passes_for_context(resp, call_context)`.
4. **Mechanical decision (in code)**:
   - Pre-tool gate: ALL verifiers must respond AND pass → APPROVED (fail-closed; missing verifier = FAIL).
   - Stop gate: all present survivors must pass (missing verifiers ignored); zero survivors → `""` (OQ-1 fail-open).
5. PASS → return `("APPROVED", expression)`.
6. FAIL → build message from failing survivors only:
   - Exactly 1 failing → raw feedback, synthesizer NOT called.
   - 2+ failing → synthesizer called to FORMAT the message (message-only; cannot decide).
   - Synthesizer error/timeout/empty → concatenate raw failing feedbacks.
   - Edge case (pre-tool, missing verifier, all present passed) → `"a required verifier did not respond (fail-closed)"`.
7. Pass the message through `_strip_leading_blocked_prefix(message)` **first** (issue #88 — verifiers routinely emit their own leading `BLOCKED:`; without the strip the result reads `BLOCKED: BLOCKED: ...`), **then** return `("BLOCKED: " + stripped, expression)`. The `BLOCKED:` prefix is applied mechanically; synthesizer output is only the message body.

**Synthesizer-cannot-flip guarantee**: `BLOCKED:` prefix is hardcoded in `run_mechanical()`. Even if the synthesizer returns `"APPROVED"`, the result is `"BLOCKED: APPROVED"` which `has_block_marker()` reads as blocked. The synthesizer can NEVER override a FAIL decision.

**Failure modes** (Story #77 behavior):
- All verifiers pass → `APPROVED` (synthesizer NOT called)
- 1 verifier fails (non-positive response) → `BLOCKED: <raw feedback>` (synthesizer NOT called)
- 2+ verifiers fail → `BLOCKED: <synthesizer-merged message>` (synthesizer called to format only)
- Synthesizer fails/times out/returns empty → `BLOCKED: <concat raw feedbacks>`
- Missing verifier (pre-tool infra failure) → `BLOCKED: a required verifier did not respond (fail-closed)`
- Missing verifier (stop gate) → IGNORED (survivor-only evaluation)
- Zero survivors (pre-tool) → `""` → `verdict_passes("")=False` → gate blocks
- Zero survivors (stop) → `""` → `parse_sdk_response("")→{"continue": True}` → fail-open (OQ-1)

**⚠️ SUPERSEDED BY ISSUE #131 — the pre-tool "ALL verifiers must respond AND pass" rule above and the "Missing verifier (pre-tool infra failure) → BLOCKED" failure mode are NO LONGER CURRENT.** Both gates now share the identical rule `overall_pass = all(passed)` — a verifier that raised `ProviderError` (or timed out) is an INFRASTRUCTURE FAILURE, not a verdict, and is never counted as a negative vote. Only a responder's own `BLOCKED:` blocks. Zero survivors is UNCHANGED (still blocks — nobody reviewing is no validation, which is different from degraded validation). The dead "all present passed but a verifier was missing" BLOCKED branch and its `"a required verifier did not respond (fail-closed)"` message were deleted (Messi Rule 12 — unreachable once `overall_pass` is `all(passed)`); do not resurrect that string. Root cause was 44 blocks/~18h under `haiku+gpt-5.6-terra->codex-beast` where the responding verifier approved but a flaky `gpt-5.6-terra` still caused a fail-closed block.

**Current truth table (issue #131)**:
| Situation | Result |
|---|---|
| All verifiers respond, all pass | APPROVED |
| All respond, ≥1 returns `BLOCKED:` | BLOCKED (their feedback) |
| Some respond, all responders pass | **APPROVED, recorded as degraded** |
| Some respond, ≥1 responder returns `BLOCKED:` | BLOCKED |
| Zero survivors | BLOCKED (unchanged — `""` → gate semantics). **Since #142** the pre-tool gates no longer relay that `""` as a blank "reviewer" rejection: they block with a pace-maker-authored `format_tag(..., "fail_closed_error")` message, `build_reviewer_unavailable_message()`: "No reviewer responded (<provider>: <reason>; …) — infrastructure failure, NOT a rejection — re-issue". The block is recorded under the new blockage category **`intent_validation_reviewer_unavailable`** ("Reviewer Unavailable"), with `failed_providers` in its details. `run_mechanical()` / `resolve_and_call_with_reviewer()` fill `_degradation` (`zero_survivors=True`, `failed_providers`) on this path. The Stop hook is unaffected: it calls `resolve_and_call()`, which passes no `_degradation`, and `""` still fails OPEN there. **Cross-repo contract:** the claude-usage monitor lists every pace-maker blockage category in `KNOWN_BLOCKAGE_CATEGORIES` (`claude_usage/code_mode/pacemaker_integration.py`), including this one and `_bug`/`_deferred`. That was fixed in claude-usage 2.19.4 (#7, commit `5d00cd7`). Unknown categories fall back to a humanized label and still count toward Total. When you add a category to `BLOCKAGE_CATEGORIES` here, add it there too, so it gets a proper label. |

**Degraded-approval telemetry (issue #131)**: `run_mechanical()` and `resolve_and_call_with_reviewer()` (registry.py) both take an optional `_degradation: Optional[dict] = None` out-param — same idiom as `_diagnostics`/`_outcome` in `transcript_reader.py` (additive, backward-compatible; `None` is a no-op for existing callers). Populated `{"degraded": True, "failed_providers": {model: reason}, "context": "competitive"|"single_model_fallback"}` when (a) a competitive verifier failed to respond but the survivors' verdict still APPROVED, or (b) the single configured provider raised `ProviderError` and the Anthropic SDK fallback served instead. `_call_stage2_validation()` (`intent_validator.py`) threads it through and `validate_intent_and_code()` attaches it as `result["degradation"]` on the APPROVED path. `hook.py`'s new shared helper `_record_degraded_review_telemetry(degradation, reviewer, session_id)` (defined just above `run_pre_tool_hook`, Messi Anti-Duplication) records a `DG` activity event (status `"yellow"`) and a `DG` governance event (`feedback_text` names the failed verifier(s) and reason) — wired at both the Write/Edit gate's approved branch and the danger-bash Phase 2 approved branch. `DG` is registered in `record_activity_event()`'s docstring (`database.py`) and the Activity Indicators table in `docs/ARCHITECTURE.md`. **Not yet implemented**: the claude-usage monitor display of a degraded-vs-fully-verified approval (tracked in issue #131 itself as follow-up; touches `claude-usage-reporting`, a separate repo).

**Issue #132 (prerequisite for #131's reason text)**: `CodexProvider.query()`'s empty-stdout branch (`codex_provider.py`) previously discarded `stderr` entirely — `raise ProviderError("Codex CLI returned empty response")` — while the non-zero-exit branch reported it. Since codex writes diagnostics to stderr while leaving stdout empty on the observed failure (exit 0, `stdout_len=0`, stderr carrying `"ERROR: Reconnecting... 2/5 ... 404 Not Found"`), the empty-response path was undiagnosable. Fixed: both branches now share a single `stderr_preview` computed once after `subprocess.run()` (via a new `_STDERR_PREVIEW_CHARS = 300` module constant, replacing a duplicated magic number), and the empty-response message now includes both `stderr_preview` and `returncode`: `"Codex CLI returned empty response (exit {returncode}): {stderr_preview}"`. The literal substring `"empty response"` is preserved — `tests/test_codex_profile.py::test_profile_mode_empty_stdout_raises_provider_error` and `tests/unit/test_inference_provider.py::test_codex_provider_raises_on_empty_response` still match on it unchanged.

**Synthesizer prompt**: Externalized to `src/pacemaker/prompts/common/mechanical_failure_synthesis.md` (Messi Rule 11). Instructions: merge failing reviews into ONE message; do NOT output APPROVED/BLOCKED/COMPLETE; you are a FORMATTER not a judge.

**Tag format**: `[expression]` in feedback_text (no REVIEWER: prefix), e.g. `[gpt-5+gemini-flash->sonnet]`

**CLI**: `pace-maker hook-model gpt-5+gemini-flash->sonnet` — validates via `parse_competitive()`, stores canonical form

**Reviewer verdict logging**: Each reviewer's raw response is logged at DEBUG level (first 300 chars via `MAX_REVIEW_LOG_CHARS`) via `log_debug("competitive", f"Reviewer {model} verdict: ...")`.

**Timeouts: derived from one budget (#108).** Everything flows from `PRE_TOOL_HOOK_TIMEOUT_SECONDS` in `constants.py`, which is **180** since issue #152 (it was 120, raised from 60 on 2026-09-21):
- `PRE_TOOL_REVIEW_BUDGET_SECONDS` = 180 - 10 (safety margin) = 170.
- `REVIEWER_WAIT_TIMEOUT_SEC` = `int(170 * 0.7)` = **118s** — NOT 119: Python's `170 * 0.7` evaluates to `118.99999999999999` (float rounding), and `int()` truncates toward zero, so the formula's actual output is 118, not the "obvious" 119 a hand calculation gives. Verified by direct interpreter execution; treat the code as authoritative over mental arithmetic here.
- `SYNTHESIS_TIMEOUT_SEC` = 170 - 118 = **52s**.
- The gate's single `_gate_deadline` clamps the anchor wait and the review, so the gate always answers before the harness kills it.

**The PreToolUse hook timeout is configurable per hook, not hard-coded by Claude Code.** 60s is only Claude Code's default; each hook entry's `"timeout"` in `~/.claude/settings.json` overrides it. Current values: Stop = 120s (unchanged by #152 — a separate registration), **PreToolUse = 180s**, PostToolUse = 360s, SessionStart / SubagentStart / SubagentStop = 10s.
- **Four places must agree:** `PRE_TOOL_HOOK_TIMEOUT_SECONDS`, `install.sh` (the PreToolUse `"timeout"`), `hooks/hooks.json`, and the live `~/.claude/settings.json`.
- `tests/unit/test_pretool_budget.py` asserts that `install.sh` matches the constant, AND (added in #152) that `hooks/hooks.json`'s PreToolUse `"timeout"` matches it too (`test_hooks_json_timeout_matches_constant`) — closing a previously-unchecked fourth-place gap.
- **Raise `settings.json` first.** If the code budget is higher than the harness timeout, the hook is killed mid-review, and **a killed PreToolUse hook is a silently unvalidated tool call**, not a block.
- **Issue #152 status: fixed in code, pending deploy.** `constants.py`, `install.sh`, and `hooks/hooks.json` were bumped to 180 in this change; the live `~/.claude/settings.json` and the installed hook snapshot were deliberately NOT touched (no `./install.sh` run) — a separate deploy step applies both after user approval.

**Single-model path is now deadline-aware too (issue #152).** Before this fix, only the competitive (`m1+m2->synth`) path threaded `_deadline` into its reviewer calls — the single-model branch of `resolve_and_call_with_reviewer()` (`inference/registry.py`) ignored `_deadline` entirely, so a hung/slow primary provider (e.g. codex hitting its own hardcoded 120s subprocess timeout) plus an unbounded Anthropic SDK fallback on top could exceed the hook's own budget outright — a live replay showed a `codex-beast` review taking 133.6s against a 120s hook timeout, i.e. a silently unvalidated tool call once the harness killed the hook.
- **`_remaining_budget(_deadline, margin)`** (`registry.py`) — `max(0.0, _deadline - time.monotonic() - margin)` when `_deadline` is given, else `None`. Mirrors `competitive.py`'s own `_budget()` clamp idiom.
- **`PROVIDER_TIMEOUT_SAFETY_MARGIN_SECONDS = 5.0`** — reserved off the remaining budget before it's handed to a provider as its subprocess/SDK timeout.
- **`MIN_SDK_FALLBACK_BUDGET_SECONDS = 15.0`** — minimum remaining budget (after the margin) required to even attempt the Anthropic SDK fallback after the primary provider fails.
- The primary single-model `provider.query(...)` call now passes `timeout=_remaining_budget(_deadline, PROVIDER_TIMEOUT_SAFETY_MARGIN_SECONDS)`.
- **`InferenceProvider.query()`** (`provider.py`) gained an optional `timeout: Optional[float] = None` kwarg, threaded through all four concrete providers. `None` (the default, used by every pre-#152 caller including the Stop hook and the competitive path's own per-reviewer calls) preserves each provider's old hardcoded behavior byte-identically:
  - `CodexProvider`/`GeminiProvider`/`AgyProvider` clamp their `subprocess.run(timeout=...)` to `max(0.0, min(<own 120s ceiling>, timeout))` when `timeout is not None` — shrink-only, floored at 0.0 so a negative value can never reach `subprocess.run`.
  - `AnthropicProvider.query()` wraps the whole `_query_async(...)` coroutine in `asyncio.wait_for(coro, timeout=timeout)` when `timeout is not None`, re-raising `asyncio.TimeoutError` as `ProviderError`.
- **Fail-closed skip-fallback rule**: before attempting the Anthropic SDK fallback after the primary provider raises `ProviderError`, the registry computes `_fallback_remaining = _remaining_budget(_deadline, PROVIDER_TIMEOUT_SAFETY_MARGIN_SECONDS)`; if it's below `MIN_SDK_FALLBACK_BUDGET_SECONDS`, the fallback is skipped entirely (never attempted) and the call fails closed via the **existing #142 zero-survivor / reviewer-unavailable path** — `_degradation["zero_survivors"] = True`, `_degradation["failed_providers"] = {hook_model: str(e)}`, returning `("", _REVIEWER_UNKNOWN")` — which `hook.py` turns into `build_reviewer_unavailable_message()` under category `intent_validation_reviewer_unavailable`, exactly as it already does for the competitive zero-survivors case. No new hook.py code was needed. Otherwise the fallback runs with `timeout=_fallback_remaining`.
- **The Stop hook path (`resolve_and_call()`) is completely unaffected** — it has no `_deadline` parameter at all (by design, and must never grow one), so every provider call it makes gets `timeout=None` automatically and behaves exactly as before #152: it never fails closed, it keeps its pre-existing fail-open semantics.
- **Codex stderr preview, issue #132 follow-up.** `codex_provider.py`'s `stderr_preview` used to take the FIRST 300 chars of raw stderr — but every codex-beast run prints harmless diagnostic lines first (`codex_models_manager::manager: failed to refresh available models...`, `codex_rmcp_client::oauth::refresh_transaction`), which on a real empty-response failure consumed the entire preview budget and hid the actual reason. `_BENIGN_STDERR_LINE_PATTERNS` + `_filter_benign_stderr_lines()` now drop lines matching those two known-benign substrings first, then `stderr_preview` takes the LAST 300 chars of what remains (a real error is more likely to be the last thing printed). The literal `"empty response"` substring existing tests match on is unchanged.
- Tests: `tests/unit/test_issue_152_deadline_aware_provider.py` (22 tests — provider-level timeout clamps for all 4 providers, the single-model deadline-clamp/fail-closed/fake-clock-never-exceeds-budget suite, the Stop-path-unchanged suite, and the stderr filter).
- **Code-review follow-up: the danger-bash Phase 2 gate was ALSO missing `_deadline` (HIGH, fixed same day).** The Write/Edit gate's Stage 2 call already passed `_deadline=_gate_deadline`, but the danger-bash Phase 2 call (`hook.py` ~3364-3377, inside the PreToolUse Bash-validation gate) did not — so `_remaining_budget()` always returned `None` for Bash reviews, the codex subprocess kept its unclamped 120s timeout, and the Anthropic SDK fallback ran with no timeout and no #152 minimum-budget check at all. Worst case still exceeded the 180s hook budget for a **dangerous Bash command**, not just an edit. Fixed by adding `_deadline=_gate_deadline` to that call — no new branch logic was needed, since it reuses the exact same registry.py deadline-skip machinery and the exact same hook.py `elif not response:` reviewer-unavailable path that issue #142 already built for this gate. Tests: `tests/test_issue_152_danger_bash_deadline.py` (2 hook-level tests — proves the call receives a real, non-None `_deadline`; proves the gate fails closed under `intent_validation_reviewer_unavailable` with the fallback provider **never even attempted** when the forced remaining budget is below `MIN_SDK_FALLBACK_BUDGET_SECONDS`).
- **Known follow-up, deliberately NOT done (competitive-path `timeout` passthrough).** The competitive (`m1+m2->synth`) path in `inference/competitive.py` already threads `_deadline` end-to-end for its own `ThreadPoolExecutor`/`futures_wait` budget accounting, but its individual per-reviewer provider `.query(...)` calls do **not** pass the new `timeout=` kwarg from #152 — each reviewer thread's own CLI subprocess/SDK call still uses the provider's hardcoded default timeout. Practical effect: when `futures_wait()` times out, the *gate* correctly moves on with whatever survived, but an individual abandoned reviewer thread (still blocked in a subprocess or `asyncio.run()`) can keep running past the gate's own deadline, all the way to interpreter exit. This was intentionally left out of this round's fix — the current production config is single-model, not competitive, so the single-model path (already fixed) is where the real exposure was. If the config is ever switched to a competitive expression, revisit `_call_single_reviewer()` in `competitive.py` and thread `timeout=` the same way `registry.py`'s single-model path now does.

**Why the budget was raised (issue #147).** On 9/20–9/21, with a 35s reviewer wait, there were **195–225 reviewer timeouts per day**, against 0–3 per day at 60s. That caused ~100 degraded approvals and ~50 blank blocks (#142) per day.
- **Root cause:** the Claude reviewer (`AnthropicProvider`, via `claude_agent_sdk`) started a **full user Claude Code session** for every review. That session loaded `settings.json` hooks (pace-maker's own hooks, so every review also wrote Langfuse state and `usage.db` rows), plugins, and 11 MCP servers, including unreachable ones.
- **Cost:** a one-word prompt took **20–49s**; isolated, it takes **2.5–3.9s**.

**⚠️ The Claude reviewer MUST stay isolated.** `anthropic_provider._build_options()` passes `setting_sources=[]` (→ `--setting-sources=`) and `strict_mcp_config=True` (→ `--strict-mcp-config`) for both the primary and the limit-fallback call.
- **Do not drop these.** Login still works without them, because auth comes from the CLI credential store, not from settings. Effort is passed explicitly.
- Locked by `tests/test_issue_147_reviewer_isolation.py`.
- `mcp_servers={}` is the SDK default and adds no flag; `--strict-mcp-config` alone does the MCP isolation.

**Status display**: `pace-maker status` shows full expression (e.g. `opus+gpt-5->haiku`) in ANSI blue — no separate "reviewers:" breakdown line.

**claude-usage display**: Hook Model shows `comp` in `bright_blue`; governance feed shows `[Comp]` in `bright_blue` for competitive expressions.

**Concurrency**: `ThreadPoolExecutor` with `futures_wait(timeout=REVIEWER_WAIT_TIMEOUT_SEC)` — partial results preserved on timeout; `executor.shutdown(wait=False)` avoids blocking on in-flight threads.

**AgyProvider label fix** (Story #77): `_call_single_reviewer()` now returns the verbatim model alias as label for AgyProvider (e.g. `"agy-flash-high"`). Previously fell through to `"anthropic-sdk"` — fixed by adding `elif isinstance(provider, AgyProvider): label = model`.

**Tests**: `tests/test_mechanical.py` (68 tests) — migrated from `tests/unit/test_competitive.py` + full Story #77 truth tables, synthesizer-cannot-flip safety test, stop-gate matrix, N=2/N=3 coverage.

---

## Memory Localization

**Story #65**: Makes Claude Code per-project memory git-portable by symlinking the central memory folder to a repo-local `.claude-memory/` directory that developers commit to git.

### Flows
- **Flow A — SessionStart auto-link** (`link_if_local_exists`): If `.claude-memory/` exists at the git root, the SessionStart hook replaces `~/.claude/projects/<encoded>/memory/` with a symlink pointing at it. Local always wins — any stale central content is renamed to `memory.bak_localize`, the symlink is created, and the backup is deleted. Rollback on OSError restores the backup.
- **Flow B — CLI seed** (`pace-maker localize-memory`): Copies central memory contents into `<repo>/.claude-memory/`, then replaces central with symlink. Refuses if `.claude-memory/` already exists (except idempotent correct-symlink case).
- **Flow C — CLI unlink** (`pace-maker memory-localization unlink`): Removes the symlink and copies `.claude-memory/` contents back to the central folder. Leaves the repo folder in place — user can `git rm -r .claude-memory` to remove from repo.

### CLI Commands
```bash
pace-maker localize-memory                      # Flow B — seed fresh
pace-maker memory-localization on|off|status   # Config gate
pace-maker memory-localization unlink          # Flow C — reverse
```

### Architecture

**Path discovery** — no re-implementation of Claude Code's encoding:
- Flow A uses `transcript_path` from SessionStart `hook_data` → `Path(transcript_path).parent / "memory"`
- Flow B/C scan `~/.claude/projects/*/*.jsonl` matching the project's cwd

**Classification states** (`classify_central`): `missing`, `correct_symlink`, `wrong_symlink`, `regular_folder`, `permission_denied`, `unknown`.

**Safety invariants**:
- `assert_safe_to_destroy(path)` requires `path` under `CENTRAL_BASE` and `path.name == "memory"` before any rmtree
- `replace_with_symlink_atomic` renames to `.bak_localize`, symlinks, rmtree's the backup — on OSError the rename is reversed
- `_is_under` uses canonicalize-parent-only strategy so symlink leaves do not escape the boundary check

**Concurrency**: Optimistic — on `FileExistsError`, re-classify; if now `correct_symlink` return `raced_but_ok`.

**Symlink target**: Absolute, via `local.resolve()`.

**Nudge injection**: On success states (`linked_fresh`, `replaced_with_symlink`, `relinked`, `already_linked`, `raced_but_ok`), hook emits JSON `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "<nudge>"}}` to stdout — same channel as the SubagentStart CSA banner.

### Config Gate
```json
{ "memory_localization_enabled": true }
```
Default `true`. Checked as the first operation in `link_if_local_exists`. When `false`, Flow A returns `("disabled", None)` immediately — no filesystem operations.

### Test Isolation — `PACEMAKER_CENTRAL_BASE`
Mirrors `PACEMAKER_SESSION_REGISTRY_PATH` pattern. `core.py` raises `RuntimeError` when `PACEMAKER_TEST_MODE=1` and the env var is unset, preventing accidental pollution of real `~/.claude/projects/`.

`CENTRAL_BASE` is resolved dynamically per access via module-level `__getattr__` so per-test `monkeypatch.setenv` changes take effect. All internal references use `_resolve_central_base()` to bypass stale caching.

`tests/conftest.py` provides shared fixtures: `ml_central_base`, `ml_repo`, `ml_enc_dir`, `ml_transcript_path`, `ml_local_memory`.

### Key Files
- `src/pacemaker/memory_localization/core.py` — path helpers, classification, atomic replace, Flow A/B/C entry points
- `src/pacemaker/memory_localization/__init__.py` — public API exports
- `src/pacemaker/memory_localization_cli.py` — `localize_memory_cmd`, `memory_localization_cmd` CLI handlers
- `src/pacemaker/hook.py` (~lines 406-441) — SessionStart wiring (fires for `source` in startup/resume only)
- `src/pacemaker/user_commands.py` — command patterns (25, 26) and dispatch
- `install.sh` line 630 — copies `memory_localization/` subdir to `~/.claude/hooks/pacemaker/`
- `tests/test_memory_localization_*.py` — 33 tests (classification, linking, seed/restore)

---

## Antigravity CLI (agy) Provider

**Story #72**: Adds `agy` CLI as an inference provider for hook model validation, enabling Gemini Flash/Pro thinking modes, GPT-OSS, and Claude-via-agy as reviewers.

### CLI Invocation Pattern

```
agy --print <full_prompt>                         # bare "agy" — no --model flag
agy --print <full_prompt> --model "<model_name>"  # all agy-* variants
```

System prompt is embedded directly in the prompt text (not a separate flag):
```
SYSTEM INSTRUCTIONS:
<system_prompt>

USER REQUEST:
<user_prompt>
```

### Model Name Table (authoritative)

| pace-maker alias | agy --model argument | Notes |
|---|---|---|
| `agy` | (no --model flag) | agy's own default |
| `agy-flash` | `Gemini 3.5 Flash (Medium)` | default thinking level |
| `agy-flash-low` | `Gemini 3.5 Flash (Low)` | |
| `agy-flash-medium` | `Gemini 3.5 Flash (Medium)` | |
| `agy-flash-high` | `Gemini 3.5 Flash (High)` | |
| `agy-pro` | `Gemini 3.1 Pro (High)` | defaults to high |
| `agy-pro-low` | `Gemini 3.1 Pro (Low)` | |
| `agy-pro-high` | `Gemini 3.1 Pro (High)` | |
| `agy-gpt-oss` | `GPT-OSS 120B (Medium)` | |
| `agy-sonnet` | `Claude Sonnet 4.6 (Thinking)` | Claude via agy |
| `agy-opus` | `Claude Opus 4.6 (Thinking)` | Claude via agy |

### Reviewer Label

The reviewer label returned by `resolve_and_call_with_reviewer()` for agy providers equals the `hook_model` value exactly (e.g. `"agy-flash-high"`). This is different from gemini providers which map to short labels (`"gem-flash"`).

### Failure Modes & Fallback

All 5 ProviderError cases trigger Anthropic SDK fallback (reviewer: `"anthropic-sdk"`):
1. `TimeoutExpired` — agy CLI timed out after 120s
2. `FileNotFoundError` — agy CLI not installed
3. `OSError` — OS error
4. Non-zero returncode — agy CLI failed (exit N)
5. Empty stdout — agy CLI returned empty response

### Key Files
- `src/pacemaker/inference/agy_provider.py` — `AgyProvider` class, `_MODEL_MAP`
- `src/pacemaker/inference/model_aliases.py` — 11 agy tokens in `KNOWN_MODELS`
- `src/pacemaker/inference/registry.py` — `get_provider()` agy routing, `is_agy_provider` reviewer label
- `src/pacemaker/user_commands.py` — regex pattern, `is_known_model()` validation, confirmation messages (NOT a static `valid_models` list — the only such list belongs to the unrelated `_execute_prefer_model`)
- `claude-usage-reporting/claude_usage/code_mode/display.py` — `REVIEWER_TAGS` agy entries (`"[Agy]"`, `"bright_green"`)
- `tests/test_agy_provider.py` — 29 unit tests (MODEL_MAP, command construction, failure modes)
- `tests/test_agy_registry.py` — 22 tests (KNOWN_MODELS, get_provider routing, reviewer labels, fallback)
- `tests/test_agy_user_commands.py` — 36 tests (regex, execution, status display)
- `claude-usage-reporting/tests/test_agy_display_tags.py` — 25 tests (REVIEWER_TAGS, colors, regex)

---

## Codex Profile Provider (Story #74)

**Grammar:** `codex-<profile>` — regex `^codex-[A-Za-z0-9][A-Za-z0-9._-]*$`

A `codex-<profile>` token binds pace-maker to a named profile in `~/.codex/` (e.g. `~/.codex/beast.config.toml`). The profile pins model+base_url+wire_api so `-m` is NOT passed; the profile config owns the model. pace-maker validates the token **shape only** — unknown profile names are rejected by codex CLI at runtime (non-zero exit → ProviderError → Anthropic fallback).

### CLI Invocation

**Profile mode** (`codex-beast`, `codex-local-llama`, etc.):
```
codex exec --skip-git-repo-check - --profile <profile-name> -s read-only
```
No `-m` flag. The `--profile` name is the substring after `"codex-"`.

**Non-profile mode** (plain `codex`, `gpt-5.5`, `gpt-5`, etc.) — unchanged:
```
codex exec --skip-git-repo-check - -m <resolved-model> -s read-only
```

**`--skip-git-repo-check` is NOT optional** — both branches in `CodexProvider.query()` pass it immediately after `exec` (`codex_provider.py:66-86`). Omitting it reintroduces the codex 0.139 trusted-directory guard: exit 1 → `ProviderError` → silent fallback to `anthropic-sdk`, i.e. the configured reviewer is downgraded without any visible failure. See the "Codex Profile Provider" section earlier in this file for the full rationale.

The function `_parse_codex_target(model_hint) -> (profile|None, model|None)` in `codex_provider.py` handles the dispatch:
- `model_hint.startswith("codex-")` → `(profile, None)` — profile mode
- else → `(None, SHORT_ALIASES.get(model_hint, model_hint) or "o3")` — model mode

### Reviewer Label

`resolve_and_call_with_reviewer()` returns the `hook_model` token **verbatim** as the reviewer label for all `codex-<profile>` tokens (e.g. `"codex-beast"`). Plain codex aliases (`codex`, `gpt-5.5`, etc.) still map to `"codex-gpt5"`.

### Failure Modes & Fallback

Identical to plain codex — all 5 ProviderError cases trigger Anthropic SDK fallback (reviewer: `"anthropic-sdk"`):
1. `TimeoutExpired` — codex CLI timed out after 120s
2. `FileNotFoundError` — codex CLI not installed
3. `OSError` — OS error
4. Non-zero returncode — unknown profile name or codex CLI error
5. Empty stdout — codex CLI returned empty response

**Do NOT read or parse `~/.codex/config.toml`.** Profile existence is validated by codex at runtime only.

### is_known_model()

`model_aliases.is_known_model(token) -> bool` accepts:
- Every token in `KNOWN_MODELS`
- Every key in `SHORT_ALIASES` (e.g. `codex`, `gpt-5`, `gem-flash` — not in `KNOWN_MODELS` but valid CLI tokens; accepted here so story #75 CLI does not regress)
- Any token matching the `codex-<profile>` regex above

### CLI / Expression / Monitor Surfacing (Story #75)

#### Single-model CLI token

`pace-maker hook-model codex-beast` — the `pattern_hook_model_single` regex in `user_commands.py` includes an `|codex-[a-z0-9][a-z0-9._-]*` alternative. Confirmation message names the profile: "Hook model set to codex profile 'beast'...".

After short-alias normalization (which leaves `codex-<profile>` untouched), validation uses `is_known_model(subcommand)` instead of a static `valid_models` list — so any valid-shape profile is accepted without requiring an enumeration.

#### Competitive / synthesizer slot

`parse_competitive()` uses `is_known_model(token)` for all slots, so `codex-<profile>` is accepted as a reviewer AND as the synthesizer:
- `codex-beast+haiku->sonnet` — codex-beast is reviewer
- `haiku+sonnet->codex-beast` — codex-beast is synthesizer

The competitive pattern regex (`[a-z0-9.\-]+`) already covered the allowed characters; no regex change was needed there.

#### Reviewer label in competitive

In `_call_single_reviewer` (competitive.py), a `codex-` prefix check now returns the verbatim token as the label; plain `gpt-5.5`/`gpt-5.4` keep `"codex-gpt5"`.

#### `pace-maker status`

The existing `.upper()` fallback in `_HOOK_MODEL_DISPLAY.get(hook_model, hook_model.upper())` renders `codex-beast` as `CODEX-BEAST`. No special case needed.

#### claude-usage monitor `[Codex]` tag

`claude-usage-reporting/claude_usage/code_mode/display.py` exports `get_reviewer_tag_info(reviewer_id)` (Story #75):
1. Exact `REVIEWER_TAGS` dict lookup first (preserves `codex-gpt5 → [Codex]/yellow` and all existing entries).
2. `startswith("codex-")` prefix fallback for dynamic profile tokens → `("[Codex]", "yellow")`.

The governance feed renderer calls `get_reviewer_tag_info()` instead of `REVIEWER_TAGS.get()` directly.

### Key Files
- `src/pacemaker/inference/codex_provider.py` — `_parse_codex_target()`, updated `CodexProvider.query()`
- `src/pacemaker/inference/model_aliases.py` — `is_known_model()`, `_CODEX_PROFILE_RE`
- `src/pacemaker/inference/registry.py` — `get_provider()` `codex-` branch, verbatim reviewer label
- `src/pacemaker/inference/competitive.py` — `is_known_model` token validation, verbatim label for `codex-<profile>`
- `src/pacemaker/user_commands.py` — `codex-[a-z0-9][a-z0-9._-]*` in single-model regex, `is_known_model` validation, profile confirmation message
- `claude-usage-reporting/claude_usage/code_mode/display.py` — `get_reviewer_tag_info()`, `_CODEX_PROFILE_TAG`
- `tests/test_codex_profile.py` — 47 tests (is_known_model, _parse_codex_target, argv, routing, label) — Story #74
- `tests/test_codex_profile_story75.py` — 25 tests (competitive parser, reviewer labels, synthesizer routing, CLI regex, execute, status) — Story #75
- `claude-usage-reporting/tests/test_codex_profile_display_tags.py` — 20 tests (exact entry unaffected, prefix resolution, ordering) — Story #75

---

## Canonical Verdict-Normalization Primitive (Story #76 B1)

**File**: `src/pacemaker/inference/verdict.py` — STDLIB-ONLY leaf module (no imports from other pacemaker modules; safe to import from any gate).

### Functions

| Function | Signature | Purpose |
|---|---|---|
| `is_positive` | `(text, positive_token="APPROVED") -> bool` | True iff ANY line, stripped+uppercased, STARTS WITH the token. Guarded-lenient. |
| `has_block_marker` | `(text) -> bool` | True iff ANY line starts with `BLOCKED:`. |
| `has_complete_marker` | `(text) -> bool` | True iff ANY line starts with `COMPLETE:`. |
| `verdict_passes` | `(text, positive_token="APPROVED") -> bool` | BLOCKED wins; then `is_positive`. Fail-closed. |
| `verdict_passes_for_context` | `(text, call_context) -> bool` | Default → `verdict_passes`. `stop_hook` → APPROVED OR COMPLETE: (BLOCKED still wins). |

### Contract (truth table)

| Input | Default verdict | `stop_hook` verdict |
|---|---|---|
| `APPROVED` | PASS | PASS |
| `APPROVED.` | PASS | PASS |
| `APPROVED\n\nnice work` | PASS | PASS |
| `NOT APPROVED` | FAIL | FAIL |
| `(empty)` | FAIL | FAIL |
| `BLOCKED: x` | FAIL | FAIL |
| `APPROVED\nBLOCKED: x` | FAIL (BLOCKED priority) | FAIL |
| `COMPLETE: done` | FAIL | PASS |
| `BLOCKED: x\nCOMPLETE: y` | FAIL | FAIL (BLOCKED wins over COMPLETE) |

### Matching strategy — guarded-lenient (starts-with)

Positive detection uses **starts-with**, NOT equality. This means `APPROVED.`, `APPROVED — ok`, `APPROVED\n(reasoning)` all PASS. `NOT APPROVED` FAILS because that line starts with `NOT`, not `APPROVED`. This is a **deliberate leniency change** from the old strict `== "APPROVED"` equality.

BLOCKED always wins: if any line starts with `BLOCKED:`, `verdict_passes` returns False regardless of APPROVED lines.

Fail-closed: empty / whitespace-only input → all predicates False.

### Gate convergence (all three gates use this primitive)

1. **Stop-hook** (`intent_validator.py:parse_sdk_response`): `_find_verdict` uses `is_positive` for the positive branch. **BLOCKED detection is an inline scan, NOT `has_block_marker`** — the gate needs the raw `BLOCKED:` line itself to use as the reason string, and `has_block_marker` returns only a bool. Do not "simplify" that loop into `has_block_marker()`; it would silently strip the block reason from every stop-hook rejection. `parse_sdk_response` is unchanged externally (positive→`{"continue":True}`, BLOCKED→`{"decision":"block"}`, unparseable→fail-open).
2. **Stage 2 Write/Edit gate** (`intent_validator.py` line ~951): replaced `_find_verdict(stage2_feedback) == "APPROVED"` with `verdict_passes(stage2_feedback)`. **Deliberate leniency**: `APPROVED.` and `APPROVED — ok` now PASS (old strict equality would block them).
3. **Danger-bash Phase 2** (`hook.py` line ~2803, import at ~2755): uses `verdict_passes(response)`. Converged late, in commit `0beaed2` / issue #94 — Story #76 B1 missed this gate while claiming all three were done, and this very section asserted the convergence for roughly two months before it was true. The gate meanwhile compared the reviewer's whole reply to the literal string `APPROVED`, so `APPROVED.` or `APPROVED — the rule match is a false positive` BLOCKED the command: seven such false blocks are recorded in `usage.db`. Safety of the switch was established by replaying all 1148 recorded danger-bash rejections through the new predicate — exactly 7 flip to passing, all of them the known false blocks.

**Note on the prompt, not just the predicate**: `verdict_passes` is line-based starts-with, so a rejection phrased `Approved: NO\n<explanation>` would pass. The primitive's `BLOCKED:`-wins guard closes that, but only if reviewers actually emit the prefix — so the danger-bash prompt (`hook.py` ~2776) prescribes an explicit response format requiring rejections to begin with `BLOCKED:`. If you rewrite that prompt, keep the requirement.

### Tests

- `tests/test_verdict.py` — 57 parametrized unit tests covering the full truth table, all three sub-functions, context-aware dispatch, and the lenient-flip cases. 100% coverage on `verdict.py`.

---

## Self-Identifying Pace-Maker Provenance Tagging (Story #101)

**Problem it solves**: none of pace-maker's emitted governance text (SessionStart guidance, PostToolUse nudges, PreToolUse block reasons, Stop-hook messages, CSA banners) previously self-identified as originating from pace-maker. A session receiving an unattributed, imperative, tool-blocking message had no way to distinguish "my own governance layer doing its job" from genuinely foreign/adversarial injected content.

**Core design principle**: a plaintext tag cannot be cryptographically authenticated — anyone who can inject text into a transcript can also write the tag. The tag's purpose is therefore NOT to prove trust. Its purpose is to be checkable against a declared, closed contract:

> **The manifest is the contract; the tag is just an index into it.**

Claude reads the SessionStart/SubagentStart manifest, which declares the complete closed enumeration of channels pace-maker will ever use, plus an explicit four-item never-list. Any tagged content whose claimed channel is not in the manifest, or whose behavior violates the never-list, is recognizable as not-pace-maker regardless of how it is tagged. **There is deliberately no matching/detection code in pace-maker for the never-list** — enforcement lives entirely in Claude's own reasoning when it reads tagged content.

### The shared module — `src/pacemaker/prompt_provenance.py`

STDLIB-ONLY leaf module, same invariant as `inference/verdict.py` (zero imports from other pacemaker modules, verified by `tests/test_prompt_provenance.py::TestLeafModulePurity` which resolves every import via `importlib.util.find_spec` against `sysconfig`'s stdlib path rather than a name blocklist).

Two tag classes:
- **`format_tag(body, event) -> str`** — pace-maker's own mechanical messages. Renders `[pace-maker · <event>]\n<body>` where `·` is the EXACT U+00B7 MIDDLE DOT (asserted via `ord() == 0xB7`, not a lookalike like bullet U+2022). Raises `ValueError` for an `event` not in the closed `CHANNELS` enumeration, `TypeError` for a non-str `body`. Idempotent: re-wrapping an already-tagged-for-this-event body (header immediately followed by `\n`) returns it unchanged rather than stacking a second header — a body that merely starts with the header text WITHOUT the newline boundary is treated as NOT already tagged and gets a fresh header (closes a forged-prefix bypass caught in code review).
- **`format_reviewer_relay(body, model) -> str`** — relayed third-party reviewer/verifier LLM output (Stage 2 code review, danger-bash Phase 2). Renders `[pace-maker · reviewer-relay · model=<id>]\n(advisory/third-party framing)\n<body>`, visibly distinct from the plain tag. `model` accepts any reviewer id including a competitive expression (`"opus+gpt-5->haiku"`). Raises `ValueError` for an empty model id or one containing `]`/newline (would malform or forge additional tag-like structure in the header).

`CHANNELS` (frozenset, 19 entries as of Story #155 — the earlier "17" was already stale before it, since `version_block_notice` was added) is the closed enumeration — both the manifest's channel list AND the thing `tests/test_prompt_provenance.py::TestClosedEnumeration` and the manifest-completeness tests check every emission site against:

| Channel | Emission site |
|---|---|
| `session_start_manifest` | `hook.py::run_session_start_hook` (the manifest itself) |
| `subagent_start_manifest` | `hook.py::run_subagent_start_hook` (the abbreviated manifest itself) |
| `intent_validation_guidance` | `hook.py::display_intent_validation_guidance` (shared by SessionStart + SubagentStart) |
| `secrets_nudge` | `hook.py::get_secrets_nudge` (shared by SessionStart + PostToolUse call sites) |
| `csa_sibling_banner` | `session_registry/nudges.py::build_start_banner` |
| `csa_periodic_reminder` | `session_registry/nudges.py::build_periodic_reminder` |
| `csa_danger_bash_warning` | `session_registry/nudges.py::build_danger_bash_warning` |
| `subagent_delegation_reminder` | `hook.py::inject_subagent_reminder` (PostToolUse) |
| `intel_nudge` | `hook.py::run_user_prompt_submit` (`§ intel` nudge) |
| `intent_validation_block` | `intent_validator.py::validate_intent_and_code` (Stage 1 NO/NO_TDD, SDK-unavailable, exception fail-closed) |
| `intent_validation_deferred` | `hook.py::run_pre_tool_hook` (Write/Edit TOCTOU-race block, issue #91/#93 territory) |
| `danger_bash_block` | `hook.py::run_pre_tool_hook` (danger-bash Phase 1, no-INTENT fast reject) |
| `danger_bash_deferred` | `hook.py::run_pre_tool_hook` (danger-bash "transcript not ready" block) |
| `fail_closed_error` | `hook.py::_fail_closed_message` (shared by both Write/Edit and danger-bash gates); since #142 also the zero-survivor "No reviewer responded" block in `intent_validator.py` Stage 2 and in the danger-bash Phase 2 block in `hook.py` (no reviewer said anything, so it is NOT `reviewer-relay`) |
| `stop_tempo_block` | `hook.py::run_stop_hook` (tempo/completion block reason) |
| `stop_continuation_nudge` | `hook.py::run_stop_hook` (silent-tool-stop continuation nudge) |
| `declare_intent_result` | `intent_mcp/server.py::_text_result` (Story #155) — the declare_intent MCP tool's acknowledgement / "Intent NOT recorded" text, emitted by the MCP server PROCESS (not a hook); the server imports the stdlib-only `prompt_provenance` leaf module for this |
| `reviewer-relay` | not a `format_tag` channel — see `format_reviewer_relay` above; listed in `CHANNELS` only so the manifest declares this second tag class too |

**`reviewer-relay` wiring — exactly two call sites, both threading the resolved reviewer id**:
1. `intent_validator.py::validate_intent_and_code` Stage 2 block path — wraps `stage2_feedback` with `format_reviewer_relay(stage2_feedback, reviewer)`. `_parse_stage2_classification(stage2_feedback)` runs on the UNTAGGED variable first — the tag is applied only to the dict's `"feedback"` value, never fed back into classification parsing. The dict ALSO carries a `"raw_feedback"` key holding the untagged `stage2_feedback` text — see the AC5 discipline note below (issue #101 review, B2) on why a second untagged copy is needed.
2. `hook.py` danger-bash Phase 2 mismatch block — **nested tagging** (issue #101 review, B3): the reviewer's own response segment is wrapped with `format_reviewer_relay(response[:500], reviewer)` as before, but that wrapped segment is now itself nested INSIDE an outer `format_tag(..., "danger_bash_block")` that also wraps pace-maker's own framing text (`"⛔ Dangerous Bash command — intent mismatch"`, `"Matched danger rules:"`, `"Reviewer:"`) — symmetric with Phase 1, which was already tagged. An earlier revision of this story left the framing text completely untagged; that was the exact class of unattributed pace-maker message this story exists to fix, caught in code review and corrected.

Since both `resolve_and_call_with_reviewer()` and `run_mechanical()` return `(text, reviewer_label)` where `reviewer_label` is the full competitive expression when `hook_model` contains `+`, mechanical-failure-synthesis output is covered transparently through these same two call sites — no separate wiring was needed for it (unlike the story's component list implied).

**Deliberately NOT wrapped**: the Stop-hook tempo-block reason (`intent_validator.py::validate_intent`, via `call_sdk_validation` → `resolve_and_call`, not `resolve_and_call_with_reviewer`) has no reviewer id available and is not routed through the mechanical/reviewer pipeline, so it gets the plain `stop_tempo_block` tag, not `reviewer-relay` — tagged in `hook.py::run_stop_hook` immediately after the `validate_intent()` call (NOT inside `intent_validator.py`, to avoid touching `parse_sdk_response()`'s own pre-existing unit tests, which assert exact `BLOCKED:`-parsing behavior unrelated to tagging).

**AC5 regression discipline — where the tag is applied, and where it deliberately is NOT**:
- `record_blockage(reason=...)` DB storage and the Claude-facing `"reason"`/`"feedback"` JSON field share the same already-tagged string **only at the two gates that read from a pre-built result dict, NOT the gates that construct their block-response dict inline** (corrected, issue #101 review, B4 — the prior wording had this backwards). The stop-hook tempo block tags `result["reason"]` in place at `hook.py:2335-2337`, then both `record_blockage(reason=result.get("reason", ...))` (`hook.py:2380`) and the final `return result` (`hook.py:2399`) read that same tagged dict entry. The Write/Edit intent-validation block is the same shape: both `record_blockage(reason=result.get("feedback", ...))` (`hook.py:3163`) and the block-response `"reason": result.get("feedback", ...)` (`hook.py:3234`) read the same already-tagged `result["feedback"]` from `validate_intent_and_code()`. By contrast, the three danger-bash gates that build their `record_blockage` call and their block-response dict inline, side by side in the same code block, each pass a SEPARATE, untagged, short reason to the DB that is NOT the tagged Claude-facing string built moments later via `format_tag`/`format_reviewer_relay`: the "transcript not ready" block (`record_blockage` at `hook.py:2731` vs. the tagged `"reason"` at `hook.py:2752`), the Phase 1 no-INTENT block (`hook.py:2772` vs. `hook.py:2811`), and the Phase 2 mismatch block (`hook.py:2893` vs. `hook.py:2928`). This asymmetry is intentional, not an oversight; no test in this repo asserts an exact DB-stored blockage reason string.
- Governance-event `feedback_text` (the `[reviewer]`-prefixed string built separately in `hook.py` for the monitor's live event feed) stays **untagged/raw** by design (corrected, issue #101 review, B2) — the Claude-facing block `reason`/`"feedback"` DOES carry the pace-maker/reviewer-relay tag, but `feedback_text` deliberately does not, so the claude-usage monitor's pre-existing single-bracket `[expression]` reviewer-tag display (documented above under "Reviewer Identity Tracking") is never fed a second, nested bracket group. Each `validate_intent_and_code` blocked-return dict therefore carries BOTH `"feedback"` (tagged, for Claude) and `"raw_feedback"` (untagged, for governance/telemetry); `hook.py`'s Write/Edit governance-event wiring reads `result.get("raw_feedback", ...)`, matching the danger-bash Phase 2 governance path which already used the untagged `response`. An earlier revision of this story left the Write/Edit governance path reading the tagged `"feedback"` value, producing a duplicated/leaking tag in the governance feed — caught in code review and corrected. `tests/test_provenance_wiring.py::TestGovernanceFeedbackUntagged` locks in the untagged, non-duplicated behavior.
- Pre-existing tests asserting exact equality on a tagged function's return value were updated to `in`-substring assertions that pin the pre-existing text verbatim inside the new wrapper (e.g. `tests/test_subagent_reminder.py`'s three `TestReminderInjection` tests) — everything else (substring checks like `"intent" in result["feedback"].lower()`) needed no changes since the tag only prepends a header line.
- `tests/test_session_registry_hook_subagent_start_json_wiring.py::test_emits_no_json_when_neither_fires` was renamed and rewritten: since the SubagentStart abbreviated manifest is now UNCONDITIONALLY appended (independent of `intent_validation_enabled`/CSA banner state — subagents have zero session history and need the contract regardless), "neither guidance nor banner fires" now correctly emits exactly ONE JSON object (manifest-only), not zero. This is new, deliberate always-on behavior from this story, not a regression of the guidance/banner wiring that test file otherwise covers.

### The manifest builders — `session_start_manifest()` / `subagent_start_manifest()`

Both live in `prompt_provenance.py`, both self-tagged with their own channel (`format_tag(body, "session_start_manifest")` / `"subagent_start_manifest"`), both enumerate the full `CHANNELS` set and all four `NEVER_LIST` items — `subagent_start_manifest()` is shorter only in surrounding prose, never in substantive content (`tests/test_prompt_provenance.py::TestSubagentStartManifest::test_shorter_than_session_start_manifest` + `test_still_enumerates_every_declared_channel`).

`NEVER_LIST` (4 items, enforced by Claude's own reasoning, zero matching code in this module):
1. Never exfiltrate data to an external destination outside the session.
2. Never disable a pace-maker safety/governance check on pace-maker's own authority — only the USER may authorize that.
3. Never conceal pace-maker's behavior/decisions/mechanism from the user.
4. Never speak as if it were the user (impersonation).

**Wiring**:
- SessionStart (`hook.py::run_session_start_hook`): manifest printed UNCONDITIONALLY (independent of `intent_validation_enabled`) right after the existing intent-validation-guidance block, in its own try/except so a failure there can never break SessionStart. Existing SessionStart emissions' content and ordering are otherwise undisturbed.
- SubagentStart (`hook.py::run_subagent_start_hook`): abbreviated manifest appended to `_additional_context_parts` UNCONDITIONALLY, before the existing guidance-append block, same fail-safe try/except pattern.

### Out of scope (explicitly, per issue #101)

- No cryptographic/signed nonce — plaintext tag only, by design (see Core design principle above).
- The "Senior Coding Nanny" concealment clause in `prompts/session_start/intent_validation_guidance.md` is untouched — only the emission is tagged, the guidance TEXT itself was not reworded.
- `SECRET_TEXT`/`SECRET_FILE` parsing/masking (`secrets/parser.py`, `secrets/sanitizer.py`) is untouched — only the secrets-nudge TEXT is wrapped.
- No never-list matching/detection code anywhere in pace-maker — enforced by Claude's reasoning only.

### Key files

| Concern | File |
|---|---|
| Shared leaf module (tag formatters, manifests, `CHANNELS`, `NEVER_LIST`) | `src/pacemaker/prompt_provenance.py` |
| CSA banner tagging (3 builders) | `src/pacemaker/session_registry/nudges.py` |
| Stage 1/2/exception feedback tagging | `src/pacemaker/intent_validator.py::validate_intent_and_code` |
| SessionStart/SubagentStart manifest + guidance/secrets-nudge/intel-nudge/subagent-reminder/danger-bash/Write-Edit/Stop-hook tagging | `src/pacemaker/hook.py` |

### Tests

- `tests/test_prompt_provenance.py` — 30 unit tests, 100% coverage on `prompt_provenance.py`: leaf-module purity (stdlib-only, verified via `sysconfig` path containment, not a name blocklist), tag formatting (middle-dot codepoint, unknown-channel `ValueError`, non-str-body `TypeError`, empty/multiline/tag-like-body edge cases, no-double-wrap idempotency), reviewer-relay formatting (distinctness, advisory framing, competitive-expression model ids, invalid-model-id `ValueError`s), both manifest builders, closed-enumeration consistency.
- `tests/test_provenance_wiring.py` — 22 integration tests (verified count; a prior revision of this file said 19, which was already wrong before the count below was added — see issue #101 review) driving real entry points (`validate_intent_and_code`, `run_pre_tool_hook`, `run_stop_hook`, `run_user_prompt_submit`, `run_session_start_hook`, `run_subagent_start_hook`, `_fail_closed_message`, `inject_subagent_reminder`, `get_secrets_nudge`) and asserting the emitted tag, per-channel, plus AC5 verbatim-text-preservation assertions. Includes `TestGovernanceFeedbackUntagged` (B2 fix) and the extended `TestDangerBashPhase2ReviewerRelayTag` (B3 fix, nested-tag-order assertions).
- `tests/test_session_registry_nudges.py` — extended with `TestProvenanceTagsPresent` / `TestProvenanceEmptySiblingsStaysEmptyString` / `TestProvenanceContentPreserved` (parametrized across all three builders).
- `tests/test_subagent_reminder.py` — three pre-existing exact-equality tests updated to substring + tag-prefix assertions (AC5).
- `tests/test_session_registry_hook_subagent_start_json_wiring.py` — one pre-existing test renamed and rewritten to reflect the new always-on abbreviated-manifest emission at SubagentStart.
