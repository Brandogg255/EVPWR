"""Trip arithmetic and rejection rules."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from trip import SocSample, TripStatus, evaluate_trip, hourly_bucket_starts

UTC = timezone.utc


def trip(**kwargs):
    """evaluate_trip with the user's stated defaults (60 kWh battery)."""
    kwargs.setdefault("battery_capacity_kwh", 60.0)
    return evaluate_trip(**kwargs)


def test_worked_example_60_to_40_percent_over_10_hours():
    """Leaves 07:00 at 60%, returns 17:00 at 40% -> 12 kWh over 10 h -> 1200 W."""
    result = trip(
        previous=SocSample(60.0, datetime(2026, 5, 4, 7, 0, tzinfo=UTC)),
        current=SocSample(40.0, datetime(2026, 5, 4, 17, 0, tzinfo=UTC)),
    )
    assert result.status is TripStatus.VALID
    assert result.energy_kwh == pytest.approx(12.0)
    assert result.avg_power_w == pytest.approx(1200.0)
    assert result.soc_delta == pytest.approx(20.0)
    assert result.duration_minutes == pytest.approx(600.0)


def test_usable_capacity_factor_scales_energy():
    result = trip(
        previous=SocSample(60.0, datetime(2026, 5, 4, 7, 0, tzinfo=UTC)),
        current=SocSample(40.0, datetime(2026, 5, 4, 17, 0, tzinfo=UTC)),
        usable_factor=0.9,
    )
    assert result.energy_kwh == pytest.approx(10.8)
    assert result.avg_power_w == pytest.approx(1080.0)


@pytest.mark.parametrize("soc", [None, float("nan"), -1.0, 101.0])
def test_soc_unavailable_or_bogus_rejects_trip(soc):
    result = trip(
        previous=SocSample(60.0, datetime(2026, 5, 4, 7, 0, tzinfo=UTC)),
        current=SocSample(soc, datetime(2026, 5, 4, 17, 0, tzinfo=UTC)),
    )
    assert result.status is TripStatus.INVALID_SOC
    assert result.avg_power_w is None


def test_missing_timestamp_rejects_trip():
    result = trip(previous=SocSample(60.0, None), current=SocSample(40.0, None))
    assert result.status is TripStatus.INVALID_SOC


def test_soc_increase_is_flagged_charging_not_consumption():
    result = trip(
        previous=SocSample(40.0, datetime(2026, 5, 4, 7, 0, tzinfo=UTC)),
        current=SocSample(70.0, datetime(2026, 5, 4, 17, 0, tzinfo=UTC)),
    )
    assert result.status is TripStatus.CHARGING
    assert not result.is_valid
    assert result.energy_kwh == pytest.approx(-18.0)


@pytest.mark.parametrize(
    "start,end",
    [
        (datetime(2026, 5, 4, 7, 0, tzinfo=UTC), datetime(2026, 5, 4, 7, 0, tzinfo=UTC)),
        (datetime(2026, 5, 4, 17, 0, tzinfo=UTC), datetime(2026, 5, 4, 7, 0, tzinfo=UTC)),
    ],
)
def test_zero_or_negative_duration_rejected(start, end):
    result = trip(previous=SocSample(60.0, start), current=SocSample(40.0, end))
    assert result.status is TripStatus.INVALID_DURATION
    assert result.avg_power_w is None


def test_short_interval_is_a_parked_refresh_not_a_trip():
    result = trip(
        previous=SocSample(60.0, datetime(2026, 5, 4, 7, 0, tzinfo=UTC)),
        current=SocSample(59.0, datetime(2026, 5, 4, 7, 10, tzinfo=UTC)),
        min_duration_minutes=30,
    )
    assert result.status is TripStatus.TOO_SHORT
    assert not result.is_valid


def test_stale_soc_over_months_rejected():
    result = trip(
        previous=SocSample(60.0, datetime(2026, 1, 1, 7, 0, tzinfo=UTC)),
        current=SocSample(40.0, datetime(2026, 5, 4, 17, 0, tzinfo=UTC)),
        max_trip_days=30,
    )
    assert result.status is TripStatus.TOO_LONG


def test_sub_resolution_soc_change_is_noise():
    """0.5% of 60 kWh = 0.3 kWh: below the SOC sensor's 1% resolution."""
    result = trip(
        previous=SocSample(60.0, datetime(2026, 5, 4, 7, 0, tzinfo=UTC)),
        current=SocSample(59.5, datetime(2026, 5, 4, 17, 0, tzinfo=UTC)),
        min_soc_delta=1.0,
    )
    assert result.status is TripStatus.NOISE


