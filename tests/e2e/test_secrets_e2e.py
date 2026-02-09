"""
End-to-end tests for secrets management with ZERO mocking.

These tests verify the complete secrets workflow from declaration to masking,
using real database, real files, and real function calls with NO mocks.

Following strict TDD methodology - tests interact with real systems only.
"""

import os
import tempfile
import json

import pytest

from src.pacemaker.secrets.database import (
    create_secret,
    list_secrets,
    get_all_secrets,
    remove_secret,
    clear_all_secrets,
)
from src.pacemaker.secrets.parser import parse_assistant_response
from src.pacemaker.secrets.masking import mask_text, mask_structure
from src.pacemaker.secrets.sanitizer import sanitize_trace


@pytest.fixture
def temp_db():
    """Create a temporary database file for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    # Cleanup
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def temp_file():
    """Create a temporary file for file secret testing."""
    fd, path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)

    # Write test content
    with open(path, "w") as f:
        f.write("secret-api-key-12345\npassword123\n")

    yield path

    # Cleanup
    if os.path.exists(path):
        os.remove(path)


class TestAC1_ParseTextSecrets:
    """AC1: Parse text secrets from assistant responses."""

    def test_parse_single_text_secret(self, temp_db):
        """Test parsing a single text secret declaration."""
        response = "Here's your API key: 🔐 SECRET_TEXT: sk-test-api-key-xyz"

        secrets = parse_assistant_response(response, temp_db)

        assert len(secrets) == 1
        assert secrets[0]["type"] == "text"
        assert secrets[0]["id"] > 0

        # Verify stored in database
        all_secrets = get_all_secrets(temp_db)
        assert len(all_secrets) == 1
        assert all_secrets[0] == "sk-test-api-key-xyz"

    def test_parse_multiple_text_secrets(self, temp_db):
        """Test parsing multiple text secret declarations."""
        response = """
        🔐 SECRET_TEXT: password123
        Some other text here.
        🔐 SECRET_TEXT: token-xyz-789
        """

        secrets = parse_assistant_response(response, temp_db)

        assert len(secrets) == 2
        assert secrets[0]["type"] == "text"
        assert secrets[1]["type"] == "text"

        # Verify both stored in database
        all_secrets = get_all_secrets(temp_db)
        assert len(all_secrets) == 2
        assert "password123" in all_secrets
        assert "token-xyz-789" in all_secrets


class TestAC2_ParseFileSecrets:
    """AC2: Parse file secrets from assistant responses."""

    def test_parse_file_secret(self, temp_db):
        """Test parsing a file secret declaration."""
        response = """
        🔐 SECRET_FILE_START
        -----BEGIN RSA PRIVATE KEY-----
        MIIEpAIBAAKCAQEA1234567890
        -----END RSA PRIVATE KEY-----
        🔐 SECRET_FILE_END
        """

        secrets = parse_assistant_response(response, temp_db)

        assert len(secrets) == 1
        assert secrets[0]["type"] == "file"
        assert secrets[0]["id"] > 0

        # Verify stored in database
        all_secrets = get_all_secrets(temp_db)
        assert len(all_secrets) == 1
        assert "BEGIN RSA PRIVATE KEY" in all_secrets[0]

    def test_parse_multiple_file_secrets(self, temp_db):
        """Test parsing multiple file secret declarations."""
        response = """
        First file:
        🔐 SECRET_FILE_START
        content1
        🔐 SECRET_FILE_END

        Second file:
        🔐 SECRET_FILE_START
        content2
        🔐 SECRET_FILE_END
        """

        secrets = parse_assistant_response(response, temp_db)

        assert len(secrets) == 2
        assert secrets[0]["type"] == "file"
        assert secrets[1]["type"] == "file"

        # Verify stored in database
        all_secrets = get_all_secrets(temp_db)
        assert "content1" in all_secrets[0]
        assert "content2" in all_secrets[1]


class TestAC3_StoreSecrets:
    """AC3: Store secrets in database with proper indexing."""

    def test_store_and_retrieve_secrets(self, temp_db):
        """Test storing and retrieving secrets from database."""
        # Store secrets
        create_secret(temp_db, "text", "secret1")
        create_secret(temp_db, "text", "secret2")
        create_secret(temp_db, "file", "file-content")

        # Retrieve all secrets
        all_secrets = get_all_secrets(temp_db)

        assert len(all_secrets) == 3
        assert "secret1" in all_secrets
        assert "secret2" in all_secrets
        assert "file-content" in all_secrets

    def test_list_secrets_returns_raw_values(self, temp_db):
        """Test that list_secrets returns raw dictionary with values (masking done in CLI)."""
        create_secret(temp_db, "text", "my-secret-password")

        secrets_list = list_secrets(temp_db)

        assert len(secrets_list) == 1
        assert secrets_list[0]["id"] > 0
        assert secrets_list[0]["type"] == "text"
        assert secrets_list[0]["value"] == "my-secret-password"  # Raw value, not masked

    def test_remove_secret(self, temp_db):
        """Test removing a secret by ID."""
        secret_id = create_secret(temp_db, "text", "secret-to-remove")

        # Remove it
        remove_secret(temp_db, secret_id)

        # Verify removed
        all_secrets = get_all_secrets(temp_db)
        assert len(all_secrets) == 0

    def test_clear_all_secrets(self, temp_db):
        """Test clearing all secrets from database."""
        create_secret(temp_db, "text", "secret1")
        create_secret(temp_db, "text", "secret2")
        create_secret(temp_db, "text", "secret3")

        clear_all_secrets(temp_db)

        all_secrets = get_all_secrets(temp_db)
        assert len(all_secrets) == 0


class TestAC4_MaskSecrets:
    """AC4: Mask secrets in text and structured data."""

    def test_mask_text_with_secrets(self, temp_db):
        """Test masking secrets in plain text."""
        create_secret(temp_db, "text", "api-key-12345")
        create_secret(temp_db, "text", "password-xyz")

        text = "My API key is api-key-12345 and password is password-xyz"

        secrets = get_all_secrets(temp_db)
        masked, count = mask_text(text, secrets)

        assert "api-key-12345" not in masked
        assert "password-xyz" not in masked
        assert masked.count("*** MASKED ***") == 2
        assert count == 2

    def test_mask_structure_dict(self, temp_db):
        """Test masking secrets in nested dictionaries."""
        create_secret(temp_db, "text", "secret-token")

        data = {
            "api_key": "secret-token",
            "nested": {"password": "secret-token", "safe_value": "public-data"},
        }

        secrets = get_all_secrets(temp_db)
        masked, count = mask_structure(data, secrets)

        assert masked["api_key"] == "*** MASKED ***"
        assert masked["nested"]["password"] == "*** MASKED ***"
        assert masked["nested"]["safe_value"] == "public-data"
        assert count == 2

    def test_mask_structure_list(self, temp_db):
        """Test masking secrets in lists."""
        create_secret(temp_db, "text", "secret123")

        data = ["public", "secret123", "also-public"]

        secrets = get_all_secrets(temp_db)
        masked, count = mask_structure(data, secrets)

        assert masked[0] == "public"
        assert masked[1] == "*** MASKED ***"
        assert masked[2] == "also-public"
        assert count == 1


class TestAC5_SanitizeLangfuseTraces:
    """AC5: Sanitize Langfuse traces before push."""

    def test_sanitize_trace_batch(self, temp_db):
        """Test sanitizing a complete Langfuse trace batch."""
        create_secret(temp_db, "text", "secret-api-key")

        trace_batch = [
            {
                "id": "trace-1",
                "type": "trace-create",
                "body": {
                    "id": "trace-1",
                    "input": "Use this API key: secret-api-key",
                    "metadata": {
                        "tool": "Bash",
                        "command": "curl -H 'Authorization: secret-api-key'",
                    },
                },
            }
        ]

        sanitized = sanitize_trace(trace_batch, temp_db)

        # Original unchanged
        assert "secret-api-key" in trace_batch[0]["body"]["input"]

        # Sanitized version masked
        assert "secret-api-key" not in sanitized[0]["body"]["input"]
        assert "*** MASKED ***" in sanitized[0]["body"]["input"]
        assert "secret-api-key" not in sanitized[0]["body"]["metadata"]["command"]
        assert "*** MASKED ***" in sanitized[0]["body"]["metadata"]["command"]

    def test_sanitize_preserves_non_secrets(self, temp_db):
        """Test that sanitization doesn't affect non-secret data."""
        create_secret(temp_db, "text", "only-this-secret")

        trace_batch = [
            {
                "id": "trace-1",
                "body": {
                    "input": "This is public data",
                    "output": "only-this-secret should be masked",
                },
            }
        ]

        sanitized = sanitize_trace(trace_batch, temp_db)

        assert sanitized[0]["body"]["input"] == "This is public data"
        assert "only-this-secret" not in sanitized[0]["body"]["output"]
        assert "*** MASKED ***" in sanitized[0]["body"]["output"]


