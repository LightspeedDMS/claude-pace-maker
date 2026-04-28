# Changelog

## [2.20.1] - 2026-04-28

### Fixed
- **Stop hook blocking legitimate stoppages when background agents / scheduled wakeups are in flight**: The stop-hook validator (`src/pacemaker/prompts/stop/stop_hook_validator_prompt.md`) had a "BACKGROUND TASKS ARE REAL" exception, but its keyword list was too narrow ("running in background", "background job", "I'll be notified when it completes", "waiting for background", "run_in_background", "background process") and missed the way Claude actually phrases async delegation in practice — phrases like *"two agents in flight"*, *"will report consolidated findings when both return"*, and *"agent returns"* slipped past detection. Worse, the exception was framed as a single bullet inside the "agent still running fallacy" section, so when the LLM validator processed the conversation top-to-bottom and reached the strict "ALL DEVELOPMENT WORK REQUIRES E2E EVIDENCE" block (or saw "show-stopper" / "production blocking" framing from earlier user messages), it would override the exception and BLOCK the stop, trapping Claude in an awake state while delegated subagents were still working. This wasted context, burned tokens, and prevented the legitimate asynchronous workflow Claude Code is designed to support
- **Stop hook now recognizes `ScheduleWakeup` as a parallel async mechanism**: The validator previously knew only about `run_in_background=True` and treated scheduled wakeups as "Claude avoiding completion". Both mechanisms automatically re-awaken Claude when ready, so both must trigger the APPROVED path. Added explicit recognition of scheduled-wakeup signals: "scheduled wakeup", "scheduled to resume", "ScheduleWakeup", "wakeup at", "next firing", "I'll resume in", "scheduled fire-time", "the loop will continue at", "scheduled re-entry", "drives the loop closure", "explicit wakeup", and similar
- **Stop hook now has an explicit precedence clause**: The expanded "BACKGROUND TASKS / SCHEDULED WAKEUPS ARE REAL (HIGHEST PRECEDENCE)" section now states explicitly that this exception OVERRIDES "show-stopper / blocker / critical / production blocking" framing, the E2E-evidence requirement (the in-flight agents may BE doing the E2E work — demanding it from the orchestrator before they return is impossible by definition), "analysis paralysis" suspicion (delegation IS the action being taken), user urgency phrases ("can't release", "must complete this session", "ASAP"), and apparent unfinished work in the LAST MESSAGE (it IS being worked on, asynchronously). Only a visible launch FAILURE (error / exception / "agent crashed" / "wakeup rejected") with no recovery in progress overrides the exception
- **Stop hook now has a precedence reminder at the top of the E2E section**: A one-line reminder was added to the top of "ALL DEVELOPMENT WORK REQUIRES E2E EVIDENCE" pointing back to the background/wakeup exception, so the LLM validator does not lose the precedence rule when it reaches the strict E2E block. The "WHEN TO BLOCK" bullet about "agent/slash command still running" was also updated to reference both background tasks AND scheduled wakeups as the legitimate exceptions
- **Net behavior**: When Claude's last message announces background-agent delegation or a scheduled wakeup (alone or in combination), the stop hook now correctly returns APPROVED and lets the runtime resume Claude when the async work completes, instead of forcing Claude to stay awake and burn context

## [2.20.0] - 2026-04-18

