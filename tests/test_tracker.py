"""The SOC sample chain: anchor handling, totals, restart restore."""

from datetime import datetime, timezone

import pytest
from trip import SocSample, TripStatus, TripTracker

UTC = timezone.utc


def tracker(**kwargs):
    kwargs.setdefault("battery_capacity_kwh", 60.0)
    return TripTracker(**kwargs)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 5, 4, hour, minute, tzinfo=UTC)


def test_first_sample_only_opens_a_trip():
    state = tracker()
    assert state.add_sample(SocSample(60.0, at(7))) is None
    assert state.anchor == SocSample(60.0, at(7))
    assert state.trips_recorded == 0


def test_parked_refresh_folds_into_open_trip_and_keeps_the_start():
    state = tracker(min_duration_minutes=30)
    state.add_sample(SocSample(60.0, at(7)))
    refresh = state.add_sample(SocSample(59.0, at(7, 10)))
    assert refresh.status is TripStatus.TOO_SHORT
    assert state.anchor == SocSample(59.0, at(7))

    trip = state.add_sample(SocSample(55.0, at(17)))
    assert trip.status is TripStatus.VALID
    assert trip.start == at(7)
    assert trip.soc_start == 59.0
    assert trip.energy_kwh == pytest.approx(2.4)
    assert trip.avg_power_w == pytest.approx(240.0)


def test_charging_away_resets_the_anchor_and_is_not_counted():
    state = tracker()
    state.add_sample(SocSample(60.0, at(7)))
    state.add_sample(SocSample(50.0, at(17)))  # valid 6 kWh trip
    charge = state.add_sample(SocSample(80.0, at(19)))

    assert charge.status is TripStatus.CHARGING
    assert state.anchor == SocSample(80.0, at(19))
    assert state.trips_recorded == 1
    assert state.total_kwh == pytest.approx(6.0)
    assert state.last_trip.status is TripStatus.CHARGING
    assert state.last_valid_trip.status is TripStatus.VALID


def test_totals_accumulate_across_trips_in_one_day():
    state = tracker()
    for soc, hour in [(80.0, 7), (70.0, 9), (70.0, 17), (58.0, 20)]:
        state.add_sample(SocSample(soc, at(hour)))

    assert state.trips_recorded == 2
    assert state.total_kwh == pytest.approx(13.2)
    assert state.last_valid_trip.avg_power_w == pytest.approx(2400.0)


def test_rejected_trip_still_moves_the_anchor_forward():
    state = tracker(min_soc_delta=5.0)
    state.add_sample(SocSample(60.0, at(7)))
    noise = state.add_sample(SocSample(59.0, at(17)))
    assert noise.status is TripStatus.NOISE
    assert state.anchor == SocSample(59.0, at(17))
    assert state.trips_recorded == 0


def test_restore_resumes_a_trip_across_a_restart():
    state = tracker()
    state.add_sample(SocSample(60.0, at(7)))
    state.restore(
        anchor=SocSample(60.0, at(7)),
        last_trip=None,
        last_valid_trip=None,
        total_kwh=0.0,
        trips_recorded=0,
    )
    trip = state.add_sample(SocSample(40.0, at(17)))
    assert trip.status is TripStatus.VALID
    assert trip.avg_power_w == pytest.approx(1200.0)
