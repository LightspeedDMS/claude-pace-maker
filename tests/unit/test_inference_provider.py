#!/usr/bin/env python3
"""
Unit tests for InferenceProvider abstract base class and ProviderError.

Tests:
- ProviderError is a catchable Exception subclass
- InferenceProvider cannot be instantiated directly (abstract)
- Concrete subclass with query() is instantiable
"""

import subprocess
from unittest.mock import patch, MagicMock


class TestProviderError:
    def test_provider_error_is_exception(self):
        """ProviderError must be a catchable Exception subclass."""
        from pacemaker.inference.provider import ProviderError

        err = ProviderError("test message")
        assert isinstance(err, Exception)

    def test_provider_error_carries_message(self):
        """ProviderError must carry the message string."""
        from pacemaker.inference.provider import ProviderError

        err = ProviderError("something went wrong")
        assert "something went wrong" in str(err)

    def test_provider_error_can_be_caught_as_exception(self):
        """ProviderError must be catchable via bare Exception clause."""
        from pacemaker.inference.provider import ProviderError

        caught = False
        try:
            raise ProviderError("oops")
        except Exception:
            caught = True
        assert caught


class TestInferenceProviderAbstract:
    def test_cannot_instantiate_inference_provider_directly(self):
        """InferenceProvider is abstract — direct instantiation must raise TypeError."""
        from pacemaker.inference.provider import InferenceProvider

        raised = False
        try:
            InferenceProvider()
        except TypeError:
            raised = True
        assert raised

    def test_concrete_subclass_without_query_is_abstract(self):
        """Subclass that does not implement query() must also be non-instantiable."""
        from pacemaker.inference.provider import InferenceProvider

        class Incomplete(InferenceProvider):
            pass

        raised = False
        try:
            Incomplete()
        except TypeError:
            raised = True
        assert raised

    def test_concrete_subclass_with_query_is_instantiable(self):
        """Subclass that implements query() must be instantiable."""
        from pacemaker.inference.provider import InferenceProvider

        class Complete(InferenceProvider):
            def query(
                self,
                prompt,
                system_prompt=None,
                model_hint=None,
                max_thinking_tokens=None,
            ):
                return "result"

        instance = Complete()
        assert instance is not None

    def test_concrete_query_method_returns_string(self):
        """Concrete query() must return a string."""
        from pacemaker.inference.provider import InferenceProvider

        class Echo(InferenceProvider):
            def query(
                self,
                prompt,
                system_prompt=None,
                model_hint=None,
                max_thinking_tokens=None,
            ):
                return prompt

        instance = Echo()
        result = instance.query("hello")
        assert result == "hello"


