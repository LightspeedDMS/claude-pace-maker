# Claude Pace Maker

Intelligent credit consumption throttling for Claude Code that extends your usage windows by adaptively pacing tool executions.

## What It Does

Pace Maker monitors your Claude Code credit usage in real-time and automatically throttles tool executions to ensure you stay within your 5-hour and 7-day credit limits. It uses weekend-aware adaptive algorithms to maximize credit usage while preventing you from hitting hard limits that would stop Claude Code from working.

## Key Features

- **Weekend-Aware Throttling**: Recognizes you don't work weekends and adjusts pacing accordingly
- **12-Hour Preload**: First 12 weekday hours get 10% allowance to prevent day-1 throttling
- **95% Safety Buffer**: Targets 95% of allowance to leave 5% headroom, preventing hard limit failures
- **Adaptive Algorithm**: Forward-looking calculations adjust delays to reach exactly 95% by window end
- **Zero Tolerance**: Starts throttling immediately when over budget (no deviation threshold)
- **Silent Operation**: Pacing happens transparently in the background

## Quick Start

### Installation

**Option 1: One-command installation via pipx (recommended)**

```bash
pipx install git+https://github.com/LightspeedDMS/claude-pace-maker.git
claude-pace-maker
```

**Option 2: Manual installation from source**

```bash
# Clone repository
git clone https://github.com/LightspeedDMS/claude-pace-maker.git
cd claude-pace-maker

# Global installation (all projects)
./install.sh

# Local installation (specific project)
./install.sh /path/to/project
```

**Installation Process:**

The installer provides detailed feedback for every step:
- ✓ Checks for system dependencies (jq, curl, python3)
- ✓ Detects Python version (requires 3.10+, auto-upgrades if needed)
- ✓ Installs Python packages with version verification (requests≥2.31.0, claude-agent-sdk≥0.1.0)
- ✓ Creates directories and installs hook scripts with correct Python detection
- ✓ Registers hooks in Claude Code settings
- ✓ Shows exactly what's being installed, created, or preserved

The installer is fully idempotent - safe to run multiple times.

### Usage

```bash
# Show help
pace-maker help

# Check status
pace-maker status

# Enable/disable all throttling
pace-maker on
pace-maker off

# Enable/disable weekly (7-day) limit only
pace-maker weekly-limit on
pace-maker weekly-limit off

# Enable/disable session lifecycle tracking (tempo)
pace-maker tempo on
pace-maker tempo off
```

**Throttling Notes:**
- When weekly limit is disabled, only the 5-hour window throttling remains active
- The 7-day window is completely ignored in pacing decisions

**Tempo Tracking:**
- Tempo tracking prevents Claude from prematurely ending implementation sessions
- When Claude says `IMPLEMENTATION_START`, the Stop hook requires Claude to declare `IMPLEMENTATION_COMPLETE` before allowing the session to end
- Enabled by default - disable with `pace-maker tempo off` if you don't want this behavior

### Implementation Validation

Pace Maker includes AI-powered validation of `IMPLEMENTATION_COMPLETE` claims to prevent Claude from declaring work complete when it isn't. This anti-cheating mechanism uses the Claude Agent SDK to analyze conversation history.

**How It Works:**

When Claude declares `IMPLEMENTATION_COMPLETE`, an AI judge automatically:
1. Analyzes the last 5 conversation messages for context
2. Checks for signs of incomplete work:
   - TODO markers or FIXME comments
   - Failing tests or build errors
   - Partial implementations or missing features
   - Unresolved issues or error conditions
3. Either confirms completion or challenges Claude with specific evidence

**The Validation Flow:**

If work appears incomplete, Claude receives a challenge message:
```
❌ IMPLEMENTATION_COMPLETE REJECTED

The AI judge analyzed your work and found evidence of incomplete implementation:
[Specific issues listed here]

You MUST address these issues. Once genuinely complete, use:
CONFIRMED_IMPLEMENTATION_COMPLETE
```

