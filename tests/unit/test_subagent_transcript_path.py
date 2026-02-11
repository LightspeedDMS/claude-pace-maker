"""
Test subagent transcript path detection for Claude Code 2.1.39+ directory structure.

Tests the fix for the bug where subagent transcripts moved from:
  OLD: ~/.claude/projects/<project-dir>/agent-*.jsonl
  NEW: ~/.claude/projects/<project-dir>/<session-id>/subagents/agent-*.jsonl
"""

import json
import os
import tempfile
import time
from pathlib import Path


def create_mock_transcript(
    transcript_path: Path, tool_use_id: str = None, content: str = None
) -> None:
    """Create a mock JSONL transcript file with optional tool_use_id."""
    transcript_path.parent.mkdir(parents=True, exist_ok=True)

    if content:
        transcript_path.write_text(content)
    else:
        # Create JSONL entries with tool_use_id if provided
        entries = []
        if tool_use_id:
            entries.append(
                json.dumps(
                    {
                        "type": "tool_use",
                        "id": tool_use_id,
                        "name": "Task",
                        "input": {"description": "Test task"},
                    }
                )
            )
        entries.append(json.dumps({"type": "text", "text": "Some content"}))
        transcript_path.write_text("\n".join(entries) + "\n")


def test_finds_agent_transcript_in_new_nested_structure():
    """Verifies glob searches <session_id>/subagents/ path (new Claude Code format)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir)
        session_id = "98d876d6-c609-4f4e-b75b-cb1a30e93df0"
        tool_use_id = "toolu_0194HN"

        # Create new nested structure
        subagents_dir = projects_dir / session_id / "subagents"
        agent_transcript = subagents_dir / "agent-aae4b14.jsonl"
        create_mock_transcript(agent_transcript, tool_use_id)

        # Create main transcript
        main_transcript = projects_dir / f"{session_id}.jsonl"
        create_mock_transcript(
            main_transcript, content='{"type": "text", "text": "main context"}'
        )

        # Simulate the hook logic
        import glob

        agent_transcripts = glob.glob(str(projects_dir / "agent-*.jsonl"))
        if session_id:
            agent_transcripts += glob.glob(
                str(projects_dir / session_id / "subagents" / "agent-*.jsonl")
            )

        recent_agents = [
            f for f in agent_transcripts if time.time() - os.path.getmtime(f) < 30
        ]

        # Should find the nested agent transcript
        assert len(recent_agents) == 1
        assert "subagents/agent-aae4b14.jsonl" in recent_agents[0]

        # Should contain the tool_use_id
        with open(recent_agents[0], "r") as f:
            content = f.read()
            assert tool_use_id in content


def test_finds_agent_transcript_in_old_flat_structure():
    """Backward compatibility with old agent-*.jsonl in flat directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir)
        session_id = "98d876d6-c609-4f4e-b75b-cb1a30e93df0"
        tool_use_id = "toolu_OLD_FORMAT"

        # Create old flat structure
        agent_transcript = projects_dir / "agent-abc123.jsonl"
        create_mock_transcript(agent_transcript, tool_use_id)

        # Create main transcript
        main_transcript = projects_dir / f"{session_id}.jsonl"
        create_mock_transcript(
            main_transcript, content='{"type": "text", "text": "main context"}'
        )

        # Simulate the hook logic
        import glob

        agent_transcripts = glob.glob(str(projects_dir / "agent-*.jsonl"))
        if session_id:
            agent_transcripts += glob.glob(
                str(projects_dir / session_id / "subagents" / "agent-*.jsonl")
            )

        recent_agents = [
            f for f in agent_transcripts if time.time() - os.path.getmtime(f) < 30
        ]

        # Should find the flat agent transcript
        assert len(recent_agents) == 1
        assert "agent-abc123.jsonl" in recent_agents[0]

        # Should contain the tool_use_id
        with open(recent_agents[0], "r") as f:
            content = f.read()
            assert tool_use_id in content