class TestCodexProvider:
    """Tests for CodexProvider subprocess integration."""

    def test_codex_provider_returns_stdout(self):
        """CodexProvider should return stripped stdout on success."""
        from pacemaker.inference.codex_provider import CodexProvider

        provider = CodexProvider()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "  YES  \n"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = provider.query("test prompt", "system prompt", "o3", 4000)

        assert result == "YES"
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0] == ["codex", "exec", "-", "-m", "o3", "-s", "read-only"]
        assert "SYSTEM INSTRUCTIONS:" in call_args[1]["input"]
        assert "test prompt" in call_args[1]["input"]

    def test_codex_provider_embeds_system_prompt(self):
        """CodexProvider should embed system prompt when provided."""
        from pacemaker.inference.codex_provider import CodexProvider

        provider = CodexProvider()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "NO"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            provider.query("user question", "be strict", "o3", 4000)

        input_text = mock_run.call_args[1]["input"]
        assert "SYSTEM INSTRUCTIONS:\nbe strict" in input_text
        assert "USER REQUEST:\nuser question" in input_text

    def test_codex_provider_no_system_prompt(self):
        """CodexProvider should pass raw prompt when no system prompt."""
        from pacemaker.inference.codex_provider import CodexProvider

        provider = CodexProvider()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "YES"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            provider.query("raw prompt", "", "o3", 4000)

        input_text = mock_run.call_args[1]["input"]
        assert input_text == "raw prompt"

    def test_codex_provider_raises_on_nonzero_exit(self):
        """CodexProvider should raise ProviderError on non-zero exit."""
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.provider import ProviderError

        provider = CodexProvider()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "model not found"

        with patch("subprocess.run", return_value=mock_result):
            raised = False
            try:
                provider.query("test", "", "bad-model", 4000)
            except ProviderError as e:
                raised = True
                assert "exit 1" in str(e)
            assert raised

    def test_codex_provider_raises_on_timeout(self):
        """CodexProvider should raise ProviderError on timeout."""
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.provider import ProviderError

        provider = CodexProvider()

        with patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired("codex", 120)
        ):
            raised = False
            try:
                provider.query("test", "", "o3", 4000)
            except ProviderError as e:
                raised = True
                assert "timed out" in str(e)
            assert raised

    def test_codex_provider_raises_on_not_found(self):
        """CodexProvider should raise ProviderError if codex not installed."""
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.provider import ProviderError

        provider = CodexProvider()

        with patch("subprocess.run", side_effect=FileNotFoundError("codex")):
            raised = False
            try:
                provider.query("test", "", "o3", 4000)
            except ProviderError as e:
                raised = True
                assert "not found" in str(e).lower()
            assert raised

    def test_codex_provider_raises_on_empty_response(self):
        """CodexProvider should raise ProviderError on empty stdout."""
        from pacemaker.inference.codex_provider import CodexProvider
        from pacemaker.inference.provider import ProviderError

        provider = CodexProvider()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "   \n"

        with patch("subprocess.run", return_value=mock_result):
            raised = False
            try:
                provider.query("test", "", "o3", 4000)
            except ProviderError as e:
                raised = True
                assert "empty" in str(e).lower()
            assert raised

    def test_codex_provider_default_model_is_o3(self):
        """CodexProvider should default to o3 when model_hint is empty."""
        from pacemaker.inference.codex_provider import CodexProvider

        provider = CodexProvider()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            provider.query("test", "", "", 4000)

        cmd = mock_run.call_args[0][0]
        assert "-m" in cmd
        model_idx = cmd.index("-m")
        assert cmd[model_idx + 1] == "o3"