def test_multiple_trips_in_one_day_are_separate():
    morning_out = SocSample(80.0, datetime(2026, 5, 4, 7, 0, tzinfo=UTC))
    morning_back = SocSample(70.0, datetime(2026, 5, 4, 9, 0, tzinfo=UTC))
    evening_out = SocSample(70.0, datetime(2026, 5, 4, 17, 0, tzinfo=UTC))
    evening_back = SocSample(58.0, datetime(2026, 5, 4, 20, 0, tzinfo=UTC))

    first = trip(previous=morning_out, current=morning_back)
    second = trip(previous=evening_out, current=evening_back)

    assert first.status is second.status is TripStatus.VALID
    assert first.energy_kwh == pytest.approx(6.0)
    assert first.avg_power_w == pytest.approx(3000.0)
    assert second.energy_kwh == pytest.approx(7.2)
    assert second.avg_power_w == pytest.approx(2400.0)
    assert first.end < second.start


def test_dst_spring_forward_uses_absolute_duration():
    """01:00 EST -> 03:00 EDT on a spring-forward day is one real hour."""
    new_york = ZoneInfo("America/New_York")
    result = trip(
        previous=SocSample(60.0, datetime(2026, 3, 8, 1, 0, tzinfo=new_york)),
        current=SocSample(59.0, datetime(2026, 3, 8, 3, 0, tzinfo=new_york)),
        min_duration_minutes=30,
    )
    assert result.status is TripStatus.VALID
    assert result.duration_minutes == pytest.approx(60.0)
    assert result.energy_kwh == pytest.approx(0.6)
    assert result.avg_power_w == pytest.approx(600.0)


def test_dst_fall_back_uses_absolute_duration():
    new_york = ZoneInfo("America/New_York")
    result = trip(
        previous=SocSample(60.0, datetime(2026, 11, 1, 0, 30, tzinfo=new_york)),
        current=SocSample(58.0, datetime(2026, 11, 1, 2, 30, tzinfo=new_york)),
        min_duration_minutes=30,
    )
    assert result.status is TripStatus.VALID
    assert result.duration_minutes == pytest.approx(180.0)
    assert result.avg_power_w == pytest.approx(400.0)


def test_buckets_for_whole_hour_trip():
    buckets = hourly_bucket_starts(
        datetime(2026, 5, 4, 7, 0, tzinfo=UTC), datetime(2026, 5, 4, 17, 0, tzinfo=UTC)
    )
    assert len(buckets) == 10
    assert buckets[0] == datetime(2026, 5, 4, 7, 0, tzinfo=UTC)
    assert buckets[-1] == datetime(2026, 5, 4, 16, 0, tzinfo=UTC)


def test_buckets_cover_mid_hour_trip_edges():
    buckets = hourly_bucket_starts(
        datetime(2026, 5, 4, 7, 20, tzinfo=UTC), datetime(2026, 5, 4, 17, 40, tzinfo=UTC)
    )
    assert len(buckets) == 11
    assert buckets[0] == datetime(2026, 5, 4, 7, 0, tzinfo=UTC)
    assert buckets[-1] == datetime(2026, 5, 4, 17, 0, tzinfo=UTC)


def test_sub_hour_trip_still_gets_one_bucket():
    buckets = hourly_bucket_starts(
        datetime(2026, 5, 4, 7, 20, tzinfo=UTC), datetime(2026, 5, 4, 7, 40, tzinfo=UTC)
    )
    assert buckets == [datetime(2026, 5, 4, 7, 0, tzinfo=UTC)]


def test_buckets_are_utc_aligned_and_local_trips_align_to_utc_hours():
    berlin = ZoneInfo("Europe/Berlin")
    buckets = hourly_bucket_starts(
        datetime(2026, 5, 4, 7, 0, tzinfo=berlin), datetime(2026, 5, 4, 10, 0, tzinfo=berlin)
    )
    assert all(b.tzinfo == UTC and (b.minute, b.second, b.microsecond) == (0, 0, 0) for b in buckets)
    assert buckets[0] == datetime(2026, 5, 4, 5, 0, tzinfo=UTC)
    assert len(buckets) == 3


def test_bucket_count_matches_trip_hours():
    """A 10 h trip tiles exactly 10 hourly buckets, so the graph is a flat line."""
    start = datetime(2026, 5, 4, 7, 0, tzinfo=UTC)
    buckets = hourly_bucket_starts(start, start + timedelta(hours=10))
    assert len(buckets) == 10


def test_unchanged_soc_is_not_a_trip_even_with_zero_threshold():
    result = trip(
        previous=SocSample(60.0, datetime(2026, 5, 4, 7, 0, tzinfo=UTC)),
        current=SocSample(60.0, datetime(2026, 5, 4, 17, 0, tzinfo=UTC)),
        min_soc_delta=0.0,
    )
    assert result.status is TripStatus.NOISE
    assert not result.is_valid
