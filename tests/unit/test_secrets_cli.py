"""
Unit tests for secrets CLI commands.

Following strict TDD methodology - these tests are written FIRST and will FAIL
until the CLI commands are implemented.
"""

import os
import tempfile
import pytest
from unittest.mock import patch

# This import will work but the secrets command won't exist yet
from src.pacemaker.user_commands import parse_command, execute_command
from src.pacemaker.secrets.database import create_secret, list_secrets


@pytest.fixture
def temp_db():
    """Create a temporary database file for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    # Cleanup
    if os.path.exists(path):
        os.remove(path)


class TestParseSecretsCommands:
    """Test parsing of secrets CLI commands."""

    def test_parse_secrets_add(self):
        """Test parsing 'pace-maker secrets add' command."""
        result = parse_command("pace-maker secrets add")

        assert result["is_pace_maker_command"] is True
        assert result["command"] == "secrets"
        assert result["subcommand"] == "add"

    def test_parse_secrets_addfile(self):
        """Test parsing 'pace-maker secrets addfile <path>' command."""
        result = parse_command("pace-maker secrets addfile /tmp/secret.txt")

        assert result["is_pace_maker_command"] is True
        assert result["command"] == "secrets"
        assert result["subcommand"] == "addfile /tmp/secret.txt"

    def test_parse_secrets_list(self):
        """Test parsing 'pace-maker secrets list' command."""
        result = parse_command("pace-maker secrets list")

        assert result["is_pace_maker_command"] is True
        assert result["command"] == "secrets"
        assert result["subcommand"] == "list"

    def test_parse_secrets_remove(self):
        """Test parsing 'pace-maker secrets remove <id>' command."""
        result = parse_command("pace-maker secrets remove 123")

        assert result["is_pace_maker_command"] is True
        assert result["command"] == "secrets"
        assert result["subcommand"] == "remove 123"

    def test_parse_secrets_clear(self):
        """Test parsing 'pace-maker secrets clear' command."""
        result = parse_command("pace-maker secrets clear")

        assert result["is_pace_maker_command"] is True
        assert result["command"] == "secrets"
        assert result["subcommand"] == "clear"

    def test_parse_secrets_case_insensitive(self):
        """Test that secrets commands are case-insensitive."""
        result = parse_command("PACE-MAKER SECRETS LIST")

        assert result["is_pace_maker_command"] is True
        assert result["command"] == "secrets"


class TestExecuteSecretsAdd:
    """Test execution of 'secrets add' command."""

    @patch("getpass.getpass")
    def test_secrets_add_prompts_for_secret(self, mock_getpass, temp_db):
        """Test that 'secrets add' prompts for secret value."""
        mock_getpass.return_value = "my-secret-value"

        result = execute_command("secrets", None, db_path=temp_db, subcommand="add")

        # Should have prompted for secret
        mock_getpass.assert_called_once()

        # Should return success
        assert result["success"] is True
        assert (
            "added" in result["message"].lower()
            or "stored" in result["message"].lower()
        )

    @patch("getpass.getpass")
    def test_secrets_add_stores_in_database(self, mock_getpass, temp_db):
        """Test that secret is actually stored in database."""
        mock_getpass.return_value = "test-secret-123"

        execute_command("secrets", None, db_path=temp_db, subcommand="add")

        # Verify stored
        secrets = list_secrets(temp_db)
        assert len(secrets) == 1
        assert secrets[0]["value"] == "test-secret-123"
        assert secrets[0]["type"] == "text"

    @patch("getpass.getpass")
    def test_secrets_add_returns_secret_id(self, mock_getpass, temp_db):
        """Test that response includes the secret ID."""
        mock_getpass.return_value = "secret"

        result = execute_command("secrets", None, db_path=temp_db, subcommand="add")

        assert result["success"] is True
        # Message should contain ID reference
        assert "id" in result["message"].lower() or "#" in result["message"]

    @patch("getpass.getpass")
    def test_secrets_add_empty_value_fails(self, mock_getpass, temp_db):
        """Test that adding empty secret fails."""
        mock_getpass.return_value = ""

        result = execute_command("secrets", None, db_path=temp_db, subcommand="add")

        assert result["success"] is False
        assert "empty" in result["message"].lower()


class TestExecuteSecretsAddfile:
    """Test execution of 'secrets addfile' command."""

    def test_secrets_addfile_reads_and_stores_file(self, temp_db):
        """Test that addfile reads file and stores content."""
        # Create temp file with content
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("secret file content\nline 2")
            temp_file = f.name

        try:
            result = execute_command(
                "secrets", None, db_path=temp_db, subcommand=f"addfile {temp_file}"
            )

            assert result["success"] is True

            # Verify stored
            secrets = list_secrets(temp_db)
            assert len(secrets) == 1
            assert "secret file content" in secrets[0]["value"]
            assert secrets[0]["type"] == "file"
        finally:
            os.unlink(temp_file)

    def test_secrets_addfile_nonexistent_file_fails(self, temp_db):
        """Test that addfile with non-existent file fails."""
        result = execute_command(
            "secrets", None, db_path=temp_db, subcommand="addfile /nonexistent/path.txt"
        )

        assert result["success"] is False
        assert (
            "not found" in result["message"].lower()
            or "does not exist" in result["message"].lower()
        )

    def test_secrets_addfile_no_path_fails(self, temp_db):
        """Test that addfile without path fails."""
        result = execute_command("secrets", None, db_path=temp_db, subcommand="addfile")

        assert result["success"] is False
        assert (
            "path" in result["message"].lower() or "file" in result["message"].lower()
        )


class TestExecuteSecretsList:
    """Test execution of 'secrets list' command."""

    def test_secrets_list_empty_database(self, temp_db):
        """Test listing secrets from empty database."""
        result = execute_command("secrets", None, db_path=temp_db, subcommand="list")

        assert result["success"] is True
        assert (
            "no secrets" in result["message"].lower()
            or "empty" in result["message"].lower()
        )

    def test_secrets_list_shows_secrets_masked(self, temp_db):
        """Test that list shows secrets with masked values."""
        # Add some secrets
        create_secret(temp_db, "text", "short")
        create_secret(temp_db, "text", "longer-secret-value")
        create_secret(temp_db, "file", "file content here")

        result = execute_command("secrets", None, db_path=temp_db, subcommand="list")

        assert result["success"] is True

        # Should mask values
        assert "***" in result["message"]

        # Should NOT show full secrets
        assert "longer-secret-value" not in result["message"]
        assert "file content here" not in result["message"]

    def test_secrets_list_shows_id_and_type(self, temp_db):
        """Test that list shows ID and type for each secret."""
        id1 = create_secret(temp_db, "text", "secret1")
        id2 = create_secret(temp_db, "file", "secret2")

        result = execute_command("secrets", None, db_path=temp_db, subcommand="list")

        message = result["message"]

        # Should show IDs
        assert str(id1) in message
        assert str(id2) in message

        # Should show types
        assert "text" in message.lower()
        assert "file" in message.lower()


class TestExecuteSecretsRemove:
    """Test execution of 'secrets remove' command."""

    def test_secrets_remove_existing_secret(self, temp_db):
        """Test removing an existing secret."""
        secret_id = create_secret(temp_db, "text", "to-remove")

        result = execute_command(
            "secrets", None, db_path=temp_db, subcommand=f"remove {secret_id}"
        )

        assert result["success"] is True
        assert (
            "removed" in result["message"].lower()
            or "deleted" in result["message"].lower()
        )

        # Verify actually removed
        secrets = list_secrets(temp_db)
        assert len(secrets) == 0

    def test_secrets_remove_nonexistent_secret(self, temp_db):
        """Test removing non-existent secret fails."""
        result = execute_command(
            "secrets", None, db_path=temp_db, subcommand="remove 99999"
        )

        assert result["success"] is False
        assert "not found" in result["message"].lower()

    def test_secrets_remove_no_id_fails(self, temp_db):
        """Test that remove without ID fails."""
        result = execute_command("secrets", None, db_path=temp_db, subcommand="remove")

        assert result["success"] is False


class TestExecuteSecretsClear:
    """Test execution of 'secrets clear' command."""

    @patch("builtins.input")
    def test_secrets_clear_prompts_confirmation(self, mock_input, temp_db):
        """Test that clear prompts for confirmation."""
        create_secret(temp_db, "text", "secret1")
        create_secret(temp_db, "text", "secret2")

        mock_input.return_value = "yes"

        result = execute_command("secrets", None, db_path=temp_db, subcommand="clear")

        # Should have prompted
        mock_input.assert_called_once()
        assert result["success"] is True

    @patch("builtins.input")
    def test_secrets_clear_deletes_all_when_confirmed(self, mock_input, temp_db):
        """Test that clear deletes all secrets when confirmed."""
        create_secret(temp_db, "text", "secret1")
        create_secret(temp_db, "text", "secret2")
        create_secret(temp_db, "file", "secret3")

        mock_input.return_value = "yes"

        execute_command("secrets", None, db_path=temp_db, subcommand="clear")

        # Verify all deleted
        secrets = list_secrets(temp_db)
        assert len(secrets) == 0

    @patch("builtins.input")
    def test_secrets_clear_shows_count(self, mock_input, temp_db):
        """Test that clear shows count of deleted secrets."""
        create_secret(temp_db, "text", "secret1")
        create_secret(temp_db, "text", "secret2")

        mock_input.return_value = "yes"

        result = execute_command("secrets", None, db_path=temp_db, subcommand="clear")

        assert result["success"] is True
        assert "2" in result["message"]  # Should mention count

    @patch("builtins.input")
    def test_secrets_clear_cancelled_when_not_confirmed(self, mock_input, temp_db):
        """Test that clear is cancelled when not confirmed."""
        create_secret(temp_db, "text", "secret1")

        mock_input.return_value = "no"

        result = execute_command("secrets", None, db_path=temp_db, subcommand="clear")

        assert result["success"] is True
        assert (
            "cancelled" in result["message"].lower()
            or "aborted" in result["message"].lower()
        )

        # Verify nothing deleted
        secrets = list_secrets(temp_db)
        assert len(secrets) == 1

    @patch("builtins.input")
    def test_secrets_clear_empty_database(self, mock_input, temp_db):
        """Test clearing empty database."""
        mock_input.return_value = "yes"

        result = execute_command("secrets", None, db_path=temp_db, subcommand="clear")

        assert result["success"] is True
        assert "0" in result["message"]  # Should mention 0 deleted
