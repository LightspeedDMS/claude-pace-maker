# Reviewer Pipeline (`src/pacemaker/inference/`)

Read this before changing providers, `hook_model` handling, competitive review, verdict parsing, or the Stop-hook validator prompt.

## Providers and hook_model tokens

- Valid tokens are whatever `model_aliases.is_known_model()` accepts: `KNOWN_MODELS` (including the 11 `agy-*` tokens), `SHORT_ALIASES` (`gpt-5`, `gpt`, `codex`, `gem-flash`, `gem-pro`), and any `codex-<profile>` (shape `^codex-[A-Za-z0-9][A-Za-z0-9._-]*$`). **`codex-<x>` always means a Codex CLI profile, never a model**, so a plain model token never takes the `codex-` prefix. `pace-maker hook-model` validates single tokens and every competitive/synthesizer slot with `is_known_model()`, not a static list (the only static `valid_models` list belongs to the unrelated `prefer-model` command).
- **Codex argv always includes `--skip-git-repo-check`** right after `exec`. Without it, codex 0.139's trust guard exits 1 and the reviewer silently falls back to `anthropic-sdk`. It is safe because codex always runs with `-s read-only`.
  - Profile mode is `codex exec --skip-git-repo-check - --profile <name> -s read-only` (no `-m`). Never parse `~/.codex/` config; codex validates profiles at runtime.
  - The stderr preview filters known-benign lines and keeps the **last** 300 chars.
- agy: `agy --print <prompt> [--model "<name>"]`, with the system prompt embedded in the text. The model map is `agy_provider._MODEL_MAP`.
- Reviewer labels:
  - codex aliases: `codex-gpt5`
  - `codex-<profile>` and `agy-*`: verbatim
  - gemini: `gem-flash` / `gem-pro`
  - SDK fallback: `anthropic-sdk`

  Governance `feedback_text` is prefixed with `[label]` (there is **no** `REVIEWER:` prefix). The monitor's `get_reviewer_tag_info()` maps `codex-*` to `[Codex]`.
- On the single-model path, a `ProviderError` falls back to the Anthropic SDK.
- **The Anthropic reviewer must stay isolated**: `anthropic_provider._build_options()` passes `setting_sources=[]` and `strict_mcp_config=True`. Without them each review starts a full user session (hooks, plugins, MCP), and a one-word review took 20–49 s instead of about 3 s (#147). Locked by `tests/test_issue_147_reviewer_isolation.py`.

## Deadlines (#152)

- On the single-model path, the provider gets `timeout=_remaining_budget(_deadline, 5.0)`. Providers clamp their subprocess/SDK timeout with it, and `None` keeps the old behavior.
- The SDK fallback is skipped when less than `MIN_SDK_FALLBACK_BUDGET_SECONDS` (15) remains, and the gate fails closed as reviewer-unavailable.
- Both the Write/Edit and danger-bash gates pass `_deadline`. The Stop path (`resolve_and_call`) has no deadline and must not get one.
- **Reviewer CLI ceiling is 240 s (#165)**: `constants.REVIEWER_CLI_TIMEOUT_SECONDS`, shared by the codex, agy and gemini providers (they used to carry three copies of 120). The PreToolUse and Stop hook timeouts are 300 s so the ceiling can take effect; the four places that must agree, and the budget arithmetic, are in [deployment.md](deployment.md#hook-timeouts-settingsjson). Locked by `tests/unit/test_issue_165_reviewer_timeout.py`.
- **"anthropic-sdk: Empty response from opus" after a codex timeout is NOT a deadline cut** (#165 investigation, `blockage_events` 57514 and 57522): both fallbacks died after 29 s and 23 s of a ~45 s budget. A fallback cut by its deadline reads `Anthropic SDK query timed out after Ns` (`AnthropicProvider.query` wraps the call in `asyncio.wait_for`; `tests/unit/test_issue_165_reviewer_timeout.py::TestDeadlineCutIsLabelledAsTimeout`). "Empty response" comes from `_query_async`, which logs an SDK exception at debug level only and then raises it when there is no result text. The underlying SDK error is therefore not visible in the blockage reason.
- Known gap: competitive per-reviewer calls don't pass `timeout=`, so an abandoned reviewer thread can outlive the gate.

## Competitive / mechanical (`m1+m2[+m3]->synth`, `competitive.run_mechanical()`)

- **The decision is made in code.** It passes when every verifier that responded passes (#131); a verifier that didn't respond is an infrastructure failure, not a vote.
  - Approvals with missing verifiers are recorded as degraded via the `_degradation` out-param, which `hook._record_degraded_review_telemetry` turns into a `DG` event.
  - Don't bring back the "a required verifier did not respond" block.
- **Zero survivors returns `""`.**
  - PreToolUse blocks with `build_reviewer_unavailable_message()` under category `intent_validation_reviewer_unavailable`, tagged `fail_closed_error` (#142).
  - Stop fails open.
- On failure:
  - One failing verifier: its raw feedback is used.
  - Two or more: the synthesizer only *formats* the message (`prompts/common/mechanical_failure_synthesis.md`).
  - In both cases the code applies `BLOCKED: ` after `_strip_leading_blocked_prefix`, so the synthesizer can never flip the verdict.
- Parallel dispatch uses `ThreadPoolExecutor` with `futures_wait(timeout=REVIEWER_WAIT_TIMEOUT_SEC)` and `shutdown(wait=False)`.
- CLI: `pace-maker hook-model <expr>` validates with `parse_competitive()`. Tests: `tests/test_mechanical.py`.
- When you add a category to `constants.BLOCKAGE_CATEGORIES`, also add it to claude-usage `KNOWN_BLOCKAGE_CATEGORIES`.

## Verdict primitive (`inference/verdict.py`, stdlib-only leaf)

- A line passes if it *starts with* `APPROVED` (so `APPROVED.` passes and `NOT APPROVED` fails). **A `BLOCKED:` line always wins.** Empty output fails. In the `stop_hook` context, `COMPLETE:` also passes.
- All three gates use it: Stop, Stage 2 and danger-bash Phase 2. The Stop hook's `_find_verdict` keeps its own inline BLOCKED scan because it needs the line text as the block reason; don't replace that with `has_block_marker()`.

## Stop-hook validator prompt (`prompts/stop/stop_hook_validator_prompt.md`, #87)

- It is written for the **weakest** verifier, because in a competitive setup either weak verifier can block.
- The CORE PRINCIPLE comes first: waiting on any async mechanism that wakes Claude again means APPROVED. Phrase lists are examples, not the rule.
- Waiting on a genuine user decision is allowed; asking "shall I fix it?" about actionable work is blocked as analysis paralysis.
- It accepts FORMAT A (E2E report), B (table against acceptance criteria) or C (ad-hoc evidence table). A test asserts the literal `E2E TEST COMPLETION REPORT`.
- These invariants are locked by `tests/test_stop_hook_prompt_async_wait.py`. Untested hypothesis, not a known defect: listing the heavy FORMAT A first may work against the "permissive rule first" principle; C → B → A would be more consistent.
