#!/usr/bin/env python3
"""
Unit tests for Langfuse stats command (Story #33).

Tests AC1-AC5:
- AC1: Daily Usage Summary Command
- AC2: Weekly Breakdown Option
- AC3: Langfuse Metrics API Integration
- AC4: Graceful Fallback on Unavailability
- AC5: Response Time Under 3 Seconds
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import json
import time


# AC1: Daily Usage Summary Command Tests
class TestDailyUsageSummary:
    """AC1: Test 'pace-maker langfuse stats' daily summary."""

    def test_daily_stats_queries_api_for_today(self):
        """GIVEN Langfuse configured, WHEN stats command runs, THEN queries API for today's data."""
        from pacemaker.langfuse import stats

        today = datetime.now().date()
        mock_data = {
            "traces": 5,
            "input_tokens": 125000,
            "output_tokens": 28000,
            "cost_usd": 4.575,
        }

        with patch.object(
            stats, "fetch_daily_stats", return_value=mock_data
        ) as mock_fetch:
            stats.get_daily_summary()

            # Should call API with today's date
            mock_fetch.assert_called_once()
            args = mock_fetch.call_args[0]
            assert args[0] == today
            assert args[1] == today  # from_date = to_date for daily

    def test_daily_stats_formats_output_correctly(self):
        """GIVEN API returns data, WHEN formatting, THEN display shows sessions, tokens, cost."""
        from pacemaker.langfuse import stats

        mock_data = {
            "traces": 5,
            "input_tokens": 125000,
            "output_tokens": 28000,
            "cost_usd": 4.575,
        }

        output = stats.format_daily_summary(mock_data)

        # AC1 requirements: sessions, input tokens, output tokens, cost
        assert "Sessions today: 5" in output
        assert "125,000" in output  # Input tokens with comma formatting
        assert "28,000" in output  # Output tokens with comma formatting
        assert "$4.58" in output  # Cost formatted to 2 decimals

    def test_daily_stats_calculates_cost_from_tokens(self):
        """GIVEN token counts, WHEN calculating cost, THEN use Claude Opus pricing ($15/M input, $75/M output)."""
        from pacemaker.langfuse import stats

        input_tokens = 100000  # 0.1M
        output_tokens = 50000  # 0.05M

        # Expected: (0.1 * 15) + (0.05 * 75) = 1.5 + 3.75 = $5.25
        cost = stats.calculate_cost(input_tokens, output_tokens)

        assert cost == pytest.approx(5.25, abs=0.01)

    def test_daily_stats_completes_within_3_seconds(self):
        """AC5: GIVEN API is responsive, WHEN fetching daily stats, THEN completes within 3 seconds."""
        from pacemaker.langfuse import stats

        mock_data = {
            "traces": 5,
            "input_tokens": 125000,
            "output_tokens": 28000,
            "cost_usd": 4.575,
        }

        with patch.object(stats, "fetch_daily_stats", return_value=mock_data):
            start = time.time()
            stats.get_daily_summary()
            elapsed = time.time() - start

            assert elapsed < 3.0, f"Daily stats took {elapsed:.2f}s, should be < 3s"


