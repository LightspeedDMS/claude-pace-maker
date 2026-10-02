# Langfuse, Shared State, and Secret Masking

Read this before changing `langfuse/`, `secrets/`, `hook.save_state`, or the SubagentStart/SubagentStop/PostToolUse trace paths.

## Trace attribution and finalization

- **Attribution comes only from the hook payload's `agent_id`** (#158):
  - If present, use `langfuse_state/subagent-<agent_id>.json`.
  - If that state file is missing, skip the trace-bound work (still collect `🔐` secrets and flush the parent's `pending_trace`).
  - If `agent_id` is absent, use the session's main trace. (A Claude Code that omits `agent_id` on subagent PostToolUse therefore lands in the same session's main trace — safe, never cross-session.)
  - **Never use the global `state.json` for attribution**, not even as a heuristic to "recover" such calls: that was the bug.
- **Finalization (#161)** reads the agent's own state file (`langfuse.state.read_subagent_trace_info`). The `subagent_traces` map is retired; don't re-add it.
  - The trace must belong to the payload's session: trace ids are `<parent_session_id>-subagent-<agent_type>-<uuid8>` (`orchestrator.handle_subagent_start`), so one that doesn't start with `<payload session_id>-subagent-` is never finalized. A payload without `session_id` finalizes nothing.
  - The legacy `current_subagent_*` slot in `state.json` (global across sessions) is claimed by ONE rule, with or without a payload `agent_id`: the slot's trace must start with `<payload session_id>-subagent-`, and when the payload has an `agent_id` the slot's `agent_id` must equal it. A payload with no `session_id` (including empty stdin) never claims the slot.
- **Every state id comes from the hook payload and becomes a file name**, so ONE allowlist guards all of them: `StateManager.read` / `create_or_update` refuse any id that fails `is_safe_state_id` (return None / False and log a warning). The rule is `is_safe_agent_id`: 1-128 chars of ASCII-or-Unicode letters and digits, `-`, `_`; a non-string is refused. It applies to session ids too, not only `subagent-<agent_id>` (for those it applies to the `<agent_id>` part). Without that, a parent id like `../config` overwrote `~/.claude-pace-maker/config.json`. Real Claude Code session UUIDs and `subagent-<hex>` ids pass.
  - SubagentStart (`orchestrator.handle_subagent_start`) checks the same rule before it pushes a trace, so an unsafe id gets no trace, no state file and no legacy slot.
  - PostToolUse and SubagentStop reach the file only through `StateManager`, so an unsafe id is an "unregistered subagent" there: no span, nothing finalized. Start and stop therefore always agree on which ids are valid. Add new per-agent state access through `StateManager`, never by building the path yourself.
- User-prompt traces are kept as `pending_trace` and pushed later, because secrets are declared after the prompt. The flush happens in PostToolUse, Stop and SubagentStop.

## state.json

- **`state.json` is one file shared by every session on the machine.**
- `hook.save_state` and `StateManager.create_or_update` are atomic via `atomic_file.atomic_write_text` (unique temp file plus `os.replace`, mode preserved). Serialize before touching disk.
  - A symlinked target is resolved with `os.path.realpath`: the file behind the link is replaced and the link is kept, like the old `open(path, "w")` wrote through it.
  - **No fsync, by decision** (hook latency): readers never see a partial file, but a power loss can lose the last write.
  - A process killed (SIGKILL, power loss) between creating the temp file and the rename leaves an orphan `<name>.tmp.<pid>.<rand>` file. It is never read, and nothing cleans it up.
- After any slow step, re-load right before editing and saving.
- **Known remaining risk (#162): atomic writes stop torn files, not lost updates.** There is no lock, and every hook still loads `state.json`, edits it and saves the whole file, so the remaining global fields (counters, `in_subagent`, the legacy `current_subagent_*` slot, cross-session data) can lose updates from concurrent sessions. The worst case is PostToolUse, which saves a snapshot loaded before a pacing delay that can last minutes (about 6 minutes in the observed case), overwriting whatever other sessions wrote meanwhile. #161 only made per-agent *finalization* independent of this file. Don't rely on any `state.json` field being exact under concurrency. The fix belongs in #162 (per-session state files or a re-load-before-save), not a global lock.

## Secret masking (`secrets/masking.py`)

- `build_prefiltered_pattern()` keeps only the secrets that occur in the payload. `sanitize_trace` and `_mask_reviewer_prompt` share it. Without it, compiling all ~765 secrets (whole files included) cost about 7 s per hook (#157). Output is identical to full-store masking by construction. Langfuse masking has no minimum secret length (only reviewer-prompt masking skips secrets under 8 chars).
- `SecretsMasker` works in two passes:
  - Secrets of 8+ chars are masked anywhere, longest first.
  - Shorter ones are masked only as standalone tokens (`\b`-style edges, ASCII word class), and only in the text between pass-1 matches, so a long secret can never survive.
- `is_degenerate_secret()` covers empty values and fragments of `*** MASKED ***`: `create_secret` refuses them, `get_all_secrets` excludes them, `pace-maker secrets list` flags them. **Code never deletes stored rows**; pruning is up to the user.
- Log with `log_warning("secrets", …)`, never stdlib `logging`, and never the value.

## Hook time budget and cleanup

- **SubagentStart and SubagentStop bound their Langfuse step to 5 s** (`SUBAGENT_HOOK_LANGFUSE_BUDGET_SECONDS`, `bounded_call.run_with_deadline`, daemon thread). Guidance output never waits on Langfuse. PostToolUse is not bounded.
- A `WARNING ... exceeded its 5.0s budget` log line means the trace was skipped, not that the hook failed.
- `subagent_context.read_subagent_start_context()` reads bounded head and tail windows instead of parsing the parent transcript three times:
  - The tail decides the prompt and model, and the latest entry wins.
  - The head may answer only the user id (legacy `auth_profile`) and a `session_start` model, never the prompt or the "last model".
  - If the tail has no answer, one streaming pass over the whole file fills it in.
- **Late guidance**: SubagentStart records its completion as its *last* step. If a subagent's PostToolUse finds no completion record, it delivers the guidance once.
  - It does a cheap read-only check first, then `claim_late_guidance` right before printing.
  - The table (`subagent_guidance`) lives in `intent_declarations.db` with a 24 h TTL. Don't move it to `session_registry.db`: that DB is gated by `cross_session_awareness_enabled`, is a live monitor contract, and purges after 20 min. It is not governed by `intent_declaration_tool_enabled`.
  - Only subagent calls (payload `agent_id`) are considered; the main thread is never touched. The CSA sibling banner is not part of the late text.
  - Known limits: delivery waits for the first call that actually runs, and there is a millisecond window that is lost.
  - Log evidence: an INFO line `delivering subagent guidance late` means SubagentStart did not complete and PostToolUse covered for it.
- Cleanup: subagent state files are kept 2 days, session files 7 days, at most once every 24 h (`maybe_cleanup_stale_files`, `.last_cleanup`).

## Troubleshooting

- Logs: `~/.claude-pace-maker/pace-maker-YYYY-MM-DD.log`. Hook crashes: `~/.claude-pace-maker/hook_debug.log`.
- Query the API with the pace-maker project keys from `config.json` against `langfuse_base_url`.
