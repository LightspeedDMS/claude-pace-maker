STAGE 2: COMPREHENSIVE CODE REVIEW

You are validating the proposed code against the declared intent and clean code rules.

FILE BEING MODIFIED: {file_path}

RECENT CONTEXT (last 4 messages):
{messages}

PROPOSED CODE:
{code}

⚠️  PARTIAL CONTEXT WARNING (Edit operations)
════════════════════════════════════════════════════════════════
When the tool is Edit (not Write), PROPOSED CODE above shows ONLY the changed
fragment (old string → new string), NOT the complete file.

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

If you are genuinely uncertain whether a required pattern exists elsewhere in
the file, prefer APPROVED over a false rejection. A missed issue is recoverable;
a false block wastes developer time and erodes trust in the review system.
════════════════════════════════════════════════════════════════

⚠️  NEW FILE WARNING (Write operations)
════════════════════════════════════════════════════════════════
When the tool is Write, the file at the path above is being CREATED by this
operation — it does NOT exist on disk yet, and you have NO filesystem access.
The PROPOSED CODE above is the COMPLETE, final content of that new file; you
already have everything you need to review it.

NEVER reject on the grounds that the file "does not exist", "could not be read",
"was not created", or "the path appears incorrect / wrong" — that is the
EXPECTED state for a file being created, not a problem, and NEVER a reason to
block. Do not ask for the file to be created first. Base your entire decision
SOLELY on the PROPOSED CODE shown above and the declared intent.
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
