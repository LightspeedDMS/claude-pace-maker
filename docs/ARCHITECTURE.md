# Claude Pace Maker - Architecture Documentation

## Table of Contents

1. [System Overview](#system-overview)
2. [Core Components](#core-components)
3. [Hook Integration](#hook-integration)
4. [Throttling Algorithms](#throttling-algorithms)
5. [Weekend-Aware Logic](#weekend-aware-logic)
6. [Safety Buffer System](#safety-buffer-system)
7. [Data Flow](#data-flow)
8. [Configuration](#configuration)
9. [Database Schema](#database-schema)
10. [Error Handling](#error-handling)

---

## System Overview

Claude Pace Maker is a hook-based credit throttling system for Claude Code that prevents hitting API rate limits by intelligently pacing tool executions.

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

**Trigger**: After every tool execution in Claude Code
**Script**: `~/.claude/hooks/post-tool-use.sh`
**Timeout**: 360 seconds (allows up to 350s sleep)

**Flow**:
1. Check if pace-maker is enabled
2. Run pacing check (sleeps if needed)
3. Display steering message: `[Remember: Say 'Mission completed.' when ALL tasks are done]`
4. Return JSON with `decision: "allow"` and steering message

**Python Handler**: `src/pacemaker/hook.py:run_hook()`

### UserPromptSubmit Hook

**Trigger**: Before user prompt is sent to Claude
**Script**: `~/.claude/hooks/user-prompt-submit.sh`
**Purpose**: Intercept `pace-maker` commands

**Flow**:
1. Check if input starts with "pace-maker"
2. If yes: execute command, block from reaching Claude, display output
3. If no: pass through to Claude unchanged

**Python Handler**: `src/pacemaker/hook.py:run_user_prompt_submit()`

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

### State File

**Location**: `~/.claude-pace-maker/state.json`

**Purpose**: Track hook state across invocations

**Schema**:
```json
{
  "session_id": "session-1731607200",
  "last_poll_time": "2025-11-14T12:00:00.279746",
  "last_cleanup_time": "2025-11-14T00:00:00.000000"
}
```

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

**Cleanup**: Daily cleanup removes snapshots older than 8 days

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

**Document Version**: 1.0
**Last Updated**: 2025-11-14
**Maintainer**: Claude Code Pace Maker Team
