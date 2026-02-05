#!/usr/bin/env python3
"""
Unit tests for telemetry JSONL parser.

Tests:
- Extract session metadata (session_id, model, timestamp)
- Extract user_id from OAuth profile email
- Count messages in session
- Handle empty/malformed transcripts
"""

import json
import os
import tempfile
import unittest

from src.pacemaker.telemetry import jsonl_parser


class TestTelemetryJsonlParser(unittest.TestCase):
    """Test telemetry JSONL parser"""

    def setUp(self):
        """Create temporary test transcript."""
        self.test_dir = tempfile.mkdtemp()
        self.transcript_path = os.path.join(self.test_dir, "transcript.jsonl")

    def tearDown(self):
        """Clean up temporary files."""
        import shutil

        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_test_transcript(self, entries: list):
        """Helper to create test transcript file."""
        with open(self.transcript_path, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

    def test_parse_session_metadata(self):
        """Extract session_id, model, and timestamp from transcript"""
        # Arrange - Create transcript with session start event
        entries = [
            {
                "type": "session_start",
                "session_id": "test-session-123",
                "model": "claude-sonnet-4-5",
                "timestamp": "2026-02-03T10:00:00Z",
            }
        ]
        self._create_test_transcript(entries)

        # Act
        metadata = jsonl_parser.parse_session_metadata(self.transcript_path)

        # Assert
        self.assertEqual(metadata["session_id"], "test-session-123")
        self.assertEqual(metadata["model"], "claude-sonnet-4-5")
        self.assertIsNotNone(metadata["timestamp"])

    def test_extract_user_id_from_oauth(self):
        """Extract user_id from OAuth profile email in transcript"""
        # Arrange - Create transcript with OAuth profile event
        entries = [
            {
                "type": "auth_profile",
                "profile": {
                    "email": "user@example.com",
                    "name": "Test User",
                },
            }
        ]
        self._create_test_transcript(entries)

        # Act
        user_id = jsonl_parser.extract_user_id(self.transcript_path)

        # Assert
        self.assertEqual(user_id, "user@example.com")

    def test_count_messages(self):
        """Count total messages (user + assistant) in transcript"""
        # Arrange - Create transcript with multiple messages
        entries = [
            {
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Hello"}],
                }
            },
            {
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Hi"}],
                }
            },
            {
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "How are you"}],
                }
            },
            {
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Good"}],
                }
            },
        ]
        self._create_test_transcript(entries)

        # Act
        count = jsonl_parser.count_messages(self.transcript_path)

        # Assert
        self.assertEqual(count, 4)

    def test_parse_empty_transcript(self):
        """Handle empty transcript gracefully"""
        # Arrange - Create empty transcript
        self._create_test_transcript([])

        # Act
        metadata = jsonl_parser.parse_session_metadata(self.transcript_path)

        # Assert - Returns defaults without crashing
        self.assertIsNotNone(metadata)
        self.assertIn("session_id", metadata)


if __name__ == "__main__":
    unittest.main()
