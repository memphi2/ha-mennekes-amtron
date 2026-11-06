"""Home Assistant source settings for an optional vehicle charge target."""

from typing import Final

CONF_VEHICLE_SOC_ENTITY: Final = "vehicle_soc_entity"
CONF_VEHICLE_CHARGE_LIMIT_ENTITY: Final = "vehicle_charge_limit_entity"
VEHICLE_SOC_DOMAINS: Final = ("sensor",)
VEHICLE_CHARGE_LIMIT_DOMAINS: Final = ("sensor", "number", "input_number", "select")
