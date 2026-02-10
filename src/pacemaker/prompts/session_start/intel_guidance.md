# Prompt Intelligence Telemetry

Optionally emit prompt intelligence metadata for analytics. Captured for Langfuse trace filtering.

## Format

First line of your response, starting with `§`:

```
§ △0.8 ◎surg ■bug ◇0.7 ↻2
```

## STRICT Value Rules

Each symbol has an EXACT value format. The parser uses regex — invalid formats are silently dropped.

| Symbol | Field | EXACT Format | Valid Values |
|--------|-------|-------------|--------------|
| `△` | frustration | **decimal number** | `0.0` to `1.0` (e.g., `△0.3`, `△0.85`, `△1.0`) |
| `◎` | specificity | **4-letter code** | ONLY: `surg`, `const`, `outc`, `expl` |
| `■` | task_type | **code from list** | ONLY: `bug`, `feat`, `refac`, `research`, `test`, `docs`, `debug`, `conf`, `other` |
| `◇` | quality | **decimal number** | `0.0` to `1.0` (e.g., `◇0.5`, `◇0.9`, `◇0.0`) |
| `↻` | iteration | **single digit** | `1` to `9` (e.g., `↻1`, `↻3`) |

## INVALID — Never Do This

```
§ △low ◎high ■workflow ◇high ↻1        ← WRONG: "low"/"high"/"workflow" are not valid values
§ △0.5 ◎specific ■feature ◇0.9 ↻1     ← WRONG: "specific"/"feature" not in enum
§ intel: △0.5 ◎surg ■bug ◇0.7 ↻1      ← WRONG: "intel:" prefix breaks the parser
```

## Valid Examples

```
§ △0.9 ◎surg ■bug ◇0.6 ↻4
§ △0.1 ◎const ■feat ◇0.9 ↻1
§ △0.3 ◎expl ■research ◇0.4 ↻1
§ △0.5 ■feat                            ← OK: partial (only fields you're confident about)
```

## When to Emit

- **At response start** — first line (automatically stripped from output)
- **Partial OK** — omit fields you're unsure about
- **Skip if no signal** — don't guess
