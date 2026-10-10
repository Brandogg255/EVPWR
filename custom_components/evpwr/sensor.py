"""Sensors exposing the EV's average load over each away period."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_ENABLE_TOTAL
from .engine import DATA_KEY, EvTripEngine
from .trip import TripResult


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the EV trip sensors from a config entry."""
    engine: EvTripEngine = hass.data[DATA_KEY][entry.entry_id]

    entities: list[SensorEntity] = [
        AwayAveragePowerSensor(engine),
        TripEnergySensor(engine),
    ]
    if entry.data.get(CONF_ENABLE_TOTAL, False):
        entities.append(AwayEnergyTotalSensor(engine))

    async_add_entities(entities)


class EvTripSensor(SensorEntity):
    """Shared plumbing: one device, one trip source, push updates."""

    _attr_has_entity_name = True
    _key: str

    def __init__(self, engine: EvTripEngine) -> None:
        self._engine = engine
        self._attr_unique_id = f"{engine.entry.entry_id}-{self._key}"
        self._attr_device_info = engine.device_info
        self._unsub: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        self._unsub = self._engine.async_subscribe(self._async_trip_updated)
        self.async_on_remove(self._unsub)

    @callback
    def _async_trip_updated(self) -> None:
        self.async_write_ha_state()

    def _trip(self) -> TripResult | None:
        return self._engine.last_valid_trip

    @property
    def extra_state_attributes(self) -> dict:
        attrs: dict = {}
        trip = self._trip()
        if trip is not None:
            attrs.update(self._engine.trip_attributes(trip))
        attrs.update(self._engine.engine_attributes())
        if self._engine.last_trip is not None:
            attrs["last_event_status"] = self._engine.last_trip.status.value
            attrs["last_event_detail"] = self._engine.last_trip.detail
        return attrs


class AwayAveragePowerSensor(EvTripSensor):
    """The 0 W baseline the away-average statistics are attached to."""

    _attr_name = "Away average power"
    _key = "away_average_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    @property
    def native_value(self) -> float:
        """Always 0 W: the car draws nothing once it is home.

        The away average is a property of a past trip, not of the present
        moment, so it lives in the imported hourly statistics rows and in the
        ``avg_power_w`` attribute. Rendering it here is what left the entity
        showing the previous night's 1120 W hours after the car was parked.
        """
        return 0.0


class TripEnergySensor(EvTripSensor):
    """Energy the pack lost during the most recent away period."""

    _attr_name = "Trip energy"
    _key = "trip_energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    # HA rejects MEASUREMENT on an ENERGY device class; this value resets with every
    # trip and is never offered to the Energy dashboard.
    _attr_state_class = None
    _attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float | None:
        trip = self._trip()
        if trip is None or trip.energy_kwh is None:
            return None
        return round(trip.energy_kwh, 3)


class AwayEnergyTotalSensor(EvTripSensor):
    """Cumulative away energy; the only sensor the Energy dashboard accepts."""

    _attr_name = "Away energy total"
    _key = "away_energy_total"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2

    def _trip(self) -> TripResult | None:
        return None

    @property
    def native_value(self) -> float:
        return round(self._engine.total_kwh, 3)

    @property
    def extra_state_attributes(self) -> dict:
        attrs = self._engine.engine_attributes()
        attrs["trips_recorded"] = self._engine.trips_recorded
        return attrs
