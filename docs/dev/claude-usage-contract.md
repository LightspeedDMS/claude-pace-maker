# Cross-Process Contract with the claude-usage Monitor

Read this before changing any table, column or file that the monitor reads, or before writing a new monitor reader.

"claude usage" / "claude-usage" means the repo `LightspeedDMS/claude-usage`, cloned at `~/Dev/claude-usage`.

- Its `claude_usage/code_mode/pacemaker_integration.py` reads `~/.claude-pace-maker/usage.db` and `session_registry.db` directly over SQLite. The `agents` and `agent_actions` tables are live and used by `get_active_agent_tree()`.
- It reads `config.json`.
- It imports `UsageModel` in-process through `install_source`.

There is no other IPC between the monitor and pace-maker. The consumer hardcodes `Path.home() / ".claude-pace-maker"`.

## `profile_cache.json` and credentials (#177)

- The monitor reads `~/.claude-pace-maker/profile_cache.json` (JSON). It does **not** read the SQLite `profile_cache` table, which has no writer in `src/pacemaker`; `UsageModel._detect_tier` no longer reads it either. The table stays in `database.py` `SCHEMA` (no schema change).
- File shape: `{"profile": {...}, "timestamp": <epoch>, "token_fingerprint": "<16 hex>"}`. `token_fingerprint` is **additive** (top-level, next to `profile` and `timestamp`): the first 16 hex chars of `sha256(accessToken UTF-8)`, written by `cache_profile(..., access_token=...)`. Credentials carry no account id, so this is the account-match key. Never store the token itself.
- Reader rule (identical in both tools; pace-maker: `load_cached_profile(for_current_account=True)`, used by `UsageModel._detect_tier`): when the cache has a `token_fingerprint` that **differs** from the fingerprint of the current access token, ignore the cached profile (pace-maker then falls back to the default tier "5x"). When the key is **missing**, the reader accepts the cache: claude-usage applies only its age check (24 h), and pace-maker's reader accepts it too, but `refresh_profile_if_needed()` treats a missing key as stale and refetches, so pace-maker rewrites the file with a fingerprint on its next poll. When no current token is available, accept the cache. The current token is only loaded when the cache has a fingerprint.
- Refresh rule (pace-maker): after a successful usage fetch, `refresh_profile_if_needed()` refetches the profile when the cache is missing, older than config `profile_cache_max_age_seconds` (default 3600), or its fingerprint differs from the current token's or is missing.
- Credentials rule shared with the monitor: on Darwin the Keychain item `Claude Code-credentials` is the source of truth and `~/.claude/.credentials.json` is only a cache, used when the Keychain read fails (pace-maker logs a warning). On Linux only the file is used. Pace-maker only reads credentials; it never writes the file.
- Tier detection (`fallback.detect_tier`) uses `organization.rate_limit_tier` (`default_claude_max_20x` -> 20x, `default_claude_max_5x` -> 5x) and falls back to `account.has_claude_max` only when the tier is absent.

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
