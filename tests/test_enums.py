from __future__ import annotations

import pytest

from custom_components.mennekes_amtron.enums import (
    CableLockStatus,
    CpState,
    ErrorCategory,
    EvseState,
    SolarChargingMode,
    enum_map,
    enum_option,
    enum_options,
    error_category,
)


def test_evse_states_match_the_specification() -> None:
    assert EvseState.IDLE == 1
    assert EvseState.CHARGING == 5
    assert EvseState.SERVICE_MODE == 7


def test_cp_states_keep_the_documented_gaps() -> None:
    assert CpState.A1 == 10
    assert CpState.C2 == 28
    with pytest.raises(ValueError):
        CpState(20)


def test_options_are_lowercased_member_names() -> None:
    assert enum_options(SolarChargingMode) == (
        "not_active",
        "fast",
        "sunshine",
        "sunshine_plus",
    )
    assert enum_option(EvseState.READY_TO_CHARGE) == "ready_to_charge"


def test_cable_lock_avoids_the_reserved_unknown_state() -> None:
    assert "unknown" not in enum_options(CableLockStatus)
    assert CableLockStatus.UNDETERMINED == 0


def test_enum_map_keys_are_register_values() -> None:
    assert enum_map(EvseState)[5] == "charging"


@pytest.mark.parametrize(
    ("code", "category"),
    [
        (0, ErrorCategory.NO_ERROR),
        (200, ErrorCategory.ENERGY_MANAGER_UNAVAILABLE),
        (2011, ErrorCategory.EV_OVERCURRENT),
        (2300, ErrorCategory.VOLTAGE_OUT_OF_RANGE),
        (2305, ErrorCategory.VOLTAGE_OUT_OF_RANGE),
        (2323, ErrorCategory.CONNECTED_PHASES_MISMATCH),
        (2306, ErrorCategory.OTHER),
        (99, ErrorCategory.OTHER),
    ],
)
def test_error_codes_map_to_documented_categories(
    code: int, category: ErrorCategory
) -> None:
    assert error_category(code) is category
