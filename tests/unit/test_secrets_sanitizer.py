"""
Unit tests for trace sanitizer.

Following strict TDD methodology - these tests are written FIRST and will FAIL
until the sanitizer module is implemented.
"""

import os
import tempfile
import pytest

# This import will FAIL initially - that's expected in TDD
from src.pacemaker.secrets.sanitizer import sanitize_trace
from src.pacemaker.secrets.database import create_secret


@pytest.fixture
def temp_db():
    """Create a temporary database file for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    # Cleanup
    if os.path.exists(path):
        os.remove(path)


class TestSanitizeTrace:
    """Test trace sanitization with secrets masking."""

    def test_sanitize_empty_trace(self, temp_db):
        """Test sanitizing an empty trace."""
        trace = {}

        sanitized = sanitize_trace(trace, temp_db)

        assert sanitized == {}
        assert sanitized is not trace  # Should be a copy

    def test_sanitize_trace_no_secrets(self, temp_db):
        """Test sanitizing trace when database has no secrets."""
        trace = {
            "name": "test-trace",
            "input": {"prompt": "hello world"},
            "output": {"response": "hi there"},
        }

        sanitized = sanitize_trace(trace, temp_db)

        # Should be deep copy with same content
        assert sanitized == trace
        assert sanitized is not trace
        assert sanitized["input"] is not trace["input"]

    def test_sanitize_trace_masks_text_secret(self, temp_db):
        """Test that text secrets in trace are masked."""
        # Store a secret
        create_secret(temp_db, "text", "api-key-12345")

        trace = {
            "name": "test-trace",
            "input": {"prompt": "Use this key: api-key-12345"},
            "output": {"response": "I used api-key-12345"},
        }

        sanitized = sanitize_trace(trace, temp_db)

        # Original should be unchanged
        assert "api-key-12345" in trace["input"]["prompt"]
        assert "api-key-12345" in trace["output"]["response"]

        # Sanitized should have secrets masked
        assert "api-key-12345" not in sanitized["input"]["prompt"]
        assert "api-key-12345" not in sanitized["output"]["response"]
        assert "*** MASKED ***" in sanitized["input"]["prompt"]
        assert "*** MASKED ***" in sanitized["output"]["response"]

    def test_sanitize_trace_masks_file_secret(self, temp_db):
        """Test that file secrets in trace are masked."""
        file_content = "-----BEGIN PRIVATE KEY-----\nMIIE...\n-----END PRIVATE KEY-----"
        create_secret(temp_db, "file", file_content)

        trace = {
            "tool": {"input": {"file_path": "/tmp/key.pem"}, "output": file_content}
        }

        sanitized = sanitize_trace(trace, temp_db)

        # Original unchanged
        assert trace["tool"]["output"] == file_content

        # Sanitized should mask entire value
        assert sanitized["tool"]["output"] == "*** MASKED ***"

    def test_sanitize_trace_masks_multiple_secrets(self, temp_db):
        """Test masking multiple different secrets in trace."""
        create_secret(temp_db, "text", "password123")
        create_secret(temp_db, "text", "token-xyz")
        create_secret(temp_db, "file", "ssh-key-content")

        trace = {
            "steps": [
                {"action": "login", "password": "password123"},
                {"action": "auth", "token": "token-xyz"},
                {"action": "connect", "key": "ssh-key-content"},
            ]
        }

        sanitized = sanitize_trace(trace, temp_db)

        # All secrets should be masked
        assert sanitized["steps"][0]["password"] == "*** MASKED ***"
        assert sanitized["steps"][1]["token"] == "*** MASKED ***"
        assert sanitized["steps"][2]["key"] == "*** MASKED ***"

        # Original unchanged
        assert trace["steps"][0]["password"] == "password123"

    def test_sanitize_trace_deeply_nested_structure(self, temp_db):
        """Test sanitization of deeply nested trace structures."""
        create_secret(temp_db, "text", "secret")

        trace = {
            "level1": {
                "level2": {"level3": {"level4": {"data": "contains secret here"}}}
            }
        }

        sanitized = sanitize_trace(trace, temp_db)

        assert "secret" not in sanitized["level1"]["level2"]["level3"]["level4"]["data"]
        assert (
            "*** MASKED ***"
            in sanitized["level1"]["level2"]["level3"]["level4"]["data"]
        )

    def test_sanitize_trace_with_lists(self, temp_db):
        """Test sanitization of traces containing lists."""
        create_secret(temp_db, "text", "secret123")

        trace = {
            "items": ["normal value", "contains secret123 here", "another normal value"]
        }

        sanitized = sanitize_trace(trace, temp_db)

        assert sanitized["items"][0] == "normal value"
        assert "secret123" not in sanitized["items"][1]
        assert "*** MASKED ***" in sanitized["items"][1]
        assert sanitized["items"][2] == "another normal value"

    def test_sanitize_trace_preserves_non_string_types(self, temp_db):
        """Test that non-string values are preserved correctly."""
        create_secret(temp_db, "text", "secret")

        trace = {
            "number": 42,
            "boolean": True,
            "null": None,
            "float": 3.14,
            "list": [1, 2, 3],
        }

        sanitized = sanitize_trace(trace, temp_db)

        assert sanitized["number"] == 42
        assert sanitized["boolean"] is True
        assert sanitized["null"] is None
        assert sanitized["float"] == 3.14
        assert sanitized["list"] == [1, 2, 3]

    def test_sanitize_trace_with_tuples(self, temp_db):
        """Test sanitization preserves tuples."""
        create_secret(temp_db, "text", "secret")

        trace = {"tuple_data": ("normal", "has secret in it", "normal")}

        sanitized = sanitize_trace(trace, temp_db)

        assert isinstance(sanitized["tuple_data"], tuple)
        assert sanitized["tuple_data"][0] == "normal"
        assert "secret" not in sanitized["tuple_data"][1]
        assert sanitized["tuple_data"][2] == "normal"

    def test_sanitize_trace_realistic_langfuse_structure(self, temp_db):
        """Test sanitization of realistic Langfuse trace structure."""
        create_secret(temp_db, "text", "sk-proj-abc123")

        trace = {
            "id": "trace-123",
            "name": "main-conversation",
            "input": {"prompt": "Use API key sk-proj-abc123 to connect"},
            "output": {"response": "Connected with key sk-proj-abc123"},
            "metadata": {"user_id": "user-456", "session": "sess-789"},
        }

        sanitized = sanitize_trace(trace, temp_db)

        # Metadata should be unchanged
        assert sanitized["id"] == "trace-123"
        assert sanitized["metadata"]["user_id"] == "user-456"

        # Secrets should be masked
        assert "sk-proj-abc123" not in sanitized["input"]["prompt"]
        assert "sk-proj-abc123" not in sanitized["output"]["response"]
        assert "*** MASKED ***" in sanitized["input"]["prompt"]

    def test_sanitize_trace_secret_in_tool_io(self, temp_db):
        """Test masking secrets in tool input/output."""
        create_secret(temp_db, "text", "token-abc")

        trace = {
            "spans": [
                {
                    "name": "tool_use",
                    "input": {"command": "auth token-abc"},
                    "output": {"result": "authenticated with token-abc"},
                }
            ]
        }

        sanitized = sanitize_trace(trace, temp_db)

        assert "token-abc" not in sanitized["spans"][0]["input"]["command"]
        assert "token-abc" not in sanitized["spans"][0]["output"]["result"]
        assert "*** MASKED ***" in sanitized["spans"][0]["input"]["command"]

    def test_sanitize_trace_empty_database(self, temp_db):
        """Test sanitization when database is empty (no secrets stored)."""
        # Don't store any secrets

        trace = {"data": "this could be a secret but isn't in database"}

        sanitized = sanitize_trace(trace, temp_db)

        # Should return identical copy (no masking)
        assert sanitized == trace
        assert sanitized is not trace
