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
        """SM event is recorded when flush_pending_trace calls sanitize_trace AND masks occur.

        This is a functional integration test: call flush_pending_trace with
        a fake config and verify record_activity_event was called with 'SM'.
        SM only fires when mask_count > 0, so a secret must be present in the DB
        and referenced in the trace.
        """
        from src.pacemaker.secrets.database import create_secret
        from src.pacemaker.langfuse import orchestrator

        # Store a secret so sanitize_trace will mask it (mask_count > 0)
        secret_val = "test-secret-token-for-sm-flush-abc"
        create_secret(temp_db, "text", secret_val)

        fake_config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://cloud.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
            "db_path": temp_db,
        }

        # Pending trace containing the secret so sanitize_trace masks it
        pending_trace = [
            {
                "id": "evt-1",
                "type": "trace-create",
                "body": {"name": "test", "input": f"token={secret_val}"},
            }
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


# ===========================================================================
# Bug 3: SA fires blue (not green) on SubagentStop
# ===========================================================================


class TestSAColorOnSubagentStop:
    """SA event must fire 'blue' on SubagentStop (green=start, blue=stop)."""

    def _get_sa_calls_in_function(self, func_name: str):
        """Parse hook.py AST and return record_activity_event('SA',...) calls in func_name."""
        hook_path = Path(__file__).parent.parent / "src" / "pacemaker" / "hook.py"
        tree = ast.parse(hook_path.read_text())
        func_body = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                func_body = node
                break
        if func_body is None:
            return None, None
        sa_calls = []
        for node in ast.walk(func_body):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else (func.attr if isinstance(func, ast.Attribute) else None)
            )
            if name == "record_activity_event":
                if any(
                    isinstance(a, ast.Constant) and a.value == "SA" for a in node.args
                ):
                    sa_calls.append(node)
        return func_body, sa_calls

    def test_sa_fires_blue_in_run_subagent_stop_hook(self):
        """SA event in run_subagent_stop_hook must use 'blue' color."""
        func_body, sa_calls = self._get_sa_calls_in_function("run_subagent_stop_hook")
        assert func_body is not None, "run_subagent_stop_hook must exist in hook.py"
        assert (
            len(sa_calls) > 0
        ), "run_subagent_stop_hook must call record_activity_event('SA', ...)"
        for call_node in sa_calls:
            args = call_node.args
            assert len(args) >= 3, "record_activity_event needs ≥3 positional args"
            color_arg = args[2]
            assert isinstance(color_arg, ast.Constant), "Color must be a string literal"
            assert (
                color_arg.value == "blue"
            ), f"SA in run_subagent_stop_hook must be 'blue', got '{color_arg.value}'"


# ===========================================================================
# Bug 4: LF only fires when handle_post_tool_use actually pushed data
# ===========================================================================


class TestLFGatedOnActualPush:
    """LF event must only fire when handle_post_tool_use returns True."""

    def _run_post_tool_with_lf_result(self, lf_return_value, tmp_path):
        """Run PostToolUse hook with handle_post_tool_use returning lf_return_value.

        Returns the mock_record MagicMock so callers can inspect call_args_list.
        """
        import json
        import pacemaker.hook as hook

        config_path = str(tmp_path / "config.json")
        with open(config_path, "w") as f:
            json.dump(
                {
                    "enabled": True,
                    "langfuse_enabled": True,
                    "intent_validation_enabled": False,
                    "poll_interval": 300,
                },
                f,
            )

        state_path = str(tmp_path / "state.json")
        with open(state_path, "w") as f:
            json.dump({"session_id": "test-lf-gate", "tool_execution_count": 0}, f)

        db_path = str(tmp_path / "test.db")
        from pacemaker.database import initialize_database

        initialize_database(db_path)

        hook_data = json.dumps(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "echo test"},
                "tool_response": "test",
                "session_id": "test-lf-gate",
                "transcript_path": str(tmp_path / "transcript.jsonl"),
            }
        )

        pacing_result = {"polled": False, "decision": {"should_throttle": False}}
        mock_record = MagicMock(return_value=True)

        fake_langfuse = MagicMock()
        fake_langfuse.orchestrator.handle_post_tool_use.return_value = lf_return_value

        with (
            patch.object(
                hook.pacing_engine, "run_pacing_check", return_value=pacing_result
            ),
            patch.object(hook, "record_activity_event", mock_record),
            patch.object(hook, "DEFAULT_DB_PATH", db_path),
            patch.object(hook, "DEFAULT_CONFIG_PATH", config_path),
            patch.object(hook, "DEFAULT_STATE_PATH", state_path),
            patch("sys.stdin") as mock_stdin,
            patch("sys.stdout"),
            patch("sys.stderr"),
        ):
            mock_stdin.read.return_value = hook_data
            with patch.dict(
                "sys.modules",
                {
                    "pacemaker.langfuse": fake_langfuse,
                    "pacemaker.langfuse.orchestrator": fake_langfuse.orchestrator,
                },
            ):
                with patch.object(hook, "should_inject_reminder", return_value=False):
                    with patch.object(hook, "save_state"):
                        hook.run_hook()
        return mock_record

    def test_lf_call_exists_in_hook_source(self):
        """hook.py must contain a record_activity_event('LF', ...) call in PostToolUse handler."""
        hook_path = Path(__file__).parent.parent / "src" / "pacemaker" / "hook.py"
        tree = ast.parse(hook_path.read_text())
        lf_calls = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else (func.attr if isinstance(func, ast.Attribute) else None)
            )
            if name == "record_activity_event":
                if any(
                    isinstance(a, ast.Constant) and a.value == "LF" for a in node.args
                ):
                    lf_calls.append(node)
        assert (
            len(lf_calls) > 0
        ), "hook.py must contain at least one record_activity_event('LF', ...) call"

    def test_lf_does_not_fire_when_push_returns_false(self, tmp_path):
        """LF must NOT fire when handle_post_tool_use returns False."""
        mock_record = self._run_post_tool_with_lf_result(False, tmp_path)
        lf_calls = [c for c in mock_record.call_args_list if c.args[1] == "LF"]
        assert len(lf_calls) == 0, (
            f"LF must not fire when handle_post_tool_use returns False, "
            f"got {len(lf_calls)} LF call(s)"
        )

    def test_lf_fires_blue_when_push_returns_true(self, tmp_path):
        """LF fires blue exactly once when handle_post_tool_use returns True."""
        mock_record = self._run_post_tool_with_lf_result(True, tmp_path)
        lf_calls = [c for c in mock_record.call_args_list if c.args[1] == "LF"]
        assert len(lf_calls) == 1, (
            f"LF must fire exactly once when handle_post_tool_use returns True, "
            f"got {len(lf_calls)} LF call(s)"
        )
        assert lf_calls[0].args[2] == "blue"


