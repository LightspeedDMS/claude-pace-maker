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

YOUR TASK - TWO VALIDATION CHECKS:

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

  CLEAN_CODE     — Use for ALL stage 2 rejections: style violations, quality issues,
                   scope creep, missing functionality, unauthorized changes, or any
                   mismatch between intent and code. Stage 2 IS the code review stage,
                   so every stage 2 rejection is a code review issue.

  INTENT_MISMATCH — Reserved for future use. Do not use unless explicitly instructed.

RESPONSE FORMAT - Choose EXACTLY one:

APPROVED

OR

⛔ Code Review Violations Found
[detailed feedback]

CLASSIFICATION: CLEAN_CODE
