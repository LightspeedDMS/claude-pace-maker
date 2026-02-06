#!/usr/bin/env python3
"""
Langfuse stats CLI command implementation (Story #33).

Fetches usage statistics from Langfuse Metrics API v2:
- AC1: Daily usage summary
- AC2: Weekly breakdown with --week flag
- AC3: API integration with 60-second caching
- AC4: Graceful fallback with cached data
- AC5: <3 second response time
"""

import requests
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, date
from . import cache


# Claude Opus 4 pricing (per million tokens)
OPUS_INPUT_PRICE_PER_M = 15.0  # $15/M input tokens
OPUS_OUTPUT_PRICE_PER_M = 75.0  # $75/M output tokens


def calculate_cost(input_tokens: int, output_tokens: int) -> float:
    """
    Calculate estimated cost using Claude Opus pricing.

    Args:
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens

    Returns:
        Cost in USD
    """
    input_cost = (input_tokens / 1_000_000) * OPUS_INPUT_PRICE_PER_M
    output_cost = (output_tokens / 1_000_000) * OPUS_OUTPUT_PRICE_PER_M
    return input_cost + output_cost


def query_metrics_api(
    base_url: str,
    public_key: str,
    secret_key: str,
    from_date: date,
    to_date: date,
    view: str = "traces",
    metrics: Optional[List[Dict[str, str]]] = None,
    dimensions: Optional[List[Dict[str, str]]] = None,
    timeout: int = 3,
) -> Dict[str, Any]:
    """
    Query Langfuse Metrics API v2.

    Args:
        base_url: Langfuse API base URL
        public_key: API public key
        secret_key: API secret key
        from_date: Start date
        to_date: End date
        view: API view ("traces" or "observations")
        metrics: List of metric definitions
        dimensions: List of dimension definitions
        timeout: Request timeout in seconds

    Returns:
        API response data

    Raises:
        requests.exceptions.RequestException: On API errors
    """
    endpoint = f"{base_url.rstrip('/')}/api/public/v2/metrics"

    # Build query
    query = {
        "view": view,
        "metrics": metrics or [],
        "dimensions": dimensions or [],
        "filters": [],
        "fromTimestamp": f"{from_date}T00:00:00Z",
        "toTimestamp": f"{to_date}T23:59:59Z",
    }

    # Make request
    response = requests.get(
        endpoint,
        params={"query": json.dumps(query)},
        auth=(public_key, secret_key),
        timeout=timeout,
    )

    response.raise_for_status()
    return response.json()


def query_trace_count(
    base_url: str, public_key: str, secret_key: str, from_date: date, to_date: date
) -> int:
    """
    Query trace count (sessions) from Langfuse.

    Args:
        base_url: Langfuse API base URL
        public_key: API public key
        secret_key: API secret key
        from_date: Start date
        to_date: End date

    Returns:
        Number of traces (sessions)
    """
    result = query_metrics_api(
        base_url,
        public_key,
        secret_key,
        from_date,
        to_date,
        view="traces",
        metrics=[{"measure": "traceCount", "aggregation": "count"}],
    )

    # Extract count from response
    data = result.get("data", [])
    if data:
        return data[0].get("traceCount_count", 0)
    return 0


def query_token_usage(
    base_url: str, public_key: str, secret_key: str, from_date: date, to_date: date
) -> Dict[str, int]:
    """
    Query token usage from Langfuse observations.

    Args:
        base_url: Langfuse API base URL
        public_key: API public key
        secret_key: API secret key
        from_date: Start date
        to_date: End date

    Returns:
        Dict with input_tokens and output_tokens
    """
    result = query_metrics_api(
        base_url,
        public_key,
        secret_key,
        from_date,
        to_date,
        view="observations",
        metrics=[
            {"measure": "inputUsage", "aggregation": "sum"},
            {"measure": "outputUsage", "aggregation": "sum"},
        ],
    )

    # Extract token counts from response
    data = result.get("data", [])
    if data:
        return {
            "input_tokens": int(data[0].get("inputUsage_sum", 0)),
            "output_tokens": int(data[0].get("outputUsage_sum", 0)),
        }
    return {"input_tokens": 0, "output_tokens": 0}


