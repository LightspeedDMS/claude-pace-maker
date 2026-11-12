# Claude Pace Maker

Intelligent credit consumption throttling and code quality enforcement for Claude Code.

## Features

- **Credit Throttling**: Adaptive pacing for 5-hour and 7-day usage windows
- **Stale Data Detection**: Resilient pacing calculations when usage data is outdated
- **Model Preference**: Nudges Claude to use specific model for subagents (quota balancing)
- **Intent Validation**: Requires intent declaration before code modifications
- **TDD Enforcement**: Core code paths require test declarations
- **Clean Code Checks**: Blocks security vulnerabilities, anti-patterns, and logic bugs
- **Folder Exclusion**: Exclude paths from TDD/Clean Code enforcement
- **Session Lifecycle**: Prevents premature session endings via AI validation
- **Langfuse Telemetry**: Optional tracing integration with Langfuse for session/trace/span tracking
- **Blockage Telemetry**: Track and report validation blocks via `pace-maker blockage-stats`
- **Daily Log Rotation**: Automatic log rotation (one file per day, 15 days retention)
- **Enhanced Status Display**: Shows versions, Langfuse connectivity, and error counts
- **Prompt Intelligence (Intel)**: Optional per-prompt metadata (frustration, specificity, task type, quality, iteration) attached to Langfuse traces for dashboard analytics
- **Secrets Management**: Sanitizes sensitive data from Langfuse trace outputs
- **Langfuse Auto-Provisioning**: Automatic API key provisioning with configurable URL
- **Danger Bash Validation**: Two-phase validation for dangerous Bash commands — regex gate fast-rejects without LLM call, then LLM validates intent alignment when INTENT: is present. 55 default rules covering work destruction (git) and system destruction (rm, kill, etc.)
- **Activity Indicators**: Real-time hook activity tracking (IV, TD, CC, ST, CX, PA, PL, LF, SS, SM, SE, SA, UP) with color-coded status
- **Global API Poll Coordination**: SQLite singleton prevents redundant API polling across concurrent sessions
- **Companion Tool Installer**: `pace-maker install claude-usage-monitor` installs the usage monitoring dashboard
- **Cross-Session Awareness Registry**: Prevents "rogue agent" hallucinations when multiple Claude Code sessions work concurrently on the same repository. Each session is factually aware of sibling sessions in the same workspace via a shared SQLite registry. At session start, on subagent spawn, periodically every 5 tool uses, and before destructive Bash operations, the hook injects a banner listing active sibling sessions — so Claude no longer mistakes another session's unfamiliar files for corruption and attempts to "recover" by running `git checkout --`, `git reset --hard`, `git clean`, or `rm`. Strict workspace isolation: sessions in different repositories never see each other. CLI: `pace-maker sessions list`, `pace-maker cross-session-awareness on/off`
- **Memory Localization**: Makes Claude Code's per-project memory portable across clones by symlinking the central `~/.claude/projects/<encoded>/memory/` folder to a repo-local `.claude-memory/` directory that developers commit to git. When you clone a repo containing `.claude-memory/`, the SessionStart hook auto-links it — no manual symlink surgery per clone. Local always wins: stale central content is moved to a rename-backup then replaced with the symlink, with rollback on OSError. Optimistic race handling tolerates concurrent sessions. Safety asserts prevent any rmtree outside the central base. CLI: `pace-maker localize-memory` (seed a fresh project), `pace-maker memory-localization on/off/status/unlink` (toggle gate, reverse localization). Default enabled; state shown in `pace-maker status`
- **Self-Identifying Provenance Tagging**: Every message pace-maker injects into Claude's context (SessionStart/SubagentStart guidance, PostToolUse nudges, PreToolUse block reasons, Stop-hook messages, CSA banners) is wrapped in a `[pace-maker · <channel>]` tag, and relayed third-party reviewer/verifier LLM output (Stage 2 code review, danger-bash Phase 2) in a distinct `[pace-maker · reviewer-relay · model=<id>]` tag. SessionStart and SubagentStart also emit a manifest declaring the complete closed enumeration of channels pace-maker will ever use plus a four-item never-list, so Claude can recognize its own governance layer instead of misidentifying it as prompt injection — the manifest is the contract, the tag is just an index into it.

