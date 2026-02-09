🔐 **Did the output above contain sensitive data?** (passwords, keys, tokens, credentials, file contents from `.secrets`/`.env`)

**If YES and you forgot to declare the file path beforehand**, declare the VALUES now as fallback:
```
🔐 SECRET_TEXT: each-secret-value
```
Or for multi-line:
```
🔐 SECRET_FILE_START
the actual secret contents here
🔐 SECRET_FILE_END
```

⚠️ **Note:** Declaring AFTER reading is a fallback. Next time, declare the FILE PATH BEFORE reading!
