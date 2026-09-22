"""Issue #147: AnthropicProvider must isolate the nested Claude Code session.

Without isolation, every reviewer call (haiku/sonnet/opus via claude_agent_sdk)
spawns a full user Claude Code session that loads the user's own hooks, MCP
servers, and plugins — costing 20-49s per call instead of ~3-5s, and causing
reviewer timeouts (root cause of #142, #145).

Fix: ClaudeAgentOptions must be built with setting_sources=[], strict_mcp_config=True,
and mcp_servers={} for BOTH the primary call and the limit-error fallback call, via a
single shared helper so the two constructions cannot diverge.

No real claude_agent_sdk/CLI calls are made here — claude_agent_sdk and
claude_agent_sdk.types are replaced with fake modules (same technique as
TestAnthropicProviderEffortLevel in tests/unit/test_inference_provider.py), and
the conftest.py autouse guard additionally blocks any real subprocess call to
codex/gemini/claude in non-e2e tests.
"""

import sys
import types


def _install_fake_sdk(stub_query, stub_options_cls, stub_result_cls):
    """Install fake claude_agent_sdk / claude_agent_sdk.types modules.

    Returns the saved originals so the caller can restore them in a finally block —
    mirrors the exact pattern used by TestAnthropicProviderEffortLevel.
    """
    fake_sdk = types.ModuleType("claude_agent_sdk")
    fake_sdk.query = stub_query
    fake_types = types.ModuleType("claude_agent_sdk.types")
    fake_types.ClaudeAgentOptions = stub_options_cls
    fake_types.ResultMessage = stub_result_cls
    fake_sdk.types = fake_types

    saved_sdk = sys.modules.get("claude_agent_sdk")
    saved_types = sys.modules.get("claude_agent_sdk.types")
    sys.modules["claude_agent_sdk"] = fake_sdk
    sys.modules["claude_agent_sdk.types"] = fake_types
    return saved_sdk, saved_types


def _restore_sdk(saved_sdk, saved_types):
    if saved_sdk is not None:
        sys.modules["claude_agent_sdk"] = saved_sdk
    else:
        sys.modules.pop("claude_agent_sdk", None)
    if saved_types is not None:
        sys.modules["claude_agent_sdk.types"] = saved_types
    else:
        sys.modules.pop("claude_agent_sdk.types", None)


class TestPrimaryPathIsolation:
    """Primary ClaudeAgentOptions construction must carry the isolation fields."""

    def test_primary_path_sets_isolation_fields(self):
        """Primary options must have setting_sources=[], strict_mcp_config=True, mcp_servers={}."""
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        recorded_kwargs: list[dict] = []

        class StubOptions:
            def __init__(self, **kwargs):
                recorded_kwargs.append(kwargs)

        class StubResult:
            result = "APPROVED"

        async def stub_query(prompt, options):
            yield StubResult()

        saved_sdk, saved_types = _install_fake_sdk(stub_query, StubOptions, StubResult)
        try:
            provider = AnthropicProvider()
            result = provider.query("hi", "sys", "opus", 1024)
        finally:
            _restore_sdk(saved_sdk, saved_types)

        assert result == "APPROVED"
        assert len(recorded_kwargs) >= 1, "FreshOptions was never constructed"
        primary_kwargs = recorded_kwargs[0]
        assert (
            primary_kwargs.get("setting_sources") == []
        ), f"Expected setting_sources=[] in primary FreshOptions, got: {primary_kwargs}"
        assert (
            primary_kwargs.get("strict_mcp_config") is True
        ), f"Expected strict_mcp_config=True in primary FreshOptions, got: {primary_kwargs}"
        assert (
            primary_kwargs.get("mcp_servers") == {}
        ), f"Expected mcp_servers={{}} in primary FreshOptions, got: {primary_kwargs}"

    def test_primary_path_preserves_existing_fields(self):
        """Isolation must not disturb model/effort/max_thinking_tokens/system_prompt/disallowed_tools."""
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        recorded_kwargs: list[dict] = []

        class StubOptions:
            def __init__(self, **kwargs):
                recorded_kwargs.append(kwargs)

        class StubResult:
            result = "APPROVED"

        async def stub_query(prompt, options):
            yield StubResult()

        saved_sdk, saved_types = _install_fake_sdk(stub_query, StubOptions, StubResult)
        try:
            provider = AnthropicProvider()
            provider.query("hi", "you are a reviewer", "opus", 2048)
        finally:
            _restore_sdk(saved_sdk, saved_types)

        primary_kwargs = recorded_kwargs[0]
        assert primary_kwargs.get("model") == "opus"
        assert primary_kwargs.get("effort") == "high"
        assert primary_kwargs.get("max_thinking_tokens") == 2048
        assert primary_kwargs.get("system_prompt") == "you are a reviewer"
        assert primary_kwargs.get("disallowed_tools") == [
            "Write",
            "Edit",
            "Bash",
            "TodoWrite",
            "Read",
            "Grep",
            "Glob",
        ]


