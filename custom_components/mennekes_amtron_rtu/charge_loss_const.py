"""Optional external vehicle energy used for estimated charging losses."""

from typing import Final

CONF_VEHICLE_LAST_CHARGE_ENERGY: Final = "vehicle_last_charge_energy_entity"
CONF_CHARGE_LOSS_SOURCE: Final = "charge_loss_source"
CONF_LOSS_BAVARIAN_DEVICE: Final = "charge_loss_bavarian_device"
CONF_LOSS_AUTO_MATCH: Final = "charge_loss_auto_match"
LOSS_SOURCE_DISABLED: Final = "disabled"
LOSS_SOURCE_BAVARIANDATA: Final = "bavariandata"
LOSS_SOURCE_VEHICLE_HISTORY: Final = "vehicle_history"
LOSS_SOURCE_SENSOR: Final = "sensor"
LOSS_SOURCES: Final = (
    LOSS_SOURCE_DISABLED,
    LOSS_SOURCE_VEHICLE_HISTORY,
    LOSS_SOURCE_SENSOR,
)
LOSS_MATCH_WINDOW_SECONDS: Final = 30 * 60
LOSS_SENSOR_KEYS: Final = ("last_charging_loss", "average_charging_loss")
