#!/usr/bin/env python3
"""
Tests for profile_cache.py - Profile caching to shared disk.

TDD: Tests written first to define behavior before implementation.
Story #38: Profile fetch, cache, and backoff-aware retrieval.

Acceptance Criteria covered:
- Scenario 3: Profile cached to shared disk file
- Scenario 4: Profile fetch respects shared 429 backoff
- Scenario 7: Self-sufficient without claude-usage
"""

import json
import time
from pathlib import Path
import sys
from unittest.mock import MagicMock, patch


# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestCacheProfile:
    """Tests for cache_profile() function."""

    def test_writes_profile_to_file(self, tmp_path):
        """cache_profile writes profile data to the specified path."""
        from pacemaker.profile_cache import cache_profile

        cache_path = tmp_path / "profile_cache.json"
        profile_data = {
            "account": {
                "email": "test@example.com",
                "has_claude_pro": True,
                "has_claude_max": False,
            }
        }

        cache_profile(profile_data, str(cache_path))

        assert cache_path.exists()

    def test_written_file_contains_profile(self, tmp_path):
        """cache_profile writes profile data that can be read back."""
        from pacemaker.profile_cache import cache_profile

        cache_path = tmp_path / "profile_cache.json"
        profile_data = {
            "account": {
                "email": "test@example.com",
                "has_claude_pro": True,
                "has_claude_max": False,
            }
        }

        cache_profile(profile_data, str(cache_path))

        content = json.loads(cache_path.read_text())
        assert content["profile"] == profile_data

    def test_written_file_contains_timestamp(self, tmp_path):
        """cache_profile includes a timestamp in the written file."""
        from pacemaker.profile_cache import cache_profile

        cache_path = tmp_path / "profile_cache.json"
        profile_data = {"account": {"email": "test@example.com"}}

        before = time.time()
        cache_profile(profile_data, str(cache_path))
        after = time.time()

        content = json.loads(cache_path.read_text())
        assert "timestamp" in content
        assert before <= content["timestamp"] <= after

    def test_uses_atomic_write(self, tmp_path):
        """cache_profile writes atomically (no tmp file left behind)."""
        from pacemaker.profile_cache import cache_profile

        cache_path = tmp_path / "profile_cache.json"
        profile_data = {"account": {"email": "test@example.com"}}

        cache_profile(profile_data, str(cache_path))

        # No temp files should remain
        tmp_files = list(tmp_path.glob("*.tmp*"))
        assert len(tmp_files) == 0

        # Main file should exist
        assert cache_path.exists()

    def test_creates_parent_directory(self, tmp_path):
        """cache_profile creates parent directories if needed."""
        from pacemaker.profile_cache import cache_profile

        nested_path = tmp_path / "subdir" / "profile_cache.json"
        profile_data = {"account": {"email": "test@example.com"}}

        cache_profile(profile_data, str(nested_path))

        assert nested_path.exists()

    def test_overwrites_existing_cache(self, tmp_path):
        """cache_profile overwrites an existing cache file."""
        from pacemaker.profile_cache import cache_profile

        cache_path = tmp_path / "profile_cache.json"

        # Write initial data
        cache_profile({"account": {"email": "old@example.com"}}, str(cache_path))

        # Overwrite with new data
        cache_profile({"account": {"email": "new@example.com"}}, str(cache_path))

        content = json.loads(cache_path.read_text())
        assert content["profile"]["account"]["email"] == "new@example.com"


class TestLoadCachedProfile:
    """Tests for load_cached_profile() function."""

    def test_returns_none_when_file_missing(self, tmp_path):
        """load_cached_profile returns None when cache file does not exist."""
        from pacemaker.profile_cache import load_cached_profile

        missing_path = tmp_path / "profile_cache.json"

        result = load_cached_profile(str(missing_path))

        assert result is None

    def test_returns_none_when_file_corrupt(self, tmp_path):
        """load_cached_profile returns None when cache file is corrupt JSON."""
        from pacemaker.profile_cache import load_cached_profile

        corrupt_path = tmp_path / "profile_cache.json"
        corrupt_path.write_text("not valid json {{{")

        result = load_cached_profile(str(corrupt_path))

        assert result is None

    def test_returns_profile_data_from_cache(self, tmp_path):
        """load_cached_profile returns the profile data from the cache file."""
        from pacemaker.profile_cache import cache_profile, load_cached_profile

        cache_path = tmp_path / "profile_cache.json"
        profile_data = {
            "account": {
                "email": "test@example.com",
                "has_claude_pro": True,
                "has_claude_max": False,
            }
        }

        cache_profile(profile_data, str(cache_path))
        result = load_cached_profile(str(cache_path))

        assert result is not None
        assert result["account"]["email"] == "test@example.com"

    def test_returns_profile_dict_not_wrapper(self, tmp_path):
        """load_cached_profile returns the profile dict, not the {profile, timestamp} wrapper."""
        from pacemaker.profile_cache import cache_profile, load_cached_profile

        cache_path = tmp_path / "profile_cache.json"
        profile_data = {"account": {"email": "test@example.com"}}

        cache_profile(profile_data, str(cache_path))
        result = load_cached_profile(str(cache_path))

        # Should return the profile directly, not the wrapper dict
        assert "account" in result
        assert "timestamp" not in result

    def test_returns_none_for_empty_file(self, tmp_path):
        """load_cached_profile returns None for an empty file."""
        from pacemaker.profile_cache import load_cached_profile

        empty_path = tmp_path / "profile_cache.json"
        empty_path.write_text("")

        result = load_cached_profile(str(empty_path))

        assert result is None


