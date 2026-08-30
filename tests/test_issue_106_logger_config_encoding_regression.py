#!/usr/bin/env python3
"""
Issue #106 review regression (comment 5466550628): reproduce the exact
call-chain crash the rejected fix pass introduced.

hook.load_config() catches ANY exception raised while reading a corrupted
config file and calls log_warning() to record it -- but log_warning() ->
log() -> _get_log_level() independently RE-READS DEFAULT_CONFIG_PATH on its
own. Before this fix, if that same config file contained bytes that are not
valid UTF-8, json.load() raised UnicodeDecodeError on the second read too --
and because _get_log_level()'s except clause was narrowed to
`(OSError, json.JSONDecodeError)`, UnicodeDecodeError (a ValueError
subclass, not a JSONDecodeError) escaped uncaught. That second,
uncaught UnicodeDecodeError happened INSIDE hook.load_config()'s own
`except Exception as e:` block (not wrapped in a nested try), so it
propagated straight out of load_config() itself -- load_config() never
reached its `return DEFAULT_CONFIG.copy()` fallback. Since load_config() is
the first call in every hook handler, a non-UTF-8 config file crashed every
hook at entry -- the exact failure mode #106 was filed to eliminate, but now
total instead of partial.

This test deliberately does NOT mock log_warning (unlike
tests/test_logging_integration.py's test_hook_load_config_logs_on_error),
because mocking log_warning is precisely what let the original #106 fix
pass ship with the regression unnoticed -- the mock replaces the entire
real call chain, so the second, independent config read inside
_get_log_level() never executes under test. This test uses the same
path-redirection test-isolation technique already established by
tests/test_logger.py's temp_log_dir fixture (monkeypatch.setattr on
DEFAULT_CONFIG_PATH/DEFAULT_LOG_DIR, monkeypatch.delenv on
PACEMAKER_TEST_MODE) -- real file I/O against redirected temp paths, not a
mock of the code under test.
"""

from pacemaker import hook, logger


class TestIssue106LoadConfigSurvivesNonUtf8CallChain:
    """Full real call-chain reproduction: hook.load_config() -> real
    log_warning() -> real log() -> real _get_log_level(), all pointed at
    the SAME non-UTF-8 config file, matching the production scenario where
    DEFAULT_CONFIG_PATH itself is the corrupted file every hook reads."""

    def test_load_config_returns_defaults_without_raising(self, monkeypatch, tmp_path):
        bad_config = tmp_path / "config.json"
        bad_config.write_bytes(b"\xff\xfe\x00\x01not-valid-utf8-log_level")

        # Allow log_warning() to execute for real -- conftest.py's autouse
        # fixture sets PACEMAKER_TEST_MODE=1 globally, which makes
        # logger.log() return before ever calling _get_log_level(),
        # exactly the condition that hid this regression from the test
        # suite in the first place.
        monkeypatch.delenv("PACEMAKER_TEST_MODE", raising=False)

        # _get_log_level() independently re-reads DEFAULT_CONFIG_PATH from
        # the logger module's own globals -- point it at the SAME
        # non-UTF-8 file used above to reproduce the real chain.
        monkeypatch.setattr(logger, "DEFAULT_CONFIG_PATH", str(bad_config))
        # Give logger a writable temp log dir so a successful post-fix
        # log() write doesn't touch the real ~/.claude-pace-maker
        # directory.
        monkeypatch.setattr(logger, "DEFAULT_LOG_DIR", str(tmp_path))

        result = hook.load_config(str(bad_config))  # must not raise

        assert result == hook.DEFAULT_CONFIG.copy()
