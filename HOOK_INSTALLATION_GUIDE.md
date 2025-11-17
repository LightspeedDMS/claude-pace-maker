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

## Correct Installation Pattern

### Global Installation (Rare)

**Use case**: Hooks you want active in ALL Claude Code sessions (even non-project work)

```bash
cd ~/Dev/claude-pace-maker
./install.sh
```

**Registers hooks in**: `~/.claude/settings.json`

**Installed hooks**:
- `SessionStart` → session-start.sh
- `UserPromptSubmit` → user-prompt-submit.sh
- `PostToolUse` → post-tool-use.sh (**Don't do this!** See below)
- `Stop` → stop.sh

**WARNING**: **DO NOT install PostToolUse globally!** Pace-maker's throttling should be project-specific, not global.

### Project Installation (Recommended)

**Use case**: Enable pace-maker for a specific project

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

### Mixed Installation (Most Common)

**Recommended setup**:

1. **Global settings** (`~/.claude/settings.json`):
   - Lightweight hooks you want everywhere
   - Do NOT include PostToolUse (too slow for all projects)

   ```json
   {
     "hooks": {
       "SessionStart": [
         {"hooks": [{"type": "command", "command": "~/.claude/hooks/session-start.sh"}]}
       ],
       "Stop": [
         {"hooks": [{"type": "command", "command": "~/.claude/hooks/stop.sh"}]}
       ],
       "UserPromptSubmit": [
         {"hooks": [{"type": "command", "command": "~/.claude/hooks/user-prompt-submit.sh"}]}
       ]
     }
   }
   ```

2. **Project settings** (`<project>/.claude/settings.json`):
   - Include ALL hooks (global + project-specific)
   - This is required due to Claude Code's override behavior

   ```json
   {
     "hooks": {
       "PreToolUse": [
         {"matcher": "Write|Edit", "hooks": [{"type": "command", "command": "tdd-guard"}]}
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
         {"hooks": [{"type": "command", "command": "~/.claude/hooks/stop.sh"}]}
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

**Option 1: Remove from global** (Recommended)
```bash
# Edit global settings
nano ~/.claude/settings.json

# Remove PostToolUse entirely from global (it should be project-specific)
```

**Option 2: Remove from project**
```bash
# Edit project settings
nano <project>/.claude/settings.json

# Remove PostToolUse hook - but you'll lose pace-maker throttling for this project
```

**Option 3: Use pace-maker's install script** (Automatic fix)
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

# Project hooks
cat <project>/.claude/settings.json | jq '.hooks'
```

**Expected**:
- Each hook appears EXACTLY ONCE per settings file
- Project settings include ALL hooks (global + project-specific) for hook types you want to override
- No duplicate pace-maker hooks across global and project

---

## Advanced: How install.sh Handles Merging

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