### Added
- **Memory Localization — git-tracked `.claude-memory/` with auto-linking (story #65)**: New feature that makes Claude Code's per-project memory portable across clones by symlinking the central `~/.claude/projects/<encoded>/memory/` folder to a repo-local `.claude-memory/` directory that developers commit to git. Three flows: (A) **SessionStart auto-link** — when `.claude-memory/` exists at the git root, the hook replaces the central memory folder with a symlink to it; local always wins, central content is safely renamed to `memory.bak_localize` then deleted only after symlink succeeds, with rename-back rollback on OSError. (B) **CLI seed** `pace-maker localize-memory` — copies central memory contents into `<repo>/.claude-memory/` then replaces central with symlink; refuses if `.claude-memory/` already exists. (C) **CLI unlink** `pace-maker memory-localization unlink` — removes the symlink and copies `.claude-memory/` contents back to central; leaves the repo folder in place. Config gate `memory_localization_enabled` (default `true`) togglable via `pace-maker memory-localization on/off/status` and displayed in `pace-maker status`. On all success states (`linked_fresh`, `replaced_with_symlink`, `relinked`, `already_linked`, `raced_but_ok`), the hook injects a nudge via `hookSpecificOutput.additionalContext` telling Claude the memory is git-tracked
- **Path discovery without re-implementing encoding**: Flow A derives the encoded central folder from SessionStart `hook_data.transcript_path` (`Path(transcript_path).parent`); Flow B/C scan `~/.claude/projects/*/*.jsonl` matching the project's cwd. No re-implementation of Claude Code's path-encoding scheme
- **Safety invariants**: `assert_safe_to_destroy(path)` requires `path` under `CENTRAL_BASE` and `path.name == "memory"` before any rmtree; `replace_with_symlink_atomic` renames to `.bak_localize`, symlinks, rmtree's the backup — on OSError the rename is reversed; `_is_under` uses canonicalize-parent-only boundary check so symlink leaves do not escape
- **Optimistic race handling**: On `FileExistsError` during symlink creation, re-classify; if now `correct_symlink` return `raced_but_ok` (two concurrent SessionStart hooks for the same project race safely to convergent state)
- **`PACEMAKER_CENTRAL_BASE` test-mode enforcement**: Mirrors `PACEMAKER_SESSION_REGISTRY_PATH` precedent — `core.py` raises `RuntimeError` when `PACEMAKER_TEST_MODE=1` and the env var is unset, preventing tests from accidentally polluting real `~/.claude/projects/`. `CENTRAL_BASE` resolved dynamically per access via module-level `__getattr__` so per-test `monkeypatch.setenv` changes take effect
- **Classification states**: `missing`, `correct_symlink`, `wrong_symlink`, `regular_folder`, `permission_denied`, `unknown` — explicit handling for each, unknown states logged and skipped rather than destroyed
- **33 unit tests** across `tests/test_memory_localization_classification.py`, `test_memory_localization_linking.py`, `test_memory_localization_seed_restore.py` — including deterministic race-condition test (`raced_but_ok` via monkeypatched `os.symlink` that creates-then-raises-FileExistsError) and OSError rollback test (monkeypatched `os.symlink` raising OSError, assert backup renamed back and content intact)
- **E2E validation against real Claude Code binary**: Headless `claude -p` in a tmp git repo with `.claude-memory/` pre-seeded with synthetic unique facts; verified Claude's internal memory tooling accesses the central symlinked path (`~/.claude/projects/.../memory/`) which transparently resolves through the symlink to workspace `.claude-memory/` files
- **Project CLAUDE.md Memory Localization section**: Architecture notes, Flow A/B/C descriptions, CLI commands, config gate, key-files map, test-sandbox pattern
- **`pace-maker status` memory-localization line**: Displays `Memory Localization: ENABLED/DISABLED` between Danger Bash and Model Preference

## [2.19.3] - 2026-04-18

### Changed
- **SessionStart intent validation guidance — "Senior Coding Nanny" framing**: Added a NAMING directive to `src/pacemaker/prompts/session_start/intent_validation_guidance.md` instructing Claude to refer to validation blockages as coming from the **"Senior Coding Nanny"** rather than "the hook system", "the hooks blocked me", or similar passive phrasing. The underlying blocker is a reviewer LLM agent making a judgment call on whether the declared INTENT matches the diff — not a dumb regex or a mechanical hook. Renaming the actor surfaces that reality to the user and stops Claude from blaming infrastructure for decisions made by a reasoning agent

## [2.19.2] - 2026-04-16

### Fixed
- **Stop hook death spiral on valid E2E evidence**: When the stop hook rejected an E2E evidence table, it injected the rejection as an `isMeta: true` user message. The assistant responded with a short "I'm waiting" message. On the next stop attempt, the backwards-walk context evaluator saw the short message as the "last message" — not the E2E table. Each rejection generated a new short response, pushing the real evidence further down and creating an infinite rejection loop. Fix: `build_stop_hook_context()` in `transcript_reader.py` now filters `isMeta` entries and short (<200 char) assistant responses that immediately follow META messages. Real substantive content (>200 chars) after META is preserved. 5 new tests in `test_stop_hook_meta_filtering.py`
- **Overly rigid E2E evidence format check**: The stop hook validator prompt demanded exact column headers (`| # | AC | Test Description | How Performed | Real System / Data | Observed Result |`) for FORMAT B. Real E2E evidence tables from subagents used similar but different headers like `| # | Test | Command | Captured Output | Result |` — containing the same substantive information (test name, execution method, real captured output, pass/fail) but with different column names. Added FORMAT C to `stop_hook_validator_prompt.md` accepting any markdown table with 3+ rows of real commands, real captured output, and per-row results regardless of exact column headers

## [2.19.1] - 2026-04-15

### Added
- **Cross-Session Awareness Registry (story #64)**: New feature that prevents "rogue agent" hallucinations when multiple Claude Code sessions work concurrently on the same repository. Shared SQLite registry at `~/.claude-pace-maker/session_registry.db` (WAL mode, 2s busy_timeout) tracks live sessions keyed on canonical workspace root resolved via `git rev-parse --show-toplevel` (fallback `os.path.realpath(os.getcwd())`). Sibling discovery fires at SessionStart (startup/resume), at SubagentStart via `hookSpecificOutput.additionalContext`, periodically every 5 PreToolUse calls per agent_id, and before destructive Bash commands (injected into Stage 2 danger_bash validation context). Heartbeats on every hook invocation (SessionStart, SubagentStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop, SubagentStop, Notification, PreCompact, SessionEnd) with 20-minute stale-row purge. Config gate `cross_session_awareness_enabled` (default true) with CLI toggle `pace-maker cross-session-awareness on/off`. Best-effort graceful unregistration on SessionEnd. Fail-open semantics: all registry operations wrapped in broad exception handlers, hook path never crashes on registry errors
- **`pace-maker sessions list` CLI command**: Renders active sibling sessions with workspace, PID, start time, last seen. Stale-row filter (>20 min) applied at render time
- **`pace-maker cross-session-awareness on/off` CLI command**: Toggles the feature flag in `~/.claude-pace-maker/config.json`
- **Namespace-safe state access**: All non-SessionStart CSA handlers use defensive `state.get("cross_session_awareness")` pattern so mid-session config enable/disable does not crash subsequent hooks
- **`PACEMAKER_SESSION_REGISTRY_PATH` test-mode enforcement**: `db.py` raises `RuntimeError` when `PACEMAKER_TEST_MODE=1` is set but the env var is unset, preventing tests from accidentally polluting the live registry
- **Multi-process concurrency test**: `test_three_concurrent_heartbeats_succeed` uses real `multiprocessing.Process` (fork-based) to verify WAL + busy_timeout handles 3 concurrent writers without crash or corruption
- **Project CLAUDE.md Cross-Session Awareness Registry section**: Architecture notes, key file map, CLI reference, config gate, and state schema

### Fixed
- **CSA state pollution across concurrent workspaces (critical bug in 2.19.0 story before ship)**: The original story design assumed `~/.claude-pace-maker/state.json` was per-session, but it is a single global file shared across all live Claude Code sessions. The `cross_session_awareness` namespace was a flat dict, so when a pace-maker session wrote `{"workspace_root": "/.../claude-pace-maker", ...}`, it overwrote a concurrent code-indexer session's cached workspace_root. The code-indexer session's next hook then queried siblings in the pace-maker workspace and received pace-maker sibling sessions, leaking across unrelated repositories. Fix: scope the entire CSA namespace by `session_id` so each session reads and writes only its own sub-dict `state["cross_session_awareness"][<session_id>] = {workspace_root, seen_agent_ids, tool_use_counter}`. Added 6 session-scoping regression tests and a legacy-flat-format migration path. Verified via live outside-in reproduction: a session launched in `/tmp` sees zero pace-maker siblings, a session launched in the pace-maker workspace sees only pace-maker siblings (never the `/tmp` one). `on_session_end` now GCs the session's sub-dict to prevent unbounded growth of state.json. See `src/pacemaker/session_registry/_csa.py` and `tests/test_session_registry_csa_session_scoping.py`
- **Pre-tool hook `_csa_result` UnboundLocalError**: `_csa_result` was declared deep inside the `run_pre_tool_hook` try block, so any exception before that line (malformed stdin, JSON decode errors, transcript path resolution) caused the outer `except Exception` handler to crash with `UnboundLocalError` when calling `_merge_csa_reminder(..., _csa_result)`. Fix: hoisted the initializer to the first statement inside the try block. Added `test_fails_open_on_invalid_stdin_json_no_unbound_local_error` regression test that feeds invalid JSON to the hook and asserts `{"continue": True}` fail-open
- **`build_periodic_reminder` UnicodeEncodeError (surrogate pair)**: `_REMINDER_HEADER` used `\ud83d\udd14` (UTF-16 surrogate pair escape for 🔔) which Python holds in memory but crashes on UTF-8 encoding (e.g., `safe_print` to stdout). Would have broken AC3 (periodic reminder every 5th PreToolUse) in production. Fix: changed to `\U0001F514` (single-codepoint escape). Added three encoding-safety tests for all nudge builders that call `.encode("utf-8")` on the builder output
- **SubagentStart double `hookSpecificOutput` emission**: The refactor that extracted `_emit_subagent_additional_context()` helper called it twice (once for intent validation guidance, once for CSA banner), emitting two JSON objects concatenated on stdout. Claude Code's hook parser expects exactly one JSON object per SubagentStart invocation, so one message would be silently dropped. Fix: accumulate both context strings into a local list and emit once at the end of the handler with blank-line separator. Added integration test that verifies exactly one JSON object is emitted containing both context texts
- **AC3 periodic reminder dropped on Write/Edit return paths**: `_csa_result.get("periodic_reminder")` was only merged into `hookSpecificOutput` on the `tool_name not in ["Write", "Edit"]` path. The Write/Edit branch had 8 return sites that all skipped the merge (no file_path, master disabled, feature disabled, non-source file, unknown tool, approved, blocked, exception handler). Fix: added `_merge_csa_reminder(response, csa_result)` helper and wrapped every Write/Edit return with it. Added 10 parameterized wiring tests covering all 8 return paths
- **Regressions in existing test files from hook.py CSA wiring**: Updated mock signatures in `test_two_stage_validation.py`, `test_stage2_classification_parsing.py`, `test_pre_tool_hook.py`, `test_clean_code_rules_e2e.py`, `test_langfuse_orchestrator_incremental_spans.py`, and `test_secrets_e2e.py` to match the new reviewer-identity tuple return shape and state isolation fixtures. No assertion weakening — only mock-shape corrections
- **`tests/conftest.py` registry isolation**: Added autouse fixture that sets `PACEMAKER_SESSION_REGISTRY_PATH` to a per-test tmp path under the fake HOME, preventing pre-existing tests that happen to trigger a CSA path from polluting the live registry

### Changed
- **Version bump 2.18.2 → 2.19.1**: Minor bump for the cross-session awareness feature (planned as 2.19.0) plus a patch bump for the pre-ship state-pollution fix documented above
- **`scripts/pace-maker`**: Restored to bash shim (had been accidentally converted to Python, breaking the plugin install path's 5 CLI wrapper tests)
- **`src/pacemaker/constants.py`**: Added `DEFAULT_DANGER_RULES_PATH` constant so `_csa.on_pre_tool_use` and the main danger_bash block share a single source of truth instead of duplicating the `os.path.expanduser("~/.claude-pace-maker/danger_bash_rules.yaml")` literal

## [2.18.2] - 2026-04-14

### Fixed
- **Competitive expression CLI regex**: `pace-maker hook-model gpt-5.4+gemini-flash->haiku` was silently dropped by `parse_command()` because the competitive-expression regex character class `[a-z0-9-]+` did not include the literal `.`. The canonical form advertised in help text therefore never reached the hook-model handler — only the legacy alias `gpt-5+...` worked by accident. Character class widened to `[a-z0-9.\-]+`; semantic validation is still delegated to `parse_competitive()`. New tests added for both `gpt-5.4` and legacy `gpt-5` competitive parse paths via `parse_command`

### Changed
- **Centralized model alias map**: Extracted `KNOWN_MODELS` and `SHORT_ALIASES` into a new leaf module `src/pacemaker/inference/model_aliases.py` imported by both `competitive.py` and `codex_provider.py`. Eliminates the hardcoded inline `gpt-5 → gpt-5.4` check previously duplicated in `codex_provider.query()` (Messi rule 4, anti-duplication). Single source of truth; no circular import between sibling providers
- **Stale docstrings/comments updated**: `gpt-5` references in docstrings and comments across `code_reviewer.py`, `constants.py`, `inference/provider.py`, `inference/registry.py`, `intent_validator.py`, and `user_commands.py` replaced with canonical `gpt-5.4` (backward compat note retained where helpful)

## [2.18.1] - 2026-04-14

### Fixed
- **gpt-5.4 Codex CLI model identifier**: `gpt-5.4` is now the canonical model token for Codex CLI (ChatGPT account subscription). The previous token `gpt-5` is retained as a backward-compatible alias that transparently maps to `gpt-5.4` — existing configs and competitive expressions using `gpt-5` continue to work without change. The Codex CLI subprocess always receives `-m gpt-5.4`, fixing the `ERROR: The 'gpt-5' model is not supported` failure that caused the competitive reviewer pipeline to fall back to SDK solo

## [2.18.0] - 2026-04-08

### Added
- **Stop hook E2E enforcement protocol**: Replaced narrow story/epic-only E2E gate with a 3-step protocol covering ALL development work (story/epic, bug fixes, feature additions, behavioral refactors). Step 1 detects development work from session context; Step 2 checks for valid exits (EXIT A: specific not-applicable justification validated by LLM; EXIT B: verbatim user waiver quote, identical rules to TDD override); Step 3 requires E2E evidence in accepted format before allowing stop
- **E2E evidence formats**: Stop hook now accepts FORMAT A (`E2E TEST COMPLETION REPORT` from `e2e-test-heuristic.md` standard with CHANGED CODE COVERAGE + REGRESSION COVERAGE + OVERALL VERDICT) or FORMAT B (E2E Evidence Table with `#`, `AC`, `Test Description`, `How Performed`, `Real System / Data`, `Observed Result` columns); running pytest or claiming "tests pass" explicitly rejected
- **Stop hook exit valve counter**: New `consecutive_stop_blocks` field in session state prevents infinite block loops — increments on each BLOCKED stop, resets to 0 on any tool use (PostToolUse handler), releases on 5th consecutive block (`STOP_EXIT_VALVE_THRESHOLD = 4`); "EV" activity event recorded on activation
- **`STOP_EXIT_VALVE_THRESHOLD` module constant**: Replaces local variable, imported by tests to eliminate threshold duplication across files
- **Minimum 3 E2E tests per acceptance criterion**: `e2e-test-heuristic.md` now mandates happy path + edge case + error/boundary test per AC, enforced by stop hook's minimum row count requirement
- **All-dev-work E2E scope in standards**: `testing-quality-standards.md` updated to state E2E is mandatory for all development (not just story/epic), stop hook enforcement noted, completion criteria strengthened to require accepted format with 3+ rows and real captured output

### Fixed
- **`test_blockage_telemetry.py` stale count**: Pre-existing test failure fixed — category count assertion corrected from 6 to 7 to reflect `intent_validation_dangerbash` category added with Danger Bash feature

## [2.16.0] - 2026-04-07

### Added
- **5 new default clean code rules**: `type-safety-erosion` (TypeScript `any`, Java raw types, C# `dynamic`, Go `interface{}`, Kotlin `!!`), `ignored-error-return` (Go `_, err` discard, Java/Kotlin discarded Result), `unhandled-async` (missing `await`, `async void`, fire-and-forget Promises), `hardcoded-config` (env-specific URLs/ports/paths distinct from secrets and magic numbers), `unsafe-string-interpolation` (command injection and XSS via string interpolation, extends sql-injection coverage)

### Changed
- **Replaced `mutable-defaults` with `unsafe-defaults`**: Generalizes Python-only mutable default argument rule to cover JS/TS module-level mutable defaults and Java/Kotlin static mutable fields used as defaults
- **Patched 5 rule descriptions for multi-language coverage**: `exception-handling` (adds Go `val, _ := fn()` idiom), `resource-leak` (adds Java try-with-resources, C# using, Go defer), `path-traversal` (adds Java/Go/Node examples), `concurrency-hazard` (generalizes from Python global/module-level to Go/Java/C#/JS), `hidden-magic` (adds JS eval, Java/Kotlin reflection, Go reflect, C# dynamic)
- **Default rule count**: 20 → 25

## [2.15.0] - 2026-04-06

### Added
- **Competitive multi-model review pipeline** (#63): New `hook_model` expression syntax `m1+m2[+m3]->synthesizer` dispatches 2–3 reviewers in parallel and synthesizes results; supported models: `auto`, `sonnet`, `opus`, `haiku`, `gpt-5`, `gemini-flash`, `gemini-pro`
- **`inference/competitive.py`** (#63): Core module with `parse_competitive()` (validates expression), `run_competitive()` (parallel dispatch + synthesis), `_dispatch_reviewers()` (ThreadPoolExecutor with per-reviewer timeout), `_synthesize()` (synthesis phase with bounded timeout)
- **Synthesis formatter prompt** (#63): Synthesizer acts as formatter not judge — mechanically consolidates verdicts: any BLOCK from any reviewer → BLOCKED; all APPROVED → APPROVED; does not add or remove concerns
- **Reviewer verdict logging at DEBUG** (#63): First 300 chars of each reviewer's raw response logged via `log_debug()` for synthesis quality evaluation (`MAX_REVIEW_LOG_CHARS = 300`)
- **`COMPETITIVE REVIEW MODE:` section in `pace-maker help`** (#63): Documents expression syntax, model aliases, supported reviewers, failure modes, and examples
- **Named constants for timeouts and sizing** (#63): `REVIEWER_WAIT_TIMEOUT_SEC = 60`, `SYNTHESIS_TIMEOUT_SEC = 30`, `MIN_REVIEWERS = 2`, `MAX_REVIEWERS = 3` replace magic numbers throughout `competitive.py`

### Changed
- **`pace-maker status` competitive display** (#63): Shows full expression (e.g. `opus+gpt-5->haiku`) in ANSI blue instead of "competitive" + separate "reviewers:" breakdown line
- **PreToolUse hook timeout** (#63): Bumped from 60s to 120s in `~/.claude/settings.json` to accommodate 2-phase pipeline (reviewer dispatch + synthesis) without mid-execution kill
- **Synthesis timeout bounded** (#63): `_synthesize()` wraps synthesizer call in `future.result(timeout=SYNTHESIS_TIMEOUT_SEC)` — timeout → first-survivor-wins; avoids indefinite block

### Fixed
- **Individual phase timeouts** (#63): Per-reviewer timeout (`futures_wait(timeout=60s)`) and synthesis timeout (`future.result(timeout=30s)`) replace a single unbounded outer timeout, ensuring partial results are always preserved on slow reviewers

## [2.14.0] - 2026-04-05

### Added
- **Gemini CLI inference provider** (#54): Two new hook model options — `gemini-flash` (→ `gemini-2.5-flash`) and `gemini-pro` (→ `gemini-2.5-pro`) — using Google Gemini CLI subprocess with stdin prompt injection (avoids process-list exposure)
- **Short CLI aliases** (#54): `pace-maker hook-model gem-flash` and `pace-maker hook-model gem-pro` accepted as aliases, stored canonically as `gemini-flash`/`gemini-pro`
- **Reviewer identity tracking for Gemini** (#54): `[REVIEWER:gem-flash]` and `[REVIEWER:gem-pro]` tags in governance event `feedback_text`; displayed as `[Gem]` (cyan) in claude-usage monitor
- **GEM-FLASH / GEM-PRO in `pace-maker status`** (#54): Short display names shown instead of raw model strings
- **Fallback chain for Gemini** (#54): Gemini failure falls back to auto (Anthropic) then fail-open; same chain as Codex provider
- **`GeminiProvider` in `inference/` package** (#54): `GeminiProvider` exported from `inference/__init__.py`; `inference/` subdirectory now deployed by `install.sh`

## [2.13.0] - 2026-04-05

### Added
- **Danger bash CLI CRUD commands** (#62): Full command-line management matching clean-code rules UX — `pace-maker danger-bash list/add/remove/restore/modify/on/off`
- **`danger_bash_cli.py` helper module** (#62): Thin CLI dispatcher with argument parsing, regex validation on add (`re.compile` before write), category validation, and clear error messages
- **CRUD functions in `danger_bash_rules.py`** (#62): `add_rule()`, `remove_rule()`, `restore_rule()`, `modify_rule()`, `_write_config()` (atomic write with directory creation), `format_rules_for_display()` (source tags, pattern truncation, stable sort)
- **`pace-maker status` shows Danger Bash** (#62): Displays ENABLED/DISABLED state alongside Intent Validation and TDD
- **Help text updates** (#62): All danger-bash commands in `pace-maker help` with DANGER BASH RULES documentation section
- **Same-ID warning in loader** (#62): `load_rules()` logs warning when custom rule ID matches a default (skips custom, uses default)
- **54 CRUD unit tests** (#62): Comprehensive coverage for add/remove/restore/modify, validation, atomic write, display formatting

### Changed
- **CLI title** (#62): Updated from "Credit-Aware Adaptive Throttling" to "Credit-Aware Adaptive Throttling... and much more"

## [2.12.0] - 2026-04-05

### Added
- **Danger Bash intent validation** (#58): Two-phase validation for dangerous Bash commands — Phase 1 regex gate fast-rejects commands matching danger rules when no `INTENT:` declaration is present (no LLM call); Phase 2 LLM validates intent-to-command alignment when `INTENT:` is present
- **55 default danger bash regex rules** (#58): `danger_bash_rules_default.yaml` with 25 Work Destruction (WD) rules and 30 System Destruction (SD) rules covering git destructive operations, file deletion, process killing, and infrastructure commands
- **`danger_bash_rules.py` module** (#58): Loads bundled defaults, merges with user customizations from `~/.claude-pace-maker/danger_bash_rules.yaml`, pre-compiles regex patterns at load time, and matches commands against compiled rules (same merge strategy as clean code rules)
- **`intent_validation_dangerbash` blockage category** (#58): New blockage category with "Danger Bash" label for telemetry and blockage stats
- **483 parametrized regex tests** (#58): Comprehensive test coverage for all 55 rules including loader, match (WD and SD categories), performance, and edge cases
- **Reviewer identity tracking** (#60): `resolve_and_call_with_reviewer()` returns `(response, reviewer_name)` tuple; reviewer identity threaded through intent_validator to hook to blockage_events details JSON; governance event `feedback_text` prefixed with `[REVIEWER:xxx]` tag
- **`_refresh_codex_usage()` on fallback** (#60): Codex usage stats refreshed when Codex inference fails and falls back to auto provider

### Changed
- **PreToolUse hook tool matching** (#58): Expanded from `Write|Edit` to include `Bash` tool for danger bash validation (existing Write/Edit validation unchanged)
- **Installer updated** (#58): Deploys `danger_bash_rules_default.yaml` config file alongside existing YAML configs

### Fixed
- **Codex PAYG billing crash** (#59): `_parse_last_token_count()` no longer crashes when Codex CLI returns null primary/secondary fields for PAYG billing plans
- **`limit_id` column in `codex_usage` table** (#59): Added with idempotent `ALTER TABLE` migration; wired migration call in hook.py SubagentStop handler

## [2.11.0] - 2026-04-04

### Added
- **Clean code rules merge strategy** (#55): YAML config now stores only custom rules and deletion markers, not defaults. `load_rules()` merges at runtime: defaults minus deleted, overrides applied at position, custom appended
- **`_load_custom_config()`**: Reads YAML returning `{rules, deleted_rules}` with validation for missing files, empty YAML, invalid types, and malformed entries
- **`_write_config()`**: Atomic write via temp file + `os.replace()` with orphan cleanup on failure (replaces `_write_rules`)
- **`_migrate_snapshot()`**: One-time migration strips old snapshot copies of defaults, preserving only genuine overrides and custom rules
- **`get_rules_metadata()`**: Returns `[{id, source}]` with source as "default", "override", or "custom" for display tagging
- **Source tags in CLI**: `pace-maker clean-code list` now shows `[default]`, `[override]`, `[custom]` tags per rule
- **4 new security rules**: `credential-construction`, `path-traversal`, `resource-leak`, `concurrency-hazard`
- **58 unit tests + 7 integration tests** covering all 24 acceptance criteria

### Changed
- **Default rules refactored from 25 to 20**: Merged redundant rules (`bare-except` + `swallowed-exceptions` → `exception-handling`, 4 size rules → `blob-size`, 2 mock rules → `mock-abuse`, `undeclared-fallbacks` → `silent-degradation`), removed context-dependent rules (`missing-comments`, `missing-invariants`, `excessive-indirection`, `commented-code`), sharpened descriptions for micro-review detectability
- **`add_rule()`**: Only writes custom rule to YAML, clears deletion marker if re-adding
- **`remove_rule()`**: Deletion marker for defaults, direct removal for custom, removes override AND suppresses default
- **`modify_rule()`**: Override copy for defaults, in-place for custom, strips `id` from updates
- **`format_rules_for_display()`**: Optional `config_path` parameter for source tagging

## [2.10.0] - 2026-04-04

### Added
- **Codex GPT-5 usage tracking** (#57): New `src/pacemaker/codex_usage.py` module extracts subscription rate limits from Codex session JSONL files (`~/.codex/sessions/YYYY/MM/DD/*.jsonl`) after each codex subagent run
- **`codex_usage` SQLite table**: Single deterministic record (`id=1`, `INSERT OR REPLACE`) in `usage.db` storing `primary_used_pct`, `secondary_used_pct`, `primary_resets_at`, `secondary_resets_at`, `plan_type`, and `timestamp` — same pattern as `api_cache` and `backoff_state`
- **SubagentStop hook integration**: Automatically captures codex rate limits when `hook_model` contains "gpt" or "codex"; wrapped in isolated try/except to never break existing hook logic
- **18 new unit tests**: `tests/test_codex_usage.py` covering JSONL parser (valid data, missing fields, malformed JSON, no sessions, historical fallback, mtime-based file selection) and SQLite read/write (single record, overwrite, DB errors)

## [2.7.0] - 2026-03-30

### Added
- **Multi-model inference provider abstraction** (#53): New `src/pacemaker/inference/` package enabling non-Anthropic models for hook validation
- **`InferenceProvider` abstract base class** with `ProviderError` exception for provider-agnostic inference
- **`AnthropicProvider`**: Wraps existing `claude_agent_sdk` calls with automatic sonnet↔opus fallback on usage limits
- **`CodexProvider`**: Shells out to OpenAI Codex CLI (`codex exec`) for GPT-5 inference with 120s timeout
- **Provider registry**: `get_provider()` factory, `resolve_model_for_call()` for per-call-site defaults, `resolve_and_call()` orchestrator with cross-vendor fallback chain (selected provider → auto/Anthropic → fail-open)
- **`hook_model` config setting**: New setting separate from `preferred_subagent_model` — controls which model pace-maker uses for its own inference (intent validation, code review, stop hook)
- **`pace-maker hook-model` CLI command**: Set hook inference model via `pace-maker hook-model [auto|sonnet|opus|gpt-5]` (both real CLI and pseudo-CLI)
- **Hook model in status/help**: `hook_model` value displayed in `pace-maker status` and `pace-maker --help`
- **29 new unit tests**: `test_inference_provider.py` covering provider contracts, CodexProvider subprocess handling, registry routing, resolve_and_call fallback chain, and config defaults

### Changed
- **Refactored all 5 inference call sites** to use `resolve_and_call()` provider abstraction:
  - `intent_validator.py`: stop hook validation, intent declaration check, stage 1 validation, stage 2 unified validation
  - `code_reviewer.py`: code review validation
- **Sync wrappers bypass async functions**: Refactored sync wrappers (`call_sdk_validation`, `_call_sdk_intent_validation`, `_call_stage1_validation`, `_call_stage2_validation`) to call `resolve_and_call()` directly, avoiding nested event loop errors
- **claude-usage display**: Relabeled "Model:" to "Subagent:" for `preferred_subagent_model`, added new "Hook Model:" line showing `hook_model` value

## [2.6.0] - 2026-03-30

### Added
- **Governance event feed** (#52): New `governance_events` SQLite table records intent validation rejections (IV), TDD enforcement failures (TD), and clean code violations (CC) with full feedback text, project name, and session ID
- **`record_governance_event()`**: Writes governance events to SQLite with WAL mode and 5s timeout, returns True/False (never raises)
- **`cleanup_old_governance_events()`**: Purges events older than 24h, called from SessionStart hook
- **Hook integration**: Governance events recorded at all 3 rejection paths alongside existing `record_blockage()` calls
- **13 new unit tests**: `test_governance_events.py` covering table creation, IV/TD/CC insertion, success/error returns, concurrent writes, and cleanup

## [2.5.0] - 2026-03-26

### Added
- **8 new Messi-derived clean code rules**: `mock-in-e2e`, `over-engineering`, `code-duplication`, `orphan-code`, `unbounded-loops`, `missing-invariants`, `excessive-indirection`, `hidden-magic` — total rules now 25 (was 17)
- **16 new unit tests** for Messi rules including rule ID uniqueness guard

### Changed
- **Enhanced 4 existing rule descriptions**: `undeclared-fallbacks` (covers alternative code paths, "just in case" logic), `swallowed-exceptions` (covers unchecked return values, explicit handling strategies), `over-mocking` (focused on unit test scope), `large-files` (specific thresholds: Scripts >200, Classes >300, Modules >500)

## [2.4.0] - 2026-03-24

### Fixed
- **Stage 2 rejection categorization non-deterministic** (#51): Replaced fragile keyword matching (`clean_code_keywords` list) with deterministic `CLASSIFICATION:` structured output parsing from stage 2 code review responses. All stage 2 rejections now correctly categorize as `intent_validation_cleancode` instead of being silently miscategorized as `intent_validation`
- **Stage 1 intent validation false rejections**: Softened the stage 1 declaration check prompt to accept `INTENT:` marker anywhere in the message, not just at the start. Added explicit guidance and valid examples showing mid-message intent declarations

## [2.3.0] - 2026-03-10

### Removed
- **Legacy pacing algorithm**: Removed `calculate_logarithmic_target()` and `calculate_delay()` from calculator module — adaptive algorithm now runs unconditionally
- **`use_adaptive` parameter**: Removed from `calculate_pacing_decision()` signature — no longer needed since adaptive is the only algorithm
- **`algorithm` key from pacing decisions**: Return dict no longer contains `"algorithm": "adaptive"` or `"algorithm": "legacy"` — the Algorithm display line in claude-usage-reporting monitor is removed

### Added
- **20 acceptance tests**: `test_legacy_removal.py` validates all legacy code paths are removed and adaptive runs unconditionally

## [2.2.1] - 2026-03-10

### Fixed
- **Fallback mode 5-hour window rollover** (#46): `_get_synthetic_snapshot()` now correctly filters accumulated costs by the rollover boundary timestamp, reporting only post-rollover usage instead of inflated values from the prior window
- **Fallback rollover persistence independence** (#46): `_get_synthetic_snapshot()` now persists rollover state independently, eliminating the ordering dependency on `get_reset_windows()` being called first
- **7-day fallback rollover** (#46): Same rollover fix applied to the 7-day window path, ensuring consistent behavior across both window types

## [2.2.0] - 2026-03-09

### Added
- **`pace-maker install claude-usage-monitor` command**: New subcommand installs the claude-usage-monitor tool from its repository (Story #45)
- **HTTPS-first git auth detection**: `detect_git_auth()` now tries plain HTTPS before falling back to SSH, improving compatibility with public repositories
- **Independent test runner**: `scripts/run_tests.sh` runs each test file in its own pytest process with a 30-second timeout, eliminating SQLite WAL contention hangs during test suite execution

### Fixed
- **install.sh CLI path detection**: Corrected detection of the pace-maker CLI executable path during installation
- **Python 3.9 version checks**: All version gating now correctly handles Python 3.9 (requirement lowered from >=3.10 to >=3.9)
- **`--version` flag verification**: Installer now verifies the deployed CLI responds correctly to `--version`
- **SQLite WAL contention in tests**: Resolved hangs caused by concurrent test files sharing WAL-mode databases in the same pytest process
- **Naive datetime bug**: Fixed remaining naive datetime comparisons that caused crashes when mixing timezone-aware and naive objects

### Changed
- **Minimum Python version**: Lowered from >=3.10 to >=3.9 to broaden supported environments

## [2.1.0] - 2026-03-08

### Fixed
- **SA indicator color on stop**: SubagentStop now correctly shows the blue indicator instead of an incorrect color
- **LF indicator gating**: Langfuse (LF) indicator is only shown when a trace was actually pushed, not on every hook invocation
- **SM indicator gating**: Secrets Masking (SM) indicator is only shown when secrets were actually masked in the trace, not on every sanitize call

## [2.0.0] - 2026-03-08

### Added
- **Global API poll coordination** (#43): SQLite-backed singleton ensures only one session polls the Claude API at a time, eliminating redundant concurrent requests across parallel sessions
- **Activity events table**: New `activity_events` SQLite table records hook invocations with timestamps for activity indicator computation
- **Hook instrumentation**: All hook entry points record activity events for accurate timing and settings-awareness
- **Activity indicator help text**: `pace-maker help` now documents all activity indicator symbols (PL, LF, SM, SS, SA) and their color meanings
- **PL indicator colors**: Pacing (PL) indicator uses blue/yellow/red to reflect normal/warning/critical pacing state
- **Fallback coefficients in status**: `pace-maker status` displays the active cost-to-utilization coefficients, including calibrated fallback values
- **COEFFICIENTS section in help**: `pace-maker help` includes an explanation of how fallback mode coefficients work and how they are calibrated

### Fixed
- **Naive/aware datetime mismatch**: Replaced all `datetime.now()` and `datetime.utcnow()` calls in `hook.py` and supporting modules with `datetime.now(timezone.utc)`, eliminating crashes in the pacing engine caused by mixing naive and aware datetime objects
- **Activity indicator timing**: Corrected timing windows used to determine whether recent activity qualifies for indicator display
- **SS indicator gating**: SessionStart (SS) indicator now fires only when new secrets are detected, not on every session start
- **SM indicator placement**: Secrets Masking (SM) indicator fires in the orchestrator after `sanitize_trace()` completes, ensuring it reflects actual masking results
- **Global poll coordination code review findings** (#43): Addressed follow-up issues identified during code review of the SQLite poll coordination feature

## [1.19.0] - 2026-03-07

### Added
- **Complete JSON-to-SQLite migration**: All remaining state management (fallback state, backoff state, API cache, profile cache) migrated from JSON files to `UsageModel` SQLite tables, eliminating TOCTOU races between concurrent sessions
- **Test safety guard**: `conftest.py` now sets `PACEMAKER_TEST_MODE=1` to prevent test runs from polluting the production SQLite database

### Removed
- **Dead `api_backoff.py` module**: Deleted legacy JSON-based backoff implementation replaced entirely by `UsageModel` SQLite in v1.18.0
- **Dead fallback JSON code paths**: Removed all remaining read/write paths that previously fell back to JSON files for state storage

## [1.18.0] - 2026-03-07

### Added
- **Resilient Fallback Mode**: When the Claude API returns 429 errors, pace-maker enters fallback mode and synthesizes utilization estimates from accumulated token costs. Includes automatic state machine transitions (NORMAL → FALLBACK → NORMAL), per-session cost tracking in SQLite, and rollover-safe window projections
- **UsageModel — Single Source of Truth**: New `UsageModel` class (`src/pacemaker/usage_model.py`) unifies all usage data access. Stateless between calls with all state in SQLite WAL mode. Both pace-maker hooks and claude-usage monitor read from the same source
- **Coefficient Calibration**: When the API recovers after a fallback period, compares synthetic predictions against real API values. Auto-adjusts cost-to-utilization coefficients via weighted average, stored per tier (5x/20x) in `calibrated_coefficients` SQLite table
- **SQLite State Migration**: Fallback state, API cache, backoff state, and profile cache moved from JSON files to SQLite tables (`fallback_state_v2`, `api_cache`, `backoff_state`, `profile_cache`). Eliminates TOCTOU races between concurrent sessions
- **Accumulated Cost Tracking**: `accumulated_costs` table with INSERT-only concurrency-safe cost accumulation (no read-modify-write). Idempotent per-session deduplication prevents double-counting
- **Pressure Tests**: 23 new tests covering 5h/7d cycle switching, coefficient calibration, rollover handling, and edge cases

### Fixed
- **Rollover detection in synthetic mode**: Fixed production bug where `_project_window()` returned `five_rolled=False` after `get_reset_windows()` persisted the projected window. Now checks persisted `rollover_cost_5h/7d` as primary indicator
- **Stale JSON fallback guard**: `is_fallback_active()` no longer lets stale `fallback_state.json` override empty SQLite table during transition period
- **Tier-aware calibration**: `calibrate_on_recovery()` stores under detected tier (e.g., "20x") from profile cache, not hardcoded "5x"
- **Per-project fallback lock**: Scoped fallback lock file path to prevent cross-project interference

## [1.17.0] - 2026-02-22

### Added
- **Version bump TDD bypass**: Stage 1 validation now skips TDD enforcement for version-bump-only changes to core path files, avoiding unnecessary test declarations for trivial version string updates

## [1.11.0] - 2026-02-11

### Fixed
- **Per-turn token counting**: Generation observations now report tokens for the current turn only, not accumulated across the entire transcript. Fixes inflated cost reporting (e.g. $3.74 reported vs actual ~$0.02 per turn)
- **Subagent transcript path detection**: Search new Claude Code 2.1.39+ nested directory structure (`<session-id>/subagents/agent-*.jsonl`) with backward compatibility for old flat structure

### Added
- **Subagent generation observations**: Subagent traces now include `generation-create` events with token usage and cost, matching main session behavior
- **Per-turn token counting tests**: 3 tests for turn boundary detection and token scoping
- **Subagent generation tests**: 4 tests for subagent cost tracking

## [1.10.0] - 2026-02-10

### Fixed
- **Langfuse trace pipeline**: Fixed 12 bugs in trace/span lifecycle (deferred push, pending trace flush, subagent state corruption, BrokenPipeError protection)
- **Generation observation**: Added `generation-create` event to stop hook for Langfuse totalCost computation (traces/spans alone don't compute cost)

### Added
- **BrokenPipeError protection**: All stdout writes use `safe_print()` to prevent hook crashes

## [1.9.0] - 2026-02-09

### Fixed
- **Intel prompt value formats**: Enforce strict decimal/code formats to prevent text-based values that break the parser

## [1.8.0] - 2026-02-08

### Added
- **Prompt Intelligence (Intel)**: Per-prompt metadata telemetry (`§` intel lines with frustration, specificity, task type, quality, iteration)
- **Intel Langfuse Integration**: Parsed intel attached to Langfuse traces as `intel_*` metadata keys for dashboard filtering
- **Intel Guidance Prompt**: Session-start injection of intel symbol vocabulary
- **Langfuse Provisioner E2E Tests**: End-to-end test coverage for auto-provisioning

## [1.7.0] - 2026-02-06

### Added
- **Secrets Management**: Sanitizes sensitive data (API keys, tokens, passwords) from Langfuse trace outputs before pushing

### Fixed
- **Langfuse Tool Output Capture**: Fixed tool output capture for accurate trace content

## [1.6.0] - 2026-02-05

### Added
- **Langfuse Auto-Provisioning**: Automatic API key provisioning with configurable URL via `pace-maker langfuse configure`
- **Langfuse Status Display**: Shows provisioning URL and connectivity in `pace-maker langfuse status`

## [1.5.0] - 2026-02-04

### Added
- **Daily Log Rotation**: One log file per day (`pace-maker-YYYY-MM-DD.log`), 15 days retention
- **Enhanced Status Display**: Shows versions, Langfuse connectivity, 24-hour error counts
- **Mypy Type Fixes**: Resolved all type errors in langfuse modules

## [1.4.1] - 2026-02-04

### Added
- **Langfuse telemetry integration**: Direct HTTP API integration for tracing Claude Code sessions
- **Blockage telemetry tracking**: Track and report intent validation blockages via CLI stats
- **Model preference to status display**: Shows preferred model in pace-maker status output
- **Stale data detection**: Resilient pacing calculations handle stale/missing data gracefully

### Fixed
- **Intent validation message count**: Fixed to n=2 (minimum required because Claude Code writes text content and tool_use as separate transcript entries)
- **TDD blockage tracking**: Proper tracking of TDD enforcement blockages
- **Stop hook tempo checker**: More permissive handling of incomplete context

### Changed
- **Package description**: Updated to reflect full feature set (pacing, intent validation, TDD enforcement, Langfuse telemetry)

## [1.5.0] - 2025-12-09

### Added
- **Two-stage validation system**: Separates fast declaration checking (Stage 1, ~2-4s) from comprehensive code review (Stage 2, ~10-15s)
- **Stage 1 (Fast Declaration Check)**: Uses Sonnet to validate intent declaration exists with all required components
- **Stage 2 (Comprehensive Code Review)**: Uses Opus (with Sonnet fallback) for deep code quality validation
- **TDD enforcement CLI**: `pace-maker tdd on|off` command
- **Clean code rules CLI**: `pace-maker clean-code list|add|remove` commands
- **Core paths CLI**: `pace-maker core-paths list|add|remove` commands
- **Log level control**: `pace-maker loglevel 0-4` command (0=OFF, 1=ERROR, 2=WARNING, 3=INFO, 4=DEBUG)
- **Externalized clean code rules**: `~/.claude-pace-maker/clean_code_rules.yaml`
- **Externalized core paths**: `~/.claude-pace-maker/core_paths.yaml`
- **Centralized logging system**: Configurable log levels across all modules

### Changed
- **Message extraction**: Combines last 2 messages to handle Claude Code's text/tool call splitting
- **Stage 1 model**: Changed from Haiku to Sonnet for better intent detection
- **Stage 2 response format**: Returns "APPROVED" text instead of empty string for pass
- **Model naming**: All models use generic aliases (claude-sonnet-4-5, claude-opus-4-5, claude-haiku-4-5) that auto-update
- **Prompt organization**: Reorganized into pre_tool_use/, common/, stop/, session_start/, user_commands/ directories
- **Exception handling**: Validation now fails closed (blocks) on errors instead of failing open
- **Intent declaration requirement**: Must be in SAME message as Write/Edit tool (not in prior messages)

### Fixed
- **Installer**: Now correctly copies Python modules to `~/.claude/hooks/pacemaker/`
- **Model names**: Fixed non-existent claude-haiku-4 model name
- **Thinking budget**: Increased from 1000 to 1024 (API minimum requirement)
- **PyYAML dependency**: Added for python3.11 compatibility

## [1.4.0] - 2025-12-03

### Added
- **Pre-tool intent validation**: Claude must declare intent (FILE, CHANGES, GOAL) before code modifications
- **Light-TDD enforcement**: Core code paths (`src/`, `lib/`, `core/`, `source/`, `libraries/`, `kernel/`) require test declarations or explicit user permission to skip
- **Clean code validation**: Blocks 15 categories of violations including hardcoded secrets, SQL injection, bare except clauses, magic numbers, mutable defaults, over-mocked tests, and logic bugs
- **Code-intent alignment checks**: Detects scope creep, missing functionality, and unauthorized deletions
- **5-hour limit toggle**: `pace-maker 5-hour-limit on|off` command
- **Intent validation toggle**: `pace-maker intent-validation on|off` command
- **Test report**: `reports/pre_tool_validation_test_report.md` documenting all validation behaviors

### Changed
- Pre-tool validator uses Opus as primary model with Sonnet fallback
- Message context expanded to 5 messages (4 text-only + 1 full with tool parameters)

## [1.3.1] - 2025-11-XX

### Added
- Subagent reminder system for main context delegation
- Session lifecycle tracking (tempo) with AI validation

## [1.3.0] - 2025-11-XX

### Added
- Weekend-aware throttling algorithm
- 12-hour preload allowance for weekday starts
- 95% safety buffer targeting

## [1.2.0] - 2025-11-XX

### Added
- Dual window support (5-hour and 7-day limits)
- Adaptive delay calculation

## [1.1.0] - 2025-11-XX

### Added
- CLI interface (`pace-maker` command)
- Configuration file support

## [1.0.0] - 2025-11-XX

### Added
- Initial release
- Basic credit throttling via PostToolUse hook
- SQLite usage database
