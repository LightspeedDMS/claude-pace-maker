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
9. [Langfuse Telemetry System](#langfuse-telemetry-system)
10. [Secrets Management](#secrets-management)
11. [Prompt Intelligence (Intel)](#prompt-intelligence-intel)
12. [Resilient Fallback Mode](#resilient-fallback-mode)
13. [Activity Indicators](#activity-indicators)
14. [Global API Poll Coordination](#global-api-poll-coordination)
15. [Data Flow](#data-flow)
16. [Configuration](#configuration)
17. [Database Schema](#database-schema)
18. [Error Handling](#error-handling)

---

## System Overview

Claude Pace Maker is a hook-based system for Claude Code that provides:
- **Credit throttling**: Prevents hitting API rate limits by intelligently pacing tool executions
- **Pre-tool validation**: Enforces intent declaration, TDD for core code, and clean code standards
- **Session lifecycle (tempo)**: Prevents premature session endings via AI-powered completion validation
- **Langfuse telemetry**: Records sessions, traces, spans, and generation observations with token costs
- **Secrets management**: Sanitizes declared secrets from Langfuse traces before upload
- **Prompt intelligence (intel)**: Parses structured metadata from assistant responses (frustration, task type, quality)
- **Resilient fallback mode**: Provides synthetic usage estimates during Claude API outages
- **Activity indicators**: Real-time hook activity tracking visible in the usage monitor
- **Global API poll coordination**: SQLite singleton prevents redundant API calls across concurrent hook invocations

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
                          │  │ SQLite Database (WAL mode)               ││
                          │  │  - Usage snapshots / api_cache           ││
                          │  │  - Fallback state + accumulated costs    ││
                          │  │  - Langfuse metrics / secrets            ││
                          │  │  - Activity events                       ││
                          │  │  - Global poll coordination              ││
                          │  └─────────────────────────────────────────┘│
                          │              │                               │
                          │              ▼                               │
                          │  ┌─────────────────────────────────────────┐│
                          │  │ Langfuse Cloud                           ││
                          │  │  - Traces / sessions                     ││
                          │  │  - Generation observations (costs)       ││
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
- Record activity events for all hook types
- Coordinate Langfuse trace lifecycle (pending trace, flush on stop)

**Key Functions**:
- `run_hook()` - PostToolUse hook handler
- `execute_delay(delay_seconds)` - Sleep for throttling (max 350s)
- `run_user_prompt_submit()` - UserPromptSubmit hook handler
- `run_stop_hook()` - Stop/exit hook with tempo validation and Langfuse flush
- `run_session_start()` - SessionStart hook handler
- `run_subagent_start_hook()` / `run_subagent_stop_hook()` - Subagent context tracking
- `run_pre_tool_use_hook()` - Pre-tool validation dispatcher
- `safe_print(text)` - BrokenPipeError-safe stdout write (all output uses this)
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
- Calculate weekend-aware allowance with safety buffer

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
- Trigger fallback mode on persistent failures

### 5. Database (`src/pacemaker/database.py`)

SQLite database for usage tracking and system coordination.

**Responsibilities**:
- Store usage snapshots and api_cache (real API responses)
- Track sessions and pacing decisions
- Record blockage events from pre-tool validation
- Store langfuse and secrets metrics
- Manage activity events for the usage monitor
- Maintain global_poll_state singleton for API coordination
- Daily cleanup of old data

**Concurrency**: All writes use WAL (Write-Ahead Logging) mode with `execute_with_retry()` for lock-safe operations. `PACEMAKER_TEST_MODE=1` environment variable enables `PRAGMA synchronous=OFF` for 20x faster test execution.

### 6. Usage Model (`src/pacemaker/usage_model.py`)

Single source of truth for all usage metrics — real API data or synthetic estimates during fallback.

**Responsibilities**:
- Provide `get_current_usage()` as the single unified method for both the hook system and the usage monitor
- Manage fallback state machine (NORMAL → FALLBACK → NORMAL)
- Accumulate token costs during API outages (`accumulate_cost()`)
- Store raw API responses in `api_cache` table
- Track backoff state for rate-limit (429) responses
- Calibrate synthetic coefficients against real values on API recovery

**Key Interface**:
```python
class UsageModel:
    def get_current_usage(self) -> Optional[UsageSnapshot]:
        """Returns real or synthetic snapshot depending on fallback state."""

    def is_fallback_active(self) -> bool
    def enter_fallback(self) -> None
    def exit_fallback(self, real_5h: float, real_7d: float) -> None
    def accumulate_cost(self, input_tokens, output_tokens, ...) -> None
    def store_api_response(self, response_data: Dict) -> None
    def get_pacing_decision(self, config: Dict) -> Optional[Dict]
```

`UsageSnapshot` dataclass carries `is_synthetic: bool` to distinguish real from estimated data.

### 7. Fallback Utilities (`src/pacemaker/fallback.py`)

Shared primitives used by `UsageModel`.

**Contents**:
- `FallbackState` enum — `NORMAL` / `FALLBACK` states
- `API_PRICING` — per-1M-token pricing by model family (opus, sonnet, haiku)
- `_DEFAULT_TOKEN_COSTS` — fallback coefficients when calibration unavailable (`5x`/`20x` tiers)
- `parse_api_datetime()` — ISO 8601 datetime parser for API response strings
- `_project_window()` — projects an expired `resets_at` timestamp forward past `now`
- `detect_tier()` — infers subscription tier (`"5x"` or `"20x"`) from profile dict

### 8. User Commands (`src/pacemaker/user_commands.py`)

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
3. Record activity event (event code `PA` = pacing, or `LF` = Langfuse push)
4. Check global_poll_state for API poll coordination
5. If poll interval elapsed, run pacing check and cache result
6. Apply throttling delay if needed (sleeps if needed)
7. Push incremental Langfuse trace data
8. Check if should inject subagent reminder (every 5 executions in main context)
9. Return control to Claude Code

**Python Handler**: `src/pacemaker/hook.py:run_hook()`

**Key Change**: Pacing now throttles on EVERY tool use, not just when API is polled. Decisions are cached in database to avoid API spam while still throttling continuously.

### UserPromptSubmit Hook

**Trigger**: Before user prompt is sent to Claude
**Script**: `~/.claude/hooks/user-prompt-submit.sh`
**Purpose**: Intercept `pace-maker` commands; store pending trace for Langfuse; parse intel metadata

**Flow**:
1. Check if input starts with "pace-maker"
2. If yes: execute command, block from reaching Claude, display output
3. If no: store prompt as `pending_trace` in Langfuse state (deferred push — secrets not yet visible)
4. Record activity event (`UP` = user prompt)
5. Pass through to Claude unchanged

**Python Handler**: `src/pacemaker/hook.py:run_user_prompt_submit()`

**Deferred Push Design**: Traces are stored as `pending_trace` in Langfuse state at UserPromptSubmit, NOT pushed immediately. Secrets are only disclosed after the assistant responds, so sanitization must happen later. Pending traces are flushed in PostToolUse, Stop, and SubagentStop hooks via `flush_pending_trace()`.

### SessionStart Hook

**Trigger**: When Claude Code session starts
**Script**: `~/.claude/hooks/session-start.sh`
**Purpose**: Show IMPLEMENTATION LIFECYCLE PROTOCOL reminder; clean up stale Langfuse state files

**Flow**:
1. Check if tempo is enabled
2. If yes: print reminder text to Claude about IMPLEMENTATION_START/COMPLETE markers
3. Clean up Langfuse state files older than 7 days
4. Record activity event (`SS` = session start)

**Python Handler**: `src/pacemaker/hook.py:run_session_start()`

### Stop Hook

**Trigger**: When Claude Code session attempts to stop/exit
**Script**: `~/.claude/hooks/stop.sh`
**Purpose**: Prevent premature session termination using AI-powered intent validation; flush Langfuse trace

**Flow**:
1. Flush any pending Langfuse trace (push with generation observation)
2. Check if tempo is enabled (global or session override)
3. Read conversation transcript from JSONL file
4. Extract user prompts and Claude's responses
5. Call intent validator to check if work is complete
6. SDK acts as user proxy and judges if Claude completed the original request
7. Return APPROVED (allow exit) or BLOCKED with specific feedback
8. Record activity event (`ST` = stop)

**Python Handler**: `src/pacemaker/hook.py:run_stop_hook()`

**Session Tempo Control**: Uses `should_run_tempo()` to check both global `tempo_enabled` and session override `tempo_session_enabled`. Session override takes precedence.

### SubagentStart Hook

**Trigger**: When entering subagent context (Task tool invoked)
**Script**: `~/.claude/hooks/subagent-start.sh`
**Purpose**: Track subagent context for reminder system; record activity

**Flow**:
1. Load current state
2. Set `in_subagent` flag to True
3. Increment `subagent_depth` counter
4. Save state; record activity event (`SA` = subagent)

**Python Handler**: `src/pacemaker/hook.py:run_subagent_start_hook()`

### SubagentStop Hook

**Trigger**: When exiting subagent context (Task tool completes)
**Script**: `~/.claude/hooks/subagent-stop.sh`
**Purpose**: Track subagent context; flush Langfuse subagent trace

**Flow**:
1. Flush Langfuse subagent trace (uses `subagent-<agent_id>` as session_id)
2. Decrement `subagent_depth` counter
3. Update `in_subagent` flag (False if depth reaches 0)
4. Save state

**Python Handler**: `src/pacemaker/hook.py:run_subagent_stop_hook()`

**Agent Transcript Location**: Claude Code 2.1.39+ moved agent transcripts from `<project>/agent-*.jsonl` to `<project>/<session-id>/subagents/agent-*.jsonl`. The hook searches both locations for backward compatibility.

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
7. Record activity event (`IV` = intent validation pass/fail)

**Python Handler**: `src/pacemaker/hook.py:run_pre_tool_use_hook()`

**Validation Module**: `src/pacemaker/intent_validator.py:validate_intent_and_code()`

**Fails Closed**: When the validator encounters an error (e.g., SDK unavailable), the tool use is **blocked**, not allowed through. This is intentional to prevent bypassing validation during transient errors.

---

## Pre-Tool Validation System

Two-stage AI-powered validation system that enforces code quality, intent transparency, and TDD practices.

### Two-Stage Validation Architecture

**Stage 1: Fast Declaration Check** (~0ms, pure regex — no LLM call)
- **Model**: None (regex-based, no inference required)
- **Purpose**: Lightweight structural validation of intent declaration
- **Validates**:
  - `INTENT:` marker present in current message
  - File name mentioned in current message
  - TDD declarations for core code paths (or excluded path bypass)
- **Returns**: `YES`, `NO`, or `NO_TDD`
- **Implementation**: `_regex_stage1_check()` in `src/pacemaker/intent_validator.py`

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

Note: Validation failures (SDK errors, timeouts) cause a **BLOCK** — the system fails closed, not open.

### Message Context Packaging

The two-stage system uses different message extraction strategies:

**Stage 1 (Fast Declaration Check)**:
- Pure regex check — no LLM call, no message packaging overhead
- Uses `extract_current_assistant_message()` to locate the current message
- Searches back up to 3 messages for an `intent:` marker (handles split tool calls)
- Only the located current message is passed to `_regex_stage1_check()`

**Stage 2 (Comprehensive Code Review)**:
- Extracts last 5 messages for full context
- Messages 1-4: Text only (tool parameters stripped)
- Message 5 (current): Full content including tool parameters
- Provides complete context for detecting scope creep, missing functionality, and code quality issues

### Validation Prompts

The system uses external prompt templates:

**Stage 1 (Regex)**: `_regex_stage1_check()` in `src/pacemaker/intent_validator.py`
- No prompt file — pure regex, no LLM call
- Validates `INTENT:` marker and file mention in current message
- Checks TDD requirements for core paths (bypassed for excluded paths)
- Returns: `YES`, `NO`, or `NO_TDD`

**Stage 2 Prompt**: `src/pacemaker/prompts/pre_tool_use/stage2_code_review.md`
- Comprehensive code review
- Validates code matches intent
- Checks clean code violations
- Returns: `APPROVED` or detailed feedback text

**Common Includes**: Both prompts reference:
- `src/pacemaker/prompts/common/intent_declaration_prompt.md` - Intent format specification
- `src/pacemaker/prompts/common/tdd_declaration_prompt.md` - TDD requirements

**Session Start Prompts** (`src/pacemaker/prompts/session_start/`):
- `intel_guidance.md` - Prompt intelligence metadata guidance
- `intent_validation_guidance.md` - Intent validation reminder
- `secrets_nudge.md` - Secret declaration reminder

### SDK Integration

The two-stage validator uses Claude Agent SDK with different models for each stage:

**Stage 1 (Declaration Check)**:
- **Model**: None — pure regex, no SDK or API call
- **Latency**: ~0ms (no network I/O)
- **Purpose**: Structural validation of intent marker and TDD declaration
- **No fallback needed**: Deterministic regex, never fails due to rate limits

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
  "tempo_session_enabled": true
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

**5-Hour Window**: Uses continuous-time linear pacing with 30-minute preload
```python
# Linear allowance with preload (30 min = 10% of 5 hours)
allowance = calculate_continuous_allowance_pct(window_start, now, 5.0, 0.5)
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

## Langfuse Telemetry System

The Langfuse integration provides full observability of Claude Code sessions: traces, spans, generation observations with token costs, and subagent activity.

### Architecture

All Langfuse logic lives in `src/pacemaker/langfuse/`:

| Module | Responsibility |
|--------|---------------|
| `orchestrator.py` | Main lifecycle coordinator; `flush_pending_trace()` helper |
| `state.py` | Per-session state management in JSON files |
| `push.py` | HTTP batch event submission to Langfuse API |
| `incremental.py` | Incremental transcript parsing for token counting |
| `trace.py` | Trace creation and finalization |
| `span.py` | Span and text span creation |
| `subagent.py` | Subagent trace handling |
| `client.py` | Langfuse API client |
| `metrics.py` | Langfuse metric counters |
| `filter.py` | Event filtering |
| `transformer.py` | Data transformation utilities |
| `cache.py` | Local caching |
| `stats.py` | Statistics aggregation |
| `project_context.py` | Project metadata extraction |
| `provisioner.py` | Langfuse resource provisioning |
| `backfill.py` | Historical data backfill |

### Deferred Push Design

Traces are **not pushed immediately** when a user prompt is submitted. The reason: secrets are only disclosed by Claude in the assistant response, after the prompt is processed. Pushing immediately would leak unmasked secrets.

**Flow**:
```
UserPromptSubmit → store pending_trace in state file
PostToolUse     → flush_pending_trace() → sanitize → push
Stop            → flush_pending_trace() → sanitize → push
SubagentStop    → flush_pending_trace() → sanitize → push
```

### Langfuse Hierarchy

```
Session (Claude Code session_id)
└── Trace (one per conversation turn)
    ├── Span (tool call or text block)
    └── Generation (token usage observation → drives cost calculation)
```

- **Session**: Created automatically by Langfuse when a `sessionId` is set on a trace
- **Trace**: One per conversation turn; carries token metadata
- **Generation**: Required for Langfuse cost computation — traces and spans do NOT compute cost; only generation observations do
- **Token types tracked**: `input`, `output`, `cache_read_input_tokens`, `cache_creation_input_tokens`

### Per-Session State Files

State is stored in `~/.claude-pace-maker/langfuse_state/<session_id>.json`:

```json
{
  "session_id": "session-abc123",
  "trace_id": "session-abc123",
  "last_pushed_line": 0,
  "metadata": {...},
  "pending_trace": [...],
  "pending_intel": {...}
}
```

- `pending_trace`: Deferred batch events (set at UserPromptSubmit, cleared after flush)
- `pending_intel`: Prompt intelligence metadata waiting to be attached to next trace
- Stale files (>7 days old) are cleaned up at SessionStart

### Incremental Transcript Parsing

`incremental.py` implements a two-pass algorithm for efficient transcript parsing:

**Pass 1**: Scan all lines to build `tool_use_id → output` mapping (from `tool_result` blocks)
**Pass 2**: Extract content blocks from lines after `last_pushed_line`, attaching tool outputs

**Token deduplication**: Claude Code writes 2-4 JSONL entries per API turn with identical `message.usage`. The parser deduplicates by comparing usage tuples — only counts when usage changes between entries.

### Payload Size Management

`push.py` enforces a 900KB payload limit (Langfuse Cloud enforces 1MB). Oversized payloads are progressively truncated:
1. Fields `input`, `output`, `text` are identified and sorted by length
2. Largest field is truncated first (with `[TRUNCATED]` marker)
3. Second-pass aggressive truncation to 1000 chars per field if still over limit

### Subagent Trace Handling

Subagents use `subagent-<agent_id>` as their session_id. The SubagentStop hook flushes the subagent trace when the Task tool completes. The hook searches for agent transcripts in both:
- `<project>/agent-*.jsonl` (Claude Code ≤ 2.1.38)
- `<project>/<session-id>/subagents/agent-*.jsonl` (Claude Code ≥ 2.1.39)

### Configuration

Langfuse is configured in `~/.claude-pace-maker/config.json`:

```json
{
  "langfuse_enabled": true,
  "langfuse_host": "https://cloud.langfuse.com",
  "langfuse_public_key": "pk-lf-...",
  "langfuse_secret_key": "sk-lf-..."
}
```

---

## Secrets Management

The secrets system allows users to declare sensitive values (API keys, passwords, tokens) that are automatically masked in Langfuse traces before upload.

### Architecture

All secrets logic lives in `src/pacemaker/secrets/`:

| Module | Responsibility |
|--------|---------------|
| `sanitizer.py` | Top-level `sanitize_trace()` function; pattern caching |
| `database.py` | CRUD operations for secrets in SQLite `secrets` table |
| `masking.py` | Regex-based string masking; `mask_structure()` for deep traversal |
| `parser.py` | Parses secret declarations from assistant messages |
| `metrics.py` | Increments `secrets_metrics` counters |

### How Sanitization Works

1. User declares secrets via `pace-maker secret add <value>` command
2. Secrets are stored in the `secrets` table in `usage.db` (file permissions: 0600)
3. At flush time, `sanitize_trace()` is called before any Langfuse push:
   - Loads all secrets from database
   - Builds a compiled regex pattern (cached; only rebuilt when secrets change)
   - Deep-copies the trace structure, masks all occurrences
   - Restores protected fields (`userId`) that must never be masked
   - Records masking count in `secrets_metrics`

### Protected Fields

`userId` in traces is always restored after masking because it contains the user's email address required for Langfuse trace identity. If the user's email happened to match a declared secret pattern, it would be incorrectly masked without this restoration step.

### Session Start Nudge

At each session start, a nudge message (`src/pacemaker/prompts/session_start/secrets_nudge.md`) is included in the session startup context, reminding Claude to declare secrets before they appear in tool outputs.

---

## Prompt Intelligence (Intel)

The intel system allows Claude to embed structured metadata in responses using a compact inline format. This metadata is captured and attached to Langfuse traces.

### Intel Line Format

Intel lines start with the `§` marker and contain space-separated fields:

```
§ △0.8 ◎surg ■bug ◇0.7 ↻2
```

| Symbol | Field | Type | Values |
|--------|-------|------|--------|
| `△` | frustration | float 0.0-1.0 | User frustration estimate |
| `◎` | specificity | enum | `surg` `const` `outc` `expl` |
| `■` | task_type | enum | `bug` `feat` `refac` `research` `test` `docs` `debug` `conf` `other` |
| `◇` | quality | float 0.0-1.0 | Response quality self-assessment |
| `↻` | iteration | int 1-9 | Iteration count on this task |

### Parser (`src/pacemaker/intel/parser.py`)

```python
def parse_intel_line(response: str) -> Optional[dict]:
    """Returns dict with parsed fields, or None if no § marker found."""
```

Missing or invalid fields are excluded from the result (no defaults applied). The intel line is stripped from assistant output before it is sent to Langfuse spans.

### Integration

- Intel metadata is stored as `pending_intel` in the Langfuse state file
- Attached to the next trace push as trace metadata
- Guidance for generating intel lines is provided at session start via `src/pacemaker/prompts/session_start/intel_guidance.md`

---

## Resilient Fallback Mode

When the Claude API becomes unavailable, the system switches to fallback mode and generates synthetic usage estimates from accumulated token costs.

### State Machine

```
          API available           API fails
 NORMAL ──────────────── ... ──────────────► FALLBACK
   ▲                                              │
   │         API recovers                         │
   └──────────────────────────────────────────────┘
   (calibrate_on_recovery() called before reset)
```

States are persisted in the `fallback_state_v2` table (singleton row, `id=1`).

### Entering Fallback

When `enter_fallback()` is called:
1. Reads baseline utilization from `api_cache` (last known real values)
2. Synthesizes `resets_at` timestamps if missing (5h → now + 5h; 7d → now + 7d)
3. Detects subscription tier from `profile_cache` (`"5x"` or `"20x"`)
4. Persists state to `fallback_state_v2` (idempotent — does not reset accumulated costs if already in fallback)

### Synthetic Usage Calculation

During fallback, `UsageModel.get_current_usage()` returns a `UsageSnapshot` with `is_synthetic=True`. The utilization estimate is:

```python
synthetic_util = baseline + accumulated_cost * coefficient * 100
```

Where:
- `baseline` = last known real utilization from `api_cache`
- `accumulated_cost` = sum of `accumulated_costs` rows since `entered_at`
- `coefficient` = conversion factor from dollars to % utilization (tier-specific)

**Default coefficients** (from `fallback.py`):
- `5x` tier: `coefficient_5h=0.0075`, `coefficient_7d=0.0011`
- `20x` tier: `coefficient_5h=0.001875`, `coefficient_7d=0.000275`

### Rollover Detection

`_project_window()` detects when a window has expired during fallback (e.g., the 5-hour window resets while API is down). When rollover is detected:
- `resets_at` is projected forward by the window length
- Current accumulated cost is saved as `rollover_cost_*`
- Post-rollover synthetic calculation uses only costs since the rollover

### Calibration on Recovery

When the API recovers and `exit_fallback()` is called:
1. `calibrate_on_recovery(real_5h, real_7d)` computes error ratio between synthetic prediction and real values
2. New coefficient = weighted average of old coefficient and measured coefficient
3. Calibrated coefficients are stored in `calibrated_coefficients` table
4. Future fallback periods use calibrated coefficients (more accurate over time)

### Backoff on Rate Limits

`UsageModel` tracks consecutive 429 responses in the `backoff_state` table:
- Base delay: 5 minutes, doubling with each consecutive 429, capped at 60 minutes
- `record_429()` increments counter and sets `backoff_until` timestamp
- `record_success()` resets counter and clears `backoff_until`

---

## Activity Indicators

The activity indicator system records hook events to the `activity_events` table, making them visible in the usage monitor's real-time activity line.

### Event Codes

| Code | Meaning | Hook |
|------|---------|------|
| `IV` | Intent validation result | PreToolUse |
| `TD` | TDD check result | PreToolUse |
| `CC` | Clean code check result | PreToolUse |
| `ST` | Session tempo (stop hook) | Stop |
| `CX` | Context/subagent check | PostToolUse |
| `PA` | Pacing check | PostToolUse |
| `PL` | Poll (API call) | PostToolUse |
| `LF` | Langfuse push | PostToolUse / Stop / SubagentStop |
| `SS` | Session start | SessionStart |
| `SM` | Secrets masking | PostToolUse |
| `SE` | Secrets declaration | UserPromptSubmit |
| `SA` | Subagent start/stop | SubagentStart / SubagentStop |
| `UP` | User prompt submitted | UserPromptSubmit |

### Status Values

Each event has a status:
- `green` — Success / allowed / passed
- `red` — Failure / blocked / error
- `blue` — Informational / neutral

### Database Mechanics

```python
# Record an event
record_activity_event(db_path, event_code="IV", status="green", session_id=session_id)

# Query recent events (usage monitor polls this)
events = get_recent_activity(db_path, window_seconds=10)
# Returns: [{"event_code": "IV", "status": "green"}, ...]
```

The monitor polls `get_recent_activity()` to display a real-time indicator strip. Events older than 60 seconds are cleaned up by `cleanup_old_activity()` to prevent unbounded table growth.

---

## Global API Poll Coordination

Multiple hook invocations can run concurrently (e.g., PostToolUse fires while a previous invocation is still sleeping). Without coordination, each would independently poll the Claude API, causing unnecessary load.

### Singleton Design

The `global_poll_state` table (singleton row, `id=1`) acts as a distributed lock:

```sql
CREATE TABLE IF NOT EXISTS global_poll_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_poll_time REAL NOT NULL DEFAULT 0,
    last_poll_session TEXT
);
```

### Poll Decision Logic

Before polling the API, each hook invocation checks:
1. Read `last_poll_time` from `global_poll_state`
2. If `now - last_poll_time < poll_interval` (default 60s): skip poll, use cached `api_cache`
3. If stale: update `last_poll_time` atomically (using `execute_with_retry()`), then poll

This ensures at most one API call per 60 seconds across all concurrent hook invocations.

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
5. Record activity event
         ↓
6. Check global_poll_state: 60s elapsed since last poll?
         ↓
7. If yes: Poll Claude API for usage data
         ↓
8. Store usage snapshot in api_cache table
         ↓
9. Calculate pacing decision:
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
10. If should throttle:
    execute_delay(delay_seconds)  # Sleep 0-350s
         ↓
11. Flush pending Langfuse trace:
    - sanitize_trace() → mask secrets
    - push_batch_events() → POST /api/public/ingestion
         ↓
12. Return to Claude Code
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
  "safety_buffer_pct": 95.0,
  "preload_hours": 12.0,
  "tempo_enabled": true,
  "tdd_enabled": true,
  "intent_validation_enabled": true,
  "log_level": 2,
  "subagent_reminder_enabled": true,
  "subagent_reminder_frequency": 5,
  "langfuse_enabled": false,
  "langfuse_host": "https://cloud.langfuse.com",
  "langfuse_public_key": "",
  "langfuse_secret_key": ""
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
| `tempo_enabled` | boolean | `true` | Enable stop-hook completion validation |
| `tdd_enabled` | boolean | `true` | Enable TDD enforcement in pre-tool validation |
| `intent_validation_enabled` | boolean | `true` | Enable pre-tool validation (intent, TDD, clean code) |
| `log_level` | integer | `2` | Log verbosity: 0=off, 1=error, 2=warning, 3=info, 4=debug |
| `subagent_reminder_enabled` | boolean | `true` | Enable/disable subagent delegation reminders |
| `subagent_reminder_frequency` | integer | `5` | Tool executions between reminders |
| `subagent_reminder_message` | string | (default) | Custom reminder message text |
| `conversation_context_size` | integer | `5` | Number of messages for intent validation context |
| `langfuse_enabled` | boolean | `false` | Enable Langfuse telemetry |
| `langfuse_host` | string | `"https://cloud.langfuse.com"` | Langfuse API base URL |
| `langfuse_public_key` | string | `""` | Langfuse public key (pk-lf-...) |
| `langfuse_secret_key` | string | `""` | Langfuse secret key (sk-lf-...) |

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
- `last_poll_time`: When API was last polled (also tracked in `global_poll_state`)
- `last_cleanup_time`: When database cleanup last ran
- `tempo_session_enabled`: Session override for tempo (optional)
- `in_subagent`: Boolean flag for subagent context
- `subagent_depth`: Nested subagent level counter
- `tool_execution_count`: Global tool execution counter

### Langfuse State Files

**Location**: `~/.claude-pace-maker/langfuse_state/<session_id>.json`

**Purpose**: Per-session Langfuse push state

**Schema**:
```json
{
  "session_id": "session-abc123",
  "trace_id": "session-abc123",
  "last_pushed_line": 142,
  "metadata": {
    "model": "claude-opus-4-5",
    "tool_calls": ["Read", "Write"],
    "input_tokens": 45231,
    "output_tokens": 3201
  },
  "pending_trace": null,
  "pending_intel": null
}
```

---

## Database Schema

All tables reside in `~/.claude-pace-maker/usage.db` (WAL mode).

### Table: usage_snapshots

```sql
CREATE TABLE usage_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp INTEGER NOT NULL,
  five_hour_util REAL NOT NULL,
  five_hour_resets_at TEXT,
  seven_day_util REAL NOT NULL,
  seven_day_resets_at TEXT,
  session_id TEXT NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);
```

**Cleanup**: Daily cleanup removes snapshots older than `retention_days` (default: 60).

### Table: pacing_decisions

```sql
CREATE TABLE pacing_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp INTEGER NOT NULL,
  should_throttle INTEGER NOT NULL,
  delay_seconds INTEGER NOT NULL,
  session_id TEXT NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);
```

**Purpose**: Cache pacing decisions between API polls for continuous throttling without API spam.

### Table: api_cache

```sql
CREATE TABLE api_cache (
  id INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton
  timestamp REAL NOT NULL,
  five_hour_util REAL NOT NULL,
  five_hour_resets_at TEXT,
  seven_day_util REAL NOT NULL,
  seven_day_resets_at TEXT,
  raw_response TEXT
);
```

**Purpose**: Stores the last successful API response. Singleton row (`id=1`). Used as baseline when entering fallback mode.

### Table: fallback_state_v2

```sql
CREATE TABLE fallback_state_v2 (
  id INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton
  state TEXT NOT NULL DEFAULT 'normal',   -- 'normal' or 'fallback'
  baseline_5h REAL DEFAULT 0.0,
  baseline_7d REAL DEFAULT 0.0,
  resets_at_5h TEXT,
  resets_at_7d TEXT,
  tier TEXT DEFAULT '5x',
  entered_at REAL,
  rollover_cost_5h REAL,
  rollover_cost_7d REAL,
  last_rollover_resets_5h TEXT,
  last_rollover_resets_7d TEXT
);
```

**Purpose**: Fallback mode state machine. Tracks baselines, rollover points, and subscription tier.

### Table: accumulated_costs

```sql
CREATE TABLE accumulated_costs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp REAL NOT NULL,
  session_id TEXT NOT NULL,
  cost_dollars REAL NOT NULL,
  input_tokens INTEGER,
  output_tokens INTEGER,
  cache_read_tokens INTEGER,
  cache_creation_tokens INTEGER,
  model_family TEXT
);
```

**Purpose**: Token costs accumulated during fallback mode. Used to compute synthetic utilization estimates.

### Table: backoff_state

```sql
CREATE TABLE backoff_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton
  consecutive_429s INTEGER NOT NULL DEFAULT 0,
  backoff_until REAL,
  last_success_time REAL
);
```

**Purpose**: Exponential backoff state for API rate-limit responses.

### Table: profile_cache

```sql
CREATE TABLE profile_cache (
  id INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton
  timestamp REAL NOT NULL,
  profile_json TEXT NOT NULL
);
```

**Purpose**: Cached Claude profile (used for tier detection: `5x` vs `20x`).

### Table: calibrated_coefficients

```sql
CREATE TABLE calibrated_coefficients (
  tier TEXT PRIMARY KEY,
  coefficient_5h REAL NOT NULL,
  coefficient_7d REAL NOT NULL,
  sample_count INTEGER NOT NULL DEFAULT 0,
  last_calibrated REAL
);
```

**Purpose**: Self-calibrating fallback coefficients, improved with each API recovery.

### Table: global_poll_state

```sql
CREATE TABLE global_poll_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton
  last_poll_time REAL NOT NULL DEFAULT 0,
  last_poll_session TEXT
);
```

**Purpose**: Coordinates API poll timing across concurrent hook invocations.

### Table: activity_events

```sql
CREATE TABLE activity_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp REAL NOT NULL,
  event_code TEXT NOT NULL,
  status TEXT NOT NULL,
  session_id TEXT NOT NULL
);
```

**Purpose**: Real-time hook activity feed for the usage monitor. Cleaned up after 60 seconds.

### Table: blockage_events

```sql
CREATE TABLE blockage_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp INTEGER NOT NULL,
  category TEXT NOT NULL,
  reason TEXT NOT NULL,
  hook_type TEXT NOT NULL,
  session_id TEXT NOT NULL,
  details TEXT,
  created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);
```

**Purpose**: Records when pre-tool validation blocks a Write/Edit operation.

### Table: langfuse_metrics

```sql
CREATE TABLE langfuse_metrics (
  bucket_timestamp INTEGER PRIMARY KEY,
  sessions_count INTEGER DEFAULT 0,
  traces_count INTEGER DEFAULT 0,
  spans_count INTEGER DEFAULT 0
);
```

**Purpose**: Aggregated Langfuse push statistics (hourly buckets).

### Table: secrets

```sql
CREATE TABLE secrets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,
  value TEXT NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);
```

**Purpose**: Declared secret values to mask before Langfuse upload. File permissions: 0600.

### Table: secrets_metrics

```sql
CREATE TABLE secrets_metrics (
  bucket_timestamp INTEGER PRIMARY KEY,
  secrets_masked_count INTEGER DEFAULT 0
);
```

**Purpose**: Counts of masking operations performed (hourly buckets).

---

## Error Handling

### Graceful Degradation

The system is designed to fail open for **non-security-critical** paths — if throttling or telemetry fails, Claude Code continues working. However, pre-tool validation **fails closed** by design.

**Examples**:

| Failure | Behavior |
|---------|----------|
| API polling fails | Skip throttling; enter fallback mode |
| Database error | Log and continue without storing snapshot |
| Invalid configuration | Use defaults |
| Langfuse push fails | Log and continue (no retry) |
| Pre-tool validation SDK error | **BLOCK** tool use (fail closed) |
| Exception in hook | Log error, return control to Claude Code |

### Error Logging

Log files use daily rotation: `~/.claude-pace-maker/pace-maker-YYYY-MM-DD.log`

- 15 days of logs retained (configurable)
- Log level controlled by `log_level` config key
- All stdout writes from hooks use `safe_print()` to avoid `BrokenPipeError`

**Log levels**:
```
0 = OFF      - No logging
1 = ERROR    - Errors only
2 = WARNING  - Warnings + Errors (default)
3 = INFO     - Info + Warnings + Errors
4 = DEBUG    - All messages
```

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
- **Throttle**: Global poll state prevents API spam across concurrent invocations
- **Cached**: `api_cache` persists last real response for fallback baseline

### Database

- **Mode**: WAL (Write-Ahead Logging) — concurrent readers do not block writers
- **Size**: Auto-cleanup keeps database small (<2MB typical)
- **Performance**: Indexed queries, `execute_with_retry()` for lock contention
- **No Connection Pooling**: Acceptable for hook use case (infrequent access)
- **Test optimization**: `PACEMAKER_TEST_MODE=1` enables `PRAGMA synchronous=OFF` for 20x speedup

### Hook Execution Time

- **Without Throttling**: <200ms (API poll, database writes, Langfuse push)
- **With Throttling**: 0-350s (sleep delay)
- **Timeout**: 360s (hooks killed after this; max sleep capped at 350s for safety)

---

## Security

### External Dependencies

The system uses:
- `requests` library: HTTP calls to Langfuse API
- `anthropic` SDK: Pre-tool validation (Stage 2 only — Stage 1 is regex-based)
- Python stdlib: All other functionality

No external dependencies are required for core throttling when Langfuse and intent validation are disabled.

### No Claude Code Credentials Stored

- Uses Claude Code's existing authentication for usage API calls
- No Claude API keys in pace-maker configuration

### Langfuse Credentials

Langfuse keys (`langfuse_public_key`, `langfuse_secret_key`) are stored in `~/.claude-pace-maker/config.json`. This file is not automatically set to 0600; users should secure it manually if needed.

### Secrets Sanitization

Secrets declared via `pace-maker secret add` are stored in the `secrets` table (database file has 0600 permissions). All Langfuse traces are sanitized against declared secrets before upload. The `userId` field is protected from masking to preserve trace identity.

### SQL Injection Prevention

All queries use parameterized statements; no string concatenation in SQL.

---

## Future Enhancements

### Potential Improvements

1. **Machine Learning**: Learn from usage patterns to improve delay estimations
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
- **Fallback Mode**: Operation during API outages using synthetic usage estimates
- **Synthetic Snapshot**: Usage estimate computed from accumulated token costs
- **Pending Trace**: Langfuse trace stored in state file awaiting secrets sanitization before push
- **Intel Line**: Structured metadata line (`§ △... ◎... ■...`) embedded in assistant responses

### B. File Structure

```
claude-pace-maker/
├── src/
│   ├── pacemaker/
│   │   ├── __init__.py
│   │   ├── hook.py              # Hook entry point, safe_print(), all hook handlers
│   │   ├── pacing_engine.py     # Pacing orchestration
│   │   ├── adaptive_throttle.py # Weekend-aware algorithm
│   │   ├── calculator.py        # Utility calculations (time percent, constrained window)
│   │   ├── database.py          # SQLite operations, schema, activity events
│   │   ├── api_client.py        # Claude API client
│   │   ├── usage_model.py       # Single source of truth for usage data
│   │   ├── fallback.py          # Fallback state machine primitives
│   │   ├── profile_cache.py     # Profile caching
│   │   ├── user_commands.py     # pace-maker status/on/off commands
│   │   ├── intent_validator.py  # Pre-tool validation via SDK
│   │   ├── transcript_reader.py # Message extraction from JSONL
│   │   ├── clean_code_rules.py  # Clean code violation definitions
│   │   ├── code_reviewer.py     # Code review integration
│   │   ├── core_paths.py        # Core path detection for TDD enforcement
│   │   ├── excluded_paths.py    # Paths excluded from validation
│   │   ├── extension_registry.py# Source code file extension registry
│   │   ├── constants.py         # Shared constants
│   │   ├── logger.py            # Daily-rotating logger
│   │   ├── prompt_loader.py     # External prompt template loader
│   │   ├── install_commands.py  # Install subcommand handlers
│   │   ├── installer.py         # Install logic
│   │   ├── telemetry/           # Internal telemetry utilities
│   │   │   └── jsonl_parser.py
│   │   ├── langfuse/            # Langfuse telemetry integration
│   │   │   ├── __init__.py
│   │   │   ├── orchestrator.py  # Main Langfuse logic, flush_pending_trace()
│   │   │   ├── state.py         # Per-session state management
│   │   │   ├── push.py          # HTTP batch event push
│   │   │   ├── incremental.py   # Transcript parsing, token counting
│   │   │   ├── trace.py         # Trace creation/finalization
│   │   │   ├── span.py          # Span creation
│   │   │   ├── subagent.py      # Subagent trace handling
│   │   │   ├── client.py        # Langfuse API client
│   │   │   ├── metrics.py       # Metric counters
│   │   │   ├── filter.py        # Event filtering
│   │   │   ├── transformer.py   # Data transformation
│   │   │   ├── cache.py         # Local caching
│   │   │   ├── stats.py         # Statistics
│   │   │   ├── project_context.py # Project metadata
│   │   │   ├── provisioner.py   # Resource provisioning
│   │   │   └── backfill.py      # Historical backfill
│   │   ├── secrets/             # Secrets management
│   │   │   ├── __init__.py
│   │   │   ├── sanitizer.py     # sanitize_trace(), pattern caching
│   │   │   ├── database.py      # Secrets CRUD operations
│   │   │   ├── masking.py       # Regex masking, deep traversal
│   │   │   ├── parser.py        # Secret declaration parsing
│   │   │   └── metrics.py       # Masking metrics
│   │   ├── intel/               # Prompt intelligence
│   │   │   ├── __init__.py
│   │   │   └── parser.py        # § intel line parser
│   │   └── prompts/             # External prompt templates
│   │       ├── pre_tool_use/
│   │       │   └── stage2_code_review.md
│   │       ├── common/
│   │       │   ├── intent_declaration_prompt.md
│   │       │   └── tdd_declaration_prompt.md
│   │       ├── stop/
│   │       │   └── stop_hook_validator_prompt.md
│   │       ├── session_start/
│   │       │   ├── intel_guidance.md
│   │       │   ├── intent_validation_guidance.md
│   │       │   └── secrets_nudge.md
│   │       ├── post_tool_use/
│   │       │   └── subagent_reminder.md
│   │       └── user_commands/
│   │           └── status_message.md
│   └── hooks/
│       ├── post-tool-use.sh
│       ├── user-prompt-submit.sh
│       ├── session-start.sh
│       ├── stop.sh
│       ├── pre-tool-use.sh
│       ├── subagent-start.sh
│       └── subagent-stop.sh
├── tests/                       # Test suite
├── docs/                        # Documentation
├── scripts/
│   └── run_tests.sh             # Independent test runner (avoids WAL contention)
├── install.sh                   # Installation script
└── README.md                    # Quick start guide
```

### C. Testing

**IMPORTANT**: Never run tests as a single pytest process (`python -m pytest tests/`). SQLite WAL contention causes hangs when multiple test files create databases concurrently in the same process.

Always use the independent test runner:

```bash
./scripts/run_tests.sh          # Run all tests (each file independently)
./scripts/run_tests.sh --quick  # Skip slow e2e tests
./scripts/run_tests.sh --tb     # Show failure tracebacks
```

Each test file gets its own pytest process with a 30-second timeout, avoiding WAL lock contention between concurrent database teardown/setup cycles.

For coverage reports, run a specific test file directly:

```bash
python -m pytest tests/test_pacing_engine.py --cov=src/pacemaker --cov-report=html
```

**Test mode optimization**: `PACEMAKER_TEST_MODE=1` is set automatically by `conftest.py`, enabling `PRAGMA synchronous=OFF` for 20x faster database operations in tests.

---

**Document Version**: 2.0
**Last Updated**: 2026-03-10
**Maintainer**: Claude Code Pace Maker Team
**Changes**:
- v2.0: Major update — added Langfuse Telemetry System, Secrets Management, Prompt Intelligence (Intel), Resilient Fallback Mode, Activity Indicators, Global API Poll Coordination sections; added UsageModel and fallback.py to Core Components; updated Database Schema with all current tables (api_cache, fallback_state_v2, accumulated_costs, backoff_state, profile_cache, calibrated_coefficients, global_poll_state, activity_events, blockage_events, langfuse_metrics, secrets, secrets_metrics); updated Configuration with langfuse_enabled, langfuse_host, langfuse_public_key, langfuse_secret_key, tdd_enabled, log_level; updated Security to reflect requests + anthropic SDK dependencies and secrets sanitization; updated Error Handling to reflect fail-closed validation and daily log rotation; updated Testing to reference run_tests.sh; updated File Structure to reflect current codebase; updated architecture diagram
- v1.6: Updated Pre-Tool Validation to two-stage architecture (Stage 1: declaration check with Sonnet, Stage 2: code review with Opus/Sonnet), updated message extraction to combine last 2 messages, updated validation flow diagram, updated prompt file structure to reflect pre_tool_use/, common/, stop/, session_start/, user_commands/ organization
- v1.5: Updated model names to versionless (claude-sonnet-4-5, claude-opus-4-5), added hookEventName to PostToolUse additionalContext
- v1.4: Added Pre-Tool Validation System (intent declaration, Light-TDD enforcement, clean code validation), PreToolUse hook, transcript_reader module, external prompt template
- v1.3: Added SubagentStart/Stop hooks, pacing_decisions table, subagent reminder system, session tempo control, continuous throttling architecture
- v1.2: Updated intent validation from marker-based to AI-powered SDK approach
- v1.1: Added Tempo System section, documented SessionStart and Stop hooks, removed slash command references
