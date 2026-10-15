"""
Issue #177: on macOS the Keychain is the source of truth for the OAuth token;
~/.claude/.credentials.json is only a cache used when the Keychain read fails.

Only the `security` subprocess and platform.system() are simulated. The
credentials file is a real file under the per-test fake HOME (tests/conftest.py).
All tokens are fake.
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from pacemaker import api_client

FILE_TOKEN = "fake-file-token-A"
KEYCHAIN_TOKEN = "fake-keychain-token-B"

# Captured at import (collection) time, before conftest's autouse fixture stubs it.
ORIGINAL_LOAD_TOKEN_FROM_KEYCHAIN = api_client._load_token_from_keychain


@pytest.fixture(autouse=True)
def _use_real_keychain_loader(monkeypatch):
    """conftest stubs the loader for every test; these tests fake subprocess.run
    and platform.system themselves, so they exercise the real loader."""
    monkeypatch.setattr(
        api_client, "_load_token_from_keychain", ORIGINAL_LOAD_TOKEN_FROM_KEYCHAIN
    )


def _write_creds_file(token: str) -> None:
    path = Path.home() / ".claude" / ".credentials.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"claudeAiOauth": {"accessToken": token}}))


def _security_ok(token: str) -> subprocess.CompletedProcess:
    payload = json.dumps({"claudeAiOauth": {"accessToken": token}})
    return subprocess.CompletedProcess(
        args=["security"], returncode=0, stdout=payload + "\n", stderr=""
    )


def _security_fail(returncode: int = 44) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["security"], returncode=returncode, stdout="", stderr="not found"
    )


def test_darwin_keychain_beats_unexpired_file_token():
    _write_creds_file(FILE_TOKEN)
    with patch("pacemaker.api_client.platform.system", return_value="Darwin"), patch(
        "pacemaker.api_client.subprocess.run", return_value=_security_ok(KEYCHAIN_TOKEN)
    ) as run:
        assert api_client.load_access_token() == KEYCHAIN_TOKEN
    cmd = run.call_args[0][0]
    assert cmd[:2] == ["security", "find-generic-password"]
    assert "Claude Code-credentials" in cmd
    assert run.call_args[1]["timeout"] <= 3


def test_darwin_keychain_failure_falls_back_to_file_with_warning():
    _write_creds_file(FILE_TOKEN)
    with patch("pacemaker.api_client.platform.system", return_value="Darwin"), patch(
        "pacemaker.api_client.subprocess.run", return_value=_security_fail()
    ), patch("pacemaker.api_client.log_warning") as warn:
        assert api_client.load_access_token() == FILE_TOKEN
    assert warn.called


def test_darwin_keychain_timeout_falls_back_to_file_with_warning():
    _write_creds_file(FILE_TOKEN)
    with patch("pacemaker.api_client.platform.system", return_value="Darwin"), patch(
        "pacemaker.api_client.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="security", timeout=3),
    ), patch("pacemaker.api_client.log_warning") as warn:
        assert api_client.load_access_token() == FILE_TOKEN
    assert warn.called


def test_darwin_keychain_garbage_payload_falls_back_to_file_with_warning():
    _write_creds_file(FILE_TOKEN)
    garbage = subprocess.CompletedProcess(
        args=["security"], returncode=0, stdout="not json", stderr=""
    )
    with patch("pacemaker.api_client.platform.system", return_value="Darwin"), patch(
        "pacemaker.api_client.subprocess.run", return_value=garbage
    ), patch("pacemaker.api_client.log_warning") as warn:
        assert api_client.load_access_token() == FILE_TOKEN
    assert warn.called


def test_darwin_keychain_failure_and_no_file_returns_none():
    with patch("pacemaker.api_client.platform.system", return_value="Darwin"), patch(
        "pacemaker.api_client.subprocess.run", return_value=_security_fail()
    ):
        assert api_client.load_access_token() is None


@pytest.mark.parametrize(
    "payload, expected_fragment",
    [
        ({}, "claudeAiOauth is not an object"),
        ({"claudeAiOauth": "a-string"}, "claudeAiOauth is not an object"),
        ({"claudeAiOauth": ["x"]}, "claudeAiOauth is not an object"),
        ({"claudeAiOauth": {}}, "accessToken is not a non-empty string"),
        ({"claudeAiOauth": {"accessToken": ""}}, "accessToken is not a non-empty string"),
        ({"claudeAiOauth": {"accessToken": None}}, "accessToken is not a non-empty string"),
        ({"claudeAiOauth": {"accessToken": 12345}}, "accessToken is not a non-empty string"),
    ],
)
def test_darwin_keychain_bad_payload_shape_falls_back_with_specific_warning(
    payload, expected_fragment
):
    _write_creds_file(FILE_TOKEN)
    bad = subprocess.CompletedProcess(
        args=["security"], returncode=0, stdout=json.dumps(payload), stderr=""
    )
    with patch("pacemaker.api_client.platform.system", return_value="Darwin"), patch(
        "pacemaker.api_client.subprocess.run", return_value=bad
    ), patch("pacemaker.api_client.log_warning") as warn:
        assert api_client.load_access_token() == FILE_TOKEN
    messages = [call.args[1] for call in warn.call_args_list]
    assert any(expected_fragment in m for m in messages), messages


def test_linux_never_invokes_keychain():
    _write_creds_file(FILE_TOKEN)
    with patch("pacemaker.api_client.platform.system", return_value="Linux"), patch(
        "pacemaker.api_client.subprocess.run"
    ) as run:
        assert api_client.load_access_token() == FILE_TOKEN
    run.assert_not_called()
