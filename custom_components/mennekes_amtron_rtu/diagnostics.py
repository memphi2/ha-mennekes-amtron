"""Diagnostics for the MENNEKES AMTRON integration.

The dump is built from an allowlist, never from the config entry itself: the
serial number identifies the household's hardware and a ``/dev/serial/by-id``
path contains the adapter's serial number, so neither value is reported.
Network adapter addresses are likewise omitted and redacted from errors.
Support also receives a register image, excluding the raw serial-number
block, and error messages with known identifiers redacted.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from ._flow_serial import port_category
from .const import (
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_DEVICE_ID,
    CONF_PARITY,
    CONF_STOPBITS,
)
from .data import MennekesAmtronRuntimeData
from .device.capabilities import supported_blocks, supported_register_keys
from .device.client_errors import AmtronBusError
from .device.connection import NetworkConfig
from .device.registers import REGISTERS_BY_KEY, layout_label
from .device.runtime_events import sanitize_events
from .device.write_trace import sanitize_snapshot
from .entry_connection import connection_config
from .entry_types import MennekesAmtronConfigEntry
from .service_client import CONF_SERVER_URL

FNV1A64_OFFSET = 0xCBF29CE484222325
FNV1A64_PRIME = 0x100000001B3
FNV1A64_MASK = 0xFFFFFFFFFFFFFFFF


# Sections of the service state that carry no identifier. The serial number,
# the Plug & Charge session and the vehicle source stay out.
_CONTROL_KEYS = (
    "read_only", "current_cap", "max_charging_current", "charging_setpoint",
    "pause_requested", "charging_release_requested", "guest_mode", "intent_rejected",
    "pending_action", "pending_until", "state_change_interval_seconds",
    "phase_switch_grace_seconds",
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry
) -> dict[str, Any]:
    """Return the diagnostics dump of one wallbox."""

    if not hasattr(entry, "runtime_data"):
        # Setup never finished, e.g. the service is down: say that much.
        return {
            "setup": entry.state.value,
            "bus": {"transport": "service" if CONF_SERVER_URL in entry.data else "direct"},
        }
    runtime_data = entry.runtime_data
    identity = runtime_data.identity
    client = runtime_data.client
    coordinator = runtime_data.coordinator
    data = coordinator.current_data()
    state = client.snapshot
    try:
        trace = await client.request("GET", "/v2/diagnostics", update_connection=False)
        write_transactions = sanitize_snapshot(trace.get("write_transactions"))
        runtime_events = sanitize_events(trace.get("runtime_events"))
    except AmtronBusError:
        write_transactions = None
        runtime_events = None

    identifiers: tuple[str | None, ...] = (identity.serial_number,)
    bus: dict[str, Any] = {"connected": client.connected}
    if CONF_SERVER_URL in entry.data:
        bus["transport"] = "service"
    else:
        config = connection_config(entry)
        identifiers += (config.port,)
        bus[CONF_DEVICE_ID] = config.device_id
        if isinstance(config, NetworkConfig):
            bus["transport"] = config.connection_type
            bus["tcp_authentication"] = config.tcp_authentication
            identifiers += (config.host, config.tcp_auth_password or None)
        else:
            bus.update({
                "transport": "serial",
                "port_category": port_category(config.port),
                CONF_BAUDRATE: config.baudrate,
                CONF_BYTESIZE: config.bytesize,
                CONF_PARITY: config.parity,
                CONF_STOPBITS: config.stopbits,
            })
    control = state.get("control", {})
    payload = {
        "bus": bus,
        "service": {
            "api_version": state.get("api_version"),
            "service_version": state.get("service_version"),
            "available": state.get("available"),
        },
        "device": {
            "layout_version": layout_label(identity.layout_version),
            "firmware_version": identity.firmware_version,
            "article_number": identity.article_number,
            "serial_number_hash": fnv1a64(identity.serial_number),
            "phase_options_hw": identity.phase_options_hw,
            "max_evse_current": identity.max_evse_current,
        },
        "control": {key: control.get(key) for key in _CONTROL_KEYS},
        "operation": control.get("operation"),
        "heartbeat": state.get("heartbeat"),
        "plug_charge": _plug_charge_diagnostics(runtime_data),
        "connection_quality": {
            "state": data.connection_quality,
            "failed_transactions": data.connection_failures,
            "retried_transactions": data.connection_retries,
        },
        "counters": state.get("diagnostics"),
        "unresolved_write_errors": state.get("write_errors"),
        "poll": {
            "last_update_success": coordinator.last_update_success,
            "failed_blocks": list(data.failed_blocks),
            "supported_registers": sorted(
                supported_register_keys(
                    identity.layout_version, identity.phase_options_hw
                )
            ),
        },
        "runtime_events": runtime_events,
        "write_transactions": write_transactions,
        "values": decoded_values(data.values),
        "registers": await async_raw_registers(entry),
    }
    return {
        key: _redact_identifiers(value, identifiers) for key, value in payload.items()
    }


def _redact_identifiers(value: Any, identifiers: tuple[str | None, ...]) -> Any:
    """Also redact identifiers embedded in transport error messages."""

    if isinstance(value, dict):
        return {
            key: _redact_identifiers(item, identifiers) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_identifiers(item, identifiers) for item in value]
    if isinstance(value, str):
        for identifier in identifiers:
            if identifier:
                value = value.replace(identifier, "**REDACTED**")
    return value


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
        # Raw register words encode the same serial number as the decoded
        # string. Omitting only the string does not anonymize the dump.
        if "serial_number" in block.keys:
            continue
        try:
            words = await client.async_read_words(
                address=block.address, count=block.count
            )
        except AmtronBusError as err:
            image[block.name] = {"error": str(err)}
            continue
        image[block.name] = {
            f"0x{block.address + offset:04X}": word for offset, word in enumerate(words)
        }
    return image


def fnv1a64(value: str | None) -> str | None:
    """Return a stable pseudonymous identifier for matching support reports."""

    if not value:
        return None
    digest = FNV1A64_OFFSET
    for byte in value.encode("utf-8"):
        digest = ((digest ^ byte) * FNV1A64_PRIME) & FNV1A64_MASK
    return f"{digest:016x}"


def _plug_charge_diagnostics(runtime_data: MennekesAmtronRuntimeData) -> dict[str, Any] | None:
    """Gate state without the session token or vehicle policy hash."""
    observer = runtime_data.plug_charge
    if observer is None:
        return None
    gate = runtime_data.control.plug_charge
    return {
        "status": observer.status,
        "blocked_reason": observer.blocked_reason,
        "decision": getattr(observer, "decision_details", None),
        "gate_enabled": gate.get("enabled"),
        "gate_authorized": gate.get("authorized"),
        "session_active": isinstance(gate.get("session"), str),
        "session_since": gate.get("since"),
    }