class TestRegistry:
    """Tests for provider registry and orchestrator."""

    def test_get_provider_auto_returns_anthropic(self):
        """get_provider('auto') should return AnthropicProvider."""
        from pacemaker.inference.registry import get_provider
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        provider = get_provider("auto")
        assert isinstance(provider, AnthropicProvider)

    def test_get_provider_sonnet_returns_anthropic(self):
        """get_provider('sonnet') should return AnthropicProvider."""
        from pacemaker.inference.registry import get_provider
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        assert isinstance(get_provider("sonnet"), AnthropicProvider)

    def test_get_provider_opus_returns_anthropic(self):
        """get_provider('opus') should return AnthropicProvider."""
        from pacemaker.inference.registry import get_provider
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        assert isinstance(get_provider("opus"), AnthropicProvider)

    def test_get_provider_gpt5_returns_codex(self):
        """get_provider('gpt-5') should return CodexProvider."""
        from pacemaker.inference.registry import get_provider
        from pacemaker.inference.codex_provider import CodexProvider

        assert isinstance(get_provider("gpt-5"), CodexProvider)

    def test_get_provider_unknown_falls_back_to_anthropic(self):
        """get_provider with unknown value should fall back to AnthropicProvider."""
        from pacemaker.inference.registry import get_provider
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        assert isinstance(get_provider("banana"), AnthropicProvider)

    def test_resolve_model_auto_stop_hook(self):
        """Auto mode for stop_hook should resolve to sonnet."""
        from pacemaker.inference.registry import resolve_model_for_call

        assert resolve_model_for_call("auto", "stop_hook") == "sonnet"

    def test_resolve_model_auto_stage2(self):
        """Auto mode for stage2_unified should resolve to opus."""
        from pacemaker.inference.registry import resolve_model_for_call

        assert resolve_model_for_call("auto", "stage2_unified") == "opus"

    def test_resolve_model_explicit_passes_through(self):
        """Explicit model should pass through regardless of call context."""
        from pacemaker.inference.registry import resolve_model_for_call

        assert resolve_model_for_call("opus", "stage1") == "opus"
        assert resolve_model_for_call("gpt-5", "stop_hook") == "gpt-5"

    def test_resolve_and_call_success(self):
        """resolve_and_call should return provider response on success."""
        from pacemaker.inference.registry import resolve_and_call

        with patch("pacemaker.inference.registry.get_provider") as mock_get:
            mock_provider = MagicMock()
            mock_provider.query.return_value = "YES"
            mock_get.return_value = mock_provider

            result = resolve_and_call("auto", "prompt", "sys", "stage1", 4000)

        assert result == "YES"

    def test_resolve_and_call_fallback_on_provider_error(self):
        """resolve_and_call should fall back to Anthropic when non-auto provider fails."""
        from pacemaker.inference.registry import resolve_and_call
        from pacemaker.inference.provider import ProviderError

        mock_primary = MagicMock()
        mock_primary.query.side_effect = ProviderError("codex failed")

        mock_fallback = MagicMock()
        mock_fallback.query.return_value = "FALLBACK_YES"

        with patch(
            "pacemaker.inference.registry.get_provider", return_value=mock_primary
        ):
            with patch(
                "pacemaker.inference.anthropic_provider.AnthropicProvider",
                return_value=mock_fallback,
            ):
                result = resolve_and_call("gpt-5", "prompt", "sys", "stage1", 4000)

        assert result == "FALLBACK_YES"

    def test_resolve_and_call_fail_open_when_both_fail(self):
        """resolve_and_call should return empty string when both providers fail."""
        from pacemaker.inference.registry import resolve_and_call
        from pacemaker.inference.provider import ProviderError

        mock_provider = MagicMock()
        mock_provider.query.side_effect = ProviderError("failed")

        with patch(
            "pacemaker.inference.registry.get_provider", return_value=mock_provider
        ):
            with patch(
                "pacemaker.inference.anthropic_provider.AnthropicProvider",
                return_value=mock_provider,
            ):
                result = resolve_and_call("gpt-5", "prompt", "sys", "stage1", 4000)

        assert result == ""

    def test_resolve_and_call_auto_fail_open_no_secondary(self):
        """resolve_and_call with auto should fail-open directly (no secondary attempt)."""
        from pacemaker.inference.registry import resolve_and_call
        from pacemaker.inference.provider import ProviderError

        mock_provider = MagicMock()
        mock_provider.query.side_effect = ProviderError("failed")

        with patch(
            "pacemaker.inference.registry.get_provider", return_value=mock_provider
        ):
            result = resolve_and_call("auto", "prompt", "sys", "stage1", 4000)

        assert result == ""
        # Should only call query once (no fallback attempt for auto)
        assert mock_provider.query.call_count == 1


