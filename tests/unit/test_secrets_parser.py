"""
Unit tests for secret declaration parser.

Following strict TDD methodology - these tests are written FIRST and will FAIL
until the parser module is implemented.
"""

import os
import tempfile
import pytest

# This import will FAIL initially - that's expected in TDD
from src.pacemaker.secrets.parser import (
    parse_text_secret,
    parse_file_secret,
    parse_assistant_response,
)
from src.pacemaker.secrets.database import list_secrets


@pytest.fixture
def temp_db():
    """Create a temporary database file for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    # Cleanup
    if os.path.exists(path):
        os.remove(path)


class TestParseTextSecret:
    """Test parsing text secret declarations from responses."""

    def test_parse_single_text_secret(self):
        """Test parsing a single text secret declaration."""
        response = "Here is the API key: 🔐 SECRET_TEXT: abc123def456"

        secrets = parse_text_secret(response)

        assert len(secrets) == 1
        assert secrets[0] == "abc123def456"

    def test_parse_multiple_text_secrets(self):
        """Test parsing multiple text secret declarations."""
        response = """
        First secret: 🔐 SECRET_TEXT: password123
        Second secret: 🔐 SECRET_TEXT: api-key-xyz
        Third secret: 🔐 SECRET_TEXT: token-abc
        """

        secrets = parse_text_secret(response)

        assert len(secrets) == 3
        assert "password123" in secrets
        assert "api-key-xyz" in secrets
        assert "token-abc" in secrets

    def test_parse_text_secret_with_spaces(self):
        """Test parsing text secret with spaces in value."""
        response = "🔐 SECRET_TEXT: my secret with spaces"

        secrets = parse_text_secret(response)

        assert len(secrets) == 1
        assert secrets[0] == "my secret with spaces"

    def test_parse_text_secret_multiline_value(self):
        """Test parsing text secret up to newline."""
        response = """🔐 SECRET_TEXT: single-line-secret
        This should not be included"""

        secrets = parse_text_secret(response)

        assert len(secrets) == 1
        assert secrets[0] == "single-line-secret"
        assert "This should not be included" not in secrets[0]

    def test_parse_no_text_secrets(self):
        """Test parsing response with no text secrets returns empty list."""
        response = "This is a normal response with no secrets"

        secrets = parse_text_secret(response)

        assert secrets == []

    def test_parse_text_secret_strips_trailing_whitespace(self):
        """Test that trailing whitespace is stripped from secret value."""
        response = "🔐 SECRET_TEXT: secret-value   \n"

        secrets = parse_text_secret(response)

        assert len(secrets) == 1
        assert secrets[0] == "secret-value"

    def test_parse_text_secret_strips_trailing_backticks(self):
        """Test that trailing backticks from markdown are stripped."""
        response = "🔐 SECRET_TEXT: password123`"

        secrets = parse_text_secret(response)

        assert len(secrets) == 1
        assert secrets[0] == "password123"

    def test_parse_text_secret_strips_trailing_asterisks(self):
        """Test that trailing asterisks from markdown are stripped."""
        response = "🔐 SECRET_TEXT: api-key-xyz*"

        secrets = parse_text_secret(response)

        assert len(secrets) == 1
        assert secrets[0] == "api-key-xyz"

    def test_parse_text_secret_strips_multiple_trailing_chars(self):
        """Test that multiple trailing markdown characters are stripped."""
        response = "🔐 SECRET_TEXT: token123`**_"

        secrets = parse_text_secret(response)

        assert len(secrets) == 1
        assert secrets[0] == "token123"

    def test_parse_text_secret_preserves_internal_markdown(self):
        """Test that markdown characters inside the value are preserved."""
        response = "🔐 SECRET_TEXT: pass`word*123"

        secrets = parse_text_secret(response)

        assert len(secrets) == 1
        assert secrets[0] == "pass`word*123"

    def test_parse_text_secret_rejects_email_addresses(self):
        """Test that email addresses are rejected — they are identity fields, not secrets."""
        response = "🔐 SECRET_TEXT: user@example.com"

        secrets = parse_text_secret(response)

        assert len(secrets) == 0

    def test_parse_text_secret_rejects_email_among_real_secrets(self):
        """Test that emails are filtered but real secrets are kept."""
        response = (
            "🔐 SECRET_TEXT: sk-ant-api03-real-secret\n"
            "🔐 SECRET_TEXT: seba.battig@lightspeeddms.com\n"
            "🔐 SECRET_TEXT: another-real-key-123"
        )

        secrets = parse_text_secret(response)

        assert len(secrets) == 2
        assert "sk-ant-api03-real-secret" in secrets
        assert "another-real-key-123" in secrets
        assert "seba.battig@lightspeeddms.com" not in secrets


class TestParseFileSecret:
    """Test parsing file secret declarations from responses using single-line format."""

    def test_parse_single_file_secret(self):
        """Test parsing a single file secret declaration."""
        fd, temp_path = tempfile.mkstemp(suffix=".txt")
        try:
            os.write(fd, b"ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ")
            os.close(fd)

            response = f"Declaring sensitive file: 🔐 SECRET_FILE: {temp_path}"

            secrets = parse_file_secret(response)

            assert len(secrets) == 1
            assert "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ" in secrets[0]
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_parse_multiple_file_secrets(self):
        """Test parsing multiple file secret declarations."""
        fd1, temp_path1 = tempfile.mkstemp(suffix=".txt")
        fd2, temp_path2 = tempfile.mkstemp(suffix=".txt")
        try:
            os.write(fd1, b"content1")
            os.close(fd1)
            os.write(fd2, b"content2")
            os.close(fd2)

            response = f"""
            First file: 🔐 SECRET_FILE: {temp_path1}
            Second file: 🔐 SECRET_FILE: {temp_path2}
            """

            secrets = parse_file_secret(response)

            assert len(secrets) == 2
            assert any("content1" in s for s in secrets)
            assert any("content2" in s for s in secrets)
        finally:
            for p in [temp_path1, temp_path2]:
                if os.path.exists(p):
                    os.remove(p)

    def test_parse_no_file_secrets(self):
        """Test parsing response with no file secrets returns empty list."""
        response = "This is a normal response with no file secrets"

        secrets = parse_file_secret(response)

        assert secrets == []

    def test_parse_file_secret_strips_trailing_whitespace_from_path(self):
        """Test that trailing whitespace is stripped from the file path."""
        fd, temp_path = tempfile.mkstemp(suffix=".txt")
        try:
            os.write(fd, b"actual-content")
            os.close(fd)

            # Path has trailing spaces before newline
            response = f"🔐 SECRET_FILE: {temp_path}   \n"

            secrets = parse_file_secret(response)

            assert len(secrets) == 1
            assert secrets[0] == "actual-content"
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_parse_file_secret_strips_trailing_backticks_from_path(self):
        """Test that trailing backticks from markdown are stripped from path."""
        fd, temp_path = tempfile.mkstemp(suffix=".txt")
        try:
            os.write(fd, b"content-no-backtick")
            os.close(fd)

            response = f"🔐 SECRET_FILE: {temp_path}`"

            secrets = parse_file_secret(response)

            assert len(secrets) == 1
            assert secrets[0] == "content-no-backtick"
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_parse_file_secret_inline_literal_fallback(self):
        """Test that inline literal value (not a file path) is returned as-is."""
        response = "🔐 SECRET_FILE: some-literal-value-not-a-path"

        secrets = parse_file_secret(response)

        assert len(secrets) == 1
        assert secrets[0] == "some-literal-value-not-a-path"


class TestParseFileSecretWithFilePaths:
    """Test parsing file paths in single-line SECRET_FILE: markers."""

    def test_parse_file_secret_with_absolute_path(self):
        """Test that absolute file paths are detected and contents read."""
        fd, temp_path = tempfile.mkstemp(suffix=".txt")
        try:
            os.write(fd, b"secret-from-file-content")
            os.close(fd)

            response = f"🔐 SECRET_FILE: {temp_path}"

            secrets = parse_file_secret(response)

            assert len(secrets) == 1
            assert secrets[0] == "secret-from-file-content"
            assert temp_path not in secrets[0]  # Path itself should not be in result
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_parse_file_secret_with_tilde_path(self):
        """Test that tilde paths are expanded and contents read."""
        home = os.path.expanduser("~")
        temp_path = os.path.join(home, f".test_secret_{os.getpid()}.tmp")

        try:
            with open(temp_path, "w") as f:
                f.write("tilde-expanded-secret")

            tilde_path = temp_path.replace(home, "~")
            response = f"🔐 SECRET_FILE: {tilde_path}"

            secrets = parse_file_secret(response)

            assert len(secrets) == 1
            assert secrets[0] == "tilde-expanded-secret"
            assert "~" not in secrets[0]  # Tilde should be expanded
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_parse_file_secret_with_multiline_file_content(self):
        """Test that multiline file contents are read correctly."""
        fd, temp_path = tempfile.mkstemp(suffix=".txt")
        try:
            multiline_content = "line1\nline2\nline3"
            os.write(fd, multiline_content.encode())
            os.close(fd)

            response = f"🔐 SECRET_FILE: {temp_path}"

            secrets = parse_file_secret(response)

            assert len(secrets) == 1
            assert secrets[0] == multiline_content
            assert "line1" in secrets[0]
            assert "line2" in secrets[0]
            assert "line3" in secrets[0]
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_parse_file_secret_nonexistent_file_returns_path(self):
        """Test that non-existent file paths return the path itself."""
        nonexistent_path = "/tmp/this-file-does-not-exist-12345.txt"
        response = f"🔐 SECRET_FILE: {nonexistent_path}"

        secrets = parse_file_secret(response)

        assert len(secrets) == 1
        assert secrets[0] == nonexistent_path  # Should return original path

    def test_parse_file_secret_permission_denied_returns_path(self):
        """Test that permission denied returns the path itself."""
        fd, temp_path = tempfile.mkstemp(suffix=".txt")
        try:
            os.write(fd, b"content")
            os.close(fd)
            os.chmod(temp_path, 0o000)  # Remove all permissions

            response = f"🔐 SECRET_FILE: {temp_path}"

            secrets = parse_file_secret(response)

            assert len(secrets) == 1
            assert temp_path in secrets[0]
        finally:
            os.chmod(temp_path, 0o644)
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_parse_file_secret_empty_file_returns_nothing(self):
        """Test that an empty file produces no secrets (skipped)."""
        fd, temp_path = tempfile.mkstemp(suffix=".txt")
        try:
            os.close(fd)  # Create empty file

            response = f"🔐 SECRET_FILE: {temp_path}"

            secrets = parse_file_secret(response)

            assert len(secrets) == 0  # Empty file should be skipped entirely
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_parse_file_secret_whitespace_around_path(self):
        """Test that whitespace around file path is stripped before checking."""
        fd, temp_path = tempfile.mkstemp(suffix=".txt")
        try:
            os.write(fd, b"content-with-whitespace-path")
            os.close(fd)

            # Add extra whitespace after the path
            response = f"🔐 SECRET_FILE:   {temp_path}   "

            secrets = parse_file_secret(response)

            assert len(secrets) == 1
            assert secrets[0] == "content-with-whitespace-path"
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_parse_file_secret_generic_exception_returns_path(self):
        """Test that generic exceptions during file reading return the path."""
        from unittest.mock import patch

        fake_path = "/tmp/fake-file.txt"
        response = f"🔐 SECRET_FILE: {fake_path}"

        # Mock os.path.exists to return True, but open() to raise IOError
        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", side_effect=IOError("Simulated I/O error")):
                secrets = parse_file_secret(response)

                assert len(secrets) == 1
                assert fake_path in secrets[0]


class TestParseAssistantResponse:
    """Test parsing and storing secrets from assistant response."""

    def test_parse_assistant_response_text_secret(self, temp_db):
        """Test that text secrets are parsed and stored in database."""
        response = "Here is your key: 🔐 SECRET_TEXT: my-api-key-123"

        results = parse_assistant_response(response, temp_db)

        assert len(results) == 1
        assert results[0]["type"] == "text"
        assert "id" in results[0]

        stored_secrets = list_secrets(temp_db)
        assert len(stored_secrets) == 1
        assert stored_secrets[0]["value"] == "my-api-key-123"

    def test_parse_assistant_response_file_secret(self, temp_db):
        """Test that file secrets are parsed and stored in database."""
        fd, temp_path = tempfile.mkstemp(suffix=".txt")
        try:
            os.write(fd, b"ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ")
            os.close(fd)

            response = f"Declaring key file: 🔐 SECRET_FILE: {temp_path}"

            results = parse_assistant_response(response, temp_db)

            assert len(results) == 1
            assert results[0]["type"] == "file"

            stored_secrets = list_secrets(temp_db)
            assert len(stored_secrets) == 1
            assert "ssh-rsa" in stored_secrets[0]["value"]
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_parse_assistant_response_mixed_secrets(self, temp_db):
        """Test parsing response with both text and file secrets."""
        fd, temp_path = tempfile.mkstemp(suffix=".txt")
        try:
            os.write(fd, b"private-key-content")
            os.close(fd)

            response = f"""
            Text secret: 🔐 SECRET_TEXT: password123

            File secret: 🔐 SECRET_FILE: {temp_path}

            Another text: 🔐 SECRET_TEXT: token-xyz
            """

            results = parse_assistant_response(response, temp_db)

            assert len(results) == 3

            text_secrets = [r for r in results if r["type"] == "text"]
            file_secrets = [r for r in results if r["type"] == "file"]
            assert len(text_secrets) == 2
            assert len(file_secrets) == 1

            stored_secrets = list_secrets(temp_db)
            assert len(stored_secrets) == 3
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_parse_assistant_response_no_secrets(self, temp_db):
        """Test parsing response with no secrets returns empty list."""
        response = "This is a normal response with no secret declarations"

        results = parse_assistant_response(response, temp_db)

        assert results == []

        stored_secrets = list_secrets(temp_db)
        assert stored_secrets == []

    def test_parse_assistant_response_duplicate_secrets(self, temp_db):
        """Test that duplicate secret values return same ID and are deduplicated."""
        response = """
        🔐 SECRET_TEXT: same-value
        🔐 SECRET_TEXT: same-value
        """

        results = parse_assistant_response(response, temp_db)

        assert len(results) == 2
        assert results[0]["id"] == results[1]["id"]

        stored_secrets = list_secrets(temp_db)
        assert len(stored_secrets) == 1
        assert stored_secrets[0]["value"] == "same-value"

    def test_parse_assistant_response_returns_ids(self, temp_db):
        """Test that returned results include database IDs."""
        response = "🔐 SECRET_TEXT: test-secret"

        results = parse_assistant_response(response, temp_db)

        assert len(results) == 1
        assert "id" in results[0]
        assert isinstance(results[0]["id"], int)
        assert results[0]["id"] > 0
