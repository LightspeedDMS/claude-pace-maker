"""
Unit tests for secrets masking engine.

Following strict TDD methodology - these tests are written FIRST and will FAIL
until the masking module is implemented.
"""

# This import will FAIL initially - that's expected in TDD
from src.pacemaker.secrets.masking import mask_text, mask_structure


class TestMaskText:
    """Test text masking with secret replacement."""

    def test_mask_single_secret(self):
        """Test masking a single secret occurrence."""
        content = "The API key is abc123def456 for authentication"
        secrets = ["abc123def456"]

        masked, count = mask_text(content, secrets)

        assert "abc123def456" not in masked
        assert "*** MASKED ***" in masked
        assert "The API key is" in masked
        assert "for authentication" in masked
        assert count == 1

    def test_mask_multiple_occurrences_of_same_secret(self):
        """Test masking multiple occurrences of the same secret."""
        content = "Key: secret123, again: secret123, and once more: secret123"
        secrets = ["secret123"]

        masked, count = mask_text(content, secrets)

        assert "secret123" not in masked
        assert masked.count("*** MASKED ***") == 3
        assert count == 3

    def test_mask_multiple_different_secrets(self):
        """Test masking multiple different secrets."""
        content = "Password: pass123, Token: token456, API key: key789"
        secrets = ["pass123", "token456", "key789"]

        masked, count = mask_text(content, secrets)

        assert "pass123" not in masked
        assert "token456" not in masked
        assert "key789" not in masked
        assert masked.count("*** MASKED ***") == 3
        assert count == 3

    def test_mask_text_case_sensitive(self):
        """Test that masking is case-sensitive."""
        content = "Secret: MySecret and MYSECRET and mysecret"
        secrets = ["MySecret"]

        masked, count = mask_text(content, secrets)

        # Only exact case match should be masked
        assert "MySecret" not in masked
        assert "MYSECRET" in masked  # Different case, not masked
        assert "mysecret" in masked  # Different case, not masked
        assert masked.count("*** MASKED ***") == 1
        assert count == 1

    def test_mask_text_no_secrets(self):
        """Test masking with empty secrets list returns original text."""
        content = "This is normal content"
        secrets = []

        masked, count = mask_text(content, secrets)

        assert masked == content
        assert count == 0

    def test_mask_text_no_matches(self):
        """Test masking when no secrets match returns original text."""
        content = "This is normal content"
        secrets = ["not-in-content", "also-not-there"]

        masked, count = mask_text(content, secrets)

        assert masked == content
        assert count == 0

    def test_mask_text_partial_match_not_masked(self):
        """Test that partial matches are not masked (only exact matches)."""
        content = "The value is abc123def456xyz"
        secrets = ["abc123def456"]  # Shorter than actual content

        masked, count = mask_text(content, secrets)

        # Exact match should be masked even within larger string
        assert "abc123def456" not in masked
        assert "*** MASKED ***" in masked
        assert count == 1

    def test_mask_text_empty_string(self):
        """Test masking empty string."""
        content = ""
        secrets = ["secret"]

        masked, count = mask_text(content, secrets)

        assert masked == ""
        assert count == 0

    def test_mask_text_multiline_content(self):
        """Test masking secrets in multiline content."""
        content = """Line 1: secret123
Line 2: normal
Line 3: secret123 again"""
        secrets = ["secret123"]

        masked, count = mask_text(content, secrets)

        assert "secret123" not in masked
        assert masked.count("*** MASKED ***") == 2
        assert "Line 2: normal" in masked
        assert count == 2


