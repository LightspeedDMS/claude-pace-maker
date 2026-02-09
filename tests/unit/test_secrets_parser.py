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


class TestParseFileSecret:
    """Test parsing file secret declarations from responses."""

    def test_parse_single_file_secret(self):
        """Test parsing a single file secret declaration."""
        response = """
        🔐 SECRET_FILE_START
        ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ
        🔐 SECRET_FILE_END
        """

        secrets = parse_file_secret(response)

        assert len(secrets) == 1
        assert "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ" in secrets[0]

    def test_parse_file_secret_multiline_content(self):
        """Test parsing file secret with multiple lines."""
        response = """
        🔐 SECRET_FILE_START
        -----BEGIN RSA PRIVATE KEY-----
        MIIEpAIBAAKCAQEA1234567890abcdef
        ghijklmnopqrstuvwxyz1234567890
        -----END RSA PRIVATE KEY-----
        🔐 SECRET_FILE_END
        """

        secrets = parse_file_secret(response)

        assert len(secrets) == 1
        assert "-----BEGIN RSA PRIVATE KEY-----" in secrets[0]
        assert "MIIEpAIBAAKCAQEA1234567890abcdef" in secrets[0]
        assert "-----END RSA PRIVATE KEY-----" in secrets[0]

    def test_parse_multiple_file_secrets(self):
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

        secrets = parse_file_secret(response)

        assert len(secrets) == 2
        assert any("content1" in s for s in secrets)
        assert any("content2" in s for s in secrets)

    def test_parse_no_file_secrets(self):
        """Test parsing response with no file secrets returns empty list."""
        response = "This is a normal response with no file secrets"

        secrets = parse_file_secret(response)

        assert secrets == []

    def test_parse_file_secret_preserves_formatting(self):
        """Test that file secret content preserves indentation and formatting."""
        response = """
        🔐 SECRET_FILE_START
def function():
    return "indented"
        🔐 SECRET_FILE_END
        """

        secrets = parse_file_secret(response)

        assert len(secrets) == 1
        # Content should preserve structure
        assert "def function():" in secrets[0]
        assert "return" in secrets[0]

    def test_parse_file_secret_strips_boundary_whitespace(self):
        """Test that leading/trailing whitespace around content is stripped."""
        response = """
        🔐 SECRET_FILE_START

        actual-content

        🔐 SECRET_FILE_END
        """

        secrets = parse_file_secret(response)

        assert len(secrets) == 1
        assert secrets[0].strip() == "actual-content"


class TestParseFileSecretWithFilePaths:
    """Test parsing file paths in SECRET_FILE_START/END markers."""

    def test_parse_file_secret_with_absolute_path(self):
        """Test that absolute file paths are detected and contents read."""
        # Create a temporary file with known content
        fd, temp_path = tempfile.mkstemp(suffix=".txt")
        try:
            os.write(fd, b"secret-from-file-content")
            os.close(fd)

            response = f"""
            🔐 SECRET_FILE_START
            {temp_path}
            🔐 SECRET_FILE_END
            """

            secrets = parse_file_secret(response)

            assert len(secrets) == 1
            assert secrets[0] == "secret-from-file-content"
            assert temp_path not in secrets[0]  # Path itself should not be in result
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_parse_file_secret_with_tilde_path(self):
        """Test that tilde paths are expanded and contents read."""
        # Create a temporary file in home directory
        home = os.path.expanduser("~")
        temp_path = os.path.join(home, f".test_secret_{os.getpid()}.tmp")

        try:
            with open(temp_path, "w") as f:
                f.write("tilde-expanded-secret")

            # Use tilde path in response
            tilde_path = temp_path.replace(home, "~")
            response = f"""
            🔐 SECRET_FILE_START
            {tilde_path}
            🔐 SECRET_FILE_END
            """

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

            response = f"""
            🔐 SECRET_FILE_START
            {temp_path}
            🔐 SECRET_FILE_END
            """

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
        response = f"""
        🔐 SECRET_FILE_START
        {nonexistent_path}
        🔐 SECRET_FILE_END
        """

        secrets = parse_file_secret(response)

        assert len(secrets) == 1
        assert secrets[0] == nonexistent_path  # Should return original path

    def test_parse_file_secret_literal_content_not_path(self):
        """Test that literal content (not a file path) is returned as-is."""
        response = """
        🔐 SECRET_FILE_START
        This is literal content
        not a file path at all
        🔐 SECRET_FILE_END
        """

        secrets = parse_file_secret(response)

        assert len(secrets) == 1
        assert "This is literal content" in secrets[0]
        assert "not a file path at all" in secrets[0]

    def test_parse_file_secret_permission_denied_returns_path(self):
        """Test that permission denied returns the path itself."""
        # Create a file and make it unreadable
        fd, temp_path = tempfile.mkstemp(suffix=".txt")
        try:
            os.write(fd, b"content")
            os.close(fd)
            os.chmod(temp_path, 0o000)  # Remove all permissions

            response = f"""
            🔐 SECRET_FILE_START
            {temp_path}
            🔐 SECRET_FILE_END
            """

            secrets = parse_file_secret(response)

            assert len(secrets) == 1
            # Should return the path since file can't be read
            assert temp_path in secrets[0]
        finally:
            # Restore permissions and cleanup
            os.chmod(temp_path, 0o644)
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_parse_file_secret_empty_file_returns_empty_string(self):
        """Test that an empty file returns empty string."""
        fd, temp_path = tempfile.mkstemp(suffix=".txt")
        try:
            os.close(fd)  # Create empty file

            response = f"""
            🔐 SECRET_FILE_START
            {temp_path}
            🔐 SECRET_FILE_END
            """

            secrets = parse_file_secret(response)

            assert len(secrets) == 1
            assert secrets[0] == ""  # Empty file should return empty string

        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_parse_file_secret_whitespace_around_path(self):
        """Test that whitespace around file path is stripped before checking."""
        fd, temp_path = tempfile.mkstemp(suffix=".txt")
        try:
            os.write(fd, b"content-with-whitespace-path")
            os.close(fd)

            # Add extra whitespace around the path
            response = f"""
            🔐 SECRET_FILE_START

            {temp_path}

            🔐 SECRET_FILE_END
            """

            secrets = parse_file_secret(response)

            assert len(secrets) == 1
            assert secrets[0] == "content-with-whitespace-path"
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_parse_file_secret_mixed_paths_and_literals(self):
        """Test parsing response with both file paths and literal content."""
        fd, temp_path = tempfile.mkstemp(suffix=".txt")
        try:
            os.write(fd, b"file-content")
            os.close(fd)

            response = f"""
            First is a file:
            🔐 SECRET_FILE_START
            {temp_path}
            🔐 SECRET_FILE_END

            Second is literal:
            🔐 SECRET_FILE_START
            literal-secret-value
            🔐 SECRET_FILE_END
            """

            secrets = parse_file_secret(response)

            assert len(secrets) == 2
            assert "file-content" in secrets  # From file
            assert "literal-secret-value" in secrets  # Literal content
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_parse_file_secret_generic_exception_returns_path(self):
        """Test that generic exceptions during file reading return the path."""
        from unittest.mock import patch

        fake_path = "/tmp/fake-file.txt"

        response = f"""
        🔐 SECRET_FILE_START
        {fake_path}
        🔐 SECRET_FILE_END
        """

        # Mock os.path.exists to return True, but open() to raise IOError
        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", side_effect=IOError("Simulated I/O error")):
                secrets = parse_file_secret(response)

                assert len(secrets) == 1
                # Should return the path since file couldn't be read
                assert fake_path in secrets[0]


