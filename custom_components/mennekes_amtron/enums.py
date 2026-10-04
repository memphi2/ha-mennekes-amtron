"""Device enumerations exposed by the AMTRON Modbus register map.

Every member name doubles as its Home Assistant state option: the option
string is the lowercased member name, which keeps ``strings.json`` and the
register map mechanically checkable against each other.
"""

from __future__ import annotations

from enum import IntEnum


class EvseState(IntEnum):
    """Charging-station state reported in 0x0100."""

    NOT_INITIALIZED = 0
    IDLE = 1
    EV_CONNECTED = 2
    PRECONDITIONS_VALID = 3
    READY_TO_CHARGE = 4
    CHARGING = 5
    ERROR = 6
    SERVICE_MODE = 7


class AuthorizationStatus(IntEnum):
    """RFID and energy-manager authorization status reported in 0x0101."""

    NOT_USED = 0
    AUTHORIZED = 1
    NOT_AUTHORIZED = 2


class DowngradeStatus(IntEnum):
    """External downgrade-signal status reported in 0x0102."""

    NOT_RELEVANT = 0
    NOT_DOWNGRADED = 1
    DOWNGRADED = 2


class PhaseRotation(IntEnum):
    """Order of the connected phases reported in 0x0103."""

    L1_L2_L3 = 0
    L2_L3_L1 = 1
    L3_L1_L2 = 2


class CpState(IntEnum):
    """Control-pilot state between EVSE and EV reported in 0x0108."""

    INIT = 0
    A1 = 10
    B1 = 11
    C1 = 12
    D1 = 13
    E = 14
    F = 15
    A2 = 26
    B2 = 27
    C2 = 28
    D2 = 29


class PhaseSwitchingMode(IntEnum):
    """Phase usage of the internal solar algorithm reported in 0x030A."""

    SOLAR_ONE_PHASE = 0
    SOLAR_THREE_PHASES = 1
    SOLAR_DYNAMIC = 2


class PhaseOptionsHardware(IntEnum):
    """Hardware phase capability reported in 0x030C."""

    ONE_PHASE_ONLY = 0
    THREE_PHASES_ONLY = 1
    ONE_OR_THREE_PHASES = 2


class GridPhasesConnected(IntEnum):
    """Number of grid phases connected, reported in 0x0311."""

    L1 = 0
    L1_L2_L3 = 2


class DetectedEvPhases(IntEnum):
    """Maximum number of EV phases detected in a session, from 0x0B06."""

    NOT_INITIALIZED = 0
    ONE_PHASE = 1
    TWO_PHASES = 2
    THREE_PHASES = 3


class CableLockStatus(IntEnum):
    """Cable locking status reported in 0x0D02.

    The specification calls value 0 "cable locking unknown"; the option is
    named ``undetermined`` because ``unknown`` is a reserved Home Assistant
    state and would be indistinguishable from a missing value.
    """

    UNDETERMINED = 0
    UNLOCKED = 1
    LOCKED = 2
    FIXED_CABLE = 3


class SolarChargingMode(IntEnum):
    """Active solar charging mode in 0x0D03.

    Writing this register only drives the wallbox HMI and its solar LEDs; the
    energy manager stays responsible for the solar algorithm itself.
    """

    NOT_ACTIVE = 0
    FAST = 1
    SUNSHINE = 2
    SUNSHINE_PLUS = 3


class RequestedPhases(IntEnum):
    """Requested phase usage in 0x0D04."""

    ALL_AVAILABLE = 0
    SINGLE_PHASE = 1


class SwitchedPhases(IntEnum):
    """Phases the EVSE will use when it closes the relay, from 0x0E02."""

    ALL_AVAILABLE = 0
    SINGLE_PHASE = 1


class ErrorCategory(IntEnum):
    """Grouped interpretation of the active error code in 0x0E00.

    The register is a plain error number, and the specification documents only
    a few of them. The sensor therefore reports a documented category and
    keeps the raw number as a state attribute instead of inventing names for
    undocumented codes.
    """

    NO_ERROR = 0
    ENERGY_MANAGER_UNAVAILABLE = 1
    EV_OVERCURRENT = 2
    VOLTAGE_OUT_OF_RANGE = 3
    CONNECTED_PHASES_MISMATCH = 4
    OTHER = 5


def enum_options(enum_class: type[IntEnum]) -> tuple[str, ...]:
    """Return the Home Assistant state options of an enum, in member order."""

    return tuple(member.name.lower() for member in enum_class)


def enum_option(member: IntEnum) -> str:
    """Return the Home Assistant state option of one enum member."""

    return member.name.lower()


def enum_map(enum_class: type[IntEnum]) -> dict[int, str]:
    """Return the register-value to state-option mapping of an enum."""

    return {int(member): member.name.lower() for member in enum_class}


def error_category(code: int) -> ErrorCategory:
    """Return the documented category of an active error code."""

    if code == 0:
        return ErrorCategory.NO_ERROR
    if code == 200:
        return ErrorCategory.ENERGY_MANAGER_UNAVAILABLE
    if code == 2011:
        return ErrorCategory.EV_OVERCURRENT
    if 2300 <= code <= 2305:
        return ErrorCategory.VOLTAGE_OUT_OF_RANGE
    if code == 2323:
        return ErrorCategory.CONNECTED_PHASES_MISMATCH
    return ErrorCategory.OTHER
