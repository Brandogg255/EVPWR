"""Config flow for the EV Away Power integration."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_NAME
from homeassistant.helpers import selector

from .const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_ENABLE_TOTAL,
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

NAME_SCHEMA = selector.TextSelector()
SOC_ENTITY_SCHEMA = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=["sensor", "input_number", "number"])
)


def _number(
    min_value: float,
    max_value: float,
    step: float,
    unit: str | None = None,
) -> selector.NumberSelector:
    """Build a box number selector. HA rejects a null unit, so omit the key."""
    config = selector.NumberSelectorConfig(
        min=min_value,
        max=max_value,
        step=step,
        mode=selector.NumberSelectorMode.BOX,
    )
    if unit is not None:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


class EvPwrConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EV Away Power."""

    VERSION = 1

    def _data_schema(self, defaults: dict | None = None) -> vol.Schema:
        values = {
            CONF_NAME: "EV",
            CONF_SOC_ENTITY: None,
            CONF_BATTERY_CAPACITY_KWH: DEFAULT_BATTERY_CAPACITY_KWH,
            CONF_USABLE_FACTOR: DEFAULT_USABLE_FACTOR,
            CONF_MIN_DURATION_MINUTES: DEFAULT_MIN_DURATION_MINUTES,
            CONF_MIN_SOC_DELTA: DEFAULT_MIN_SOC_DELTA,
            CONF_MAX_TRIP_DAYS: DEFAULT_MAX_TRIP_DAYS,
            CONF_ENABLE_TOTAL: False,
        }
        if defaults:
            values.update({key: value for key, value in defaults.items() if value is not None})

        return vol.Schema(
            {
                vol.Required(CONF_NAME, default=values[CONF_NAME]): NAME_SCHEMA,
                vol.Required(CONF_SOC_ENTITY, default=values[CONF_SOC_ENTITY]): SOC_ENTITY_SCHEMA,
                vol.Required(
                    CONF_BATTERY_CAPACITY_KWH, default=values[CONF_BATTERY_CAPACITY_KWH]
                ): _number(1, 300, 0.1, "kWh"),
                vol.Required(CONF_USABLE_FACTOR, default=values[CONF_USABLE_FACTOR]): _number(
                    0.1, 1.0, 0.01
                ),
                vol.Required(
                    CONF_MIN_DURATION_MINUTES, default=values[CONF_MIN_DURATION_MINUTES]
                ): _number(0, 1440, 1, "min"),
                vol.Required(CONF_MIN_SOC_DELTA, default=values[CONF_MIN_SOC_DELTA]): _number(
                    0, 50, 0.1, "%"
                ),
                vol.Required(CONF_MAX_TRIP_DAYS, default=values[CONF_MAX_TRIP_DAYS]): _number(
                    1, 365, 1, "days"
                ),
                vol.Required(CONF_ENABLE_TOTAL, default=values[CONF_ENABLE_TOTAL]): selector.BooleanSelector(),
            }
        )

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Create the first config entry."""
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=self._data_schema())

        await self.async_set_unique_id(user_input[CONF_SOC_ENTITY].lower())
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

    async def async_step_reconfigure(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Change the SOC entity, battery size or thresholds later."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:  # pragma: no cover - defensive
            return self.async_abort(reason="reconfigure_failed")

        if user_input is None:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=self._data_schema({**entry.data, CONF_NAME: entry.title}),
            )

        self.async_update_entry(
            entry,
            data={**entry.data, **user_input},
            title=user_input[CONF_NAME],
            unique_id=user_input[CONF_SOC_ENTITY].lower(),
        )
        await self.hass.config_entries.async_reload(entry.entry_id)
        return self.async_abort(reason="reconfigure_successful")
