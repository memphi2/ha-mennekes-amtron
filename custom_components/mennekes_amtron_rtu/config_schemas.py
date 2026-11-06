"""Schemas for the config and options flows."""

from __future__ import annotations

from typing import Any

import probatio
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    BooleanSelector,
    DeviceSelector,
    DeviceSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from ._flow_serial import PortOption
from .charge_loss_const import (
    CONF_CHARGE_LOSS_SOURCE,
    CONF_LOSS_AUTO_MATCH,
    CONF_LOSS_BAVARIAN_DEVICE,
    CONF_VEHICLE_LAST_CHARGE_ENERGY,
    LOSS_SOURCE_DISABLED,
    LOSS_SOURCES,
)
from .charging_cost_const import (
    CONF_ACTIVE_QUARTERS,
    CONF_AUTO_TARIFF_REFRESH,
    CONF_CHARGING_COST_SOURCE,
    CONF_CHARGING_NETWORK_MODULE,
    CONF_CHARGING_PRICE,
    CONF_CHARGING_PRICE_ENTITY,
    CONF_HIGH_NETWORK,
    CONF_HIGH_PERIODS,
    CONF_LOW_NETWORK,
    CONF_LOW_PERIODS,
    CONF_PRICE_INCLUDES_NETWORK,
    CONF_REGULAR_NETWORK,
    CONF_TARIFF_YEAR,
    COST_SOURCE_DISABLED,
    COST_SOURCE_FIXED,
    COST_SOURCES,
    NETWORK_MODULE_3,
    NETWORK_MODULE_NONE,
    NETWORK_MODULES,
)
from .device.const import (
    CONF_BAUDRATE,
    CONF_CONNECTION_TYPE,
    CONF_CONTROL_MODE,
    CONF_CURRENT_LIMIT,
    CONF_DEVICE_ID,
    CONF_FRAME,
    CONF_HOST,
    CONF_MEASUREMENT_INTERVAL_SECONDS,
    CONF_PHASE_SWITCH_GRACE_SECONDS,
    CONF_PORT,
    CONF_SCAN_INTERVAL_SECONDS,
    CONF_SEARCH,
    CONF_STATE_CHANGE_INTERVAL_SECONDS,
    CONF_TCP_PORT,
    CONNECTION_RTU_OVER_TCP,
    CONTROL_MODES,
    DEFAULT_BAUDRATE,
    DEFAULT_CONTROL_MODE,
    DEFAULT_DEVICE_ID,
    DEFAULT_FRAME,
    DEFAULT_MEASUREMENT_INTERVAL_SECONDS,
    DEFAULT_PHASE_SWITCH_GRACE_SECONDS,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DEFAULT_SEARCH,
    DEFAULT_STATE_CHANGE_INTERVAL_SECONDS,
    DEFAULT_TCP_PORT,
    DEVICE_ID_MAX,
    DEVICE_ID_MIN,
    MAX_PHASE_SWITCH_GRACE_SECONDS,
    MAX_SCAN_INTERVAL_SECONDS,
    MAX_STATE_CHANGE_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS,
    NETWORK_CONNECTION_TYPES,
    SEARCH_MODES,
    SUPPORTED_BAUDRATES,
    SUPPORTED_FRAMES,
)
from .vehicle_soc_const import (
    CONF_VEHICLE_CHARGE_LIMIT_ENTITY,
    CONF_VEHICLE_SOC_ENTITY,
    VEHICLE_CHARGE_LIMIT_DOMAINS,
    VEHICLE_SOC_DOMAINS,
)


