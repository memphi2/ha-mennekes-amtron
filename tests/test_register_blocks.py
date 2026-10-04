from __future__ import annotations

from custom_components.mennekes_amtron.register_blocks import (
    REGISTER_BLOCKS,
    RegisterBlock,
    block_consistency_failures,
)
from custom_components.mennekes_amtron.registers import READABLE_REGISTERS


def test_declared_blocks_match_the_register_map() -> None:
    assert block_consistency_failures() == []


def test_every_readable_register_is_in_exactly_one_block() -> None:
    covered = [key for block in REGISTER_BLOCKS for key in block.keys]
    assert sorted(covered) == sorted(spec.key for spec in READABLE_REGISTERS)
    assert len(covered) == len(set(covered))


def test_specs_resolve_in_block_order() -> None:
    status = next(block for block in REGISTER_BLOCKS if block.name == "status")
    assert [spec.key for spec in status.specs] == list(status.keys)


def test_consistency_rule_rejects_a_gap() -> None:
    broken = RegisterBlock(
        name="broken",
        address=0x0100,
        count=4,
        min_layout=0x0100,
        keys=("evse_state", "phase_rotation"),
    )
    failures = _failures_for(broken)
    assert any("but the map says" in failure for failure in failures)


def test_consistency_rule_rejects_a_wrong_count() -> None:
    broken = RegisterBlock(
        name="broken",
        address=0x0100,
        count=9,
        min_layout=0x0100,
        keys=("evse_state", "authorization_status"),
    )
    assert any("declares count 9" in failure for failure in _failures_for(broken))


def test_consistency_rule_rejects_an_unknown_register() -> None:
    broken = RegisterBlock(
        name="broken", address=0x0100, count=1, min_layout=0x0100, keys=("nope",)
    )
    assert any("unknown register" in failure for failure in _failures_for(broken))


def test_consistency_rule_rejects_a_write_only_register() -> None:
    broken = RegisterBlock(
        name="broken", address=0x0D00, count=1, min_layout=0x0100, keys=("heartbeat",)
    )
    assert any("write-only" in failure for failure in _failures_for(broken))


def test_consistency_rule_rejects_a_layout_mismatch() -> None:
    broken = RegisterBlock(
        name="broken", address=0x0108, count=1, min_layout=0x0100, keys=("cp_state",)
    )
    assert any("declares layout" in failure for failure in _failures_for(broken))


def test_consistency_rule_rejects_an_empty_block() -> None:
    broken = RegisterBlock(
        name="empty", address=0x0100, count=0, min_layout=0x0100, keys=()
    )
    assert any("covers no register" in failure for failure in _failures_for(broken))


def test_consistency_rule_rejects_an_oversized_block() -> None:
    broken = RegisterBlock(
        name="measurements",
        address=0x0500,
        count=400,
        min_layout=0x0100,
        keys=("current_l1",),
    )
    assert any("register cap" in failure for failure in _failures_for(broken))


def _failures_for(block: RegisterBlock) -> list[str]:
    from custom_components.mennekes_amtron import register_blocks

    original = register_blocks.REGISTER_BLOCKS
    register_blocks.REGISTER_BLOCKS = (block,)
    try:
        return register_blocks.block_consistency_failures()
    finally:
        register_blocks.REGISTER_BLOCKS = original


def test_consistency_rule_rejects_a_register_in_two_blocks() -> None:
    from custom_components.mennekes_amtron import register_blocks

    duplicate = RegisterBlock(
        name="duplicate",
        address=0x0100,
        count=1,
        min_layout=0x0100,
        keys=("evse_state",),
    )
    original = register_blocks.REGISTER_BLOCKS
    register_blocks.REGISTER_BLOCKS = (*original, duplicate)
    try:
        failures = register_blocks.block_consistency_failures()
    finally:
        register_blocks.REGISTER_BLOCKS = original
    assert any("covered by both" in failure for failure in failures)