class TestResolveAndCallWithReviewer:
    """Tests for resolve_and_call_with_reviewer() returning (response, reviewer) tuple."""

    def test_primary_success_returns_response_and_codex_reviewer(self):
        """resolve_and_call_with_reviewer with gpt-5 success returns ('YES', 'codex-gpt5').

        Patches CodexProvider.query directly so isinstance(provider, CodexProvider)
        returns True — confirming reviewer identity from the real provider type.
        """
        from pacemaker.inference.registry import resolve_and_call_with_reviewer
        from pacemaker.inference.codex_provider import CodexProvider

        with patch.object(CodexProvider, "query", return_value="YES"):
            response, reviewer = resolve_and_call_with_reviewer(
                "gpt-5", "prompt", "sys", "stage1", 4000
            )

        assert response == "YES"
        assert reviewer == "codex-gpt5"

    def test_auto_model_returns_anthropic_sdk_reviewer(self):
        """resolve_and_call_with_reviewer with auto returns reviewer 'anthropic-sdk'."""
        from pacemaker.inference.registry import resolve_and_call_with_reviewer

        with patch("pacemaker.inference.registry.get_provider") as mock_get:
            mock_provider = MagicMock()
            mock_provider.query.return_value = "APPROVED"
            mock_get.return_value = mock_provider

            response, reviewer = resolve_and_call_with_reviewer(
                "auto", "prompt", "sys", "stage1", 4000
            )

        assert response == "APPROVED"
        assert reviewer == "anthropic-sdk"

    def test_fallback_returns_anthropic_sdk_reviewer(self):
        """When codex fails and fallback succeeds, reviewer is 'anthropic-sdk'."""
        from pacemaker.inference.registry import resolve_and_call_with_reviewer
        from pacemaker.inference.provider import ProviderError

        mock_primary = MagicMock()
        mock_primary.query.side_effect = ProviderError("codex failed")

        mock_fallback = MagicMock()
        mock_fallback.query.return_value = "FALLBACK_YES"

        with patch(
            "pacemaker.inference.registry.get_provider", return_value=mock_primary
        ):
            with patch(
                "pacemaker.inference.anthropic_provider.AnthropicProvider",
                return_value=mock_fallback,
            ):
                with patch(
                    "pacemaker.inference.registry.get_latest_codex_usage",
                    return_value=None,
                ):
                    response, reviewer = resolve_and_call_with_reviewer(
                        "gpt-5", "prompt", "sys", "stage1", 4000
                    )

        assert response == "FALLBACK_YES"
        assert reviewer == "anthropic-sdk"

    def test_both_fail_returns_empty_and_unknown_reviewer(self):
        """When both providers fail, returns ('', 'unknown')."""
        from pacemaker.inference.registry import resolve_and_call_with_reviewer
        from pacemaker.inference.provider import ProviderError

        mock_provider = MagicMock()
        mock_provider.query.side_effect = ProviderError("failed")

        with patch(
            "pacemaker.inference.registry.get_provider", return_value=mock_provider
        ):
            with patch(
                "pacemaker.inference.anthropic_provider.AnthropicProvider",
                return_value=mock_provider,
            ):
                with patch(
                    "pacemaker.inference.registry.get_latest_codex_usage",
                    return_value=None,
                ):
                    response, reviewer = resolve_and_call_with_reviewer(
                        "gpt-5", "prompt", "sys", "stage1", 4000
                    )

        assert response == ""
        assert reviewer == "unknown"

    def test_sonnet_model_returns_anthropic_sdk_reviewer(self):
        """Explicit sonnet model returns 'anthropic-sdk' reviewer."""
        from pacemaker.inference.registry import resolve_and_call_with_reviewer

        with patch("pacemaker.inference.registry.get_provider") as mock_get:
            mock_provider = MagicMock()
            mock_provider.query.return_value = "YES"
            mock_get.return_value = mock_provider

            response, reviewer = resolve_and_call_with_reviewer(
                "sonnet", "prompt", "sys", "stage1", 4000
            )

        assert response == "YES"
        assert reviewer == "anthropic-sdk"

    def test_resolve_and_call_backward_compat_still_returns_str(self):
        """Original resolve_and_call still returns str (backward compatible)."""
        from pacemaker.inference.registry import resolve_and_call

        with patch("pacemaker.inference.registry.get_provider") as mock_get:
            mock_provider = MagicMock()
            mock_provider.query.return_value = "YES"
            mock_get.return_value = mock_provider

            result = resolve_and_call("auto", "prompt", "sys", "stage1", 4000)

        assert isinstance(result, str)
        assert result == "YES"


