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

**This project does NOT use scripted/automated end-to-end tests** — they consume too much time and are explicitly unwanted here.

**What "end-to-end" means in this project**: Claude itself executes and inspects what pace-maker is doing when finishing agentic work — run the hooks/CLI, observe pace-maker's actual behavior, and report the real observed evidence. E2E verification here is **agentic/manual (performed by Claude)**, never a test script.

**Therefore:**
- Do NOT write scripted/automated E2E test files for this project.
- Do NOT enforce scripted-E2E evidence in validation prompts (e.g. demanding "E2E TEST COMPLETION REPORT" formats, "pytest does NOT satisfy E2E", "execute the real application", etc.). Two stop-hook prompt tests that asserted this requirement were removed for this reason.
- When end-to-end verification is needed, run pace-maker and inspect its behavior directly, then report the real observed output.

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

**When bumping the version**, ALWAYS update ALL THREE files:
- `src/pacemaker/__init__.py` — the Python package version
- `.claude-plugin/plugin.json` — the Claude Code plugin manifest version
- `pyproject.toml` — the packaging metadata version

These MUST always match. Forgetting `plugin.json` has happened before. `pyproject.toml` is
easy to miss too — `tests/test_plugin_hooks_config.py::TestPluginJson::test_plugin_json_version_matches_pyproject`
asserts `plugin.json`'s version equals `pyproject.toml`'s version, so a two-file bump (missing
`pyproject.toml`) fails that test even though `__init__.py` and `plugin.json` agree with each other.

---

## Claude Code Compatibility Policy

**Backwards compatibility is the contract.** When Claude Code introduces a breaking change to its transcript format, hook payload schema, or hook event lifecycle, pace-maker ADAPTS to handle both the old and new behavior. We do NOT bump the minimum supported Claude Code version to force users to upgrade.

**Why**: Forcing every pace-maker user to upgrade Claude Code on every breaking change would brick their install at the worst possible time. Backwards-compat code in pace-maker is annoying to maintain but invisible to users — that's the right tradeoff.

**Minimum supported Claude Code version**: `2.1.39`

This is the floor pace-maker explicitly tests against and guarantees. The hook code can technically read pre-2.1.39 layouts via fallback paths (see `src/pacemaker/hook.py:38, 81`), but anything below `2.1.39` is best-effort, not supported. Below the minimum, the SessionStart hook hard-blocks with an upgrade message — pace-maker refuses to run rather than silently produce wrong telemetry.

**Adding new compatibility shims** when Claude Code ships a breaking change in a future version:
1. Add an entry to the "Tracked breaking changes" list below — version, what changed, what we adapted, where the shim lives
2. Add the shim/fallback code with a comment naming the Claude Code version that introduced the change
3. Add tests that exercise both old and new behaviors
4. **Do NOT bump `min_claude_version`** in config — we support both old and new. Bumping the minimum is reserved for cases where the old behavior is truly unrecoverable (no shim possible).

**Tracked breaking changes**:

| Claude Code version | What changed | Pace-maker adaptation | Tests |
|---------------------|--------------|----------------------|-------|
| `2.1.39` | Subagent transcripts moved from `<project>/agent-*.jsonl` to `<project>/<session-id>/subagents/agent-*.jsonl` | Hook glob searches both locations (`src/pacemaker/hook.py:38, 81`) | `tests/unit/test_subagent_transcript_path.py` |

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

## Minimum Claude Code Version Check (Story #66)

When a user's installed Claude Code is below pace-maker's configured minimum version, SessionStart hard-blocks with an actionable stderr message. All subsequent hooks skip their logic silently (fail-open). Version status is persisted to a dedicated SQLite DB.

### Architecture

- **Version probe**: `subprocess.run(["claude", "--version"], timeout=5)` — any failure (FileNotFoundError, TimeoutExpired, non-zero exit, parse error) returns `None` and the check fails-open (no block).
- **Minimum configured in**: `DEFAULT_CONFIG["min_claude_version"] = "2.1.39"` in `src/pacemaker/constants.py`. Overridable via `pace-maker min-claude-version set X.Y.Z`.
- **Block flag**: `state["version_block_active"] = True` written to `state.json` when installed version is below minimum.
- **Downstream hooks**: PreToolUse and Stop check `version_block_active` at entry and return `{"continue": True}` immediately when set.

