"""Trip computation for the EV Away Power integration.

This module is deliberately free of Home Assistant imports so the arithmetic can
be unit tested on its own.

Model
-----
The car's state of charge (SOC) is only reported meaningfully around departure
and arrival, so a *trip* is defined by two consecutive SOC samples:

    (t0, soc0) -> (t1, soc1)

    energy_kWh = (soc0 - soc1) / 100 * battery_capacity_kWh * usable_factor
    avg_power_W = energy_kWh * 1000 / duration_hours

The result is the average load over the *whole* away window (driving plus parked
time), not driving power.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum

HOUR = timedelta(hours=1)


class TripStatus(StrEnum):
    """Outcome of evaluating a pair of SOC samples."""

    VALID = "valid"
    CHARGING = "charging"
    TOO_SHORT = "too_short"
    TOO_LONG = "too_long"
    NOISE = "noise"
    INVALID_SOC = "invalid_soc"
    INVALID_DURATION = "invalid_duration"


@dataclass(frozen=True)
class SocSample:
    """One SOC reading."""

    soc: float | None
    at: datetime | None


@dataclass(frozen=True)
class TripResult:
    """A evaluated (possibly rejected) trip."""

    status: TripStatus
    start: datetime | None = None
    end: datetime | None = None
    soc_start: float | None = None
    soc_end: float | None = None
    soc_delta: float | None = None
    duration_minutes: float | None = None
    energy_kwh: float | None = None
    avg_power_w: float | None = None
    detail: str = ""

    @property
    def is_valid(self) -> bool:
        return self.status is TripStatus.VALID


def _valid_soc(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and 0.0 <= value <= 100.0


def evaluate_trip(
    previous: SocSample,
    current: SocSample,
    *,
    battery_capacity_kwh: float,
    usable_factor: float = 1.0,
    min_duration_minutes: float = 30.0,
    min_soc_delta: float = 1.0,
    max_trip_days: float = 30.0,
) -> TripResult:
    """Evaluate the trip implied by two consecutive SOC samples.

    Rejection precedence: missing/bogus SOC, non-positive duration, SOC increase
    (charging away from home), trip shorter than ``min_duration_minutes``, trip
    longer than ``max_trip_days``, then SOC change too small to be meaningful.
    """
    if not _valid_soc(previous.soc) or not _valid_soc(current.soc):
        return TripResult(
            TripStatus.INVALID_SOC,
            start=previous.at,
            end=current.at,
            soc_start=previous.soc,
            soc_end=current.soc,
            detail="SOC unavailable, non-numeric or outside 0..100",
        )

    if previous.at is None or current.at is None:
        return TripResult(
            TripStatus.INVALID_SOC,
            start=previous.at,
            end=current.at,
            soc_start=previous.soc,
            soc_end=current.soc,
            detail="SOC sample timestamp missing",
        )

    # Aware datetimes sharing one tzinfo object are subtracted as wall-clock
    # times by CPython, which silently ignores a DST change inside the window.
    # Normalise to UTC first so the duration is the real elapsed time.
    duration = current.at.astimezone(timezone.utc) - previous.at.astimezone(timezone.utc)
    duration_minutes = duration.total_seconds() / 60.0
    soc_start = float(previous.soc)
    soc_end = float(current.soc)
    soc_delta = soc_start - soc_end

    base = {
        "start": previous.at,
        "end": current.at,
        "soc_start": soc_start,
        "soc_end": soc_end,
        "soc_delta": soc_delta,
        "duration_minutes": duration_minutes,
    }

    if duration <= timedelta(0):
        return TripResult(
            TripStatus.INVALID_DURATION, detail="Trip duration is zero or negative", **base
        )

    energy_kwh = soc_delta / 100.0 * battery_capacity_kwh * usable_factor
    duration_hours = duration.total_seconds() / 3600.0
    avg_power_w = energy_kwh * 1000.0 / duration_hours

    if soc_delta < 0:
        return TripResult(
            TripStatus.CHARGING,
            energy_kwh=energy_kwh,
            avg_power_w=avg_power_w,
            detail="SOC increased: charged away from home, excluded from consumption",
            **base,
        )

    if duration_minutes < min_duration_minutes:
        return TripResult(
            TripStatus.TOO_SHORT,
            energy_kwh=energy_kwh,
            avg_power_w=avg_power_w,
            detail=f"Shorter than {min_duration_minutes:g} min: treated as a parked SOC refresh",
            **base,
        )

    if duration > timedelta(days=max_trip_days):
        return TripResult(
            TripStatus.TOO_LONG,
            energy_kwh=energy_kwh,
            avg_power_w=avg_power_w,
            detail=f"Longer than {max_trip_days:g} days: SOC is stale, not a trip",
            **base,
        )

    if soc_delta == 0:
        return TripResult(
            TripStatus.NOISE,
            energy_kwh=0.0,
            avg_power_w=0.0,
            detail="SOC unchanged: no measurable consumption",
            **base,
        )

    if soc_delta < min_soc_delta:
        return TripResult(
            TripStatus.NOISE,
            energy_kwh=energy_kwh,
            avg_power_w=avg_power_w,
            detail=f"SOC change below {min_soc_delta:g}%: below sensor resolution",
            **base,
        )

    return TripResult(
        TripStatus.VALID,
        energy_kwh=energy_kwh,
        avg_power_w=avg_power_w,
        detail="",
        **base,
    )


def hourly_bucket_starts(start: datetime, end: datetime) -> list[datetime]:
    """Hour bucket starts (UTC, top of hour) covering ``[start, end]``.

    Home Assistant only accepts imported long-term statistics aligned to the top
    of an hour, so the flat line is drawn with one bucket per hour. Buckets are
    aligned in UTC; a trip that starts or ends mid-hour therefore extends to the
    edges of its first and last bucket.
    """
    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    first = start_utc.replace(minute=0, second=0, microsecond=0)

    buckets: list[datetime] = []
    cursor = first
    while cursor < end_utc:
        buckets.append(cursor)
        cursor += HOUR
    if not buckets:
        buckets.append(first)
    return buckets


def zero_fill_bucket_starts(
    window_start: datetime,
    window_end: datetime,
    busy: Iterable[tuple[datetime, datetime]] = (),
) -> list[datetime]:
    """Hour buckets in ``[window_start, window_end]`` that no trip window covers.

    Parked stretches need their own 0 W rows: without them the last trip's value
    appears to run on forever after the car is home. Buckets that overlap a trip
    window are left to that trip's value, so the two never write the same row.
    """
    start_utc = window_start.astimezone(timezone.utc)
    end_utc = window_end.astimezone(timezone.utc)
    last = end_utc.replace(minute=0, second=0, microsecond=0)
    busy_utc = [(s.astimezone(timezone.utc), e.astimezone(timezone.utc)) for s, e in busy]

    buckets: list[datetime] = []
    cursor = start_utc.replace(minute=0, second=0, microsecond=0)
    while cursor <= last:
        if not any(
            cursor < trip_end and trip_start < cursor + HOUR
            for trip_start, trip_end in busy_utc
        ):
            buckets.append(cursor)
        cursor += HOUR
    return buckets


class TripTracker:
    """Chains SOC samples into trips, keeping the open (anchor) sample.

    The anchor is the SOC reading that starts the current away period. Every
    new reading closes it and becomes the anchor of the next period, which is
    what makes a presence sensor unnecessary: the car's own SOC reports define
    the boundaries.
    """

    def __init__(
        self,
        *,
        battery_capacity_kwh: float,
        usable_factor: float = 1.0,
        min_duration_minutes: float = 30.0,
        min_soc_delta: float = 1.0,
        max_trip_days: float = 30.0,
    ) -> None:
        self._options = {
            "battery_capacity_kwh": battery_capacity_kwh,
            "usable_factor": usable_factor,
            "min_duration_minutes": min_duration_minutes,
            "min_soc_delta": min_soc_delta,
            "max_trip_days": max_trip_days,
        }
        self.anchor: SocSample | None = None
        self.last_trip: TripResult | None = None
        self.last_valid_trip: TripResult | None = None
        self.total_kwh: float = 0.0
        self.trips_recorded: int = 0

    def add_sample(self, sample: SocSample) -> TripResult | None:
        """Feed one SOC reading; return the trip it closed, if any."""
        if self.anchor is None:
            self.anchor = sample
            return None

        result = evaluate_trip(self.anchor, sample, **self._options)

        if result.status is TripStatus.TOO_SHORT:
            # Parked SOC refresh: fold the drift into the open trip, keep its start.
            self.anchor = SocSample(sample.soc, self.anchor.at)
            return result

        self.last_trip = result
        self.anchor = sample

        if result.is_valid:
            self.last_valid_trip = result
            self.trips_recorded += 1
            self.total_kwh += result.energy_kwh or 0.0
        return result

    def restore(
        self,
        anchor: SocSample | None,
        last_trip: TripResult | None,
        last_valid_trip: TripResult | None,
        total_kwh: float,
        trips_recorded: int,
    ) -> None:
        """Re-adopt state saved before a Home Assistant restart mid-trip."""
        self.anchor = anchor
        self.last_trip = last_trip
        self.last_valid_trip = last_valid_trip
        self.total_kwh = total_kwh
        self.trips_recorded = trips_recorded
