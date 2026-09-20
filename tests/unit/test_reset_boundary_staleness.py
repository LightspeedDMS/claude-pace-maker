"""Issue #137 — a passed reset boundary must invalidate the window immediately.

`calculate_time_percent()` used to return 100.0 for five minutes after
`resets_at` ("window just ended"), telling the pacing calculation the user was
at the point of MAXIMUM pressure. Paired with the cached utilization from the
window that had just ended — 100% in the observed incident — that produced
full-strength throttling at the start of every new window, exactly when the
most headroom exists. With the poll interval also 300s, worst-case exposure
was roughly ten minutes.
"""

from datetime import datetime, timedelta, timezone

import pytest

from pacemaker.calculator import calculate_time_percent

STALE = -1.0


def _resets_in(seconds):
    """A resets_at that is `seconds` from now (negative = already passed)."""
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


class TestPassedResetIsImmediatelyStale:
    @pytest.mark.parametrize("seconds_past", [1, 30, 60, 120, 299])
    def test_grace_band_now_reports_stale(self, seconds_past):
        """The 0-300s band used to return 100.0 — the whole bug."""
        assert calculate_time_percent(_resets_in(-seconds_past)) == STALE

    def test_never_reports_hundred_percent_after_reset(self):
        """100.0 means 'maximum pacing pressure' and must not follow a reset."""
        for seconds_past in (1, 60, 299):
            assert calculate_time_percent(_resets_in(-seconds_past)) != 100.0

    @pytest.mark.parametrize("seconds_past", [301, 600, 86400])
    def test_beyond_old_threshold_still_stale(self, seconds_past):
        """Behavior past 300s is unchanged."""
        assert calculate_time_percent(_resets_in(-seconds_past)) == STALE

    def test_seven_day_window_same_rule(self):
        assert calculate_time_percent(_resets_in(-60), window_hours=168.0) == STALE


class TestUnchangedPaths:
    def test_inactive_window_returns_zero(self):
        assert calculate_time_percent(None) == 0.0

    def test_window_not_started_returns_zero(self):
        """resets_at further out than the window length — not started yet."""
        assert calculate_time_percent(_resets_in(6 * 3600), window_hours=5.0) == 0.0

    def test_midpoint_reports_about_half(self):
        pct = calculate_time_percent(_resets_in(2.5 * 3600), window_hours=5.0)
        assert 49.0 < pct < 51.0

    def test_near_end_reports_high_but_not_stale(self):
        pct = calculate_time_percent(_resets_in(60), window_hours=5.0)
        assert 99.0 < pct <= 100.0
        assert pct != STALE


class TestEngineDoesNotThrottleOnDeadWindow:
    def test_just_reset_window_with_full_utilization_does_not_throttle(self):
        """End-to-end shape of the incident: both windows just reset while the
        cached utilization still reads 100%. Nothing may be throttled."""
        from pacemaker.pacing_engine import calculate_pacing_decision

        decision = calculate_pacing_decision(
            five_hour_util=100.0,
            five_hour_resets_at=_resets_in(-60),
            seven_day_util=63.0,
            seven_day_resets_at=_resets_in(-60),
        )
        assert decision["should_throttle"] is False
        assert decision["delay_seconds"] == 0
        assert decision["stale_data"] is True
