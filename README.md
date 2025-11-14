# Claude Pace Maker

Credit-aware adaptive throttling system for Claude Code that monitors API usage and introduces intelligent delays to extend credit budgets across rate limit windows.

## Overview

Claude Pace Maker is a hook-based system that integrates with Claude Code to automatically pace credit consumption. It polls the Claude API usage endpoint, calculates optimal pacing targets, and applies adaptive delays when usage exceeds target thresholds.

## Features

- **Automatic usage monitoring**: Polls Claude API every 60 seconds to track credit consumption
- **Intelligent pacing**: Logarithmic pacing for 5-hour window, linear for 7-day window
- **Adaptive throttling**: Applies delays when ahead of target pace to extend credit budget
- **Hybrid delay strategy**: Direct sleep for short delays (<30s), prompt injection for longer delays
- **Enterprise support**: Handles both enterprise accounts (5-hour only) and Pro Max accounts (5-hour + 7-day)
- **User control**: Simple commands to enable/disable and check status
- **Global installation**: Installs once and works across all Claude Code projects

## Installation

```bash
git clone https://github.com/LightspeedDMS/claude-pace-maker.git
cd claude-pace-maker
./install.sh
```

The installer will:
1. Create hook scripts in `~/.claude/hooks/`
2. Register hooks in `~/.claude/settings.json`
3. Initialize SQLite database in `~/.claude-pace-maker/`
4. Create default configuration file

## Usage

### Commands

Control the pace maker using simple text commands:

```
pace-maker status    # Show current usage and pacing information
pace-maker on        # Enable throttling
pace-maker off       # Disable throttling
```

### Status Output

```
Pace Maker: ACTIVE

Current Usage:
  5-hour window: 12.0% used
  Resets at: 2025-11-14 08:59:59

Pacing Status:
  Target pace: 13.5% (should be at this point)
  Deviation: -1.5% (ahead of pace)

✓ On pace - no throttling needed
```

## Configuration

Configuration file: `~/.claude-pace-maker/config.json`

```json
{
  "enabled": true,
  "base_delay": 5,
  "max_delay": 120,
  "threshold_percent": 10,
  "poll_interval": 60
}
```

- `enabled`: Enable/disable throttling
- `base_delay`: Base delay in seconds when throttling is triggered
- `max_delay`: Maximum delay in seconds
- `threshold_percent`: Deviation threshold (%) before throttling activates
- `poll_interval`: API polling interval in seconds

## How It Works

1. **PostToolUse Hook**: Executes after each tool use in Claude Code
   - Polls usage API if poll interval has elapsed
   - Stores usage snapshots in SQLite database
   - Calculates deviation from target pace
   - Applies adaptive delay if needed

2. **UserPromptSubmit Hook**: Intercepts user prompts
   - Detects `pace-maker` commands
   - Blocks command from reaching Claude
   - Executes command and displays result

3. **Pacing Algorithm**:
   - Calculates time elapsed in rate limit window
   - Determines target utilization based on time elapsed
   - Compares actual vs target utilization
   - Applies delay proportional to deviation

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed technical documentation.

## Requirements

- Python 3.7+
- Claude Code CLI
- `jq` (JSON processor)
- `curl` (HTTP client)

## License

MIT License - see [LICENSE](LICENSE) for details

## Contributing

Contributions are welcome. Please ensure all tests pass before submitting pull requests.

```bash
python -m pytest tests/
```
