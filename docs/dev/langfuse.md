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
  - **#167:** `hook.get_transcript_path(session_id)` (builds `~/.claude/projects/<dir>/<session_id>.jsonl`, and its result becomes SubagentStop's parent transcript path) imports and uses the same `is_safe_state_id`, never a copy. An unsafe id gives `None` plus a warning that logs only the id's type. The old `langfuse/subagent.py` (unchecked `Path(state_dir)/f"{session_id}.json"`, imported by nothing in `src/`) was deleted with its tests.
  - PostToolUse and SubagentStop reach the file only through `StateManager`, so an unsafe id is an "unregistered subagent" there: no span, nothing finalized. Start and stop therefore always agree on which ids are valid. Add new per-agent state access through `StateManager`, never by building the path yourself.
- User-prompt traces are kept as `pending_trace` and pushed later, because secrets are declared after the prompt. The flush happens in PostToolUse, Stop and SubagentStop.

## state.json

- **`state.json` is one file shared by every session on the machine.**
- `hook.save_state` and `StateManager.create_or_update` are atomic via `atomic_file.atomic_write_text` (unique temp file plus `os.replace`, mode preserved). Serialize before touching disk.
  - A symlinked target is resolved with `os.path.realpath`: the file behind the link is replaced and the link is kept, like the old `open(path, "w")` wrote through it.
  - **No fsync, by decision** (hook latency): readers never see a partial file, but a power loss can lose the last write.
  - A process killed (SIGKILL, power loss) between creating the temp file and the rename leaves an orphan `<name>.tmp.<pid>.<rand>` file. It is never read, and nothing cleans it up.
- **Reload before save, own changes only (#161, #162).** Atomic writes stop torn files, not lost updates: a hook that loads `state.json`, runs a slow step and saves its old copy erases what other sessions wrote meanwhile. After any slow step, re-load right before editing and saving, and change only the fields the hook owns. `hook.update_state(mutate, path)` is the primitive (load latest, apply the mutation, save, return the saved state).
  - **PostToolUse** (`run_hook`) applies `tool_execution_count` + 1 and the `consecutive_stop_blocks` reset through `update_state` *before* the pacing poll and the throttle sleep (up to 350 s), and saves `last_cleanup_time` the same way. It re-loads once more before the reminder gate so `in_subagent` is current, and it no longer does a final whole-state save.
  - **Stop** (`run_stop_hook`) re-loads after the Langfuse finalize and after the LLM review, before it edits `silent_tool_nudge_count` / `consecutive_stop_blocks`.
  - **SessionStart** saves only `version_block_active` / `version_block_message` after the version probe, and only its own `cross_session_awareness[<session>]` entry after CSA registration.
  - **PreToolUse** saves only its own agent's `tool_use_counter` (`_adopt_csa_agent_counter`) and writes nothing when `state.json` has no CSA entry for the session. That guard is defensive and harmless: today nothing removes the entry (the Stop hook's `csa_on_session_end` only edits an in-memory copy that is never saved, so session entries stay in `state.json`).
  - SubagentStart / SubagentStop / UserPromptSubmit have no slow step between load and save (UserPromptSubmit loads after its slow work), so they keep the plain load, edit, save.
  - **`update_state` takes a lock (#162).** Re-loading shrinks the window but does not close it: two processes inside load-edit-save at the same instant each saved a copy without the other's key (lost in 16/20 runs with no sleep, 5/5 with a 0.3 s mutate). `update_state` now serializes load, mutate and save with `fcntl.flock(LOCK_EX)` on a sidecar `<state path>.lock`. The sidecar is needed because `os.replace` swaps `state.json`'s inode, so a lock on it would vanish on every save. The lock file is never deleted.
    - The wait is bounded: `LOCK_EX | LOCK_NB` polled every 10 ms for at most `hook.STATE_LOCK_TIMEOUT_SECONDS` (2.0 s, a counted loop). On timeout (or if the lock file cannot be opened or locked) it logs `log_warning("hook", ...)` naming the state path and proceeds **unlocked**, which is the pre-lock behavior. A hook must never block or fail because of the lock.
    - The lock is held only while `mutate` runs, so every `mutate` passed to `update_state` must be a quick in-memory edit (all five current call sites are). The fd is closed in `finally`.
    - Tests: `tests/test_issue_162_state_lock.py` (real forked processes on a barrier, held-lock timeout, release on exception).
  - **Residual risk:** only `update_state` takes the lock. The plain load-edit-save paths still run unlocked: SessionStart's `tool_execution_count` reset, SubagentStart and SubagentStop (`subagent_counter`, `in_subagent`, `current_subagent_*`), UserPromptSubmit, the Stop hook's `silent_tool_nudge_count` / `consecutive_stop_blocks` saves, and the `tempo session on|off` command. They can still lose an update against any other writer inside their millisecond window. Moving them onto `update_state` is the next step; not done. New hooks must use `update_state` and never hold a snapshot across a slow step.

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
