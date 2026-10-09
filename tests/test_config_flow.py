"""The config flow has to produce a form HA can actually serve.

HA converts any voluptuous error raised while a flow form is being built into a
bare ``400 Bad Request`` (``homeassistant/helpers/http.py``), so a selector
config HA dislikes makes the integration impossible to set up.  These checks
need the real ``homeassistant`` package, so they skip when it is not installed.
"""

import pytest

pytest.importorskip("homeassistant")

from homeassistant.helpers import config_validation as cv
from probatio import to_field_list

from custom_components.evpwr.config_flow import EvPwrConfigFlow

FORM_FIELDS = 8


def _fields(defaults: dict | None = None) -> list[dict]:
    schema = EvPwrConfigFlow()._data_schema(defaults)
    return to_field_list(schema, custom_serializer=cv.custom_serializer)


def test_user_form_builds_and_serializes():
    fields = _fields()
    assert len(fields) == FORM_FIELDS
    assert {field["name"] for field in fields} == {
        "name",
        "soc_entity_id",
        "battery_capacity_kwh",
        "usable_factor",
        "min_duration_minutes",
        "min_soc_delta",
        "max_trip_days",
        "enable_total_energy",
    }


@pytest.mark.parametrize(
    "defaults",
    [
        None,
        {"soc_entity_id": "input_number.ev_manual_soc", "battery_capacity_kwh": 82.5},
    ],
)
def test_no_selector_option_is_null(defaults: dict | None):
    """HA's selector schemas reject a null option; the key has to be omitted."""
    for field in _fields(defaults):
        for name, options in (field.get("selector") or {}).items():
            nulls = [key for key, value in (options or {}).items() if value is None]
            assert not nulls, f"{field['name']}: null {nulls} in {name} selector"
