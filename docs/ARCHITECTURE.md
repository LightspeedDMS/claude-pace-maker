# Claude Pace Maker - Architecture Documentation

## Table of Contents

1. [System Overview](#system-overview)
2. [Core Components](#core-components)
3. [Hook Integration](#hook-integration)
4. [Pre-Tool Validation System](#pre-tool-validation-system)
5. [Tempo System](#tempo-system)
6. [Throttling Algorithms](#throttling-algorithms)
7. [Weekend-Aware Logic](#weekend-aware-logic)
8. [Safety Buffer System](#safety-buffer-system)
9. [Data Flow](#data-flow)
10. [Configuration](#configuration)
11. [Database Schema](#database-schema)
12. [Error Handling](#error-handling)

---

## System Overview

Claude Pace Maker is a hook-based system for Claude Code that provides:
- **Credit throttling**: Prevents hitting API rate limits by intelligently pacing tool executions
- **Pre-tool validation**: Enforces intent declaration, TDD for core code, and clean code standards
- **Session lifecycle**: Prevents premature session endings via AI-powered completion validation

### Architecture Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                      Claude Code                              │
│  ┌────────────┐                           ┌────────────┐     │
│  │ Tool       │──── PostToolUse Hook ────▶│ Pace Maker │     │
│  │ Execution  │                           │ Hook       │     │
│  └────────────┘                           └──────┬─────┘     │
│                                                  │            │
│  ┌────────────┐                                 │            │
│  │ User       │──── UserPromptSubmit Hook ──────┤            │
│  │ Command    │                                 │            │
│  └────────────┘                                 │            │
└────────────────────────────────────────────────┼────────────┘
                                                  │
                          ┌───────────────────────┼───────────────────────┐
                          │   Pace Maker System   │                       │
                          │                       ▼                       │
                          │  ┌─────────────────────────────────────────┐ │
                          │  │ Pacing Engine                           │ │
                          │  │  - Poll API every 60s                   │ │
                          │  │  - Calculate allowance                  │ │
                          │  │  - Apply safety buffer (95%)            │ │
                          │  │  - Determine throttle decision          │ │
                          │  └───────────┬─────────────────────────────┘ │
                          │              │                               │
                          │    ┌─────────┴──────────┐                   │
                          │    │                    │                   │
                          │    ▼                    ▼                   │
                          │  ┌──────────────┐  ┌──────────────────┐    │
                          │  │ Weekend-Aware│  │ Safety Buffer    │    │
                          │  │ Algorithm    │  │ System           │    │
                          │  │              │  │                  │    │
                          │  │ - Count      │  │ - 95% target     │    │
                          │  │   weekday    │  │ - 5% headroom    │    │
                          │  │   seconds    │  │ - Prevent limits │    │
                          │  │ - Freeze on  │  │                  │    │
                          │  │   weekends   │  │                  │    │
                          │  └──────────────┘  └──────────────────┘    │
                          │              │                               │
                          │              ▼                               │
                          │  ┌─────────────────────────────────────────┐│
                          │  │ SQLite Database                          ││
                          │  │  - Usage snapshots                       ││
                          │  │  - Session tracking                      ││
                          │  └─────────────────────────────────────────┘│
                          └─────────────────────────────────────────────┘
```

---

## Core Components

### 1. Hook Entry Point (`src/pacemaker/hook.py`)

Main entry point for Claude Code hooks.

**Responsibilities**:
- Load configuration from `~/.claude-pace-maker/config.json`
- Manage hook state (last poll time, session ID)
- Dispatch to appropriate hook handler
- Execute sleep delays for throttling

**Key Functions**:
- `run_hook()` - PostToolUse hook handler
- `execute_delay(delay_seconds)` - Sleep for throttling (max 350s)
- `run_user_prompt_submit()` - UserPromptSubmit hook handler
- `load_config()` / `save_state()` - State management

### 2. Pacing Engine (`src/pacemaker/pacing_engine.py`)

Orchestrates usage monitoring and pacing decisions.

**Responsibilities**:
- Poll Claude API every 60 seconds
- Calculate pacing decisions for both windows (5-hour, 7-day)
- Determine most constrained window
- Apply weekend-aware adaptive algorithm
- Manage database cleanup (daily)

**Key Functions**:
```python
def calculate_pacing_decision(
    five_hour_util: float,
    five_hour_resets_at: Optional[datetime],
    seven_day_util: float,
    seven_day_resets_at: Optional[datetime],
    threshold_percent: int = 0,
    base_delay: int = 5,
    max_delay: int = 350,
    use_adaptive: bool = True,
    safety_buffer_pct: float = 95.0
) -> Dict
```

Returns:
```python
{
    'should_throttle': bool,
    'delay_seconds': int,
    'constrained_window': '5-hour'|'7-day',
    'deviation_percent': float,
    'five_hour': {...},
    'seven_day': {...},
    'strategy': {...}
}
```

### 3. Adaptive Throttle (`src/pacemaker/adaptive_throttle.py`)

Weekend-aware adaptive throttling algorithm with safety buffer.

**Responsibilities**:
- Calculate weekend-aware allowance
- Apply 95% safety buffer
- Project forward to calculate optimal delay
- Support both weekend-aware and legacy modes

**Key Functions**:
```python
def calculate_allowance_pct(
    window_start: datetime,
    current_time: datetime,
    window_hours: float
) -> float:
    """Calculate allowance % based on weekday seconds elapsed."""

def count_weekday_seconds(
    start_dt: datetime,
    end_dt: datetime
) -> int:
    """Count only weekday (Mon-Fri) seconds, exclude weekends."""

def is_weekend(dt: datetime) -> bool:
    """Check if datetime falls on Sat/Sun."""

def calculate_adaptive_delay(
    current_util: float,
    window_start: datetime,
    current_time: datetime,
    time_remaining_hours: float,
    window_hours: float,
    estimated_tools_per_hour: float = 10.0,
    min_delay: int = 5,
    max_delay: int = 350,
    safety_buffer_pct: float = 95.0
) -> dict:
    """Calculate optimal delay with safety buffer."""
```

### 4. API Client (`src/pacemaker/api_client.py`)

Interfaces with Claude API to fetch usage data.

**Responsibilities**:
- Call Claude usage API endpoint
- Parse JSON responses
- Handle API errors gracefully

### 5. Database (`src/pacemaker/database.py`)

SQLite database for usage tracking.

**Responsibilities**:
- Store usage snapshots
- Track sessions
- Support historical queries
- Daily cleanup of old data

### 6. User Commands (`src/pacemaker/user_commands.py`)

Handles `pace-maker status/on/off` commands.

**Responsibilities**:
- Parse user commands
- Update configuration
- Display status with safety buffer info
- Format user-friendly output

---

## Hook Integration

### PostToolUse Hook

**Trigger**: After EVERY tool execution in Claude Code (not just during API polls)
**Script**: `~/.claude/hooks/post-tool-use.sh`
**Timeout**: 360 seconds (allows up to 350s sleep)

**Flow**:
1. Check if pace-maker is enabled
2. Increment global tool execution counter
3. Check for cached pacing decision (avoids API spam)
4. If no cached decision or stale, run pacing check and cache result
5. Apply throttling delay if needed (sleeps if needed)
6. Check if should inject subagent reminder (every 5 executions in main context)
7. Return control to Claude Code

**Python Handler**: `src/pacemaker/hook.py:run_hook()`

**Key Change**: Pacing now throttles on EVERY tool use, not just when API is polled. Decisions are cached in database to avoid API spam while still throttling continuously.

### UserPromptSubmit Hook

**Trigger**: Before user prompt is sent to Claude
**Script**: `~/.claude/hooks/user-prompt-submit.sh`
**Purpose**: Intercept `pace-maker` commands

**Flow**:
1. Check if input starts with "pace-maker"
2. If yes: execute command, block from reaching Claude, display output
3. If no: pass through to Claude unchanged

**Python Handler**: `src/pacemaker/hook.py:run_user_prompt_submit()`

### SessionStart Hook

**Trigger**: When Claude Code session starts
**Script**: `~/.claude/hooks/session-start.sh`
**Purpose**: Show IMPLEMENTATION LIFECYCLE PROTOCOL reminder

**Flow**:
1. Check if tempo is enabled
2. If yes: print reminder text to Claude about IMPLEMENTATION_START/COMPLETE markers
3. If no: do nothing

**Python Handler**: `src/pacemaker/hook.py:run_session_start()`

### Stop Hook

**Trigger**: When Claude Code session attempts to stop/exit
**Script**: `~/.claude/hooks/stop.sh`
**Purpose**: Prevent premature session termination using AI-powered intent validation

**Flow**:
1. Check if tempo is enabled (global or session override)
2. Read conversation transcript from JSONL file
3. Extract user prompts and Claude's responses
4. Call intent validator to check if work is complete
5. SDK acts as user proxy and judges if Claude completed the original request
6. Return APPROVED (allow exit) or BLOCKED with specific feedback

**Python Handler**: `src/pacemaker/hook.py:run_stop_hook()`

**Session Tempo Control**: Uses `should_run_tempo()` to check both global `tempo_enabled` and session override `tempo_session_enabled`. Session override takes precedence.

### SubagentStart Hook

**Trigger**: When entering subagent context (Task tool invoked)
**Script**: `~/.claude/hooks/subagent-start.sh`
**Purpose**: Track subagent context for reminder system

**Flow**:
1. Load current state
2. Set `in_subagent` flag to True
3. Increment `subagent_depth` counter
4. Save state

**Python Handler**: `src/pacemaker/hook.py:run_subagent_start_hook()`

### SubagentStop Hook

**Trigger**: When exiting subagent context (Task tool completes)
**Script**: `~/.claude/hooks/subagent-stop.sh`
**Purpose**: Track subagent context for reminder system

**Flow**:
1. Load current state
2. Decrement `subagent_depth` counter
3. Update `in_subagent` flag (False if depth reaches 0)
4. Save state

**Python Handler**: `src/pacemaker/hook.py:run_subagent_stop_hook()`

### PreToolUse Hook

**Trigger**: Before Write/Edit tool executes on source code files
**Script**: `~/.claude/hooks/pre-tool-use.sh`
**Purpose**: Validate intent declaration, TDD requirements, and clean code standards

**Flow**:
1. Check if pace-maker master switch is enabled
2. Check if intent validation feature is enabled
3. Check if target file is a source code file (by extension)
4. Extract last 5 assistant messages from transcript
5. Call Claude SDK to validate:
   - Intent declaration present (FILE, CHANGES, GOAL)
   - TDD declaration for core code paths
   - Code matches declared intent
   - No clean code violations
6. Return empty (allow) or error message (block)

**Python Handler**: `src/pacemaker/hook.py:run_pre_tool_use_hook()`

**Validation Module**: `src/pacemaker/intent_validator.py:validate_intent_and_code()`

---

## Pre-Tool Validation System

Two-stage AI-powered validation system that enforces code quality, intent transparency, and TDD practices.

### Two-Stage Validation Architecture

**Stage 1: Fast Declaration Check** (~2-4 seconds)
- **Model**: `claude-sonnet-4-5` (generic alias, auto-updates to latest)
- **Purpose**: Lightweight validation of intent declaration
- **Validates**:
  - Intent declaration exists with all 3 required components
  - TDD declarations for core code paths
- **Returns**: `YES`, `NO`, or `NO_TDD`
- **Prompt**: `src/pacemaker/prompts/pre_tool_use/stage1_declaration_check.md`

**Stage 2: Comprehensive Code Review** (~10-15 seconds)
- **Model**: `claude-opus-4-5` (primary), `claude-sonnet-4-5` (fallback on rate limits)
- **Purpose**: Deep validation of code quality
- **Validates**:
  - Code matches declared intent exactly
  - No scope creep or unauthorized changes
  - Clean code violations (security, anti-patterns, bugs)
- **Returns**: `APPROVED` (empty string means block) or detailed feedback
- **Prompt**: `src/pacemaker/prompts/pre_tool_use/stage2_code_review.md`

### Intent Declaration Requirement

Every code modification must declare intent **in the same message** as the Write/Edit tool:
1. **FILE**: Which file is being modified
2. **CHANGES**: What specific changes are being made
3. **GOAL**: Why the changes are being made

**Example**:
```
I will modify src/auth.py to add a validate_password() function
that checks password strength, to improve security.

[then use Write/Edit tool in same message]
```

### Light-TDD Enforcement

Files in core code paths require either a test declaration or explicit user permission to skip TDD.

**Core Code Paths**:
- `src/`
- `lib/`
- `core/`
- `source/`
- `libraries/`
- `kernel/`

**Option A - Declare test coverage**:
```
I will modify src/auth.py to add validate_password().
Test coverage: tests/test_auth.py - test_validate_password_rejects_weak()
```

**Option B - Quote user permission**:
```
I will modify src/auth.py to add validate_password().
User permission to skip TDD: User said "skip tests for this" in message 3.
```

**Critical Rules**:
- The quoted permission must exist in the last 5 messages
- Fabricated or paraphrased permissions are rejected
- The validator verifies the quote against actual message content

### Clean Code Validation

The validator blocks code containing these violations:

| Category | Violations |
|----------|------------|
| Security | Hardcoded secrets, SQL injection vulnerabilities |
| Error Handling | Bare except clauses, silently swallowed exceptions |
| Code Quality | Magic numbers, mutable default arguments, commented-out code |
| Structure | Deeply nested conditionals (6+), large methods (>50 lines) |
| Logic | Off-by-one bugs, missing boundary checks |
| Testing | Over-mocked tests (mocking code under test) |
| Intent Match | Scope creep, missing functionality, unauthorized deletions |
| Anti-patterns | Undeclared fallback behaviors |

### Code-Intent Alignment

The validator ensures code exactly matches the declared intent:
- **No scope creep**: Cannot add functions/features not declared
- **No missing functionality**: Must implement everything declared
- **No unauthorized deletions**: Cannot remove code not mentioned

### Validation Flow

```
Write/Edit Tool Attempted
        │
        ▼
┌───────────────────────┐
│ Master switch enabled?│──── NO ──────► ALLOW (bypass all)
└───────────────────────┘
        │ YES
        ▼
┌───────────────────────┐
│ Intent validation on? │──── NO ──────► ALLOW (feature disabled)
└───────────────────────┘
        │ YES
        ▼
┌───────────────────────┐
│ Source code file?     │──── NO ──────► ALLOW (non-source file)
└───────────────────────┘
        │ YES
        ▼
┌────────────────────────────────────────────────────────┐
│ STAGE 1: Fast Declaration Check (Sonnet, ~2-4 seconds) │
│  - Extract last 2 messages (text + tool call)           │
│  - Check intent declaration exists                      │
│  - Check TDD declaration for core paths                 │
└────────────────────────────────────────────────────────┘
        │
        ├──── NO ──────► BLOCK + teach format
        │
        ├──── NO_TDD ─► BLOCK + request TDD declaration
        │
        │ YES
        ▼
┌────────────────────────────────────────────────────────┐
│ STAGE 2: Comprehensive Code Review (Opus, ~10-15s)     │
│  - Extract last 5 messages for full context            │
│  - Validate code matches intent exactly                │
│  - Check for scope creep                               │
│  - Check for missing functionality                     │
│  - Check for unauthorized deletions                    │
│  - Validate clean code standards                       │
└────────────────────────────────────────────────────────┘
        │
        ├──── Violations ──► BLOCK + detailed feedback
        │
        │ APPROVED
        ▼
    ✓ ALLOW EDIT
```

### Message Context Packaging

The two-stage system uses different message extraction strategies:

**Stage 1 (Fast Declaration Check)**:
- Extracts last 2 messages and combines them
- Addresses Claude Code's behavior of splitting text and tool calls into separate transcript entries
- Message N-1: Intent declaration text
- Message N: Tool call parameters
- Combined into single string for validation

**Stage 2 (Comprehensive Code Review)**:
- Extracts last 5 messages for full context
- Messages 1-4: Text only (tool parameters stripped)
- Message 5 (current): Full content including tool parameters
- Provides complete context for detecting scope creep, missing functionality, and code quality issues

This dual approach optimizes for speed (Stage 1) while maintaining thoroughness (Stage 2).

### Validation Prompts

The system uses two separate external prompt templates:

**Stage 1 Prompt**: `src/pacemaker/prompts/pre_tool_use/stage1_declaration_check.md`
- Validates intent declaration format
- Checks TDD requirements for core paths
- Fast, focused validation
- Returns: `YES`, `NO`, or `NO_TDD`

**Stage 2 Prompt**: `src/pacemaker/prompts/pre_tool_use/stage2_code_review.md`
- Comprehensive code review
- Validates code matches intent
- Checks clean code violations
- Returns: `APPROVED` or detailed feedback text

**Common Includes**: Both prompts reference:
- `src/pacemaker/prompts/common/intent_declaration_prompt.md` - Intent format specification
- `src/pacemaker/prompts/common/tdd_declaration_prompt.md` - TDD requirements

### SDK Integration

The two-stage validator uses Claude Agent SDK with different models for each stage:

**Stage 1 (Declaration Check)**:
- **Model**: `claude-sonnet-4-5` (generic alias, auto-updates)
- **Thinking tokens**: 1024 (API minimum)
- **Purpose**: Fast intent detection with capable model
- **No fallback**: Single model for speed

**Stage 2 (Code Review)**:
- **Primary**: `claude-opus-4-5` (highest quality analysis)
- **Fallback**: `claude-sonnet-4-5` (on rate limit)
- **Thinking tokens**: 1024 (API minimum)
- **Purpose**: Deep code quality validation

**Model Naming**: All models use generic aliases (e.g., `claude-sonnet-4-5`) that automatically resolve to the latest version of each model family.

**Configuration**: `src/pacemaker/intent_validator.py`

---

## Tempo System

The tempo system prevents Claude from prematurely ending implementation sessions using AI-powered intent validation.

### How It Works

**Protocol**:
1. User submits a request (captured by UserPromptSubmit hook)
2. Claude does work
3. When Claude tries to stop, Stop hook triggers
4. Intent validator calls Claude Agent SDK with user's original request and Claude's work
5. SDK acts as user proxy and judges if work is complete
6. Returns APPROVED (allow exit) or BLOCKED with feedback

**Key Features**:
- **AI-powered validation**: Uses Claude Agent SDK as user proxy
- **Intent-based**: Judges completion based on user's original intent
- **Context-aware**: Analyzes conversation history for accurate assessment
- **Session override**: Can be controlled per-session with `pace-maker tempo session on/off`
- **Configurable**: Global setting with `tempo_enabled`, session override with `tempo_session_enabled`

**Implementation** (`src/pacemaker/intent_validator.py`):
- `validate_intent()`: Main validation function that calls SDK
- Extracts user messages and Claude's responses from transcript
- Calls SDK with validation prompt
- Parses SDK response (APPROVED or BLOCKED)

**Configuration**:
- Global setting: `tempo_enabled` in `config.json` (default: True)
- Session override: `tempo_session_enabled` in `state.json` (optional, takes precedence)
- Context size: `conversation_context_size` in `config.json` (default: 5 messages)

**State Tracking**:
The system uses `~/.claude-pace-maker/state.json` to track:
```json
{
  "tempo_session_enabled": true  // Optional session override
}
```

**Precedence Logic**: Session override → Global setting

---

## Subagent Reminder System

The subagent reminder system encourages delegation to specialized subagents in main context.

### How It Works

**Trigger Conditions**:
1. NOT in subagent context (`in_subagent == False`)
2. Feature enabled (`subagent_reminder_enabled == True`)
3. Tool execution count is multiple of frequency (every 5 executions by default)

**Flow**:
1. PostToolUse hook increments global `tool_execution_count`
2. Every Nth execution in main context, reminder is injected
3. Reminder appears as JSON block with "block" decision
4. In subagent context, reminders are suppressed

**Implementation** (`src/pacemaker/hook.py`):
- `should_inject_reminder()`: Check if reminder should be shown
- `inject_subagent_reminder()`: Output JSON reminder to stdout

**Configuration**:
- `subagent_reminder_enabled`: Enable/disable feature (default: True)
- `subagent_reminder_frequency`: Executions between reminders (default: 5)
- `subagent_reminder_message`: Custom reminder text

**Context Tracking**:
- SubagentStart/Stop hooks track `in_subagent` flag
- `subagent_depth` counter handles nested subagents
- Global `tool_execution_count` persists across all contexts

---

## Throttling Algorithms

### Window-Specific Algorithms

**5-Hour Window**: Uses legacy mode (no weekend awareness needed for short windows)
```python
# Simple time-based allowance
allowance = (time_elapsed / total_time) * 100
```

**7-Day Window**: Uses weekend-aware mode
```python
# Weekend-aware allowance
weekday_seconds_elapsed = count_weekday_seconds(window_start, now)
total_weekday_seconds = count_weekday_seconds(window_start, window_end)
allowance = (weekday_seconds_elapsed / total_weekday_seconds) * 100
```

### Zero Tolerance Throttling

**Configuration**: `threshold_percent: 0`

**Behavior**: Throttle immediately when `current_util > (allowance * 0.95)`

No deviation threshold - any overage triggers throttling.

### Adaptive Delay Calculation

**Algorithm** (simplified):
```python
# 1. Calculate how much we're over budget
overage = current_util - safe_allowance

# 2. Project remaining usage
tools_remaining = time_remaining_hours * tools_per_hour
budget_per_tool = remaining_budget / tools_remaining

# 3. Calculate delay to stay on track
if overage is small:
    strategy = 'gradual'  # Small delays
elif overage is moderate:
    strategy = 'aggressive'  # Larger delays
else:
    strategy = 'emergency'  # Max delay (350s)
```

---

## Weekend-Aware Logic

### Motivation

Users typically don't work on weekends, but the 7-day credit window includes Saturday and Sunday. Without weekend awareness, the algorithm would expect 14.3% usage by Saturday (1 day / 7 days), but in reality, usage should be 100% by Friday (5 weekdays / 5 weekdays).

### Implementation

**Weekday Seconds Counter**:
```python
def count_weekday_seconds(start_dt, end_dt):
    """Count only Mon-Fri seconds."""
    total_seconds = 0
    current = start_dt

    while current < end_dt:
        if not is_weekend(current):
            # Count seconds until end of day or end_dt
            day_end = datetime(..., 23, 59, 59)
            next_boundary = min(day_end, end_dt)
            total_seconds += (next_boundary - current).total_seconds()

        current = next_day

    return total_seconds
```

**Weekend Detection**:
```python
def is_weekend(dt):
    """Saturday=5, Sunday=6 in Python's weekday()"""
    return dt.weekday() in (5, 6)
```

### Behavior Examples

| Current Time | Allowance (Calendar) | Allowance (Weekend-Aware) |
|--------------|---------------------|---------------------------|
| Mon 12:00    | 14.3%               | 10.0% (0.5/5 days)       |
| Wed 12:00    | 42.9%               | 50.0% (2.5/5 days)       |
| Fri 23:59    | 71.4%               | 100.0% (5/5 days)        |
| Sat 12:00    | 78.6%               | 100.0% (frozen)          |
| Sun 18:00    | 92.9%               | 100.0% (frozen)          |

---

## Preload Allowance System

### Problem: Day-1 Throttling Hell

**Without Preload**:
- Window resets at 4 PM Friday
- Allowance at reset: 0%
- User starts working immediately
- After 1 hour: Allowance = 0.83%, Usage = 2%
- **Result**: Immediate throttling on day 1, unusable system

**Root Cause**: Linear accrual from 0% means no working capacity at window start.

### Solution: 12-Hour Preload

**Concept**: Preload the first 12 weekday hours with flat 10% allowance.

**Benefits**:
- Users can work at full speed for first 12 weekday hours
- No throttling on day 1 (unless usage exceeds 9.5% safe threshold)
- Smooth transition to normal accrual after preload period

### How It Works

**Algorithm**:
```python
weekday_hours_elapsed = count_weekday_seconds(start, now) / 3600.0
preload_allowance = (12.0 / 120.0) * 100  # 10%

if weekday_hours_elapsed <= 12.0:
    return preload_allowance  # Flat 10%
else:
    return (weekday_hours_elapsed / 120.0) * 100  # Normal accrual
```

**Timeline Example (Window starts Friday 4 PM)**:

| Time | Weekday Hours | Allowance | Mode |
|------|---------------|-----------|------|
| Fri 4 PM | 0h | 10% | Preload |
| Fri 8 PM | 4h | 10% | Preload |
| Sat 12 AM | 8h | 10% | Preload (weekend frozen) |
| Mon 4 AM | 12h | 10% | End of preload |
| Mon 8 AM | 16h | 13.3% | Normal accrual |
| Mon 4 PM | 24h | 20% | Normal accrual |
| Fri 4 PM | 120h | 100% | Window end |

**Key Points**:
- Preload counts only **weekday hours** (weekends don't consume preload)
- After 12 weekday hours, switches to normal accrual
- Provides +10% boost at start to prevent throttling hell

### Configuration

```json
{
  "preload_hours": 12.0
}
```

- `12.0` = 10% preload (default, recommended)
- `24.0` = 20% preload (very generous)
- `6.0` = 5% preload (conservative)
- `0.0` = No preload (original behavior)

---

## Safety Buffer System

### Problem

Targeting exactly 100% is risky:
- Tool execution costs vary (some use more credits than estimated)
- API polling happens every 60s (usage can spike between polls)
- Hitting the hard limit causes Claude Code to stop working immediately

### Solution: 95% Safety Buffer

**Target**: 95% of allowance at any point in time
**Headroom**: 5% buffer before hitting hard limit

### How It Works

**Without Safety Buffer**:
```python
if current_util > allowance:
    throttle()  # Risky - might hit 100% before next check
```

**With Safety Buffer**:
```python
safe_allowance = allowance * 0.95
if current_util > safe_allowance:
    throttle()  # Safe - 5% headroom before hard limit
```

### Configuration

```json
{
  "safety_buffer_pct": 95.0
}
```

- `95.0` = 5% safety buffer (default, recommended)
- `90.0` = 10% safety buffer (very conservative)
- `98.0` = 2% safety buffer (aggressive)
- `100.0` = No safety buffer (risky, original behavior)

### Example

**Wednesday noon, 7-day window**:
- Raw allowance: 50.0% (2.5 weekdays / 5 weekdays)
- Safe allowance: 47.5% (50% × 95%)
- Current usage: 48.0%
- Decision: **THROTTLE** (48% > 47.5%)

---

## Data Flow

### PostToolUse Hook Execution

```
1. Tool execution completes in Claude Code
         ↓
2. PostToolUse hook triggers
         ↓
3. ~/.claude/hooks/post-tool-use.sh executes
         ↓
4. Python: src/pacemaker/hook.py:run_hook()
         ↓
5. Check if 60s elapsed since last poll
         ↓
6. If yes: Poll Claude API for usage data
         ↓
7. Store usage snapshot in SQLite database
         ↓
8. Calculate pacing decision:
         ↓
   ┌──────────────────────────────────┐
   │ pacing_engine.py                 │
   │  - Calculate allowance           │
   │  - Apply safety buffer (95%)     │
   │  - Determine if should throttle  │
   └──────────┬───────────────────────┘
              ↓
   ┌──────────────────────────────────┐
   │ adaptive_throttle.py             │
   │  - Count weekday seconds         │
   │  - Calculate safe allowance      │
   │  - Project forward               │
   │  - Calculate optimal delay       │
   └──────────┬───────────────────────┘
              ↓
9. If should throttle:
    execute_delay(delay_seconds)  # Sleep 0-350s
         ↓
10. Display steering message:
    "[Remember: Say 'Mission completed.' when ALL tasks are done]"
         ↓
11. Return to Claude Code
```

---

## Configuration

### Configuration File

**Location**: `~/.claude-pace-maker/config.json`

**Schema**:
```json
{
  "enabled": true,
  "base_delay": 5,
  "max_delay": 350,
  "threshold_percent": 0,
  "poll_interval": 60,
  "safety_buffer_pct": 95.0
}
```

**Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enabled` | boolean | `true` | Enable/disable throttling |
| `base_delay` | integer | `5` | Minimum delay in seconds |
| `max_delay` | integer | `350` | Maximum delay in seconds (capped at 360s hook timeout - 10s safety) |
| `threshold_percent` | integer | `0` | Deviation threshold before throttling (0 = zero tolerance) |
| `poll_interval` | integer | `60` | API polling interval in seconds |
| `safety_buffer_pct` | float | `95.0` | Target percentage of allowance (95% = 5% safety buffer) |
| `preload_hours` | float | `12.0` | Weekday hours to preload (12h = 10% of 120 weekday hours) |
| `subagent_reminder_enabled` | boolean | `true` | Enable/disable subagent delegation reminders |
| `subagent_reminder_frequency` | integer | `5` | Tool executions between reminders |
| `subagent_reminder_message` | string | (default) | Custom reminder message text |
| `conversation_context_size` | integer | `5` | Number of messages for intent validation context |
| `intent_validation_enabled` | boolean | `false` | Enable pre-tool validation (intent, TDD, clean code) |

### State File

**Location**: `~/.claude-pace-maker/state.json`

**Purpose**: Track hook state across invocations

**Schema**:
```json
{
  "session_id": "session-1731607200",
  "last_poll_time": "2025-11-14T12:00:00.279746",
  "last_cleanup_time": "2025-11-14T00:00:00.000000",
  "tempo_session_enabled": true,
  "in_subagent": false,
  "subagent_depth": 0,
  "tool_execution_count": 42
}
```

**Fields**:
- `session_id`: Unique session identifier
- `last_poll_time`: When API was last polled
- `last_cleanup_time`: When database cleanup last ran
- `tempo_session_enabled`: Session override for tempo (optional)
- `in_subagent`: Boolean flag for subagent context
- `subagent_depth`: Nested subagent level counter
- `tool_execution_count`: Global tool execution counter

---

## Database Schema

### Table: usage_snapshots

```sql
CREATE TABLE usage_snapshots (
  timestamp INTEGER PRIMARY KEY,
  five_hour_util REAL,
  five_hour_resets_at TEXT,
  seven_day_util REAL,
  seven_day_resets_at TEXT,
  session_id TEXT
);

CREATE INDEX idx_timestamp ON usage_snapshots(timestamp);
CREATE INDEX idx_session ON usage_snapshots(session_id);
```

**Cleanup**: Daily cleanup removes snapshots older than retention_days (default: 60)

### Table: pacing_decisions

```sql
CREATE TABLE pacing_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp INTEGER NOT NULL,
  should_throttle INTEGER NOT NULL,
  delay_seconds INTEGER NOT NULL,
  session_id TEXT NOT NULL
);

CREATE INDEX idx_pacing_timestamp ON pacing_decisions(timestamp DESC);
CREATE INDEX idx_pacing_session ON pacing_decisions(session_id);
```

**Purpose**: Cache pacing decisions between API polls to enable continuous throttling without API spam.

**Flow**:
1. API poll happens every 60 seconds
2. Pacing decision is calculated and stored
3. PostToolUse hook checks for cached decision
4. If fresh decision exists, use cached delay
5. If stale or missing, trigger new API poll

**Benefit**: Throttles on EVERY tool execution, not just during API polls, while avoiding excessive API calls.

---

## Error Handling

### Graceful Degradation

The system is designed to fail open - if something goes wrong, Claude Code continues working without throttling.

**Examples**:

1. **API Polling Fails**: Skip throttling for this invocation
2. **Database Error**: Continue without storing snapshot
3. **Invalid Configuration**: Use defaults
4. **Exception in Hook**: Log error, return control to Claude Code

**Error Logging**: `~/.claude-pace-maker/hook_debug.log`

### Exception Handling

```python
try:
    run_hook()
except Exception as e:
    # Log error but don't crash
    print(f"[PACE-MAKER ERROR] {e}", file=sys.stderr)
    # Continue execution without throttling
```

---

## Performance Considerations

### API Polling

- **Interval**: 60 seconds (configurable)
- **Throttle**: Prevents API spam
- **Cached**: State persists across hook invocations

### Database

- **Size**: Auto-cleanup keeps database small (<1MB)
- **Performance**: Indexed queries, minimal overhead
- **No Connection Pooling**: Acceptable for hook use case (infrequent access)

### Hook Execution Time

- **Without Throttling**: <100ms (API poll, database write)
- **With Throttling**: 0-350s (sleep delay)
- **Timeout**: 360s (hooks killed after this)

---

## Security

### No External Dependencies

- No pip packages required
- All dependencies are Python stdlib or system tools (jq, curl)

### No Credentials Stored

- Uses Claude Code's existing authentication
- No API keys in configuration

### SQL Injection Prevention

- All queries use parameterized statements
- No string concatenation in SQL

---

## Future Enhancements

### Potential Improvements

1. **Machine Learning**: Learn from usage patterns to improve estimations
2. **Multi-User Support**: Track different users in shared environments
3. **Historical Analytics**: Graph usage patterns over time
4. **Notification System**: Alert when approaching limits
5. **Dynamic Safety Buffer**: Adjust buffer based on volatility

---

## Appendices

### A. Glossary

- **Allowance**: Budget percentage that should be used at current point in time
- **Safe Allowance**: Allowance × safety_buffer_pct (e.g., 95% of allowance)
- **Deviation**: Difference between current usage and allowance
- **Throttling**: Introducing delays to slow down credit consumption
- **Weekend-Aware**: Algorithm that recognizes weekends and adjusts accordingly

### B. File Structure

```
claude-pace-maker/
├── src/
│   ├── pacemaker/
│   │   ├── __init__.py
│   │   ├── hook.py              # Hook entry point
│   │   ├── pacing_engine.py     # Orchestration
│   │   ├── adaptive_throttle.py # Weekend-aware algorithm
│   │   ├── calculator.py        # Legacy algorithms
│   │   ├── database.py          # SQLite operations
│   │   ├── api_client.py        # Claude API client
│   │   ├── user_commands.py     # Status/on/off commands
│   │   ├── intent_validator.py  # Pre-tool validation via SDK
│   │   ├── transcript_reader.py # Message extraction from JSONL
│   │   ├── prompts/
│   │   │   ├── pre_tool_use/
│   │   │   │   ├── stage1_declaration_check.md  # Stage 1 prompt
│   │   │   │   └── stage2_code_review.md        # Stage 2 prompt
│   │   │   ├── common/
│   │   │   │   ├── intent_declaration_prompt.md # Intent format spec
│   │   │   │   └── tdd_declaration_prompt.md    # TDD requirements
│   │   │   ├── stop/
│   │   │   │   └── stop_hook_validator_prompt.md # Tempo validation
│   │   │   ├── session_start/
│   │   │   │   └── session_start_message.md     # Session startup text
│   │   │   └── user_commands/
│   │   │       └── status_message.md            # Status command output
│   │   └── hooks/
│   │       └── post_tool.py     # Steering message
│   └── hooks/
│       ├── post-tool-use.sh     # PostToolUse hook
│       └── user-prompt-submit.sh # UserPromptSubmit hook
├── tests/                       # Test suite
├── docs/                        # Documentation
├── install.sh                   # Installation script
└── README.md                    # Quick start guide
```

### C. Testing

Run the complete test suite:

```bash
python -m pytest tests/ -v
```

Test coverage:

```bash
python -m pytest tests/ --cov=src/pacemaker --cov-report=html
```

---

**Document Version**: 1.6
**Last Updated**: 2025-12-09
**Maintainer**: Claude Code Pace Maker Team
**Changes**:
- v1.6: Updated Pre-Tool Validation to two-stage architecture (Stage 1: declaration check with Sonnet, Stage 2: code review with Opus/Sonnet), updated message extraction to combine last 2 messages, updated validation flow diagram, updated prompt file structure to reflect pre_tool_use/, common/, stop/, session_start/, user_commands/ organization
- v1.5: Updated model names to versionless (claude-sonnet-4-5, claude-opus-4-5), added hookEventName to PostToolUse additionalContext
- v1.4: Added Pre-Tool Validation System (intent declaration, Light-TDD enforcement, clean code validation), PreToolUse hook, transcript_reader module, external prompt template
- v1.3: Added SubagentStart/Stop hooks, pacing_decisions table, subagent reminder system, session tempo control, continuous throttling architecture
- v1.2: Updated intent validation from marker-based to AI-powered SDK approach
- v1.1: Added Tempo System section, documented SessionStart and Stop hooks, removed slash command references
