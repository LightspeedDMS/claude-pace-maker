🔐 **STOP!** If you're about to read `.secrets`, `.env`, `credentials.*`, `*.key`, `*.pem`, or ANY sensitive file:

**YOU MUST declare the file path FIRST, BEFORE this Read executes:**
```
🔐 SECRET_FILE: /full/path/to/the/sensitive/file
```

**WHY:** Declaring the path FIRST lets the system register the file contents so they're masked before the next Langfuse trace push.

**DO NOT** read first then declare the value - that's TOO LATE!

⚠️ **Also:** If you previously read a sensitive file and are now using an extracted value (password, key, token) in a Bash command, declare it as `🔐 SECRET_TEXT: <value>` BEFORE this tool executes!
