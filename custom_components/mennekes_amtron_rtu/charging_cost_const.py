"""Configuration keys for optional estimated charging costs."""
from typing import Final

CONF_CHARGING_COST_SOURCE: Final = "charging_cost_source"
CONF_CHARGING_PRICE: Final = "charging_price"
CONF_CHARGING_PRICE_ENTITY: Final = "charging_price_entity"
CONF_CHARGING_NETWORK_MODULE: Final = "charging_network_module"
CONF_CHARGING_NETWORK_TARIFF: Final = "charging_network_tariff"
COST_SOURCE_DISABLED: Final = "disabled"
COST_SOURCE_FIXED: Final = "fixed"
COST_SOURCE_SENSOR: Final = "sensor"
COST_SOURCES: Final = (COST_SOURCE_DISABLED, COST_SOURCE_FIXED, COST_SOURCE_SENSOR)
NETWORK_MODULE_NONE: Final = "none"
NETWORK_MODULE_2: Final = "module_2"
NETWORK_MODULE_3: Final = "module_3"
NETWORK_MODULES: Final = (NETWORK_MODULE_NONE, NETWORK_MODULE_2, NETWORK_MODULE_3)
CONF_REGULAR_NETWORK: Final = "regular_network"
CONF_PRICE_INCLUDES_NETWORK: Final = "price_includes_network"
CONF_HIGH_NETWORK: Final = "high_network"
CONF_LOW_NETWORK: Final = "low_network"
CONF_HIGH_PERIODS: Final = "high_periods"
CONF_LOW_PERIODS: Final = "low_periods"
CONF_ACTIVE_QUARTERS: Final = "active_quarters"
CONF_TARIFF_YEAR: Final = "tariff_year"