def test_searches_both_locations():
    """Both old and new locations are searched."""
    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir)
        session_id = "98d876d6-c609-4f4e-b75b-cb1a30e93df0"

        # Create both old flat structure
        agent_transcript_old = projects_dir / "agent-old.jsonl"
        create_mock_transcript(agent_transcript_old, "toolu_OLD")

        # And new nested structure
        subagents_dir = projects_dir / session_id / "subagents"
        agent_transcript_new = subagents_dir / "agent-new.jsonl"
        create_mock_transcript(agent_transcript_new, "toolu_NEW")

        # Simulate the hook logic
        import glob

        agent_transcripts = glob.glob(str(projects_dir / "agent-*.jsonl"))
        if session_id:
            agent_transcripts += glob.glob(
                str(projects_dir / session_id / "subagents" / "agent-*.jsonl")
            )

        recent_agents = [
            f for f in agent_transcripts if time.time() - os.path.getmtime(f) < 30
        ]

        # Should find both transcripts
        assert len(recent_agents) == 2
        agent_names = [os.path.basename(a) for a in recent_agents]
        assert "agent-old.jsonl" in agent_names
        assert "agent-new.jsonl" in agent_names


def test_no_agent_transcripts_found():
    """Gracefully handles no matches (reads main transcript)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir)
        session_id = "98d876d6-c609-4f4e-b75b-cb1a30e93df0"

        # Create only main transcript
        main_transcript = projects_dir / f"{session_id}.jsonl"
        create_mock_transcript(
            main_transcript, content='{"type": "text", "text": "main context"}'
        )

        # Simulate the hook logic
        import glob

        agent_transcripts = glob.glob(str(projects_dir / "agent-*.jsonl"))
        if session_id:
            agent_transcripts += glob.glob(
                str(projects_dir / session_id / "subagents" / "agent-*.jsonl")
            )

        recent_agents = [
            f for f in agent_transcripts if time.time() - os.path.getmtime(f) < 30
        ]

        # Should find no agent transcripts
        assert len(recent_agents) == 0

        # In the real hook, this means transcript_path stays as main_transcript
        # and the hook reads from the main context (expected behavior)


def test_filters_by_30_second_recency():
    """Only recently modified agent transcripts (last 30 seconds) are included."""
    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir)
        session_id = "98d876d6-c609-4f4e-b75b-cb1a30e93df0"

        # Create recent nested agent transcript
        subagents_dir = projects_dir / session_id / "subagents"
        agent_recent = subagents_dir / "agent-recent.jsonl"
        create_mock_transcript(agent_recent, "toolu_RECENT")

        # Create old agent transcript (simulated by setting mtime to 60 seconds ago)
        agent_old = subagents_dir / "agent-old.jsonl"
        create_mock_transcript(agent_old, "toolu_OLD")
        old_time = time.time() - 60  # 60 seconds ago
        os.utime(agent_old, (old_time, old_time))

        # Simulate the hook logic
        import glob

        agent_transcripts = glob.glob(str(projects_dir / "agent-*.jsonl"))
        if session_id:
            agent_transcripts += glob.glob(
                str(projects_dir / session_id / "subagents" / "agent-*.jsonl")
            )

        recent_agents = [
            f for f in agent_transcripts if time.time() - os.path.getmtime(f) < 30
        ]

        # Should only find the recent transcript
        assert len(recent_agents) == 1
        assert "agent-recent.jsonl" in recent_agents[0]


def test_session_id_none_only_searches_flat_structure():
    """When session_id is None, only searches flat structure (backward compatibility)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir)
        session_id = None  # No session_id available

        # Create old flat structure
        agent_transcript_old = projects_dir / "agent-old.jsonl"
        create_mock_transcript(agent_transcript_old, "toolu_OLD")

        # Create nested structure (should NOT be found without session_id)
        fake_session = "fake-session-id"
        subagents_dir = projects_dir / fake_session / "subagents"
        agent_transcript_new = subagents_dir / "agent-new.jsonl"
        create_mock_transcript(agent_transcript_new, "toolu_NEW")

        # Simulate the hook logic
        import glob

        agent_transcripts = glob.glob(str(projects_dir / "agent-*.jsonl"))
        if session_id:
            agent_transcripts += glob.glob(
                str(projects_dir / session_id / "subagents" / "agent-*.jsonl")
            )

        recent_agents = [
            f for f in agent_transcripts if time.time() - os.path.getmtime(f) < 30
        ]

        # Should only find the flat transcript
        assert len(recent_agents) == 1
        assert "agent-old.jsonl" in recent_agents[0]
