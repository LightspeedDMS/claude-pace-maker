# Stage 2 Review Prompts (Write/Edit)

Read this before changing `prompts/pre_tool_use/stage2_code_review*.md`, the prompt builders in `intent_validator.py`, or how Write/Edit code is shown to the reviewer.

Related: [intent-validation.md](intent-validation.md), [reviewer-pipeline.md](reviewer-pipeline.md).

## Two templates kept in lockstep

- `stage2_code_review.md` covers the normal/strict path. `stage2_code_review_reasoning_summary.md` covers the relaxed path (#151).
- **Four sections must stay verbatim-identical** between them: CHECK 1, CHECK 3, CLASSIFICATION VALUES and PARTIAL CONTEXT WARNING. Lock tests (`TestM3TemplateSectionsVerbatim` in `tests/test_issue_151_reasoning_summary_intent.py`, `TestPartialContextWarningM3LockExtended` in `tests/test_issue_153_edit_context.py`) extract and compare them. If you edit one, edit the other the same way. CHECK 0 differs on purpose.
- NEW FILE WARNING is identical by construction: both templates keep the same static header/separator lines around a shared `{write_file_warning_body}` placeholder. Keep that header static: the lock tests use it as the end boundary of PARTIAL CONTEXT WARNING.
- CHECK 1's direction check ("intent says remove/revert…, diff ADDS it, so REJECT") is judged against the **current** turn's words only, never against RECENT CONTEXT.
- PARTIAL CONTEXT WARNING:
  - Benefit of the doubt applies only when surrounding file context is **not** shown (absent, or an "omitted (…)" note) **and** no sibling edit covers the gap. Otherwise the fragment is judged on the evidence.
  - CHECK 2 and CHECK 3 apply only to added (`+`) lines.
- Build prompts with `str.format()` on the template, never `PromptLoader` `variables=` (it rescans values for `{{word}}`).

## What the reviewer sees

- **Edit**: PROPOSED CODE is a real unified diff (`_build_edit_diff_section`) of the on-disk file against the result of applying the edit (one occurrence, or all with `replace_all`).
  - If the file read fails or `old_string` is missing/ambiguous, it diffs `old_string` against `new_string` instead.
  - The diff is capped at 6000 chars, keeping the tail. Known, accepted gap: early hunks of a large `replace_all` can be cut off.
- **Surrounding context** (`_build_edit_surrounding_context_section`): ±15 lines with the edited region marked.
  - It shares one file read with the diff via `_precomputed` (`_read_edit_target_file`, built on `_read_target_file_for_review`) and reads with `newline=""` so CRLF `old_string`s match.
  - It shows an "omitted (…)" note on deadline, secret-like path, missing/ambiguous `old_string` or a file over 2 MB.
- **Sibling edits** (`transcript_reader._build_sibling_edits_data` plus `_build_sibling_edits_section`): the other Write/Edit calls in the same message.
  - The reviewed call is identified by the anchor's own `tool_use.id`, never by content, since identical edits can't be told apart by content.
  - Each sibling keeps its absolute position (`#1`, `#3`).
- **Write over an existing file** sends a diff if it fits in 6000 chars, otherwise the full new content uncapped (a truncated diff hid a planted violation). `write_case` (`new_file` / `existing_diff` / `existing_full_content`) selects the NEW FILE WARNING wording via `_build_write_file_warning_body`. A new file sends its content as-is.

## Secret masking

- **Reviewer prompts are secret-masked.** `_mask_reviewer_prompt()` runs at the `_call_stage2_validation()` choke point, which both Write/Edit paths go through. Danger-bash Phase 2 masks its own prompt in `hook.py`.
- Pass `db_path` explicitly from `hook.DEFAULT_DB_PATH`. `None` is a deliberate no-op. **Never import `constants.DEFAULT_DB_PATH` fresh**, or tests will hit the real `usage.db`.
- Masking uses the shared substring prefilter (`secrets.masking.build_prefiltered_pattern`). Without it, masking cost about 6.5 s per prompt on a 763-secret store.
- Minimum secret length is 8 (`_MIN_REVIEWER_MASK_SECRET_LENGTH`) for reviewer masking only. Skipped secrets are reported as a count, never their values.
- Fail-safe: a secrets-DB read failure logs a WARNING and sends the original prompt; masking never blocks validation.
- `_is_secret_like_path()` is a basename heuristic (`*.env*`, `*secret*`, `*credential*`, `*.pem`, `*.key`, `*.p12`, `id_rsa*`) that over- and under-matches; both are accepted. A secret-like path, or content equal to a stored `SECRET_FILE`, skips the surrounding-context section.
