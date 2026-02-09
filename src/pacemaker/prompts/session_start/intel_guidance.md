# Prompt Intelligence Telemetry

You can optionally emit prompt intelligence metadata to help users analyze conversation dynamics. This metadata is captured for analytics and filtering in Langfuse traces.

## Intel Line Format

Emit a single line starting with `§` marker at the beginning of your response:

```
§ △0.8 ◎surg ■bug ◇0.7 ↻2
```

## Symbol Vocabulary

- **§** - Intel line marker (required to identify the line)
- **△** - Frustration (0.0-1.0): User's frustration level based on context
  - 0.0 = Calm, clear request
  - 0.5 = Some confusion or repetition
  - 1.0 = High frustration, multiple failed attempts
- **◎** - Specificity: How specific the user's request is
  - `surg` = Surgical (very specific, targeted change)
  - `const` = Constrained (specific scope with boundaries)
  - `outc` = Outcome-focused (goal stated, approach open)
  - `expl` = Exploratory (vague, needs clarification)
- **■** - Task type: Nature of the work requested
  - `bug` = Bug fix
  - `feat` = New feature
  - `refac` = Refactoring
  - `research` = Investigation/analysis
  - `test` = Testing work
  - `docs` = Documentation
  - `debug` = Debugging/troubleshooting
  - `conf` = Configuration
  - `other` = Other tasks
- **◇** - Quality (0.0-1.0): Your confidence in understanding and solution
  - 0.0 = Low confidence, uncertain
  - 0.5 = Moderate confidence
  - 1.0 = High confidence, clear path forward
- **↻** - Iteration (1-9): Number of attempts on this issue
  - 1 = First attempt
  - 2-3 = Follow-up refinements
  - 4+ = Multiple retries, may indicate complexity

## When to Emit Intel

- **Optional**: Only emit when you have insight into conversation dynamics
- **At response start**: Place intel line as the first line (it will be stripped from user-visible output)
- **Partial data OK**: Emit only fields you're confident about (e.g., `§ △0.5 ■feat` without specificity)
- **Skip if uncertain**: Don't emit intel if you don't have clear signals

## Examples

Frustrated user after failed attempts:
```
§ △0.9 ◎surg ■bug ◇0.6 ↻4
```

Clear new feature request:
```
§ △0.1 ◎const ■feat ◇0.9 ↻1
```

Vague exploratory question:
```
§ △0.3 ◎expl ■research ◇0.4 ↻1
```

## Important Notes

- Intel line is **automatically stripped** from trace output (users won't see it)
- Intel metadata is **attached to Langfuse traces** for filtering/analytics
- This is **purely optional** - emit only when it provides value
- **No defaults** - missing fields are simply not included (don't guess)
