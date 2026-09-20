"""Issue #138 — usage credits cover the plan window once it is exhausted.

Pacing read only five_hour/seven_day utilization and discarded the rest of the
API response. In the observed incident the user was throttled at full strength
with five_hour at 100% while extra_usage was enabled, $422.54 of $500 unused,
and spend_limit_reached false. The API states the relationship in its own
payload: "Usage credits cover you when you hit your plan limits."
"""

from datetime import datetime, timedelta, timezone

import pytest

from pacemaker.api_client import parse_usage_response
from pacemaker.pacing_engine import calculate_pacing_decision

# Verbatim from api_cache.raw_response during the incident.
INCIDENT_EXTRA_USAGE = {
    "is_enabled": True,
    "monthly_limit": 50000,
    "used_credits": 7746.0,
    "utilization": 15.492,
    "currency": "USD",
    "spend_limit_reached": False,
    "user_disabled": False,
}


def _resets_in(hours):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _response(extra_usage="__omit__"):
    body = {
        "five_hour": {"utilization": 100.0, "resets_at": _resets_in(2)},
        "seven_day": {"utilization": 63.0, "resets_at": _resets_in(24)},
    }
    if extra_usage != "__omit__":
        body["extra_usage"] = extra_usage
    return body


class TestParsing:
    def test_parses_incident_payload(self):
        parsed = parse_usage_response(_response(INCIDENT_EXTRA_USAGE))
        assert parsed["credits_enabled"] is True
        assert parsed["credits_util"] == pytest.approx(15.492)
        assert parsed["credits_exhausted"] is False
        assert parsed["credits_user_disabled"] is False

    @pytest.mark.parametrize("extra", ["__omit__", None, "not-a-dict", []])
    def test_missing_or_malformed_degrades_to_previous_behavior(self, extra):
        """Accounts without credits and older API shapes must be unaffected."""
        parsed = parse_usage_response(_response(extra))
        assert parsed["credits_enabled"] is False
        assert parsed["credits_util"] == 0.0
        assert parsed["credits_exhausted"] is False

    def test_plan_windows_still_parsed(self):
        parsed = parse_usage_response(_response(INCIDENT_EXTRA_USAGE))
        assert parsed["five_hour_util"] == 100.0
        assert parsed["seven_day_util"] == 63.0


def _decide(**overrides):
    kwargs = dict(
        five_hour_util=100.0,
        five_hour_resets_at=datetime.now(timezone.utc) + timedelta(hours=2),
        seven_day_util=63.0,
        seven_day_resets_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    kwargs.update(overrides)
    return calculate_pacing_decision(**kwargs)


class TestCreditsSuppressThrottle:
    def test_incident_shape_no_longer_throttles(self):
        d = _decide(credits_enabled=True, credits_util=15.492)
        assert d["should_throttle"] is False
        assert d["delay_seconds"] == 0
        assert d["credits_covering"] is True

    def test_without_credits_behavior_unchanged(self):
        """The baseline this bug report was filed against."""
        assert _decide()["should_throttle"] is True

    def test_exhausted_credits_do_not_suppress(self):
        d = _decide(credits_enabled=True, credits_util=100.0, credits_exhausted=True)
        assert d["should_throttle"] is True
        assert "credits_covering" not in d

    def test_disabled_credits_do_not_suppress(self):
        d = _decide(credits_enabled=False, credits_util=15.0)
        assert d["should_throttle"] is True


class TestNeverThrottlesHarder:
    def test_credits_only_ever_reduce_delay(self):
        """Guard on the issue's stated invariant."""
        without = _decide()
        with_credits = _decide(credits_enabled=True, credits_util=15.492)
        assert with_credits["delay_seconds"] <= without["delay_seconds"]

    def test_below_full_plan_utilization_is_untouched(self):
        """Credits only apply once the plan window is actually exhausted."""
        base = dict(five_hour_util=50.0, seven_day_util=40.0)
        without = _decide(**base)
        with_credits = _decide(credits_enabled=True, credits_util=15.0, **base)
        assert with_credits["should_throttle"] == without["should_throttle"]
        assert with_credits["delay_seconds"] == without["delay_seconds"]
