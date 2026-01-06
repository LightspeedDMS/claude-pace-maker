# Claude Pace Maker

Intelligent credit consumption throttling and code quality enforcement for Claude Code.

## Features

- **Credit Throttling**: Adaptive pacing for 5-hour and 7-day usage windows
- **Model Preference**: Nudges Claude to use specific model for subagents (quota balancing)
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
pace-maker tdd on|off             # Enable/disable TDD enforcement
pace-maker clean-code list        # List all clean code rules
pace-maker clean-code add NAME DESCRIPTION  # Add custom clean code rule
pace-maker clean-code remove NAME # Remove clean code rule
pace-maker core-paths list        # List core code paths
pace-maker core-paths add PATH    # Add core code path
pace-maker core-paths remove PATH # Remove core code path
pace-maker prefer-model opus|sonnet|haiku|auto  # Set model preference for subagents
pace-maker loglevel 0-4           # Set log level (0=OFF, 1=ERROR, 2=WARNING, 3=INFO, 4=DEBUG)
pace-maker version                # Show version
pace-maker help                   # Show help
```

## Intent Validation System

Two-stage AI validation system that ensures code quality and intent transparency.

### Two-Stage Validation Architecture

**Stage 1: Fast Declaration Check** (~2-4 seconds)
- Model: Claude Sonnet 4.5
- Validates intent declaration exists with all 3 required components
- Checks TDD declarations for core code paths
- Returns: YES, NO, or NO_TDD

**Stage 2: Comprehensive Code Review** (~10-15 seconds)
- Model: Claude Opus 4.5 (falls back to Sonnet on rate limits)
- Validates code matches declared intent exactly
- Checks for clean code violations (security, anti-patterns, bugs)
- Detects scope creep and unauthorized changes
- Returns: APPROVED or detailed violation feedback

### Required Declaration Format

Before any code edit, declare in the SAME message as the Write/Edit tool:
1. **FILE**: Which file is being modified
2. **CHANGES**: What specific changes are being made
3. **GOAL**: Why the changes are being made

Example:
```
I will modify src/auth.py to add a validate_password() function
that checks password strength, to improve security.

[then use Write/Edit tool in same message]
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

### Configuration Files

All configuration is externalized for easy customization:

- **Config**: `~/.claude-pace-maker/config.json` - Main configuration (enable/disable features, log level)
- **Extensions**: `~/.claude-pace-maker/source_code_extensions.json` - Source file extensions to validate
- **Clean Code Rules**: `~/.claude-pace-maker/clean_code_rules.yaml` - Customizable code quality rules
- **Core Paths**: `~/.claude-pace-maker/core_paths.yaml` - Directories requiring TDD enforcement
- **Prompts**: `~/.claude/hooks/pacemaker/prompts/` - Validation prompt templates

Use CLI commands to manage rules and paths without editing YAML directly.

### Modifying Validation Prompts Without Loops

⚠️ **CRITICAL**: When modifying intent validator prompts, disable validation temporarily to prevent infinite loops.

**Problem**: Editing validation prompts while validation is enabled creates recursive validation loops.

**Solution**: Temporarily bypass validation during prompt development.

```bash
# BEFORE modifying prompts in src/pacemaker/prompts/
pace-maker intent-validation off

# Now safe to edit validation prompt files:
# - src/pacemaker/prompts/pre_tool_use/pre_tool_validator_prompt.md
# - src/pacemaker/prompts/stop/stop_hook_validator_prompt.md
# - src/pacemaker/prompts/common/intent_declaration_prompt.md

# Make your prompt changes...

# AFTER completing prompt modifications
pace-maker intent-validation on
```

**Workflow**:
1. Disable validation: `pace-maker intent-validation off`
2. Edit prompt files in `src/pacemaker/prompts/`
3. Deploy changes: `./install.sh` (copies prompts to `~/.claude/hooks/`)
4. Re-enable validation: `pace-maker intent-validation on`
5. Test the updated prompts

**Note**: This applies to ALL prompt file modifications that affect validation logic. Prompts for other hooks (session_start, user_commands) don't require validation bypass.

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

## Model Preference (Quota Balancing)

Controls which model Claude uses for subagent Task tool calls to balance quota consumption across models.

### The Problem

Claude Pro Max has separate quotas for different models (Opus, Sonnet). When one model's quota is consumed faster than another, you can hit rate limits on one while having unused capacity on the other.

**Example scenario:**
- 7-day overall usage: 82%
- Sonnet-specific usage: 96%
- Opus-specific usage: 60%

Without intervention, Claude defaults to Sonnet for subagents, exhausting that quota while Opus capacity sits unused.

### The Solution

```bash
pace-maker prefer-model opus    # Force subagents to use Opus
pace-maker prefer-model sonnet  # Force subagents to use Sonnet
pace-maker prefer-model haiku   # Force subagents to use Haiku
pace-maker prefer-model auto    # No preference (default behavior)
```

### How It Works

When a model preference is set, the system injects **mandatory nudges** at two points:

1. **Session Start**: Shows current usage stats and the required model
2. **Post-Tool Reminders**: After every tool use, reminds Claude to use the preferred model

The nudge is assertive:
```
⚠️  MANDATORY MODEL PREFERENCE: OPUS

   You MUST use model: "opus" for ALL Task tool subagent calls.

   WHY: This is for QUOTA BALANCING, not capability.
   The user needs to balance token consumption across models to maximize
   their usage window. Even if the default model 'works fine', using the
   preferred model (opus) helps prevent hitting rate limits.

   REQUIRED FORMAT:
   Task(subagent_type='...', model='opus', prompt='...')
```

### Important Notes

- **Main session model cannot change mid-conversation** - to switch the main conversation model, restart with `claude --model opus`
- The nudge is about **resource management**, not capability - Claude should use the preferred model even if the default "works fine"
- Set to `auto` to disable nudging and return to default behavior

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
  "preferred_subagent_model": "auto",
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
