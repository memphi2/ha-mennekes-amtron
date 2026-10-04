from __future__ import annotations

from custom_components.mennekes_amtron.data import (
    ConnectionState,
    DeviceIdentity,
    WallboxData,
    WriteDiagnostics,
)
from custom_components.mennekes_amtron.registers import LAYOUT_V01_00


def test_snapshot_reports_missing_values() -> None:
    data = WallboxData(values={"evse_state": 5, "temperature": None})
    assert data.get("evse_state") == 5
    assert data.has("evse_state")
    assert not data.has("temperature")
    assert not data.has("absent")
    assert data.get("absent") is None


def test_empty_snapshot_has_nothing() -> None:
    assert WallboxData().get("evse_state") is None
    assert WallboxData().failed_blocks == ()


def test_connection_state_reports_only_changes() -> None:
    state = ConnectionState()
    assert not state.record(True)
    assert state.record(False)
    assert not state.record(False)
    assert state.record(True)
    assert state.available


def test_identity_defaults_to_the_oldest_layout() -> None:
    identity = DeviceIdentity()
    assert identity.layout_version == LAYOUT_V01_00
    assert identity.serial_number is None


def test_write_diagnostics_start_at_zero() -> None:
    diagnostics = WriteDiagnostics()
    assert diagnostics.writes_sent == 0
    assert diagnostics.last_error is None
