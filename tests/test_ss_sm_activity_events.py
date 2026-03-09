"""
Tests for SS (Secret Stored) and SM (Secret Masked) activity event correctness.

Bug 1: SS fires for duplicate secrets — create_secret must return None for
       duplicates so parse_assistant_response only counts genuinely new secrets.

Bug 2: SM fires in wrong place — SM must fire in orchestrator after each
       sanitize_trace() call, NOT in hook.py Stop handler.
"""

import ast
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db(tmp_path):
    return str(tmp_path / "test_secrets.db")


# ===========================================================================
# Bug 1: create_secret returns None for duplicates
# ===========================================================================


class TestCreateSecretReturnNoneForDuplicate:
    """create_secret must return None when the secret already exists in DB."""

    def test_create_secret_returns_none_for_duplicate(self, temp_db):
        """Second call with identical (type, value) must return None, not an ID."""
        from src.pacemaker.secrets.database import create_secret

        first_id = create_secret(temp_db, "text", "my-api-key-duplicate")
        second_id = create_secret(temp_db, "text", "my-api-key-duplicate")

        assert first_id is not None, "First insertion must return a valid integer ID"
        assert isinstance(first_id, int), "First ID must be an integer"
        assert (
            second_id is None
        ), "Duplicate must return None to signal 'already existed'"

    def test_create_secret_returns_id_for_new_secret(self, temp_db):
        """First insertion of a value must still return a valid integer ID."""
        from src.pacemaker.secrets.database import create_secret

        secret_id = create_secret(temp_db, "text", "brand-new-unique-secret")

        assert secret_id is not None
        assert isinstance(secret_id, int)
        assert secret_id > 0

    def test_create_secret_different_types_both_return_ids(self, temp_db):
        """Same value stored under different types are both new — both get IDs."""
        from src.pacemaker.secrets.database import create_secret

        id_text = create_secret(temp_db, "text", "same-value-different-types")
        id_file = create_secret(temp_db, "file", "same-value-different-types")

        assert id_text is not None
        assert id_file is not None
        assert id_text != id_file


# ===========================================================================
# Bug 1: parse_assistant_response only counts new secrets
# ===========================================================================


class TestSSFiresOnlyForNewSecrets:
    """SS event must only reflect genuinely new secrets, not duplicates."""

    def test_ss_does_not_fire_for_duplicate_secrets(self, temp_db):
        """parse_assistant_response called twice with same secret returns [] second time."""
        from src.pacemaker.secrets.parser import parse_assistant_response

        msg = "🔐 SECRET_TEXT: token-abc-xyz-duplicate-test\nSome other content."

        first_result = parse_assistant_response(msg, temp_db)
        second_result = parse_assistant_response(msg, temp_db)

        assert len(first_result) == 1, "First parse must return 1 new secret"
        assert len(second_result) == 0, (
            "Second parse of identical secret must return empty list — "
            "secret was already stored, SS must NOT fire"
        )

    def test_ss_fires_only_for_new_secrets_mixed(self, temp_db):
        """When message has one new + one existing secret, only the new one is counted."""
        from src.pacemaker.secrets.database import create_secret
        from src.pacemaker.secrets.parser import parse_assistant_response

        # Pre-store one secret
        create_secret(temp_db, "text", "existing-secret-already-stored")

        # Message declares both the existing and a brand new secret
        msg = (
            "🔐 SECRET_TEXT: existing-secret-already-stored\n"
            "🔐 SECRET_TEXT: brand-new-secret-never-seen\n"
        )

        result = parse_assistant_response(msg, temp_db)

        assert len(result) == 1, (
            "Only the brand-new secret should be counted; "
            "existing-secret-already-stored was a duplicate"
        )
        assert result[0]["type"] == "text"

    def test_parse_assistant_response_returns_empty_for_all_duplicates(self, temp_db):
        """All duplicates in one message → empty result list."""
        from src.pacemaker.secrets.database import create_secret
        from src.pacemaker.secrets.parser import parse_assistant_response

        create_secret(temp_db, "text", "dup-one")
        create_secret(temp_db, "text", "dup-two")

        msg = "🔐 SECRET_TEXT: dup-one\n🔐 SECRET_TEXT: dup-two\n"

        result = parse_assistant_response(msg, temp_db)

        assert result == [], "All duplicates must produce an empty result"


# ===========================================================================
# Bug 2: SM fires in orchestrator after sanitize_trace, not in Stop handler
# ===========================================================================