## Installation

### Option 1: Claude Code Plugin (recommended)

Install pace-maker as a Claude Code plugin via a marketplace for automatic hook registration and updates.

**Prerequisites**: A marketplace repo that includes pace-maker (see [Setting Up a Marketplace](#setting-up-a-marketplace) below).

```bash
# One-time: add your organization's marketplace
claude plugin marketplace add https://github.com/YourOrg/claude-plugins.git

# Install pace-maker
claude plugin install claude-pace-maker@claude-plugins
```

That's it. The first Claude Code session after installation automatically bootstraps:
- `~/.claude-pace-maker/config.json` with production defaults
- `~/.claude-pace-maker/source_code_extensions.json` for validation
- `~/.claude-pace-maker/pacemaker` symlink to the plugin Python package (required for CLI)
- `~/.local/bin/pace-maker` CLI symlink (if `~/.local/bin` is in PATH)
- A managed Python venv at `~/.claude-pace-maker/venv` with dependencies (`requests`, `pyyaml`, `claude-agent-sdk`) on SessionStart

No `install.sh` or manual `settings.json` editing required.

**CLI not found after `claude plugin install`?** The plugin is registered in Claude, but bootstrap runs on the first session (or manually):

```bash
# Ensure ~/.local/bin is on PATH, then:
pace-maker doctor

# Or without a working CLI yet:
bash "$(find ~/.claude -path '*/claude-pace-maker/scripts/doctor.sh' 2>/dev/null | sort -V | tail -1)"
```

Add to `~/.bashrc` or `~/.zshrc` if needed: `export PATH="$HOME/.local/bin:$PATH"`

### Option 2: pipx

```bash
pipx install git+https://github.com/LightspeedDMS/claude-pace-maker.git
claude-pace-maker
```

### Option 3: From source

```bash
git clone https://github.com/LightspeedDMS/claude-pace-maker.git
cd claude-pace-maker
./install.sh
```

---

## Setting Up a Marketplace

To distribute pace-maker across your organization, add it as a submodule in a Claude Code marketplace repository.

### 1. Create a marketplace repo

```bash
mkdir claude-plugins && cd claude-plugins
git init
mkdir -p .claude-plugin
```

### 2. Add the marketplace manifest

Create `.claude-plugin/marketplace.json`:

```json
{
  "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
  "name": "your-org-plugins",
  "description": "Internal Claude Code plugins for YourOrg",
  "plugins": [
    {
      "name": "claude-pace-maker",
      "description": "Usage observability, adaptive pacing, and Langfuse telemetry for Claude Code",
      "category": "productivity",
      "source": {
        "source": "url",
        "url": "https://github.com/LightspeedDMS/claude-pace-maker.git"
      },
      "homepage": "https://github.com/LightspeedDMS/claude-pace-maker"
    }
  ]
}
```

### 3. (Optional) Add as a git submodule

If you prefer vendoring the plugin source instead of referencing the URL:

```bash
git submodule add https://github.com/LightspeedDMS/claude-pace-maker.git plugins/claude-pace-maker
```

Then update `marketplace.json` to use a local source:

```json
{
  "name": "claude-pace-maker",
  "source": "./plugins/claude-pace-maker"
}
```

### 4. Push and distribute

```bash
git add -A && git commit -m "Add claude-pace-maker plugin"
git remote add origin https://github.com/YourOrg/claude-plugins.git
git push -u origin main
```

Users install with:

```bash
claude plugin marketplace add https://github.com/YourOrg/claude-plugins.git
claude plugin install claude-pace-maker@your-org-plugins
```

### Plugin Directory Structure

When installed as a plugin, pace-maker uses this structure:

```
claude-pace-maker/                    (repo root = plugin root)
  .claude-plugin/
    plugin.json                       (metadata: name, description, author, version)
  hooks/
    hooks.json                        (7 hook definitions using ${CLAUDE_PLUGIN_ROOT})
  scripts/
    hook.sh                           (unified entry point, lazy-init, Python dispatch)
    pace-maker                        (CLI wrapper script, symlink target)
  config/
    config.defaults.json              (default config.json template)
    source_code_extensions.json       (file extensions for validation)
  src/pacemaker/                      (Python source code)
```

The `${CLAUDE_PLUGIN_ROOT}` variable is set by Claude Code at runtime, pointing to the plugin's installation directory. All hook commands reference this variable for portability.

---

## Migrating from Legacy Installation

If you previously installed pace-maker via `install.sh` and want to switch to plugin mode:

```bash
# 1. Install the plugin via marketplace (see above)

# 2. Run the migration script to remove legacy hooks from settings.json
cd /path/to/claude-pace-maker
bash migrate-to-plugin.sh

# 3. Verify: settings.json should no longer contain pace-maker hook entries
cat ~/.claude/settings.json | jq '.hooks'
```

The migration script:
- Removes all pace-maker hook entries from `~/.claude/settings.json`
- Deletes legacy hook scripts from `~/.claude/hooks/` (pre-tool-use.sh, post-tool-use.sh, etc.)
- Removes the `~/.claude/hooks/pacemaker/` directory
- Preserves all non-pace-maker hooks intact

Your `~/.claude-pace-maker/config.json` and database are preserved — no reconfiguration needed.

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
pace-maker langfuse on|off|status|configure  # Langfuse telemetry controls
pace-maker loglevel 0-4           # Set log level (0=OFF, 1=ERROR, 2=WARNING, 3=INFO, 4=DEBUG)
pace-maker excluded-paths list         # List excluded paths (skip TDD/clean code)
pace-maker excluded-paths add PATH     # Add excluded path
pace-maker excluded-paths remove PATH  # Remove excluded path
pace-maker secrets add VALUE           # Declare a secret value for masking
pace-maker secrets addfile PATH        # Declare a file as containing secrets
pace-maker secrets list                # List declared secrets
pace-maker danger-bash list            # List danger bash rules
pace-maker danger-bash add ID PATTERN CATEGORY DESC  # Add custom danger bash rule
pace-maker danger-bash remove ID       # Remove danger bash rule
pace-maker blockage-stats              # Show validation blockage statistics
pace-maker install claude-usage-monitor  # Install claude-usage-monitor companion tool
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

Files in core paths require a TDD declaration. A file is treated as a core path in two ways:

1. **Directory name match** — the file sits under one of these segments (customizable via `pace-maker core-paths add/remove`, see below): `src/`, `lib/`, `code/`, `core/`, `source/`, `libraries/`, `kernel/`, `app/`, `routes/`, `services/`, `internal/`.
2. **Structural project-marker fallback** — no directory name matches, but a per-ecosystem project marker file is found anywhere in the file's ancestor directories: `*.csproj`/`*.sln` (.NET), `pyproject.toml`/`setup.py` (Python), `package.json` (Node), `build.gradle`/`build.gradle.kts`/`pom.xml` (Java/Kotlin), `go.mod` (Go), `Cargo.toml` (Rust). This catches production code whose source-root directory is named after the project itself (e.g. a .NET solution's `MyProject/Program.cs` with no `src/` wrapper) rather than a generic word. Test files (excluded paths, non-source extensions, filename-suffix patterns like `*_test.go`, and dedicated test projects like `MyProject.Tests.csproj`) never trigger either check.

Once a file is identified as core, it requires:

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
- **Danger Bash Rules**: `~/.claude-pace-maker/danger_bash_rules.yaml` - Customizable dangerous command rules (same merge strategy as clean code rules)
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

## Resilient Fallback Mode

When Anthropic's usage API becomes unavailable (HTTP 429 rate limiting), pace-maker automatically switches to **fallback mode** — synthesizing utilization estimates from accumulated token costs so that pacing continues uninterrupted.

### How It Works

```
Normal Mode                        Fallback Mode
┌──────────────┐    429 error     ┌──────────────────────┐
│  Real API     │ ──────────────> │  Synthetic estimates  │
│  utilization  │                 │  from token costs     │
│  from Claude  │ <────────────── │  + baseline snapshot  │
└──────────────┘    API recovers  └──────────────────────┘
```

1. **Entry**: When the API returns 429 or exponential backoff is active, pace-maker captures the last known utilization as a baseline and begins tracking token costs per session
2. **During fallback**: Each hook invocation accumulates cost (input, output, cache tokens × model pricing). Synthetic utilization = baseline + accumulated_cost × coefficient. Reset windows are projected forward using the last known reset timestamp
3. **Window rollovers**: When a 5-hour or 7-day window rolls over during fallback, the accumulated cost offset is recorded so only post-rollover costs count toward the new window
4. **Exit**: When the API returns a successful response, pace-maker compares its synthetic prediction against the real value, calibrates the coefficient, and returns to normal mode

### Coefficient Calibration

The mapping from dollar cost to utilization percentage depends on your subscription tier. Pace-maker ships with default coefficients for 5x and 20x tiers, but **automatically learns better values** over time:

- When the API recovers after a fallback period, the system computes: `ratio = real_utilization / synthetic_prediction`
- The coefficient is adjusted: `new = weighted_average(old_coefficient, measured_coefficient)`
- Calibrated coefficients are stored per tier in SQLite (`calibrated_coefficients` table) and used in preference to defaults
- A clamp range (0.1×–10×) prevents wild swings from outlier measurements
- Each calibration event increments a sample counter for progressively stable averaging

### UsageModel — Single Source of Truth

The `UsageModel` class (`src/pacemaker/usage_model.py`) is the unified access point for all usage data:

```python
from pacemaker.usage_model import UsageModel

model = UsageModel()
snapshot = model.get_current_usage()  # Returns real or synthetic
if snapshot:
    print(f"5h: {snapshot.five_hour_util:.1f}%")
    print(f"Synthetic: {snapshot.is_synthetic}")
```

Key design principles:
- **Stateless between calls**: All state lives in SQLite (WAL mode for concurrent access)
- **Can be instantiated fresh** on every hook invocation — no in-memory state to stale
- **Automatic mode selection**: `get_current_usage()` returns real API data in normal mode, synthetic estimates in fallback mode
- **Concurrency-safe**: Cost accumulation uses INSERT-only pattern (no read-modify-write races)

### State Storage

All fallback state is stored in SQLite tables (replacing previous JSON files):

| Table | Purpose |
|-------|---------|
| `api_cache` | Last successful API response (raw) |
| `fallback_state_v2` | Current mode, baseline, tier, rollover offsets |
| `accumulated_costs` | Per-session cost records (INSERT-only) |
| `backoff_state` | Exponential backoff tracking |
| `calibrated_coefficients` | Learned cost→utilization mappings per tier |
| `profile_cache` | Cached profile data for tier detection |

## Langfuse Telemetry Integration

Optional integration with [Langfuse](https://langfuse.com) for observability and tracing.

### Configuration

```bash
pace-maker langfuse on                           # Enable Langfuse
pace-maker langfuse off                          # Disable Langfuse
pace-maker langfuse status                       # Show connection status
pace-maker langfuse configure URL PUBLIC SECRET  # Set credentials
```

### Features

- **Session Tracking**: Creates Langfuse sessions per Claude Code conversation
- **Trace Hierarchy**: Tracks main conversation and subagent traces
- **Span Details**: Records tool calls, token usage, and timing
- **24-Hour Metrics**: Aggregates sessions, traces, and spans

### Requirements

- Self-hosted or cloud Langfuse instance
- API credentials (public key + secret key)

### Prompt Intelligence Telemetry (Intel)

Optional per-prompt metadata that Claude emits at the start of responses. The intel line is automatically parsed, stripped from visible output, and attached to the corresponding Langfuse trace as metadata.

#### Intel Line Format

Claude emits a single line starting with the `§` marker:

```
§ △0.8 ◎surg ■bug ◇0.7 ↻2
```

#### Symbol Vocabulary

| Symbol | Field | Type | Values | Description |
|--------|-------|------|--------|-------------|
| `§` | _(marker)_ | - | - | Identifies the intel line (required) |
| `△` | frustration | float | 0.0 - 1.0 | User frustration level (0=calm, 1=high) |
| `◎` | specificity | enum | `surg`, `const`, `outc`, `expl` | How specific the request is |
| `■` | task_type | enum | `bug`, `feat`, `refac`, `research`, `test`, `docs`, `debug`, `conf`, `other` | Nature of the work |
| `◇` | quality | float | 0.0 - 1.0 | Claude's confidence in understanding/solution |
| `↻` | iteration | int | 1 - 9 | Number of attempts on this issue |

**Specificity values:**
- `surg` = Surgical (very specific, targeted change)
- `const` = Constrained (specific scope with boundaries)
- `outc` = Outcome-focused (goal stated, approach open)
- `expl` = Exploratory (vague, needs clarification)

#### Langfuse Metadata Keys (for Dashboards)

Intel fields are attached to Langfuse traces with the `intel_` prefix. Use these keys when building Langfuse dashboards, filters, and analytics:

| Langfuse Metadata Key | Source Field | Type | Example Value |
|----------------------|-------------|------|---------------|
| `intel_frustration` | `△` | float | `0.8` |
| `intel_specificity` | `◎` | string | `"surg"` |
| `intel_task_type` | `■` | string | `"bug"` |
| `intel_quality` | `◇` | float | `0.7` |
| `intel_iteration` | `↻` | int | `2` |

**Dashboard examples:**
- Filter traces by `intel_task_type = "bug"` to see all bug fix conversations
- Chart `intel_frustration` over time to detect recurring pain points
- Group by `intel_specificity` to analyze prompt quality patterns
- Alert on `intel_iteration >= 4` to identify stuck conversations
- Correlate `intel_quality` with session duration for efficiency metrics

#### Data Flow

1. **Session start**: Intel guidance prompt is injected (full symbol vocabulary, from `prompts/session_start/intel_guidance.md`)
2. **User prompt submit**: Short nudge reminder injected after each user prompt (`§ intel: Start your response with a § intel line...`)
3. **Claude responds**: Optionally emits `§` intel line at the start of a response
4. **Post-tool hook**: Parses intel from the last 3 assistant messages in the transcript
5. **Langfuse push**: Attaches parsed intel as trace metadata (upsert to current trace)
6. **Output cleanup**: Intel line is stripped from trace output so users never see it

All fields are optional. Missing or invalid fields are silently skipped (no defaults injected).

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
| SessionStart | Initializes state, injects intel guidance prompt, Langfuse session creation |
| UserPromptSubmit | Captures prompts, handles CLI commands, deferred trace creation |
| PreToolUse | Intent validation, TDD checks, clean code validation |
| PostToolUse | Credit throttling, subagent reminders, intel parsing, Langfuse trace/span push, activity event recording |
| Stop | Session completion validation, Langfuse trace finalization with token usage |
| SubagentStart/Stop | Tracks subagent context, Langfuse subagent trace management |

## Configuration File

`~/.claude-pace-maker/config.json`:

```json
{
  "enabled": true,
  "weekly_limit_enabled": true,
  "five_hour_limit_enabled": true,
  "tempo_enabled": true,
  "intent_validation_enabled": true,
  "tdd_enabled": true,
  "danger_bash_enabled": true,
  "subagent_reminder_enabled": true,
  "preferred_subagent_model": "auto",
  "base_delay": 5,
  "max_delay": 350,
  "safety_buffer_pct": 95.0,
  "preload_hours": 12.0,
  "log_level": 2,
  "langfuse_enabled": false,
  "langfuse_host": "",
  "langfuse_public_key": "",
  "langfuse_secret_key": ""
}
```

Optional: `"shared_data_dir": "/var/lib/claude-pace-maker"` (see [Shared data directory](#shared-data-directory-multiple-os-users-optional)). Absent by default.

## Shared data directory (multiple OS users, optional)

By default every user's pace-maker is fully self-contained in `~/.claude-pace-maker`. If several OS users on one machine (for example your main account and a sandbox account that runs Claude Code) should report into **one** database so you can watch them all from your own account, opt in to a shared data directory. **Existing installs are not affected**: without the setting nothing changes, and upgrading in place keeps your `usage.db` (including its stored secrets) working as before.

### Setup

1. As root, create a group and a setgid directory both users can write:

   ```bash
   groupadd pmshared && usermod -aG pmshared alice && usermod -aG pmshared bob
   install -d -o root -g pmshared -m 2770 /var/lib/claude-pace-maker   # setgid, NO sticky bit
   setfacl -d -m g:pmshared:rw /var/lib/claude-pace-maker   # optional, see below
   ```

2. In **each** user's own `~/.claude-pace-maker/config.json` add:

   ```json
   { "shared_data_dir": "/var/lib/claude-pace-maker" }
   ```

      **Do not add the sticky bit** (`3770`, `chmod +t`). On Ubuntu (and any system with `fs.protected_regular=2`, which systemd's default sysctl sets) the kernel refuses `open(O_CREAT)` on an existing file owned by another user inside a sticky, group-writable directory. SQLite opens the database and its `-wal`/`-shm` files exactly that way, gets `EACCES` and silently falls back to read-only, so **every shared write fails for every user** ("attempt to write a readonly database"). Fedora's default (`1`) only applies to world-writable sticky directories, which is why this does not show everywhere. pace-maker detects the combination: `pace-maker status` prints `shared_data_dir '<dir>' has the sticky bit and fs.protected_regular=2 ... Use mode 2770 (chmod o-t ...)` and every shared database open fails with that message in the log (it also reports any shared database it may write but cannot open with `O_CREAT`). If you see it, run `chmod o-t <dir>`.

      Security note (without the sticky bit): any group member can delete or rename another member's `usage.db` and put a symlink there. pace-maker refuses symlinks and other non-regular files for the shared databases and their `-wal`/`-shm`/`-journal` side files (it never follows them and never changes a mode through one), and never changes a mode on a file it does not own. SQLite itself cannot be told not to follow symlinks from Python, so a file swapped for a symlink in the instant between pace-maker's last check and SQLite's own `open()` is a narrow residual race that this configuration does not close. The worst case is that, if the swapped-in target is a file the victim could already write, SQLite's open-time schema statements (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE`, journal-mode setup) run against it before pace-maker's post-open check refuses and closes the connection; no pace-maker data rows are written in that window, and pace-maker will not widen its permissions. Only share the directory with accounts you accept that risk for.

The setting must be an absolute path and cannot be `~/.claude-pace-maker` itself. It lives in the per-user config, never in the shared directory.

3. Run `pace-maker status`. It prints a `Shared Data Dir:` block with the directory and the live sessions of all OS users (also `pace-maker sessions`, which gains an `OS_USER` column).

If the setting is malformed (empty, not a string, relative, or the per-user directory itself) or the directory is missing or not writable, hooks log the error and `pace-maker status` shows `pace-maker: shared_data_dir '<dir>' does not exist` (or `is not writable by <user> (need group rw, setgid dir)`; a malformed value shows `Shared Data Dir: INVALID`). Hooks keep running (the error never breaks imports), but every shared database open fails and is logged: there is **no silent fallback** to the per-user directory. pace-maker never creates the shared directory itself. Secrets keep resolving to your per-user store, so masking still works. A `config.json` that cannot be parsed is treated as "setting absent" (`pace-maker status` already reports a corrupted config).

### What is shared and what is not

| Data | Location in shared mode | Why |
|---|---|---|
| `usage.db` (usage snapshots, pacing decisions, blockages, activity/governance events, `api_cache`, `global_poll_state`, fallback state, accumulated costs) | **shared** | One account, one view. Because `global_poll_state` is shared, usage API polling stays at the configured `poll_interval` for the whole machine, not per user |
| `session_registry.db` (sessions, agents, agent actions) | **shared** | See every user's sessions and agents |
| `secrets` table (declared SECRET_TEXT / SECRET_FILE values) | per user: `~/.claude-pace-maker/secrets.db` (0600) | Secrets must never be readable by the other user. On first use in shared mode the secrets already in your per-user `usage.db` are **copied** to `secrets.db` (the old copy is left in place for rollback) |
| `config.json`, `install_source`, `state.json`, `*.yaml` rules, `pacemaker/` code copy, hooks, logs, `profile_cache.json`, `langfuse_state/` | per user | Code and configuration must never be writable by another user |
| `version_status.db`, `intent_declarations.db` | per user | They describe one user's installation and one user's declared intents; sharing them would let another user influence this user's gates |

The shared directory holds only SQLite files (`*.db`, `-wal`, `-shm`, `-journal`). Nothing read from it is used as a path, interpreter, module or command.

### Permissions

Shared DB files are created and kept `0660` regardless of the user's umask, and are never chmod'd to `0600`. SQLite creates the `-wal`/`-shm` side files lazily with the process umask and only then corrects their mode; with the common umask 022 the other user could hit them in that window ("attempt to write a readonly database" was reproduced with two real users). So pace-maker sets umask `007` **only while it opens a shared connection** (the connect and first read that create those files), then restores it. Why not hold it for the whole connection: that would leak into other threads and child processes for as long as the connection lives. Why not rely on an ACL alone: pace-maker cannot assume one exists. The remaining side effect is that, for a few milliseconds per DB open, other threads in the same hook process create files with umask 007; this is harmless when users have private primary groups (the default on most Linux distributions) but would make such files group-writable if both users share one primary group. Use private primary groups plus the shared group only for the data directory.

Strongly recommended: the default ACL from step 1 (`setfacl -d -m g:pmshared:rw ...`). A default ACL overrides the umask for files created in that directory, and it also covers processes pace-maker does not control (for example the `claude-usage` monitor).

### Rows say who wrote them

Each session, agent and usage/activity row carries `os_user` (the login name of the writer). Rows written before the upgrade have `NULL`.

### Trust model (read this before sharing with a sandbox user)

The shared directory is a data channel, not a trust boundary between equals: whoever can write the SQLite files can write any row, including a false `os_user`.

- **Session registry.** Rows are untrusted. Cross-session-awareness nudges (session-start banner, periodic reminder, danger-bash warning) only use rows whose `os_user` is you (or NULL), and in shared mode only rows whose `session_id` is a canonical UUID; rows whose `pid` is not an integer or whose timestamps are not numbers are ignored (SQLite lets another user store text there); every field is stripped of control characters and newlines before it reaches Claude. `pace-maker status` / `sessions` strip them before printing to the terminal and show `<invalid-id>` for foreign non-UUID ids. `os_user` is self-reported, so this sanitising, not the `os_user` filter, is what protects you from a hostile writer.
- **Pacing (accepted risk, AC5).** `api_cache`, `global_poll_state`, the fallback state and accumulated costs are shared on purpose so the account is polled once. A user who can write `usage.db` can therefore influence the pacing numbers and throttle delays every user sees. Do not share with an account you do not trust to behave; the shared usage figures are data about one Anthropic account, not authenticated input.
- **Code and secrets** are never read from or written to the shared directory.

### Upgrade and rollback notes

- Upgrading without the setting: nothing to do. `usage.db` gets nullable `os_user` columns added in place (schema version 2); older pace-maker versions and the `claude-usage` monitor keep working.
- Turning shared mode on later starts a fresh shared `usage.db`; history in your per-user `usage.db` is not merged. Turning it off again returns to the per-user files, which are untouched.
- Secrets added while in shared mode live in `secrets.db`. If you turn shared mode off again, masking still reads that `secrets.db` (in addition to `usage.db`), so those secrets keep being masked; `pace-maker secrets list/remove` only operate on `usage.db` in that mode.
- The `claude-usage` monitor still reads `~/.claude-pace-maker`; pointing it at the shared directory needs a matching change in that project.

## Requirements

- Python 3.9+
- Claude Agent SDK
- jq
- Bash

## Documentation

- [Changelog](CHANGELOG.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Preload System](docs/PRELOAD_SYSTEM.md)
- [Weekend Algorithm](docs/WEEKEND_AWARE_ALGORITHM.md)
- [Test Report](reports/pre_tool_validation_test_report.md)

## License

MIT
