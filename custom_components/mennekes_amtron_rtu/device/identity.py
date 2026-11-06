"""Read the immutable device facts a wallbox reports once.

These registers decide what the rest of the integration may do: the layout
version gates every other register, and the hardware phase option gates the
phase-switch entities. Reading them is also the config flow's connection
test, so this module stays free of Home Assistant imports.
"""

from __future__ import annotations

from .client import MennekesModbusClient
from .client_errors import AmtronBusError
from .const import CHARGING_CURRENT_MINIMUM, DEFAULT_MODEL
from .data import DeviceIdentity
from .models import ARTICLE_MODELS
from .registers import (
    ARTICLE_NUMBER,
    FIRMWARE_VERSION,
    MAX_EVSE_CURRENT,
    MODBUS_LAYOUT_VERSION,
    PHASE_OPTIONS_HW,
    SERIAL_NUMBER,
    RegisterSpec,
)


def display_identifier(value: str | None) -> str | None:
    """Remove leading device padding for display, never for registry keys.

    Existing entries and entities use the raw serial as their stable identity.
    Cleaning that key would create new devices and lose entity associations.
    Embedded underscores and leading zeroes are part of the identifier.
    """

    return value.lstrip("_") or None if value else None


def model_name(article_number: str | None) -> str:
    """Resolve verified article numbers without guessing from a prefix.

    See the manufacturer sources in docs/compatibility.md. Other articles
    keep a generic model plus model_id.
    """

    return ARTICLE_MODELS.get(display_identifier(article_number) or "", DEFAULT_MODEL)


def usable_max_evse_current(value: float | None) -> float | None:
    """Treat an unusable optional hardware limit as unknown, not a command.

    Raw register telemetry remains available in the polled values. Commands
    continue to use the configured current cap when this capability is absent.
    """

    return value if value is not None and CHARGING_CURRENT_MINIMUM <= value <= 32 else None


async def async_read_identity(client: MennekesModbusClient) -> DeviceIdentity:
    """Read the device identity, raising only when the layout is unreadable.

    The layout version is mandatory: without it the integration cannot know
    which registers exist. Everything else is optional, because older layouts
    simply do not have those registers.
    """

    layout = await client.async_read_register(MODBUS_LAYOUT_VERSION)
    if not isinstance(layout, int) or layout <= 0:
        raise AmtronBusError("device did not report a Modbus layout version")

    identity = DeviceIdentity(layout_version=layout)
    identity.firmware_version = await _optional_text(client, FIRMWARE_VERSION, layout)
    identity.serial_number = await _optional_text(client, SERIAL_NUMBER, layout)
    identity.article_number = await _optional_text(client, ARTICLE_NUMBER, layout)
    identity.phase_options_hw = await _optional_int(client, PHASE_OPTIONS_HW, layout)
    identity.max_evse_current = usable_max_evse_current(
        await _optional_float(client, MAX_EVSE_CURRENT, layout)
    )
    client.bind_identity(identity.serial_number)
    return identity


async def _optional_value(
    client: MennekesModbusClient,
    spec: RegisterSpec,
    layout: int,
) -> object:
    if layout < spec.min_layout:
        return None
    try:
        return await client.async_read_register(spec)
    except AmtronBusError:
        return None


async def _optional_text(
    client: MennekesModbusClient,
    spec: RegisterSpec,
    layout: int,
) -> str | None:
    value = await _optional_value(client, spec, layout)
    return value if isinstance(value, str) else None


async def _optional_int(
    client: MennekesModbusClient,
    spec: RegisterSpec,
    layout: int,
) -> int | None:
    value = await _optional_value(client, spec, layout)
    return value if isinstance(value, int) else None


async def _optional_float(
    client: MennekesModbusClient,
    spec: RegisterSpec,
    layout: int,
) -> float | None:
    value = await _optional_value(client, spec, layout)
    if isinstance(value, (int, float)):
        return float(value)
    return None
