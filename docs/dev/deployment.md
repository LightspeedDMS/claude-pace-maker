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
| 300 s | 300 s | 360 s | 10 s | none set (harness default) |

Stop and PreToolUse were doubled/raised together with the reviewer CLI ceiling in #165 (Stop 120 → 300, PreToolUse 180 → 300, ceiling 120 → 240).

- **Four places must agree** on each of the Stop and PreToolUse numbers: the constant in `constants.py` (`STOP_HOOK_TIMEOUT_SECONDS` / `PRE_TOOL_HOOK_TIMEOUT_SECONDS`), `install.sh`, `hooks/hooks.json`, and the live `~/.claude/settings.json` (written by `install.sh`). `tests/unit/test_pretool_budget.py` and `tests/unit/test_issue_165_reviewer_timeout.py` check the first three; the live file is only fixed by running `./install.sh`.
- **Raise settings.json first.** A PreToolUse hook killed by the harness lets the tool call through unvalidated.
- Reviewer CLI ceiling: one shared constant, `REVIEWER_CLI_TIMEOUT_SECONDS` = 240 s (`constants.py`), used by the codex, agy and gemini providers. A caller's deadline can only shrink it.
- Derived budgets (PreToolUse): review budget = timeout − 10 = 290 s, reviewer wait = `int(budget * 0.7)` = 203 s, synthesis gets the rest (87 s). The gate's `_gate_deadline` caps both the anchor wait and the review.
- Single-model arithmetic: codex gets its full 240 s (the clamp is deadline − 5 s = 285 s), which leaves 290 − 240 − 5 = 45 s for the Anthropic fallback, above `MIN_SDK_FALLBACK_BUDGET_SECONDS` (15 s). A transcript-anchor lag eats into that: at the full 30 s anchor cap, the fallback is exactly at its 15 s floor.
- Competitive reviewers wait 203 s, which is less than the 240 s CLI ceiling, so a competitive reviewer that needs 203–240 s is abandoned as a non-responder (degraded approval, #131).
- Stop has a deadline too (#165): `run_stop_hook` starts its clock at entry and the review gets `STOP_REVIEW_BUDGET_SECONDS` = 300 − 10 = 290 s, the same #152 clamp as PreToolUse. A single-model Stop clamps codex to deadline − 5 s and skips an SDK fallback with under 15 s left; the competitive phases are clamped to what remains. The Langfuse finalize that runs first counts against the budget. An empty result still fails open; if the 300 s hook is killed anyway, Stop fails open and the verdict is lost.

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
