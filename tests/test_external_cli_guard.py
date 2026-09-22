"""
Verifies the real-external-call guard (tests/conftest.py's
`_ExternalCallGuard`, wired up by the autouse `_block_real_external_cli_calls`
fixture) actually detects a leaked, unmocked call -- including the exact
"caller swallows the immediate exception" scenario the issue #144
code-review follow-up flagged: a real call was never caught if the raising
provider's own broad exception handling swallowed the guard's immediate
RuntimeError.

These tests instantiate `_ExternalCallGuard` directly (not through the
autouse fixture) so the detection logic itself is exercised in isolation,
without needing a nested pytest subprocess just to prove a leak surfaces.
"""

import pytest

from conftest import _ExternalCallGuard


def test_subprocess_leak_is_recorded_and_raises_immediately():
    guard = _ExternalCallGuard()
    assert guard.leaked_calls == []

    with pytest.raises(RuntimeError, match="Real external CLI call"):
        guard.guarded_run(["codex", "exec"])

    assert guard.leaked_calls == ["subprocess.run(cmd=['codex', 'exec'])"]


def test_non_blocked_subprocess_call_is_not_recorded():
    guard = _ExternalCallGuard()
    # "echo" is not in _BLOCKED_CLI_NAMES -- must pass through untouched
    # and record nothing.
    result = guard.guarded_run(["echo", "hi"], capture_output=True, text=True)
    assert result.returncode == 0
    assert guard.leaked_calls == []


def test_check_passes_silently_when_nothing_leaked():
    guard = _ExternalCallGuard()
    guard.check()  # must not raise


def test_check_fails_after_a_recorded_leak():
    guard = _ExternalCallGuard()
    guard.leaked_calls.append("some-leak")
    with pytest.raises(pytest.fail.Exception):
        guard.check()


def test_sdk_leak_is_recorded_even_when_caller_swallows_the_raise(monkeypatch):
    """Reproduces the exact bug the code review found: AnthropicProvider
    wraps `claude_agent_sdk.query()` iteration in a broad `except
    Exception`, converting the guard's RuntimeError into an empty
    response and then a ProviderError -- so the immediate raise alone is
    silently absorbed. `guard.check()` (the fixture's teardown call) must
    still catch it via `leaked_calls`."""
    claude_agent_sdk = pytest.importorskip("claude_agent_sdk")
    from pacemaker.inference.anthropic_provider import AnthropicProvider
    from pacemaker.inference.provider import ProviderError

    guard = _ExternalCallGuard()
    # AnthropicProvider._query_async does
    # `from claude_agent_sdk import query as fresh_query` fresh on every
    # call, so patching the module attribute (rather than
    # anthropic_provider's own namespace) is what's actually exercised
    # in production.
    monkeypatch.setattr(claude_agent_sdk, "query", guard.guarded_sdk_query)

    expected_exception_type = ProviderError
    with pytest.raises(expected_exception_type, match="Empty response"):
        AnthropicProvider().query(prompt="hello", system_prompt="", model_hint="sonnet")

    assert guard.leaked_calls == [
        "claude_agent_sdk.query(...)"
    ], "guard must have recorded the leaked SDK call despite the swallowed raise"

    with pytest.raises(pytest.fail.Exception):
        guard.check()