class TestFallbackPathIsolation:
    """Limit-error fallback ClaudeAgentOptions construction must also carry isolation fields."""

    def test_fallback_path_sets_isolation_fields(self):
        """Fallback options (triggered by usage-limit response) must also isolate the session."""
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        recorded_kwargs: list[dict] = []

        class StubOptions:
            def __init__(self, **kwargs):
                recorded_kwargs.append(kwargs)

        call_count = 0

        class StubResult:
            def __init__(self, text):
                self.result = text

        async def stub_query(prompt, options):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield StubResult("usage limit reached, resets soon")
            else:
                yield StubResult("APPROVED")

        saved_sdk, saved_types = _install_fake_sdk(stub_query, StubOptions, StubResult)
        try:
            provider = AnthropicProvider()
            result = provider.query("hi", "sys", "sonnet", 1024)
        finally:
            _restore_sdk(saved_sdk, saved_types)

        assert result == "APPROVED"
        assert (
            len(recorded_kwargs) == 2
        ), f"Expected 2 FreshOptions constructions (primary + fallback), got {len(recorded_kwargs)}"
        fallback_kwargs = recorded_kwargs[1]
        assert (
            fallback_kwargs.get("setting_sources") == []
        ), f"Expected setting_sources=[] in fallback FreshOptions, got: {fallback_kwargs}"
        assert (
            fallback_kwargs.get("strict_mcp_config") is True
        ), f"Expected strict_mcp_config=True in fallback FreshOptions, got: {fallback_kwargs}"
        assert (
            fallback_kwargs.get("mcp_servers") == {}
        ), f"Expected mcp_servers={{}} in fallback FreshOptions, got: {fallback_kwargs}"
        assert fallback_kwargs.get("model") == "opus"
        assert fallback_kwargs.get("effort") == "high"

    def test_primary_and_fallback_isolation_cannot_diverge(self):
        """Both calls must be built via the same helper — assert identical isolation subsets."""
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        recorded_kwargs: list[dict] = []

        class StubOptions:
            def __init__(self, **kwargs):
                recorded_kwargs.append(kwargs)

        call_count = 0

        class StubResult:
            def __init__(self, text):
                self.result = text

        async def stub_query(prompt, options):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield StubResult("usage limit reached, resets soon")
            else:
                yield StubResult("APPROVED")

        saved_sdk, saved_types = _install_fake_sdk(stub_query, StubOptions, StubResult)
        try:
            provider = AnthropicProvider()
            provider.query("hi", "sys", "sonnet", 1024)
        finally:
            _restore_sdk(saved_sdk, saved_types)

        assert len(recorded_kwargs) == 2
        isolation_keys = ("setting_sources", "strict_mcp_config", "mcp_servers")
        primary_isolation = {k: recorded_kwargs[0].get(k) for k in isolation_keys}
        fallback_isolation = {k: recorded_kwargs[1].get(k) for k in isolation_keys}
        assert (
            primary_isolation
            == fallback_isolation
            == {
                "setting_sources": [],
                "strict_mcp_config": True,
                "mcp_servers": {},
            }
        )
