"""
Unit tests for secrets database CRUD operations.

Following strict TDD methodology - these tests are written FIRST and will FAIL
until the database module is implemented.
"""

import os
import sqlite3
import tempfile
import pytest

# This import will FAIL initially - that's expected in TDD
from src.pacemaker.secrets.database import (
    create_secret,
    list_secrets,
    get_all_secrets,
    remove_secret,
    clear_all_secrets,
    deduplicate_secrets,
)


@pytest.fixture
def temp_db():
    """Create a temporary database file for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    # Cleanup
    if os.path.exists(path):
        os.remove(path)


class TestCreateSecret:
    """Test secret creation in database."""

    def test_create_text_secret_returns_id(self, temp_db):
        """Test that creating a text secret returns an integer ID."""
        secret_id = create_secret(temp_db, "text", "my-api-key-12345")
        assert isinstance(secret_id, int)
        assert secret_id > 0

    def test_create_file_secret_returns_id(self, temp_db):
        """Test that creating a file secret returns an integer ID."""
        file_content = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ..."
        secret_id = create_secret(temp_db, "file", file_content)
        assert isinstance(secret_id, int)
        assert secret_id > 0

    def test_create_secret_initializes_database(self, temp_db):
        """Test that create_secret creates the database schema if it doesn't exist."""
        # Database file doesn't exist yet
        assert not os.path.exists(temp_db) or os.path.getsize(temp_db) == 0

        # Create a secret
        create_secret(temp_db, "text", "secret-value")

        # Database should now exist with proper schema
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()

        # Check table exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='secrets'"
        )
        assert cursor.fetchone() is not None

        # Check schema columns
        cursor.execute("PRAGMA table_info(secrets)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        assert "id" in columns
        assert "type" in columns
        assert "value" in columns
        assert "created_at" in columns

        conn.close()

    def test_create_secret_sets_correct_permissions(self, temp_db):
        """Test that database file is created with 0600 permissions."""
        create_secret(temp_db, "text", "secret")

        # Check file permissions (owner read/write only)
        stat_info = os.stat(temp_db)
        permissions = stat_info.st_mode & 0o777
        assert permissions == 0o600, f"Expected 0600, got {oct(permissions)}"

    def test_create_multiple_secrets_returns_sequential_ids(self, temp_db):
        """Test that multiple secrets get sequential IDs."""
        id1 = create_secret(temp_db, "text", "secret1")
        id2 = create_secret(temp_db, "text", "secret2")
        id3 = create_secret(temp_db, "file", "secret3")

        assert id2 == id1 + 1
        assert id3 == id2 + 1

    def test_create_duplicate_secret_returns_existing_id(self, temp_db):
        """Test that creating a duplicate secret returns the existing ID."""
        id1 = create_secret(temp_db, "text", "duplicate-value")
        id2 = create_secret(temp_db, "text", "duplicate-value")

        assert id1 == id2

    def test_create_duplicate_secret_does_not_create_new_row(self, temp_db):
        """Test that duplicate secrets don't create new database rows."""
        create_secret(temp_db, "text", "duplicate-value")
        create_secret(temp_db, "text", "duplicate-value")
        create_secret(temp_db, "text", "duplicate-value")

        secrets = list_secrets(temp_db)
        # Should only have one row despite 3 create calls
        assert len(secrets) == 1
        assert secrets[0]["value"] == "duplicate-value"

    def test_create_duplicate_different_types_creates_separate_secrets(self, temp_db):
        """Test that same value with different types are separate secrets."""
        id1 = create_secret(temp_db, "text", "same-value")
        id2 = create_secret(temp_db, "file", "same-value")

        # Different types should get different IDs
        assert id1 != id2

        secrets = list_secrets(temp_db)
        assert len(secrets) == 2


class TestListSecrets:
    """Test listing all secrets with metadata."""

    def test_list_secrets_empty_database(self, temp_db):
        """Test listing secrets from empty database returns empty list."""
        # Initialize database
        create_secret(temp_db, "text", "dummy")
        conn = sqlite3.connect(temp_db)
        conn.execute("DELETE FROM secrets")
        conn.commit()
        conn.close()

        secrets = list_secrets(temp_db)
        assert secrets == []

    def test_list_secrets_returns_all_metadata(self, temp_db):
        """Test that list_secrets returns all secret metadata."""
        id1 = create_secret(temp_db, "text", "secret1")
        id2 = create_secret(temp_db, "file", "secret2")

        secrets = list_secrets(temp_db)

        assert len(secrets) == 2

        # Check first secret
        secret1 = next(s for s in secrets if s["id"] == id1)
        assert secret1["type"] == "text"
        assert secret1["value"] == "secret1"
        assert "created_at" in secret1
        assert isinstance(secret1["created_at"], int)

        # Check second secret
        secret2 = next(s for s in secrets if s["id"] == id2)
        assert secret2["type"] == "file"
        assert secret2["value"] == "secret2"

    def test_list_secrets_ordered_by_id(self, temp_db):
        """Test that secrets are returned in ID order."""
        id1 = create_secret(temp_db, "text", "secret1")
        id2 = create_secret(temp_db, "text", "secret2")
        id3 = create_secret(temp_db, "text", "secret3")

        secrets = list_secrets(temp_db)

        assert secrets[0]["id"] == id1
        assert secrets[1]["id"] == id2
        assert secrets[2]["id"] == id3


class TestGetAllSecrets:
    """Test retrieving all secret values."""

    def test_get_all_secrets_empty_database(self, temp_db):
        """Test getting secrets from empty database returns empty list."""
        # Initialize database
        create_secret(temp_db, "text", "dummy")
        conn = sqlite3.connect(temp_db)
        conn.execute("DELETE FROM secrets")
        conn.commit()
        conn.close()

        secrets = get_all_secrets(temp_db)
        assert secrets == []

    def test_get_all_secrets_returns_only_values(self, temp_db):
        """Test that get_all_secrets returns only the secret values."""
        create_secret(temp_db, "text", "api-key-12345")
        create_secret(temp_db, "file", "ssh-rsa AAAAB3...")
        create_secret(temp_db, "text", "password123")

        secrets = get_all_secrets(temp_db)

        assert len(secrets) == 3
        assert "api-key-12345" in secrets
        assert "ssh-rsa AAAAB3..." in secrets
        assert "password123" in secrets

        # Ensure we only got strings, no metadata
        for secret in secrets:
            assert isinstance(secret, str)

    def test_get_all_secrets_includes_both_types(self, temp_db):
        """Test that both text and file secrets are included."""
        create_secret(temp_db, "text", "text-secret")
        create_secret(temp_db, "file", "file-secret")

        secrets = get_all_secrets(temp_db)

        assert len(secrets) == 2
        assert "text-secret" in secrets
        assert "file-secret" in secrets


class TestRemoveSecret:
    """Test secret removal by ID."""

    def test_remove_secret_returns_true_on_success(self, temp_db):
        """Test that removing an existing secret returns True."""
        secret_id = create_secret(temp_db, "text", "to-delete")

        result = remove_secret(temp_db, secret_id)

        assert result is True

    def test_remove_secret_deletes_from_database(self, temp_db):
        """Test that secret is actually deleted from database."""
        id1 = create_secret(temp_db, "text", "keep")
        id2 = create_secret(temp_db, "text", "delete")
        id3 = create_secret(temp_db, "text", "keep2")

        remove_secret(temp_db, id2)

        secrets = list_secrets(temp_db)
        assert len(secrets) == 2
        assert any(s["id"] == id1 for s in secrets)
        assert any(s["id"] == id3 for s in secrets)
        assert not any(s["id"] == id2 for s in secrets)

    def test_remove_secret_returns_false_for_nonexistent_id(self, temp_db):
        """Test that removing non-existent secret returns False."""
        create_secret(temp_db, "text", "dummy")

        result = remove_secret(temp_db, 99999)

        assert result is False

    def test_remove_secret_from_empty_database(self, temp_db):
        """Test removing from empty database returns False."""
        # Initialize database
        create_secret(temp_db, "text", "dummy")
        conn = sqlite3.connect(temp_db)
        conn.execute("DELETE FROM secrets")
        conn.commit()
        conn.close()

        result = remove_secret(temp_db, 1)
        assert result is False


class TestClearAllSecrets:
    """Test clearing all secrets from database."""

    def test_clear_all_secrets_returns_count(self, temp_db):
        """Test that clear_all_secrets returns number of deleted secrets."""
        create_secret(temp_db, "text", "secret1")
        create_secret(temp_db, "text", "secret2")
        create_secret(temp_db, "file", "secret3")

        count = clear_all_secrets(temp_db)

        assert count == 3

    def test_clear_all_secrets_deletes_all(self, temp_db):
        """Test that all secrets are deleted from database."""
        create_secret(temp_db, "text", "secret1")
        create_secret(temp_db, "text", "secret2")

        clear_all_secrets(temp_db)

        secrets = list_secrets(temp_db)
        assert secrets == []

    def test_clear_all_secrets_empty_database(self, temp_db):
        """Test clearing empty database returns 0."""
        # Initialize database
        create_secret(temp_db, "text", "dummy")
        conn = sqlite3.connect(temp_db)
        conn.execute("DELETE FROM secrets")
        conn.commit()
        conn.close()

        count = clear_all_secrets(temp_db)
        assert count == 0

    def test_clear_all_secrets_table_still_exists(self, temp_db):
        """Test that clearing secrets doesn't drop the table."""
        create_secret(temp_db, "text", "secret")
        clear_all_secrets(temp_db)

        # Should be able to add new secrets after clearing
        new_id = create_secret(temp_db, "text", "new-secret")
        assert isinstance(new_id, int)
        assert new_id > 0


class TestDatabaseIndex:
    """Test that database index is created correctly."""

    def test_index_exists_on_type_column(self, temp_db):
        """Test that an index exists on the type column for performance."""
        create_secret(temp_db, "text", "dummy")

        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()

        # Check index exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_secrets_type'"
        )
        result = cursor.fetchone()

        assert result is not None, "Index idx_secrets_type should exist"

        conn.close()