def user_schema(ports: list[PortOption]) -> probatio.Schema:
    """Return the schema of the first config-flow step."""

    return probatio.Schema(
        {
            probatio.Required(CONF_PORT): _port_selector(ports),
            # A bare integer with a range renders as a slider, which is the
            # wrong control for a bus address somebody reads off a tool.
            probatio.Required(CONF_DEVICE_ID, default=DEFAULT_DEVICE_ID): probatio.All(
                NumberSelector(
                    NumberSelectorConfig(
                        min=DEVICE_ID_MIN,
                        max=DEVICE_ID_MAX,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                probatio.Coerce(int),
            ),
            probatio.Required(CONF_BAUDRATE, default=DEFAULT_BAUDRATE): probatio.In(
                SUPPORTED_BAUDRATES
            ),
            probatio.Required(CONF_FRAME, default=DEFAULT_FRAME): SelectSelector(
                SelectSelectorConfig(
                    options=list(SUPPORTED_FRAMES),
                    translation_key="frame",
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            probatio.Required(CONF_SEARCH, default=DEFAULT_SEARCH): SelectSelector(
                SelectSelectorConfig(
                    options=list(SEARCH_MODES),
                    translation_key="search",
                    mode=SelectSelectorMode.LIST,
                )
            ),
        }
    )


def network_schema() -> probatio.Schema:
    """Ask for the gateway endpoint and its explicit Modbus framing."""

    return probatio.Schema(
        {
            probatio.Required(
                CONF_CONNECTION_TYPE, default=CONNECTION_RTU_OVER_TCP
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(NETWORK_CONNECTION_TYPES),
                    translation_key="connection_type",
                    mode=SelectSelectorMode.LIST,
                )
            ),
            probatio.Required(CONF_HOST): cv.string,
            probatio.Required(CONF_TCP_PORT, default=DEFAULT_TCP_PORT): probatio.All(
                _network_integer,
                NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=65535, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
                probatio.Coerce(int),
            ),
            probatio.Required(CONF_DEVICE_ID, default=DEFAULT_DEVICE_ID): probatio.All(
                _network_integer,
                NumberSelector(
                    NumberSelectorConfig(
                        min=DEVICE_ID_MIN,
                        max=DEVICE_ID_MAX,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                probatio.Coerce(int),
            ),
        }
    )


def _network_integer(value: Any) -> int:
    """Accept whole UI numbers without truncating fractions or accepting bools."""

    if type(value) is int:
        return value
    if type(value) is float and value.is_integer():
        return int(value)
    raise probatio.Invalid("expected a whole number")


def options_schema(
    current: dict[str, Any],
    *,
    max_current: float,
    service_mode: bool = False,
) -> probatio.Schema:
    """Return the options schema, bounded by what the device allows."""

    fields: dict[Any, Any] = {
        probatio.Required(
            CONF_CONTROL_MODE,
            default=current.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE),
        ): SelectSelector(
            SelectSelectorConfig(
                options=list(CONTROL_MODES),
                translation_key="control_mode",
                mode=SelectSelectorMode.LIST,
            )
        ),
        probatio.Required(
            CONF_SCAN_INTERVAL_SECONDS,
            default=current.get(
                CONF_SCAN_INTERVAL_SECONDS, DEFAULT_SCAN_INTERVAL_SECONDS
            ),
        ): NumberSelector(
            NumberSelectorConfig(
                min=MIN_SCAN_INTERVAL_SECONDS,
                max=MAX_SCAN_INTERVAL_SECONDS,
                step=1,
                unit_of_measurement="s",
                mode=NumberSelectorMode.BOX,
            )
        ),
        probatio.Required(
            CONF_MEASUREMENT_INTERVAL_SECONDS,
            default=current.get(
                CONF_MEASUREMENT_INTERVAL_SECONDS,
                DEFAULT_MEASUREMENT_INTERVAL_SECONDS,
            ),
        ): NumberSelector(
            NumberSelectorConfig(
                min=MIN_SCAN_INTERVAL_SECONDS,
                max=MAX_SCAN_INTERVAL_SECONDS,
                step=1,
                unit_of_measurement="s",
                mode=NumberSelectorMode.BOX,
            )
        ),
        probatio.Required(
            CONF_CURRENT_LIMIT,
            default=current.get(CONF_CURRENT_LIMIT, max_current),
        ): NumberSelector(
            NumberSelectorConfig(
                min=6,
                max=max(max_current, 6),
                step=0.1,
                unit_of_measurement="A",
                mode=NumberSelectorMode.BOX,
            )
        ),
        probatio.Required(
            CONF_STATE_CHANGE_INTERVAL_SECONDS,
            default=current.get(
                CONF_STATE_CHANGE_INTERVAL_SECONDS,
                DEFAULT_STATE_CHANGE_INTERVAL_SECONDS,
            ),
        ): probatio.All(
            _network_integer,
            NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=MAX_STATE_CHANGE_INTERVAL_SECONDS,
                    step=1,
                    unit_of_measurement="s",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            probatio.Coerce(int),
        ),
        probatio.Required(
            CONF_PHASE_SWITCH_GRACE_SECONDS,
            default=current.get(
                CONF_PHASE_SWITCH_GRACE_SECONDS,
                DEFAULT_PHASE_SWITCH_GRACE_SECONDS,
            ),
        ): probatio.All(
            _network_integer,
            NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=MAX_PHASE_SWITCH_GRACE_SECONDS,
                    step=1,
                    unit_of_measurement="s",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            probatio.Coerce(int),
        ),
    }
    if service_mode:
        fields = {
            marker: selector
            for marker, selector in fields.items()
            if marker.schema not in (
                CONF_CONTROL_MODE,
                CONF_MEASUREMENT_INTERVAL_SECONDS,
                CONF_STATE_CHANGE_INTERVAL_SECONDS,
                CONF_PHASE_SWITCH_GRACE_SECONDS,
            )
        }
    # Suggested values remain removable. A schema default would silently put
    # a formerly selected vehicle back when the user clears the optional field.
    for key, domains in (
        (CONF_VEHICLE_SOC_ENTITY, VEHICLE_SOC_DOMAINS),
        (CONF_VEHICLE_CHARGE_LIMIT_ENTITY, VEHICLE_CHARGE_LIMIT_DOMAINS),
    ):
        fields[probatio.Optional(
            key, description={"suggested_value": current.get(key)},
        )] = EntitySelector(EntitySelectorConfig(filter={"domain": list(domains)}))
    fields[probatio.Required(
        CONF_CHARGE_LOSS_SOURCE,
        default=current.get(CONF_CHARGE_LOSS_SOURCE, LOSS_SOURCE_DISABLED),
    )] = SelectSelector(SelectSelectorConfig(
        options=list(LOSS_SOURCES),
        translation_key="charge_loss_source",
        mode=SelectSelectorMode.DROPDOWN,
    ))
    fields[probatio.Required(
        CONF_CHARGING_COST_SOURCE,
        default=current.get(CONF_CHARGING_COST_SOURCE, COST_SOURCE_DISABLED),
    )] = SelectSelector(SelectSelectorConfig(
        options=list(COST_SOURCES),
        translation_key="charging_cost_source",
        mode=SelectSelectorMode.DROPDOWN,
    ))
    return probatio.Schema(fields)


def loss_bavariandata_schema(current: dict[str, Any]) -> probatio.Schema:
    """Select a vehicle whose locally stored charging history can be matched."""
    return probatio.Schema({
        probatio.Required(
            CONF_LOSS_BAVARIAN_DEVICE,
            description={"suggested_value": current.get(CONF_LOSS_BAVARIAN_DEVICE)},
        ): DeviceSelector(DeviceSelectorConfig(integration="bavariandata")),
    })


def loss_sensor_schema(current: dict[str, Any]) -> probatio.Schema:
    """Select optional estimated-energy matching separately from basic options."""
    return probatio.Schema({
        probatio.Required(
            CONF_VEHICLE_LAST_CHARGE_ENERGY,
            description={"suggested_value": current.get(CONF_VEHICLE_LAST_CHARGE_ENERGY)},
        ): EntitySelector(EntitySelectorConfig(filter={"domain": "sensor"})),
        probatio.Optional(
            CONF_LOSS_AUTO_MATCH, default=current.get(CONF_LOSS_AUTO_MATCH, False),
        ): BooleanSelector(),
    })


def cost_source_schema(
    current: dict[str, Any], *, source: str, allow_network: bool = True,
) -> probatio.Schema:
    """Select a gross electricity price and optional network-tariff calculation."""
    fields: dict[Any, Any] = {}
    if source == COST_SOURCE_FIXED:
        fields[probatio.Required(
            CONF_CHARGING_PRICE,
            description={"suggested_value": current.get(CONF_CHARGING_PRICE)},
        )] = _price_selector(allow_negative=True)
    else:
        fields[probatio.Required(
            CONF_CHARGING_PRICE_ENTITY,
            description={"suggested_value": current.get(CONF_CHARGING_PRICE_ENTITY)},
        )] = EntitySelector(EntitySelectorConfig(filter={"domain": "sensor"}))
    if allow_network:
        fields[probatio.Required(
            CONF_CHARGING_NETWORK_MODULE,
            default=current.get(CONF_CHARGING_NETWORK_MODULE, NETWORK_MODULE_NONE),
        )] = SelectSelector(SelectSelectorConfig(
            options=list(NETWORK_MODULES),
            translation_key="charging_network_module",
            mode=SelectSelectorMode.DROPDOWN,
        ))
    return probatio.Schema(fields)


def cost_network_schema(current: dict[str, Any], *, module: str) -> probatio.Schema:
    """Ask only for the published network prices used by the chosen module."""
    fields: dict[Any, Any] = {
        probatio.Required(
            CONF_REGULAR_NETWORK,
            description={"suggested_value": current.get(CONF_REGULAR_NETWORK)},
        ): _price_selector(),
        probatio.Required(
            CONF_PRICE_INCLUDES_NETWORK,
            default=current.get(CONF_PRICE_INCLUDES_NETWORK, True),
        ): BooleanSelector(),
    }
    if module == NETWORK_MODULE_3:
        fields[probatio.Required(
            CONF_AUTO_TARIFF_REFRESH,
            default=current.get(CONF_AUTO_TARIFF_REFRESH, False),
        )] = BooleanSelector()
        for key in (CONF_HIGH_NETWORK, CONF_LOW_NETWORK):
            fields[probatio.Required(
                key, description={"suggested_value": current.get(key)},
            )] = _price_selector()
        for key in (CONF_HIGH_PERIODS, CONF_LOW_PERIODS):
            fields[probatio.Required(
                key, description={"suggested_value": current.get(key)},
            )] = TextSelector(TextSelectorConfig(multiline=True))
        fields[probatio.Required(
            CONF_ACTIVE_QUARTERS,
            description={"suggested_value": current.get(CONF_ACTIVE_QUARTERS)},
        )] = SelectSelector(SelectSelectorConfig(
            options=["1", "2", "3", "4"],
            translation_key="charging_tariff_quarter",
            multiple=True,
            mode=SelectSelectorMode.LIST,
        ))
        fields[probatio.Required(
            CONF_TARIFF_YEAR,
            description={"suggested_value": current.get(CONF_TARIFF_YEAR)},
        )] = probatio.All(_network_integer, NumberSelector(NumberSelectorConfig(
            min=2024, max=9998, step=1, mode=NumberSelectorMode.BOX,
        )), probatio.Coerce(int))
    return probatio.Schema(fields)


def _price_selector(*, allow_negative: bool = False) -> NumberSelector:
    return NumberSelector(NumberSelectorConfig(
        min=-1000 if allow_negative else 0,
        max=1000, step="any", unit_of_measurement="EUR/kWh", mode=NumberSelectorMode.BOX,
    ))


def _port_selector(ports: list[PortOption]) -> Any:
    if not ports:
        return cv.string
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(value=port.value, label=port.label) for port in ports
            ],
            custom_value=True,
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


def service_schema(*, credentials_only: bool = False) -> probatio.Schema:
    """Connection details for the independent service."""
    from homeassistant.helpers.selector import (
        TextSelector,
        TextSelectorConfig,
        TextSelectorType,
    )

    from .remote import CONF_SERVER_TOKEN, CONF_SERVER_URL

    fields = {
        probatio.Required(CONF_SERVER_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
    if not credentials_only:
        fields = {
            probatio.Required(CONF_SERVER_URL): TextSelector(
                TextSelectorConfig(type=TextSelectorType.URL)
            ),
            **fields,
        }
    return probatio.Schema(fields)
