#!/usr/bin/env python3
"""
Negative test cases for lifecycle.py error handling.

Tests exception paths, malformed JSON, file permission errors,
and edge cases to achieve >90% code coverage.
"""

import unittest
import tempfile
import os
from unittest.mock import patch


class TestLifecycleErrorHandling(unittest.TestCase):
    """Test error handling in lifecycle module."""

    def setUp(self):
        """Set up temp environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.state_path = os.path.join(self.temp_dir, "state.json")

    def tearDown(self):
        """Clean up."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    # NOTE: has_implementation_started() removed - Stop hook now scans conversation transcripts

    # NOTE: has_implementation_completed() removed - Stop hook now scans conversation transcripts

    def test_mark_implementation_started_with_corrupted_existing_state(self):
        """Should handle corrupted state file gracefully when marking started."""
        from pacemaker.lifecycle import mark_implementation_started

        # Write corrupted JSON to state file
        with open(self.state_path, "w") as f:
            f.write("corrupted{json}")

        # Capture printed error message
        captured_output = []

        def mock_print(*args, **kwargs):
            captured_output.append(" ".join(str(a) for a in args))

        # Should print error but not crash
        with patch("builtins.print", side_effect=mock_print):
            mark_implementation_started(self.state_path)

        # Verify error was logged
        self.assertTrue(
            any(
                "Error marking implementation started" in msg for msg in captured_output
            )
        )

        # State file should still contain corrupted data (function doesn't recover)
        with open(self.state_path) as f:
            content = f.read()
            self.assertEqual(content, "corrupted{json}")

    def test_mark_implementation_started_with_file_write_error(self):
        """Should gracefully handle file write errors."""
        from pacemaker.lifecycle import mark_implementation_started

        # Mock open() to raise IOError during write
        with patch("builtins.open", side_effect=IOError("Disk full")):
            # Capture printed error message
            captured_output = []

            def mock_print(*args, **kwargs):
                captured_output.append(" ".join(str(a) for a in args))

            with patch("builtins.print", side_effect=mock_print):
                mark_implementation_started(self.state_path)

            # Verify error was logged
            self.assertTrue(
                any(
                    "Error marking implementation started" in msg
                    for msg in captured_output
                )
            )

    def test_mark_implementation_completed_with_file_write_error(self):
        """Should gracefully handle file write errors."""
        from pacemaker.lifecycle import mark_implementation_completed

        # Mock open() to raise PermissionError
        with patch("builtins.open", side_effect=PermissionError("Access denied")):
            # Capture printed error message
            captured_output = []

            def mock_print(*args, **kwargs):
                captured_output.append(" ".join(str(a) for a in args))

            with patch("builtins.print", side_effect=mock_print):
                mark_implementation_completed(self.state_path)

            # Verify error was logged
            self.assertTrue(
                any(
                    "Error marking implementation completed" in msg
                    for msg in captured_output
                )
            )

    def test_clear_lifecycle_markers_with_nonexistent_file(self):
        """Should return early when state file doesn't exist."""
        from pacemaker.lifecycle import clear_lifecycle_markers

        # Call with non-existent file
        nonexistent = os.path.join(self.temp_dir, "does_not_exist.json")

        # Should not crash
        clear_lifecycle_markers(nonexistent)

        # File should still not exist
        self.assertFalse(os.path.exists(nonexistent))

    def test_clear_lifecycle_markers_with_corrupted_json(self):
        """Should handle corrupted JSON gracefully."""
        from pacemaker.lifecycle import clear_lifecycle_markers

        # Write corrupted JSON
        with open(self.state_path, "w") as f:
            f.write("not json")

        # Capture printed error message
        captured_output = []

        def mock_print(*args, **kwargs):
            captured_output.append(" ".join(str(a) for a in args))

        with patch("builtins.print", side_effect=mock_print):
            clear_lifecycle_markers(self.state_path)

        # Verify error was logged
        self.assertTrue(
            any("Error clearing lifecycle markers" in msg for msg in captured_output)
        )

    def test_get_stop_hook_prompt_count_with_corrupted_json(self):
        """Should return 0 when state file is corrupted."""
        from pacemaker.lifecycle import get_stop_hook_prompt_count

        # Write corrupted JSON
        with open(self.state_path, "w") as f:
            f.write("{bad json")

        # Should return 0 instead of crashing
        count = get_stop_hook_prompt_count(self.state_path)
        self.assertEqual(count, 0)

    def test_get_stop_hook_prompt_count_with_nonexistent_file(self):
        """Should return 0 when state file doesn't exist."""
        from pacemaker.lifecycle import get_stop_hook_prompt_count

        nonexistent = os.path.join(self.temp_dir, "nope.json")

        count = get_stop_hook_prompt_count(nonexistent)
        self.assertEqual(count, 0)

    # NOTE: has_implementation_completed() removed - Stop hook now scans conversation transcripts

    def test_increment_stop_hook_prompt_count_with_file_write_error(self):
        """Should handle file write errors gracefully."""
        from pacemaker.lifecycle import increment_stop_hook_prompt_count

        # Mock open() to raise OSError
        with patch("builtins.open", side_effect=OSError("I/O error")):
            # Capture printed error message
            captured_output = []

            def mock_print(*args, **kwargs):
                captured_output.append(" ".join(str(a) for a in args))

            with patch("builtins.print", side_effect=mock_print):
                increment_stop_hook_prompt_count(self.state_path)

            # Verify error was logged
            self.assertTrue(
                any(
                    "Error incrementing stop hook prompt count" in msg
                    for msg in captured_output
                )
            )

    def test_mark_implementation_started_with_json_decode_error_on_read(self):
        """Should handle JSON decode error when reading existing state."""
        from pacemaker.lifecycle import mark_implementation_started

        # Create corrupted state file
        with open(self.state_path, "w") as f:
            f.write("corrupted json content")

        # Capture printed error message
        captured_output = []

        def mock_print(*args, **kwargs):
            captured_output.append(" ".join(str(a) for a in args))

        # Should handle error gracefully without crashing
        with patch("builtins.print", side_effect=mock_print):
            mark_implementation_started(self.state_path)

        # Verify error was logged
        self.assertTrue(
            any(
                "Error marking implementation started" in msg for msg in captured_output
            )
        )

    def test_mark_implementation_completed_with_json_decode_error_on_read(self):
        """Should handle JSON decode error when reading existing state."""
        from pacemaker.lifecycle import mark_implementation_completed

        # Create corrupted state file
        with open(self.state_path, "w") as f:
            f.write("not json")

        # Capture printed error message
        captured_output = []

        def mock_print(*args, **kwargs):
            captured_output.append(" ".join(str(a) for a in args))

        # Should handle error gracefully without crashing
        with patch("builtins.print", side_effect=mock_print):
            mark_implementation_completed(self.state_path)

        # Verify error was logged
        self.assertTrue(
            any(
                "Error marking implementation completed" in msg
                for msg in captured_output
            )
        )


if __name__ == "__main__":
    unittest.main()