class TestDeduplicateSecrets:
    """Test deduplication of existing duplicate secrets."""

    def test_deduplicate_secrets_removes_duplicates(self, temp_db):
        """Test that deduplicate_secrets removes duplicate entries."""
        # Manually insert duplicates by bypassing create_secret
        conn = sqlite3.connect(temp_db)
        from src.pacemaker.secrets.database import _init_database

        _init_database(temp_db)

        conn.execute(
            "INSERT INTO secrets (type, value) VALUES (?, ?)", ("text", "duplicate")
        )
        conn.execute(
            "INSERT INTO secrets (type, value) VALUES (?, ?)", ("text", "duplicate")
        )
        conn.execute(
            "INSERT INTO secrets (type, value) VALUES (?, ?)", ("text", "duplicate")
        )
        conn.execute(
            "INSERT INTO secrets (type, value) VALUES (?, ?)", ("text", "unique")
        )
        conn.commit()
        conn.close()

        removed = deduplicate_secrets(temp_db)

        assert removed == 2  # 3 duplicates - 1 kept = 2 removed

        secrets = list_secrets(temp_db)
        assert len(secrets) == 2  # One duplicate + one unique
        duplicate_secrets = [s for s in secrets if s["value"] == "duplicate"]
        assert len(duplicate_secrets) == 1

    def test_deduplicate_secrets_keeps_lowest_id(self, temp_db):
        """Test that deduplicate_secrets keeps the entry with lowest ID."""
        # Manually insert duplicates
        conn = sqlite3.connect(temp_db)
        from src.pacemaker.secrets.database import _init_database

        _init_database(temp_db)

        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO secrets (type, value) VALUES (?, ?)", ("text", "dup")
        )
        id1 = cursor.lastrowid
        cursor.execute(
            "INSERT INTO secrets (type, value) VALUES (?, ?)", ("text", "dup")
        )
        cursor.execute(
            "INSERT INTO secrets (type, value) VALUES (?, ?)", ("text", "dup")
        )
        conn.commit()
        conn.close()

        deduplicate_secrets(temp_db)

        secrets = list_secrets(temp_db)
        assert len(secrets) == 1
        assert secrets[0]["id"] == id1  # Lowest ID should be kept

    def test_deduplicate_secrets_respects_type(self, temp_db):
        """Test that same value with different types are not deduplicated."""
        # Manually insert same value with different types
        conn = sqlite3.connect(temp_db)
        from src.pacemaker.secrets.database import _init_database

        _init_database(temp_db)

        conn.execute(
            "INSERT INTO secrets (type, value) VALUES (?, ?)", ("text", "same")
        )
        conn.execute(
            "INSERT INTO secrets (type, value) VALUES (?, ?)", ("text", "same")
        )
        conn.execute(
            "INSERT INTO secrets (type, value) VALUES (?, ?)", ("file", "same")
        )
        conn.execute(
            "INSERT INTO secrets (type, value) VALUES (?, ?)", ("file", "same")
        )
        conn.commit()
        conn.close()

        removed = deduplicate_secrets(temp_db)

        assert removed == 2  # 1 text duplicate + 1 file duplicate

        secrets = list_secrets(temp_db)
        assert len(secrets) == 2  # One text + one file
        types = [s["type"] for s in secrets]
        assert "text" in types
        assert "file" in types

    def test_deduplicate_secrets_no_duplicates_returns_zero(self, temp_db):
        """Test that deduplicate_secrets returns 0 when no duplicates exist."""
        create_secret(temp_db, "text", "unique1")
        create_secret(temp_db, "text", "unique2")
        create_secret(temp_db, "file", "unique3")

        removed = deduplicate_secrets(temp_db)

        assert removed == 0

        secrets = list_secrets(temp_db)
        assert len(secrets) == 3

    def test_deduplicate_secrets_empty_database_returns_zero(self, temp_db):
        """Test that deduplicate_secrets returns 0 for empty database."""
        from src.pacemaker.secrets.database import _init_database

        _init_database(temp_db)

        removed = deduplicate_secrets(temp_db)

        assert removed == 0
