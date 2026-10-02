# Session Features: Cross-Session Awareness, Provenance Tagging, Memory Localization

Read the matching section before changing `session_registry/`, `prompt_provenance.py`, any text pace-maker emits to Claude, or `memory_localization/`.

## Cross-Session Awareness (#64)

- `~/.claude-pace-maker/session_registry.db` (WAL, 2 s busy timeout; `PACEMAKER_SESSION_REGISTRY_PATH`).
- The workspace key is `git rev-parse --show-toplevel`, falling back to the realpath of cwd. Subagents reuse their parent's session_id.
- Rows idle for 20 minutes are purged. CLI: `pace-maker sessions list`.
- **CSA state in `state.json` must be keyed by session_id** (`_csa._get_cs(state, session_id)`). A flat block leaks one workspace's siblings into another. `on_session_end` removes the session's entry.
- **Every registry write goes through a `_csa.*` entry point gated by `_csa._is_enabled()`** (master `enabled` AND `cross_session_awareness_enabled`), including PostToolUse's `record_action` / heartbeat (`_csa.on_post_tool_use_record_action`, #97, v2.34.6). **Hook code must never call `session_registry.registry` directly** — that bypass was #97.
- Nudges (`session_registry/nudges.py`):
  - the SessionStart banner
  - the SubagentStart banner (via `additionalContext`)
  - a reminder every 5th PreToolUse
  - a warning injected into danger-bash Phase 2
- Hook integration: `session_registry/_csa.py`.

## Provenance Tagging (#101)

- `prompt_provenance.py` is a stdlib-only leaf.
  - `format_tag(body, event)` renders `[pace-maker · <event>]` with U+00B7 and is idempotent. `event` must be in `CHANNELS`.
  - `format_reviewer_relay(body, model)` wraps relayed reviewer output with an advisory header.
- **Every new emission must use a channel from `CHANNELS`.** Add new ones there; the manifest lists them all and tests check every emission site.
- The SessionStart and SubagentStart manifests are always emitted. `NEVER_LIST` is enforced only by Claude's reasoning; there is no detection code. It covers:
  - no exfiltration
  - no self-disabling of checks
  - no concealment
  - no impersonating the user
- The Claude-facing `reason` / `feedback` is tagged. `raw_feedback` and governance `feedback_text` stay **untagged**, so the monitor's `[label]` display never gets nested brackets.
- Danger-bash Phase 2 nests the reviewer relay inside an outer `danger_bash_block` tag.

## Memory Localization (#65)

- **Flow A** (SessionStart, `link_if_local_exists`): when `<git root>/.claude-memory/` exists, `~/.claude/projects/<enc>/memory/` is replaced by a symlink to it. The local folder always wins.
- **Flow B** (`pace-maker localize-memory`): seed the local folder from central, then link.
- **Flow C** (`pace-maker memory-localization unlink`): copy the local folder back to central and remove the link.
- Config gate: `memory_localization_enabled` (default true).
- Safety: `assert_safe_to_destroy()` requires the path to be under `CENTRAL_BASE` and named `memory`. The replace goes through a `.bak_localize` rename and is rolled back on `OSError`.
- Path discovery never re-implements Claude Code's project-dir encoding: Flow A uses `Path(transcript_path).parent / "memory"`; Flows B/C find the project by scanning `~/.claude/projects/*/*.jsonl` for the cwd.
- Tests need `PACEMAKER_CENTRAL_BASE`, which is resolved on each access.
- Code: `src/pacemaker/memory_localization/core.py`, `memory_localization_cli.py`.