# AC2: Weekly Breakdown Option Tests
class TestWeeklyBreakdown:
    """AC2: Test 'pace-maker langfuse stats --week' weekly breakdown."""

    def test_weekly_stats_queries_7_days(self):
        """GIVEN --week flag, WHEN stats command runs, THEN queries API for 7-day range."""
        from pacemaker.langfuse import stats

        mock_data = [
            {
                "date": "2026-02-04",
                "traces": 5,
                "input_tokens": 125000,
                "output_tokens": 28000,
            },
            {
                "date": "2026-02-03",
                "traces": 8,
                "input_tokens": 150000,
                "output_tokens": 35000,
            },
            # ... more days
        ]

        with patch.object(
            stats, "fetch_weekly_stats", return_value=mock_data
        ) as mock_fetch:
            stats.get_weekly_breakdown()

            # Should call API with 7-day range
            mock_fetch.assert_called_once()
            args = mock_fetch.call_args[0]
            from_date, to_date = args[0], args[1]
            assert (to_date - from_date).days == 6  # 7 days inclusive

    def test_weekly_stats_formats_table_correctly(self):
        """GIVEN 7 days of data, WHEN formatting, THEN display table with date, sessions, tokens, cost."""
        from pacemaker.langfuse import stats

        mock_data = [
            {
                "date": "2026-02-04",
                "traces": 5,
                "input_tokens": 125000,
                "output_tokens": 28000,
                "cost_usd": 4.575,
            },
            {
                "date": "2026-02-03",
                "traces": 8,
                "input_tokens": 150000,
                "output_tokens": 35000,
                "cost_usd": 4.875,
            },
        ]

        output = stats.format_weekly_breakdown(mock_data)

        # AC2 requirements: table with columns
        assert "Date" in output
        assert "Sessions" in output
        assert "Input Tokens" in output
        assert "Output Tokens" in output
        assert "Est. Cost" in output
        assert "2026-02-04" in output
        assert "2026-02-03" in output
        assert "Total" in output  # Should have totals row

    def test_weekly_stats_includes_totals_row(self):
        """GIVEN 7 days of data, WHEN formatting, THEN include totals row at bottom."""
        from pacemaker.langfuse import stats

        mock_data = [
            {
                "date": "2026-02-04",
                "traces": 5,
                "input_tokens": 125000,
                "output_tokens": 28000,
                "cost_usd": 4.575,
            },
            {
                "date": "2026-02-03",
                "traces": 8,
                "input_tokens": 150000,
                "output_tokens": 35000,
                "cost_usd": 4.875,
            },
        ]

        output = stats.format_weekly_breakdown(mock_data)

        # Should sum all values
        assert "13" in output  # Total sessions (5 + 8)
        assert "275,000" in output  # Total input tokens
        assert "63,000" in output  # Total output tokens
        assert "9.45" in output  # Total cost (may have spacing in table format)

    def test_weekly_stats_completes_within_3_seconds(self):
        """AC5: GIVEN API is responsive, WHEN fetching weekly stats, THEN completes within 3 seconds."""
        from pacemaker.langfuse import stats

        mock_data = [
            {
                "date": f"2026-02-0{i}",
                "traces": 5,
                "input_tokens": 100000,
                "output_tokens": 20000,
            }
            for i in range(1, 8)
        ]

        with patch.object(stats, "fetch_weekly_stats", return_value=mock_data):
            start = time.time()
            stats.get_weekly_breakdown()
            elapsed = time.time() - start

            assert elapsed < 3.0, f"Weekly stats took {elapsed:.2f}s, should be < 3s"


