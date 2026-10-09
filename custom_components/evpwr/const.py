"""Constants for the EV Away Power integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "evpwr"
PLATFORMS = [Platform.SENSOR]

CONF_SOC_ENTITY = "soc_entity_id"
CONF_BATTERY_CAPACITY_KWH = "battery_capacity_kwh"
CONF_USABLE_FACTOR = "usable_factor"
CONF_MIN_DURATION_MINUTES = "min_duration_minutes"
CONF_MIN_SOC_DELTA = "min_soc_delta"
CONF_MAX_TRIP_DAYS = "max_trip_days"
CONF_ENABLE_TOTAL = "enable_total_energy"

DEFAULT_BATTERY_CAPACITY_KWH = 60.0
DEFAULT_USABLE_FACTOR = 1.0
DEFAULT_MIN_DURATION_MINUTES = 30
DEFAULT_MIN_SOC_DELTA = 1.0
DEFAULT_MAX_TRIP_DAYS = 30
DEFAULT_BACKFILL_DAYS = 30

SERVICE_BACKFILL = "backfill"

ATTR_STATISTIC_ID = "statistic_id"
ATTR_TRIP_START = "trip_start"
ATTR_TRIP_END = "trip_end"
ATTR_SOC_START = "soc_start"
ATTR_SOC_END = "soc_end"
ATTR_SOC_DELTA = "soc_delta"
ATTR_DURATION_MINUTES = "duration_minutes"
ATTR_DURATION = "duration"
ATTR_ENERGY_KWH = "energy_kwh"
ATTR_AVG_POWER_W = "avg_power_w"
ATTR_STATUS = "status"
ATTR_DETAIL = "detail"
ATTR_BATTERY_CAPACITY_KWH = "battery_capacity_kwh"
ATTR_USABLE_FACTOR = "usable_factor"
ATTR_ANCHOR_SOC = "anchor_soc"
ATTR_ANCHOR_TIME = "anchor_time"
