# Changelog

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