def fetch_daily_stats(
    from_date: date,
    to_date: date,
    base_url: Optional[str] = None,
    public_key: Optional[str] = None,
    secret_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetch daily stats with caching (AC3).

    Checks cache first, falls back to API if cache miss.

    Args:
        from_date: Start date
        to_date: End date
        base_url: API base URL (required if not cached)
        public_key: API public key (required if not cached)
        secret_key: API secret key (required if not cached)

    Returns:
        Dict with traces, input_tokens, output_tokens, cost_usd

    Raises:
        Exception: On API errors if not cached
    """
    # Check cache
    cache_key = cache.generate_key("daily", from_date, to_date)
    cached_data = cache.get(cache_key)
    if cached_data:
        return cached_data

    # API call required
    if not all([base_url, public_key, secret_key]):
        raise ValueError("API credentials required for cache miss")

    # Type narrowing for mypy: after the check above, these cannot be None
    assert base_url is not None
    assert public_key is not None
    assert secret_key is not None

    # Fetch trace count and token usage in parallel would be ideal,
    # but for simplicity and staying under 3s, we'll do sequential
    trace_count = query_trace_count(
        base_url, public_key, secret_key, from_date, to_date
    )
    token_usage = query_token_usage(
        base_url, public_key, secret_key, from_date, to_date
    )

    # Calculate cost
    cost = calculate_cost(token_usage["input_tokens"], token_usage["output_tokens"])

    result = {
        "traces": trace_count,
        "input_tokens": token_usage["input_tokens"],
        "output_tokens": token_usage["output_tokens"],
        "cost_usd": cost,
    }

    # Cache result
    cache.set(cache_key, result, ttl=60)

    return result


def fetch_weekly_stats(
    from_date: date, to_date: date, base_url: str, public_key: str, secret_key: str
) -> List[Dict[str, Any]]:
    """
    Fetch weekly stats (7 days) with per-day breakdown (AC2).

    Args:
        from_date: Start date
        to_date: End date (should be from_date + 6 days)
        base_url: API base URL
        public_key: API public key
        secret_key: API secret key

    Returns:
        List of daily stats dicts
    """
    # Check cache
    cache_key = cache.generate_key("weekly", from_date, to_date)
    cached_data = cache.get(cache_key)
    if cached_data:
        return cached_data

    # Query with date dimension for daily breakdown
    result = query_metrics_api(
        base_url,
        public_key,
        secret_key,
        from_date,
        to_date,
        view="observations",
        metrics=[
            {"measure": "traceCount", "aggregation": "count"},
            {"measure": "inputUsage", "aggregation": "sum"},
            {"measure": "outputUsage", "aggregation": "sum"},
        ],
        dimensions=[{"field": "traceTimestamp", "temporalUnit": "day"}],
    )

    # Parse response into daily records
    daily_stats = []
    data = result.get("data", [])

    for entry in data:
        # Extract date from timestamp
        timestamp = entry.get("traceTimestamp")
        if timestamp:
            # Parse ISO timestamp to date
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            date_str = dt.strftime("%Y-%m-%d")
        else:
            continue

        input_tokens = int(entry.get("inputUsage_sum", 0))
        output_tokens = int(entry.get("outputUsage_sum", 0))
        cost = calculate_cost(input_tokens, output_tokens)

        daily_stats.append(
            {
                "date": date_str,
                "traces": int(entry.get("traceCount_count", 0)),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost,
            }
        )

    # Cache result
    cache.set(cache_key, daily_stats, ttl=60)

    return daily_stats


def format_daily_summary(stats: Dict[str, Any]) -> str:
    """
    Format daily stats for display (AC1).

    Args:
        stats: Dict with traces, input_tokens, output_tokens, cost_usd

    Returns:
        Formatted string
    """
    return f"""Sessions today: {stats['traces']}
Total input tokens: {stats['input_tokens']:,}
Total output tokens: {stats['output_tokens']:,}
Estimated cost: ${stats['cost_usd']:.2f}"""


def format_weekly_breakdown(weekly_stats: List[Dict[str, Any]]) -> str:
    """
    Format weekly stats as table (AC2).

    Args:
        weekly_stats: List of daily stats dicts

    Returns:
        Formatted table string
    """
    # Calculate totals
    total_traces = sum(day["traces"] for day in weekly_stats)
    total_input = sum(day["input_tokens"] for day in weekly_stats)
    total_output = sum(day["output_tokens"] for day in weekly_stats)
    total_cost = sum(day["cost_usd"] for day in weekly_stats)

    # Build table
    lines = []
    lines.append("| Date       | Sessions | Input Tokens | Output Tokens | Est. Cost |")
    lines.append("|------------|----------|--------------|---------------|-----------|")

    for day in weekly_stats:
        lines.append(
            f"| {day['date']} |    {day['traces']:>2}    | {day['input_tokens']:>12,} | {day['output_tokens']:>13,} | ${day['cost_usd']:>8.2f} |"
        )

    # Add totals row
    lines.append("|------------|----------|--------------|---------------|-----------|")
    lines.append(
        f"| Total      |   {total_traces:>3}    | {total_input:>12,} | {total_output:>13,} | ${total_cost:>8.2f} |"
    )

    return "\n".join(lines)


def get_daily_summary(
    base_url: Optional[str] = None,
    public_key: Optional[str] = None,
    secret_key: Optional[str] = None,
) -> str:
    """
    Get daily usage summary (AC1 + AC4 graceful fallback).

    Args:
        base_url: API base URL
        public_key: API public key
        secret_key: API secret key

    Returns:
        Formatted daily summary string
    """
    today = datetime.now().date()

    try:
        stats = fetch_daily_stats(today, today, base_url, public_key, secret_key)
        return format_daily_summary(stats)
    except Exception as e:
        # AC4: Graceful fallback with cached data
        cache_key = cache.generate_key("daily", today, today)
        cached_meta = cache.get_with_metadata(cache_key)

        if cached_meta and cached_meta.get("data"):
            # Calculate age
            cached_at = cached_meta.get("cached_at")
            if cached_at:
                age = datetime.now() - cached_at
                hours = int(age.total_seconds() / 3600)
                minutes = int((age.total_seconds() % 3600) / 60)
                age_str = f"{hours} hours ago" if hours > 0 else f"{minutes} min ago"
            else:
                age_str = "unknown time"

            error_msg = f"[LANGFUSE] Unable to fetch stats: {str(e)}\nLast cached stats ({age_str}):\n"
            return error_msg + format_daily_summary(cached_meta["data"])
        else:
            return (
                f"[LANGFUSE] Unable to fetch stats: {str(e)}\nNo cached data available."
            )


def get_weekly_breakdown(
    base_url: Optional[str] = None,
    public_key: Optional[str] = None,
    secret_key: Optional[str] = None,
) -> str:
    """
    Get weekly breakdown (AC2 + AC4 graceful fallback).

    Args:
        base_url: API base URL
        public_key: API public key
        secret_key: API secret key

    Returns:
        Formatted weekly table string
    """
    today = datetime.now().date()
    week_ago = today - timedelta(days=6)  # 7 days inclusive

    try:
        # Type narrowing for mypy: these should be provided by caller
        assert base_url is not None, "base_url required"
        assert public_key is not None, "public_key required"
        assert secret_key is not None, "secret_key required"

        stats = fetch_weekly_stats(week_ago, today, base_url, public_key, secret_key)
        return format_weekly_breakdown(stats)
    except Exception as e:
        # AC4: Graceful fallback with cached data
        cache_key = cache.generate_key("weekly", week_ago, today)
        cached_meta = cache.get_with_metadata(cache_key)

        if cached_meta and cached_meta.get("data"):
            # Calculate age
            cached_at = cached_meta.get("cached_at")
            if cached_at:
                age = datetime.now() - cached_at
                hours = int(age.total_seconds() / 3600)
                minutes = int((age.total_seconds() % 3600) / 60)
                age_str = f"{hours} hours ago" if hours > 0 else f"{minutes} min ago"
            else:
                age_str = "unknown time"

            error_msg = f"[LANGFUSE] Unable to fetch stats: {str(e)}\nLast cached stats ({age_str}):\n\n"
            return error_msg + format_weekly_breakdown(cached_meta["data"])
        else:
            return (
                f"[LANGFUSE] Unable to fetch stats: {str(e)}\nNo cached data available."
            )
