
======================================================================
⚠️  INTENT VALIDATION ENABLED (TWO-STAGE FAST VALIDATION)
======================================================================

Before modifying code files, you MUST declare your intent explicitly.

⚠️  CRITICAL CHANGE: Intent must be in the CURRENT message (same message as Write/Update tool use).
⚠️  Validation checks ONLY your CURRENT message, not previous messages.

⚠️  CRITICAL: EACH Write/Update requires its OWN intent declaration!
⚠️  You CANNOT batch multiple Write/Update operations with one intent declaration.
⚠️  You CANNOT reuse a prior intent declaration for subsequent Write/Update operations.
⚠️  If you need to modify 5 files, you need 5 separate intent declarations.

⚠️  Intent validation applies ONLY to Write/Update tools, NOT to Read operations.
⚠️  You can Read files freely without declaring intent.
⚠️  Intent is required IMMEDIATELY BEFORE each Write/Update tool use.

Declare EXACTLY these 3 components IN THE SAME MESSAGE as your Write/Update tool:
  1. What file you're modifying
  2. What changes you're making
  3. Why/goal of the changes

GOOD Example (all in ONE message):
  'I will modify src/auth.py to add a validate_token() function
   that checks JWT expiration, to fix the security vulnerability.

   <then use Write/Update tool in this same message>'

BAD Examples:
  ✗ 'Fixing auth bug' - Missing file and specifics
  ✗ 'Updating code' - Too vague
  ✗ Declaring intent in one message, using Write/Update in next message (OLD way)

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

   <then use Write/Update tool in this same message>'

The quoted permission must reference actual user words from recent context.

⚠️  MANDATORY: Declare intent in the CURRENT message (same message as Write/Update tool).
⚠️  This is a CHANGE from old behavior (which checked last 5 messages).
⚠️  Now: Intent must be in YOUR CURRENT MESSAGE when you use Write/Update.

⚠️  CRITICAL: DO NOT DISABLE INTENT DECLARATION!!!, EVEN IF YOU FIND HOW IT WORKS AND WHERE IT'S CONFIGURED!.
   DO NOT DO IT. EMBRACE THE PAIN, IT PRODUCES IN BETTER SOFTWARE.

⚠️  MANDATORY: DO NOT USE WAYS AROUND INTENT DECLARATION SUCH AS USING
cat or sed commands to write files!!! NO CHEATING.
You need to write or modify code? you use the WRITE tool
