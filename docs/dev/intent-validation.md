# Intent Validation — Architecture and Invariants

Read this before changing `hook.run_pre_tool_hook`, `intent_validator.py`, `transcript_reader.py`, `core_paths.py` / `core_path_markers.py` / `excluded_paths.py`, the danger-bash rules, or anything under `prompts/pre_tool_use/` or `prompts/session_start/`.

Related: [stage2-review-prompts.md](stage2-review-prompts.md), [declare-intent-tool.md](declare-intent-tool.md), [reviewer-pipeline.md](reviewer-pipeline.md).

**Claude never disables intent validation.** To change this code, follow "Changing validation code" in [deployment.md](deployment.md#changing-validation-code).

## Pipeline

**Write/Edit gate** (`hook.run_pre_tool_hook`, source files only):
1. declare_intent store ([declare-intent-tool.md](declare-intent-tool.md))
2. transcript anchor
3. Stage 1 regex (`_regex_stage1_check`: an `INTENT:` marker whose text names the target file; on core paths also a `Test coverage:` / quoted-user-permission declaration or a version bump)
4. Stage 2 LLM review ([stage2-review-prompts.md](stage2-review-prompts.md))

**Danger-bash gate** (Bash): rule match, then Phase 1 (INTENT present?), then Phase 2 (LLM checks intent against the command).

## Transcript anchor (#83, #91, #93, #139, #140)

- PreToolUse often fires **before the current turn has been flushed** to the transcript. Never fall back to "the last message". `transcript_reader.get_current_turn_message_for_validation(transcript_path, tool_input, tool_name)` anchors on the tool_use whose input is **byte-identical** to the current one:
  - Write: `file_path` + `content`
  - Edit: `file_path` + `old_string` + `new_string` + `bool(replace_all)`. **Do not loosen this:** it stops a deletion from reusing an earlier, different deletion's intent.
  - Bash: `command`
- Each read costs the same regardless of file size: one `TAIL_READ_BYTES` (512 KB) read from EOF, never grown, searching only the last `LAST_N_TURNS_FOR_TOOL_MATCH` = 2 *logical* turns (grouped by requestId). **Don't raise N**, because that reopens stale-anchor acceptance (#90). **Don't reintroduce window growth:** the not-found case would scan the whole file on every retry.
- Retries back off exponentially against a `time.monotonic()` ceiling: 30 s for Write/Edit (`PRE_TOOL_ANCHOR_CAP_SECONDS`, clamped to the gate deadline), `_DANGER_BASH_MAX_WAIT_SECONDS` = 3 s for Bash (for Bash the current turn is usually not readable inside the hook window at all, so a long wait is pure latency). Write/Edit returns early on `stale` after `_WRITE_EDIT_STALE_GRACE_SECONDS` = 3 s. A match returns immediately.
- Outcomes (`_outcome` / `_diagnostics["outcome"]`):
  - `found`: use the anchor.
  - `not_found`: **fail closed** (`intent_validation_deferred` / danger-bash deferred) and ask the agent to re-issue the IDENTICAL call as its very next tool call. This used to fail open, which let raced edits through completely unvalidated (v2.33.2); don't go back. It applies to every model, including #151 exception-listed ones.
  - `stale`: **accepted by both gates**, since a byte-identical match means it is a re-issue. Deleting the stale branches (e.g. danger-bash's `elif _bash_outcome == "stale":`) brings back the deadlock where every call was refused.
- **`stale_text` must stay gated on the turn's own TEXT**, symmetric with the found path: the `INTENT_MARKER_PATTERN` check runs on `merged["text"]`, never on the rendered `_format_message_with_tools(merged)` string. A first version had no such gate, so an `INTENT:` inside written file content satisfied Phase 1 (#93).
- Recovery only works if the re-issue lands within 2 logical turns. The number of rounds is not otherwise bounded.
- **Stage 1 only ever sees prose (the turn's text), never rendered tool parameters**, because an `INTENT:` inside file content must not count. Prose travels as structured data:
  - `_outcome["anchor_prose_text"]`
  - `get_last_n_messages_for_validation(..., _with_prose=True)`, which returns a `(rendered, prose)` tuple from one parse
  - `validate_intent_and_code(stage1_fallback_messages=...)`

  Never split on a `[TOOL: ` string. An empty stale text sets `messages = []` so there is no n-back rescue.
- `hook.py` checks prose substitution with `isinstance(p, str) and p`. Don't replace that with `.get(key, default)`: the key can be present with the value `None`.
- **Bash is anchor-only** (no n-back; an INTENT in a prior requestId is a Phase-1 block, on purpose). Write/Edit keeps the prose-only n-back rescue for turns split across requestIds.
- Defense in depth (#83): `extract_current_assistant_message(messages, file_path=...)` discards an n-back message that carries an `INTENT:` marker but names a different file (`_mentions_file`), so a wrong-file stale turn can't pass.
- Stale acceptance proves only that the INTENT came from a turn that issued this exact call. In a multi-tool turn, the INTENT text may describe a sibling call; checking intent against the actual call is Stage 2's / Phase 2's job.
- `INTENT_MARKER_PATTERN` (transcript_reader) is the single regex definition. Don't copy it elsewhere.
- Anchor-shape fields copied into `_diagnostics`: `anchor_prose_text`, `anchor_has_visible_text`, `anchor_has_thinking`, `anchor_model`, `anchor_reasoning_summary`, `anchor_recent_context`, `anchor_sibling_edits`, `anchor_declare_intents`.
  - All come from the same tail window, so a second transcript parse is never needed.
  - Each is set outside or before unrelated try blocks, so one failure can't leave another unset.
- A `WARNING ... gave up after N attempts` line is also logged on an *accepted* stale match. Check `blockage_events` before concluding something was blocked.

## Core-path detection (#92) — `intent_validator._is_core_path()`

Core paths require a `Test coverage:` (TDD) declaration.

- **Layer 0 (negative) runs first, and the order matters:** excluded path, then non-source extension, then test filename pattern (`*_test.go`).
- **Layer 1**: word list from `core_paths.yaml` via `core_paths.load_paths_with_migration()`. The 11 defaults are `src lib code core source libraries kernel app routes services internal`.
  - Rejected words (`apps`, `packages`, `utils`, `libs`, `models`, `jobs`, `common`, `scripts`, …) are locked by tests.
  - The one-time migration appends the 4 new words and sets `_migrated_story_92`, so a word the user removes later is never re-added.
  - The CLI's plain `load_paths()` never migrates.
- **Layer 2**: an uncapped upward walk looking for project markers (`*.csproj`/`*.sln`, `pyproject.toml`/`setup.py`, `package.json`, gradle/`pom.xml`, `go.mod`, `Cargo.toml`). It terminates because a path has finitely many ancestors. **2c**: test-project markers (`*.Tests.csproj`, …) mean not core.
- `core_path_markers.find_project_marker()` requires an absolute path. **Don't switch it to `abspath()`**, because tests that pass relative paths would then resolve against this repo's own `pyproject.toml`.
- Expected consequence: in this repo `scripts/`, `install.sh`, `docs/*.py` and `examples/*.py` count as core paths. Non-source files under `src/` (e.g. `.md`) are not.
- `excluded_paths.is_excluded_path()` also accepts `*`-prefixed filename-suffix patterns.
- Survey evidence: `.analysis/core_paths_survey_by_class.md`.

## No visible text / Claude 5.x at xhigh effort (#141, #148, #150)

- At xhigh effort, Claude 5.x models write their pre-edit note as *thinking* and produce turns with no visible text. These are detected by `anchor_has_visible_text is False`, which covers all three shapes: non-empty thinking, empty thinking, and no thinking at all. Blockage details key: `no_visible_text` (older rows use `thinking_only`).
- The block reason **leads** with `intent_validator.build_no_visible_text_notice(example)`: static `prompts/common/no_visible_text_notice.md` plus a ready-to-copy example appended in code. The example names the real file (with a `Test coverage:` line on core paths), or for Bash the first non-blank line of the command, whitespace-collapsed and capped at 60 chars (`hook._build_bash_command_preview`).
- **Never pass file paths or commands through `PromptLoader.load_prompt(variables=...)`.** It rescans substituted values for `{{word}}` and crashes. Load static text and use concatenation, `str.replace` or `str.format`.
- `prompts/session_start/intent_validation_guidance.md` contains the pilot-validated "C text". With declare_intent on, it comes right after the declare_intent paragraph. **Do not reword it.**
- **Never phrase any guidance as asking the model to write out, reveal or copy its reasoning:** the API refuses that with `[reasoning_extraction]`. **Don't add a PostToolUse reminder** for this either; it measured worse in the pilot.

## Reasoning-summary intent exception (#151, #154)

- Config `reasoning_summary_intent_models` (default `["claude-opus-5-5"]`) is matched against the anchor's `message.model`. Always normalize it with `hook._normalize_reasoning_summary_intent_models()` (a non-list becomes `[]`; non-strings are dropped). **Never test `in` against the raw value**: a string does substring matching and `null` raises `TypeError`, which fails closed for every model.
- Both gates call the shared resolver `intent_validator.resolve_reasoning_summary_intent_source()`:

| Case | Source | Path |
|---|---|---|
| model not listed | — | strict, untagged |
| the anchor's own **visible** text has `INTENT:` | `declaration` | strict (TDD enforced) |
| no marker, reasoning summary non-empty | `reasoning_summary` | relaxed |
| no marker, no summary, visible text non-empty | `visible_text` | relaxed |
| anchor empty, previous turn non-empty (**Write/Edit only**, `allow_prior_turn_fallback=True`; danger-bash passes `False`) | `prior_reasoning_summary` | relaxed |
| all empty | `none` | strict, so the n-back rescue still runs |

- The relaxed path (`_validate_reasoning_summary_path`, template `stage2_code_review_reasoning_summary.md`):
  - Intent text is capped at 3000 chars.
  - On core paths, `reasoning_summary_core_path_note.md` asks for `CLASSIFICATION: TDD` (maps to `intent_validation_tdd`) unless one of four cases applies: tests added/updated; an existing test named or referred to (including making a failing test pass); the edit is itself a test; quoted user permission. These mirror the positive forms `_has_tdd_declaration` accepts; narrowing them caused false rejects. Negative or deferred test mentions don't count. The note is kept short for weak verifiers (a test locks it under 1200 chars).
  - For a prior-turn intent, `reasoning_summary_prior_turn_note.md` tells the reviewer the text comes from the previous message.
  - Accepted risk: Stage 1 passes any non-empty text, so CHECK 0 (specificity) is the only backstop.
- **RECENT CONTEXT** (relaxed Write/Edit only): up to `RECENT_CONTEXT_MAX_TURNS` (3) prior turns, about 2000 chars, keeping the newest, computed from the anchor's own tail window. It is included only when the current intent is terse (`_should_include_recent_context`: under `RECENT_CONTEXT_TERSE_MAX_CHARS` = 150, or the target's basename/stem isn't named). It must never override an explicit current intent. The direction-word suppression clause was removed on purpose; don't re-add it.
- Telemetry: on the transcript path, `details["intent_source"]` and `recent_context_included` are written **only** for listed models; all other rows stay byte-identical. (The declare_intent tool path writes its own `intent_source` values for any model; see [declare-intent-tool.md](declare-intent-tool.md).) The `RS` activity/governance event (`hook._record_reasoning_summary_telemetry`) fires only for relaxed sources.
- Danger-bash: the relaxed wording comes from `danger_bash_relaxed_intent_wording.md` (three paragraphs, used together) plus label templates. The strict wording stays inline in `hook.py`, locked by a fixed-string test.

## Danger Bash

- Runs only when `enabled` **and** `intent_validation_enabled` **and** `danger_bash_enabled` are all set.
- Rules: 55 bundled (`danger_bash_rules_default.yaml`, 25 work-destruction + 30 system-destruction) minus `deleted_rules`, plus the user's additions from `~/.claude-pace-maker/danger_bash_rules.yaml`. Loader/matcher: `danger_bash_rules.py`.
- Phase 1: a rule matches and there is no INTENT, so it blocks with no LLM call.
- Phase 2: the LLM prompt is built **inline in `hook.py`**, a separate code path from Stage 2. The two share only the reviewer resolver and `verdict_passes()`. **Keep them in sync on purpose** (#94 came from them drifting apart).
  - The prompt requires rejections to start with `BLOCKED:`. Keep that: `verdict_passes` is line-based starts-with, so a reply like `Approved: NO` would otherwise pass.
  - It judges only the destructive steps (`danger_bash_destructive_scope_note.md`).
  - When a danger rule matches, the CSA sibling warning is injected.
- Category: `intent_validation_dangerbash`.
