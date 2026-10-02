# declare_intent MCP Tool (#155, #157, #159)

Read this before changing `intent_mcp/`, `intent_declarations/`, the declare_intent hints, or the MCP registration in `install.sh`.

Related: [intent-validation.md](intent-validation.md).

## Why it exists

On Claude 5.x the visible `INTENT:` line is often missing (the note ends up in thinking), and the Write/Edit tool_use is often not yet flushed when PreToolUse fires. With this tool, the agent calls a real tool instead, and the Write/Edit gate reads the store **before any transcript work**.

## Pieces

| Piece | File |
|---|---|
| MCP server (stdlib JSON-RPC over stdio; **stores nothing** and only acknowledges, because only the hooks know `session_id`/`agent_id`) | `intent_mcp/server.py` (`python -m pacemaker.intent_mcp`) |
| Shared field rules and kill switch (stdlib leaf; `transcript_reader` imports this, never the gate/store) | `intent_declarations/fields.py` |
| Store `intent_declarations.db` (`declarations`, `chains`: 60 min TTL; `subagent_guidance` (#157): 24 h; expired rows purged on every access; one `BEGIN IMMEDIATE` per operation; `PACEMAKER_INTENT_DECLARATIONS_PATH`) | `intent_declarations/store.py` |
| Hook wiring | `intent_declarations/gate.py` |
| Tool names (user scope and plugin) | `constants.DECLARE_INTENT_TOOL_NAMES` |

The monitor does not read this DB, so there is no cross-process contract.

## Flow

- PostToolUse (`hook.run_hook`, right after stdin is parsed) records the declaration, keyed by `(session_id, agent_key)`, where `agent_key` = hook `agent_id` or `"main"`.
- Gate order (Write/Edit, before the anchor wait):
  1. A declaration for this file: use the newest and **delete every row** for that file.
  2. The chain's file equals this file: reuse the chain's intent.
  3. The chain is for a different file: delete the chain.
  4. Fall back to the transcript, which also accepts a same-message `declare_intent` tool_use.
- The intent is rendered as `INTENT: <change> in <file> — goal: <goal>` plus an optional `Test coverage:` line (`gate.render_intent_message`; each field capped at 3000 chars, the message at 10000).
- **TDD for tool intents is decided only by a non-blank `test_coverage`** (`tool_declared_tdd`). Stage 1 never regex-scans the synthesized text, and tool intents get no version-bump exemption. Tool intents always take the strict path.
- Outcome:
  - Approved with a tool intent: upsert the chain.
  - **Any** rejection, or an internal error while a tool intent is in use: delete the chain.
  - Approved with a transcript intent: leave the chain alone.
- Telemetry: `details["intent_source"]` = `declare_intent` / `declare_intent_chain`, absent on the transcript path.

## Kill switch and hints

- `intent_declaration_tool_enabled` (`fields.intent_declaration_tool_enabled`, one implementation for hooks and server): an absent key means on; **any value other than a real `true`** (`false`, `null`, `"false"`, …) turns it off. The server re-reads config on every `tools/call` (an unreadable/malformed config counts as on there, as in the hooks) and answers "disabled" instead of recording.
- When it is off: nothing is recorded, gate steps 1–3 are skipped, there are no hints, and the guidance is unchanged from before #155.
- Hints are appended only when the kill switch is on. Each kind of block gets its own static template in `prompts/common/`:
  - Stage 1: `declare_intent_hint.md`
  - Stage 2 rejection of a transcript intent: `declare_intent_hint_review.md`
  - a consumed tool intent: `declare_intent_consumed_note.md`, or `…_unavailable.md` when the reviewer was unavailable
  - deferred: `declare_intent_hint_deferred.md`
- Infrastructure failures (reviewer unavailable on a transcript intent, SDK unavailable, internal error) get no hint. The reviewer's own text and `raw_feedback` are never changed.
- The Stage-1 hint is inserted before the #150 no-visible-text notice is prepended, so that notice still leads. Hint templates are static text plus `str.replace` of `<file_path>`, never `PromptLoader` `variables=`.

## Installation

- `install.sh` (`register_intent_mcp_server`) runs `pacemaker.intent_mcp.registration` from the snapshot, with the interpreter from `find_hook_python`:
  - The command is `claude mcp add --scope user pace-maker -e … -- <python> -m pacemaker.intent_mcp`. The name goes **before** `-e`, because `-e` is variadic.
  - The step is idempotent (remove, then add).
- `install.sh` (`allow_intent_mcp_tool`) adds `mcp__pace-maker__declare_intent` to `permissions.allow` via `intent_mcp/permissions.py`, in the same settings file the hooks were registered in. Without that rule every call prompts or is denied, and edits silently fall back to the transcript path. `permissions.py` edits idempotently and atomically, preserves every other key, and refuses (file untouched) any settings file that isn't the expected shape.
- Both steps are non-fatal and skipped with `PACEMAKER_SKIP_MCP_REGISTRATION=1`. `migrate-to-plugin.sh` removes both.
- A **plugin cannot ship a permission**, so plugin users must allow `mcp__plugin_claude-pace-maker_pace-maker__declare_intent` themselves.
- The plugin declares the server inline in `.claude-plugin/plugin.json` `mcpServers`. Don't use a repo-root `.mcp.json`: the repo root is also a project root.

## Accepted edges

- On the tool/chain path, Stage 2 sees no conversation and no sibling edits. The on-disk surrounding context still applies.
- Edits to non-source files neither consume declarations nor touch the chain.
- A same-message declare_intent counts even if that call itself errored.
- A chain reuses the old intent. If Stage 2 rejects a different change against it, the agent must declare again.

## Sibling edits after a rejection (#163)

An agent may send several Edits to one file in one message under one declaration. If Edit 1 is rejected, the rejection deletes the declaration and chain (unchanged), so Edits 2..n fall back to the transcript path and used to get only the generic "NO visible text" block, which misled the agent.

- `IntentDeclarationStore.reject(..., rejected_file_path)` also writes a marker `(session, agent_key, normalized file)` into the additive table `rejected_declarations`. `gate.record_outcome` passes the file only when a tool/chain declaration was in use. TTL: `constants.REJECTED_DECLARATION_MARKER_TTL_SECONDS` (2 min), purged in `_transaction` like the other tables. A fresh `declare_intent` for that file clears the marker.
- `gate.lead_with_rejected_sibling_note` prepends `prompts/common/declare_intent_rejected_sibling_note.md` (tagged `intent_validation_block`) to the Claude-facing block reason. Only with the kill switch on and an unexpired marker for the same agent and file. Block paths that get it (all are edits with NO declaration or chain in use):
  - the Stage-1 `RegEx` block (NO / NO_TDD, including the no-visible-text notice);
  - a Stage-2 rejection of a transcript-sourced intent, **including the #151 relaxed path** (`intent_source` `reasoning_summary`, `visible_text`, `prior_reasoning_summary`). This is what exception models (Sonnet/Opus 5.5) actually hit, because their sibling edit never reaches the Stage-1 block. The reviewer-relay segment and the #159 review hint stay below the note;
  - the deferred transcript-race block (wrapped in `run_pre_tool_hook`).
- Never gets it: a tool/chain-declared rejection (it already carries the #159 consumed-declaration note, so never both), reviewer-unavailable blocks (infrastructure failure: no #159 hint either, and the cause is not a missing declaration), SDK-unavailable and internal-error blocks. The `hook.py` condition is `_declared_intent is None`, a `reviewer` in the result, and not `reviewer_unavailable_failure`.
- It changes only the block message. Which edits are blocked, Stage 2, reviewer text and `raw_feedback` are untouched.

## Declare per edit (#164)

Sonnet 5.5 tends to declare a whole plan and then edit in pieces; Stage 2 rejects each piece as "missing functionality" on purpose (#156, by design). The fix is wording only:

- One sentence is **appended** to the end of `prompts/session_start/declare_intent_guidance.md` and `prompts/mcp/declare_intent_tool_description.md`: "Declare only what that one Write/Edit does; if a change needs several edits, declare each edit separately, or make the whole change in a single edit."
- A shorter "Declare only what that one edit does." ends `declare_intent_hint_review.md` and `declare_intent_consumed_note.md`, the two blocks that send the agent back to re-declare after a Stage 2 rejection. The Stage-1, deferred and reviewer-unavailable texts are about missing or lost declarations, so they are unchanged.
- The pilot-validated #155 paragraph and the #150 "C text" stay character-exact; the sentence follows the paragraph, before the C text. No reasoning-extraction phrasing. Pinned by `tests/test_issue_164_declare_per_edit_guidance.py` (plus the #155 and #159 exact-wording tests).
