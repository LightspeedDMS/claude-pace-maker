# Changelog

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
