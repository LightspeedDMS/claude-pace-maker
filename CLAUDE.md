# Claude Pace Maker - Development Knowledge

## Deployment After Code Changes

**CRITICAL**: After completing code changes to hook logic (`src/pacemaker/`), you MUST run the installer to deploy:

```bash
./install.sh
```

**Why:**
- Hooks are installed in `~/.claude/hooks/` (not the project directory)
- Code changes in `src/pacemaker/` won't take effect until hooks are reinstalled
- The installer copies updated Python modules and hook scripts to the active location

**When to Deploy:**
- After any changes to `src/pacemaker/*.py` files
- After refactoring hook logic or intent validation
- After bug fixes in the pacing engine
- Before testing hook behavior changes

**Don't Forget:**
1. Make code changes in `src/pacemaker/`
2. Write/update tests
3. Commit changes to git
4. **Run `./install.sh` to deploy** ← CRITICAL STEP
5. Test the deployed hooks

Without running the installer, your code changes remain undeployed and inactive.
