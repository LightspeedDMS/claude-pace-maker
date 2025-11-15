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

```bash
# Global installation (all projects)
./install.sh

# Local installation (specific project)
./install.sh /path/to/project
```

### Usage

```bash
# Check status
pace-maker status

# Enable throttling
pace-maker on

# Disable throttling
pace-maker off
```

### Configuration

Edit `~/.claude-pace-maker/config.json`:

```json
{
  "enabled": true,
  "base_delay": 5,
  "max_delay": 350,
  "threshold_percent": 0,
  "poll_interval": 60,
  "safety_buffer_pct": 95.0,
  "preload_hours": 12.0
}
```

## How It Works

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
- Python 3.7+
- jq (for JSON manipulation)
- Bash shell

## License

MIT License - See LICENSE file for details

## Credits

Created to solve the problem of running out of Claude Code credits mid-session. Uses forward-looking adaptive algorithms with weekend awareness to maximize usage while maintaining safety margins.
