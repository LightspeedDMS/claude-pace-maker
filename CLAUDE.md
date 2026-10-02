# Claude Pace Maker — Agent Index

This file is deliberately slim: the rules that always apply, plus links to the documentation you must read **before** touching a given area. The detailed knowledge lives in `docs/dev/`. The history behind each rule is in its GitHub issue and in `git log -p CLAUDE.md`.

**Keeping it slim:** put new knowledge in the matching `docs/dev/*.md` file, or create one, and add a row to the index below. Add a line here only when the rule must be in context in every session. Cite functions, not line numbers.

---

## Always-on rules

1. **Claude NEVER disables intent validation** (`pace-maker intent-validation off`), not even when working on the validator. Ask the USER to toggle it. No exceptions.
2. **Never run tests as a single pytest process.** Use `./scripts/run_tests.sh` (`--quick`, `--tb`). Unit tests must never make real codex/gemini/claude calls.
3. **Do not write new scripted E2E tests.** E2E here means Claude runs the hooks/CLI and reports the output it observed.
4. **Code goes live only after `./install.sh`.** Hooks import from the installed snapshot in `~/.claude/hooks/`, not from `src/`.
5. **Version bump = three files:** `src/pacemaker/__init__.py`, `.claude-plugin/plugin.json`, `pyproject.toml`. No test checks `__init__.py`.
6. **Changing `database.py` `SCHEMA` means bumping `SCHEMA_VERSION`** and updating the pinned hash test. Schema changes are additive only, because the claude-usage monitor reads these DBs.
7. **Claude Code breaking changes:** add a shim that supports both old and new behavior. Never raise `min_claude_version` (floor: 2.1.39).
8. **All text pace-maker emits to Claude is tagged** via `prompt_provenance.format_tag()` with a channel from `CHANNELS`.
9. "claude usage" / "claude-usage" means the separate repo `/home/jsbattig/Dev/claude-usage-reporting` (the monitor).

---

## Read before touching

| If you are working on… | Read first |
|---|---|
| Running or writing tests, test isolation, E2E policy | [docs/dev/testing.md](docs/dev/testing.md) |
| Deploying, version bumps, hook timeouts, Claude Code compatibility / version gate | [docs/dev/deployment.md](docs/dev/deployment.md) |
| PreToolUse Write/Edit or Bash gates, transcript anchoring, core paths, no-visible-text, the Opus reasoning-summary exception, danger-bash rules | [docs/dev/intent-validation.md](docs/dev/intent-validation.md) |
| Stage 2 review templates, what the reviewer sees (diff, surrounding context, sibling edits), reviewer-prompt secret masking | [docs/dev/stage2-review-prompts.md](docs/dev/stage2-review-prompts.md) |
| The `declare_intent` MCP tool, its store, hints, registration and permissions | [docs/dev/declare-intent-tool.md](docs/dev/declare-intent-tool.md) |
| Reviewer providers (codex / agy / gemini / anthropic), `hook_model`, deadlines, competitive review, verdict parsing, Stop-hook prompt | [docs/dev/reviewer-pipeline.md](docs/dev/reviewer-pipeline.md) |
| Langfuse traces, `state.json`, secret masking, subagent hook time budget, late guidance | [docs/dev/langfuse.md](docs/dev/langfuse.md) |
| Any DB table, column or file the claude-usage monitor reads | [docs/dev/claude-usage-contract.md](docs/dev/claude-usage-contract.md) |
| Cross-session awareness, provenance tagging, memory localization | [docs/dev/session-features.md](docs/dev/session-features.md) |
| Overall architecture, pacing algorithms | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/WEEKEND_AWARE_ALGORITHM.md](docs/WEEKEND_AWARE_ALGORITHM.md), [docs/adaptive_throttle_implementation.md](docs/adaptive_throttle_implementation.md), [docs/PRELOAD_SYSTEM.md](docs/PRELOAD_SYSTEM.md) |
