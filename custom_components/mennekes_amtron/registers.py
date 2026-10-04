"""The AMTRON Modbus register map.

This module is the single source of truth for the device contract. Platforms,
the client, diagnostics and the register gate all derive from ``REGISTERS``;
nothing else may hard-code a register address.

Source: MENNEKES "Modbus RTU Specification -- AMTRON 4You 300 / Compact 2.0s /
Start 2.0s", document revision 2.5 (2025-07-11), register layout v01.03. The
addresses, data types, ranges and enumerated values below are facts restated
from that document; see ``docs/modbus-registers.md`` for the project's own
description of the register map and the document reference.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from .enums import (
    AuthorizationStatus,
    CableLockStatus,
    CpState,
    DetectedEvPhases,
    DowngradeStatus,
    EvseState,
    GridPhasesConnected,
    PhaseOptionsHardware,
    PhaseRotation,
    PhaseSwitchingMode,
    RequestedPhases,
    SolarChargingMode,
    SwitchedPhases,
    enum_map,
)

# Internal register layout versions. 0x0000 reports the layout, not the
# firmware version, and it is the only reliable capability source.
LAYOUT_V01_00: Final = 0x0100
LAYOUT_V01_01: Final = 0x0101
LAYOUT_V01_02: Final = 0x0102
LAYOUT_V01_03: Final = 0x0103
LAYOUT_VERSIONS: Final = (
    LAYOUT_V01_00,
    LAYOUT_V01_01,
    LAYOUT_V01_02,
    LAYOUT_V01_03,
)
VALIDATED_LAYOUT: Final = LAYOUT_V01_03

# Modbus caps a single read at 125 holding registers; the project stays well
# below that so one block always fits one frame at 57600 baud.
MAX_BLOCK_REGISTERS: Final = 100

UNIT_AMPERE: Final = "A"
UNIT_VOLT: Final = "V"
UNIT_WATT: Final = "W"
UNIT_KILOWATT_HOUR: Final = "kWh"
UNIT_CELSIUS: Final = "degC"
UNIT_SECOND: Final = "s"


class RegisterDataType(StrEnum):
    """Wire data type of a register range."""

    UINT16 = "uint16"
    UINT32 = "uint32"
    FLOAT32 = "float32"
    STRING = "string"


class RegisterAccess(StrEnum):
    """Access mode the specification grants for a register."""

    READ = "read"
    READ_WRITE = "read_write"
    WRITE = "write"


@dataclass(frozen=True, slots=True)
class RegisterSpec:
    """One addressable register range of the device."""

    key: str
    address: int
    count: int
    datatype: RegisterDataType
    access: RegisterAccess
    min_layout: int
    unit: str | None = None
    enum_map: Mapping[int, str] | None = None
    value_min: float | None = None
    value_max: float | None = None

    @property
    def end_address(self) -> int:
        """Return the first address past this register range."""

        return self.address + self.count

    @property
    def readable(self) -> bool:
        """Return true when the specification allows reading this register."""

        return self.access in (RegisterAccess.READ, RegisterAccess.READ_WRITE)

    @property
    def writable(self) -> bool:
        """Return true when the specification allows writing this register."""

        return self.access in (RegisterAccess.WRITE, RegisterAccess.READ_WRITE)


def _u16(
    key: str,
    address: int,
    *,
    min_layout: int = LAYOUT_V01_00,
    access: RegisterAccess = RegisterAccess.READ,
    unit: str | None = None,
    values: Mapping[int, str] | None = None,
    value_min: float | None = None,
    value_max: float | None = None,
) -> RegisterSpec:
    return RegisterSpec(
        key=key,
        address=address,
        count=1,
        datatype=RegisterDataType.UINT16,
        access=access,
        min_layout=min_layout,
        unit=unit,
        enum_map=values,
        value_min=value_min,
        value_max=value_max,
    )


def _f32(
    key: str,
    address: int,
    *,
    min_layout: int = LAYOUT_V01_00,
    access: RegisterAccess = RegisterAccess.READ,
    unit: str | None = None,
) -> RegisterSpec:
    return RegisterSpec(
        key=key,
        address=address,
        count=2,
        datatype=RegisterDataType.FLOAT32,
        access=access,
        min_layout=min_layout,
        unit=unit,
    )


def _u32(
    key: str,
    address: int,
    *,
    min_layout: int = LAYOUT_V01_00,
    unit: str | None = None,
) -> RegisterSpec:
    return RegisterSpec(
        key=key,
        address=address,
        count=2,
        datatype=RegisterDataType.UINT32,
        access=RegisterAccess.READ,
        min_layout=min_layout,
        unit=unit,
    )


def _ascii16(key: str, address: int, *, min_layout: int) -> RegisterSpec:
    return RegisterSpec(
        key=key,
        address=address,
        count=8,
        datatype=RegisterDataType.STRING,
        access=RegisterAccess.READ,
        min_layout=min_layout,
    )


# General information (0x0000-0x00FF)
MODBUS_LAYOUT_VERSION: Final = _u16("modbus_layout_version", 0x0000)
FIRMWARE_VERSION: Final = _ascii16("firmware_version", 0x0001, min_layout=LAYOUT_V01_00)
SERIAL_NUMBER: Final = _ascii16("serial_number", 0x0013, min_layout=LAYOUT_V01_02)
ARTICLE_NUMBER: Final = _ascii16("article_number", 0x001B, min_layout=LAYOUT_V01_03)

# Status (0x0100-0x02FF)
EVSE_STATE: Final = _u16("evse_state", 0x0100, values=enum_map(EvseState))
AUTHORIZATION_STATUS: Final = _u16(
    "authorization_status", 0x0101, values=enum_map(AuthorizationStatus)
)
DOWNGRADE_STATUS: Final = _u16(
    "downgrade_status", 0x0102, values=enum_map(DowngradeStatus)
)
PHASE_ROTATION: Final = _u16("phase_rotation", 0x0103, values=enum_map(PhaseRotation))
CP_STATE: Final = _u16(
    "cp_state", 0x0108, min_layout=LAYOUT_V01_02, values=enum_map(CpState)
)
SIGNALED_CURRENT: Final = _f32(
    "signaled_current", 0x0114, min_layout=LAYOUT_V01_03, unit=UNIT_AMPERE
)

# Configuration (0x0300-0x04FF)
DOWNGRADE_CURRENT: Final = _f32("downgrade_current", 0x0300, unit=UNIT_AMPERE)
CHARGING_CURRENT_EMS: Final = _f32(
    "charging_current_ems",
    0x0302,
    access=RegisterAccess.READ_WRITE,
    unit=UNIT_AMPERE,
)
MAX_CURRENT_HOUSE: Final = _f32("max_current_house", 0x0304, unit=UNIT_AMPERE)
MAX_EVSE_CURRENT: Final = _f32("max_evse_current", 0x0306, unit=UNIT_AMPERE)
PHASE_SWITCHING_MODE: Final = _u16(
    "phase_switching_mode", 0x030A, values=enum_map(PhaseSwitchingMode)
)
PHASE_OPTIONS_HW: Final = _u16(
    "phase_options_hw",
    0x030C,
    min_layout=LAYOUT_V01_01,
    values=enum_map(PhaseOptionsHardware),
)
CABLE_LOCK_SETTING: Final = _u16(
    "cable_lock_setting", 0x030D, min_layout=LAYOUT_V01_02
)
EMS_FALLBACK_CURRENT: Final = _u16(
    "ems_fallback_current",
    0x030E,
    min_layout=LAYOUT_V01_02,
    unit=UNIT_AMPERE,
    value_min=0,
    value_max=32,
)
GRID_IMBALANCE: Final = _u16("grid_imbalance", 0x030F, min_layout=LAYOUT_V01_02)
GRID_IMBALANCE_THRESHOLD: Final = _u16(
    "grid_imbalance_threshold",
    0x0310,
    min_layout=LAYOUT_V01_02,
    unit=UNIT_AMPERE,
    value_min=10,
    value_max=30,
)
GRID_PHASES_CONNECTED: Final = _u16(
    "grid_phases_connected",
    0x0311,
    min_layout=LAYOUT_V01_02,
    values=enum_map(GridPhasesConnected),
)
AUTHORIZATION_ENABLED: Final = _u16(
    "authorization_enabled", 0x0312, min_layout=LAYOUT_V01_02
)
SOLAR_MIN_CURRENT: Final = _u16(
    "solar_min_current",
    0x0313,
    min_layout=LAYOUT_V01_02,
    unit=UNIT_AMPERE,
    value_min=6,
    value_max=32,
)
PHASE_SWITCHING_PAUSE: Final = _u16(
    "phase_switching_pause",
    0x0314,
    min_layout=LAYOUT_V01_02,
    unit=UNIT_SECOND,
    value_min=0,
    value_max=1200,
)

# Output measurements (0x0500-0x06FF)
CURRENT_L1: Final = _f32("current_l1", 0x0500, unit=UNIT_AMPERE)
CURRENT_L2: Final = _f32("current_l2", 0x0502, unit=UNIT_AMPERE)
CURRENT_L3: Final = _f32("current_l3", 0x0504, unit=UNIT_AMPERE)
VOLTAGE_L1: Final = _f32("voltage_l1", 0x0506, unit=UNIT_VOLT)
VOLTAGE_L2: Final = _f32("voltage_l2", 0x0508, unit=UNIT_VOLT)
VOLTAGE_L3: Final = _f32("voltage_l3", 0x050A, unit=UNIT_VOLT)
POWER_L1: Final = _f32("power_l1", 0x050C, unit=UNIT_WATT)
POWER_L2: Final = _f32("power_l2", 0x050E, unit=UNIT_WATT)
POWER_L3: Final = _f32("power_l3", 0x0510, unit=UNIT_WATT)
POWER_TOTAL: Final = _f32("power_total", 0x0512, unit=UNIT_WATT)

# Input measurements (0x0900-0x0AFF)
TEMPERATURE: Final = _f32(
    "temperature", 0x0900, min_layout=LAYOUT_V01_02, unit=UNIT_CELSIUS
)

# Charging session (0x0B00-0x0CFF)
SESSION_MAX_CURRENT: Final = _f32("session_max_current", 0x0B00, unit=UNIT_AMPERE)
SESSION_ENERGY: Final = _f32("session_energy", 0x0B02, unit=UNIT_KILOWATT_HOUR)
SESSION_DURATION: Final = _u32("session_duration", 0x0B04, unit=UNIT_SECOND)
DETECTED_EV_PHASES: Final = _u16(
    "detected_ev_phases",
    0x0B06,
    min_layout=LAYOUT_V01_02,
    values=enum_map(DetectedEvPhases),
)

# Functions (0x0D00-0x0DFF)
HEARTBEAT: Final = _u16("heartbeat", 0x0D00, access=RegisterAccess.WRITE)
CABLE_LOCK_STATUS: Final = _u16(
    "cable_lock_status", 0x0D02, values=enum_map(CableLockStatus)
)
SOLAR_CHARGING_MODE: Final = _u16(
    "solar_charging_mode",
    0x0D03,
    access=RegisterAccess.READ_WRITE,
    values=enum_map(SolarChargingMode),
)
REQUESTED_PHASES: Final = _u16(
    "requested_phases",
    0x0D04,
    access=RegisterAccess.READ_WRITE,
    values=enum_map(RequestedPhases),
)
CHARGING_RELEASE: Final = _u16(
    "charging_release", 0x0D05, access=RegisterAccess.READ_WRITE
)
LOCK_EVSE: Final = _u16("lock_evse", 0x0D06, access=RegisterAccess.READ_WRITE)
SYSTEM_RESTART: Final = _u16(
    "system_restart", 0x0D19, min_layout=LAYOUT_V01_03, access=RegisterAccess.WRITE
)

# Diagnostic (0x0E00-0x0FFF)
ERROR_CODE: Final = _u16("error_code", 0x0E00)
MASTER_LOST_FALLBACK: Final = _u16(
    "master_lost_fallback", 0x0E01, min_layout=LAYOUT_V01_02
)
SWITCHED_PHASES: Final = _u16(
    "switched_phases",
    0x0E02,
    min_layout=LAYOUT_V01_02,
    values=enum_map(SwitchedPhases),
)

# Statistic (0x1000-0x1FFF)
ENERGY_TOTAL: Final = _f32(
    "energy_total", 0x1000, min_layout=LAYOUT_V01_02, unit=UNIT_KILOWATT_HOUR
)
SESSIONS_TOTAL: Final = _u32("sessions_total", 0x1002, min_layout=LAYOUT_V01_02)

REGISTERS: Final[tuple[RegisterSpec, ...]] = (
    MODBUS_LAYOUT_VERSION,
    FIRMWARE_VERSION,
    SERIAL_NUMBER,
    ARTICLE_NUMBER,
    EVSE_STATE,
    AUTHORIZATION_STATUS,
    DOWNGRADE_STATUS,
    PHASE_ROTATION,
    CP_STATE,
    SIGNALED_CURRENT,
    DOWNGRADE_CURRENT,
    CHARGING_CURRENT_EMS,
    MAX_CURRENT_HOUSE,
    MAX_EVSE_CURRENT,
    PHASE_SWITCHING_MODE,
    PHASE_OPTIONS_HW,
    CABLE_LOCK_SETTING,
    EMS_FALLBACK_CURRENT,
    GRID_IMBALANCE,
    GRID_IMBALANCE_THRESHOLD,
    GRID_PHASES_CONNECTED,
    AUTHORIZATION_ENABLED,
    SOLAR_MIN_CURRENT,
    PHASE_SWITCHING_PAUSE,
    CURRENT_L1,
    CURRENT_L2,
    CURRENT_L3,
    VOLTAGE_L1,
    VOLTAGE_L2,
    VOLTAGE_L3,
    POWER_L1,
    POWER_L2,
    POWER_L3,
    POWER_TOTAL,
    TEMPERATURE,
    SESSION_MAX_CURRENT,
    SESSION_ENERGY,
    SESSION_DURATION,
    DETECTED_EV_PHASES,
    HEARTBEAT,
    CABLE_LOCK_STATUS,
    SOLAR_CHARGING_MODE,
    REQUESTED_PHASES,
    CHARGING_RELEASE,
    LOCK_EVSE,
    SYSTEM_RESTART,
    ERROR_CODE,
    MASTER_LOST_FALLBACK,
    SWITCHED_PHASES,
    ENERGY_TOTAL,
    SESSIONS_TOTAL,
)

REGISTERS_BY_KEY: Final[Mapping[str, RegisterSpec]] = {
    spec.key: spec for spec in REGISTERS
}
READABLE_REGISTERS: Final[tuple[RegisterSpec, ...]] = tuple(
    spec for spec in REGISTERS if spec.readable
)


def register(key: str) -> RegisterSpec:
    """Return one register specification by key."""

    return REGISTERS_BY_KEY[key]


def layout_label(layout: int) -> str:
    """Return the vendor's printed label for a register layout version."""

    return f"v{layout >> 8:02d}.{layout & 0xFF:02d}"
