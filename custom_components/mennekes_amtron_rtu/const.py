"""Names of Home Assistant's side: options, services and their defaults."""

from __future__ import annotations

from typing import Final

from .device.const import DEFAULT_MODEL

MANUFACTURER: Final = "Mennekes"
DEFAULT_NAME: Final = f"{MANUFACTURER} {DEFAULT_MODEL}"
DEVICE_ID_MIN: Final = 10
DEVICE_ID_MAX: Final = 50
DEFAULT_FRAME: Final = "8n2"
SUPPORTED_PARITY_STOPBITS: Final = (("N", 2), ("E", 1), ("O", 1))

CONF_FRAME: Final = "frame"

DEFAULT_SCAN_INTERVAL_SECONDS: Final = 5
MIN_SCAN_INTERVAL_SECONDS: Final = 2
MAX_SCAN_INTERVAL_SECONDS: Final = 300
CONF_STATE_CHANGE_INTERVAL_SECONDS: Final = "state_change_interval_seconds"
CONF_PAUSE_BEFORE_PHASE_SWITCH: Final = "pause_before_phase_switch"
CONF_PHASE_SWITCH_GRACE_SECONDS: Final = "phase_switch_grace_seconds"
CHARGING_CURRENT_STEP: Final = 0.1
# Decimals the charging-current entity may report. The register is a float32,
# so anything beyond its own step is a conversion artefact that would be
# recorded on every poll.
CHARGING_CURRENT_DECIMALS: Final = 1

CONF_PORT: Final = "port"
CONF_CONNECTION_TYPE: Final = "connection_type"
CONF_HOST: Final = "host"
CONF_TCP_PORT: Final = "tcp_port"
CONF_TCP_AUTHENTICATION: Final = "tcp_authentication"
CONF_TCP_AUTH_PASSWORD: Final = "tcp_auth_password"  # noqa: S105 - configuration key
CONF_CONFIGURE_GATEWAY: Final = "configure_gateway"
CONNECTION_SERIAL: Final = "serial"
CONNECTION_RTU_OVER_TCP: Final = "rtu_over_tcp"
CONNECTION_MODBUS_TCP: Final = "modbus_tcp"
NETWORK_CONNECTION_TYPES: Final = (CONNECTION_RTU_OVER_TCP, CONNECTION_MODBUS_TCP)
DEFAULT_TCP_PORT: Final = 502
CONF_BAUDRATE: Final = "baudrate"
CONF_BYTESIZE: Final = "bytesize"
CONF_PARITY: Final = "parity"
CONF_STOPBITS: Final = "stopbits"
CONF_DEVICE_ID: Final = "device_id"
CONF_CONTROL_MODE: Final = "control_mode"
CONF_RELEASE_OFF_ON_UNPLUG: Final = "release_off_on_unplug"
CONF_SCAN_INTERVAL_SECONDS: Final = "scan_interval_seconds"
CONF_MEASUREMENT_INTERVAL_SECONDS: Final = "measurement_interval_seconds"
CONF_CURRENT_LIMIT: Final = "current_limit"
CONF_SERIAL_NUMBER: Final = "serial_number"
CONF_SEARCH: Final = "search"

SEARCH_OFF: Final = "off"
SEARCH_ADDRESSES: Final = "addresses"
SEARCH_FULL: Final = "full"
SEARCH_MODES: Final = (SEARCH_ADDRESSES, SEARCH_FULL, SEARCH_OFF)
DEFAULT_SEARCH: Final = SEARCH_ADDRESSES

CONTROL_MODE_READ_ONLY: Final = "read_only"
CONTROL_MODE_MASTER: Final = "master"
CONTROL_MODES: Final = (CONTROL_MODE_READ_ONLY, CONTROL_MODE_MASTER)
DEFAULT_CONTROL_MODE: Final = CONTROL_MODE_READ_ONLY

SERVICE_SET_CHARGING_CURRENT: Final = "set_charging_current"
ATTR_CURRENT: Final = "current"
ATTR_ALLOW_UNLIMITED: Final = "allow_unlimited"
