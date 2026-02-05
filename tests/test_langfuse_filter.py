#!/usr/bin/env python3
"""
Unit tests for langfuse filter module.

Tests data truncation, secret redaction, and filter configuration.
"""

import json
import pytest
from src.pacemaker.langfuse import filter as langfuse_filter
from src.pacemaker import user_commands


class TestTruncation:
    """Tests for AC1: Tool Result Truncation."""

    def test_truncate_small_output_no_change(self):
        """Output below threshold remains unchanged."""
        output = "Small output"
        result = langfuse_filter.truncate_output(output, max_bytes=10240)

        assert result == output
        assert "[TRUNCATED" not in result

    def test_truncate_large_output_at_threshold(self):
        """Output exceeding threshold is truncated."""
        large_output = "x" * 15000  # 15KB > 10KB default
        result = langfuse_filter.truncate_output(large_output, max_bytes=10240)

        # Should be truncated to 10240 bytes
        assert len(result.encode("utf-8")) <= 10240 + 100  # +100 for marker
        assert "[TRUNCATED - original size:" in result
        assert "15000 bytes]" in result

    def test_truncate_exact_threshold_no_truncation(self):
        """Output exactly at threshold not truncated."""
        exact_output = "x" * 10240
        result = langfuse_filter.truncate_output(exact_output, max_bytes=10240)

        assert result == exact_output
        assert "[TRUNCATED" not in result

    def test_truncate_custom_threshold(self):
        """Custom threshold is respected."""
        output = "x" * 6000
        result = langfuse_filter.truncate_output(output, max_bytes=5000)

        assert len(result.encode("utf-8")) <= 5000 + 100
        assert "[TRUNCATED - original size: 6000 bytes]" in result

    def test_truncate_preserves_utf8_characters(self):
        """Truncation doesn't break multi-byte UTF-8 characters."""
        output = "Hello 世界 " * 1000  # Mix of ASCII and multi-byte UTF-8
        result = langfuse_filter.truncate_output(output, max_bytes=1000)

        # Should not raise UnicodeDecodeError when encoded/decoded
        result.encode("utf-8").decode("utf-8")
        assert "[TRUNCATED" in result

    def test_truncate_empty_string(self):
        """Empty string handled gracefully."""
        result = langfuse_filter.truncate_output("", max_bytes=10240)
        assert result == ""

    def test_truncate_none_input_raises_error(self):
        """None input raises TypeError."""
        with pytest.raises(TypeError):
            langfuse_filter.truncate_output(None, max_bytes=10240)


class TestSecretRedaction:
    """Tests for AC2: Secret Pattern Redaction."""

    def test_redact_openai_api_key(self):
        """OpenAI API keys are redacted."""
        text = "My key is sk-1234567890abcdefghij1234567890"
        result = langfuse_filter.redact_secrets(text)

        assert "sk-1234567890abcdefghij1234567890" not in result
        assert "[REDACTED]" in result

    def test_redact_anthropic_api_key(self):
        """Anthropic API keys are redacted."""
        text = "Claude key: sk-ant-api03-1234567890abcdefghij"
        result = langfuse_filter.redact_secrets(text)

        assert "sk-ant-api03-1234567890abcdefghij" not in result
        assert "[REDACTED]" in result

    def test_redact_aws_access_key(self):
        """AWS access keys are redacted."""
        text = "AWS key: AKIAIOSFODNN7EXAMPLE"
        result = langfuse_filter.redact_secrets(text)

        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "[REDACTED]" in result

    def test_redact_slack_token(self):
        """Slack tokens are redacted."""
        # Build token dynamically to avoid GitHub secret scanning
        slack_prefix = "xoxb"
        slack_token = f"{slack_prefix}-1234567890-1234567890-abcdefghijklmnop"
        text = f"Slack: {slack_token}"
        result = langfuse_filter.redact_secrets(text)

        assert slack_token not in result
        assert "[REDACTED]" in result

    def test_redact_bearer_token(self):
        """Bearer tokens are redacted."""
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        result = langfuse_filter.redact_secrets(text)

        assert "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result
        assert "[REDACTED]" in result

    def test_redact_private_key(self):
        """Private keys are redacted."""
        text = "Key: -----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA..."
        result = langfuse_filter.redact_secrets(text)

        assert "BEGIN RSA PRIVATE KEY" not in result
        assert "[REDACTED]" in result

    def test_redact_password_equals(self):
        """Password fields are redacted."""
        text = "password=mySecret123"
        result = langfuse_filter.redact_secrets(text)

        assert "mySecret123" not in result
        assert "[REDACTED]" in result

    def test_redact_password_colon(self):
        """Password fields with colon are redacted."""
        text = 'password: "mySecret123"'
        result = langfuse_filter.redact_secrets(text)

        assert "mySecret123" not in result
        assert "[REDACTED]" in result

    def test_redact_api_key_pattern(self):
        """Generic API key patterns are redacted."""
        text = "api_key=abc123-def456-ghi789"
        result = langfuse_filter.redact_secrets(text)

        assert "abc123-def456-ghi789" not in result
        assert "[REDACTED]" in result

    def test_redact_multiple_secrets(self):
        """Multiple secrets in one text are all redacted."""
        text = (
            "Keys: sk-1234567890abcdefghij and AKIAIOSFODNN7EXAMPLE and password=secret"
        )
        result = langfuse_filter.redact_secrets(text)

        assert "sk-1234567890abcdefghij" not in result
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "secret" not in result
        assert result.count("[REDACTED]") >= 3

    def test_redact_no_secrets_unchanged(self):
        """Text without secrets remains unchanged."""
        text = "This is a normal log message with no secrets"
        result = langfuse_filter.redact_secrets(text)

        assert result == text
        assert "[REDACTED]" not in result

    def test_redact_disabled(self):
        """Redaction can be disabled."""
        text = "My key is sk-1234567890abcdefghij1234567890"
        result = langfuse_filter.redact_secrets(text, enabled=False)

        assert result == text
        assert "[REDACTED]" not in result


