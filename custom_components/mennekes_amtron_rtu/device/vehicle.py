"""Conservative vehicle presence from EVSE and control-pilot observations."""

from collections.abc import Callable

from .decode import RegisterValue
from .enums import CpState, EvseState
from .registers import CP_STATE, EVSE_STATE

CONNECTED_EVSE_STATES = frozenset({
    EvseState.EV_CONNECTED, EvseState.PRECONDITIONS_VALID,
    EvseState.READY_TO_CHARGE, EvseState.CHARGING,
})
CONNECTED_CP_STATES = frozenset({
    CpState.B1, CpState.B2, CpState.C1, CpState.C2, CpState.D1, CpState.D2,
})
ABSENT_CP_STATES = frozenset({CpState.A1, CpState.A2})


def vehicle_connected(
    evse: RegisterValue, cp: RegisterValue, *, cp_supported: bool
) -> bool | None:
    """Never turn an error, initialization or conflicting status into unplugging."""
    if not isinstance(evse, int) or isinstance(evse, bool):
        return None
    if evse not in EvseState or evse == EvseState.NOT_INITIALIZED:
        return None
    connected = evse in CONNECTED_EVSE_STATES
    if not cp_supported:
        return True if connected else False if evse == EvseState.IDLE else None
    if not isinstance(cp, int) or isinstance(cp, bool):
        return True if connected else None
    if cp in ABSENT_CP_STATES:
        return False if evse == EvseState.IDLE else None
    if cp in CONNECTED_CP_STATES:
        return None if evse == EvseState.IDLE else True
    return True if connected else None


def snapshot_vehicle_connected(
    get: Callable[[str], RegisterValue], layout_version: int,
) -> bool | None:
    """Vehicle presence from one snapshot's ``get``, on this register layout."""

    return vehicle_connected(
        get(EVSE_STATE.key), get(CP_STATE.key),
        cp_supported=layout_version >= CP_STATE.min_layout,
    )
