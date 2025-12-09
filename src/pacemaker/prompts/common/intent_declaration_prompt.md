You are checking if Claude declared intent before attempting to {{action}} a file.

File to be modified: {{filename}}
Tool being used: {{tool_name}}

Recent assistant messages:
{{messages_text}}

Question: Did Claude clearly declare intent to {{action}} {{filename}} in these messages?

Intent declaration should include:
1. What file is being modified
2. What changes are being made
3. Why/goal of the changes

Respond with ONLY:
- "YES" if intent was clearly declared
- "NO" if intent was not declared or unclear
