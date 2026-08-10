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
- Legacy scripted E2E files DO still exist and still run: `tests/e2e/test_secrets_e2e.py`, `tests/test_clean_code_rules_e2e.py`, `tests/test_install_e2e.py`, `tests/test_langfuse_provisioner_e2e.py`, `tests/test_subagent_output_correlation_e2e.py` (`./scripts/run_tests.sh --quick` skips them). Leave them alone unless a task specifically covers them.
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
- **Dynamic imports**: Monitor adds pace-maker's `src/` to `sys.path` via `_get_pacemaker_src_path()` (reads `~/.claude-pace-maker/install_source`, lines 157-197) then calls `UsageModel.get_current_usage()` **in-process**. Import-based calls are NOT cross-process — the function runs in the monitor's Python interpreter against the shared SQLite file.
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

**⚠️ The minimum is documentation, NOT an enforced runtime gate (issue #96).** The value `2.1.39` lives as `_FALLBACK_MIN_VERSION` in `src/pacemaker/version_check.py:20`. **The check is NOT WIRED**: `perform_session_start_version_check()` has **no caller anywhere in `src/`**, and **nothing reads `state["version_block_active"]`**. No SessionStart block is emitted, no downstream hook skips, no upgrade message is ever shown — a user on Claude Code 2.0 gets zero warning. This is orphan code (Messi Rule 12). See the "Minimum Claude Code Version Check (Story #66)" section below and issue #96.

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
- **Phase 1 (Regex Gate)**: When a Bash tool call matches the **merged** ruleset (55 bundled defaults **minus `deleted_rules`, plus user additions** — not a fixed 55) and the anchored message contains no `INTENT:` declaration, the command is blocked immediately with no LLM call. This is a fast-reject path.
- **Phase 2 (LLM Validation)**: When `INTENT:` is present, an LLM validates that the declared intent aligns with the actual Bash command. **This is a SEPARATE code path from Write/Edit Stage 2, not "the same flow"**: the prompt is built inline at `hook.py:2760-2783` (there is no `prompts/pre_tool_use/` template for it) and the verdict comes from `verdict.verdict_passes()` at `hook.py:2803`. The two gates share only the reviewer resolver and the verdict primitive. **Keep them in sync deliberately — issue #94 was caused by exactly this pair silently diverging.**

**Rule categories**: 25 Work Destruction (WD) rules (git checkout --, git reset --hard, git stash drop, branch deletion, etc.) and 30 System Destruction (SD) rules (rm -rf, kill -9, chmod 777, mkfs, dd, etc.).

**Configuration**: Rules are customizable via `~/.claude-pace-maker/danger_bash_rules.yaml` using the same merge strategy as clean code rules (user config stores only additions and deletion markers, defaults loaded from bundled YAML at runtime).

**Blockage category**: `intent_validation_dangerbash` with label "Danger Bash" in telemetry and blockage stats.

**Key files**:
- `src/pacemaker/danger_bash_rules_default.yaml` — 55 bundled default rules
- `src/pacemaker/danger_bash_rules.py` — loader, merger, matcher module
- `src/pacemaker/hook.py` lines 2575-2882 (`# 2a. Danger Bash validation`) — PreToolUse Bash tool handling. (Was documented as `~2149`, which is CSA session-end code in the **Stop** hook — wrong file region entirely.)

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

> # ⚠️ STATUS: NOT WIRED — THIS FEATURE DOES NOTHING AT RUNTIME (issue #96)
>
> The three modules below exist and are unit-tested, but **nothing in `hook.py` imports or calls any of them**. Concretely:
> - **No caller** for `perform_session_start_version_check()` anywhere in `src/` — the SessionStart wiring described in Story #66 was never added.
> - **No SessionStart block ever occurs.** A user on a Claude Code below the minimum sees nothing.
> - **`version_block_active` is never written** to `state.json`, and no code reads it.
> - **PreToolUse and Stop have NO early-return guard.** There is no such code to find.
>
> This is orphan code (Messi Rule 12). Everything below describes the modules **as written**, not as reachable. Do not cite this feature as an active safeguard, and do not "fix a bug" in it without first wiring it — see issue #96.

### Architecture (module behavior as written — unreachable today)

- **Version probe**: `subprocess.run(["claude", "--version"], timeout=5)` — any failure (FileNotFoundError, TimeoutExpired, non-zero exit, parse error) returns `None` and the check fails-open (no block).
- **Minimum value**: `_FALLBACK_MIN_VERSION = "2.1.39"` in `src/pacemaker/version_check.py:20`. **There is NO `DEFAULT_CONFIG["min_claude_version"]`** — `constants.py` has no such key, and **setting `min_claude_version` in `config.json` has no effect whatsoever**.
- **Block flag**: `state["version_block_active"] = True` would be written to `state.json` when the installed version is below minimum — **never executed, since the function has no caller**.
- **Downstream hooks**: the design called for PreToolUse and Stop to check `version_block_active` at entry and return `{"continue": True}` — **this guard does not exist in the code**.

### Key Files

- `src/pacemaker/claude_code_version.py` — `ClaudeCodeVersion` dataclass: `parse()`, `compare()`, `is_below()`, `probe_installed_version()`
- `src/pacemaker/version_status_db.py` — SQLite DB following session_registry pattern: `resolve_db_path()`, `record_status()`, `read_status()`
- `src/pacemaker/version_check.py` — `perform_session_start_version_check(state, config, stderr)` with full fail-open wrapper; `_FALLBACK_MIN_VERSION` at line 20. **No caller in `src/`.**
- `src/pacemaker/hook.py` — **nothing.** The SessionStart wiring and the PreToolUse/Stop guards were never written.

### Version Status DB

Follows the session_registry pattern exactly:
- **Env override**: `PACEMAKER_VERSION_STATUS_PATH` overrides DB path
- **Test-mode enforcement**: raises `RuntimeError` if `PACEMAKER_TEST_MODE=1` and env var is unset
- **Single-row upsert**: `INSERT ... ON CONFLICT(id) DO UPDATE SET` — always id=1
- **Fail-open reads**: `read_status()` catches all exceptions at DEBUG level, returns `None`
- **Named constant**: `_READ_TIMEOUT_SECONDS = 5.0`

**No CLI, no status line.** `pace-maker min-claude-version` **does not exist** (command patterns in `user_commands.py` stop at 26; there is no `_execute_min_claude_version()`), and `pace-maker status` has **no "Claude Code:" line**. Both were previously documented here and were removed as fiction — do not re-add them without the implementation.

### Test Isolation

`tests/conftest.py` sets `PACEMAKER_VERSION_STATUS_PATH` to a tmp path in `_guard_production_db` fixture, preventing tests from writing to `~/.claude-pace-maker/version_status.db`.

### Test Files

- `tests/test_claude_code_version.py` — **38** unit tests (parse, compare, is_below, probe, DB). **No CLI coverage and no config-defaults coverage** — neither exists to cover.
- `tests/test_version_check_integration.py` — **9** component tests. **⚠️ Its 2 `TestBlockedHooksEarlyReturn` tests pass VACUOUSLY**: they assert only `decision != "block"`, which is trivially true precisely *because* no early-return guard exists. Green here is not evidence the feature works — it is evidence of the absence being untested.

---

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

Without running the installer, your code changes remain undeployed and inactive.

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

   **🚨 SUPERSEDED BY ISSUE #93 — the two gates are NO LONGER symmetric on `None`, and this paragraph is the single most dangerous stale claim in this file.** Current behaviour: the **Write/Edit gate still blocks on every `None`**, but the **danger-bash gate blocks only when the outcome is `not_found`** — a `stale` outcome is **ACCEPTED**, because `_tool_input_matches` requires the Bash `command` to be byte-identical, so a stale match is provably a re-issue of exactly this command. **Do NOT "restore symmetry" by deleting the `elif _bash_outcome == "stale":` branch at `hook.py:2649`.** That branch is the entire fix for #93; removing it re-creates the deadlock where EVERY Bash command was refused with "transcript not ready" after burning the full ceiling, with no recovery on re-issue.

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
- `src/pacemaker/hook.py` — Write/Edit gate (~lines 2949-2956, `if current_message_override is None:`): threads `tool_input`/`tool_name`/`_diagnostics`, fails CLOSED on `None` (v2.33.2), uses the 30.0s default ceiling; Danger-bash gate (~line 2590): **DOES override the retry params** — `_max_wait_seconds=_DANGER_BASH_MAX_WAIT_SECONDS` (3.0s, `hook.py:64`) — and additionally consumes `_diagnostics["outcome"]` / `_diagnostics["stale_text"]` to distinguish `stale` (accept) from `not_found` (block). It does **not** share the Write/Edit ceiling (issue #93).
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

**Synthesizer prompt**: Externalized to `src/pacemaker/prompts/common/mechanical_failure_synthesis.md` (Messi Rule 11). Instructions: merge failing reviews into ONE message; do NOT output APPROVED/BLOCKED/COMPLETE; you are a FORMATTER not a judge.

**Tag format**: `[expression]` in feedback_text (no REVIEWER: prefix), e.g. `[gpt-5+gemini-flash->sonnet]`

**CLI**: `pace-maker hook-model gpt-5+gemini-flash->sonnet` — validates via `parse_competitive()`, stores canonical form

**Reviewer verdict logging**: Each reviewer's raw response is logged at DEBUG level (first 300 chars via `MAX_REVIEW_LOG_CHARS`) via `log_debug("competitive", f"Reviewer {model} verdict: ...")`.

**Timeouts**: `REVIEWER_WAIT_TIMEOUT_SEC = 60` (per-reviewer via `futures_wait`), `SYNTHESIS_TIMEOUT_SEC = 30` (synthesis via `future.result(timeout=...)`).

**The outer hook timeout is PER-EVENT, not a single 120s** (`~/.claude/settings.json`): Stop = 120s, **PreToolUse = 60s**, PostToolUse = 360s, SessionStart / SubagentStart / SubagentStop = 10s.

**⚠️ The pipeline can outlive the PreToolUse budget.** Worst case is `REVIEWER_WAIT_TIMEOUT_SEC` (60) + `SYNTHESIS_TIMEOUT_SEC` (30) = **90s, which exceeds the 60s PreToolUse allowance**. One slow verifier plus a synthesis round is enough for the harness to kill the pre-tool gate mid-flight — **and a killed PreToolUse hook is a silently unvalidated tool call**, not a block. Budget accordingly when choosing competitive expressions for the pre-tool gate; the Stop gate's 120s has headroom, the pre-tool gate does not.

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
