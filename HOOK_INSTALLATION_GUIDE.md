# Claude Code Hook Installation Guide

## Understanding Claude Code's Settings Merge Behavior

**CRITICAL**: Claude Code merges global and project settings in a **non-intuitive** way:

### How Settings Merge Works

When you have both:
- Global settings: `~/.claude/settings.json`
- Project settings: `<project>/.claude/settings.json`

**Claude Code's merge behavior**:
- For each hook type (SessionStart, UserPromptSubmit, PostToolUse, Stop, etc.)
- If the project settings defines that hook type → **COMPLETELY REPLACES** global definition
- If the project settings doesn't define that hook type → Uses global definition

**It does NOT merge hook arrays - it's a complete override per hook type!**

### Example

**Global settings.json**:
```json
{
  "hooks": {
    "SessionStart": [
      {"hooks": [{"type": "command", "command": "~/.claude/hooks/session-start.sh"}]}
    ]
  }
}
```

**Project settings.json**:
```json
{
  "hooks": {
    "SessionStart": [
      {"hooks": [{"type": "command", "command": "tdd-guard"}]}
    ]
  }
}
```

**Result**: When working in this project, ONLY `tdd-guard` runs on SessionStart. The global `session-start.sh` is **completely ignored**.

---

## Installation Modes

### Global Installation (Primary - Recommended)

**Use case**: Enable pace-maker for ALL Claude Code sessions across all projects

```bash
cd ~/Dev/claude-pace-maker
./install.sh
```

**Registers hooks in**: `~/.claude/settings.json`

**Installed hooks**:
- `SessionStart` → session-start.sh (intent validation guidance)
- `UserPromptSubmit` → user-prompt-submit.sh (CLI commands)
- `PreToolUse` → pre-tool-use.sh (intent validation, TDD enforcement)
- `PostToolUse` → post-tool-use.sh (credit throttling, subagent reminders)
- `Stop` → stop.sh (session completion validation)
- `SubagentStart` → subagent-start.sh (context tracking)
- `SubagentStop` → subagent-stop.sh (context tracking)

**This is the recommended installation mode.** All pace-maker features work globally.

### Project Installation (For Project-Specific Overrides)

**Use case**: Override or extend global hooks for a specific project

```bash
cd ~/Dev/claude-pace-maker
./install.sh /path/to/your/project
```

**Registers hooks in**: `/path/to/your/project/.claude/settings.json`

**What it does**:
1. Detects existing hooks (like tdd-guard)
2. Removes ONLY pace-maker hooks (preserves other tools)
3. Adds pace-maker hooks back
4. **Result**: Your project has BOTH pace-maker AND other tools' hooks

### When to Use Project Installation

Use project installation when:
- You need to combine pace-maker with project-specific tools (tdd-guard, etc.)
- You want different hook behavior in a specific project
- The project already has hooks that would be overridden by global settings

**Important**: Due to Claude Code's override behavior, project settings must include ALL hooks you want for that project, not just the project-specific ones.

---

## Combining Pace-Maker with Other Hook Tools

If a project has its own hooks (like tdd-guard), you need to include BOTH in the project's settings.json:

**Project settings** (`<project>/.claude/settings.json`):
```json
{
  "hooks": {
    "PreToolUse": [
      {"matcher": "Write|Edit", "hooks": [{"type": "command", "command": "tdd-guard"}]},
      {"matcher": "Write|Edit", "hooks": [{"type": "command", "command": "~/.claude/hooks/pre-tool-use.sh", "timeout": 120}]}
    ],
    "PostToolUse": [
      {"hooks": [{"type": "command", "command": "~/.claude/hooks/post-tool-use.sh", "timeout": 360}]}
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {"type": "command", "command": "tdd-guard"},
          {"type": "command", "command": "~/.claude/hooks/user-prompt-submit.sh"}
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "startup|resume|clear",
        "hooks": [
          {"type": "command", "command": "tdd-guard"},
          {"type": "command", "command": "~/.claude/hooks/session-start.sh"}
        ]
      }
    ],
    "Stop": [
      {"hooks": [{"type": "command", "command": "~/.claude/hooks/stop.sh", "timeout": 120}]}
    ],
    "SubagentStart": [
      {"hooks": [{"type": "command", "command": "~/.claude/hooks/subagent-start.sh"}]}
    ],
    "SubagentStop": [
      {"hooks": [{"type": "command", "command": "~/.claude/hooks/subagent-stop.sh"}]}
    ]
  }
}
```

---

## Troubleshooting Double-Firing Hooks

### Symptom
A hook (like post-tool-use.sh) fires twice on every tool execution.

### Root Cause
The same hook is registered in BOTH global and project settings.

### Diagnosis
```bash
# Check global settings
cat ~/.claude/settings.json | jq '.hooks.PostToolUse'

# Check project settings
cat <project>/.claude/settings.json | jq '.hooks.PostToolUse'
```

If BOTH show the hook, you have a conflict.

### Fix

**Option 1: Remove from project** (if global installation is sufficient)
```bash
# Edit project settings
nano <project>/.claude/settings.json

# Remove the duplicate hook entry
```

**Option 2: Use pace-maker's install script** (Automatic fix)
```bash
cd ~/Dev/claude-pace-maker

# Reinstall - it will detect and prevent duplicates
./install.sh /path/to/your/project
```

---

## Verification

After installation, verify hooks are registered correctly:

```bash
# Global hooks
cat ~/.claude/settings.json | jq '.hooks'

# Project hooks (if applicable)
cat <project>/.claude/settings.json | jq '.hooks'
```

**Expected**:
- Each hook appears EXACTLY ONCE per settings file
- If using project settings, they include ALL hooks (global + project-specific) for hook types you want to override

---

## How install.sh Handles Merging

The `install.sh` script uses a sophisticated three-step jq filter:

1. **Remove pace-maker commands** from within hook entries (not entire entries)
2. **Remove empty entries** after pace-maker removal
3. **Add pace-maker back** as separate entries

This ensures:
- ✅ Existing hooks (like tdd-guard) are preserved
- ✅ No duplicates when re-running install
- ✅ Pace-maker hooks are cleanly separated
- ✅ Matchers and other configurations are maintained

See `install.sh:register_hooks()` for implementation details.

---

## Feature Control

All pace-maker features can be toggled without changing hook registration:

```bash
pace-maker on|off                    # Master switch - enable/disable ALL hooks
pace-maker intent-validation on|off  # Enable/disable pre-tool validation
pace-maker tempo on|off              # Enable/disable session lifecycle
pace-maker reminder on|off           # Enable/disable subagent reminders
pace-maker 5-hour-limit on|off       # Enable/disable 5-hour throttling
pace-maker weekly-limit on|off       # Enable/disable 7-day throttling
```

This allows you to install hooks globally but selectively disable features as needed.