class TestSMFiresInOrchestratorNotStopHandler:
    """SM must be fired by orchestrator after sanitize_trace, not by hook.py Stop."""

    def test_sm_does_not_fire_in_stop_hook_source(self):
        """Verify SM event is NOT recorded inside handle_stop() in hook.py.

        We parse the AST of hook.py and check that no Call node to
        record_activity_event with literal "SM" argument appears inside
        the function body of handle_stop.
        """
        hook_path = Path(__file__).parent.parent / "src" / "pacemaker" / "hook.py"
        source = hook_path.read_text()
        tree = ast.parse(source)

        # Collect all function defs at module level (or nested)
        stop_handler_body = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "run_stop_hook":
                stop_handler_body = node
                break

        assert (
            stop_handler_body is not None
        ), "run_stop_hook function must exist in hook.py"

        # Check that no record_activity_event("SM", ...) call is inside run_stop_hook
        sm_calls_in_stop = []
        for node in ast.walk(stop_handler_body):
            if isinstance(node, ast.Call):
                func = node.func
                func_name = None
                if isinstance(func, ast.Name):
                    func_name = func.id
                elif isinstance(func, ast.Attribute):
                    func_name = func.attr
                if func_name == "record_activity_event":
                    # Check if any argument is the string "SM"
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and arg.value == "SM":
                            sm_calls_in_stop.append(node)

        assert sm_calls_in_stop == [], (
            f"Found {len(sm_calls_in_stop)} record_activity_event('SM', ...) call(s) "
            "inside run_stop_hook() — SM must NOT fire in Stop handler"
        )

    def test_sm_fires_in_orchestrator_after_sanitize_trace(self):
        """Verify SM event IS recorded in orchestrator.py after sanitize_trace calls.

        We parse the AST of orchestrator.py and check that record_activity_event
        with literal "SM" appears at module scope (i.e., in the orchestrator functions).
        """
        orchestrator_path = (
            Path(__file__).parent.parent
            / "src"
            / "pacemaker"
            / "langfuse"
            / "orchestrator.py"
        )
        source = orchestrator_path.read_text()
        tree = ast.parse(source)

        sm_calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                func_name = None
                if isinstance(func, ast.Name):
                    func_name = func.id
                elif isinstance(func, ast.Attribute):
                    func_name = func.attr
                if func_name == "record_activity_event":
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and arg.value == "SM":
                            sm_calls.append(node)

        assert len(sm_calls) > 0, (
            "orchestrator.py must contain at least one "
            "record_activity_event('SM', ...) call after sanitize_trace"
        )

    def test_sm_fires_via_sanitize_trace_call_in_flush_pending_trace(self, temp_db):
        """SM event is recorded when flush_pending_trace calls sanitize_trace.

        This is a functional integration test: call flush_pending_trace with
        a fake config and verify record_activity_event was called with 'SM'.
        """
        from src.pacemaker.langfuse import orchestrator

        fake_config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://cloud.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
            "db_path": temp_db,
        }

        # Minimal pending trace (list of batch events)
        pending_trace = [
            {"id": "evt-1", "type": "trace-create", "body": {"name": "test"}}
        ]

        recorded_events = []

        def fake_record_activity_event(db_path, event_type, color, session_id):
            recorded_events.append(event_type)

        # We need a real state manager — use a minimal mock
        mock_state_manager = MagicMock()
        mock_state_manager.read.return_value = {
            "pending_trace": pending_trace,
            "metadata": {"is_first_trace_in_session": False},
            "last_pushed_line": 0,
        }
        mock_state_manager.write.return_value = None

        existing_state = {
            "pending_trace": pending_trace,
            "trace_id": "trace-test-123",
            "metadata": {"is_first_trace_in_session": False},
            "last_pushed_line": 0,
        }

        with (
            patch(
                "src.pacemaker.langfuse.orchestrator.push.push_batch_events",
                return_value=(True, 1),
            ),
            patch(
                "src.pacemaker.langfuse.orchestrator.record_activity_event",
                side_effect=fake_record_activity_event,
            ),
        ):
            orchestrator.flush_pending_trace(
                config=fake_config,
                session_id="test-session-sm",
                state_manager=mock_state_manager,
                existing_state=existing_state,
                caller="test",
            )

        assert "SM" in recorded_events, (
            "SM activity event must be recorded inside flush_pending_trace "
            "after sanitize_trace is called"
        )
