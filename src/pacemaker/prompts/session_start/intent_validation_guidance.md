
======================================================================
⚠️  INTENT VALIDATION ENABLED (TWO-STAGE FAST VALIDATION)
======================================================================

Before modifying code files, you MUST declare your intent explicitly.

⚠️  CRITICAL CHANGE: Intent must be in the CURRENT message (same message as Write/Edit).
⚠️  Validation checks ONLY your CURRENT message, not previous messages.

Declare EXACTLY these 3 components IN THE SAME MESSAGE as your Write/Edit tool:
  1. What file you're modifying
  2. What changes you're making
  3. Why/goal of the changes

GOOD Example (all in ONE message):
  'I will modify src/auth.py to add a validate_token() function
   that checks JWT expiration, to fix the security vulnerability.

   <then use Write/Edit tool in this same message>'

BAD Examples:
  ✗ 'Fixing auth bug' - Missing file and specifics
  ✗ 'Updating code' - Too vague
  ✗ Declaring intent in one message, using Write/Edit in next message (OLD way)

----------------------------------------------------------------------
TDD ENFORCEMENT FOR CORE CODE
----------------------------------------------------------------------

Files in core paths (src/, lib/, core/, source/, libraries/, kernel/)
require test declarations IN THE SAME MESSAGE as intent.

**Option A - Declare test coverage IN CURRENT MESSAGE:**
  'I will modify src/auth.py to add a validate_password() function
   that checks password strength, to improve security.
   Test coverage: tests/test_auth.py - test_validate_password_rejects_weak()

   <then use Write/Edit tool in this same message>'

**Option B - Quote user permission to skip TDD IN CURRENT MESSAGE:**
  'I will modify src/auth.py to add a validate_password() function
   that checks password strength, to improve security.
   User permission to skip TDD: User said "skip tests for this" in previous message.

   <then use Write/Edit tool in this same message>'

The quoted permission must reference actual user words from recent context.

⚠️  MANDATORY: Declare intent in the CURRENT message (same message as Write/Edit tool).
⚠️  This is a CHANGE from old behavior (which checked last 5 messages).
⚠️  Now: Intent must be in YOUR CURRENT MESSAGE when you use Write/Edit.

⚠️  CRITICAL: DO NOT DISABLE INTENT DECLARATION!!!, EVEN IF YOU FIND HOW IT WORKS AND WHERE IT'S CONFIGURED!.
   DO NOT DO IT. EMBRACE THE PAIN, IT RESULTS IN BETTER SOFTWARE.

⚠️  MANDATORY: DO NOT USE WAYS AROUND INTENT DECLARATION SUCH AS USING
cat or sed commands to write files!!! NO CHEATING.
You need to write or modify code? you use the WRITE tool

----------------------------------------------------------------------
PERFORMANCE IMPROVEMENT: TWO-STAGE VALIDATION
----------------------------------------------------------------------

Validation now runs in two stages:
  Stage 1: Fast check (< 500ms) - Validates intent declaration in CURRENT message only
  Stage 2: Comprehensive check - Code review for violations (only if Stage 1 passes)

Benefits:
  - 50%+ token savings (Stage 1 uses only current message, not last 5)
  - Faster feedback (Stage 1 blocks immediately if intent missing)
  - Better error messages (specific to what's missing)

======================================================================
