#!/usr/bin/env python3
"""
Tests for langfuse backfill functionality.

Tests AC4 (Historical Session Backfill) and AC5 (Error Handling).
"""

import os
from datetime import datetime, timedelta
from unittest.mock import patch

from src.pacemaker.langfuse import backfill


class TestBackfillSessionDiscovery:
    """Tests for AC4: Session Discovery."""

    def test_find_sessions_since_date(self, tmp_path):
        """Find all sessions modified since cutoff date."""
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        # Create old session (should be excluded)
        old_session = transcripts_dir / "old_session.jsonl"
        old_session.write_text('{"type":"session_start"}\n')
        old_time = (datetime.now() - timedelta(days=10)).timestamp()
        os.utime(old_session, (old_time, old_time))

        # Create recent sessions (should be included)
        recent1 = transcripts_dir / "recent1.jsonl"
        recent1.write_text('{"type":"session_start"}\n')

        recent2 = transcripts_dir / "recent2.jsonl"
        recent2.write_text('{"type":"session_start"}\n')

        # Find sessions since 5 days ago
        cutoff = datetime.now() - timedelta(days=5)
        sessions = backfill.find_sessions_since(str(transcripts_dir), cutoff)

        # Should find exactly 2 recent sessions, not old one
        assert len(sessions) == 2
        paths = [s["path"] for s in sessions]
        assert str(recent1) in paths
        assert str(recent2) in paths
        assert str(old_session) not in paths

    def test_find_sessions_empty_directory(self, tmp_path):
        """Empty directory returns empty list."""
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        cutoff = datetime.now() - timedelta(days=5)
        sessions = backfill.find_sessions_since(str(transcripts_dir), cutoff)

        assert sessions == []

    def test_find_sessions_nonexistent_directory(self):
        """Nonexistent directory returns empty list."""
        cutoff = datetime.now() - timedelta(days=5)
        sessions = backfill.find_sessions_since("/nonexistent/path", cutoff)

        assert sessions == []

    def test_find_sessions_handles_directory_errors(self, tmp_path):
        """Exception during directory scan returns empty list (covers lines 54-56)."""
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        # Create a file that will cause an error when stat() is called
        session = transcripts_dir / "session.jsonl"
        session.write_text('{"type":"session_start"}\n')

        # Mock Path.glob to raise exception during iteration
        with patch("pathlib.Path.glob") as mock_glob:
            mock_glob.side_effect = PermissionError("Access denied")

            cutoff = datetime.now() - timedelta(days=5)
            sessions = backfill.find_sessions_since(str(transcripts_dir), cutoff)

            # Should return empty list and log warning
            assert sessions == []


class TestPushSession:
    """Tests for push_session() function (lines 100-130)."""

    def test_push_session_success_with_real_data(self, tmp_path):
        """Successful push with real transcript data, only mocking external API."""
        transcript = tmp_path / "session.jsonl"
        # Create real transcript data that parsers can handle
        transcript.write_text(
            '{"type":"session_start","session_id":"test-sess-001","model":"claude-opus-4","timestamp":"2024-01-01T12:00:00Z"}\n'
            '{"type":"user_message","content":"Hello"}\n'
            '{"type":"token_usage","input_tokens":100,"output_tokens":50,"cache_read_tokens":0,"cache_creation_tokens":0}\n'
        )

        # Only mock the external API boundary
        with patch("src.pacemaker.langfuse.backfill.push_trace") as mock_push:
            mock_push.return_value = True

            result = backfill.push_session(
                str(transcript),
                "http://test.langfuse.com",
                "pk-test-key",
                "sk-test-secret",
            )

            # Should succeed
            assert result is True
            # Verify API was called once
            assert mock_push.call_count == 1

    def test_push_session_network_failure(self, tmp_path):
        """Push fails when Langfuse API returns error."""
        transcript = tmp_path / "session.jsonl"
        transcript.write_text(
            '{"type":"session_start","session_id":"test-sess-002","model":"claude-opus-4","timestamp":"2024-01-01T12:00:00Z"}\n'
        )

        # Mock API to return failure
        with patch("src.pacemaker.langfuse.backfill.push_trace") as mock_push:
            mock_push.return_value = False

            result = backfill.push_session(
                str(transcript),
                "http://test.langfuse.com",
                "pk-test-key",
                "sk-test-secret",
            )

            assert result is False

    def test_push_session_parsing_error(self, tmp_path):
        """Push fails gracefully when transcript is corrupt."""
        transcript = tmp_path / "corrupt.jsonl"
        transcript.write_text("not valid json at all\n")

        result = backfill.push_session(
            str(transcript), "http://test.langfuse.com", "pk-test-key", "sk-test-secret"
        )

        # Should return False and log warning
        assert result is False

    def test_push_session_nonexistent_file(self):
        """Push fails gracefully for nonexistent file."""
        result = backfill.push_session(
            "/nonexistent/transcript.jsonl",
            "http://test.langfuse.com",
            "pk-test-key",
            "sk-test-secret",
        )

        assert result is False


