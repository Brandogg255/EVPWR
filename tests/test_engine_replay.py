"""Replaying history must not emit one state write or Store save per sample.

Regression guard for the 2026-10-09 live failure: `evpwr.backfill` replayed 30
days of SOC history and wrote a sensor state plus a `.storage` save for every
sample. The UI socket hit "Client unable to keep up with pending messages.
Reached 4096 pending messages" and was dropped, so the service response never
arrived and the Actions dialog reported "Failed to perform the action
evpwr.backfill. undefined".
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")

from evpwr.const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_MIN_DURATION_MINUTES,
    CONF_MIN_SOC_DELTA,
    CONF_SOC_ENTITY,
    CONF_USABLE_FACTOR,
)
from evpwr.engine import EvTripEngine
from evpwr.trip import SocSample

SAMPLES = 120
START = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)


class FakeHass:
    """Enough of HomeAssistant to build a Store and count persist tasks."""

    def __init__(self) -> None:
        self.data: dict = {}
        self.config = SimpleNamespace(path="/tmp", config_dir="/tmp")
        self.persist_tasks = 0

    def async_create_task(self, coro, **kwargs):
        self.persist_tasks += 1
        coro.close()  # never scheduled; the mute must stop the task being made


class FakeState:
    """Recorder history row: only these two fields are read."""

    def __init__(self, state: str, when: dt.datetime) -> None:
        self.state = state
        self.last_changed = when


def _engine() -> tuple[EvTripEngine, FakeHass, list[int]]:
    hass = FakeHass()
    entry = SimpleNamespace(
        entry_id="test",
        title="Test EV",
        data={
            CONF_SOC_ENTITY: "input_number.ev_soc",
            CONF_BATTERY_CAPACITY_KWH: 60.0,
            CONF_USABLE_FACTOR: 1.0,
            CONF_MIN_DURATION_MINUTES: 0,
            CONF_MIN_SOC_DELTA: 0.1,
        },
    )
    engine = EvTripEngine(hass, entry)
    notifications: list[int] = []
    engine.async_subscribe(lambda: notifications.append(1))
    return engine, hass, notifications


def _history() -> list[FakeState]:
    """120 samples, one a minute, 0.5 % apart: every one closes a valid trip."""
    return [
        FakeState(f"{80.0 - i * 0.5:.1f}", START + dt.timedelta(minutes=i))
        for i in range(SAMPLES)
    ]


def test_replay_writes_nothing_per_sample() -> None:
    engine, hass, notifications = _engine()

    engine._async_replay_states(_history())

    assert engine.trips_recorded == SAMPLES - 1, "the replay must actually run"
    assert engine.total_kwh == pytest.approx((SAMPLES - 1) * 0.3)
    assert notifications == [], "replay must not write a sensor state per sample"
    assert hass.persist_tasks == 0, "replay must not save storage per sample"
    assert engine._muted is False, "the mute must be released even on error"


def test_replay_releases_the_mute_when_a_sample_raises() -> None:
    engine, _, _ = _engine()

    class Boom:
        state = "80.0"

        @property
        def last_changed(self) -> dt.datetime:
            raise RuntimeError("broken history row")

    with pytest.raises(RuntimeError):
        engine._async_replay_states([Boom(), Boom()])

    assert engine._muted is False


def test_live_sample_still_notifies_and_persists() -> None:
    engine, hass, notifications = _engine()

    engine._async_process_sample(SocSample(80.0, START))
    engine._async_process_sample(SocSample(70.0, START + dt.timedelta(hours=3)))

    assert engine.trips_recorded == 1
    assert engine.last_trip is not None
    assert engine.last_trip.avg_power_w == pytest.approx(2000.0)
    assert len(notifications) == 1, "a live trip still updates the sensors"
    assert hass.persist_tasks == 2, "a live sample still persists"
