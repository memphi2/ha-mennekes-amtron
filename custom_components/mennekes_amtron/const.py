"""Constants for the MENNEKES AMTRON integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "mennekes_amtron"

MANUFACTURER: Final = "MENNEKES"
DEFAULT_MODEL: Final = "AMTRON"

# Vendor bus defaults. All of them are changeable with the MENNEKES
# configuration tool, so every one of them is a config-flow field.
DEFAULT_BAUDRATE: Final = 57600
DEFAULT_BYTESIZE: Final = 8
DEFAULT_PARITY: Final = "N"
DEFAULT_STOPBITS: Final = 2
DEFAULT_DEVICE_ID: Final = 50
DEVICE_ID_MIN: Final = 10
DEVICE_ID_MAX: Final = 50
SUPPORTED_BAUDRATES: Final = (9600, 14400, 19200, 28800, 38400, 56000, 57600)
SUPPORTED_PARITY_STOPBITS: Final = (("N", 2), ("E", 1), ("O", 1))

DEFAULT_TIMEOUT: Final = 1.0
DEFAULT_RETRIES: Final = 2

# The vendor requires a heartbeat at least every 10 s. Half of that keeps one
# lost frame from reaching the deadline.
HEARTBEAT_INTERVAL_SECONDS: Final = 5.0
HEARTBEAT_DEADLINE_SECONDS: Final = 10.0

DEFAULT_SCAN_INTERVAL_SECONDS: Final = 5
MIN_SCAN_INTERVAL_SECONDS: Final = 2
MAX_SCAN_INTERVAL_SECONDS: Final = 300

# Vendor rate limits: charging current not faster than every 5 s, and
# pause/resume plus phase switching with hysteresis and intervals > 5 min.
MIN_CURRENT_WRITE_INTERVAL_SECONDS: Final = 5.0
MIN_MODE_CHANGE_INTERVAL_SECONDS: Final = 300.0

# 0x0302 semantics. 0 A means "no limitation" and makes the wallbox signal its
# maximum, 0.01-5.99 A is the vendor's documented pause value.
CHARGING_CURRENT_MINIMUM: Final = 6.0
CHARGING_CURRENT_UNLIMITED: Final = 0.0
CHARGING_PAUSE_CURRENT: Final = 1.0
CHARGING_CURRENT_STEP: Final = 0.1

HEARTBEAT_VALUE: Final = 0x55AA
SYSTEM_RESTART_VALUE: Final = 0xBB

CONF_PORT: Final = "port"
CONF_BAUDRATE: Final = "baudrate"
CONF_BYTESIZE: Final = "bytesize"
CONF_PARITY: Final = "parity"
CONF_STOPBITS: Final = "stopbits"
CONF_DEVICE_ID: Final = "device_id"
CONF_CONTROL_MODE: Final = "control_mode"
CONF_SCAN_INTERVAL_SECONDS: Final = "scan_interval_seconds"
CONF_CURRENT_LIMIT: Final = "current_limit"
CONF_SERIAL_NUMBER: Final = "serial_number"

CONTROL_MODE_READ_ONLY: Final = "read_only"
CONTROL_MODE_MASTER: Final = "master"
CONTROL_MODES: Final = (CONTROL_MODE_READ_ONLY, CONTROL_MODE_MASTER)
DEFAULT_CONTROL_MODE: Final = CONTROL_MODE_READ_ONLY

SIGNAL_WRITE_DIAGNOSTICS_CHANGED: Final = f"{DOMAIN}_write_diagnostics_changed"

SERVICE_SET_CHARGING_CURRENT: Final = "set_charging_current"
ATTR_CURRENT: Final = "current"
ATTR_ALLOW_UNLIMITED: Final = "allow_unlimited"

ERROR_CODE_EMS_UNAVAILABLE: Final = 200
