#!/usr/bin/env python3
"""
Unit tests for token usage extractor (AC4).

Tests:
- AC4: Extract token usage from transcript JSONL
- Aggregate input_tokens, output_tokens, cache_read_tokens
- Handle missing transcript files gracefully
- Handle invalid JSON gracefully
- Handle I/O errors gracefully
- Multiple usage entries aggregation
"""

import json
import os
import tempfile
import unittest

from src.pacemaker.telemetry.token_extractor import extract_token_usage


class TestTokenExtractor(unittest.TestCase):
    """Test token usage extraction from transcript"""

    def setUp(self):
        """Set up test fixtures."""
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        """Clean up temporary files."""
        import shutil

        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_extract_token_usage_single_entry(self):
        """Test extraction from single usage entry"""
        # Arrange
        transcript_path = os.path.join(self.test_dir, "transcript.jsonl")
        with open(transcript_path, "w") as f:
            entry = {
                "type": "response",
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "cache_read_input_tokens": 200,
                },
            }
            f.write(json.dumps(entry) + "\n")

        # Act
        usage = extract_token_usage(transcript_path)

        # Assert
        self.assertEqual(usage["input_tokens"], 1000)
        self.assertEqual(usage["output_tokens"], 500)
        self.assertEqual(usage["cache_read_tokens"], 200)

    def test_extract_token_usage_multiple_entries_aggregated(self):
        """Test aggregation across multiple usage entries"""
        # Arrange
        transcript_path = os.path.join(self.test_dir, "transcript.jsonl")
        with open(transcript_path, "w") as f:
            # First turn
            entry1 = {
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "cache_read_input_tokens": 100,
                }
            }
            f.write(json.dumps(entry1) + "\n")

            # Second turn
            entry2 = {
                "usage": {
                    "input_tokens": 2000,
                    "output_tokens": 800,
                    "cache_read_input_tokens": 150,
                }
            }
            f.write(json.dumps(entry2) + "\n")

            # Third turn
            entry3 = {
                "usage": {
                    "input_tokens": 500,
                    "output_tokens": 300,
                    "cache_read_input_tokens": 50,
                }
            }
            f.write(json.dumps(entry3) + "\n")

        # Act
        usage = extract_token_usage(transcript_path)

        # Assert - should sum all entries
        self.assertEqual(usage["input_tokens"], 3500)  # 1000 + 2000 + 500
        self.assertEqual(usage["output_tokens"], 1600)  # 500 + 800 + 300
        self.assertEqual(usage["cache_read_tokens"], 300)  # 100 + 150 + 50

    def test_extract_token_usage_entries_without_usage_ignored(self):
        """Test entries without 'usage' field are ignored"""
        # Arrange
        transcript_path = os.path.join(self.test_dir, "transcript.jsonl")
        with open(transcript_path, "w") as f:
            # Entry with usage
            entry1 = {
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "cache_read_input_tokens": 100,
                }
            }
            f.write(json.dumps(entry1) + "\n")

            # Entry without usage (should be ignored)
            entry2 = {"type": "message", "content": "Hello"}
            f.write(json.dumps(entry2) + "\n")

            # Another entry with usage
            entry3 = {
                "usage": {
                    "input_tokens": 500,
                    "output_tokens": 300,
                    "cache_read_input_tokens": 50,
                }
            }
            f.write(json.dumps(entry3) + "\n")

        # Act
        usage = extract_token_usage(transcript_path)

        # Assert - only entries with usage counted
        self.assertEqual(usage["input_tokens"], 1500)  # 1000 + 500
        self.assertEqual(usage["output_tokens"], 800)  # 500 + 300
        self.assertEqual(usage["cache_read_tokens"], 150)  # 100 + 50

    def test_extract_token_usage_missing_token_fields_default_zero(self):
        """Test missing token fields are treated as 0"""
        # Arrange
        transcript_path = os.path.join(self.test_dir, "transcript.jsonl")
        with open(transcript_path, "w") as f:
            # Entry with only input_tokens
            entry1 = {"usage": {"input_tokens": 1000}}
            f.write(json.dumps(entry1) + "\n")

            # Entry with only output_tokens
            entry2 = {"usage": {"output_tokens": 500}}
            f.write(json.dumps(entry2) + "\n")

            # Entry with no cache tokens
            entry3 = {"usage": {"input_tokens": 200, "output_tokens": 100}}
            f.write(json.dumps(entry3) + "\n")

        # Act
        usage = extract_token_usage(transcript_path)

        # Assert
        self.assertEqual(usage["input_tokens"], 1200)  # 1000 + 0 + 200
        self.assertEqual(usage["output_tokens"], 600)  # 0 + 500 + 100
        self.assertEqual(usage["cache_read_tokens"], 0)  # No cache tokens

    def test_extract_token_usage_empty_file_returns_zeros(self):
        """Test empty transcript file returns zero usage"""
        # Arrange
        transcript_path = os.path.join(self.test_dir, "empty.jsonl")
        with open(transcript_path, "w"):
            pass  # Empty file

        # Act
        usage = extract_token_usage(transcript_path)

        # Assert
        self.assertEqual(usage["input_tokens"], 0)
        self.assertEqual(usage["output_tokens"], 0)
        self.assertEqual(usage["cache_read_tokens"], 0)

    def test_extract_token_usage_file_not_found_graceful_failure(self):
        """Test missing file returns zero usage (graceful failure)"""
        # Arrange
        nonexistent_path = os.path.join(self.test_dir, "does_not_exist.jsonl")

        # Act
        usage = extract_token_usage(nonexistent_path)

        # Assert - returns zero usage, doesn't raise
        self.assertEqual(usage["input_tokens"], 0)
        self.assertEqual(usage["output_tokens"], 0)
        self.assertEqual(usage["cache_read_tokens"], 0)

    def test_extract_token_usage_invalid_json_graceful_failure(self):
        """Test invalid JSON returns zero usage (graceful failure)"""
        # Arrange
        transcript_path = os.path.join(self.test_dir, "invalid.jsonl")
        with open(transcript_path, "w") as f:
            f.write("not valid json\n")
            f.write('{"valid": "json"}\n')
            f.write("also not valid\n")

        # Act
        usage = extract_token_usage(transcript_path)

        # Assert - returns zero usage, doesn't raise
        self.assertEqual(usage["input_tokens"], 0)
        self.assertEqual(usage["output_tokens"], 0)
        self.assertEqual(usage["cache_read_tokens"], 0)

    def test_extract_token_usage_partial_invalid_json_stops_processing(self):
        """Test first invalid JSON line stops processing"""
        # Arrange
        transcript_path = os.path.join(self.test_dir, "partial_invalid.jsonl")
        with open(transcript_path, "w") as f:
            # Valid entry
            entry1 = {
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 500,
                }
            }
            f.write(json.dumps(entry1) + "\n")

            # Invalid JSON - should stop processing here
            f.write("invalid json line\n")

            # Valid entry after invalid (should not be processed)
            entry2 = {
                "usage": {
                    "input_tokens": 2000,
                    "output_tokens": 1000,
                }
            }
            f.write(json.dumps(entry2) + "\n")

        # Act
        usage = extract_token_usage(transcript_path)

        # Assert - processing continues until error, counts first entry, then stops
        # The JSONDecodeError is caught and processing stops, but first entry is counted
        self.assertEqual(usage["input_tokens"], 1000)
        self.assertEqual(usage["output_tokens"], 500)

    def test_extract_token_usage_cache_read_input_tokens_field_name(self):
        """Test correct mapping of cache_read_input_tokens to cache_read_tokens"""
        # Arrange
        transcript_path = os.path.join(self.test_dir, "transcript.jsonl")
        with open(transcript_path, "w") as f:
            entry = {
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "cache_read_input_tokens": 300,  # Source field name
                }
            }
            f.write(json.dumps(entry) + "\n")

        # Act
        usage = extract_token_usage(transcript_path)

        # Assert - mapped to cache_read_tokens
        self.assertEqual(usage["cache_read_tokens"], 300)

    def test_extract_token_usage_no_cache_field_zero(self):
        """Test entries without cache field contribute 0 to cache total"""
        # Arrange
        transcript_path = os.path.join(self.test_dir, "transcript.jsonl")
        with open(transcript_path, "w") as f:
            # Entry without cache field
            entry1 = {
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 500,
                }
            }
            f.write(json.dumps(entry1) + "\n")

            # Entry with cache field
            entry2 = {
                "usage": {
                    "input_tokens": 500,
                    "output_tokens": 300,
                    "cache_read_input_tokens": 100,
                }
            }
            f.write(json.dumps(entry2) + "\n")

        # Act
        usage = extract_token_usage(transcript_path)

        # Assert
        self.assertEqual(usage["cache_read_tokens"], 100)  # Only from entry2

    def test_extract_token_usage_mixed_valid_invalid_entries(self):
        """Test file with mix of valid and invalid entries"""
        # Arrange
        transcript_path = os.path.join(self.test_dir, "mixed.jsonl")
        with open(transcript_path, "w") as f:
            # Valid entry
            entry1 = {
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 500,
                }
            }
            f.write(json.dumps(entry1) + "\n")

            # Entry without usage field (valid JSON, but no usage)
            entry2 = {"type": "message"}
            f.write(json.dumps(entry2) + "\n")

            # Valid entry with usage
            entry3 = {
                "usage": {
                    "input_tokens": 500,
                    "output_tokens": 300,
                }
            }
            f.write(json.dumps(entry3) + "\n")

        # Act
        usage = extract_token_usage(transcript_path)

        # Assert - both valid usage entries counted
        self.assertEqual(usage["input_tokens"], 1500)
        self.assertEqual(usage["output_tokens"], 800)

    def test_extract_token_usage_large_numbers(self):
        """Test extraction handles large token counts"""
        # Arrange
        transcript_path = os.path.join(self.test_dir, "large.jsonl")
        with open(transcript_path, "w") as f:
            entry = {
                "usage": {
                    "input_tokens": 100000,
                    "output_tokens": 50000,
                    "cache_read_input_tokens": 25000,
                }
            }
            f.write(json.dumps(entry) + "\n")

        # Act
        usage = extract_token_usage(transcript_path)

        # Assert
        self.assertEqual(usage["input_tokens"], 100000)
        self.assertEqual(usage["output_tokens"], 50000)
        self.assertEqual(usage["cache_read_tokens"], 25000)

    def test_extract_token_usage_zero_values(self):
        """Test extraction handles zero token counts"""
        # Arrange
        transcript_path = os.path.join(self.test_dir, "zeros.jsonl")
        with open(transcript_path, "w") as f:
            entry = {
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_input_tokens": 0,
                }
            }
            f.write(json.dumps(entry) + "\n")

        # Act
        usage = extract_token_usage(transcript_path)

        # Assert
        self.assertEqual(usage["input_tokens"], 0)
        self.assertEqual(usage["output_tokens"], 0)
        self.assertEqual(usage["cache_read_tokens"], 0)


if __name__ == "__main__":
    unittest.main()