class TestProcessSingleSession:
    """Tests for _process_single_session() error handling (lines 162-169)."""

    def test_process_single_session_corrupt_transcript(self, tmp_path):
        """Corrupt transcript triggers error handling path (lines 165-169)."""
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        # Create corrupt transcript
        corrupt = transcripts_dir / "corrupt.jsonl"
        corrupt.write_text("not valid json\n")

        session = {"path": str(corrupt)}

        # Call _process_single_session directly
        status = backfill._process_single_session(
            session,
            base_url="http://test",
            public_key="pk",
            secret_key="sk",
            progress=False,
        )

        # Should return 'failed' status
        assert status == "failed"

    def test_process_single_session_unreadable_metadata(self, tmp_path):
        """Unreadable metadata triggers error path (line 162)."""
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        # Create transcript with missing required fields
        incomplete = transcripts_dir / "incomplete.jsonl"
        incomplete.write_text('{"type":"other"}\n')  # Missing session_start

        session = {"path": str(incomplete)}

        # Mock parse_session_metadata to raise exception
        with patch(
            "src.pacemaker.langfuse.backfill.parse_session_metadata"
        ) as mock_parse:
            mock_parse.side_effect = KeyError("session_id not found")

            status = backfill._process_single_session(
                session,
                base_url="http://test",
                public_key="pk",
                secret_key="sk",
                progress=False,
            )

            # Should return 'failed' status
            assert status == "failed"


class TestBackfillProcessing:
    """Tests for AC4: Backfill Processing."""

    def test_backfill_processes_all_sessions(self, tmp_path):
        """Backfill processes all discovered sessions."""
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        # Create 3 sessions
        for i in range(3):
            session = transcripts_dir / f"session{i}.jsonl"
            session.write_text(f'{{"type":"session_start","session_id":"sess{i}"}}\n')

        with patch("src.pacemaker.langfuse.backfill.push_session") as mock_push:
            mock_push.return_value = True

            result = backfill.backfill_sessions(
                str(transcripts_dir),
                since=datetime.now() - timedelta(days=1),
                base_url="http://test",
                public_key="pk",
                secret_key="sk",
            )

            # Should have processed exactly 3 sessions
            assert result["total"] == 3
            assert mock_push.call_count == 3

    def test_backfill_shows_progress(self, tmp_path, capsys):
        """Backfill shows progress indicator."""
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        # Create sessions
        for i in range(5):
            session = transcripts_dir / f"session{i}.jsonl"
            session.write_text(f'{{"type":"session_start","session_id":"sess{i}"}}\n')

        with patch("src.pacemaker.langfuse.backfill.push_session") as mock_push:
            mock_push.return_value = True

            backfill.backfill_sessions(
                str(transcripts_dir),
                since=datetime.now() - timedelta(days=1),
                base_url="http://test",
                public_key="pk",
                secret_key="sk",
                progress=True,
            )

            # Check that progress was printed
            captured = capsys.readouterr()
            assert "Processing" in captured.out or "Processed" in captured.out

    def test_backfill_skips_already_pushed_sessions(self, tmp_path):
        """Already-pushed sessions are skipped."""
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        # Create session
        session = transcripts_dir / "session.jsonl"
        session.write_text('{"type":"session_start","session_id":"sess1"}\n')

        with (
            patch(
                "src.pacemaker.langfuse.backfill.is_session_pushed"
            ) as mock_is_pushed,
            patch("src.pacemaker.langfuse.backfill.push_session") as mock_push,
        ):

            # Mark session as already pushed
            mock_is_pushed.return_value = True

            result = backfill.backfill_sessions(
                str(transcripts_dir),
                since=datetime.now() - timedelta(days=1),
                base_url="http://test",
                public_key="pk",
                secret_key="sk",
            )

            # Should have skipped the session
            assert result["skipped"] == 1
            assert mock_push.call_count == 0

    def test_backfill_summary_counts(self, tmp_path):
        """Summary shows correct counts."""
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        # Create 3 sessions
        for i in range(3):
            session = transcripts_dir / f"session{i}.jsonl"
            session.write_text(f'{{"type":"session_start","session_id":"sess{i}"}}\n')

        with patch("src.pacemaker.langfuse.backfill.push_session") as mock_push:
            # First 2 succeed, third fails
            mock_push.side_effect = [True, True, False]

            result = backfill.backfill_sessions(
                str(transcripts_dir),
                since=datetime.now() - timedelta(days=1),
                base_url="http://test",
                public_key="pk",
                secret_key="sk",
            )

            # Verify exact counts
            assert result["total"] == 3
            assert result["success"] == 2
            assert result["failed"] == 1


