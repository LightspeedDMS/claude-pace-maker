# Cross-Process Contract with the claude-usage Monitor

Read this before changing any table, column or file that the monitor reads, or before writing a new monitor reader.

"claude usage" / "claude-usage" means `/home/jsbattig/Dev/claude-usage-reporting`.

- Its `claude_usage/code_mode/pacemaker_integration.py` reads `~/.claude-pace-maker/usage.db` and `session_registry.db` directly over SQLite. The `agents` and `agent_actions` tables are live and used by `get_active_agent_tree()`.
- It reads `config.json`.
- It imports `UsageModel` in-process through `install_source`.

There is no other IPC between the monitor and pace-maker. The consumer hardcodes `Path.home() / ".claude-pace-maker"`.

## Producer rules (this repo)

- Only additive `ALTER TABLE` with idempotent migrations (catch `duplicate column name`; example: `migrate_codex_usage_schema()`).
- Never drop or rename tables or columns: the consumer tolerates missing columns but not missing tables (the whole read returns `None`). Removing a table needs a coordinated migration on both sides in the same release. Record any new table the monitor reads in this file. `session_registry.db`'s `agents` / `agent_actions` are such a live contract.
- **If you change `database.py`'s `SCHEMA`, bump `SCHEMA_VERSION`** and update the pinned hash in `tests/test_database_lock_contention_145.py`. `initialize_database()` trusts the version number and skips DDL.
- `execute_with_retry` (`MAX_RETRIES=3`) only sleeps 100 ms and then 200 ms; there is no 400 ms step.
- `codex_usage.py` stores `limit_id` verbatim (PAYG rows have null `primary`/`secondary`, which `_parse_last_token_count()` tolerates). The PAYG interpretation (`premium`) lives in the monitor's `display.py`.
- Blockage categories: add new ones to the monitor's `KNOWN_BLOCKAGE_CATEGORIES` too (`pacemaker_integration.py`). Unknown ones still count but get a humanized label.
- Activity-event codes: the monitor's `display.py` `_ACTIVITY_GROUPS` and icon map are hardcoded. `DG` and `RS` are there; a new code must be added there too or it is not shown.

## Consumer read rules (for new monitor readers)

1. `.exists()` before `sqlite3.connect()`.
2. `timeout=DB_TIMEOUT` (5 s), then `PRAGMA journal_mode=WAL`.
3. Read optional columns via `"col" in row.keys()`. Check for `row is None` after `fetchone()`.
4. Close in a `try/finally`.
5. Catch `(sqlite3.Error, OSError)`, `logging.debug` the reason and return `None`. Never raise.
6. No retries (the 5 s timeout is the circuit breaker; retries would compound it) and no schema-version checks. Caching uses a manual TTL (see `get_blockage_stats_cached()`).
7. Reads are reactive (called per render tick); no background polling.

`_fetch_blockage_rows()` (behind `get_blockage_stats()`) is a correct windowed-aggregate example: `DB_TIMEOUT`, WAL, close in `finally`.