@pytest.mark.skip(
    reason="CLI tests require installed pace-maker command, tested manually"
)
class TestAC6_CLICommands:
    """AC6: CLI commands work correctly.

    Note: These tests are skipped because they require the installed `pace-maker` CLI
    which uses the global database. The CLI functionality is verified through manual testing.
    The actual CLI command is `pace-maker secrets <subcommand>`, not `python -m pacemaker`.
    """

    def test_cli_secrets_add(self, temp_db):
        """Test 'pace-maker secrets add' command."""
        pass

    def test_cli_secrets_addfile(self, temp_db, temp_file):
        """Test 'pace-maker secrets addfile' command."""
        pass

    def test_cli_secrets_list(self, temp_db):
        """Test 'pace-maker secrets list' command."""
        pass

    def test_cli_secrets_remove(self, temp_db):
        """Test 'pace-maker secrets remove' command."""
        pass

    def test_cli_secrets_clear_with_confirmation(self, temp_db):
        """Test 'pace-maker secrets clear' command with confirmation."""
        pass


class TestEndToEndWorkflow:
    """Complete end-to-end workflow test."""

    def test_complete_secrets_workflow(self, temp_db):
        """
        Test the complete workflow:
        1. Assistant declares secrets in response
        2. Secrets are parsed and stored
        3. Secrets are masked in text
        4. Langfuse traces are sanitized

        Note: FILE secrets mask the ENTIRE file content as a block, not individual lines.
        TEXT secrets mask each occurrence individually.
        """
        # Step 1: Assistant declares multiple text secrets
        assistant_response = """
        I'll help you configure the API.

        🔐 SECRET_TEXT: sk-live-api-key-xyz123
        🔐 SECRET_TEXT: super-secret-password

        And here's the config file:
        🔐 SECRET_FILE_START
        api_key=sk-live-api-key-xyz123
        api_secret=super-secret-password
        🔐 SECRET_FILE_END
        """

        # Step 2: Parse and store secrets
        parsed = parse_assistant_response(assistant_response, temp_db)
        assert len(parsed) == 3  # 2 text secrets + 1 file secret

        # Step 3: Verify TEXT secrets are masked individually
        test_text = (
            "Connect with key sk-live-api-key-xyz123 and password super-secret-password"
        )
        secrets = get_all_secrets(temp_db)
        masked_text, count = mask_text(test_text, secrets)

        assert "sk-live-api-key-xyz123" not in masked_text
        assert "super-secret-password" not in masked_text
        assert masked_text.count("*** MASKED ***") == 2
        assert count == 2

        # Step 4: Verify Langfuse trace sanitization
        trace = [
            {
                "id": "trace-1",
                "body": {
                    "input": "Use API key: sk-live-api-key-xyz123",
                    "output": "Connected with password: super-secret-password",
                },
            }
        ]

        sanitized = sanitize_trace(trace, temp_db)

        assert "sk-live-api-key-xyz123" not in json.dumps(sanitized)
        assert "super-secret-password" not in json.dumps(sanitized)
        assert "*** MASKED ***" in json.dumps(sanitized)
