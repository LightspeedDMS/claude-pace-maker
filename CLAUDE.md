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

## Related Codebase: Claude Usage Reporting

**IMPORTANT**: When the user says "claude usage" or "claude-usage", they mean the **claude-usage-reporting** codebase located at:
- `/home/jsbattig/Dev/claude-usage-reporting`

This is a separate tool that displays usage metrics in a monitor/dashboard format. It has a "Pacing Status" column where pace-maker integration features should be displayed.

- `pace-maker status` = CLI command from THIS repo (claude-pace-maker)
- `claude-usage` = Monitor tool from claude-usage-reporting repo

## Cross-Process Data Access Pattern (Pace-Maker → claude-usage Monitor)

The `claude-usage-reporting` monitor (a.k.a. "claude-console") reads pace-maker's SQLite DBs directly. This is the **canonical pattern** for any new cross-process reader — follow it exactly when adding new panels, columns, or data consumers on the monitor side.

### Architecture

- **Producer**: `claude-pace-maker` writes SQLite DBs under `~/.claude-pace-maker/` from hook processes, using `execute_with_retry()` (exponential backoff 100ms → 200ms → 400ms, `MAX_RETRIES=3`). See `src/pacemaker/database.py:442-482`.
- **Consumer**: `claude-usage-reporting/claude_usage/code_mode/pacemaker_integration.py` opens **blocking read connections with a 5-second timeout** — NO retry loop on the reader side. The timeout IS the circuit breaker.
- **Two databases, identical access pattern**: `usage.db` (heavily read) and `session_registry.db` (reserved for cross-session features).
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
- **No Unix sockets, no subprocess CLI calls, no HTTP.** SQLite + JSON files + dynamic imports are the only IPC surfaces.

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

**When bumping the version**, ALWAYS update BOTH files:
- `src/pacemaker/__init__.py` — the Python package version
- `.claude-plugin/plugin.json` — the Claude Code plugin manifest version

These MUST always match. Forgetting `plugin.json` has happened before.

---

## Danger Bash Validation

**Two-phase validation** for dangerous Bash commands in the PreToolUse hook:

- **Phase 1 (Regex Gate)**: When a Bash tool call matches any of the 55 default danger rules and the message contains no `INTENT:` declaration, the command is blocked immediately with no LLM call. This is a fast-reject path.
- **Phase 2 (LLM Validation)**: When `INTENT:` is present, the LLM validates that the declared intent aligns with the actual Bash command being executed (same Stage 2 flow as Write/Edit).

**Rule categories**: 25 Work Destruction (WD) rules (git checkout --, git reset --hard, git stash drop, branch deletion, etc.) and 30 System Destruction (SD) rules (rm -rf, kill -9, chmod 777, mkfs, dd, etc.).

**Configuration**: Rules are customizable via `~/.claude-pace-maker/danger_bash_rules.yaml` using the same merge strategy as clean code rules (user config stores only additions and deletion markers, defaults loaded from bundled YAML at runtime).

**Blockage category**: `intent_validation_dangerbash` with label "Danger Bash" in telemetry and blockage stats.

**Key files**:
- `src/pacemaker/danger_bash_rules_default.yaml` — 55 bundled default rules
- `src/pacemaker/danger_bash_rules.py` — loader, merger, matcher module
- `src/pacemaker/hook.py` line ~2149 — PreToolUse Bash tool handling

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
- `src/pacemaker/hook.py` lines ~351, ~562, ~616, ~1946, ~2305 — Hook wiring to `_csa`

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
When `false`, ALL cross-session logic is skipped — no registry writes, no banners.

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
- `tests/conftest.py` sets `PACEMAKER_SESSION_REGISTRY_PATH` to a tmp path via `pytest.ini`-level fixture
- Tests that need registry isolation use `monkeypatch.setenv("PACEMAKER_SESSION_REGISTRY_PATH", str(tmp_path / "sessions.db"))`
- E2E tests use synthetic sibling seeding (direct SQLite INSERT + verify nudge responses)

---

## Reviewer Identity Tracking

When intent validation runs Stage 2 (LLM code review), the reviewer identity is tracked end-to-end:

