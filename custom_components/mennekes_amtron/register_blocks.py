"""Contiguous read blocks for the AMTRON register map.

Reading one block per address run keeps a full poll at roughly twenty Modbus
transactions instead of fifty. The blocks are declared explicitly rather than
derived, so ``scripts/check_register_map.py`` can prove them against
``registers.py`` instead of restating the same mistake twice.

A block is only read when its ``min_layout`` is supported. Older firmware
still answers some block reads with an illegal-data-address exception, so the
read path falls back to single-register reads for that block.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from .registers import (
    LAYOUT_V01_00,
    LAYOUT_V01_01,
    LAYOUT_V01_02,
    LAYOUT_V01_03,
    MAX_BLOCK_REGISTERS,
    READABLE_REGISTERS,
    REGISTERS_BY_KEY,
    RegisterSpec,
)


class BlockCadence(StrEnum):
    """How often a block is worth reading.

    Half of the register map is configuration: the serial number, the article
    number, the DIP-configured limits, the hardware phase option. Those change
    when somebody reconfigures the wallbox, not while it charges. Reading them
    at the charging cadence spends a large part of the bus on values that are
    already known.
    """

    FAST = "fast"
    SLOW = "slow"


# How long a slow block may go unread. Short enough that a reconfiguration
# shows up on its own, long enough to stay out of the way.
SLOW_BLOCK_INTERVAL_SECONDS: Final = 60.0


@dataclass(frozen=True, slots=True)
class RegisterBlock:
    """One contiguous range of readable registers."""

    name: str
    address: int
    count: int
    min_layout: int
    keys: tuple[str, ...]
    cadence: BlockCadence = BlockCadence.FAST

    @property
    def specs(self) -> tuple[RegisterSpec, ...]:
        """Return the register specifications covered by this block."""

        return tuple(REGISTERS_BY_KEY[key] for key in self.keys)


REGISTER_BLOCKS: Final[tuple[RegisterBlock, ...]] = (
    RegisterBlock(
        name="identity",
        address=0x0000,
        count=9,
        min_layout=LAYOUT_V01_00,
        keys=("modbus_layout_version", "firmware_version"),
        cadence=BlockCadence.SLOW,
    ),
    RegisterBlock(
        name="serial_number",
        address=0x0013,
        count=8,
        min_layout=LAYOUT_V01_02,
        keys=("serial_number",),
        cadence=BlockCadence.SLOW,
    ),
    RegisterBlock(
        name="article_number",
        address=0x001B,
        count=8,
        min_layout=LAYOUT_V01_03,
        keys=("article_number",),
        cadence=BlockCadence.SLOW,
    ),
    RegisterBlock(
        name="status",
        address=0x0100,
        count=4,
        min_layout=LAYOUT_V01_00,
        keys=(
            "evse_state",
            "authorization_status",
            "downgrade_status",
            "phase_rotation",
        ),
    ),
    RegisterBlock(
        name="cp_state",
        address=0x0108,
        count=1,
        min_layout=LAYOUT_V01_02,
        keys=("cp_state",),
    ),
    RegisterBlock(
        name="signaled_current",
        address=0x0114,
        count=2,
        min_layout=LAYOUT_V01_03,
        keys=("signaled_current",),
    ),
    RegisterBlock(
        name="current_limits",
        address=0x0300,
        count=8,
        min_layout=LAYOUT_V01_00,
        keys=(
            "downgrade_current",
            "charging_current_ems",
            "max_current_house",
            "max_evse_current",
        ),
    ),
    RegisterBlock(
        name="phase_switching_mode",
        address=0x030A,
        count=1,
        min_layout=LAYOUT_V01_00,
        keys=("phase_switching_mode",),
        cadence=BlockCadence.SLOW,
    ),
    RegisterBlock(
        name="phase_options_hw",
        address=0x030C,
        count=1,
        min_layout=LAYOUT_V01_01,
        keys=("phase_options_hw",),
        cadence=BlockCadence.SLOW,
    ),
    RegisterBlock(
        name="configuration",
        address=0x030D,
        count=8,
        min_layout=LAYOUT_V01_02,
        keys=(
            "cable_lock_setting",
            "ems_fallback_current",
            "grid_imbalance",
            "grid_imbalance_threshold",
            "grid_phases_connected",
            "authorization_enabled",
            "solar_min_current",
            "phase_switching_pause",
        ),
        cadence=BlockCadence.SLOW,
    ),
    RegisterBlock(
        name="measurements",
        address=0x0500,
        count=20,
        min_layout=LAYOUT_V01_00,
        keys=(
            "current_l1",
            "current_l2",
            "current_l3",
            "voltage_l1",
            "voltage_l2",
            "voltage_l3",
            "power_l1",
            "power_l2",
            "power_l3",
            "power_total",
        ),
    ),
    RegisterBlock(
        name="temperature",
        address=0x0900,
        count=2,
        min_layout=LAYOUT_V01_02,
        keys=("temperature",),
        cadence=BlockCadence.SLOW,
    ),
    RegisterBlock(
        name="session",
        address=0x0B00,
        count=6,
        min_layout=LAYOUT_V01_00,
        keys=("session_max_current", "session_energy", "session_duration"),
    ),
    RegisterBlock(
        name="detected_ev_phases",
        address=0x0B06,
        count=1,
        min_layout=LAYOUT_V01_02,
        keys=("detected_ev_phases",),
    ),
    RegisterBlock(
        name="functions",
        address=0x0D02,
        count=5,
        min_layout=LAYOUT_V01_00,
        keys=(
            "cable_lock_status",
            "solar_charging_mode",
            "requested_phases",
            "charging_release",
            "lock_evse",
        ),
    ),
    RegisterBlock(
        name="error_code",
        address=0x0E00,
        count=1,
        min_layout=LAYOUT_V01_00,
        keys=("error_code",),
    ),
    RegisterBlock(
        name="fallback_state",
        address=0x0E01,
        count=2,
        min_layout=LAYOUT_V01_02,
        keys=("master_lost_fallback", "switched_phases"),
    ),
    RegisterBlock(
        name="statistics",
        address=0x1000,
        count=4,
        min_layout=LAYOUT_V01_02,
        keys=("energy_total", "sessions_total"),
        cadence=BlockCadence.SLOW,
    ),
)


def block_consistency_failures() -> list[str]:
    """Return every way the declared blocks disagree with the register map.

    The register gate and a unit test both call this, so the rule lives next
    to the blocks it constrains instead of inside the gate script.
    """

    failures: list[str] = []
    covered: dict[str, str] = {}
    for block in REGISTER_BLOCKS:
        if block.cadence not in tuple(BlockCadence):
            failures.append(
                f"block {block.name} declares the unknown cadence "
                f"{block.cadence!r}"
            )
        if not block.keys:
            failures.append(f"block {block.name} covers no register")
            continue
        if block.count > MAX_BLOCK_REGISTERS:
            failures.append(
                f"block {block.name} reads {block.count} registers, "
                f"more than the {MAX_BLOCK_REGISTERS} register cap"
            )
        expected_address = block.address
        for key in block.keys:
            spec = REGISTERS_BY_KEY.get(key)
            if spec is None:
                failures.append(f"block {block.name} names unknown register {key}")
                break
            if key in covered:
                failures.append(
                    f"register {key} is covered by both {covered[key]} "
                    f"and {block.name}"
                )
            covered[key] = block.name
            if not spec.readable:
                failures.append(
                    f"block {block.name} covers write-only register {key}"
                )
            if spec.address != expected_address:
                failures.append(
                    f"block {block.name} expects {key} at "
                    f"0x{expected_address:04X} but the map says "
                    f"0x{spec.address:04X}"
                )
                break
            if spec.min_layout != block.min_layout:
                failures.append(
                    f"block {block.name} declares layout "
                    f"0x{block.min_layout:04X} but {key} needs "
                    f"0x{spec.min_layout:04X}"
                )
            expected_address = spec.end_address
        else:
            if expected_address - block.address != block.count:
                failures.append(
                    f"block {block.name} declares count {block.count} but its "
                    f"registers span {expected_address - block.address}"
                )

    missing = sorted(
        spec.key for spec in READABLE_REGISTERS if spec.key not in covered
    )
    if missing:
        failures.append(f"readable registers not covered by a block: {missing}")
    return failures
