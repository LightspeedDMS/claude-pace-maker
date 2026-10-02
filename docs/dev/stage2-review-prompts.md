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
- CHECK 3 (#166) carries one extra paragraph, identical in both templates: claims that depend on code not shown (e.g. the signature of a called function not listed under SIGNATURES OF CALLED FUNCTIONS) are uncertain, so the reviewer must not reject on such a claim alone. The lock tests compare the templates with each other, so they stayed valid; `tests/test_issue_166_stage2_wiring.py` pins the wording.
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
- **Signatures of called functions** (#166, `stage2_signatures.build_called_signatures_section`, called from `hook.run_pre_tool_hook`): for Write/Edit on `.py` files, a "SIGNATURES OF CALLED FUNCTIONS" section (header in `prompts/pre_tool_use/stage2_called_signatures_header.md`) placed between the surrounding context and the sibling edits, via the `{called_signatures_section}` placeholder in BOTH templates (outside every M3-locked section).
  - Names come from the ADDED code only (Edit `new_string`, Write `content`): `ast` when it parses, else a conservative regex; plus the targets of `patch("pkg.mod.func")`, `patch.object(mod, "func")` and `setattr`. Builtins and names the new code itself defines are skipped.
  - Resolved from the edited file on disk and from project modules it (or the new code) imports, absolute or relative (`from pkg.x import y`, `import pkg.x as m`, `from pkg import x`), against the project's source roots (each ancestor up to the `.git`/`pyproject.toml` marker, plus its `src/`). One re-export hop is followed (`from ..logger import log_warning`). Stdlib and third-party modules never resolve.
  - Each entry is the full `def`/`class` signature plus a short first docstring line; a class also shows `__init__`.
  - Caps: `STAGE2_SIGNATURES_MAX_CHARS` (1500, the whole section), `STAGE2_SIGNATURES_MAX_ENTRIES` (12), 300 chars per entry, 40 candidate names, 8 modules read, 100 000 chars of new code scanned.
  - Fail-safe: no section for a non-Python file, nothing resolvable, any error (logged as a warning), or a passed gate deadline (re-checked per name). Files are read only through `_read_target_file_for_review`, so the deadline, secret-like-path, size and stored-`SECRET_FILE` gates apply; the rendered section is part of the prompt, so the `_mask_reviewer_prompt` choke point masks it like the rest.
  - New code lives in its own module because `intent_validator.py` is already far past the 1000-line limit.
- **Write over an existing file** sends a diff if it fits in 6000 chars, otherwise the full new content uncapped (a truncated diff hid a planted violation). `write_case` (`new_file` / `existing_diff` / `existing_full_content`) selects the NEW FILE WARNING wording via `_build_write_file_warning_body`. A new file sends its content as-is.

## Secret masking

- **Reviewer prompts are secret-masked.** `_mask_reviewer_prompt()` runs at the `_call_stage2_validation()` choke point, which both Write/Edit paths go through. Danger-bash Phase 2 masks its own prompt in `hook.py`.
- Pass `db_path` explicitly from `hook.DEFAULT_DB_PATH`. `None` is a deliberate no-op. **Never import `constants.DEFAULT_DB_PATH` fresh**, or tests will hit the real `usage.db`.
- Masking uses the shared substring prefilter (`secrets.masking.build_prefiltered_pattern`). Without it, masking cost about 6.5 s per prompt on a 763-secret store.
- Minimum secret length is 8 (`_MIN_REVIEWER_MASK_SECRET_LENGTH`) for reviewer masking only. Skipped secrets are reported as a count, never their values.
- Fail-safe: a secrets-DB read failure logs a WARNING and sends the original prompt; masking never blocks validation.
- `_is_secret_like_path()` is a basename heuristic (`*.env*`, `*secret*`, `*credential*`, `*.pem`, `*.key`, `*.p12`, `id_rsa*`) that over- and under-matches; both are accepted. A secret-like path, or content equal to a stored `SECRET_FILE`, skips the surrounding-context section.
