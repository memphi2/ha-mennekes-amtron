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
    TextSelectorType,
)

from ._flow_serial import PortOption
from .charge_loss_const import (
    CONF_LOSS_AUTO_MATCH,
    CONF_VEHICLE_LAST_CHARGE_ENERGY,
)
from .charging_cost_const import (
    CONF_ACTIVE_QUARTERS,
    CONF_AUTO_TARIFF_REFRESH,
    CONF_CHARGING_COST_SOURCE,
    CONF_CHARGING_NETWORK_MODULE,
    CONF_CHARGING_NETWORK_TARIFF,
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
    COST_SOURCE_SENSOR,
    NETWORK_MODULE_3,
    NETWORK_MODULE_NONE,
    NETWORK_MODULES,
)
from .device.const import (
    CONF_BAUDRATE,
    CONF_CONFIGURE_GATEWAY,
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
    CONF_TCP_AUTH_PASSWORD,
    CONF_TCP_AUTHENTICATION,
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
from .timed_goal_const import (
    CONF_TIMED_CALENDAR_ENTITY,
    CONF_TIMED_CALENDAR_TITLE,
    CONF_TIMED_CAPACITY_ENTITY,
    CONF_TIMED_CAPACITY_KWH,
    CONF_TIMED_DEPARTURE_ENTITY,
    CONF_TIMED_TIMER1_DAYS,
    CONF_TIMED_TIMER2_DAYS,
    CONF_TIMED_VEHICLE_TIME_ZONE,
    TIMED_WEEKDAYS,
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
            probatio.Required(
                CONF_DEVICE_ID, default=DEFAULT_DEVICE_ID
            ): _IntegerSelector(
                NumberSelectorConfig(
                    min=DEVICE_ID_MIN,
                    max=DEVICE_ID_MAX,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                )
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
            probatio.Required(
                CONF_TCP_PORT, default=DEFAULT_TCP_PORT
            ): _IntegerSelector(
                NumberSelectorConfig(
                    min=1, max=65535, step=1, mode=NumberSelectorMode.BOX
                )
            ),
            probatio.Required(
                CONF_DEVICE_ID, default=DEFAULT_DEVICE_ID
            ): _IntegerSelector(
                NumberSelectorConfig(
                    min=DEVICE_ID_MIN,
                    max=DEVICE_ID_MAX,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                )
            ),
            probatio.Optional(CONF_TCP_AUTHENTICATION, default=False): BooleanSelector(),
            probatio.Optional(CONF_CONFIGURE_GATEWAY, default=False): BooleanSelector(),
        }
    )


def network_auth_schema() -> probatio.Schema:
    """Ask for an existing adapter password without publishing it as a default."""
    return probatio.Schema({
        probatio.Required(CONF_TCP_AUTH_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
    })


def network_adapter_schema(baudrates: tuple[int, ...]) -> probatio.Schema:
    """Configure only supported adapter settings, keeping passwords optional."""
    return probatio.Schema({
        probatio.Required(CONF_BAUDRATE): probatio.In(baudrates),
        probatio.Required(CONF_FRAME): SelectSelector(SelectSelectorConfig(
            options=list(SUPPORTED_FRAMES), translation_key="frame", mode=SelectSelectorMode.DROPDOWN,
        )),
        probatio.Required(CONF_TCP_AUTHENTICATION): BooleanSelector(),
        probatio.Optional(CONF_TCP_AUTH_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
    })


class _IntegerSelector(NumberSelector):
    """Keep integer validation inside a selector HA can serialize for its UI.

    An arbitrary validator in ``All`` cannot be serialized by the flow API.
    The number selector's ``step`` only controls the UI, so validate whole
    numbers here as well instead of silently truncating submitted fractions.
    """

    def __call__(self, data: Any) -> int:
        if type(data) is not int and not (type(data) is float and data.is_integer()):
            raise probatio.Invalid("expected a whole number")
        return int(super().__call__(data))


def options_schema(
    current: dict[str, Any],
    *,
    max_current: float,
    service_mode: bool = False,
) -> probatio.Schema:
    """Basic wallbox settings; optional features have their own sections."""
    fields: dict[Any, Any] = {
        probatio.Required(
            CONF_SCAN_INTERVAL_SECONDS,
            default=current.get(
                CONF_SCAN_INTERVAL_SECONDS,
                DEFAULT_SCAN_INTERVAL_SECONDS,
            ),
        ): _seconds_selector(MIN_SCAN_INTERVAL_SECONDS, MAX_SCAN_INTERVAL_SECONDS),
        probatio.Required(
            CONF_CURRENT_LIMIT, default=current.get(CONF_CURRENT_LIMIT, max_current)
        ): NumberSelector(
            NumberSelectorConfig(
                min=6,
                max=max(max_current, 6),
                step=0.1,
                unit_of_measurement="A",
                mode=NumberSelectorMode.BOX,
            )
        ),
    }
    if not service_mode:
        fields[
            probatio.Required(
                CONF_CONTROL_MODE,
                default=current.get(
                    CONF_CONTROL_MODE,
                    DEFAULT_CONTROL_MODE,
                ),
            )
        ] = SelectSelector(
            SelectSelectorConfig(
                options=list(CONTROL_MODES),
                translation_key="control_mode",
                mode=SelectSelectorMode.LIST,
            )
        )
    return probatio.Schema(fields)


def advanced_schema(current: dict[str, Any], *, phase_switch: bool) -> probatio.Schema:
    fields: dict[Any, Any] = {
        probatio.Required(
            CONF_MEASUREMENT_INTERVAL_SECONDS,
            default=current.get(
                CONF_MEASUREMENT_INTERVAL_SECONDS,
                DEFAULT_MEASUREMENT_INTERVAL_SECONDS,
            ),
        ): _seconds_selector(MIN_SCAN_INTERVAL_SECONDS, MAX_SCAN_INTERVAL_SECONDS),
        probatio.Required(
            CONF_STATE_CHANGE_INTERVAL_SECONDS,
            default=current.get(
                CONF_STATE_CHANGE_INTERVAL_SECONDS,
                DEFAULT_STATE_CHANGE_INTERVAL_SECONDS,
            ),
        ): _seconds_selector(0, MAX_STATE_CHANGE_INTERVAL_SECONDS),
    }
    if phase_switch:
        fields[
            probatio.Required(
                CONF_PHASE_SWITCH_GRACE_SECONDS,
                default=current.get(
                    CONF_PHASE_SWITCH_GRACE_SECONDS,
                    DEFAULT_PHASE_SWITCH_GRACE_SECONDS,
                ),
            )
        ] = _seconds_selector(0, MAX_PHASE_SWITCH_GRACE_SECONDS)
    return probatio.Schema(fields)


def _seconds_selector(minimum: int, maximum: int) -> _IntegerSelector:
    return _IntegerSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=1,
            unit_of_measurement="s",
            mode=NumberSelectorMode.BOX,
        )
    )


def _entity_selector(domains: tuple[str, ...]) -> EntitySelector:
    return EntitySelector(EntitySelectorConfig(filter={"domain": list(domains)}))


def vehicle_soc_schema(current: dict[str, Any]) -> probatio.Schema:
    return probatio.Schema(
        {
            probatio.Required(
                CONF_VEHICLE_SOC_ENTITY,
                description={
                    "suggested_value": current.get(CONF_VEHICLE_SOC_ENTITY),
                },
            ): _entity_selector(VEHICLE_SOC_DOMAINS),
            probatio.Required(
                "use_vehicle_limit",
                default=current.get(
                    "use_vehicle_limit",
                    bool(current.get(CONF_VEHICLE_CHARGE_LIMIT_ENTITY)),
                ),
            ): BooleanSelector(),
        }
    )


def vehicle_limit_schema(current: dict[str, Any]) -> probatio.Schema:
    return probatio.Schema(
        {
            probatio.Required(
                CONF_VEHICLE_CHARGE_LIMIT_ENTITY,
                description={
                    "suggested_value": current.get(CONF_VEHICLE_CHARGE_LIMIT_ENTITY),
                },
            ): _entity_selector(VEHICLE_CHARGE_LIMIT_DOMAINS),
        }
    )


def departure_configure_schema(
    current: dict[str, Any], *, vehicle_timer: bool, profile_capacity: bool = False,
) -> probatio.Schema:
    profile = current.get("vehicle_provider") not in (None, "none")
    capacity_sources = ["fixed", "entity", "profile"] if profile_capacity else ["fixed", "entity"]
    default_source = "profile" if profile_capacity and current.get("timed_use_vehicle_capacity") is True else "entity" if current.get(CONF_TIMED_CAPACITY_ENTITY) else "fixed"
    selected_source = current.get("capacity_source", default_source)
    if selected_source not in capacity_sources:
        selected_source = default_source
    fields = {
            probatio.Required(
                "capacity_source",
                default=selected_source,
            ): SelectSelector(
                SelectSelectorConfig(
                    options=capacity_sources,
                    translation_key="capacity_source",
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            probatio.Required(
                "use_calendar",
                default=current.get(
                    "use_calendar", bool(current.get(CONF_TIMED_CALENDAR_ENTITY))
                ),
            ): BooleanSelector(),
            probatio.Required(
                "use_departure_entity",
                default=current.get(
                    "use_departure_entity",
                    bool(current.get(CONF_TIMED_DEPARTURE_ENTITY)),
                ),
            ): BooleanSelector(),
    }
    if profile or "vehicle_profile_id" not in current or vehicle_timer:
        fields[
            probatio.Required(
                "use_vehicle_timer",
                default=current.get("use_vehicle_timer", vehicle_timer),
            )
        ] = BooleanSelector()
    return probatio.Schema(fields)


def departure_capacity_schema(
    current: dict[str, Any], *, entity: bool
) -> probatio.Schema:
    key = CONF_TIMED_CAPACITY_ENTITY if entity else CONF_TIMED_CAPACITY_KWH
    return probatio.Schema(
        {
            probatio.Required(
                key, description={"suggested_value": current.get(key)}
            ): _entity_selector(("sensor",))
            if entity
            else NumberSelector(
                NumberSelectorConfig(
                    min=1,
                    max=250,
                    step=0.1,
                    unit_of_measurement="kWh",
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
    )


def departure_calendar_schema(current: dict[str, Any]) -> probatio.Schema:
    return probatio.Schema(
        {
            probatio.Required(
                CONF_TIMED_CALENDAR_ENTITY,
                description={
                    "suggested_value": current.get(CONF_TIMED_CALENDAR_ENTITY),
                },
            ): _entity_selector(("calendar",)),
            probatio.Required(
                CONF_TIMED_CALENDAR_TITLE,
                description={
                    "suggested_value": current.get(CONF_TIMED_CALENDAR_TITLE),
                },
            ): TextSelector(),
        }
    )


def departure_entity_schema(current: dict[str, Any]) -> probatio.Schema:
    return probatio.Schema(
        {
            probatio.Required(
                CONF_TIMED_DEPARTURE_ENTITY,
                description={
                    "suggested_value": current.get(CONF_TIMED_DEPARTURE_ENTITY),
                },
            ): _entity_selector(("sensor", "input_datetime")),
        }
    )


def vehicle_provider_schema(
    key: str, provider: str, providers: list[str]
) -> probatio.Schema:
    return probatio.Schema(
        {
            probatio.Required(
                key,
                description={
                    "suggested_value": provider if provider in providers else None
                },
            ): SelectSelector(
                SelectSelectorConfig(
                    options=providers,
                    translation_key="vehicle_provider",
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


def vehicle_profile_details_schema(
    current: dict[str, Any], *, providers: list[str] | None = None,
) -> probatio.Schema:
    """Name a profile and choose its optional manufacturer integration."""
    providers = ["none", "bavariandata"] if providers is None else providers
    return probatio.Schema({
        probatio.Required("vehicle_profile_name", description={
            "suggested_value": current.get("vehicle_profile_name"),
        }): TextSelector(),
        probatio.Required("vehicle_provider", default=(
            current.get("vehicle_provider") if current.get("vehicle_provider") in providers else "none"
        )):
            SelectSelector(SelectSelectorConfig(
                options=providers, translation_key="vehicle_provider",
                mode=SelectSelectorMode.DROPDOWN,
            )),
    })


def vehicle_profile_choice_schema(
    choices: list[SelectOptionDict], current: str | None, *, remove: bool = False,
) -> probatio.Schema:
    fields: dict[Any, Any] = {
        probatio.Required("vehicle_profile_id", description={"suggested_value": current}):
            SelectSelector(SelectSelectorConfig(options=choices, mode=SelectSelectorMode.DROPDOWN)),
    }
    if remove:
        fields[probatio.Required("confirm", default=False)] = BooleanSelector()
    return probatio.Schema(fields)


def vehicle_detection_mode_schema(current: dict[str, Any]) -> probatio.Schema:
    return probatio.Schema({
        probatio.Required("vehicle_auto_selection", default=current.get("vehicle_auto_selection") is True):
            BooleanSelector(),
    })


def vehicle_plug_charge_schema(
    current: dict[str, Any], *, permission: bool,
) -> probatio.Schema:
    """Edit only the selected vehicle's permission; activation is an entity."""
    fields: dict[Any, Any] = {}
    if permission:
        fields[probatio.Required(
            "vehicle_plug_charge", default=current.get("vehicle_plug_charge") is True,
        )] = BooleanSelector()
    return probatio.Schema(fields)


def vehicle_detection_source_schema(current: str, *, profile: bool) -> probatio.Schema:
    choices = ["disabled", "entities"]
    if profile:
        choices.insert(1, "profile")
    return probatio.Schema({
        probatio.Required("vehicle_detection_source", default=current if current in choices else "disabled"):
            SelectSelector(SelectSelectorConfig(
                options=choices, translation_key="vehicle_detection_source", mode=SelectSelectorMode.DROPDOWN,
            )),
    })


def vehicle_detection_entities_schema(current: dict[str, Any]) -> probatio.Schema:
    return probatio.Schema({
        probatio.Required("vehicle_connected_entity", description={
            "suggested_value": current.get("vehicle_connected_entity"),
        }): EntitySelector(EntitySelectorConfig(domain=["binary_sensor", "sensor"])),
        probatio.Required("vehicle_home_entity", description={
            "suggested_value": current.get("vehicle_home_entity"),
        }): EntitySelector(EntitySelectorConfig(domain=["device_tracker", "person", "binary_sensor"])),
    })


def vehicle_detection_connected_states_schema(
    choices: list[str], current: list[str],
) -> probatio.Schema:
    return probatio.Schema({
        probatio.Required("vehicle_connected_states", description={
            "suggested_value": current,
        }): SelectSelector(SelectSelectorConfig(
            options=choices, multiple=True, custom_value=True,
            mode=SelectSelectorMode.DROPDOWN,
        )),
    })


def vehicle_source_schema(current: dict[str, Any], *, profile: bool = True) -> probatio.Schema:
    return probatio.Schema({
        probatio.Required("vehicle_soc_source", default=(
            "profile" if profile and current.get("vehicle_soc_use_profile") is True else "entity"
        )): SelectSelector(SelectSelectorConfig(
            options=["profile", "entity"] if profile else ["entity"], translation_key="vehicle_soc_source",
            mode=SelectSelectorMode.DROPDOWN,
        )),
    })


def vehicle_profile_soc_schema(current: dict[str, Any]) -> probatio.Schema:
    return probatio.Schema({
        probatio.Required("use_vehicle_limit", default=bool(
            current.get("vehicle_limit_use_profile") or current.get(CONF_VEHICLE_CHARGE_LIMIT_ENTITY)
        )): BooleanSelector(),
    })


def vehicle_device_schema(
    current: dict[str, Any],
    *,
    key: str,
    integration: str,
    timer_options: bool = False,
    include_device: bool = True,
) -> probatio.Schema:
    fields: dict[Any, Any] = {
        probatio.Required(
            key, description={"suggested_value": current.get(key)}
        ): DeviceSelector(DeviceSelectorConfig(integration=integration)),
    } if include_device else {}
    if timer_options:
        fields[
            probatio.Optional(
                CONF_TIMED_VEHICLE_TIME_ZONE,
                description={
                    "suggested_value": current.get(CONF_TIMED_VEHICLE_TIME_ZONE),
                },
            )
        ] = TextSelector()
        for option in (CONF_TIMED_TIMER1_DAYS, CONF_TIMED_TIMER2_DAYS):
            fields[
                probatio.Optional(
                    option, description={"suggested_value": current.get(option)}
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=list(TIMED_WEEKDAYS),
                    multiple=True,
                    translation_key="timed_weekdays",
                    mode=SelectSelectorMode.DROPDOWN,
                ),
            )
    return probatio.Schema(fields)


def loss_sensor_schema(current: dict[str, Any]) -> probatio.Schema:
    return probatio.Schema(
        {
            probatio.Required(
                CONF_VEHICLE_LAST_CHARGE_ENERGY,
                description={
                    "suggested_value": current.get(CONF_VEHICLE_LAST_CHARGE_ENERGY),
                },
            ): _entity_selector(("sensor",)),
            probatio.Required(
                CONF_LOSS_AUTO_MATCH, default=current.get(CONF_LOSS_AUTO_MATCH, False)
            ): BooleanSelector(),
        }
    )


def cost_kind_schema(current: dict[str, Any]) -> probatio.Schema:
    """Select the price source before showing its own inputs."""
    return probatio.Schema({
        probatio.Required(
            CONF_CHARGING_COST_SOURCE,
            default=current.get(CONF_CHARGING_COST_SOURCE, COST_SOURCE_DISABLED),
        ): SelectSelector(SelectSelectorConfig(
            options=[COST_SOURCE_FIXED, COST_SOURCE_SENSOR, COST_SOURCE_DISABLED],
            translation_key="charging_cost_source",
            mode=SelectSelectorMode.DROPDOWN,
        )),
    })


def cost_source_schema(
    current: dict[str, Any],
    *,
    source: str,
) -> probatio.Schema:
    fields: dict[Any, Any] = {}
    if source == COST_SOURCE_FIXED:
        fields[
            probatio.Required(
                CONF_CHARGING_PRICE,
                description={
                    "suggested_value": current.get(CONF_CHARGING_PRICE, 0.40),
                },
            )
        ] = _price_selector(allow_negative=True)
    else:
        fields[
            probatio.Required(
                CONF_CHARGING_PRICE_ENTITY,
                description={
                    "suggested_value": current.get(CONF_CHARGING_PRICE_ENTITY),
                },
            )
        ] = _entity_selector(("sensor",))
    return probatio.Schema(fields)


def cost_module_schema(current: dict[str, Any]) -> probatio.Schema:
    """Keep the optional German adjustment separate from the energy price."""
    tariff = current.get(CONF_CHARGING_NETWORK_TARIFF)
    module = tariff.get("module", NETWORK_MODULE_NONE) if isinstance(tariff, dict) else NETWORK_MODULE_NONE
    return probatio.Schema({
        probatio.Required(CONF_CHARGING_NETWORK_MODULE, default=module): SelectSelector(
            SelectSelectorConfig(
                options=list(NETWORK_MODULES),
                translation_key="charging_network_module",
                mode=SelectSelectorMode.DROPDOWN,
            ),
        ),
    })


def cost_network_schema(current: dict[str, Any], *, module: str) -> probatio.Schema:
    """Published network prices, shared by Module 2 and the full Module 3 form."""
    fields: dict[Any, Any] = {
        probatio.Required(
            CONF_REGULAR_NETWORK,
            description={
                "suggested_value": current.get(CONF_REGULAR_NETWORK),
            },
        ): _price_selector(),
        probatio.Required(
            CONF_PRICE_INCLUDES_NETWORK,
            default=current.get(CONF_PRICE_INCLUDES_NETWORK, True),
        ): BooleanSelector(),
    }
    if module == NETWORK_MODULE_3:
        for key in (CONF_HIGH_NETWORK, CONF_LOW_NETWORK):
            fields[
                probatio.Required(
                    key, description={"suggested_value": current.get(key)}
                )
            ] = _price_selector()
        fields[
            probatio.Required(
                CONF_TARIFF_YEAR,
                description={
                    "suggested_value": current.get(CONF_TARIFF_YEAR),
                },
            )
        ] = _IntegerSelector(
            NumberSelectorConfig(
                min=2024, max=9998, step=1, mode=NumberSelectorMode.BOX
            )
        )
        fields[
            probatio.Required(
                CONF_AUTO_TARIFF_REFRESH,
                default=current.get(CONF_AUTO_TARIFF_REFRESH, False),
            )
        ] = BooleanSelector()
    return probatio.Schema(fields)


def cost_windows_schema(current: dict[str, Any]) -> probatio.Schema:
    fields: dict[Any, Any] = {}
    for key in (CONF_HIGH_PERIODS, CONF_LOW_PERIODS):
        fields[
            probatio.Required(key, description={"suggested_value": current.get(key)})
        ] = TextSelector(TextSelectorConfig(multiline=True))
    fields[
        probatio.Required(
            CONF_ACTIVE_QUARTERS,
            description={
                "suggested_value": current.get(CONF_ACTIVE_QUARTERS),
            },
        )
    ] = SelectSelector(
        SelectSelectorConfig(
            options=["1", "2", "3", "4"],
            translation_key="charging_tariff_quarter",
            multiple=True,
            mode=SelectSelectorMode.LIST,
        )
    )
    return probatio.Schema(fields)


def cost_module_three_schema(current: dict[str, Any]) -> probatio.Schema:
    """Review all imported prices and periods together before saving."""
    return probatio.Schema({
        **cost_network_schema(current, module=NETWORK_MODULE_3).schema,
        **cost_windows_schema(current).schema,
    })


def _price_selector(*, allow_negative: bool = False) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=-1000 if allow_negative else 0,
            max=1000,
            step="any",
            unit_of_measurement="EUR/kWh",
            mode=NumberSelectorMode.BOX,
        )
    )


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
