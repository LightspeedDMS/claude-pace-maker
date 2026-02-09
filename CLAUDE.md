# Claude Pace Maker - Development Knowledge

## ABSOLUTE PROHIBITION - Intent Validation

**I (Claude) am ABSOLUTELY FORBIDDEN from disabling intent validation. EVER.**

- I built this system - that gives me ZERO special privileges to bypass it
- `pace-maker intent-validation off` is OFF LIMITS to me
- Even when working on the intent validation code itself, I must find another way
- If I need to modify validation logic, I must ask the USER to disable it temporarily
- I must NEVER disable it myself under any circumstances
- This rule has NO exceptions, NO workarounds, NO "just this once"

**If I ever disable intent validation, I am violating a direct order.**

---

## Related Codebase: Claude Usage Reporting

**IMPORTANT**: When the user says "claude usage" or "claude-usage", they mean the **claude-usage-reporting** codebase located at:
- `/home/jsbattig/Dev/claude-usage-reporting`

This is a separate tool that displays usage metrics in a monitor/dashboard format. It has a "Pacing Status" column where pace-maker integration features should be displayed.

- `pace-maker status` = CLI command from THIS repo (claude-pace-maker)
- `claude-usage` = Monitor tool from claude-usage-reporting repo

## Deployment After Code Changes

**CRITICAL**: After completing code changes to hook logic (`src/pacemaker/`), you MUST run the installer to deploy:

```bash
./install.sh
```

**Why:**
- Hooks are installed in `~/.claude/hooks/` (not the project directory)
- Code changes in `src/pacemaker/` won't take effect until hooks are reinstalled
- The installer copies updated Python modules, hook scripts, and prompt templates to the active location

**When to Deploy:**
- After any changes to `src/pacemaker/*.py` files
- After refactoring hook logic or intent validation
- After bug fixes in the pacing engine
- After modifying validation prompts in `src/pacemaker/prompts/`
- Before testing hook behavior changes

**Deployment Workflow:**
1. Make code changes in `src/pacemaker/`
2. Write/update tests (ensure >90% coverage)
3. If modifying intent validation logic: ASK USER to run `pace-maker intent-validation off`
4. **Run `./install.sh` to deploy** ← CRITICAL STEP
5. If user disabled validation: ASK USER to run `pace-maker intent-validation on`
6. Test the deployed hooks with manual verification

**NOTE**: Claude must NEVER disable intent validation directly. Only the user can do this.

Without running the installer, your code changes remain undeployed and inactive.

## Intent Validation Development

**Bootstrapping Problem**: When modifying intent validation code while validation is enabled, you create a circular dependency where the validator blocks changes to itself.

**Solution**: The USER (not Claude) must temporarily disable intent validation:

```bash
# USER runs this command (Claude must NEVER run this):
pace-maker intent-validation off

# Claude makes changes to:
# - src/pacemaker/intent_validator.py
# - src/pacemaker/prompts/pre_tool_use/*.md
# - src/pacemaker/hook.py (pre-tool validation logic)

# Deploy changes
./install.sh

# USER re-enables validation:
pace-maker intent-validation on

# Test that validation works correctly
```

**CRITICAL**: Claude must ASK the user to disable validation. Claude must NEVER disable it directly. See the ABSOLUTE PROHIBITION section at the top of this file.

This applies to:
- Intent validation Python code (`intent_validator.py`, `hook.py`)
- Validation prompt templates (`prompts/pre_tool_use/`)
- Clean code rules and core paths configuration
- Any code that affects the pre-tool validation hook
