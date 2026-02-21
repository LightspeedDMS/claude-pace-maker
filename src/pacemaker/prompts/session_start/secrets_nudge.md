# Secrets Management

🔐 **CRITICAL**: You MUST proactively declare secrets to prevent leakage to telemetry.

## For Sensitive FILES (`.secrets`, `.env`, `*.key`, `*.pem`, credentials):

⚠️ **STOP! BEFORE using Read tool on sensitive files, FIRST declare the file path:**
```
🔐 SECRET_FILE: /full/path/to/sensitive/file
```
This registers the file so its contents are masked in subsequent Langfuse trace pushes.

**WRONG workflow:** Read file → then declare (TOO LATE - contents already leaked!)
**CORRECT workflow:** Declare file path → THEN read file (contents masked)

## For Inline Secrets (passwords, tokens, keys in conversation):

```
🔐 SECRET_TEXT: the-secret-value
```

## CRITICAL: Extracted Values from Files

⚠️ **SECRET_FILE protects the file's FULL content from Langfuse traces, but individual values extracted from that file are NOT automatically protected when used in subsequent tool calls.**

If you read a sensitive file and then use a specific value from it (e.g., a password in an SSH command, an API key in a curl call), you **MUST also declare that individual value as SECRET_TEXT BEFORE using it**:

**WRONG workflow:** Declare file → Read file → Use password from file in Bash (password leaks in tool trace!)
**CORRECT workflow:** Declare file → Read file → Declare extracted password as SECRET_TEXT → THEN use it in Bash

Example:
```
🔐 SECRET_FILE: /path/to/.secrets          ← protects file content
[Read file, find password inside]
🔐 SECRET_TEXT: the-password-from-file      ← protects the value when used in commands
[Now safe to use in SSH/curl/etc.]
```

## Quick Reference

| Situation | Action |
|-----------|--------|
| About to read `.secrets`, `.env`, `*.key` | Declare FILE PATH first, then read |
| User provides password/token/key | Declare with SECRET_TEXT immediately |
| Generated a key/password | Declare with SECRET_TEXT immediately |
| Using a value extracted from a declared file | Declare with SECRET_TEXT before using in commands |

**CLI:** `pace-maker secrets add <value>` | `pace-maker secrets addfile <path>`

Declared secrets are masked as `*** MASKED ***` in Langfuse traces.
