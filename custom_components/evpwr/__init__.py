"""The EV Away Power integration."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DEFAULT_BACKFILL_DAYS, DOMAIN, PLATFORMS, SERVICE_BACKFILL
from .engine import DATA_KEY, EvTripEngine

_LOGGER = logging.getLogger(__name__)

BACKFILL_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
        vol.Optional("days", default=DEFAULT_BACKFILL_DAYS): cv.positive_int,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the backfill service once per HA start."""
    hass.data.setdefault(DATA_KEY, {})

    async def async_backfill(call: ServiceCall) -> ServiceResponse:
        days = call.data["days"]
        entry_id = call.data.get("entry_id")
        if entry_id:
            engines = [
                engine
                for engine_id, engine in hass.data[DATA_KEY].items()
                if engine_id == entry_id
            ]
        else:
            engines = list(hass.data[DATA_KEY].values())

        if not engines:
            _LOGGER.warning("No EV Away Power instance is configured")
            return {"entries": []}

        results = []
        for engine in engines:
            trips = await engine.async_backfill(days)
            _LOGGER.info(
                "Backfilled %s away trip(s) for %s over the last %s days",
                trips,
                engine.entry.title,
                days,
            )
            results.append(
                {
                    "entry_id": engine.entry.entry_id,
                    "title": engine.entry.title,
                    "days": days,
                    "trips_recorded": trips,
                    "total_kwh": round(engine.total_kwh, 3),
                }
            )
        return {"entries": results}

    if not hass.services.has_service(DOMAIN, SERVICE_BACKFILL):
        hass.services.async_register(
            DOMAIN,
            SERVICE_BACKFILL,
            async_backfill,
            schema=BACKFILL_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Start tracking trips for one EV."""
    engine = EvTripEngine(hass, entry)
    await engine.async_setup()
    hass.data[DATA_KEY][entry.entry_id] = engine
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stop tracking trips for one EV."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    engine = hass.data[DATA_KEY].pop(entry.entry_id, None)
    if engine is not None:
        await engine.async_shutdown()
    return True
