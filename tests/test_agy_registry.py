"""Tests for registry routing and reviewer label for agy provider (Story #72)."""

from unittest.mock import patch


from pacemaker.inference.provider import ProviderError
from pacemaker.inference.model_aliases import KNOWN_MODELS


class TestAgyInKnownModels:
    """Tests that all agy tokens appear in KNOWN_MODELS."""

    def test_agy_bare_in_known_models(self):
        assert "agy" in KNOWN_MODELS

    def test_agy_flash_in_known_models(self):
        assert "agy-flash" in KNOWN_MODELS

    def test_agy_flash_low_in_known_models(self):
        assert "agy-flash-low" in KNOWN_MODELS

    def test_agy_flash_medium_in_known_models(self):
        assert "agy-flash-medium" in KNOWN_MODELS

    def test_agy_flash_high_in_known_models(self):
        assert "agy-flash-high" in KNOWN_MODELS

    def test_agy_pro_in_known_models(self):
        assert "agy-pro" in KNOWN_MODELS

    def test_agy_pro_low_in_known_models(self):
        assert "agy-pro-low" in KNOWN_MODELS

    def test_agy_pro_high_in_known_models(self):
        assert "agy-pro-high" in KNOWN_MODELS

    def test_agy_gpt_oss_in_known_models(self):
        assert "agy-gpt-oss" in KNOWN_MODELS

    def test_agy_sonnet_in_known_models(self):
        assert "agy-sonnet" in KNOWN_MODELS

    def test_agy_opus_in_known_models(self):
        assert "agy-opus" in KNOWN_MODELS


class TestGetProviderAgy:
    """Tests that get_provider returns AgyProvider for agy tokens."""

    def test_get_provider_agy_bare_returns_agy_provider(self):
        from pacemaker.inference.registry import get_provider
        from pacemaker.inference.agy_provider import AgyProvider

        provider = get_provider("agy")
        assert isinstance(provider, AgyProvider)

    def test_get_provider_agy_flash_high_returns_agy_provider(self):
        from pacemaker.inference.registry import get_provider
        from pacemaker.inference.agy_provider import AgyProvider

        provider = get_provider("agy-flash-high")
        assert isinstance(provider, AgyProvider)

    def test_get_provider_agy_pro_returns_agy_provider(self):
        from pacemaker.inference.registry import get_provider
        from pacemaker.inference.agy_provider import AgyProvider

        provider = get_provider("agy-pro")
        assert isinstance(provider, AgyProvider)

    def test_get_provider_agy_gpt_oss_returns_agy_provider(self):
        from pacemaker.inference.registry import get_provider
        from pacemaker.inference.agy_provider import AgyProvider

        provider = get_provider("agy-gpt-oss")
        assert isinstance(provider, AgyProvider)

    def test_get_provider_agy_sonnet_returns_agy_provider(self):
        from pacemaker.inference.registry import get_provider
        from pacemaker.inference.agy_provider import AgyProvider

        provider = get_provider("agy-sonnet")
        assert isinstance(provider, AgyProvider)

    def test_get_provider_agy_opus_returns_agy_provider(self):
        from pacemaker.inference.registry import get_provider
        from pacemaker.inference.agy_provider import AgyProvider

        provider = get_provider("agy-opus")
        assert isinstance(provider, AgyProvider)


class TestResolveAndCallWithReviewerAgy:
    """Tests that resolve_and_call_with_reviewer returns correct agy reviewer label."""

    def test_agy_flash_high_returns_reviewer_label_agy_flash_high(self):
        """reviewer label must equal the hook_model string for agy providers."""
        from pacemaker.inference.registry import resolve_and_call_with_reviewer
        from pacemaker.inference.agy_provider import AgyProvider

        with patch.object(AgyProvider, "query", return_value="APPROVED"):
            response, reviewer = resolve_and_call_with_reviewer(
                "agy-flash-high", "test prompt", "sys prompt", "intent_validation"
            )
        assert response == "APPROVED"
        assert reviewer == "agy-flash-high"

    def test_agy_bare_returns_reviewer_label_agy(self):
        """reviewer label for bare 'agy' must be 'agy'."""
        from pacemaker.inference.registry import resolve_and_call_with_reviewer
        from pacemaker.inference.agy_provider import AgyProvider

        with patch.object(AgyProvider, "query", return_value="BLOCKED"):
            response, reviewer = resolve_and_call_with_reviewer(
                "agy", "test prompt", "", "intent_validation"
            )
        assert response == "BLOCKED"
        assert reviewer == "agy"

    def test_agy_pro_high_returns_reviewer_label_agy_pro_high(self):
        from pacemaker.inference.registry import resolve_and_call_with_reviewer
        from pacemaker.inference.agy_provider import AgyProvider

        with patch.object(AgyProvider, "query", return_value="APPROVED"):
            response, reviewer = resolve_and_call_with_reviewer(
                "agy-pro-high", "prompt", "", "stage2_unified"
            )
        assert reviewer == "agy-pro-high"

    def test_agy_provider_error_falls_back_to_anthropic_sdk(self):
        """When AgyProvider raises ProviderError, fallback returns anthropic-sdk reviewer."""
        from pacemaker.inference.registry import resolve_and_call_with_reviewer
        from pacemaker.inference.agy_provider import AgyProvider
        from pacemaker.inference.anthropic_provider import AnthropicProvider

        with patch.object(
            AgyProvider, "query", side_effect=ProviderError("agy not installed")
        ):
            with patch.object(
                AnthropicProvider, "query", return_value="fallback response"
            ):
                response, reviewer = resolve_and_call_with_reviewer(
                    "agy-flash", "prompt", "", "intent_validation"
                )
        assert response == "fallback response"
        assert reviewer == "anthropic-sdk"

    def test_agy_model_hint_passed_through_to_provider(self):
        """model_hint passed to AgyProvider.query must equal the hook_model value."""
        from pacemaker.inference.registry import resolve_and_call_with_reviewer
        from pacemaker.inference.agy_provider import AgyProvider

        captured_hints = []

        def capturing_query(
            prompt, system_prompt="", model_hint="", max_thinking_tokens=4000
        ):
            captured_hints.append(model_hint)
            return "APPROVED"

        with patch.object(AgyProvider, "query", side_effect=capturing_query):
            resolve_and_call_with_reviewer(
                "agy-flash-high", "prompt", "", "intent_validation"
            )

        assert captured_hints == ["agy-flash-high"]
