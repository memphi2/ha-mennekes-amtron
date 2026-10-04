"""Capability gating for the AMTRON register map.

Registers appear over several layout versions, and the dynamic phase switch
needs matching hardware. Gating lives here so a platform never has to reason
about firmware ages, and so an unsupported register is never polled, never
written and never exposed as an entity.
"""

from __future__ import annotations

from .enums import PhaseOptionsHardware
from .register_blocks import REGISTER_BLOCKS, RegisterBlock
from .registers import LAYOUT_V01_00, REGISTERS_BY_KEY

# Register keys that need more than a layout version.
PHASE_SWITCH_GATED_KEYS = frozenset({"requested_phases"})


def capability_is_supported(
    layout_version: int | None,
    phase_options_hw: int | None,
    key: str,
) -> bool:
    """Return true when this device supports a register key.

    An unknown layout version is treated as the oldest documented layout: the
    integration would rather hide a working entity than poll a register the
    device answers with an exception.
    """

    spec = REGISTERS_BY_KEY.get(key)
    if spec is None:
        return False
    if (layout_version or LAYOUT_V01_00) < spec.min_layout:
        return False
    if key in PHASE_SWITCH_GATED_KEYS:
        return phase_switch_is_supported(phase_options_hw)
    return True


def phase_switch_is_supported(phase_options_hw: int | None) -> bool:
    """Return true when the hardware can switch between 1 and 3 phases."""

    return phase_options_hw == PhaseOptionsHardware.ONE_OR_THREE_PHASES


def supported_blocks(layout_version: int | None) -> tuple[RegisterBlock, ...]:
    """Return the read blocks a device with this layout version answers."""

    layout = layout_version or LAYOUT_V01_00
    return tuple(block for block in REGISTER_BLOCKS if block.min_layout <= layout)


def supported_register_keys(
    layout_version: int | None,
    phase_options_hw: int | None,
) -> frozenset[str]:
    """Return every register key this device supports."""

    return frozenset(
        key
        for key in REGISTERS_BY_KEY
        if capability_is_supported(layout_version, phase_options_hw, key)
    )
