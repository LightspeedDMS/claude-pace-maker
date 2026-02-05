#!/usr/bin/env python3
"""
Unit tests for tool call extractor (AC4).

Tests:
- AC4: Extract tool calls from transcript JSONL
- Extract tool names from tool_use entries
- Handle duplicate tool names (include all calls)
- Handle empty transcripts
- Handle missing files gracefully
- Handle invalid JSON gracefully
"""

import json
import os
import tempfile
import unittest

from src.pacemaker.telemetry.tool_call_extractor import extract_tool_calls


class TestToolCallExtractor(unittest.TestCase):
    """Test tool call extraction from transcript"""

    def setUp(self):
        """Set up test fixtures."""
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        """Clean up temporary files."""
        import shutil

        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_extract_tool_calls_single_tool(self):
        """Test extraction of single tool call"""
        # Arrange
        transcript_path = os.path.join(self.test_dir, "transcript.jsonl")
        with open(transcript_path, "w") as f:
            entry = {
                "type": "tool_use",
                "name": "Read",
                "input": {"file_path": "/some/path.py"},
            }
            f.write(json.dumps(entry) + "\n")

        # Act
        tool_calls = extract_tool_calls(transcript_path)

        # Assert
        self.assertEqual(tool_calls, ["Read"])

    def test_extract_tool_calls_multiple_different_tools(self):
        """Test extraction of multiple different tools"""
        # Arrange
        transcript_path = os.path.join(self.test_dir, "transcript.jsonl")
        with open(transcript_path, "w") as f:
            tools = ["Read", "Write", "Bash", "Grep", "Edit"]
            for tool_name in tools:
                entry = {"type": "tool_use", "name": tool_name}
                f.write(json.dumps(entry) + "\n")

        # Act
        tool_calls = extract_tool_calls(transcript_path)

        # Assert
        self.assertEqual(tool_calls, ["Read", "Write", "Bash", "Grep", "Edit"])

    def test_extract_tool_calls_duplicate_tools_included(self):
        """Test duplicate tool calls are all included"""
        # Arrange
        transcript_path = os.path.join(self.test_dir, "transcript.jsonl")
        with open(transcript_path, "w") as f:
            # Multiple Read calls
            for _ in range(3):
                entry = {"type": "tool_use", "name": "Read"}
                f.write(json.dumps(entry) + "\n")

            # Multiple Write calls
            for _ in range(2):
                entry = {"type": "tool_use", "name": "Write"}
                f.write(json.dumps(entry) + "\n")

        # Act
        tool_calls = extract_tool_calls(transcript_path)

        # Assert - all calls included
        self.assertEqual(tool_calls, ["Read", "Read", "Read", "Write", "Write"])
        self.assertEqual(len(tool_calls), 5)

    def test_extract_tool_calls_order_preserved(self):
        """Test tool calls are returned in order they appear"""
        # Arrange
        transcript_path = os.path.join(self.test_dir, "transcript.jsonl")
        with open(transcript_path, "w") as f:
            sequence = ["Read", "Write", "Read", "Bash", "Write", "Grep"]
            for tool_name in sequence:
                entry = {"type": "tool_use", "name": tool_name}
                f.write(json.dumps(entry) + "\n")

        # Act
        tool_calls = extract_tool_calls(transcript_path)

        # Assert
        self.assertEqual(tool_calls, sequence)

    def test_extract_tool_calls_non_tool_use_entries_ignored(self):
        """Test entries without type=tool_use are ignored"""
        # Arrange
        transcript_path = os.path.join(self.test_dir, "transcript.jsonl")
        with open(transcript_path, "w") as f:
            # Tool use entry
            f.write(json.dumps({"type": "tool_use", "name": "Read"}) + "\n")

            # Other entry types (should be ignored)
            f.write(json.dumps({"type": "message", "content": "Hello"}) + "\n")
            f.write(json.dumps({"type": "response", "text": "World"}) + "\n")

            # Another tool use entry
            f.write(json.dumps({"type": "tool_use", "name": "Write"}) + "\n")

        # Act
        tool_calls = extract_tool_calls(transcript_path)

        # Assert - only tool_use entries
        self.assertEqual(tool_calls, ["Read", "Write"])

    def test_extract_tool_calls_entries_without_name_ignored(self):
        """Test tool_use entries without 'name' field are ignored"""
        # Arrange
        transcript_path = os.path.join(self.test_dir, "transcript.jsonl")
        with open(transcript_path, "w") as f:
            # Valid tool use
            f.write(json.dumps({"type": "tool_use", "name": "Read"}) + "\n")

            # tool_use without name (malformed)
            f.write(json.dumps({"type": "tool_use", "input": {}}) + "\n")

            # Valid tool use
            f.write(json.dumps({"type": "tool_use", "name": "Write"}) + "\n")

        # Act
        tool_calls = extract_tool_calls(transcript_path)

        # Assert - only valid entries with name
        self.assertEqual(tool_calls, ["Read", "Write"])

    def test_extract_tool_calls_empty_file_returns_empty_list(self):
        """Test empty transcript returns empty list"""
        # Arrange
        transcript_path = os.path.join(self.test_dir, "empty.jsonl")
        with open(transcript_path, "w"):
            pass  # Empty file

        # Act
        tool_calls = extract_tool_calls(transcript_path)

        # Assert
        self.assertEqual(tool_calls, [])

    def test_extract_tool_calls_no_tool_use_entries_returns_empty_list(self):
        """Test transcript without tool_use entries returns empty list"""
        # Arrange
        transcript_path = os.path.join(self.test_dir, "no_tools.jsonl")
        with open(transcript_path, "w") as f:
            # Only non-tool entries
            f.write(json.dumps({"type": "message", "content": "Hello"}) + "\n")
            f.write(json.dumps({"type": "response", "text": "World"}) + "\n")

        # Act
        tool_calls = extract_tool_calls(transcript_path)

        # Assert
        self.assertEqual(tool_calls, [])

    def test_extract_tool_calls_file_not_found_graceful_failure(self):
        """Test missing file returns empty list (graceful failure)"""
        # Arrange
        nonexistent_path = os.path.join(self.test_dir, "does_not_exist.jsonl")

        # Act
        tool_calls = extract_tool_calls(nonexistent_path)

        # Assert - returns empty list, doesn't raise
        self.assertEqual(tool_calls, [])

    def test_extract_tool_calls_invalid_json_graceful_failure(self):
        """Test invalid JSON returns empty list (graceful failure)"""
        # Arrange
        transcript_path = os.path.join(self.test_dir, "invalid.jsonl")
        with open(transcript_path, "w") as f:
            f.write("not valid json\n")

        # Act
        tool_calls = extract_tool_calls(transcript_path)

        # Assert - returns empty list, doesn't raise
        self.assertEqual(tool_calls, [])

    def test_extract_tool_calls_partial_invalid_json_processes_until_error(self):
        """Test processing stops at first invalid JSON line"""
        # Arrange
        transcript_path = os.path.join(self.test_dir, "partial_invalid.jsonl")
        with open(transcript_path, "w") as f:
            # Valid entries
            f.write(json.dumps({"type": "tool_use", "name": "Read"}) + "\n")
            f.write(json.dumps({"type": "tool_use", "name": "Write"}) + "\n")

            # Invalid JSON
            f.write("invalid json line\n")

            # Valid entry after invalid (should not be processed)
            f.write(json.dumps({"type": "tool_use", "name": "Bash"}) + "\n")

        # Act
        tool_calls = extract_tool_calls(transcript_path)

        # Assert - processes entries before error
        self.assertEqual(tool_calls, ["Read", "Write"])

    def test_extract_tool_calls_realistic_session(self):
        """Test extraction from realistic session transcript"""
        # Arrange
        transcript_path = os.path.join(self.test_dir, "session.jsonl")
        with open(transcript_path, "w") as f:
            # Realistic session sequence
            f.write(json.dumps({"type": "message", "role": "user"}) + "\n")
            f.write(json.dumps({"type": "tool_use", "name": "Read"}) + "\n")
            f.write(json.dumps({"type": "tool_result", "output": "..."}) + "\n")
            f.write(json.dumps({"type": "tool_use", "name": "Grep"}) + "\n")
            f.write(json.dumps({"type": "tool_result", "output": "..."}) + "\n")
            f.write(json.dumps({"type": "tool_use", "name": "Edit"}) + "\n")
            f.write(json.dumps({"type": "tool_result", "output": "..."}) + "\n")
            f.write(json.dumps({"type": "tool_use", "name": "Bash"}) + "\n")
            f.write(json.dumps({"type": "tool_result", "output": "..."}) + "\n")
            f.write(json.dumps({"type": "response", "text": "Done"}) + "\n")

        # Act
        tool_calls = extract_tool_calls(transcript_path)

        # Assert
        self.assertEqual(tool_calls, ["Read", "Grep", "Edit", "Bash"])


if __name__ == "__main__":
    unittest.main()
