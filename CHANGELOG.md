# Changelog

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
