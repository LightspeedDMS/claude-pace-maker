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

## Version Bumping

**When bumping the version**, ALWAYS update BOTH files:
- `src/pacemaker/__init__.py` — the Python package version
- `.claude-plugin/plugin.json` — the Claude Code plugin manifest version

These MUST always match. Forgetting `plugin.json` has happened before.

---

## Danger Bash Validation

**Two-phase validation** for dangerous Bash commands in the PreToolUse hook:

- **Phase 1 (Regex Gate)**: When a Bash tool call matches any of the 55 default danger rules and the message contains no `INTENT:` declaration, the command is blocked immediately with no LLM call. This is a fast-reject path.
- **Phase 2 (LLM Validation)**: When `INTENT:` is present, the LLM validates that the declared intent aligns with the actual Bash command being executed (same Stage 2 flow as Write/Edit).

**Rule categories**: 25 Work Destruction (WD) rules (git checkout --, git reset --hard, git stash drop, branch deletion, etc.) and 30 System Destruction (SD) rules (rm -rf, kill -9, chmod 777, mkfs, dd, etc.).

**Configuration**: Rules are customizable via `~/.claude-pace-maker/danger_bash_rules.yaml` using the same merge strategy as clean code rules (user config stores only additions and deletion markers, defaults loaded from bundled YAML at runtime).

**Blockage category**: `intent_validation_dangerbash` with label "Danger Bash" in telemetry and blockage stats.

**Key files**:
- `src/pacemaker/danger_bash_rules_default.yaml` — 55 bundled default rules
- `src/pacemaker/danger_bash_rules.py` — loader, merger, matcher module
- `src/pacemaker/hook.py` line ~2149 — PreToolUse Bash tool handling

---

## Reviewer Identity Tracking

When intent validation runs Stage 2 (LLM code review), the reviewer identity is tracked end-to-end:

1. `resolve_and_call_with_reviewer()` in `inference/registry.py` returns `(response, reviewer_name)` tuple
2. `intent_validator.py` threads reviewer through validation result dict (`"reviewer": reviewer`)
3. `hook.py` records reviewer in blockage_events details JSON
4. Governance event `feedback_text` is prefixed with `[REVIEWER:xxx]` tag (e.g., `[REVIEWER:codex-gpt5]`, `[REVIEWER:anthropic-sdk]`, `[REVIEWER:gemini]`)

The reviewer tag enables the claude-usage monitor to display colored reviewer identity in the governance event feed.

---

## Codex PAYG Billing Handling

The `codex_usage.py` module handles both subscription and PAYG (Pay-As-You-Go) Codex billing:

- **PAYG detection**: When Codex CLI returns `limit_id: "premium"` in rate limit headers, the plan is identified as PAYG
- **Null handling**: `_parse_last_token_count()` gracefully handles null `primary`/`secondary` fields that Codex returns for PAYG billing (no usage percentages available)
- **`limit_id` column**: Added to `codex_usage` SQLite table via idempotent `ALTER TABLE` migration in `migrate_codex_usage_schema()`
- **SubagentStop wiring**: Migration is called in `hook.py` SubagentStop handler before writing codex usage data

---

## Running Tests

**NEVER run tests as a single pytest process** (`python -m pytest tests/`). SQLite WAL contention causes hangs when multiple test files create DBs concurrently in the same process.

**Always use the independent test runner:**

```bash
./scripts/run_tests.sh          # Run all tests (each file independently)
./scripts/run_tests.sh --quick  # Skip slow e2e tests
./scripts/run_tests.sh --tb     # Show failure tracebacks
```

**Why:** Each test file gets its own pytest process with a 30s timeout, avoiding WAL lock contention between concurrent DB teardown/setup cycles.

**Test mode optimization:** `PACEMAKER_TEST_MODE=1` is set automatically by `conftest.py`, enabling `PRAGMA synchronous=OFF` for 20x faster DB operations in tests.

---

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
