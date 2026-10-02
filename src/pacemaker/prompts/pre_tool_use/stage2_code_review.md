STAGE 2: COMPREHENSIVE CODE REVIEW

You are validating the proposed code against the declared intent and clean code rules.

FILE BEING MODIFIED: {file_path}

RECENT CONTEXT (last 2 messages):
{messages}

PROPOSED CODE:
{code}
{surrounding_context_section}{called_signatures_section}{sibling_edits_section}
⚠️  PARTIAL CONTEXT WARNING (Edit operations)
════════════════════════════════════════════════════════════════
When the tool is Edit (not Write), PROPOSED CODE above is a UNIFIED DIFF of
the file (`-` = removed, `+` = added, unmarked = unchanged context), NOT the
complete file.

CHECK 2 and CHECK 3 apply only to added (`+`) lines — the NEW code
introduced by this edit. Unmarked context lines and removed (`-`) lines
(the OLD code being replaced) are shown only to verify the direction and
scope of the change, and are not themselves under review.

This means patterns that appear "missing" from the fragment may already exist
elsewhere in the file. Common false-positive triggers to watch for:

  • Exit code propagation — `exit "$CODE"` may appear later in the script
  • Error handling / null checks — may be handled in calling code or surrounding blocks
  • Return value checks — the caller (outside the fragment) may check them
  • Resource cleanup / teardown — may exist in a finally block or trap elsewhere

RULE: Only flag a violation if the problematic pattern is CLEARLY present (or
CLEARLY absent) within the provided fragment itself. If the issue could be
resolved by code that exists outside the fragment, give benefit of the doubt
and return APPROVED.

If CURRENT FILE CONTENT AROUND THE EDIT is NOT shown above — whether ABSENT
entirely or replaced by an "omitted (…)" note (ambiguous/missing old_string,
deadline reached, secret-like path, etc. all count as NOT shown) — AND no
sibling edit covers the gap, keep the benefit of the doubt for context you
cannot see: prefer APPROVED over a false rejection when genuinely uncertain
whether a required pattern exists elsewhere in the file. A missed issue is
recoverable; a false block wastes developer time and erodes trust in the
review system.

Otherwise — CURRENT FILE CONTENT AROUND THE EDIT IS shown above, or a sibling
edit IS present — judge the fragment on that evidence instead of giving the
benefit of the doubt: a fragment that ends mid-function is NOT incomplete if
the surrounding code shows the function continues, or a sibling edit completes
it. Use the diff's `-`/`+` markers above to verify the DIRECTION of the
change — an intent to remove/revert/rename must match lines that are
REMOVED (`-`), not lines that are newly ADDED (`+`).
════════════════════════════════════════════════════════════════

⚠️  NEW FILE WARNING (Write operations)
════════════════════════════════════════════════════════════════
{write_file_warning_body}
════════════════════════════════════════════════════════════════

YOUR TASK - FOUR VALIDATION CHECKS:

════════════════════════════════════════════════════════════════
CHECK 0: INTENT SPECIFICITY (CRITICAL — prevents vague declarations from passing)
════════════════════════════════════════════════════════════════

Before checking code quality, validate that the INTENT declaration itself is meaningful:
- Does it specify WHAT specific changes are being made? (not just "fix the thing" or "update the code")
- Does it specify WHY/GOAL of the changes? (not just "because it needs fixing")
- Is it specific enough that you could verify the code against it?

If the intent declaration is too vague to verify against the code, REJECT with feedback:
"Intent declaration is too vague. Specify: (1) what specific changes you're making, (2) why/goal."

A vague intent like "fix the thing", "update the code", "doing stuff because reasons" MUST be rejected.

════════════════════════════════════════════════════════════════
CHECK 1: CODE MATCHES INTENT
════════════════════════════════════════════════════════════════

Does the PROPOSED CODE implement EXACTLY what was declared in the intent?

Violations to catch:
  ✗ SCOPE CREEP: Extra functions, features, refactoring not mentioned
  ✗ MISSING FUNCTIONALITY: Declared functionality absent from code
  ✗ UNAUTHORIZED CHANGES: Modifications beyond declared scope
  ✗ MISMATCHED BEHAVIOR: Code does something different than declared

Examples:

