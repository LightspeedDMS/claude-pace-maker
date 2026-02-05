#!/usr/bin/env python3
"""
Tests for compact boundary detection in transcript_reader.py.

These tests verify that the stop hook only reads conversation content
AFTER the last compact_boundary marker, preventing validation against
stale content from previous sessions.
"""

import json
import tempfile
from pathlib import Path


from pacemaker.transcript_reader import build_stop_hook_context


def create_test_transcript(entries: list) -> Path:
    """
    Create a temporary JSONL transcript file for testing.

    Args:
        entries: List of dictionaries representing JSONL entries

    Returns:
        Path to temporary transcript file
    """
    temp = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    for entry in entries:
        temp.write(json.dumps(entry) + "\n")
    temp.close()
    return Path(temp.name)


def test_no_compaction_boundary_reads_all_messages():
    """Test that transcript with NO compaction boundary reads all messages."""
    # Create transcript with 3 user/assistant pairs, no compaction
    entries = [
        {
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "First user message"}],
            }
        },
        {
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "First assistant response"}],
            }
        },
        {
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Second user message"}],
            }
        },
        {
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Second assistant response"}],
            }
        },
        {
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Third user message"}],
            }
        },
        {
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Third assistant response"}],
            }
        },
    ]

    transcript_path = create_test_transcript(entries)

    try:
        context = build_stop_hook_context(str(transcript_path), first_n_pairs=10)

        # Should have 3 pairs
        assert len(context["first_pairs"]) == 3

        # Verify all messages are present
        assert context["first_pairs"][0][0] == "First user message"
        assert context["first_pairs"][1][0] == "Second user message"
        assert context["first_pairs"][2][0] == "Third user message"

        # No truncation
        assert context["truncated_count"] == 0

    finally:
        transcript_path.unlink()


def test_one_compaction_boundary_skips_old_content():
    """Test that transcript with ONE compaction boundary only reads messages after boundary."""
    entries = [
        # OLD CONTENT (before compaction)
        {
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "OLD: First user message"}],
            }
        },
        {
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "OLD: First assistant response"}],
            }
        },
        {
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "OLD: Second user message"}],
            }
        },
        {
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "OLD: Second assistant response"}],
            }
        },
        # COMPACTION BOUNDARY
        {
            "type": "system",
            "subtype": "compact_boundary",
            "content": "Conversation compacted",
            "timestamp": "2026-02-03T04:10:42.759Z",
            "compactMetadata": {"trigger": "auto", "preTokens": 155527},
        },
        # NEW CONTENT (after compaction - this is the CURRENT conversation)
        {
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "NEW: First user message"}],
            }
        },
        {
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "NEW: First assistant response"}],
            }
        },
        {
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "NEW: Second user message"}],
            }
        },
        {
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "NEW: Second assistant response"}],
            }
        },
    ]

    transcript_path = create_test_transcript(entries)

    try:
        context = build_stop_hook_context(str(transcript_path), first_n_pairs=10)

        # Should have ONLY 2 pairs (the NEW content)
        assert len(context["first_pairs"]) == 2

        # Verify ONLY new messages are present (no OLD content)
        assert context["first_pairs"][0][0] == "NEW: First user message"
        assert context["first_pairs"][1][0] == "NEW: Second user message"

        # Verify OLD content is NOT present
        for user_msg, _ in context["first_pairs"]:
            assert "OLD:" not in user_msg

    finally:
        transcript_path.unlink()


def test_multiple_compaction_boundaries_uses_last():
    """Test that transcript with MULTIPLE compaction boundaries only reads messages after LAST boundary."""
    entries = [
        # VERY OLD CONTENT
        {
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "VERY_OLD: message"}],
            }
        },
        {
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "VERY_OLD: response"}],
            }
        },
        # FIRST COMPACTION
        {
            "type": "system",
            "subtype": "compact_boundary",
            "content": "First compaction",
        },
        # OLD CONTENT
        {
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "OLD: message"}],
            }
        },
        {
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "OLD: response"}],
            }
        },
        # SECOND COMPACTION (LAST)
        {
            "type": "system",
            "subtype": "compact_boundary",
            "content": "Second compaction",
        },
        # CURRENT CONTENT
        {
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "CURRENT: message"}],
            }
        },
        {
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "CURRENT: response"}],
            }
        },
    ]

    transcript_path = create_test_transcript(entries)

    try:
        context = build_stop_hook_context(str(transcript_path), first_n_pairs=10)

        # Should have ONLY 1 pair (the CURRENT content)
        assert len(context["first_pairs"]) == 1

        # Verify ONLY current messages are present
        assert context["first_pairs"][0][0] == "CURRENT: message"

        # Verify OLD and VERY_OLD content is NOT present
        for user_msg, _ in context["first_pairs"]:
            assert "OLD:" not in user_msg
            assert "VERY_OLD:" not in user_msg

    finally:
        transcript_path.unlink()


def test_compaction_boundary_at_end_returns_empty():
    """Test that compaction boundary at end of file returns empty context."""
    entries = [
        # OLD CONTENT
        {
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "OLD: message"}],
            }
        },
        {
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "OLD: response"}],
            }
        },
        # COMPACTION AT END (no new content after)
        {
            "type": "system",
            "subtype": "compact_boundary",
            "content": "Compaction at end",
        },
    ]

    transcript_path = create_test_transcript(entries)

    try:
        context = build_stop_hook_context(str(transcript_path), first_n_pairs=10)

        # Should have NO pairs (nothing after boundary)
        assert len(context["first_pairs"]) == 0
        assert len(context["backwards_messages"]) == 0
        assert context["truncated_count"] == 0

    finally:
        transcript_path.unlink()


def test_backwards_messages_also_respect_compaction():
    """Test that backwards walk also only includes messages after compaction."""
    entries = [
        # OLD CONTENT
        {
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "OLD: message 1"}],
            }
        },
        {
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "OLD: response 1"}],
            }
        },
        # COMPACTION
        {
            "type": "system",
            "subtype": "compact_boundary",
            "content": "Compaction",
        },
        # NEW CONTENT (many pairs to trigger backwards walk)
        *[
            {
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"NEW: user message {i}" * 500}
                    ],
                }
            }
            for i in range(1, 21)
        ],
        *[
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": f"NEW: assistant response {i}" * 500}
                    ],
                }
            }
            for i in range(1, 21)
        ],
    ]

    transcript_path = create_test_transcript(entries)

    try:
        context = build_stop_hook_context(
            str(transcript_path), first_n_pairs=5, token_budget=10000
        )

        # Verify backwards messages don't contain OLD content
        for role, text in context["backwards_messages"]:
            assert "OLD:" not in text
            assert "NEW:" in text

    finally:
        transcript_path.unlink()
