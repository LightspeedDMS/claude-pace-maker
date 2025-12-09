════════════════════════════════════════════════════════════════
OUTCOME 1.5: CORE CODE WITHOUT TEST DECLARATION → BLOCK + REQUEST TDD
════════════════════════════════════════════════════════════════

If the file being modified is in a CORE CODE PATH:
{{core_paths}}

AND no test declaration is found in the context, return:

⛔ TDD Required for Core Code

You're modifying core code: {file_path}

No test declaration found in recent context. Before modifying core code, you must either:

1. Declare the corresponding test:
   - TEST FILE: Which test file covers this change
   - TEST SCOPE: What behavior the test validates

2. OR quote the user's explicit permission to skip TDD OR a provide a strong justification why TDD is not required

Example with test declaration:
  "I will modify src/auth.py to add password validation.
   Test coverage: tests/test_auth.py - test_password_validation_rejects_weak_passwords()"

Example citing user permission (MUST quote user's actual words):
  "I will modify src/auth.py to add password validation.
   User permission to skip TDD: User said "I allow you not to use TDD" in message 3."

CRITICAL RULES FOR TDD SKIP:
  - The LLM MUST quote the user's ACTUAL words from the messages
  - The quote MUST exist in the provided message context
  - DO NOT allow fabricated or paraphrased permission
  - If no such quote exists in the messages, BLOCK the change

Look for user statements like:
  - "skip TDD", "no tests needed", "don't worry about tests"
  - "I allow you not to use TDD", "skip tests for this"
  - "no need to write tests", "tests not required"

If test declaration OR valid user quote found, proceed to OUTCOME 2 checks.
If modifying files NOT in core paths, skip this check entirely.
