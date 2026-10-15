"""
Issue #177: the cached profile must follow the active account.

cache_profile() records a token fingerprint (first 16 hex chars of
sha256(accessToken)); the profile is refetched when the cache is too old or its
fingerprint differs from the current token's. Only the profile HTTP fetch is
simulated; cache files are real temp files. All tokens are fake.
"""

import hashlib
import json
import time
from unittest.mock import patch

import pytest

from pacemaker import profile_cache
from pacemaker.constants import DEFAULT_CONFIG

TOKEN_A = "fake-token-A"
TOKEN_B = "fake-token-B"
PROFILE = {"organization": {"rate_limit_tier": "default_claude_max_5x"}}
NEW_PROFILE = {"organization": {"rate_limit_tier": "default_claude_max_20x"}}
MAX_AGE = 3600


def _fp(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


@pytest.fixture
def cache_path(tmp_path, monkeypatch):
    path = str(tmp_path / "profile_cache.json")
    monkeypatch.setattr(profile_cache, "DEFAULT_PROFILE_CACHE_PATH", path)
    return path


def _write_cache(path, *, fingerprint, age_seconds=0, profile=PROFILE):
    content = {"profile": profile, "timestamp": time.time() - age_seconds}
    if fingerprint is not None:
        content["token_fingerprint"] = fingerprint
    with open(path, "w") as f:
        json.dump(content, f)


def test_token_fingerprint_is_16_hex_of_sha256():
    assert profile_cache.token_fingerprint(TOKEN_A) == _fp(TOKEN_A)
    assert len(profile_cache.token_fingerprint(TOKEN_A)) == 16


def test_cache_profile_writes_token_fingerprint(cache_path):
    profile_cache.cache_profile(PROFILE, cache_path, access_token=TOKEN_A)
    with open(cache_path) as f:
        content = json.load(f)
    assert content["token_fingerprint"] == _fp(TOKEN_A)
    assert content["profile"] == PROFILE
    assert "timestamp" in content
    assert TOKEN_A not in json.dumps(content)


def test_stale_cache_is_refreshed(cache_path):
    _write_cache(cache_path, fingerprint=_fp(TOKEN_A), age_seconds=MAX_AGE + 60)
    with patch(
        "pacemaker.api_client.fetch_user_profile", return_value=NEW_PROFILE
    ) as fetch:
        profile_cache.refresh_profile_if_needed(TOKEN_A, MAX_AGE)
    assert fetch.call_count == 1
    assert profile_cache.load_cached_profile() == NEW_PROFILE


def test_fingerprint_mismatch_triggers_refresh(cache_path):
    _write_cache(cache_path, fingerprint=_fp(TOKEN_A))
    with patch(
        "pacemaker.api_client.fetch_user_profile", return_value=NEW_PROFILE
    ) as fetch:
        profile_cache.refresh_profile_if_needed(TOKEN_B, MAX_AGE)
    assert fetch.call_count == 1
    with open(cache_path) as f:
        assert json.load(f)["token_fingerprint"] == _fp(TOKEN_B)


def test_missing_fingerprint_triggers_refresh(cache_path):
    _write_cache(cache_path, fingerprint=None)
    with patch(
        "pacemaker.api_client.fetch_user_profile", return_value=NEW_PROFILE
    ) as fetch:
        profile_cache.refresh_profile_if_needed(TOKEN_A, MAX_AGE)
    assert fetch.call_count == 1


def test_missing_cache_triggers_fetch(cache_path):
    with patch(
        "pacemaker.api_client.fetch_user_profile", return_value=PROFILE
    ) as fetch:
        profile_cache.refresh_profile_if_needed(TOKEN_A, MAX_AGE)
    assert fetch.call_count == 1


def test_fresh_matching_cache_is_not_refetched(cache_path):
    _write_cache(cache_path, fingerprint=_fp(TOKEN_A), age_seconds=10)
    with patch("pacemaker.api_client.fetch_user_profile") as fetch:
        profile_cache.refresh_profile_if_needed(TOKEN_A, MAX_AGE)
    fetch.assert_not_called()


BACKOFF_REMAINING_SECONDS = 120.0


def _assert_cache_unchanged(path):
    with open(path) as f:
        stored = json.load(f)
    assert stored["token_fingerprint"] == _fp(TOKEN_A)
    assert stored["profile"] == PROFILE


def test_429_backoff_during_refresh_leaves_stored_fingerprint_unchanged(cache_path):
    _write_cache(cache_path, fingerprint=_fp(TOKEN_A), age_seconds=MAX_AGE + 60)
    with patch(
        "pacemaker.usage_model.UsageModel.is_in_backoff", return_value=True
    ), patch(
        "pacemaker.usage_model.UsageModel.get_backoff_remaining",
        return_value=BACKOFF_REMAINING_SECONDS,
    ), patch(
        "pacemaker.api_client.fetch_user_profile"
    ) as fetch:
        assert profile_cache.refresh_profile_if_needed(TOKEN_B, MAX_AGE) is True
    fetch.assert_not_called()
    _assert_cache_unchanged(cache_path)


def test_failed_profile_fetch_leaves_stored_profile_and_fingerprint_unchanged(
    cache_path,
):
    _write_cache(cache_path, fingerprint=_fp(TOKEN_A))
    with patch("pacemaker.api_client.fetch_user_profile", return_value=None) as fetch:
        # True means "a refetch was attempted", whether or not it succeeded.
        assert profile_cache.refresh_profile_if_needed(TOKEN_B, MAX_AGE) is True
    fetch.assert_called_once()
    _assert_cache_unchanged(cache_path)


@pytest.mark.parametrize("bad_timestamp", ["abc", None, [], True])
def test_invalid_timestamp_is_treated_as_stale(cache_path, bad_timestamp):
    content = {
        "profile": PROFILE,
        "timestamp": bad_timestamp,
        "token_fingerprint": _fp(TOKEN_A),
    }
    with open(cache_path, "w") as f:
        json.dump(content, f)
    with patch(
        "pacemaker.api_client.fetch_user_profile", return_value=NEW_PROFILE
    ) as fetch:
        profile_cache.refresh_profile_if_needed(TOKEN_A, MAX_AGE)
    assert fetch.call_count == 1


FAR_FUTURE_SECONDS = 3600  # well beyond the 300 s clock-skew allowance
WITHIN_SKEW_SECONDS = 60  # inside the 300 s clock-skew allowance


def test_far_future_timestamp_is_treated_as_stale(cache_path):
    _write_cache(cache_path, fingerprint=_fp(TOKEN_A), age_seconds=-FAR_FUTURE_SECONDS)
    with patch(
        "pacemaker.api_client.fetch_user_profile", return_value=NEW_PROFILE
    ) as fetch:
        assert profile_cache.refresh_profile_if_needed(TOKEN_A, MAX_AGE) is True
    assert fetch.call_count == 1


def test_timestamp_within_clock_skew_is_still_fresh(cache_path):
    _write_cache(cache_path, fingerprint=_fp(TOKEN_A), age_seconds=-WITHIN_SKEW_SECONDS)
    with patch("pacemaker.api_client.fetch_user_profile") as fetch:
        assert profile_cache.refresh_profile_if_needed(TOKEN_A, MAX_AGE) is False
    fetch.assert_not_called()


def test_default_config_has_profile_cache_max_age():
    assert DEFAULT_CONFIG["profile_cache_max_age_seconds"] == 3600


def test_run_pacing_check_refreshes_with_configured_max_age(cache_path, tmp_path):
    """Wiring: run_pacing_check passes profile_cache_max_age_seconds through."""
    from pacemaker import pacing_engine

    usage = {
        "five_hour_util": 10.0,
        "five_hour_resets_at": None,
        "seven_day_util": 10.0,
        "seven_day_resets_at": None,
    }
    # 120 s old: fresh under the 3600 default, stale under a 60 s max age.
    _write_cache(cache_path, fingerprint=_fp(TOKEN_A), age_seconds=120)
    db_path = str(tmp_path / "usage.db")
    with patch("pacemaker.api_client.load_access_token", return_value=TOKEN_A), patch(
        "pacemaker.api_client.fetch_usage", return_value=usage
    ), patch(
        "pacemaker.api_client.fetch_user_profile", return_value=NEW_PROFILE
    ) as fetch:
        pacing_engine.run_pacing_check(
            db_path, "s1", poll_interval=0, profile_cache_max_age_seconds=60
        )
    assert fetch.call_count == 1