### Key Files

- `src/pacemaker/claude_code_version.py` — `ClaudeCodeVersion` dataclass: `parse()`, `compare()`, `is_below()`, `probe_installed_version()`
- `src/pacemaker/version_status_db.py` — SQLite DB following session_registry pattern: `resolve_db_path()`, `record_status()`, `read_status()`
- `src/pacemaker/version_check.py` — `perform_session_start_version_check(state, config, stderr)` with full fail-open wrapper
- `src/pacemaker/hook.py` — SessionStart wiring (after first save_state, before CSA block); PreToolUse and Stop early-return guards
- `src/pacemaker/user_commands.py` — Pattern 27 (`min-claude-version` CLI), `_execute_min_claude_version()`, status line "Claude Code: ..."

### Version Status DB

Follows the session_registry pattern exactly:
- **Env override**: `PACEMAKER_VERSION_STATUS_PATH` overrides DB path
- **Test-mode enforcement**: raises `RuntimeError` if `PACEMAKER_TEST_MODE=1` and env var is unset
- **Single-row upsert**: `INSERT ... ON CONFLICT(id) DO UPDATE SET` — always id=1
- **Fail-open reads**: `read_status()` catches all exceptions at DEBUG level, returns `None`
- **Named constant**: `_READ_TIMEOUT_SECONDS = 5.0`

### CLI Commands

```bash
pace-maker min-claude-version           # Show configured minimum
pace-maker min-claude-version show      # Same
pace-maker min-claude-version set X.Y.Z # Set new minimum
```

### Status Display

`pace-maker status` shows a "Claude Code:" line immediately after the version line:
- Green: `Claude Code: v2.1.126 ✓ (min v2.1.39)` — installed version meets minimum
- Red: `Claude Code: v2.1.10 ✗ (need ≥v2.1.39)` — blocked
- Yellow: `Claude Code: unknown (min v2.1.39, version probe failed)` — fail-open

### Test Isolation

`tests/conftest.py` sets `PACEMAKER_VERSION_STATUS_PATH` to a tmp path in `_guard_production_db` fixture, preventing tests from writing to `~/.claude-pace-maker/version_status.db`.

### Test Files

- `tests/test_claude_code_version.py` — 47 unit tests (parse, compare, is_below, probe, config defaults, DB, CLI)
- `tests/test_version_check_integration.py` — 10 component tests (session start check, downstream hook early returns, recovery)

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

2. **Bounded retry** — if no match found (turn not yet flushed), re-reads the transcript up to `_max_retries=20` times with `_retry_sleep=0.25s` (Messi Rule 14: provable termination, total wait ≤ 5s, 21 reads). Widened from the original ≤1s/10 reads in v2.33.2 (coordinator refinement) to catch more in-window transcript flushes before falling back to fail-closed + re-issue — fewer, longer-spaced reads avoid hammering large (tens-of-MB) transcripts that get re-read in full on every attempt. Both the Write/Edit gate and the danger-bash gate call this function without overriding these parameters, so the function default is the single source of truth for the wait ceiling on both pre-tool gates. The loop returns the INSTANT a match is found — the 5s figure is a MAX wait on the not-yet-flushed path, never a fixed per-edit delay.

3. **Fail-CLOSED on not-ready (v2.33.2)** — if still no match after retries, returns `None` (not `""`). Both the Write/Edit gate AND the danger-bash gate interpret `None` as "transcript-not-ready" and BLOCK (`{"decision": "block", "reason": "..."}`) with a message instructing the agent to re-issue the IDENTICAL tool call — never evaluates the previous turn. **This was changed from fail-OPEN (`{"continue": True}`) to fail-CLOSED** after live observation showed the Write/Edit gate's fail-open branch let raced edits through completely unvalidated (intent validation enforced nothing for any edit that hit this race). The danger-bash gate already proved fail-closed + re-issue works in practice: the agent re-issues the identical command, the re-issue's turn is then flushed, the tool-matched anchor binds to it, and validation proceeds normally on the second attempt (Messi Rule 14: bounded to 2 turns). The `intent_validation_deferred` blockage category and telemetry (activity event + governance event) are still recorded on this path so the race remains observable in `usage.db` / the claude-usage monitor.

