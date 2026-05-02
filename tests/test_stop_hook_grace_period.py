"""
Tests for the current_request_active grace period in the stop hook.

When UserPromptSubmit fires for a real user prompt, current_request_active is
set to True. The stop hook checks this flag before the tempo/LLM gate: if set,
it clears the flag and allows exit without calling the LLM validator.
"""

import json
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


def test_user_prompt_submit_sets_current_request_active(tmp_path):
    """UserPromptSubmit sets current_request_active=True for non-intercepted prompts."""
    from pacemaker.hook import load_state

    state_path = str(tmp_path / "state.json")
    config_path = str(tmp_path / "config.json")
    db_path = str(tmp_path / "usage.db")

    with open(config_path, "w") as f:
        json.dump({"enabled": True, "tempo_mode": "auto"}, f)

    hook_input = json.dumps({"session_id": "test-sess", "prompt": "hello"})

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
    assert state_after.get("current_request_active") is True


def test_stop_hook_allows_exit_and_skips_llm_when_current_request_active(tmp_path):
    """Stop hook returns continue=True, skips LLM, and clears flag when current_request_active=True."""
    from pacemaker.hook import save_state, load_state

    state_path = str(tmp_path / "state.json")
    config_path = str(tmp_path / "config.json")
    db_path = str(tmp_path / "usage.db")
    transcript_path = str(tmp_path / "transcript.jsonl")

    _make_transcript(transcript_path)

    with open(config_path, "w") as f:
        json.dump(
            {"enabled": True, "tempo_mode": "on"}, f
        )  # tempo ON — LLM would run without grace period

    save_state(
        {
            "current_request_active": True,
            "silent_tool_nudge_count": 0,
            "consecutive_stop_blocks": 0,
        },
        state_path,
    )

    hook_input = json.dumps(
        {"session_id": "test-sess", "transcript_path": transcript_path}
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
    assert load_state(state_path).get("current_request_active") is False


def test_stop_hook_calls_llm_when_flag_not_set(tmp_path):
    """Stop hook proceeds to LLM validation when current_request_active is False/absent."""
    from pacemaker.hook import save_state

    state_path = str(tmp_path / "state.json")
    config_path = str(tmp_path / "config.json")
    db_path = str(tmp_path / "usage.db")
    transcript_path = str(tmp_path / "transcript.jsonl")

    _make_transcript(transcript_path)

    with open(config_path, "w") as f:
        json.dump({"enabled": True, "tempo_mode": "on"}, f)

    save_state(
        {
            "current_request_active": False,
            "silent_tool_nudge_count": 0,
            "consecutive_stop_blocks": 0,
        },
        state_path,
    )

    hook_input = json.dumps({"session_id": "sess", "transcript_path": transcript_path})

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
