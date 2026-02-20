🔐 **STOP!** If you're about to read `.secrets`, `.env`, `credentials.*`, `*.key`, `*.pem`, or ANY sensitive file:

**YOU MUST declare the file path FIRST, BEFORE this Read executes:**
```
🔐 SECRET_FILE: /full/path/to/the/sensitive/file
```

**WHY:** Once you read the file, its contents go to Langfuse. Declaring the path FIRST lets the system read and register the contents so they're masked.

**DO NOT** read first then declare the value - that's TOO LATE!