1. `resolve_and_call_with_reviewer()` in `inference/registry.py` returns `(response, reviewer_name)` tuple
2. `intent_validator.py` threads reviewer through validation result dict (`"reviewer": reviewer`)
3. `hook.py` records reviewer in blockage_events details JSON
4. Governance event `feedback_text` is prefixed with `[REVIEWER:xxx]` tag (e.g., `[REVIEWER:codex-gpt5]`, `[REVIEWER:anthropic-sdk]`, `[REVIEWER:gemini]`)

The reviewer tag enables the claude-usage monitor to display colored reviewer identity in the governance event feed.

---

## Codex PAYG Billing Handling

The `codex_usage.py` module handles both subscription and PAYG (Pay-As-You-Go) Codex billing:

- **PAYG detection**: When Codex CLI returns `limit_id: "premium"` in rate limit headers, the plan is identified as PAYG
- **Null handling**: `_parse_last_token_count()` gracefully handles null `primary`/`secondary` fields that Codex returns for PAYG billing (no usage percentages available)
- **`limit_id` column**: Added to `codex_usage` SQLite table via idempotent `ALTER TABLE` migration in `migrate_codex_usage_schema()`
- **SubagentStop wiring**: Migration is called in `hook.py` SubagentStop handler before writing codex usage data

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

---

## Competitive Review Pipeline

**Syntax**: `hook_model = "m1+m2[+m3]->synthesizer"` (2-3 reviewers + 1 synthesizer)

**Supported models**: auto, sonnet, opus, haiku, gpt-5, gemini-flash, gemini-pro

**Short aliases**: gem-flash→gemini-flash, gem-pro→gemini-pro (accepted at CLI, stored canonically)

**Key file**: `src/pacemaker/inference/competitive.py`

**Wiring**: `resolve_and_call_with_reviewer()` in `registry.py` detects `+` in hook_model and delegates to `run_competitive()`

**Failure modes**:
- 2+ survivors → synthesize via synthesizer model
- 1 survivor → pass through without synthesis (survivor's label returned)
- All fail, no Anthropic in reviewers → SDK solo fallback (label: "sdk-fallback")
- All fail, Anthropic was a reviewer → fail per caller semantics (empty string returned)
- Synthesizer fails → first survivor wins

**Tag format**: `[expression]` in feedback_text (no REVIEWER: prefix), e.g. `[gpt-5+gemini-flash->sonnet]`

**CLI**: `pace-maker hook-model gpt-5+gemini-flash->sonnet` — validates via `parse_competitive()`, stores canonical form

**Synthesis prompt**: Synthesizer is a formatter, not a judge — applies verdicts mechanically: any BLOCK from any reviewer → BLOCKED with combined reasons; all APPROVED → APPROVED. Does not add new concerns or remove existing ones.

**Reviewer verdict logging**: Each reviewer's raw response is logged at DEBUG level (first 300 chars via `MAX_REVIEW_LOG_CHARS`) via `log_debug("competitive", f"Reviewer {model} verdict: ...")` for synthesis quality evaluation.

**Timeouts**: `REVIEWER_WAIT_TIMEOUT_SEC = 60` (per-reviewer via `futures_wait`), `SYNTHESIS_TIMEOUT_SEC = 30` (synthesis via `future.result(timeout=...)`), outer hook timeout = 120s (in `~/.claude/settings.json`). Timeout → first-survivor-wins for synthesis; partial reviewer results always preserved.

**Status display**: `pace-maker status` shows full expression (e.g. `opus+gpt-5->haiku`) in ANSI blue — no separate "reviewers:" breakdown line.

**claude-usage display**: Hook Model shows `comp` in `bright_blue`; governance feed shows `[Comp]` in `bright_blue` for competitive expressions.

**Concurrency**: `ThreadPoolExecutor` with `futures_wait(timeout=REVIEWER_WAIT_TIMEOUT_SEC)` — partial results preserved on timeout; `executor.shutdown(wait=False)` avoids blocking on in-flight threads.

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
- `src/pacemaker/hook.py` (~lines 373-410) — SessionStart wiring
- `src/pacemaker/user_commands.py` — command patterns (25, 26) and dispatch
- `install.sh` line 596 — copies `memory_localization/` subdir to `~/.claude/hooks/pacemaker/`
- `tests/test_memory_localization_*.py` — 33 tests (classification, linking, seed/restore)