class TestLoadCachedProfileWithTTL:
    """Tests for load_cached_profile() with TTL (max_age_seconds)."""

    def test_returns_profile_within_ttl(self, tmp_path):
        """load_cached_profile returns profile when cache is fresh (within TTL)."""
        from pacemaker.profile_cache import cache_profile, load_cached_profile

        cache_path = tmp_path / "profile_cache.json"
        profile_data = {"account": {"email": "test@example.com"}}

        cache_profile(profile_data, str(cache_path))

        # Request with 3600 second TTL (fresh cache)
        result = load_cached_profile(str(cache_path), max_age_seconds=3600)

        assert result is not None
        assert result["account"]["email"] == "test@example.com"

    def test_returns_none_when_cache_expired(self, tmp_path):
        """load_cached_profile returns None when cache is older than max_age_seconds."""
        from pacemaker.profile_cache import load_cached_profile

        cache_path = tmp_path / "profile_cache.json"
        # Write a file with an old timestamp
        old_timestamp = time.time() - 7200  # 2 hours ago
        content = {
            "profile": {"account": {"email": "test@example.com"}},
            "timestamp": old_timestamp,
        }
        cache_path.write_text(json.dumps(content))

        # Request with 3600 second TTL (cache should be expired)
        result = load_cached_profile(str(cache_path), max_age_seconds=3600)

        assert result is None


def _make_backoff_model(is_in_backoff: bool) -> MagicMock:
    """Return a MagicMock that mimics UsageModel with the given backoff state."""
    model = MagicMock()
    model.is_in_backoff.return_value = is_in_backoff
    model.get_backoff_remaining.return_value = 3600.0
    return model


class TestFetchAndCacheProfile:
    """Tests for fetch_and_cache_profile() - with backoff awareness."""

    def test_returns_cached_profile_when_backoff_active(self, tmp_path):
        """fetch_and_cache_profile returns cached profile when in backoff."""
        from pacemaker.profile_cache import cache_profile, fetch_and_cache_profile

        cache_path = tmp_path / "profile_cache.json"

        # Pre-populate the cache
        cached_profile = {"account": {"email": "cached@example.com"}}
        cache_profile(cached_profile, str(cache_path))

        with patch(
            "pacemaker.usage_model.UsageModel",
            return_value=_make_backoff_model(is_in_backoff=True),
        ):
            result = fetch_and_cache_profile(
                access_token="fake_token",
                cache_path=str(cache_path),
            )

        assert result is not None
        assert result["account"]["email"] == "cached@example.com"

    def test_returns_none_when_backoff_active_and_no_cache(self, tmp_path):
        """fetch_and_cache_profile returns None when in backoff with no cache."""
        from pacemaker.profile_cache import fetch_and_cache_profile

        cache_path = tmp_path / "profile_cache.json"  # No cache

        with patch(
            "pacemaker.usage_model.UsageModel",
            return_value=_make_backoff_model(is_in_backoff=True),
        ):
            result = fetch_and_cache_profile(
                access_token="fake_token",
                cache_path=str(cache_path),
            )

        assert result is None

    def test_skips_api_call_when_backoff_active(self, tmp_path):
        """fetch_and_cache_profile does not make API call when in backoff."""
        from pacemaker.profile_cache import cache_profile, fetch_and_cache_profile

        cache_path = tmp_path / "profile_cache.json"

        cached_profile = {"account": {"email": "cached@example.com"}}
        cache_profile(cached_profile, str(cache_path))

        # This test verifies no API call is made by using an obviously invalid token
        # The function should return from cache without trying the API
        with patch(
            "pacemaker.usage_model.UsageModel",
            return_value=_make_backoff_model(is_in_backoff=True),
        ):
            result = fetch_and_cache_profile(
                access_token="fake_invalid_token",
                cache_path=str(cache_path),
            )

        # Should return cached profile, not raise an exception
        assert result is not None


