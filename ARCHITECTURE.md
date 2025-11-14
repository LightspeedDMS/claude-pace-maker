# Architecture

Technical documentation for Claude Pace Maker implementation.

## System Overview

Claude Pace Maker is a hook-based throttling system that integrates with Claude Code's hook infrastructure. It consists of three main components:

1. **Hook Layer**: Shell scripts that execute on Claude Code events
2. **Python Core**: Business logic for pacing calculations and API interaction
3. **Data Layer**: SQLite database for usage tracking and decision making

## Component Architecture

### Hook Layer

#### PostToolUse Hook (`src/hooks/post-tool-use.sh`)

Executes after each tool use in Claude Code.

**Flow**:
1. Check if pace maker is enabled
2. Locate Python module (development or installed)
3. Execute `pacemaker.hook` with `post_tool_use` argument
4. Python module polls API if needed and applies throttling

**Throttling Methods**:
- **Direct sleep**: For delays < 30 seconds, hook sleeps directly
- **Prompt injection**: For delays >= 30 seconds, prints message for Claude to wait

#### UserPromptSubmit Hook (`src/hooks/user-prompt-submit.sh`)

Intercepts user prompts before Claude processes them.

**Flow**:
1. Read JSON input from stdin (contains `prompt` field)
2. Parse JSON and extract user prompt
3. Execute `pacemaker.hook` with `user_prompt_submit` argument
4. Python module checks if prompt is a pace-maker command
5. If command, output JSON with `decision: "block"` to prevent Claude processing
6. If not command, pass through original JSON

**Command Format**:
```json
{
  "decision": "block",
  "reason": "Status message to display"
}
```

#### Stop Hook (`src/hooks/stop.sh`)

Executes when Claude Code session ends. Currently placeholder for future cleanup logic.

### Python Core

#### API Client (`src/pacemaker/api_client.py`)

Handles communication with Claude OAuth API.

**Key Functions**:
- `load_access_token()`: Loads OAuth token from `~/.claude/.credentials.json`
- `fetch_usage(access_token)`: Polls usage API endpoint
- `parse_usage_response(data)`: Parses API response into normalized format

**API Endpoint**: `https://api.anthropic.com/api/oauth/usage`

**Response Format**:
```json
{
  "five_hour": {
    "utilization": 12.0,
    "resets_at": "2025-11-14T08:59:59.803234+00:00"
  },
  "seven_day": null  // null for enterprise accounts
}
```

**Enterprise Handling**: `seven_day` is null for enterprise accounts. Parser checks for null and sets utilization to 0.0.

#### Calculator (`src/pacemaker/calculator.py`)

Pure mathematical functions for pacing calculations.

**Key Functions**:
- `calculate_time_percent(resets_at, window_hours)`: Calculates elapsed time percentage in window
- `calculate_logarithmic_target(time_pct)`: Logarithmic target for 5-hour window (aggressive early consumption)
- `calculate_linear_target(time_pct)`: Linear target for 7-day window (steady consumption)
- `determine_most_constrained_window()`: Identifies which window requires throttling
- `calculate_delay(deviation_percent, base_delay, threshold, max_delay)`: Calculates delay based on deviation

**Pacing Formulas**:

5-hour window (logarithmic):
```
target = 100 * log10(1 + 9 * time_pct)
```

7-day window (linear):
```
target = 100 * time_pct
```

Delay calculation:
```
if deviation <= threshold:
    delay = 0
else:
    delay = base_delay * (deviation / threshold)
    delay = min(delay, max_delay)
```

#### Pacing Engine (`src/pacemaker/pacing_engine.py`)

Orchestrates pacing decisions.

**Key Functions**:
- `run_pacing_check()`: Main entry point for PostToolUse hook
- `calculate_pacing_decision()`: Determines if throttling is needed
- `determine_delay_strategy()`: Chooses delay method (direct vs prompt)
- `process_usage_update()`: Stores usage snapshot in database

**Decision Flow**:
1. Check if poll interval elapsed
2. Poll API if needed
3. Store snapshot in database
4. Calculate time elapsed in each window
5. Calculate target utilization for each window
6. Determine most constrained window
7. Calculate deviation (actual - target)
8. Apply delay if deviation exceeds threshold
9. Return decision to hook

#### Database (`src/pacemaker/database.py`)

SQLite database operations.

**Schema**:
```sql
CREATE TABLE usage_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,              -- Unix timestamp
    five_hour_util REAL NOT NULL,           -- 5-hour utilization %
    five_hour_resets_at TEXT,               -- ISO format datetime
    seven_day_util REAL NOT NULL,           -- 7-day utilization %
    seven_day_resets_at TEXT,               -- ISO format datetime
    session_id TEXT NOT NULL,               -- Session identifier
    created_at INTEGER NOT NULL             -- Record creation time
);

CREATE INDEX idx_timestamp ON usage_snapshots(timestamp DESC);
CREATE INDEX idx_session ON usage_snapshots(session_id);
```

**Key Functions**:
- `initialize_database()`: Creates schema if not exists
- `insert_usage_snapshot()`: Stores new snapshot
- `query_recent_snapshots()`: Retrieves snapshots for trend analysis
- `cleanup_old_snapshots()`: Removes old data (7+ days)