class TestFilterApplication:
    """Tests for applying both truncation and redaction together."""

    def test_filter_tool_result_both_applied(self):
        """Truncation and redaction both applied to tool result."""
        large_output = "x" * 15000 + " sk-1234567890abcdefghij1234567890"
        result = langfuse_filter.filter_tool_result(
            large_output, max_bytes=10240, enable_redaction=True
        )

        # Should be truncated
        assert len(result.encode("utf-8")) <= 10240 + 100
        assert "[TRUNCATED" in result

        # Should have redacted secrets (if they were in truncated portion)
        # Note: Secret might be truncated away, so we check it doesn't exist
        assert "sk-1234567890abcdefghij1234567890" not in result

    def test_filter_tool_result_only_redaction(self):
        """Only redaction applied when output small."""
        output = "Small output with sk-1234567890abcdefghij1234567890"
        result = langfuse_filter.filter_tool_result(
            output, max_bytes=10240, enable_redaction=True
        )

        assert "[TRUNCATED" not in result
        assert "sk-1234567890abcdefghij1234567890" not in result
        assert "[REDACTED]" in result

    def test_filter_tool_result_only_truncation(self):
        """Only truncation applied when redaction disabled."""
        large_output = "x" * 15000
        result = langfuse_filter.filter_tool_result(
            large_output, max_bytes=10240, enable_redaction=False
        )

        assert len(result.encode("utf-8")) <= 10240 + 100
        assert "[TRUNCATED" in result

    def test_filter_tool_result_no_filtering(self):
        """No filtering when both disabled."""
        output = "Normal output"
        result = langfuse_filter.filter_tool_result(
            output, max_bytes=None, enable_redaction=False
        )

        assert result == output

    def test_filter_tool_result_defaults(self):
        """Default parameters work correctly."""
        output = "Small output"
        result = langfuse_filter.filter_tool_result(output)

        assert result == output


class TestFilterCLICommand:
    """Tests for AC3: Filter Configuration Command."""

    def test_filter_command_set_max_result_size(self, tmp_path):
        """Setting max result size updates config."""
        config_path = tmp_path / "config.json"

        # Execute filter command
        result = user_commands.execute_command(
            command="langfuse",
            config_path=str(config_path),
            subcommand="filter --max-result-size 5000",
        )

        assert result["success"] is True
        assert "5000" in result["message"]

        # Verify config was written
        with open(config_path) as f:
            config = json.load(f)

        assert config["langfuse_max_result_size"] == 5000

    def test_filter_command_set_redaction(self, tmp_path):
        """Setting redaction flag updates config."""
        config_path = tmp_path / "config.json"

        # Test "on"
        result = user_commands.execute_command(
            command="langfuse",
            config_path=str(config_path),
            subcommand="filter --redact on",
        )

        assert result["success"] is True

        with open(config_path) as f:
            config = json.load(f)

        assert config["langfuse_redact_secrets"] is True

        # Test "off"
        result = user_commands.execute_command(
            command="langfuse",
            config_path=str(config_path),
            subcommand="filter --redact off",
        )

        assert result["success"] is True

        with open(config_path) as f:
            config = json.load(f)

        assert config["langfuse_redact_secrets"] is False

    def test_filter_command_shows_current_settings(self, tmp_path):
        """Filter command with no args shows current settings."""
        config_path = tmp_path / "config.json"

        # Write config with custom settings
        config = {"langfuse_max_result_size": 8000, "langfuse_redact_secrets": False}
        with open(config_path, "w") as f:
            json.dump(config, f)

        # Execute filter command with no args
        result = user_commands.execute_command(
            command="langfuse", config_path=str(config_path), subcommand="filter"
        )

        assert result["success"] is True
        assert "8000" in result["message"]
        assert "redact" in result["message"].lower()

    def test_filter_command_both_parameters(self, tmp_path):
        """Setting both parameters together works."""
        config_path = tmp_path / "config.json"

        result = user_commands.execute_command(
            command="langfuse",
            config_path=str(config_path),
            subcommand="filter --max-result-size 12000 --redact off",
        )

        assert result["success"] is True

        with open(config_path) as f:
            config = json.load(f)

        assert config["langfuse_max_result_size"] == 12000
        assert config["langfuse_redact_secrets"] is False