4. **Defense-in-depth** — `extract_current_assistant_message(messages, file_path=file_path)` cross-checks the selected message via `_mentions_file`. If it carries an INTENT: marker but mentions a different file → discards and returns `""`, preventing false-passes from wrong-file stale turns. Messages without an INTENT: marker are returned as-is (no silent discard — their Stage-1 rejection log is preserved).

**Key files**:
- `src/pacemaker/transcript_reader.py` — `_tool_input_matches()`, `_find_turn_matching_tool_input()`, updated `get_current_turn_message_for_validation()` (default `_max_retries`/`_retry_sleep`)
- `src/pacemaker/intent_validator.py` — `extract_current_assistant_message(file_path="")` hardening; `validate_intent_and_code` threads `file_path` through
- `src/pacemaker/hook.py` — Write/Edit gate (~line 2811, `if current_message_override is None:`): threads `tool_input`/`tool_name`, fails CLOSED on `None` (v2.33.2); Danger-bash gate (~line 2546): same pattern, no `_max_retries` override (shares the Write/Edit gate's default)
- `src/pacemaker/constants.py` — `BLOCKAGE_CATEGORIES["intent_validation_deferred"]` comment reflects fail-closed (v2.33.2)

**Tests**:
- `tests/test_transcript_staleness_fix.py` — 30 tests: groups 1-6 (21 tests) cover lagged/flushed/stale-same-file scenarios, bounded retry, Bash gate matching, `extract_current_assistant_message` hardening (anchor/matching logic, UNCHANGED by v2.33.2); group 7 `TestHookLevelFailClosed` (2 tests, renamed from `TestHookLevelFailOpen` in v2.33.2) locks in the Write/Edit gate's fail-closed behavior; group 8 `TestDangerBashFailClosed` (2 tests) confirms the danger-bash gate's pre-existing fail-closed behavior is unaffected; group 9 `TestRetryDefaultsWidenedTo5Seconds` (5 tests, new in v2.33.2) covers the ~5s/21-read default and early-return-on-match preservation.
- `tests/test_intent_validation_failclosed_race.py` (new in v2.33.2) — hook-level Bug A core-regression suite: `None` override → block + deferred telemetry; valid-string override → proceeds to Stage 1/2 (not the not-ready path); empty-string override (turn found, no INTENT) → Stage-1 block (not the not-ready path); subagent transcript (`.../subagents/agent-X.jsonl`) → validated correctly for both no-INTENT (block) and INTENT-present (pass) cases.
- `tests/test_intent_validation_deferred_canary.py` — updated in v2.33.2: asserts `decision: "block"` (was `continue: True`); WARNING log + blockage-event + category-constant assertions unchanged.
- `tests/test_real_transcript_replay.py` + `tests/fixtures/real_transcript_replay/manifest.json` — the `_replay_stage1` fidelity-mirror helper's `None`-branch and the 4 pre-flush fixtures' `expected_stage1` flipped from `"YES"` to `"NO"` in v2.33.2 (per the module's own "update this helper IN THE SAME COMMIT" contract).

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

**Supported models**: auto, sonnet, opus, haiku, gpt-5, gemini-flash, gemini-pro

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
7. Return `("BLOCKED: " + message, expression)` — `BLOCKED:` prefix applied mechanically; synthesizer output is only the message body.

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

**Timeouts**: `REVIEWER_WAIT_TIMEOUT_SEC = 60` (per-reviewer via `futures_wait`), `SYNTHESIS_TIMEOUT_SEC = 30` (synthesis via `future.result(timeout=...)`), outer hook timeout = 120s (in `~/.claude/settings.json`).

**Status display**: `pace-maker status` shows full expression (e.g. `opus+gpt-5->haiku`) in ANSI blue — no separate "reviewers:" breakdown line.

**claude-usage display**: Hook Model shows `comp` in `bright_blue`; governance feed shows `[Comp]` in `bright_blue` for competitive expressions.

**Concurrency**: `ThreadPoolExecutor` with `futures_wait(timeout=REVIEWER_WAIT_TIMEOUT_SEC)` — partial results preserved on timeout; `executor.shutdown(wait=False)` avoids blocking on in-flight threads.

**AgyProvider label fix** (Story #77): `_call_single_reviewer()` now returns the verbatim model alias as label for AgyProvider (e.g. `"agy-flash-high"`). Previously fell through to `"anthropic-sdk"` — fixed by adding `elif isinstance(provider, AgyProvider): label = model`.

**Tests**: `tests/test_mechanical.py` (65 tests) — migrated from `tests/unit/test_competitive.py` + full Story #77 truth tables, synthesizer-cannot-flip safety test, stop-gate matrix, N=2/N=3 coverage.

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
- `src/pacemaker/user_commands.py` — regex pattern, valid_models list, confirmation messages
- `claude-usage-reporting/claude_usage/code_mode/display.py` — `REVIEWER_TAGS` agy entries (`"[Agy]"`, `"bright_green"`)
- `tests/test_agy_provider.py` — 29 unit tests (MODEL_MAP, command construction, failure modes)
- `tests/test_agy_registry.py` — 22 tests (KNOWN_MODELS, get_provider routing, reviewer labels, fallback)
- `tests/test_agy_user_commands.py` — 24 tests (regex, execution, status display)
- `claude-usage-reporting/tests/test_agy_display_tags.py` — 21 tests (REVIEWER_TAGS, colors, regex)

---

## Codex Profile Provider (Story #74)

**Grammar:** `codex-<profile>` — regex `^codex-[A-Za-z0-9][A-Za-z0-9._-]*$`

A `codex-<profile>` token binds pace-maker to a named profile in `~/.codex/` (e.g. `~/.codex/beast.config.toml`). The profile pins model+base_url+wire_api so `-m` is NOT passed; the profile config owns the model. pace-maker validates the token **shape only** — unknown profile names are rejected by codex CLI at runtime (non-zero exit → ProviderError → Anthropic fallback).

### CLI Invocation

**Profile mode** (`codex-beast`, `codex-local-llama`, etc.):
```
codex exec - --profile <profile-name> -s read-only
```
No `-m` flag. The `--profile` name is the substring after `"codex-"`.

**Non-profile mode** (plain `codex`, `gpt-5.5`, `gpt-5`, etc.) — unchanged:
```
codex exec - -m <resolved-model> -s read-only
```

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
- `tests/test_codex_profile.py` — 45 tests (is_known_model, _parse_codex_target, argv, routing, label) — Story #74
- `tests/test_codex_profile_story75.py` — 31 tests (competitive parser, reviewer labels, synthesizer routing, CLI regex, execute, status) — Story #75
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

1. **Stop-hook** (`intent_validator.py:parse_sdk_response`): `_find_verdict` uses `is_positive`/`has_block_marker` internally; `parse_sdk_response` is unchanged externally (positive→`{"continue":True}`, BLOCKED→`{"decision":"block"}`, unparseable→fail-open).
2. **Stage 2 Write/Edit gate** (`intent_validator.py` line ~908): replaced `_find_verdict(stage2_feedback) == "APPROVED"` with `verdict_passes(stage2_feedback)`. **Deliberate leniency**: `APPROVED.` and `APPROVED — ok` now PASS (old strict equality would block them).
3. **Danger-bash Phase 2** (`hook.py` line ~2657): replaced `response.strip().upper() == "APPROVED"` with `_verdict_passes(response)`. **Deliberate leniency**: trailing commentary after APPROVED now PASSES.

### Tests

- `tests/test_verdict.py` — 57 parametrized unit tests covering the full truth table, all three sub-functions, context-aware dispatch, and the lenient-flip cases. 100% coverage on `verdict.py`.
