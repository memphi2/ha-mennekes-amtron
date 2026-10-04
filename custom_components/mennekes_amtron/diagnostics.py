"""Diagnostics for the MENNEKES AMTRON integration.

The dump is built from an allowlist, never from the config entry itself: the
serial number identifies the household's hardware and a ``/dev/serial/by-id``
path contains the adapter's serial number, so neither value is reported.
What support actually needs is the register image, and that is included in
full.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from ._flow_serial import port_category
from .capabilities import supported_blocks, supported_register_keys
from .client_errors import AmtronBusError
from .const import (
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_DEVICE_ID,
    CONF_PARITY,
    CONF_STOPBITS,
)
from .entry_types import MennekesAmtronConfigEntry
from .registers import REGISTERS_BY_KEY, layout_label

FNV1A64_OFFSET = 0xCBF29CE484222325
FNV1A64_PRIME = 0x100000001B3
FNV1A64_MASK = 0xFFFFFFFFFFFFFFFF


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry
) -> dict[str, Any]:
    """Return the diagnostics dump of one wallbox."""

    runtime_data = entry.runtime_data
    identity = runtime_data.identity
    client = runtime_data.client
    coordinator = runtime_data.coordinator
    data = coordinator.current_data()

    return {
        "bus": {
            "port_category": port_category(client.config.port),
            CONF_BAUDRATE: client.config.baudrate,
            CONF_BYTESIZE: client.config.bytesize,
            CONF_PARITY: client.config.parity,
            CONF_STOPBITS: client.config.stopbits,
            CONF_DEVICE_ID: client.config.device_id,
            "connected": client.connected,
        },
        "device": {
            "layout_version": layout_label(identity.layout_version),
            "firmware_version": identity.firmware_version,
            "article_number": identity.article_number,
            "serial_number_hash": fnv1a64(identity.serial_number),
            "phase_options_hw": identity.phase_options_hw,
            "max_evse_current": identity.max_evse_current,
        },
        "control": {
            "read_only": runtime_data.control.read_only,
            "current_cap": runtime_data.control.options.current_cap,
            "max_charging_current": runtime_data.control.max_charging_current,
            "charging_setpoint": runtime_data.control.charging_setpoint,
            "is_paused": runtime_data.control.is_paused,
            "heartbeat_running": bool(
                runtime_data.heartbeat is not None
                and runtime_data.heartbeat.running
            ),
        },
        "write_diagnostics": {
            "heartbeats_sent": runtime_data.diagnostics.heartbeats_sent,
            "heartbeats_failed": runtime_data.diagnostics.heartbeats_failed,
            "writes_sent": runtime_data.diagnostics.writes_sent,
            "writes_rejected": runtime_data.diagnostics.writes_rejected,
            "writes_rate_limited": runtime_data.diagnostics.writes_rate_limited,
            "recoveries": runtime_data.diagnostics.recoveries,
            "last_error": runtime_data.diagnostics.last_error,
        },
        "poll": {
            "last_update_success": coordinator.last_update_success,
            "failed_blocks": list(data.failed_blocks),
            "supported_registers": sorted(
                supported_register_keys(
                    identity.layout_version, identity.phase_options_hw
                )
            ),
        },
        "values": decoded_values(data.values),
        "registers": await async_raw_registers(entry),
    }


def decoded_values(values: dict[str, Any]) -> dict[str, Any]:
    """Return the decoded register values, keyed by address and name."""

    dump: dict[str, Any] = {}
    for key, value in sorted(values.items()):
        spec = REGISTERS_BY_KEY.get(key)
        if spec is None:
            continue
        if spec.key in ("serial_number", "firmware_version", "article_number"):
            continue
        dump[f"0x{spec.address:04X} {key}"] = value
    return dump


async def async_raw_registers(entry: MennekesAmtronConfigEntry) -> dict[str, Any]:
    """Read the supported blocks once more and report the raw words.

    A raw image is what makes a support report decidable: it separates a
    decoding bug in this integration from a device that really reports that
    value.
    """

    runtime_data = entry.runtime_data
    client = runtime_data.client
    image: dict[str, Any] = {}
    for block in supported_blocks(runtime_data.identity.layout_version):
        try:
            words = await client.async_read_words(
                address=block.address, count=block.count
            )
        except AmtronBusError as err:
            image[block.name] = {"error": str(err)}
            continue
        image[block.name] = {
            f"0x{block.address + offset:04X}": word
            for offset, word in enumerate(words)
        }
    return image


def fnv1a64(value: str | None) -> str | None:
    """Return a stable, non-reversible hash of an identifying string."""

    if not value:
        return None
    digest = FNV1A64_OFFSET
    for byte in value.encode("utf-8"):
        digest = ((digest ^ byte) * FNV1A64_PRIME) & FNV1A64_MASK
    return f"{digest:016x}"
