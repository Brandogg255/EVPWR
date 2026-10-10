"""The power entity reads 0 W; the away average lives only in the imported rows.

Runs against a real Home Assistant import (the sensor and recorder modules are
needed), but with a stub hass: the recorder call is intercepted, so the rows the
engine would hand the recorder are asserted directly.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")

from evpwr.const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_MAX_TRIP_DAYS,
    CONF_MIN_DURATION_MINUTES,
    CONF_MIN_SOC_DELTA,
    CONF_SOC_ENTITY,
    CONF_USABLE_FACTOR,
)
from evpwr.engine import EvTripEngine
from evpwr.sensor import AwayAveragePowerSensor
from evpwr.trip import SocSample
from homeassistant.components.recorder import (
    statistics as recorder_statistics,
)
from homeassistant.helpers.recorder import DATA_INSTANCE

UTC = dt.timezone.utc
DEPARTURE = dt.datetime(2026, 3, 14, 21, 5, tzinfo=UTC)
ARRIVAL = dt.datetime(2026, 3, 15, 1, 5, tzinfo=UTC)
TRIP_BUCKETS = [
    dt.datetime(2026, 3, 14, 21, tzinfo=UTC),
    dt.datetime(2026, 3, 14, 22, tzinfo=UTC),
    dt.datetime(2026, 3, 14, 23, tzinfo=UTC),
    dt.datetime(2026, 3, 15, 0, tzinfo=UTC),
    dt.datetime(2026, 3, 15, 1, tzinfo=UTC),
]


class FakeHass:
    """Enough of HomeAssistant for a Store plus the recorder availability check."""

    def __init__(self) -> None:
        self.data: dict = {DATA_INSTANCE: object()}
        self.config = SimpleNamespace(path="/tmp", config_dir="/tmp")

    def async_create_task(self, coro, **kwargs):
        coro.close()


def _engine(hass: FakeHass) -> EvTripEngine:
    return EvTripEngine(
        hass,
        SimpleNamespace(
            entry_id="test",
            title="Test EV",
            data={
                CONF_SOC_ENTITY: "input_number.ev_soc",
                CONF_BATTERY_CAPACITY_KWH: 60.0,
                CONF_USABLE_FACTOR: 1.0,
                CONF_MIN_DURATION_MINUTES: 0,
                CONF_MIN_SOC_DELTA: 0.1,
                CONF_MAX_TRIP_DAYS: 400,
            },
        ),
    )


@pytest.fixture
def imports(monkeypatch) -> list[tuple[float, list[dt.datetime]]]:
    """Collect (mean, bucket starts) for every statistics import."""
    rows: list[tuple[float, list[dt.datetime]]] = []

    def fake_add(hass, metadata, statistics, **kwargs):
        rows.append((statistics[0]["mean"], [stat["start"] for stat in statistics]))

    monkeypatch.setattr(recorder_statistics, "async_add_external_statistics", fake_add)
    return rows


def test_trip_average_never_reaches_the_power_state(imports) -> None:
    engine = _engine(FakeHass())
    sensor = AwayAveragePowerSensor(engine)
    assert sensor.native_value == 0.0, "startup with no open trip reads 0 W"

    engine._async_process_sample(SocSample(80.0, DEPARTURE))
    assert sensor.native_value == 0.0, "an open trip must not put an average in the state"

    engine._async_process_sample(SocSample(66.0, ARRIVAL))
    assert sensor.native_value == 0.0, "the sample that closes the trip leaves 0 W"

    trip = engine.last_valid_trip
    assert trip is not None
    assert trip.energy_kwh == 8.4
    assert engine.trip_attributes(trip)["avg_power_w"] == 2100.0


def test_closing_a_trip_writes_trip_rows_then_zero_rows(imports) -> None:
    engine = _engine(FakeHass())
    engine._async_process_sample(SocSample(80.0, DEPARTURE))
    engine._async_process_sample(SocSample(66.0, ARRIVAL))

    trip_rows = [row for row in imports if row[0] != 0.0]
    zero_rows = [row for row in imports if row[0] == 0.0]
    assert trip_rows == [(2100.0, TRIP_BUCKETS)], "non-zero rows tile the trip window only"
    assert zero_rows, "the parked hours after arrival get 0 W rows"

    filled = [start for _, starts in zero_rows for start in starts]
    assert filled[0] == dt.datetime(2026, 3, 15, 2, tzinfo=UTC), "zeros start after the trip hour"
    assert set(filled).isdisjoint(TRIP_BUCKETS)
    assert filled[-1] >= dt.datetime.now(UTC).replace(minute=0, second=0, microsecond=0)


def test_zero_fill_extends_and_repeats_idempotently(imports) -> None:
    engine = _engine(FakeHass())
    engine._trip_windows = [(DEPARTURE, ARRIVAL)]
    engine._zero_until = ARRIVAL

    engine._async_fill_zero(dt.datetime(2026, 3, 15, 4, 30, tzinfo=UTC))
    assert imports == [
        (
            0.0,
            [
                dt.datetime(2026, 3, 15, 2, tzinfo=UTC),
                dt.datetime(2026, 3, 15, 3, tzinfo=UTC),
                dt.datetime(2026, 3, 15, 4, tzinfo=UTC),
            ],
        )
    ]

    engine._async_fill_zero(dt.datetime(2026, 3, 15, 4, 59, tzinfo=UTC))
    assert len(imports) == 1, "an already filled window is not written again"

    engine._async_fill_zero(dt.datetime(2026, 3, 15, 6, 10, tzinfo=UTC))
    assert imports[-1] == (
        0.0,
        [dt.datetime(2026, 3, 15, 5, tzinfo=UTC), dt.datetime(2026, 3, 15, 6, tzinfo=UTC)],
    )


def test_zero_fill_skips_hours_covered_by_a_later_trip(imports) -> None:
    engine = _engine(FakeHass())
    second = (
        dt.datetime(2026, 3, 15, 6, 10, tzinfo=UTC),
        dt.datetime(2026, 3, 15, 9, 40, tzinfo=UTC),
    )
    engine._trip_windows = [(DEPARTURE, ARRIVAL), second]
    engine._zero_until = ARRIVAL

    engine._async_fill_zero(dt.datetime(2026, 3, 15, 10, 30, tzinfo=UTC))
    assert imports[-1][1] == [
        dt.datetime(2026, 3, 15, 2, tzinfo=UTC),
        dt.datetime(2026, 3, 15, 3, tzinfo=UTC),
        dt.datetime(2026, 3, 15, 4, tzinfo=UTC),
        dt.datetime(2026, 3, 15, 5, tzinfo=UTC),
        dt.datetime(2026, 3, 15, 10, tzinfo=UTC),
    ]
