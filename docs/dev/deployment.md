# Deployment, Versioning, Hook Timeouts, Claude Code Compatibility

Read this before deploying, bumping the version, changing hook timeouts, or adapting to a Claude Code change.

## Deployment

- **Hooks import `pacemaker` from the installed snapshot in `~/.claude/hooks/`, not from `src/`** (#146). Code goes live only after `./install.sh`. Run it after any change under `src/pacemaker/` (prompts included).
- Hook templates are `src/hooks/*.sh`. In the non-pipx branch they:
  - set `HOOK_SCRIPT_DIR` from `readlink -f "$0"` and export `PYTHONPATH="$HOOK_SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"`;
  - export `PYTHONSAFEPATH=1` so `python -m` doesn't put cwd first on `sys.path` (honoured on 3.11+, ignored on 3.10, so 3.10 still has the cwd-shadowing hazard);
  - when `$HOOK_SCRIPT_DIR/pacemaker/__init__.py` is missing, log to `~/.claude-pace-maker/hook_debug.log` and `exit 0` instead of silently importing another copy.
- `PACEMAKER_DEV_LIVE_SRC=1` is the explicit opt-in to import from the Dev `src/`. It is never the default.
- A hand-made symlink from `~/.claude/hooks/*.sh` to `src/hooks/*.sh` makes every hook skip. `install.sh` always copies.
- Places that can still resolve to the Dev `src/`, none of them hook processes:
  1. the claude-usage monitor's dynamic import via `~/.claude-pace-maker/install_source` (deliberate, unchanged by #146);
  2. an editable install (`pip install -e .`): its `.pth` puts `src/` on every interpreter's `sys.path` at startup, which `PYTHONSAFEPATH` does not stop. `PYTHONPATH` still wins, but it would silently serve `import pacemaker` if the snapshot were missing — which is why the missing-snapshot guard must `exit 0`, never fall through. Not pace-maker's to remove (none was present on 2026-10-01);
  3. `PACEMAKER_DEV_LIVE_SRC`.

  The plugin entry `scripts/hook.sh` imports from `$CLAUDE_PLUGIN_ROOT/src` by design; only sideloading the plugin from this Dev clone recreates the live-edit hazard. If it ever needs the #146 treatment, do it as its own change.
- `install.sh` copies from the directory it runs in and records that path in `install_source`. Run from this Dev clone, it deploys the working tree as it is, including other sessions' uncommitted work.
- `install.sh` copies each subpackage with `_copy_subdir`. **A new `src/pacemaker/<pkg>/` must be added there**, which `tests/test_intent_mcp_registration.py::TestInstallScriptWiring` enforces.

### Changing validation code
1. Ask the USER to run `pace-maker intent-validation off`. Claude never runs it.
2. Make the change and run its tests.
3. Run `./install.sh`.
4. Ask the USER to run `pace-maker intent-validation on`.
5. Verify the deployed hooks manually.

## Version bumping

Bump all three together: `src/pacemaker/__init__.py`, `.claude-plugin/plugin.json`, `pyproject.toml`. `tests/test_plugin_hooks_config.py` checks only plugin.json against pyproject.toml. **`__init__.py` is unchecked, so verify it by hand.**

## Hook timeouts (settings.json)

| Stop | PreToolUse | PostToolUse | SessionStart / SubagentStart / SubagentStop | UserPromptSubmit |
|---|---|---|---|---|
| 120 s | 180 s | 360 s | 10 s | none set (harness default) |

- `PRE_TOOL_HOOK_TIMEOUT_SECONDS` (`constants.py`) must match `install.sh`, `hooks/hooks.json` and the live `~/.claude/settings.json`. `tests/unit/test_pretool_budget.py` checks the first three.
- **Raise settings.json first.** A PreToolUse hook killed by the harness lets the tool call through unvalidated.
- Derived budgets: review budget = timeout − 10, reviewer wait = `int(budget * 0.7)` (118 s, because float rounding makes it 118.999…), synthesis gets the rest. The gate's `_gate_deadline` caps both the anchor wait and the review.

## Claude Code compatibility

- **Adapt to breaking changes and support both old and new behavior. Do not raise `min_claude_version`** unless the old behavior truly cannot be supported. For each shim: add a row below, put a comment naming the Claude Code version in the code, and test both behaviors.
- Minimum supported version is **2.1.39**, and it is enforced:
  - `version_check.perform_session_start_version_check()` runs at SessionStart and sets `state["version_block_active"]` and `version_block_message`.
  - The message is printed to stdout, wrapped in the `version_block_notice` tag, because stdout is the only channel SessionStart reliably surfaces.
  - Only Stop and PreToolUse check the flag; they return `{"continue": True}` before reading stdin. Every other hook stays unguarded by design.
  - Any probe failure fails open and clears `version_block_message`.
  - Config `min_claude_version` overrides `_FALLBACK_MIN_VERSION`.
  - Status lives in `version_status.db` (`PACEMAKER_VERSION_STATUS_PATH`).
- **There is no `pace-maker min-claude-version` CLI and no "Claude Code:" status line.** Don't document either unless you implement it.

| CC version | Change | Adaptation |
|---|---|---|
| 2.1.39 | Subagent transcripts moved to `<project>/<session-id>/subagents/agent-*.jsonl` | Hook globs both the old and new locations (`tests/unit/test_subagent_transcript_path.py`) |
