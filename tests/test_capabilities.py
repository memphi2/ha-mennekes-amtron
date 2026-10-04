from __future__ import annotations

from custom_components.mennekes_amtron.capabilities import (
    capability_is_supported,
    phase_switch_is_supported,
    supported_blocks,
    supported_register_keys,
)
from custom_components.mennekes_amtron.registers import (
    LAYOUT_V01_00,
    LAYOUT_V01_01,
    LAYOUT_V01_02,
    LAYOUT_V01_03,
)


def test_older_layouts_hide_newer_registers() -> None:
    assert not capability_is_supported(LAYOUT_V01_00, 2, "serial_number")
    assert capability_is_supported(LAYOUT_V01_02, 2, "serial_number")
    assert not capability_is_supported(LAYOUT_V01_02, 2, "article_number")
    assert capability_is_supported(LAYOUT_V01_03, 2, "article_number")
    assert not capability_is_supported(LAYOUT_V01_00, 2, "phase_options_hw")
    assert capability_is_supported(LAYOUT_V01_01, 2, "phase_options_hw")


def test_an_unknown_layout_falls_back_to_the_oldest_one() -> None:
    assert capability_is_supported(None, 2, "evse_state")
    assert not capability_is_supported(None, 2, "temperature")


def test_an_unknown_register_is_never_supported() -> None:
    assert not capability_is_supported(LAYOUT_V01_03, 2, "does_not_exist")


def test_phase_switching_needs_the_hardware_option() -> None:
    assert phase_switch_is_supported(2)
    assert not phase_switch_is_supported(1)
    assert not phase_switch_is_supported(None)
    assert capability_is_supported(LAYOUT_V01_03, 2, "requested_phases")
    assert not capability_is_supported(LAYOUT_V01_03, 0, "requested_phases")


def test_supported_blocks_grow_with_the_layout_version() -> None:
    oldest = {block.name for block in supported_blocks(LAYOUT_V01_00)}
    newest = {block.name for block in supported_blocks(LAYOUT_V01_03)}
    assert "statistics" not in oldest
    assert "article_number" not in oldest
    assert "statistics" in newest
    assert oldest < newest


def test_supported_register_keys_respect_both_gates() -> None:
    keys = supported_register_keys(LAYOUT_V01_02, 1)
    assert "requested_phases" not in keys
    assert "article_number" not in keys
    assert "temperature" in keys
