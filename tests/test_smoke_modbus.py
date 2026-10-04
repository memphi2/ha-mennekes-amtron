"""Tests for the bus check that ships with the integration.

The check runs on the machine the USB adapter is plugged into, usually inside
Home Assistant's own container, long before an entry exists. It therefore
imports nothing from the integration, and these tests drive it the same way a
user does: through its command line, with the pymodbus transport replaced.
"""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.mennekes_amtron import registers as R
from custom_components.mennekes_amtron import smoke_modbus
from tests.fakes import FakeModbusClient, device_bank


def _install(
    monkeypatch: pytest.MonkeyPatch,
    transport: FakeModbusClient,
    *,
    device_id: int = 50,
) -> list[dict[str, Any]]:
    """Replace the pymodbus client and record how it was built."""

    built: list[dict[str, Any]] = []

    def factory(port: str, **kwargs: Any) -> FakeModbusClient:
        built.append({"port": port, **kwargs})
        transport.device_id = device_id
        return transport

    monkeypatch.setattr(smoke_modbus, "AsyncModbusSerialClient", factory)
    return built


class _AddressedClient(FakeModbusClient):
    """A wallbox that answers on exactly one device address."""

    device_id = 50

    async def read_holding_registers(
        self, address: int, *, count: int = 1, device_id: int = 1
    ) -> Any:
        if device_id != self.device_id:
            return None
        return await super().read_holding_registers(
            address, count=count, device_id=device_id
        )


def test_a_healthy_wallbox_is_reported_in_full(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    built = _install(monkeypatch, _AddressedClient(device_bank(evse_state=5)))

    assert smoke_modbus.main(["/dev/fake"]) == 0

    out = capsys.readouterr().out
    assert "modbus layout    v01.03" in out
    assert "serial number    ABC123456789" in out
    assert "article number   1313201205" in out
    assert "evse state       5 (charging)" in out
    assert "max EVSE current 16.0 A" in out
    assert "The bus is fine" in out
    assert built[0]["baudrate"] == 57600
    assert built[0]["stopbits"] == 2
    assert built[0]["timeout"] == 1.0


def test_a_port_that_cannot_be_opened_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = _AddressedClient(device_bank())
    transport.connect_result = False
    _install(monkeypatch, transport)

    assert smoke_modbus.main(["/dev/fake"]) == 1
    assert "cannot open /dev/fake" in capsys.readouterr().err


def test_a_silent_device_names_the_dip_switches(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, _AddressedClient({R.MODBUS_LAYOUT_VERSION.address: 0}))

    assert smoke_modbus.main(["/dev/fake"]) == 1
    assert "DIP 4 and DIP 5" in capsys.readouterr().err


def test_a_broken_transport_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = _AddressedClient(device_bank())
    transport.connect_error = OSError("no such device")
    _install(monkeypatch, transport)

    assert smoke_modbus.main(["/dev/fake"]) == 1
    assert "no such device" in capsys.readouterr().err


def test_the_scan_finds_a_wallbox_on_another_address(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, _AddressedClient(device_bank()), device_id=23)

    assert smoke_modbus.main(["/dev/fake", "--scan"]) == 0

    out = capsys.readouterr().out
    assert "attempts, about" in out
    assert "found a wallbox at device address 23" in out
    assert "device address   23" in out


def test_the_scan_reports_an_empty_bus(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, _AddressedClient({}), device_id=99)

    assert smoke_modbus.main(["/dev/fake", "--scan", "--scan-baudrate"]) == 1

    out = capsys.readouterr().out
    assert "no wallbox answered" in out
    assert "Modbus A is +" in out
    assert out.count("scanning at") == len(smoke_modbus.BAUDRATES)


def test_the_scan_waits_less_per_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built = _install(monkeypatch, _AddressedClient(device_bank()), device_id=10)

    assert smoke_modbus.main(["/dev/fake", "--scan"]) == 0
    assert built[0]["timeout"] == 0.5
    assert built[0]["retries"] == 1


def test_an_explicit_timeout_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    built = _install(monkeypatch, _AddressedClient(device_bank()), device_id=10)

    assert smoke_modbus.main(["/dev/fake", "--scan", "--timeout", "2"]) == 0
    assert built[0]["timeout"] == 2.0


def test_registers_the_device_does_not_answer_are_left_out(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The layout version is mandatory; everything after it is a bonus."""

    class PartialClient(_AddressedClient):
        async def read_holding_registers(
            self, address: int, *, count: int = 1, device_id: int = 1
        ) -> Any:
            if address == R.SERIAL_NUMBER.address:
                # a short frame
                return await FakeModbusClient.read_holding_registers(
                    self, address, count=count - 1, device_id=device_id
                )
            if address in (R.MAX_EVSE_CURRENT.address, R.EVSE_STATE.address):
                return None
            return await super().read_holding_registers(
                address, count=count, device_id=device_id
            )

    _install(monkeypatch, PartialClient(device_bank()))

    assert smoke_modbus.main(["/dev/fake"]) == 0

    out = capsys.readouterr().out
    assert "serial number    -" in out
    assert "max EVSE current" not in out
    assert "evse state" not in out
    assert "The bus is fine" in out


def test_every_documented_evse_state_has_a_label() -> None:
    from custom_components.mennekes_amtron.enums import EvseState

    assert set(smoke_modbus.EVSE_STATES) == {int(state) for state in EvseState}
    assert smoke_modbus.EVSE_STATES[5] == "charging"
    assert list(smoke_modbus.DEVICE_ID_RANGE) == list(range(10, 51))