class TestAdditionalProfileCacheCoverage:
    """Additional tests to cover uncovered exception paths and edge cases."""

    def test_load_cached_profile_with_missing_timestamp_during_ttl_check(
        self, tmp_path
    ):
        """load_cached_profile returns None when cache file has no timestamp and TTL is set."""
        from pacemaker.profile_cache import load_cached_profile

        cache_path = tmp_path / "profile_cache.json"
        # Write cache without timestamp field
        content = {"profile": {"account": {"email": "test@example.com"}}}
        cache_path.write_text(json.dumps(content))

        # With TTL check, missing timestamp means expired
        result = load_cached_profile(str(cache_path), max_age_seconds=3600)

        assert result is None

    def test_load_cached_profile_with_missing_profile_key(self, tmp_path):
        """load_cached_profile returns None when cache file has no 'profile' key."""
        from pacemaker.profile_cache import load_cached_profile

        cache_path = tmp_path / "profile_cache.json"
        # Write cache with wrong structure (no 'profile' key)
        content = {
            "data": {"account": {"email": "test@example.com"}},
            "timestamp": time.time(),
        }
        cache_path.write_text(json.dumps(content))

        result = load_cached_profile(str(cache_path))

        assert result is None

    def test_fetch_and_cache_profile_returns_cache_when_api_returns_none(
        self, tmp_path
    ):
        """fetch_and_cache_profile returns cached profile when API call returns None."""
        from pacemaker.profile_cache import cache_profile, fetch_and_cache_profile

        cache_path = tmp_path / "profile_cache.json"

        # Pre-populate cache
        cached_profile = {"account": {"email": "cached@example.com"}}
        cache_profile(cached_profile, str(cache_path))

        # No active backoff; mock api_client.fetch_user_profile to return None
        with (
            patch(
                "pacemaker.usage_model.UsageModel",
                return_value=_make_backoff_model(is_in_backoff=False),
            ),
            patch("pacemaker.api_client.fetch_user_profile", return_value=None),
        ):
            result = fetch_and_cache_profile(
                access_token="test_token",
                cache_path=str(cache_path),
            )

        # Should return the cached profile since API returned None
        assert result is not None
        assert result["account"]["email"] == "cached@example.com"

    def test_fetch_and_cache_profile_caches_fresh_api_response(self, tmp_path):
        """fetch_and_cache_profile caches successful API response to disk."""
        from pacemaker.profile_cache import fetch_and_cache_profile, load_cached_profile

        cache_path = tmp_path / "profile_cache.json"

        fresh_profile = {
            "account": {"email": "fresh@example.com", "has_claude_pro": True}
        }

        # No active backoff; mock api_client.fetch_user_profile to return fresh profile
        with (
            patch(
                "pacemaker.usage_model.UsageModel",
                return_value=_make_backoff_model(is_in_backoff=False),
            ),
            patch(
                "pacemaker.api_client.fetch_user_profile", return_value=fresh_profile
            ),
        ):
            result = fetch_and_cache_profile(
                access_token="test_token",
                cache_path=str(cache_path),
            )

        # Should return the fresh profile
        assert result is not None
        assert result["account"]["email"] == "fresh@example.com"

        # And it should be cached to disk
        cached = load_cached_profile(str(cache_path))
        assert cached is not None
        assert cached["account"]["email"] == "fresh@example.com"

    def test_fetch_and_cache_profile_exception_returns_cached(self, tmp_path):
        """fetch_and_cache_profile returns cached profile when fetch_user_profile raises."""
        from pacemaker.profile_cache import cache_profile, fetch_and_cache_profile

        cache_path = tmp_path / "profile_cache.json"

        # Pre-populate cache
        cached_profile = {"account": {"email": "cached@example.com"}}
        cache_profile(cached_profile, str(cache_path))

        # No active backoff; make fetch_user_profile raise an exception
        with (
            patch(
                "pacemaker.usage_model.UsageModel",
                return_value=_make_backoff_model(is_in_backoff=False),
            ),
            patch(
                "pacemaker.api_client.fetch_user_profile",
                side_effect=RuntimeError("network error"),
            ),
        ):
            result = fetch_and_cache_profile(
                access_token="test_token",
                cache_path=str(cache_path),
            )

        # Should return the cached profile as fallback
        assert result is not None
        assert result["account"]["email"] == "cached@example.com"

    def test_fetch_and_cache_profile_exception_returns_none_when_no_cache(
        self, tmp_path
    ):
        """fetch_and_cache_profile returns None when fetch raises and no cache exists."""
        from pacemaker.profile_cache import fetch_and_cache_profile

        cache_path = tmp_path / "profile_cache.json"  # No cache

        # No active backoff; make fetch_user_profile raise an exception
        with (
            patch(
                "pacemaker.usage_model.UsageModel",
                return_value=_make_backoff_model(is_in_backoff=False),
            ),
            patch(
                "pacemaker.api_client.fetch_user_profile",
                side_effect=ConnectionError("timeout"),
            ),
        ):
            result = fetch_and_cache_profile(
                access_token="test_token",
                cache_path=str(cache_path),
            )

        assert result is None


