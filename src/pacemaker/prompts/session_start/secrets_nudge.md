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

## Quick Reference

| Situation | Action |
|-----------|--------|
| About to read `.secrets`, `.env`, `*.key` | Declare FILE PATH first, then read |
| User provides password/token/key | Declare with SECRET_TEXT immediately |
| Generated a key/password | Declare with SECRET_TEXT immediately |

**CLI:** `pace-maker secrets add <value>` | `pace-maker secrets addfile <path>`

Declared secrets are masked as `*** MASKED ***` in Langfuse traces.