Only after using `CONFIRMED_IMPLEMENTATION_COMPLETE` will the session be allowed to end.

**Requirements:**
- Python 3.10+ required for validation (installer auto-upgrades if needed)
- Claude Agent SDK (automatically installed by installer)
- Falls back gracefully if SDK unavailable (accepts IMPLEMENTATION_COMPLETE without validation)

**Note:** This validation only triggers when tempo tracking is enabled (`pace-maker tempo on`).

### Configuration

Edit `~/.claude-pace-maker/config.json`:

```json
{
  "enabled": true,
  "weekly_limit_enabled": true,
  "tempo_enabled": true,
  "base_delay": 5,
  "max_delay": 350,
  "threshold_percent": 0,
  "poll_interval": 60,
  "safety_buffer_pct": 95.0,
  "preload_hours": 12.0
}
```

**Configuration Options:**
- `enabled`: Master on/off switch for all throttling (default: `true`)
- `weekly_limit_enabled`: Enable/disable 7-day window throttling only (default: `true`)
- `tempo_enabled`: Enable/disable session lifecycle tracking (default: `true`)
- `base_delay`: Minimum throttle delay in seconds (default: `5`)
- `max_delay`: Maximum throttle delay in seconds (default: `350`)
- `threshold_percent`: Deviation threshold before throttling starts (default: `0` = zero tolerance)
- `poll_interval`: Seconds between credit usage checks (default: `60`)
- `safety_buffer_pct`: Target percentage of allowance to use (default: `95.0`)
- `preload_hours`: Hours of preload allowance on weekdays (default: `12.0`)

## How It Works

### Hooks

Pace Maker uses four Claude Code hooks:

1. **SessionStart Hook**: When Claude Code starts, shows the IMPLEMENTATION LIFECYCLE PROTOCOL reminder (when tempo enabled)
2. **UserPromptSubmit Hook**: Intercepts `pace-maker` user commands
3. **PostToolUse Hook**: After each tool execution, checks current credit usage and applies throttling
4. **Stop Hook**: Prevents premature session termination when IMPLEMENTATION_START marker is detected without IMPLEMENTATION_COMPLETE (when tempo enabled)

### Throttling Flow

1. **PostToolUse Hook**: After each tool execution, checks current credit usage
2. **12-Hour Preload**: First 12 weekday hours get flat 10% allowance
3. **Weekend-Aware Calculation**: Computes allowance based on weekday time only (Mon-Fri), frozen on weekends
4. **Safety Buffer Check**: If usage > 95% of allowance, throttles by sleeping
5. **Adaptive Delay**: Calculates exact delay needed to reach 95% by window end

## Status Example

```
Pace Maker: ACTIVE

Current Usage:
  7-day window: 45.0% used
  Resets at: 2025-11-21 16:00:00

Pacing Status:
  Most constrained: 7-day window
  Allowance: 50.0%
  Safe threshold (95%): 47.5%
  Safety buffer remaining: +2.5%

✓ On pace - no throttling needed
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) - Detailed system design and algorithms
- [Preload System](docs/PRELOAD_SYSTEM.md) - 12-hour preload allowance details
- [Weekend Algorithm](docs/WEEKEND_AWARE_ALGORITHM.md) - Weekend-aware throttling details

## Requirements

- Claude Code subscription (Pro or Enterprise)
- Python 3.10+ (required for AI validation feature; installer auto-upgrades from Python 3.7+ if needed)
- Claude Agent SDK (automatically installed by installer for implementation validation)
- jq (for JSON manipulation)
- Bash shell

**Note:** The implementation validation feature gracefully degrades if Python 3.10+ or Claude Agent SDK are unavailable, falling back to basic completion markers.

## License

MIT License - See LICENSE file for details

## Credits

Created to solve the problem of running out of Claude Code credits mid-session. Uses forward-looking adaptive algorithms with weekend awareness to maximize usage while maintaining safety margins.