#### User Commands (`src/pacemaker/user_commands.py`)

Handles pace-maker commands from UserPromptSubmit hook.

**Commands**:
- `pace-maker status`: Shows current usage and pacing information
- `pace-maker on`: Enables throttling
- `pace-maker off`: Disables throttling

**Status Command Flow**:
1. Load configuration from `~/.claude-pace-maker/config.json`
2. Query latest usage snapshot from database
3. Parse datetime strings back to datetime objects
4. Calculate pacing decision using current usage
5. Format status message with usage, target, deviation, and throttling info
6. Return JSON response to hook

#### Hook Entry Point (`src/pacemaker/hook.py`)

Main entry point called by shell hooks.

**Key Functions**:
- `run_hook()`: Executes PostToolUse logic
- `run_user_prompt_submit()`: Executes UserPromptSubmit logic
- `main()`: Routes to appropriate function based on command line argument

**State Management**:
- State stored in `~/.claude-pace-maker/state.json`
- Contains: `session_id`, `last_poll_time`, `last_cleanup_time`
- Updated after each poll

### Data Layer

#### Database Location

`~/.claude-pace-maker/usage.db`

#### State File

`~/.claude-pace-maker/state.json`

```json
{
  "session_id": "session-1763094307",
  "last_poll_time": "2025-11-14T03:26:19.828866",
  "last_cleanup_time": null
}
```

#### Configuration File

`~/.claude-pace-maker/config.json`

```json
{
  "enabled": true,
  "base_delay": 5,
  "max_delay": 120,
  "threshold_percent": 10,
  "poll_interval": 60
}
```

## Hook Registration

Hooks are registered in `~/.claude/settings.json` using Claude Code's array-based hook format:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/post-tool-use.sh"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/user-prompt-submit.sh"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/stop.sh"
          }
        ]
      }
    ]
  }
}
```

## Installation Process

The `install.sh` script performs the following:

1. **Check dependencies**: Verifies `jq`, `curl`, `python3` are installed
2. **Create directories**: Creates `~/.claude/hooks/` and `~/.claude-pace-maker/`
3. **Install hooks**: Copies hook scripts and sets executable permissions
4. **Create configuration**: Writes default config if not exists
5. **Initialize database**: Creates SQLite schema
6. **Register hooks**: Updates `~/.claude/settings.json` with hook configuration
7. **Verify installation**: Checks all files, permissions, and schema

The installer is idempotent and preserves existing configuration.

## Data Flow

### PostToolUse Flow

```
Claude Code Tool Use
    ↓
PostToolUse Hook (shell)
    ↓
Check if enabled
    ↓
Execute Python module
    ↓
Check poll interval elapsed
    ↓ (if elapsed)
Poll Claude API
    ↓
Parse response
    ↓
Store snapshot in database
    ↓
Calculate pacing decision
    ↓
Determine delay strategy
    ↓ (if should_throttle)
Apply delay (sleep or prompt)
    ↓
Return to Claude Code
```

### UserPromptSubmit Flow

```
User submits prompt
    ↓
UserPromptSubmit Hook (shell)
    ↓
Read JSON from stdin
    ↓
Parse JSON and extract prompt
    ↓
Execute Python module
    ↓
Check if pace-maker command
    ↓ (if command)
Execute command logic
    ↓
Return JSON with decision: "block"
    ↓
Claude Code blocks prompt
    ↓
Display command output to user

    ↓ (if not command)
Pass through original JSON
    ↓
Claude Code processes normally
```

## Performance Considerations

### API Polling

- Polls every 60 seconds by default (configurable)
- Graceful degradation on API failures
- No retry logic to avoid hammering API

### Database Operations

- Single table with indexes on timestamp and session_id
- Cleanup runs periodically to remove old snapshots
- No locks or transactions needed (single writer)

### Hook Execution Time

- PostToolUse: ~100-200ms when polling, ~1-5ms when not
- UserPromptSubmit: ~1-5ms for passthrough, ~10-20ms for commands
- Direct sleep: Blocks hook execution
- Prompt injection: No blocking, Claude waits

## Error Handling

All components implement graceful degradation:

- **API failures**: Return None, no throttling applied
- **Database errors**: Log error, continue without throttling
- **Parse errors**: Return None, continue without throttling
- **Hook errors**: Print to stderr, continue execution

Philosophy: Better to not throttle than to crash or block the user.

## Testing

### Unit Tests

- `tests/test_api_client.py`: API client and parsing
- `tests/test_calculator.py`: Pacing calculations
- `tests/test_database.py`: Database operations
- `tests/test_pacing_engine.py`: Pacing decision logic
- `tests/test_user_commands.py`: Command parsing and execution

### Integration Tests

- `tests/test_hook.py`: Hook execution
- `tests/test_e2e.py`: End-to-end flow
- `tests/test_install.py`: Installation verification
- `tests/test_install_e2e.py`: Full installation test

### Manual Testing

- `scripts/manual_e2e_test.py`: Manual E2E testing script

Run tests:
```bash
python -m pytest tests/
```

## Future Enhancements

- Predictive throttling based on usage trends
- Per-model pacing (different models have different costs)
- Notification system for approaching limits
- Web dashboard for usage visualization
- Multi-user support for team accounts
