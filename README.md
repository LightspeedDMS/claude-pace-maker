# Claude Pace Maker

Intelligent credit consumption throttling and code quality enforcement for Claude Code.

## Features

- **Credit Throttling**: Adaptive pacing for 5-hour and 7-day usage windows
- **Intent Validation**: Requires intent declaration before code modifications
- **TDD Enforcement**: Core code paths require test declarations
- **Clean Code Checks**: Blocks security vulnerabilities, anti-patterns, and logic bugs
- **Session Lifecycle**: Prevents premature session endings via AI validation

## Installation

**Option 1: pipx (recommended)**

```bash
pipx install git+https://github.com/LightspeedDMS/claude-pace-maker.git
claude-pace-maker
```

**Option 2: From source**

```bash
git clone https://github.com/LightspeedDMS/claude-pace-maker.git
cd claude-pace-maker
./install.sh
```

## CLI Commands

```bash
pace-maker status                  # Show current status
pace-maker on|off                  # Master switch - enable/disable ALL hooks
pace-maker weekly-limit on|off    # Enable/disable 7-day limit
pace-maker 5-hour-limit on|off    # Enable/disable 5-hour limit
pace-maker tempo on|off           # Enable/disable session lifecycle (global)
pace-maker tempo session on|off   # Override tempo for current session
pace-maker reminder on|off        # Enable/disable subagent reminder
pace-maker intent-validation on|off  # Enable/disable pre-tool validation
pace-maker version                # Show version
pace-maker help                   # Show help
```

## Intent Validation System

When enabled, Claude must declare intent before modifying source code files.

### Required Declaration Format

Before any code edit, declare:
1. **FILE**: Which file is being modified
2. **CHANGES**: What specific changes are being made
3. **GOAL**: Why the changes are being made

Example:
```
I will modify src/auth.py to add a validate_password() function
that checks password strength, to improve security.
```

### TDD Enforcement for Core Code

Files in core paths (`src/`, `lib/`, `core/`, `source/`, `libraries/`, `kernel/`) require:

**Option A - Declare test coverage:**
```
I will modify src/auth.py to add validate_password().
Test coverage: tests/test_auth.py - test_validate_password_rejects_weak()
```

**Option B - Quote user permission to skip TDD:**
```
I will modify src/auth.py to add validate_password().
User permission to skip TDD: User said "skip tests for this" in message 3.
```

The quoted permission must exist in the last 5 messages. Fabricated quotes are rejected.

### Clean Code Violations Blocked

| Violation | Description |
|-----------|-------------|
| Hardcoded secrets | API keys, passwords, tokens in code |
| SQL injection | String concatenation in queries |
| Bare except | `except:` without specific exception type |
| Swallowed exceptions | `except: pass` without logging |
| Magic numbers | Unexplained numeric literals |
| Mutable defaults | `def func(x=[])` anti-pattern |
| Commented-out code | Dead code without explanation |
| Deep nesting | 6+ levels of conditionals |
| Large methods | Functions exceeding ~50 lines |
| Undeclared fallbacks | Hidden default behaviors |
| Logic bugs | Off-by-one errors, boundary issues |
| Over-mocked tests | Tests that mock the code under test |
| Scope creep | Code changes not declared in intent |
| Missing functionality | Declared features not implemented |
| Unauthorized deletions | Removing code not mentioned in intent |

### Configuration

Source code extensions: `~/.claude-pace-maker/source_code_extensions.json`

## Credit Throttling

### How It Works

1. Monitors credit usage via Claude API
2. Calculates target pace based on time remaining in window
3. Applies adaptive delays when over budget
4. Weekend-aware: excludes Sat/Sun from calculations

### Algorithm

- **Safety Buffer**: Targets 95% of allowance (5% headroom)
- **12-Hour Preload**: First 12 weekday hours get 10% allowance
- **Zero Tolerance**: Throttles immediately when over budget
- **Adaptive Delay**: Calculates exact delay to reach 95% by window end

## Session Lifecycle (Tempo)

Prevents Claude from ending sessions prematurely.

When Claude attempts to stop:
1. System reads original user prompt
2. Extracts last 10 conversation messages
3. AI validates if request was completed
4. **APPROVED**: Session ends
5. **BLOCKED**: Claude receives feedback and continues

## Hooks

| Hook | Function |
|------|----------|
| SessionStart | Initializes state |
| UserPromptSubmit | Captures prompts, handles CLI commands |
| PreToolUse | Intent validation, TDD checks, clean code validation |
| PostToolUse | Credit throttling, subagent reminders |
| Stop | Session completion validation |
| SubagentStart/Stop | Tracks subagent context |

## Configuration File

`~/.claude-pace-maker/config.json`:

```json
{
  "enabled": true,
  "weekly_limit_enabled": true,
  "five_hour_limit_enabled": true,
  "tempo_enabled": true,
  "intent_validation_enabled": true,
  "subagent_reminder_enabled": true,
  "base_delay": 5,
  "max_delay": 350,
  "safety_buffer_pct": 95.0,
  "preload_hours": 12.0
}
```

## Requirements

- Python 3.10+
- Claude Agent SDK
- jq
- Bash

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Preload System](docs/PRELOAD_SYSTEM.md)
- [Weekend Algorithm](docs/WEEKEND_AWARE_ALGORITHM.md)
- [Test Report](reports/pre_tool_validation_test_report.md)

## License

MIT
