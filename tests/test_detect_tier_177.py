"""Issue #177: detect_tier() keys on organization.rate_limit_tier first."""

import pytest

from pacemaker.fallback import detect_tier


def test_rate_limit_tier_5x_wins_over_has_claude_max():
    profile = {
        "account": {"has_claude_max": True},
        "organization": {"rate_limit_tier": "default_claude_max_5x"},
    }
    assert detect_tier(profile) == "5x"


def test_rate_limit_tier_20x():
    profile = {
        "account": {"has_claude_max": False},
        "organization": {"rate_limit_tier": "default_claude_max_20x"},
    }
    assert detect_tier(profile) == "20x"


def test_has_claude_max_used_only_when_rate_limit_tier_absent():
    assert detect_tier({"account": {"has_claude_max": True}}) == "20x"
    assert (
        detect_tier(
            {"account": {"has_claude_max": True}, "organization": {"name": "x"}}
        )
        == "20x"
    )


def test_unknown_rate_limit_tier_defaults_to_5x():
    profile = {
        "account": {"has_claude_max": True},
        "organization": {"rate_limit_tier": "default_something_else"},
    }
    assert detect_tier(profile) == "5x"


@pytest.mark.parametrize("profile", [None, {}, {"account": None}, {"account": {}}])
def test_empty_profile_defaults_to_5x(profile):
    assert detect_tier(profile) == "5x"


def test_usage_model_detect_tier_uses_json_cache_and_ignores_sqlite_row(tmp_path):
    """The SQLite profile_cache table has no writer; UsageModel must not read it."""
    import json
    import sqlite3
    import time

    from pacemaker import profile_cache
    from pacemaker.usage_model import UsageModel

    db_path = str(tmp_path / "usage.db")
    model = UsageModel(db_path=db_path)
    # Conflicting dead-table row: would say 20x.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO profile_cache (id, timestamp, profile_json) "
            "VALUES (1, ?, ?)",
            (time.time(), json.dumps({"account": {"has_claude_max": True}})),
        )
    # Real JSON cache (path redirected to the fake home by conftest): 5x.
    profile_cache.cache_profile(
        {"organization": {"rate_limit_tier": "default_claude_max_5x"}}
    )
    assert model._detect_tier() == "5x"


_PROFILE_20X = {"organization": {"rate_limit_tier": "default_claude_max_20x"}}


def _detect_with_current_token(tmp_path, cache_token, current_token):
    """Seed the JSON cache for cache_token (None = no fingerprint key), then run
    _detect_tier() with load_access_token() returning current_token."""
    from unittest.mock import patch

    from pacemaker import profile_cache
    from pacemaker.usage_model import UsageModel

    model = UsageModel(db_path=str(tmp_path / "usage.db"))
    profile_cache.cache_profile(_PROFILE_20X, access_token=cache_token)
    with patch("pacemaker.api_client.load_access_token", return_value=current_token):
        return model._detect_tier()


def test_detect_tier_ignores_cache_with_other_accounts_fingerprint(tmp_path):
    assert _detect_with_current_token(tmp_path, "fake-token-A", "fake-token-B") == "5x"


def test_detect_tier_accepts_cache_with_matching_fingerprint(tmp_path):
    assert _detect_with_current_token(tmp_path, "fake-token-A", "fake-token-A") == "20x"


def test_detect_tier_accepts_cache_without_fingerprint_key(tmp_path):
    assert _detect_with_current_token(tmp_path, None, "fake-token-B") == "20x"


def test_detect_tier_accepts_cache_when_no_current_token(tmp_path):
    assert _detect_with_current_token(tmp_path, "fake-token-A", None) == "20x"
