STAGE 1: FAST DECLARATION CHECK

You are validating if the assistant declared intent BEFORE using the {tool_name} tool.

FILE BEING MODIFIED: {file_path}

CURRENT MESSAGE (what assistant just said):
{current_message}

YOUR TASK - RESPOND WITH ONE WORD:

════════════════════════════════════════════════════════════════
CHECK 1: INTENT DECLARATION
════════════════════════════════════════════════════════════════

Does the CURRENT MESSAGE contain "intent:" or "INTENT:" marker (case-insensitive) followed by a clear intent declaration with these 3 components?
  1. FILE: Mentions the file being modified ({file_path} or its basename)
  2. CHANGES: Describes what changes are being made
  3. GOAL: Explains why/goal of the changes

Examples of VALID intent declarations (accepts any case):
  ✓ "INTENT: Modify auth.py to add validate_token() function that checks JWT expiration"
  ✓ "intent: Edit src/utils.py to fix the parsing bug by adding null checks"
  ✓ "Intent: Create config.py to store application settings for better maintainability"

Examples of INVALID (missing components or marker):
  ✗ "Let me fix this" - No intent: marker, no file, no changes, no goal
  ✗ "Updating code" - No intent: marker, too vague, missing file and specifics
  ✗ "I will modify auth.py..." - Missing intent: marker
  ✗ "Adding function" - No intent: marker, missing file and goal

If intent declaration is MISSING or INCOMPLETE, respond: NO

If intent declaration is PRESENT and COMPLETE, proceed to CHECK 2.

════════════════════════════════════════════════════════════════
CHECK 2: TDD DECLARATION (Only for core code paths)
════════════════════════════════════════════════════════════════

Is the file being modified in a CORE CODE PATH?
Core paths: src/, lib/, core/, source/, libraries/, kernel/

If NOT in core path → Skip this check, respond: YES

If IN core path → Check if TDD declaration or TDD skip permission exists:

TDD DECLARATION - Look for test coverage statement:
  ✓ "Test coverage: tests/test_auth.py::test_validate_token()"
  ✓ "Test: tests/test_utils.py - test_parse_input_handles_null()"
  ✓ "Covered by: tests/integration/test_auth_flow.py"

TDD SKIP PERMISSION - Look for quoted user permission:
  ✓ "User permission to skip TDD: User said 'skip tests for this' in message 2"
  ✓ "User permission: User requested 'no tests needed' in previous message"

  CRITICAL: Quote must be present in CURRENT MESSAGE. Do NOT accept fabricated quotes.

If core path AND no TDD declaration AND no TDD skip permission, respond: NO_TDD

If core path AND TDD declaration OR TDD skip permission found, respond: YES

════════════════════════════════════════════════════════════════
RESPONSE FORMAT
════════════════════════════════════════════════════════════════

Respond with EXACTLY ONE WORD (no explanation):
- YES → All checks passed
- NO → Intent declaration missing or incomplete
- NO_TDD → Core path file without TDD declaration or permission

Be strict. Only respond YES if BOTH checks passed.