# ===========================================================================
# Bug 5: SM only fires when sanitize_trace actually masked secrets
# ===========================================================================


class TestSMGatedOnActualMasking:
    """SM event must only fire when sanitize_trace masked at least one secret."""

    def _run_flush_with_secrets(self, temp_db, secret_value=None, trace_input=None):
        """Call flush_pending_trace and return the list of recorded event types.

        If secret_value is provided, it is stored in DB before the call so
        sanitize_trace will find and mask it when trace_input contains that value.
        """
        from src.pacemaker.langfuse import orchestrator

        if secret_value is not None:
            from src.pacemaker.secrets.database import create_secret

            create_secret(temp_db, "text", secret_value)

        fake_config = {
            "langfuse_enabled": True,
            "langfuse_base_url": "https://cloud.langfuse.com",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
            "db_path": temp_db,
        }

        body = {"name": "test"}
        if trace_input is not None:
            body["input"] = trace_input

        pending_trace = [{"id": "evt-1", "type": "trace-create", "body": body}]

        recorded_events = []

        def fake_record(db_path, event_type, color, session_id):
            recorded_events.append(event_type)

        mock_state_manager = MagicMock()
        mock_state_manager.read.return_value = {
            "pending_trace": pending_trace,
            "metadata": {"is_first_trace_in_session": False},
            "last_pushed_line": 0,
        }

        existing_state = {
            "pending_trace": pending_trace,
            "trace_id": "trace-sm-test",
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
                side_effect=fake_record,
            ),
        ):
            orchestrator.flush_pending_trace(
                config=fake_config,
                session_id="test-sm-gate",
                state_manager=mock_state_manager,
                existing_state=existing_state,
                caller="test",
            )

        return recorded_events

    def test_sanitize_trace_returns_tuple_with_mask_count(self, temp_db):
        """sanitize_trace must return (sanitized, mask_count) tuple."""
        from src.pacemaker.secrets.sanitizer import sanitize_trace

        trace = [{"id": "evt-1", "type": "trace-create", "body": {"name": "clean"}}]
        result = sanitize_trace(trace, temp_db)

        assert isinstance(
            result, tuple
        ), f"sanitize_trace must return a tuple, got {type(result).__name__}"
        assert (
            len(result) == 2
        ), f"sanitize_trace tuple must have 2 elements, got {len(result)}"
        _, mask_count = result
        assert isinstance(
            mask_count, int
        ), f"mask_count must be int, got {type(mask_count).__name__}"
        assert mask_count == 0, "mask_count must be 0 when no secrets in DB"

    def test_sanitize_trace_returns_positive_count_when_secret_masked(self, temp_db):
        """sanitize_trace returns mask_count > 0 when a secret is in the trace."""
        from src.pacemaker.secrets.database import create_secret
        from src.pacemaker.secrets.sanitizer import sanitize_trace

        create_secret(temp_db, "text", "unique-token-for-sm-test-abc")
        trace = [
            {
                "id": "evt-1",
                "type": "trace-create",
                "body": {"input": "using unique-token-for-sm-test-abc"},
            }
        ]

        _, mask_count = sanitize_trace(trace, temp_db)
        assert mask_count > 0, "mask_count must be > 0 when secret appears in trace"

    def test_sm_does_not_fire_when_no_secrets_masked(self, temp_db):
        """SM must NOT fire when flush masks zero secrets (empty DB)."""
        events = self._run_flush_with_secrets(temp_db)
        assert (
            "SM" not in events
        ), f"SM must not fire when mask_count=0, got events: {events}"

    def test_sm_fires_when_secrets_are_masked(self, temp_db):
        """SM fires when flush masks at least one secret."""
        secret = "flush-mask-trigger-secret-xyz"
        events = self._run_flush_with_secrets(
            temp_db, secret_value=secret, trace_input=f"token={secret}"
        )
        assert "SM" in events, f"SM must fire when mask_count > 0, got events: {events}"
