"""
Bug #167: hook.get_transcript_path interpolated the payload's session_id
straight into ``~/.claude/projects/<dir>/<session_id>.jsonl``. It only reads,
but a traversal id (``../../x``) could probe or return a file outside that
directory, and the result flows into SubagentStop as the parent transcript.

Fix contract: the id goes through the same allowlist the Langfuse state files
use (``langfuse.state.is_safe_state_id``, not a copy of it); an unsafe id means
no transcript path and a logged warning.

Real files under the fake HOME that tests/conftest.py provides; nothing mocked
except a spy on the logger.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from pacemaker.hook import get_transcript_path

PROJECT_DIR = "/work/proj167"
PROJECT_KEY = "-work-proj167"


@pytest.fixture
def projects_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", PROJECT_DIR)
    monkeypatch.chdir(tmp_path)  # the cwd-derived candidate must not interfere
    path = Path.home() / ".claude" / "projects" / PROJECT_KEY
    path.mkdir(parents=True)
    return path


class TestSafeSessionIds:
    @pytest.mark.parametrize(
        "session_id",
        ["8db566bc-3863-4ded-b091-eac96858c253", "session_1", "A" * 128],
    )
    def test_existing_transcript_of_a_safe_id_is_returned(
        self, projects_dir, session_id
    ):
        transcript = projects_dir / f"{session_id}.jsonl"
        transcript.write_text("{}\n")

        assert get_transcript_path(session_id) == str(transcript)

    def test_safe_id_without_a_transcript_is_none_and_not_a_warning(self, projects_dir):
        with patch("pacemaker.hook.log_warning") as warning:
            assert get_transcript_path("no-such-session") is None

        warning.assert_not_called()


class TestUnsafeSessionIds:
    def test_traversal_id_never_reaches_a_file_outside_the_projects_dir(
        self, projects_dir
    ):
        outside = Path.home() / "outside.jsonl"  # <projects>/<dir>/../../../outside
        outside.write_text("secret\n")

        with patch("pacemaker.hook.log_warning") as warning:
            assert get_transcript_path("../../../outside") is None

        warning.assert_called_once()

    def test_id_with_a_path_separator_is_refused_even_if_the_file_exists(
        self, projects_dir
    ):
        (projects_dir / "sub").mkdir()
        (projects_dir / "sub" / "real.jsonl").write_text("{}\n")

        assert get_transcript_path("sub/real") is None

    @pytest.mark.parametrize(
        "session_id",
        ["..", "a/b", "with space", "nul\x00byte", "A" * 129, "", 123, None, ["x"]],
    )
    def test_malformed_ids_are_refused_with_a_warning(self, projects_dir, session_id):
        with patch("pacemaker.hook.log_warning") as warning:
            assert get_transcript_path(session_id) is None

        warning.assert_called_once()

    def test_the_allowlist_is_the_langfuse_state_one_not_a_copy(self, projects_dir):
        from pacemaker import hook
        from pacemaker.langfuse import state

        assert hook.is_safe_state_id is state.is_safe_state_id