class TestParseAssistantResponse:
    """Test parsing and storing secrets from assistant response."""

    def test_parse_assistant_response_text_secret(self, temp_db):
        """Test that text secrets are parsed and stored in database."""
        response = "Here is your key: 🔐 SECRET_TEXT: my-api-key-123"

        results = parse_assistant_response(response, temp_db)

        # Should return info about stored secrets
        assert len(results) == 1
        assert results[0]["type"] == "text"
        assert "id" in results[0]

        # Verify actually stored in database
        stored_secrets = list_secrets(temp_db)
        assert len(stored_secrets) == 1
        assert stored_secrets[0]["value"] == "my-api-key-123"

    def test_parse_assistant_response_file_secret(self, temp_db):
        """Test that file secrets are parsed and stored in database."""
        response = """
        🔐 SECRET_FILE_START
        ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ
        🔐 SECRET_FILE_END
        """

        results = parse_assistant_response(response, temp_db)

        assert len(results) == 1
        assert results[0]["type"] == "file"

        # Verify stored in database
        stored_secrets = list_secrets(temp_db)
        assert len(stored_secrets) == 1
        assert "ssh-rsa" in stored_secrets[0]["value"]

    def test_parse_assistant_response_mixed_secrets(self, temp_db):
        """Test parsing response with both text and file secrets."""
        response = """
        Text secret: 🔐 SECRET_TEXT: password123

        File secret:
        🔐 SECRET_FILE_START
        private-key-content
        🔐 SECRET_FILE_END

        Another text: 🔐 SECRET_TEXT: token-xyz
        """

        results = parse_assistant_response(response, temp_db)

        assert len(results) == 3

        # Verify types
        text_secrets = [r for r in results if r["type"] == "text"]
        file_secrets = [r for r in results if r["type"] == "file"]
        assert len(text_secrets) == 2
        assert len(file_secrets) == 1

        # Verify all stored
        stored_secrets = list_secrets(temp_db)
        assert len(stored_secrets) == 3

    def test_parse_assistant_response_no_secrets(self, temp_db):
        """Test parsing response with no secrets returns empty list."""
        response = "This is a normal response with no secret declarations"

        results = parse_assistant_response(response, temp_db)

        assert results == []

        # Verify nothing stored
        stored_secrets = list_secrets(temp_db)
        assert stored_secrets == []

    def test_parse_assistant_response_duplicate_secrets(self, temp_db):
        """Test that duplicate secret values return same ID and are deduplicated."""
        response = """
        🔐 SECRET_TEXT: same-value
        🔐 SECRET_TEXT: same-value
        """

        results = parse_assistant_response(response, temp_db)

        # Both parse attempts should return results
        assert len(results) == 2

        # But both should have the same ID (deduplication)
        assert results[0]["id"] == results[1]["id"]

        # Only one secret should be stored in database
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
