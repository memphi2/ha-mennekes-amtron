from __future__ import annotations

from custom_components.mennekes_amtron.registers import (
    LAYOUT_V01_00,
    LAYOUT_V01_03,
    LAYOUT_VERSIONS,
    READABLE_REGISTERS,
    REGISTERS,
    REGISTERS_BY_KEY,
    RegisterAccess,
    RegisterDataType,
    layout_label,
    register,
)


def test_every_register_key_is_unique() -> None:
    assert len(REGISTERS_BY_KEY) == len(REGISTERS)


def test_register_ranges_do_not_overlap() -> None:
    occupied: dict[int, str] = {}
    for spec in REGISTERS:
        for address in range(spec.address, spec.end_address):
            assert address not in occupied, (
                f"0x{address:04X} claimed by {occupied.get(address)} and {spec.key}"
            )
            occupied[address] = spec.key


def test_layout_versions_are_documented() -> None:
    assert all(spec.min_layout in LAYOUT_VERSIONS for spec in REGISTERS)


def test_readable_registers_exclude_write_only_ones() -> None:
    keys = {spec.key for spec in READABLE_REGISTERS}
    assert "heartbeat" not in keys
    assert "system_restart" not in keys
    assert "charging_current_ems" in keys


def test_access_flags_match_the_specification() -> None:
    heartbeat = register("heartbeat")
    assert heartbeat.access is RegisterAccess.WRITE
    assert heartbeat.writable
    assert not heartbeat.readable

    current = register("charging_current_ems")
    assert current.access is RegisterAccess.READ_WRITE
    assert current.readable
    assert current.writable
    assert current.datatype is RegisterDataType.FLOAT32
    assert current.count == 2


def test_known_addresses_match_the_specification() -> None:
    assert register("modbus_layout_version").address == 0x0000
    assert register("evse_state").address == 0x0100
    assert register("charging_current_ems").address == 0x0302
    assert register("heartbeat").address == 0x0D00
    assert register("system_restart").address == 0x0D19
    assert register("energy_total").address == 0x1000
    assert register("article_number").min_layout == LAYOUT_V01_03
    assert register("evse_state").min_layout == LAYOUT_V01_00


def test_layout_label_uses_the_vendor_notation() -> None:
    assert layout_label(0x0100) == "v01.00"
    assert layout_label(0x0103) == "v01.03"


def test_end_address_covers_the_whole_range() -> None:
    serial = register("serial_number")
    assert serial.count == 8
    assert serial.end_address == 0x001B