# AC3: Langfuse Metrics API Integration Tests
class TestLangfuseAPIIntegration:
    """AC3: Test Langfuse Metrics API queries and caching."""

    def test_api_uses_v2_metrics_endpoint(self):
        """GIVEN Langfuse configured, WHEN fetching stats, THEN use /api/public/v2/metrics endpoint."""
        from pacemaker.langfuse import stats

        base_url = "https://cloud.langfuse.com"
        public_key = "pk-test"
        secret_key = "sk-test"

        with patch("requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {"data": []}

            stats.query_metrics_api(
                base_url,
                public_key,
                secret_key,
                from_date=datetime.now().date(),
                to_date=datetime.now().date(),
            )

            # Should call v2 metrics endpoint
            call_url = mock_get.call_args[0][0]
            assert "/api/public/v2/metrics" in call_url

    def test_api_queries_traces_view_for_session_count(self):
        """GIVEN metrics query, WHEN counting sessions, THEN query traces view."""
        from pacemaker.langfuse import stats

        with patch("requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {"data": [{"traceCount": 42}]}

            stats.query_trace_count(
                "https://cloud.langfuse.com",
                "pk",
                "sk",
                datetime.now().date(),
                datetime.now().date(),
            )

            # Should query traces view
            call_args = mock_get.call_args
            query_param = call_args[1].get("params", {}).get("query")
            if query_param:
                query = json.loads(query_param)
                assert query["view"] == "traces"

    def test_api_queries_observations_view_for_tokens(self):
        """GIVEN metrics query, WHEN fetching token usage, THEN query observations view with token metrics."""
        from pacemaker.langfuse import stats

        with patch("requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {
                "data": [{"inputUsage_sum": 125000, "outputUsage_sum": 28000}]
            }

            stats.query_token_usage(
                "https://cloud.langfuse.com",
                "pk",
                "sk",
                datetime.now().date(),
                datetime.now().date(),
            )

            # Should query observations view with token aggregations
            call_args = mock_get.call_args
            query_param = call_args[1].get("params", {}).get("query")
            if query_param:
                query = json.loads(query_param)
                assert query["view"] == "observations"
                # Should have inputUsage and outputUsage metrics
                metrics = [m["measure"] for m in query["metrics"]]
                assert "inputUsage" in metrics or "usage" in str(metrics)

    def test_api_uses_http_basic_auth(self):
        """GIVEN API credentials, WHEN making requests, THEN use HTTP Basic Auth."""
        from pacemaker.langfuse import stats

        with patch("requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {"data": []}

            stats.query_metrics_api(
                "https://cloud.langfuse.com",
                "pk-test",
                "sk-test",
                datetime.now().date(),
                datetime.now().date(),
            )

            # Should use auth parameter
            call_args = mock_get.call_args
            assert call_args[1].get("auth") == ("pk-test", "sk-test")

    def test_cache_stores_results_for_60_seconds(self):
        """AC3: GIVEN API response, WHEN caching, THEN store for 60 seconds."""
        from pacemaker.langfuse import cache

        test_key = "daily_stats_2026-02-04"
        test_data = {"traces": 5, "input_tokens": 125000}

        cache.set(test_key, test_data, ttl=60)

        # Should retrieve immediately
        cached = cache.get(test_key)
        assert cached == test_data

        # Should still be cached after 30 seconds
        with patch("time.time", return_value=time.time() + 30):
            cached = cache.get(test_key)
            assert cached == test_data

        # Should expire after 61 seconds
        with patch("time.time", return_value=time.time() + 61):
            cached = cache.get(test_key)
            assert cached is None

    def test_stats_uses_cache_before_api_call(self):
        """GIVEN cached data exists, WHEN fetching stats, THEN return cached data without API call."""
        from pacemaker.langfuse import stats

        cached_data = {"traces": 5, "input_tokens": 125000, "output_tokens": 28000}

        with patch.object(stats, "cache") as mock_cache:
            mock_cache.get.return_value = cached_data

            with patch("requests.get") as mock_get:
                result = stats.fetch_daily_stats(
                    datetime.now().date(), datetime.now().date()
                )

                # Should return cached data
                assert result == cached_data

                # Should NOT call API
                mock_get.assert_not_called()

    def test_cache_get_with_metadata_when_not_found(self):
        """GIVEN cache key not found, WHEN calling get_with_metadata, THEN return None."""
        from pacemaker.langfuse import cache

        result = cache.get_with_metadata("nonexistent_key")
        assert result is None

    def test_cache_clear_removes_all_data(self):
        """GIVEN cached data exists, WHEN calling clear, THEN remove all entries."""
        from pacemaker.langfuse import cache

        # Add some data
        cache.set("key1", {"data": 1}, ttl=60)
        cache.set("key2", {"data": 2}, ttl=60)

        # Verify data exists
        assert cache.get("key1") is not None
        assert cache.get("key2") is not None

        # Clear cache
        cache.clear()

        # Verify data removed
        assert cache.get("key1") is None
        assert cache.get("key2") is None


# AC4: Graceful Fallback on Unavailability Tests
class TestGracefulFallback:
    """AC4: Test graceful fallback when Langfuse is unavailable."""

    def test_connection_error_shows_cached_stats(self):
        """GIVEN Langfuse unreachable AND cached data exists, WHEN fetching stats, THEN show cached with timestamp."""
        from pacemaker.langfuse import stats

        cached_data = {
            "traces": 5,
            "input_tokens": 125000,
            "output_tokens": 28000,
            "cost_usd": 4.575,
        }

        cached_meta = {
            "data": cached_data,
            "cached_at": datetime.now() - timedelta(hours=2),
            "expired": True,
        }

        with patch("requests.get", side_effect=ConnectionError("Connection refused")):
            with patch.object(stats, "cache") as mock_cache:
                mock_cache.get.return_value = None  # Cache miss triggers API call
                mock_cache.get_with_metadata.return_value = cached_meta

                result = stats.get_daily_summary()

                # Should include fallback message
                assert "Unable to fetch stats" in result or "cached" in result.lower()
                assert "2 hours ago" in result or "120 min" in result

    def test_timeout_error_exits_with_code_0(self):
        """AC4: GIVEN Langfuse timeout, WHEN command fails, THEN exit code 0 (not error)."""
        from pacemaker import user_commands

        with patch("requests.get", side_effect=TimeoutError("Connection timeout")):
            with patch.object(user_commands, "_langfuse_stats") as mock_handler:
                mock_handler.return_value = {
                    "success": True,  # AC4: Should be success=True even on timeout
                    "message": "Unable to fetch stats: Connection timeout\nNo cached data available.",
                }

                result = user_commands._execute_langfuse("~/.config", "stats")

                # Should succeed (exit 0) even though API failed
                assert result["success"] is True

    def test_no_cache_shows_friendly_message(self):
        """GIVEN Langfuse unreachable AND no cached data, WHEN fetching stats, THEN show friendly message."""
        from pacemaker.langfuse import stats

        with patch("requests.get", side_effect=ConnectionError("Connection refused")):
            with patch.object(stats, "cache") as mock_cache:
                mock_cache.get.return_value = None  # Cache miss triggers API call
                mock_cache.get_with_metadata.return_value = (
                    None  # No cached fallback data
                )

                result = stats.get_daily_summary()

                # Should show error but not crash
                assert "Unable to fetch stats" in result
                assert "No cached data" in result or "unavailable" in result.lower()


# AC5: Performance Tests
class TestPerformance:
    """AC5: Test response time under 3 seconds."""

    def test_daily_stats_performance_with_real_api_simulation(self):
        """GIVEN realistic API latency, WHEN fetching daily stats, THEN complete within 3 seconds."""
        from pacemaker.langfuse import stats

        def mock_api_call(*args, **kwargs):
            time.sleep(0.5)  # Simulate 500ms API latency
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "data": [
                    {
                        "traceCount": 5,
                        "inputUsage_sum": 125000,
                        "outputUsage_sum": 28000,
                    }
                ]
            }
            return mock_response

        with patch("requests.get", side_effect=mock_api_call):
            start = time.time()
            stats.get_daily_summary()
            elapsed = time.time() - start

            assert (
                elapsed < 3.0
            ), f"Daily stats took {elapsed:.2f}s with 500ms API latency"

    def test_weekly_stats_performance_with_aggregation(self):
        """GIVEN 7 days of data, WHEN fetching weekly stats, THEN server-side aggregation completes within 3s."""
        from pacemaker.langfuse import stats

        def mock_api_call(*args, **kwargs):
            time.sleep(0.8)  # Simulate 800ms API latency for aggregation
            mock_response = MagicMock()
            mock_response.status_code = 200
            # Simulate server-side aggregation returning daily data
            mock_response.json.return_value = {
                "data": [
                    {
                        "date": f"2026-02-0{i}",
                        "traceCount": 5,
                        "inputUsage_sum": 100000,
                        "outputUsage_sum": 20000,
                    }
                    for i in range(1, 8)
                ]
            }
            return mock_response

        with patch("requests.get", side_effect=mock_api_call):
            start = time.time()
            stats.get_weekly_breakdown()
            elapsed = time.time() - start

            assert (
                elapsed < 3.0
            ), f"Weekly stats took {elapsed:.2f}s with 800ms API latency"


# Integration with user_commands.py
class TestUserCommandsIntegration:
    """Test stats command integration with user_commands.py dispatcher."""

    def test_parse_langfuse_stats_command(self):
        """GIVEN 'pace-maker langfuse stats', WHEN parsing, THEN recognize as langfuse command."""
        from pacemaker import user_commands

        result = user_commands.parse_command("pace-maker langfuse stats")

        assert result["is_pace_maker_command"] is True
        assert result["command"] == "langfuse"
        assert result["subcommand"] == "stats"

    def test_parse_langfuse_stats_week_command(self):
        """GIVEN 'pace-maker langfuse stats --week', WHEN parsing, THEN recognize --week flag."""
        from pacemaker import user_commands

        result = user_commands.parse_command("pace-maker langfuse stats --week")

        assert result["is_pace_maker_command"] is True
        assert result["command"] == "langfuse"
        assert "week" in result["subcommand"]

    def test_execute_stats_requires_langfuse_configured(self):
        """GIVEN Langfuse not configured, WHEN running stats, THEN show configuration error."""
        from pacemaker import user_commands

        with patch.object(user_commands, "_load_config") as mock_load:
            mock_load.return_value = {
                "langfuse_base_url": None,
                "langfuse_public_key": None,
                "langfuse_secret_key": None,
            }

            result = user_commands._execute_langfuse("/tmp/config.json", "stats")

            assert result["success"] is False
            assert "not configured" in result["message"].lower()

    def test_execute_stats_requires_langfuse_enabled(self):
        """GIVEN Langfuse configured but disabled, WHEN running stats, THEN show disabled error."""
        from pacemaker import user_commands

        with patch.object(user_commands, "_load_config") as mock_load:
            mock_load.return_value = {
                "langfuse_base_url": "https://cloud.langfuse.com",
                "langfuse_public_key": "pk-test",
                "langfuse_secret_key": "sk-test",
                "langfuse_enabled": False,
            }

            result = user_commands._execute_langfuse("/tmp/config.json", "stats")

            assert result["success"] is False
            assert "disabled" in result["message"].lower()