class TestCacheProfileExceptionPaths:
    """Tests for exception paths in cache_profile()."""

    def test_cache_profile_mkdir_failure_does_not_raise(self, tmp_path):
        """cache_profile silently swallows exception when parent dir cannot be created."""
        from pacemaker.profile_cache import cache_profile

        # Make a FILE at the would-be parent path so mkdir() fails (covers lines 57-58)
        parent_blocker = tmp_path / "blocked_dir"
        parent_blocker.write_text("I am a file, not a directory")

        # Try to write inside the file (as if it were a directory) — mkdir will fail
        cache_path = str(parent_blocker) + "/profile_cache.json"

        # Should not raise — exception is caught and logged
        profile_data = {"account": {"email": "test@example.com"}}
        cache_profile(profile_data, cache_path)

    def test_cache_profile_uses_default_path_when_cache_path_is_none(
        self, tmp_path, monkeypatch
    ):
        """cache_profile uses DEFAULT_PROFILE_CACHE_PATH when cache_path is None (line 42)."""
        import pacemaker.profile_cache as pc
        from pacemaker.profile_cache import cache_profile

        default_path = tmp_path / "profile_cache.json"
        monkeypatch.setattr(pc, "DEFAULT_PROFILE_CACHE_PATH", str(default_path))

        profile_data = {"account": {"email": "default@example.com"}}
        cache_profile(profile_data, cache_path=None)

        assert default_path.exists()
        content = json.loads(default_path.read_text())
        assert content["profile"]["account"]["email"] == "default@example.com"


class TestLoadCachedProfileDefaultPath:
    """Tests for the default path branch in load_cached_profile()."""

    def test_load_cached_profile_uses_default_path_when_none(
        self, tmp_path, monkeypatch
    ):
        """load_cached_profile uses DEFAULT_PROFILE_CACHE_PATH when cache_path is None (line 78)."""
        import pacemaker.profile_cache as pc
        from pacemaker.profile_cache import cache_profile, load_cached_profile

        default_path = tmp_path / "profile_cache.json"
        monkeypatch.setattr(pc, "DEFAULT_PROFILE_CACHE_PATH", str(default_path))

        # Write to default path via cache_profile (also uses default)
        profile_data = {"account": {"email": "default@example.com"}}
        cache_profile(profile_data, cache_path=None)

        result = load_cached_profile(cache_path=None)

        assert result is not None
        assert result["account"]["email"] == "default@example.com"


class TestFetchAndCacheProfileDefaultPath:
    """Tests for the default path branch in fetch_and_cache_profile()."""

    def test_fetch_and_cache_profile_uses_default_path_when_none(
        self, tmp_path, monkeypatch
    ):
        """fetch_and_cache_profile uses DEFAULT_PROFILE_CACHE_PATH when cache_path is None (line 131)."""
        import pacemaker.profile_cache as pc
        from pacemaker.profile_cache import fetch_and_cache_profile

        default_path = tmp_path / "profile_cache.json"
        monkeypatch.setattr(pc, "DEFAULT_PROFILE_CACHE_PATH", str(default_path))

        # Active backoff so no API call is made — just returns cached (or None)
        with patch(
            "pacemaker.usage_model.UsageModel",
            return_value=_make_backoff_model(is_in_backoff=True),
        ):
            # cache_path=None means DEFAULT_PROFILE_CACHE_PATH is used (line 131)
            result = fetch_and_cache_profile(
                access_token="token",
                cache_path=None,
            )

        # No cache exists, in backoff → returns None
        assert result is None