class TestCodexUsageRefreshOnFallback:
    """Tests for codex usage DB refresh when Codex fails and falls back."""

    def test_codex_fallback_triggers_usage_refresh(self):
        """When gpt-5 provider fails, get_latest_codex_usage is called."""
        from pacemaker.inference.registry import resolve_and_call_with_reviewer
        from pacemaker.inference.provider import ProviderError
        from pacemaker.inference.codex_provider import CodexProvider

        # Test double that IS a CodexProvider (passes isinstance) but raises on query
        class FailingCodexProvider(CodexProvider):
            def __init__(self):
                pass  # skip parent __init__

            def query(self, prompt, system_prompt, model_hint, max_thinking_tokens):
                raise ProviderError("codex exit 1")

        # Test double for fallback
        mock_fallback_instance = MagicMock()
        mock_fallback_instance.query.return_value = "YES"
        MockAnthropicProvider = MagicMock(return_value=mock_fallback_instance)

        with patch(
            "pacemaker.inference.codex_provider.CodexProvider", FailingCodexProvider
        ):
            with patch(
                "pacemaker.inference.anthropic_provider.AnthropicProvider",
                MockAnthropicProvider,
            ):
                with patch(
                    "pacemaker.inference.registry.get_latest_codex_usage"
                ) as mock_get_usage:
                    with patch(
                        "pacemaker.inference.registry.migrate_codex_usage_schema"
                    ):
                        mock_get_usage.return_value = None
                        resolve_and_call_with_reviewer(
                            "gpt-5", "prompt", "sys", "stage1", 4000
                        )

        mock_get_usage.assert_called_once()

    def test_codex_fallback_writes_usage_when_available(self):
        """When gpt-5 fails and session data exists, write_codex_usage is called."""
        from pacemaker.inference.registry import resolve_and_call_with_reviewer
        from pacemaker.inference.provider import ProviderError
        from pacemaker.inference.codex_provider import CodexProvider

        # Test double that IS a CodexProvider (passes isinstance) but raises on query
        class FailingCodexProvider(CodexProvider):
            def __init__(self):
                pass  # skip parent __init__

            def query(self, prompt, system_prompt, model_hint, max_thinking_tokens):
                raise ProviderError("codex empty response")

        # Test double for fallback
        mock_fallback_instance = MagicMock()
        mock_fallback_instance.query.return_value = "YES"
        MockAnthropicProvider = MagicMock(return_value=mock_fallback_instance)

        usage_data = {
            "primary_used_pct": 42.0,
            "secondary_used_pct": 10.0,
            "timestamp": 9999999.0,
        }

        with patch(
            "pacemaker.inference.codex_provider.CodexProvider", FailingCodexProvider
        ):
            with patch(
                "pacemaker.inference.anthropic_provider.AnthropicProvider",
                MockAnthropicProvider,
            ):
                with patch(
                    "pacemaker.inference.registry.get_latest_codex_usage",
                    return_value=usage_data,
                ):
                    with patch(
                        "pacemaker.inference.registry.write_codex_usage"
                    ) as mock_write:
                        with patch(
                            "pacemaker.inference.registry.migrate_codex_usage_schema"
                        ):
                            resolve_and_call_with_reviewer(
                                "gpt-5", "prompt", "sys", "stage1", 4000
                            )

        mock_write.assert_called_once()

    def test_non_codex_model_does_not_refresh_usage(self):
        """When hook_model is 'auto', no codex usage refresh is attempted."""
        from pacemaker.inference.registry import resolve_and_call_with_reviewer
        from pacemaker.inference.provider import ProviderError

        mock_provider = MagicMock()
        mock_provider.query.side_effect = ProviderError("auto failed")

        with patch(
            "pacemaker.inference.registry.get_provider", return_value=mock_provider
        ):
            with patch(
                "pacemaker.inference.registry.get_latest_codex_usage"
            ) as mock_get_usage:
                resolve_and_call_with_reviewer("auto", "prompt", "sys", "stage1", 4000)

        mock_get_usage.assert_not_called()

    def test_codex_success_does_not_refresh_usage(self):
        """When gpt-5 succeeds, no codex usage refresh is triggered."""
        from pacemaker.inference.registry import resolve_and_call_with_reviewer

        with patch("pacemaker.inference.registry.get_provider") as mock_get:
            mock_provider = MagicMock()
            mock_provider.query.return_value = "YES"
            mock_get.return_value = mock_provider

            with patch(
                "pacemaker.inference.registry.get_latest_codex_usage"
            ) as mock_get_usage:
                resolve_and_call_with_reviewer("gpt-5", "prompt", "sys", "stage1", 4000)

        mock_get_usage.assert_not_called()


class TestConfigDefault:
    """Tests for hook_model config default."""

    def test_default_config_has_hook_model(self):
        """DEFAULT_CONFIG must include hook_model key."""
        from pacemaker.constants import DEFAULT_CONFIG

        assert "hook_model" in DEFAULT_CONFIG

    def test_default_hook_model_is_auto(self):
        """Default hook_model must be 'auto'."""
        from pacemaker.constants import DEFAULT_CONFIG

        assert DEFAULT_CONFIG["hook_model"] == "auto"
