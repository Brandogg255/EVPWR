"""Trip engine: watches SOC updates, closes trips, backfills statistics."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfPower
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import storage
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.recorder import DATA_INSTANCE
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify
from homeassistant.util.hass_dict import HassKey
from homeassistant.util.unit_conversion import PowerConverter

from .const import (
    ATTR_ANCHOR_SOC,
    ATTR_ANCHOR_TIME,
    ATTR_AVG_POWER_W,
    ATTR_BATTERY_CAPACITY_KWH,
    ATTR_DETAIL,
    ATTR_DURATION,
    ATTR_DURATION_MINUTES,
    ATTR_ENERGY_KWH,
    ATTR_SOC_DELTA,
    ATTR_SOC_END,
    ATTR_SOC_START,
    ATTR_STATISTIC_ID,
    ATTR_STATUS,
    ATTR_TRIP_END,
    ATTR_TRIP_START,
    ATTR_USABLE_FACTOR,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_MAX_TRIP_DAYS,
    CONF_MIN_DURATION_MINUTES,
    CONF_MIN_SOC_DELTA,
    CONF_SOC_ENTITY,
    CONF_USABLE_FACTOR,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_MAX_TRIP_DAYS,
    DEFAULT_MIN_DURATION_MINUTES,
    DEFAULT_MIN_SOC_DELTA,
    DEFAULT_USABLE_FACTOR,
    DOMAIN,
)
from .trip import SocSample, TripResult, TripStatus, TripTracker, hourly_bucket_starts

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

DATA_KEY: HassKey[dict[str, EvTripEngine]] = HassKey(DOMAIN)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _parse(value: str | None) -> Any:
    return dt_util.parse_datetime(value) if value else None


def trip_to_dict(result: TripResult | None) -> dict[str, Any] | None:
    """Serialise a trip for the entity store."""
    if result is None:
        return None
    return {
        "status": result.status.value,
        "start": _iso(result.start),
        "end": _iso(result.end),
        "soc_start": result.soc_start,
        "soc_end": result.soc_end,
        "soc_delta": result.soc_delta,
        "duration_minutes": result.duration_minutes,
        "energy_kwh": result.energy_kwh,
        "avg_power_w": result.avg_power_w,
        "detail": result.detail,
    }


def trip_from_dict(data: dict[str, Any] | None) -> TripResult | None:
    """Rebuild a trip from the entity store."""
    if not data:
        return None
    try:
        status = TripStatus(data["status"])
    except (KeyError, ValueError):
        return None
    return TripResult(
        status=status,
        start=_parse(data.get("start")),
        end=_parse(data.get("end")),
        soc_start=data.get("soc_start"),
        soc_end=data.get("soc_end"),
        soc_delta=data.get("soc_delta"),
        duration_minutes=data.get("duration_minutes"),
        energy_kwh=data.get("energy_kwh"),
        avg_power_w=data.get("avg_power_w"),
        detail=data.get("detail", ""),
    )


def human_duration(minutes: float | None) -> str | None:
    """Render a duration the way a person would read it."""
    if minutes is None:
        return None
    total = round(minutes)
    hours, mins = divmod(total, 60)
    if hours and mins:
        return f"{hours} h {mins} min"
    if hours:
        return f"{hours} h"
    return f"{mins} min"


class EvTripEngine:
    """Turns consecutive SOC samples into away-period trips and statistics."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.soc_entity_id: str = entry.data[CONF_SOC_ENTITY]
        self.battery_capacity_kwh = float(
            entry.data.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH)
        )
        self.usable_factor = float(entry.data.get(CONF_USABLE_FACTOR, DEFAULT_USABLE_FACTOR))
        self.min_duration_minutes = float(
            entry.data.get(CONF_MIN_DURATION_MINUTES, DEFAULT_MIN_DURATION_MINUTES)
        )
        self.min_soc_delta = float(entry.data.get(CONF_MIN_SOC_DELTA, DEFAULT_MIN_SOC_DELTA))
        self.max_trip_days = float(entry.data.get(CONF_MAX_TRIP_DAYS, DEFAULT_MAX_TRIP_DAYS))
        self.statistic_id = f"{DOMAIN}:{slugify(entry.title)}_away_average_power"

        self.tracker = TripTracker(
            battery_capacity_kwh=self.battery_capacity_kwh,
            usable_factor=self.usable_factor,
            min_duration_minutes=self.min_duration_minutes,
            min_soc_delta=self.min_soc_delta,
            max_trip_days=self.max_trip_days,
        )

        self._unsub_state: Callable[[], None] | None = None
        self._listeners: list[Callable[[], None]] = []
        self._muted = False
        self._store = storage.Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")

    @property
    def anchor(self) -> SocSample | None:
        return self.tracker.anchor

    @property
    def last_trip(self) -> TripResult | None:
        return self.tracker.last_trip

    @property
    def last_valid_trip(self) -> TripResult | None:
        return self.tracker.last_valid_trip

    @property
    def total_kwh(self) -> float:
        return self.tracker.total_kwh

    @property
    def trips_recorded(self) -> int:
        return self.tracker.trips_recorded

    # ---------------------------------------------------------------- lifecycle

    async def async_setup(self) -> None:
        """Restore the mid-trip anchor, then start watching the SOC sensor."""
        payload = await self._store.async_load()
        if payload:
            anchor = payload.get("anchor")
            restored_anchor = (
                SocSample(anchor.get("soc"), _parse(anchor["at"]))
                if anchor and anchor.get("at")
                else None
            )
            self.tracker.restore(
                restored_anchor,
                trip_from_dict(payload.get("last_trip")),
                trip_from_dict(payload.get("last_valid_trip")),
                float(payload.get("total_kwh") or 0.0),
                int(payload.get("trips_recorded") or 0),
            )

        self._unsub_state = async_track_state_change_event(
            self.hass, [self.soc_entity_id], self._async_soc_changed
        )

    async def async_shutdown(self) -> None:
        if self._unsub_state is not None:
            self._unsub_state()
            self._unsub_state = None
        self._listeners.clear()

    def async_subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Subscribe a sensor to trip updates."""
        self._listeners.append(listener)

        def _unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _unsubscribe

    @callback
    def _async_notify(self) -> None:
        if self._muted:
            return
        for listener in list(self._listeners):
            listener()

    # ---------------------------------------------------------------- SOC feed

    @callback
    def _async_soc_changed(self, event: Event[EventStateChangedData]) -> None:
        new_state = event.data["new_state"]
        old_state = event.data["old_state"]
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            # Keep the anchor: an unavailable reading must not break a running trip.
            return
        if old_state is not None and old_state.state == new_state.state:
            return  # attribute-only change
        try:
            soc = float(new_state.state)
        except ValueError:
            return
        self._async_process_sample(SocSample(soc, new_state.last_changed))

    @callback
    def _async_process_sample(self, sample: SocSample) -> None:
        """Close a trip with every new SOC sample; the sample starts the next one."""
        result = self.tracker.add_sample(sample)
        if result is None:
            self._async_schedule_persist()
            return

        if result.is_valid:
            self._async_import_statistics(result)

        self._async_schedule_persist()
        if result.status is not TripStatus.TOO_SHORT:
            self._async_notify()

    # ------------------------------------------------------------- statistics

    @callback
    def _async_import_statistics(self, result: TripResult) -> None:
        """Write one hourly mean per hour of the away window.

        Home Assistant only accepts imported long-term statistics on whole-hour
        boundaries, so the flat line is drawn from hourly buckets. Without this
        import the recorder would only know the sensor's single state change at
        arrival, and every graph would show a step instead of the away period.
        """
        if DATA_INSTANCE not in self.hass.data:
            _LOGGER.warning(
                "Recorder is unavailable; skipped statistics backfill for %s", self.statistic_id
            )
            return

        from homeassistant.components.recorder.models import (  # noqa: PLC0415
            StatisticData,
            StatisticMeanType,
            StatisticMetaData,
        )
        from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
            async_add_external_statistics,
        )

        power = round(result.avg_power_w or 0.0, 1)
        buckets = hourly_bucket_starts(result.start, result.end)
        metadata: StatisticMetaData = {
            "mean_type": StatisticMeanType.ARITHMETIC,
            "has_sum": False,
            "name": f"{self.entry.title} away average power",
            "source": DOMAIN,
            "statistic_id": self.statistic_id,
            "unit_class": PowerConverter.UNIT_CLASS,
            "unit_of_measurement": UnitOfPower.WATT,
        }
        statistics: list[StatisticData] = [
            {"start": bucket, "mean": power, "min": power, "max": power} for bucket in buckets
        ]

        try:
            async_add_external_statistics(self.hass, metadata, statistics)
        except (HomeAssistantError, KeyError) as err:
            _LOGGER.warning("Could not import statistics for %s: %s", self.statistic_id, err)
            return

        _LOGGER.debug(
            "Imported %s hourly statistics for %s at %s W",
            len(statistics),
            self.statistic_id,
            power,
        )

    # ---------------------------------------------------------------- backfill

    async def async_backfill(self, days: int) -> int:
        """Replay recorded SOC history and re-import every trip in the window.

        The cumulative total is recomputed from the replayed window, so running
        this again does not double count.
        """
        from homeassistant.components.recorder import (  # noqa: PLC0415
            DATA_INSTANCE,
            get_instance,
        )
        from homeassistant.components.recorder.history import (  # noqa: PLC0415
            state_changes_during_period,
        )

        if DATA_INSTANCE not in self.hass.data:
            _LOGGER.warning("The recorder is unavailable, so SOC history cannot be replayed")
            return self.trips_recorded

        start = dt_util.utcnow() - timedelta(days=days)
        history = await get_instance(self.hass).async_add_executor_job(
            partial(
                state_changes_during_period,
                self.hass,
                start,
                None,
                self.soc_entity_id,
                True,
            )
        )
        states = history.get(self.soc_entity_id, [])

        self.tracker.restore(None, None, None, 0.0, 0)
        self._async_replay_states(states)
        await self._async_persist()
        self._async_notify()
        return self.trips_recorded

    @callback
    def _async_replay_states(self, states: list[Any]) -> None:
        """Get recorded SOC states through the tracker without per-sample output.

        A month of SOC history is thousands of samples. Writing a sensor state
        and a Store save for every one floods each websocket client ("Client
        unable to keep up with pending messages. Reached 4096 pending
        messages") and stalls the event loop, so the service response never
        reaches the UI. The caller persists and notifies once afterwards.
        """
        _LOGGER.debug("Replaying %s SOC updates for %s", len(states), self.entry.title)
        self._muted = True
        try:
            for state in states:
                if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                    continue
                try:
                    soc = float(state.state)
                except ValueError:
                    continue
                self._async_process_sample(SocSample(soc, state.last_changed))
        finally:
            self._muted = False

    # ------------------------------------------------------------- persistence

    @callback
    def _async_schedule_persist(self) -> None:
        if self._muted:
            return
        self.hass.async_create_task(self._async_persist(), eager_start=False)

    async def _async_persist(self) -> None:
        payload = {
            "anchor": (
                {"soc": self.anchor.soc, "at": _iso(self.anchor.at)} if self.anchor else None
            ),
            "last_trip": trip_to_dict(self.last_trip),
            "last_valid_trip": trip_to_dict(self.last_valid_trip),
            "total_kwh": self.total_kwh,
            "trips_recorded": self.trips_recorded,
        }
        try:
            await self._store.async_save(payload)
        except OSError:
            _LOGGER.exception("Could not persist EV trip state")

    # ------------------------------------------------------------------ output

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name=self.entry.title,
        )

    def trip_attributes(self, result: TripResult) -> dict[str, Any]:
        """Common attributes describing the trip behind a sensor value."""
        return {
            ATTR_TRIP_START: _iso(result.start),
            ATTR_TRIP_END: _iso(result.end),
            ATTR_SOC_START: result.soc_start,
            ATTR_SOC_END: result.soc_end,
            ATTR_SOC_DELTA: _round(result.soc_delta, 2),
            ATTR_DURATION_MINUTES: _round(result.duration_minutes, 1),
            ATTR_DURATION: human_duration(result.duration_minutes),
            ATTR_ENERGY_KWH: _round(result.energy_kwh, 3),
            ATTR_AVG_POWER_W: _round(result.avg_power_w, 1),
            ATTR_STATUS: result.status.value,
            ATTR_DETAIL: result.detail,
            ATTR_BATTERY_CAPACITY_KWH: self.battery_capacity_kwh,
            ATTR_USABLE_FACTOR: self.usable_factor,
            ATTR_STATISTIC_ID: self.statistic_id,
        }

    def engine_attributes(self) -> dict[str, Any]:
        """Attributes describing the open trip, for diagnostics."""
        return {
            ATTR_ANCHOR_SOC: self.anchor.soc if self.anchor else None,
            ATTR_ANCHOR_TIME: _iso(self.anchor.at) if self.anchor else None,
        }


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)
