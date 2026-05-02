"""
Tests for per-session last_user_interaction_time in the stop hook.

UserPromptSubmit writes the current timestamp into
last_user_interaction_time_by_session[session_id]. The stop hook reads this
per-session entry when deciding whether to run LLM validation (auto mode):
- elapsed < threshold  -> user is watching -> skip validation
- elapsed >= threshold -> agentic work     -> run validation

Per-session keying prevents cross-session corruption: another session's
startup reset of the global last_user_interaction_time cannot affect this
session's elapsed-time calculation.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


def _make_transcript(path: str):
    """Write a minimal transcript with a final assistant text entry."""
    entry = {
        "type": "message",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Done."}],
        },
    }
    with open(path, "w") as f:
        f.write(json.dumps(entry) + "\n")


def test_user_prompt_submit_sets_per_session_time(tmp_path):
    """UserPromptSubmit writes timestamp into last_user_interaction_time_by_session[session_id]."""
    from pacemaker.hook import load_state

    state_path = str(tmp_path / "state.json")
    config_path = str(tmp_path / "config.json")
    db_path = str(tmp_path / "usage.db")

    with open(config_path, "w") as f:
        json.dump({"enabled": True, "tempo_mode": "auto"}, f)

    hook_input = json.dumps({"session_id": "sess-abc", "prompt": "hello"})

    with (
        patch("pacemaker.hook.DEFAULT_STATE_PATH", state_path),
        patch("pacemaker.hook.DEFAULT_CONFIG_PATH", config_path),
        patch("pacemaker.hook.DEFAULT_DB_PATH", db_path),
        patch(
            "pacemaker.hook.user_commands.handle_user_prompt",
            return_value={"intercepted": False, "output": ""},
        ),
        patch("pacemaker.hook.sys.stdin.read", return_value=hook_input),
        patch("pacemaker.hook.sys.exit"),
        patch("pacemaker.hook.record_activity_event"),
        patch("pacemaker.hook.get_transcript_path", return_value=None),
        patch("pacemaker.session_registry._csa.on_heartbeat"),
        patch(
            "pacemaker.session_registry.db.resolve_db_path",
            return_value=str(tmp_path / "csa.db"),
        ),
        patch("pacemaker.langfuse.orchestrator.handle_user_prompt_submit"),
    ):
        from pacemaker.hook import run_user_prompt_submit

        run_user_prompt_submit()

    state_after = load_state(state_path)
    per_session = state_after.get("last_user_interaction_time_by_session", {})
    assert "sess-abc" in per_session
    ts = datetime.fromisoformat(per_session["sess-abc"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    assert (datetime.now(timezone.utc) - ts).total_seconds() < 5


def test_stop_hook_skips_llm_when_prompt_recent(tmp_path):
    """Stop hook skips LLM when per-session elapsed time is below threshold."""
    from pacemaker.hook import save_state

    state_path = str(tmp_path / "state.json")
    config_path = str(tmp_path / "config.json")
    db_path = str(tmp_path / "usage.db")
    transcript_path = str(tmp_path / "transcript.jsonl")

    _make_transcript(transcript_path)

    with open(config_path, "w") as f:
        json.dump(
            {"enabled": True, "tempo_mode": "auto", "auto_tempo_threshold_minutes": 10},
            f,
        )

    save_state(
        {
            "last_user_interaction_time": None,
            "last_user_interaction_time_by_session": {
                "sess-abc": datetime.now(timezone.utc).isoformat()
            },
            "silent_tool_nudge_count": 0,
            "consecutive_stop_blocks": 0,
        },
        state_path,
    )

    hook_input = json.dumps(
        {"session_id": "sess-abc", "transcript_path": transcript_path}
    )

    with (
        patch("pacemaker.hook.DEFAULT_STATE_PATH", state_path),
        patch("pacemaker.hook.DEFAULT_CONFIG_PATH", config_path),
        patch("pacemaker.hook.DEFAULT_DB_PATH", db_path),
        patch("pacemaker.hook.sys.stdin.read", return_value=hook_input),
        patch("pacemaker.hook.record_activity_event"),
        patch("pacemaker.hook.record_blockage"),
        patch("pacemaker.hook.is_context_exhaustion_detected", return_value=False),
        patch(
            "pacemaker.transcript_reader.detect_silent_tool_stop", return_value=False
        ),
        patch("pacemaker.intent_validator.validate_intent") as mock_validate,
        patch("pacemaker.session_registry._csa.on_heartbeat"),
        patch("pacemaker.session_registry._csa.on_session_end"),
        patch(
            "pacemaker.session_registry.db.resolve_db_path",
            return_value=str(tmp_path / "csa.db"),
        ),
        patch("pacemaker.langfuse.orchestrator.handle_stop_finalize"),
    ):
        from pacemaker.hook import run_stop_hook

        result = run_stop_hook()

    assert result == {"continue": True}
    mock_validate.assert_not_called()


def test_stop_hook_runs_llm_after_threshold_elapsed(tmp_path):
    """Stop hook runs LLM validation when per-session elapsed time exceeds threshold."""
    from pacemaker.hook import save_state

    state_path = str(tmp_path / "state.json")
    config_path = str(tmp_path / "config.json")
    db_path = str(tmp_path / "usage.db")
    transcript_path = str(tmp_path / "transcript.jsonl")

    _make_transcript(transcript_path)

    with open(config_path, "w") as f:
        json.dump(
            {"enabled": True, "tempo_mode": "auto", "auto_tempo_threshold_minutes": 10},
            f,
        )

    old_time = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    save_state(
        {
            "last_user_interaction_time_by_session": {"sess-abc": old_time},
            "silent_tool_nudge_count": 0,
            "consecutive_stop_blocks": 0,
        },
        state_path,
    )

    hook_input = json.dumps(
        {"session_id": "sess-abc", "transcript_path": transcript_path}
    )

    with (
        patch("pacemaker.hook.DEFAULT_STATE_PATH", state_path),
        patch("pacemaker.hook.DEFAULT_CONFIG_PATH", config_path),
        patch("pacemaker.hook.DEFAULT_DB_PATH", db_path),
        patch("pacemaker.hook.sys.stdin.read", return_value=hook_input),
        patch("pacemaker.hook.record_activity_event"),
        patch("pacemaker.hook.record_blockage"),
        patch("pacemaker.hook.is_context_exhaustion_detected", return_value=False),
        patch(
            "pacemaker.transcript_reader.detect_silent_tool_stop", return_value=False
        ),
        patch("pacemaker.intent_validator.validate_intent") as mock_validate,
        patch("pacemaker.session_registry._csa.on_heartbeat"),
        patch("pacemaker.session_registry._csa.on_session_end"),
        patch(
            "pacemaker.session_registry.db.resolve_db_path",
            return_value=str(tmp_path / "csa.db"),
        ),
        patch("pacemaker.langfuse.orchestrator.handle_stop_finalize"),
    ):
        mock_validate.return_value = {"continue": True}
        from pacemaker.hook import run_stop_hook

        run_stop_hook()

    mock_validate.assert_called_once()


def test_per_session_isolation_from_global_reset(tmp_path):
    """Per-session time is used even when global is None (cross-session startup corruption)."""
    from pacemaker.hook import save_state

    state_path = str(tmp_path / "state.json")
    config_path = str(tmp_path / "config.json")
    db_path = str(tmp_path / "usage.db")
    transcript_path = str(tmp_path / "transcript.jsonl")

    _make_transcript(transcript_path)

    with open(config_path, "w") as f:
        json.dump(
            {"enabled": True, "tempo_mode": "auto", "auto_tempo_threshold_minutes": 10},
            f,
        )

    # Simulates: session B started and wiped the global; session A still has its per-session entry
    save_state(
        {
            "last_user_interaction_time": None,
            "last_user_interaction_time_by_session": {
                "sess-abc": datetime.now(timezone.utc).isoformat()
            },
            "silent_tool_nudge_count": 0,
            "consecutive_stop_blocks": 0,
        },
        state_path,
    )

    hook_input = json.dumps(
        {"session_id": "sess-abc", "transcript_path": transcript_path}
    )

    with (
        patch("pacemaker.hook.DEFAULT_STATE_PATH", state_path),
        patch("pacemaker.hook.DEFAULT_CONFIG_PATH", config_path),
        patch("pacemaker.hook.DEFAULT_DB_PATH", db_path),
        patch("pacemaker.hook.sys.stdin.read", return_value=hook_input),
        patch("pacemaker.hook.record_activity_event"),
        patch("pacemaker.hook.record_blockage"),
        patch("pacemaker.hook.is_context_exhaustion_detected", return_value=False),
        patch(
            "pacemaker.transcript_reader.detect_silent_tool_stop", return_value=False
        ),
        patch("pacemaker.intent_validator.validate_intent") as mock_validate,
        patch("pacemaker.session_registry._csa.on_heartbeat"),
        patch("pacemaker.session_registry._csa.on_session_end"),
        patch(
            "pacemaker.session_registry.db.resolve_db_path",
            return_value=str(tmp_path / "csa.db"),
        ),
        patch("pacemaker.langfuse.orchestrator.handle_stop_finalize"),
    ):
        from pacemaker.hook import run_stop_hook

        result = run_stop_hook()

    assert result == {"continue": True}
    mock_validate.assert_not_called()