class TestBackfillErrorHandling:
    """Tests for AC5: Error Handling."""

    def test_backfill_continues_after_network_error(self, tmp_path):
        """Network error doesn't stop backfill."""
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        # Create 3 sessions
        for i in range(3):
            session = transcripts_dir / f"session{i}.jsonl"
            session.write_text(f'{{"type":"session_start","session_id":"sess{i}"}}\n')

        with patch("src.pacemaker.langfuse.backfill.push_session") as mock_push:
            # Simulate network error on session 2
            mock_push.side_effect = [True, False, True]

            result = backfill.backfill_sessions(
                str(transcripts_dir),
                since=datetime.now() - timedelta(days=1),
                base_url="http://test",
                public_key="pk",
                secret_key="sk",
            )

            # All 3 sessions attempted
            assert mock_push.call_count == 3
            # 2 succeeded, 1 failed
            assert result["success"] == 2
            assert result["failed"] == 1

    def test_backfill_logs_failures(self, tmp_path, capsys):
        """Failed sessions are logged."""
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        session = transcripts_dir / "session.jsonl"
        session.write_text('{"type":"session_start","session_id":"sess1"}\n')

        with patch("src.pacemaker.langfuse.backfill.push_session") as mock_push:
            mock_push.return_value = False

            backfill.backfill_sessions(
                str(transcripts_dir),
                since=datetime.now() - timedelta(days=1),
                base_url="http://test",
                public_key="pk",
                secret_key="sk",
                progress=True,
            )

            # Check error was logged
            captured = capsys.readouterr()
            assert "failed" in captured.out.lower() or "error" in captured.out.lower()

    def test_backfill_retry_failed_sessions(self, tmp_path):
        """Failed sessions can be retried by running backfill again."""
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        session = transcripts_dir / "session.jsonl"
        session.write_text('{"type":"session_start","session_id":"sess1"}\n')

        with (
            patch("src.pacemaker.langfuse.backfill.push_session") as mock_push,
            patch(
                "src.pacemaker.langfuse.backfill.is_session_pushed"
            ) as mock_is_pushed,
        ):

            # First run: push fails
            mock_push.return_value = False
            mock_is_pushed.return_value = False

            result1 = backfill.backfill_sessions(
                str(transcripts_dir),
                since=datetime.now() - timedelta(days=1),
                base_url="http://test",
                public_key="pk",
                secret_key="sk",
            )

            assert result1["failed"] == 1

            # Second run: push succeeds
            mock_push.return_value = True

            result2 = backfill.backfill_sessions(
                str(transcripts_dir),
                since=datetime.now() - timedelta(days=1),
                base_url="http://test",
                public_key="pk",
                secret_key="sk",
            )

            assert result2["success"] == 1
            assert mock_push.call_count == 2
