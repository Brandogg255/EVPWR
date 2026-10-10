"""Parked hours have to be written as 0 W buckets, trip hours must not.

Pure Python: ``zero_fill_bucket_starts`` is the whole rule, so the graph
behaviour is pinned without a running Home Assistant.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from trip import HOUR, hourly_bucket_starts, zero_fill_bucket_starts

TRIP = (
    datetime(2026, 3, 14, 21, 5, tzinfo=UTC),  # left home
    datetime(2026, 3, 15, 1, 48, tzinfo=UTC),  # arrived home
)


def test_zero_buckets_run_from_arrival_to_the_current_hour() -> None:
    now = datetime(2026, 3, 15, 4, 30, tzinfo=UTC)
    assert zero_fill_bucket_starts(TRIP[1], now, [TRIP]) == [
        datetime(2026, 3, 15, 2, tzinfo=UTC),
        datetime(2026, 3, 15, 3, tzinfo=UTC),
        datetime(2026, 3, 15, 4, tzinfo=UTC),
    ]


def test_zero_buckets_never_collide_with_the_trip_buckets() -> None:
    trip_buckets = hourly_bucket_starts(*TRIP)
    zero = zero_fill_bucket_starts(TRIP[1], datetime(2026, 3, 16, 0, 0, tzinfo=UTC), [TRIP])
    assert set(zero).isdisjoint(trip_buckets)
    # Every non-zero bucket overlaps the trip window it describes.
    assert all(start < TRIP[1] and start + HOUR > TRIP[0] for start in trip_buckets)


def test_zero_fill_extends_as_the_clock_advances() -> None:
    early = zero_fill_bucket_starts(TRIP[1], datetime(2026, 3, 15, 2, 30, tzinfo=UTC), [TRIP])
    later = zero_fill_bucket_starts(TRIP[1], datetime(2026, 3, 15, 5, 30, tzinfo=UTC), [TRIP])
    assert len(later) == len(early) + 3
    assert later[: len(early)] == early


def test_refilling_a_filled_window_repeats_the_same_buckets() -> None:
    now = datetime(2026, 3, 15, 5, 30, tzinfo=UTC)
    once = zero_fill_bucket_starts(TRIP[1], now, [TRIP])
    again = zero_fill_bucket_starts(TRIP[1], now, [TRIP])
    assert once == again
    assert len(once) == len(set(once))


def test_parked_hours_between_two_trips_are_filled() -> None:
    second = (
        datetime(2026, 3, 15, 6, 10, tzinfo=UTC),
        datetime(2026, 3, 15, 9, 40, tzinfo=UTC),
    )
    buckets = zero_fill_bucket_starts(TRIP[1], datetime(2026, 3, 15, 10, 30, tzinfo=UTC), [TRIP, second])
    assert buckets == [
        datetime(2026, 3, 15, 2, tzinfo=UTC),
        datetime(2026, 3, 15, 3, tzinfo=UTC),
        datetime(2026, 3, 15, 4, tzinfo=UTC),
        datetime(2026, 3, 15, 5, tzinfo=UTC),
        datetime(2026, 3, 15, 10, tzinfo=UTC),
    ]


def test_window_without_a_trip_fills_every_hour() -> None:
    start = datetime(2026, 3, 15, 10, 20, tzinfo=UTC)
    end = datetime(2026, 3, 15, 13, 10, tzinfo=UTC)
    assert zero_fill_bucket_starts(start, end) == [
        start.replace(minute=0),
        datetime(2026, 3, 15, 11, tzinfo=UTC),
        datetime(2026, 3, 15, 12, tzinfo=UTC),
        datetime(2026, 3, 15, 13, tzinfo=UTC),
    ]


def test_dst_crossing_window_stays_aligned_in_utc() -> None:
    # Sydney springs forward at 02:00 local on 2026-10-04: the trip spans the jump.
    tz = ZoneInfo("Australia/Sydney")
    start = datetime(2026, 10, 4, 1, 30, tzinfo=tz)  # 15:30Z, still AEST
    end = datetime(2026, 10, 4, 3, 30, tzinfo=tz)  # 16:30Z, now AEDT
    now = datetime(2026, 10, 4, 5, 30, tzinfo=tz)  # 18:30Z
    buckets = zero_fill_bucket_starts(end, now, [(start, end)])
    assert buckets == [
        datetime(2026, 10, 3, 17, tzinfo=UTC),
        datetime(2026, 10, 3, 18, tzinfo=UTC),
    ]
    assert all(b.tzinfo == UTC and (b - datetime(2026, 10, 3, tzinfo=UTC)) % HOUR == timedelta(0) for b in buckets)
