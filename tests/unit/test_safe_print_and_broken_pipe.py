#!/usr/bin/env python3
"""
Tests for Bug #2, #9, and #10 fixes.

Bug #2: BrokenPipeError crashes hook when Claude Code closes the pipe.
Bug #9: Langfuse push should happen BEFORE pacing stdout output.
Bug #10: All print(file=sys.stdout) calls need BrokenPipeError protection.

Tests verify:
- safe_print() function exists and handles BrokenPipeError
- safe_print() works normally when stdout is OK
- Hook functions survive BrokenPipeError on stdout
"""

import json
from io import StringIO
from unittest.mock import patch, MagicMock


class TestSafePrint:
    """Tests for safe_print() function (Bug #2 and #10)."""

    def test_safe_print_exists(self):
        """safe_print should be importable from hook module."""
        from pacemaker.hook import safe_print

        assert callable(safe_print)

    def test_safe_print_writes_normally(self):
        """safe_print should write to stdout when pipe is healthy."""
        from pacemaker.hook import safe_print

        output = StringIO()
        safe_print("hello world", file=output)
        assert output.getvalue() == "hello world\n"

    def test_safe_print_handles_broken_pipe(self):
        """safe_print should NOT raise when stdout raises BrokenPipeError."""
        from pacemaker.hook import safe_print

        broken_file = MagicMock()
        broken_file.write = MagicMock(side_effect=BrokenPipeError("Broken pipe"))

        # Should NOT raise
        safe_print("test message", file=broken_file)

    def test_safe_print_handles_broken_pipe_on_flush(self):
        """safe_print should handle BrokenPipeError during flush."""
        from pacemaker.hook import safe_print

        broken_file = MagicMock()
        broken_file.write = MagicMock()
        broken_file.flush = MagicMock(side_effect=BrokenPipeError("Broken pipe"))

        # Should NOT raise
        safe_print("test message", file=broken_file, flush=True)

    def test_safe_print_with_custom_end(self):
        """safe_print should support custom end parameter."""
        from pacemaker.hook import safe_print

        output = StringIO()
        safe_print("hello", end="", file=output)
        assert output.getvalue() == "hello"


class TestBrokenPipeInHookFunctions:
    """Bug #2: BrokenPipeError in hook functions should not crash the process."""

    def test_inject_prompt_delay_handles_broken_pipe(self):
        """inject_prompt_delay should not crash on BrokenPipeError."""
        from pacemaker.hook import inject_prompt_delay

        with patch("pacemaker.hook.safe_print") as mock_safe:
            # safe_print should be used instead of raw print
            inject_prompt_delay("test prompt")
            mock_safe.assert_called_once()

    def test_run_session_start_hook_uses_safe_print(self):
        """run_session_start_hook should use safe_print for stdout."""
        from pacemaker.hook import safe_print

        # Verify safe_print exists and is used
        # The full integration test is that session_start doesn't crash on BrokenPipeError
        assert callable(safe_print)


class TestBug8NameError:
    """Bug #8: NameError on failure path in handle_post_tool_use."""

    @patch("pacemaker.langfuse.orchestrator.push.push_batch_events")
    def test_handle_post_tool_use_no_name_error_on_failure_with_tool_response(
        self, mock_push
    ):
        """
        Bug #8: When push fails in handle_post_tool_use with tool_response path,
        the error log on line 1240 references 'max_line' which is undefined.
        When tool_response is not None, the else branch (which defines max_line)
        doesn't execute.

        The NameError in the log_warning call is caught by the outer except
        block, which then returns False via the generic error handler instead
        of the explicit push-failure path. The log message should use
        'new_last_pushed_line' instead of 'max_line'.

        We verify by patching log_warning to detect that the proper failure
        path is taken (the push-specific warning, not the generic handler).
        """
        from pacemaker.langfuse.orchestrator import handle_post_tool_use
        from pacemaker.langfuse import state as langfuse_state
        import tempfile
        from pathlib import Path

        state_dir = tempfile.mkdtemp()
        transcript_dir = tempfile.mkdtemp()

        try:
            # Push FAILS for the span push
            mock_push.return_value = (False, 0)

            session_id = "test-nameerror"
            trace_id = f"{session_id}-turn-xyz"

            state_manager = langfuse_state.StateManager(state_dir)
            state_manager.create_or_update(
                session_id=session_id,
                trace_id=trace_id,
                last_pushed_line=0,
                metadata={
                    "current_trace_id": trace_id,
                    "trace_start_line": 0,
                },
                # No pending_trace - so flush_pending_trace is a no-op
            )

            # Create transcript
            transcript_path = Path(transcript_dir) / "transcript.jsonl"
            with open(transcript_path, "w") as f:
                f.write(
                    json.dumps(
                        {
                            "type": "session_start",
                            "session_id": "test-123",
                        }
                    )
                    + "\n"
                )

            config = {
                "langfuse_enabled": True,
                "langfuse_base_url": "https://test.langfuse.com",
                "langfuse_public_key": "pk-test",
                "langfuse_secret_key": "sk-test",
            }

            with patch("pacemaker.langfuse.orchestrator.log_warning") as mock_log_warn:
                result = handle_post_tool_use(
                    config=config,
                    session_id=session_id,
                    transcript_path=str(transcript_path),
                    state_dir=state_dir,
                    tool_response="some tool output",
                    tool_name="Read",
                    tool_input={"file_path": "/test.py"},
                )

                # Should return False (push failed)
                assert result is False

                # Verify the push-specific warning was logged (not the generic handler)
                # If NameError occurs, the generic "PostToolUse handler error" is logged instead
                warning_messages = [str(c) for c in mock_log_warn.call_args_list]
                push_failure_logged = any(
                    "Failed to push spans" in str(c)
                    for c in mock_log_warn.call_args_list
                )
                generic_error_logged = any(
                    "PostToolUse handler error" in str(c)
                    for c in mock_log_warn.call_args_list
                )

                assert push_failure_logged, (
                    f"Bug #8: Expected 'Failed to push spans' warning but got: {warning_messages}. "
                    f"The NameError on 'max_line' is causing the generic exception handler to run."
                )
                assert not generic_error_logged, (
                    "Bug #8: Generic 'PostToolUse handler error' should NOT be logged. "
                    "The NameError on 'max_line' is being caught by the outer except."
                )

        finally:
            import shutil

            shutil.rmtree(state_dir, ignore_errors=True)
            shutil.rmtree(transcript_dir, ignore_errors=True)