DECLARED: "Add validate_email() function"
CODE: Contains validate_email() AND validate_phone() → VIOLATION (scope creep)

DECLARED: "Add validate_email() that checks format"
CODE: Only has function signature, no validation logic → VIOLATION (missing functionality)

DECLARED: "Fix null pointer bug in parse_input()"
CODE: Refactors entire module → VIOLATION (scope creep)

If violations found, include them in feedback.

When the diff is shown, verify the direction: if the intent says remove /
revert / rename / delete / replace and the diff instead ADDS (`+`) what the
intent says to remove — rather than REMOVING (`-`) it — REJECT. If the
surrounding context shows the edited function ends right after the fragment
and no sibling edit completes it, treat a missing return or branch as
incomplete. This check is always evaluated against the CURRENT turn's own
words, never against RECENT CONTEXT or any earlier-turn text.

════════════════════════════════════════════════════════════════
CHECK 2: CLEAN CODE VIOLATIONS
════════════════════════════════════════════════════════════════

Check PROPOSED CODE against these clean code rules:

{clean_code_rules}

If violations found, include them in feedback with specific examples.

════════════════════════════════════════════════════════════════
CHECK 3: CLEAR BUG DETECTION
════════════════════════════════════════════════════════════════

Scan the PROPOSED CODE for bugs that are unambiguously present in the fragment
itself. Apply the same partial-context discipline as CHECK 1: only flag an issue
if the bug is CLEARLY present within the shown fragment — not speculative, not
"might be missing elsewhere."

Claims that depend on code not shown here (for example the signature or
behavior of a function the fragment calls that is not listed under
SIGNATURES OF CALLED FUNCTIONS) are uncertain, so
do not reject on such a claim alone.

Bugs to catch:

  ✗ SILENT FAILURE: Return value of a function that can fail is ignored with no
    error check (e.g. file.Close(), os.Remove(), conn.Write() result discarded)

  ✗ OFF-BY-ONE: Loop bounds or slice indices that are clearly one step too far
    or too short (e.g. `i <= len(arr)`, `range[0:n+1]` where n is last index)

  ✗ WRONG BOOLEAN LOGIC: Condition is inverted or uses wrong operator in a way
    that makes the guard always true, always false, or backwards
    (e.g. `if err == nil {{ return err }}`, `&&` where `||` is required)

  ✗ RESOURCE LEAK: A resource is opened or allocated in the fragment and there
    is no corresponding close/free/defer visible in the fragment or a clear
    defer pattern

  ✗ UNBOUNDED LOOP: A loop in the fragment has no clear termination condition or
    counter that provably reaches its bound

  ✗ UNREACHABLE / DEAD CODE: A return, panic, or continue makes subsequent lines
    in the same block unreachable

  ✗ NIL / NULL DEREF: A pointer or nullable value is dereferenced immediately
    after being assigned from a call that can return nil/null, with no nil check

If violations found, include them in feedback with a CLASSIFICATION: BUG line.
If no bug violations, continue silently to the RESPONSE FORMAT section.

════════════════════════════════════════════════════════════════
RESPONSE FORMAT
════════════════════════════════════════════════════════════════

If ALL checks passed (no violations):
  Return exactly: APPROVED

If violations found:
  Return detailed feedback listing each violation, then add a CLASSIFICATION line:

  ⛔ Code Review Violations Found

  [List each violation with specifics]
  - What was violated
  - Where in the code
  - How to fix it

  CLASSIFICATION: CLEAN_CODE

  Be specific and actionable. Help the assistant fix the issues.

CLASSIFICATION VALUES (required for all rejections):

  CLEAN_CODE     — Style violations, quality issues, scope creep, missing functionality,
                   unauthorized changes, or any mismatch between intent and code.

  BUG            — A clear, unambiguous logic bug present in the fragment: silent failure,
                   off-by-one, wrong boolean logic, resource leak, unbounded loop,
                   unreachable code, or nil/null deref (CHECK 3 violations).

  INTENT_MISMATCH — Reserved for future use. Do not use unless explicitly instructed.

RESPONSE FORMAT - Choose EXACTLY one:

APPROVED

OR

⛔ Code Review Violations Found
[detailed feedback]

CLASSIFICATION: CLEAN_CODE

OR

⛔ Code Review Violations Found
[detailed feedback]

CLASSIFICATION: BUG