class TestMaskStructure:
    """Test deep structure masking with recursive traversal."""

    def test_mask_simple_dict(self):
        """Test masking secrets in a simple dictionary."""
        data = {"key": "value", "secret": "password123"}
        secrets = ["password123"]

        masked, count = mask_structure(data, secrets)

        # Original should not be modified
        assert data["secret"] == "password123"

        # Masked copy should have secret replaced
        assert masked["secret"] == "*** MASKED ***"
        assert masked["key"] == "value"
        assert count == 1

    def test_mask_nested_dict(self):
        """Test masking secrets in nested dictionaries."""
        data = {"outer": {"inner": {"secret": "api-key-123", "normal": "value"}}}
        secrets = ["api-key-123"]

        masked, count = mask_structure(data, secrets)

        assert masked["outer"]["inner"]["secret"] == "*** MASKED ***"
        assert masked["outer"]["inner"]["normal"] == "value"
        # Original unchanged
        assert data["outer"]["inner"]["secret"] == "api-key-123"
        assert count == 1

    def test_mask_list(self):
        """Test masking secrets in a list."""
        data = ["normal", "secret123", "another"]
        secrets = ["secret123"]

        masked, count = mask_structure(data, secrets)

        assert masked[0] == "normal"
        assert masked[1] == "*** MASKED ***"
        assert masked[2] == "another"
        # Original unchanged
        assert data[1] == "secret123"
        assert count == 1

    def test_mask_mixed_structure(self):
        """Test masking in complex mixed structures (dicts, lists, nested)."""
        data = {
            "users": [
                {"name": "Alice", "token": "token-abc"},
                {"name": "Bob", "token": "token-xyz"},
            ],
            "config": {"api_key": "key-123", "timeout": 30},
        }
        secrets = ["token-abc", "key-123"]

        masked, count = mask_structure(data, secrets)

        assert masked["users"][0]["token"] == "*** MASKED ***"
        assert masked["users"][1]["token"] == "token-xyz"  # Not masked
        assert masked["config"]["api_key"] == "*** MASKED ***"
        assert masked["config"]["timeout"] == 30
        # Original unchanged
        assert data["users"][0]["token"] == "token-abc"
        assert count == 2

    def test_mask_structure_file_secret_replaces_entire_value(self):
        """Test that file secrets replace entire string value (per spec)."""
        file_secret = "-----BEGIN PRIVATE KEY-----\nMIIE...\n-----END PRIVATE KEY-----"
        data = {"key_content": file_secret, "normal": "value"}
        secrets = [file_secret]

        masked, count = mask_structure(data, secrets)

        # Entire value should be replaced since it contains file secret
        assert masked["key_content"] == "*** MASKED ***"
        assert masked["normal"] == "value"
        assert count == 1

    def test_mask_structure_non_string_values_unchanged(self):
        """Test that non-string values are not masked."""
        data = {"number": 42, "boolean": True, "null": None, "list": [1, 2, 3]}
        secrets = ["42", "True"]  # These shouldn't match non-strings

        masked, count = mask_structure(data, secrets)

        assert masked["number"] == 42
        assert masked["boolean"] is True
        assert masked["null"] is None
        assert masked["list"] == [1, 2, 3]
        assert count == 0

    def test_mask_structure_deep_copy(self):
        """Test that mask_structure returns a deep copy."""
        data = {"nested": {"value": "secret123"}}
        secrets = ["secret123"]

        masked, count = mask_structure(data, secrets)

        # Modify masked copy
        masked["nested"]["value"] = "modified"

        # Original should be unchanged
        assert data["nested"]["value"] == "secret123"
        assert count == 1

    def test_mask_structure_empty_dict(self):
        """Test masking empty dictionary."""
        data = {}
        secrets = ["secret"]

        masked, count = mask_structure(data, secrets)

        assert masked == {}
        assert count == 0

    def test_mask_structure_empty_list(self):
        """Test masking empty list."""
        data = []
        secrets = ["secret"]

        masked, count = mask_structure(data, secrets)

        assert masked == []
        assert count == 0

    def test_mask_structure_tuple(self):
        """Test masking tuples (should be preserved as tuples)."""
        data = ("normal", "secret123", "another")
        secrets = ["secret123"]

        masked, count = mask_structure(data, secrets)

        assert isinstance(masked, tuple)
        assert masked[0] == "normal"
        assert masked[1] == "*** MASKED ***"
        assert masked[2] == "another"
        assert count == 1

    def test_mask_structure_no_secrets(self):
        """Test masking with no secrets returns deep copy."""
        data = {"key": "value", "nested": {"inner": "data"}}
        secrets = []

        masked, count = mask_structure(data, secrets)

        # Should be equal but different objects
        assert masked == data
        assert masked is not data
        assert masked["nested"] is not data["nested"]
        assert count == 0

    def test_mask_structure_string_containing_secret_substring(self):
        """Test that strings containing secrets as substrings are properly masked."""
        data = {"message": "The secret is secret123 for access", "other": "normal"}
        secrets = ["secret123"]

        masked, count = mask_structure(data, secrets)

        assert "secret123" not in masked["message"]
        assert "*** MASKED ***" in masked["message"]
        assert "The secret is" in masked["message"]
        assert count == 1
