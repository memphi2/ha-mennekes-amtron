from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.mennekes_amtron_rtu import repairs
from custom_components.mennekes_amtron_rtu.device import registers as R
from tests.fakes import device_bank, put, raise_connection_error
from tests.ha_fakes import build_runtime


class FakeIssueRegistry:
    """Records what the integration would create and delete."""

    def __init__(self) -> None:
        self.created: dict[str, dict[str, object]] = {}
        self.deleted: list[str] = []

    def async_create_issue(self, _hass: object, _domain: str, issue_id: str, **kwargs):
        self.created[issue_id] = kwargs

    def async_delete_issue(self, _hass: object, _domain: str, issue_id: str) -> None:
        self.deleted.append(issue_id)
        self.created.pop(issue_id, None)


def _patch(monkeypatch: pytest.MonkeyPatch) -> FakeIssueRegistry:
    registry = FakeIssueRegistry()
    monkeypatch.setattr(repairs.ir, "async_create_issue", registry.async_create_issue)
    monkeypatch.setattr(repairs.ir, "async_delete_issue", registry.async_delete_issue)
    return registry


def test_a_healthy_wallbox_raises_no_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        registry = _patch(monkeypatch)
        hass, entry, _transport = await build_runtime()
        repairs.async_check_repairs(hass, entry)
        assert registry.created == {}
        assert len(registry.deleted) == 4

    asyncio.run(run())


def test_error_200_raises_a_fixable_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        registry = _patch(monkeypatch)
        hass, entry, _transport = await build_runtime(
            bank=device_bank(error_code=200)
        )
        repairs.async_check_repairs(hass, entry)
        issue = registry.created["entry-1_ems_heartbeat_lost"]
        assert issue["is_fixable"] is True
        assert issue["translation_key"] == "ems_heartbeat_lost"
        assert issue["data"]["entry_id"] == "entry-1"

    asyncio.run(run())


def test_the_remaining_conditions_raise_their_issues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        registry = _patch(monkeypatch)
        hass, entry, _transport = await build_runtime(
            bank=device_bank(layout=R.LAYOUT_V01_02, fallback_current=0),
            layout=R.LAYOUT_V01_02,
        )
        entry.runtime_data.diagnostics.writes_rejected = 1
        entry.runtime_data.diagnostics.write_errors.add("charging_release")
        repairs.async_check_repairs(hass, entry)
        assert "entry-1_ems_fallback_not_configured" in registry.created
        assert "entry-1_write_rejected" in registry.created
        layout_issue = registry.created["entry-1_unsupported_modbus_layout"]
        assert layout_issue["translation_placeholders"]["layout"] == "v01.02"

    asyncio.run(run())


def test_setup_subscribes_to_the_coordinator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        registry = _patch(monkeypatch)
        hass, entry, _transport = await build_runtime(
            bank=device_bank(error_code=200)
        )
        repairs.async_setup_repairs(hass, entry)
        assert entry.unloads
        assert "entry-1_ems_heartbeat_lost" in registry.created

    asyncio.run(run())


def test_an_unchanged_issue_is_not_written_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The coordinator fires every few seconds; the registry must not."""

    async def run() -> None:
        registry = _patch(monkeypatch)
        hass, entry, _transport = await build_runtime(
            bank=device_bank(error_code=200)
        )
        repairs.async_setup_repairs(hass, entry)
        registry.created.clear()
        registry.deleted.clear()

        for _ in range(5):
            entry.runtime_data.coordinator.async_update_listeners()
        assert registry.created == {}
        assert registry.deleted == []

        # but a real change still reaches the registry
        entry.runtime_data.coordinator.data.values["error_code"] = 0
        entry.runtime_data.coordinator.async_update_listeners()
        assert "entry-1_ems_heartbeat_lost" in registry.deleted

        entry.runtime_data.coordinator.data.values["error_code"] = 200
        entry.runtime_data.coordinator.async_update_listeners()
        assert "entry-1_ems_heartbeat_lost" in registry.created

    asyncio.run(run())


def test_changed_placeholders_are_written_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        registry = _patch(monkeypatch)
        hass, entry, _transport = await build_runtime(
            bank=device_bank(layout=R.LAYOUT_V01_02), layout=R.LAYOUT_V01_02
        )
        repairs.async_check_repairs(hass, entry)
        registry.created.clear()

        entry.runtime_data.identity.layout_version = R.LAYOUT_V01_01
        repairs.async_check_repairs(hass, entry)
        issue = registry.created["entry-1_unsupported_modbus_layout"]
        assert issue["translation_placeholders"]["layout"] == "v01.01"

    asyncio.run(run())


def test_a_stale_repair_closes_without_recovering_a_healthy_wallbox() -> None:
    async def run() -> None:
        hass, entry, transport = await build_runtime()
        flow = await repairs.async_create_fix_flow(
            hass, "entry-1_ems_heartbeat_lost", {"entry_id": entry.entry_id}
        )
        flow.hass = hass
        flow.async_show_form = lambda **kwargs: {"type": "form", **kwargs}
        flow.async_create_entry = lambda **kwargs: {"type": "create_entry", **kwargs}

        form = await flow.async_step_init()
        assert form["step_id"] == "confirm"

        result = await flow.async_step_confirm({})
        assert result["type"] == "create_entry"
        assert transport.writes == []

    asyncio.run(run())


def test_the_fix_flow_tolerates_a_missing_entry() -> None:
    async def run() -> None:
        hass, _entry, _transport = await build_runtime()
        flow = await repairs.async_create_fix_flow(hass, "x", None)
        flow.hass = hass
        flow.async_create_entry = lambda **kwargs: {"type": "create_entry"}
        assert (await flow.async_step_confirm({}))["type"] == "create_entry"

        hass.config_entries.entries["blank"] = SimpleNamespace()
        flow = await repairs.async_create_fix_flow(hass, "x", {"entry_id": "blank"})
        flow.hass = hass
        flow.async_create_entry = lambda **kwargs: {"type": "create_entry"}
        flow.async_show_form = lambda **kwargs: {"type": "form", **kwargs}
        assert (await flow.async_step_confirm({}))["errors"] == {"base": "entry_unavailable"}

    asyncio.run(run())


@pytest.mark.parametrize(("scenario", "error"), [
    ("rate_limit", "write_rate_limited"),
    ("read_only", "read_only"),
    ("read_failure", "recovery_failed"),
    ("write_failure", "recovery_failed"),
    ("different_error", "different_error"),
    ("still_active", "still_active"),
])
def test_expected_repair_failures_stay_in_the_dialog(monkeypatch, scenario, error):
    import time

    monkeypatch.setattr(repairs, "RECOVERY_CONFIRM_SECONDS", 0.04)
    monkeypatch.setattr(repairs, "RECOVERY_CONFIRM_INTERVAL", 0.01)

    async def run():
        hass, entry, transport = await build_runtime(
            bank=device_bank(error_code=2011 if scenario == "different_error" else 200),
            read_only=scenario == "read_only",
        )
        if scenario == "read_failure":
            transport.read_exceptions[R.ERROR_CODE.address] = raise_connection_error()
        elif scenario == "write_failure":
            transport.write_exceptions[R.HEARTBEAT.address] = raise_connection_error()
        elif scenario == "rate_limit":
            entry.runtime_data.client.write_state.limiter.record("charging_current_ems", now=time.monotonic())
        flow = await repairs.async_create_fix_flow(hass, "x", {"entry_id": entry.entry_id})
        flow.hass = hass
        flow.async_show_form = lambda **kwargs: {"type": "form", **kwargs}
        result = await flow.async_step_confirm({})
        assert result["type"] == "form"
        assert result["errors"] == {"base": error}
        await entry.runtime_data.client.async_close()

    asyncio.run(run())


@pytest.mark.parametrize("delay", [0, 0.02])
def test_repair_finishes_only_after_the_wallbox_confirms_recovery(monkeypatch, delay):
    monkeypatch.setattr(repairs, "RECOVERY_CONFIRM_INTERVAL", 0.01)

    async def run():
        hass, entry, transport = await build_runtime(bank=device_bank(error_code=200))
        original = transport.write_registers

        async def write(address, values, **kwargs):
            response = await original(address, values, **kwargs)
            if address == R.CHARGING_CURRENT_EMS.address:
                asyncio.get_running_loop().call_later(
                    delay, put, transport.bank, R.ERROR_CODE, 0
                )
            return response

        transport.write_registers = write
        flow = await repairs.async_create_fix_flow(hass, "x", {"entry_id": entry.entry_id})
        flow.hass = hass
        flow.async_create_entry = lambda **kwargs: {"type": "create_entry", **kwargs}
        flow.async_show_form = lambda **kwargs: {"type": "form", **kwargs}
        result = await flow.async_step_confirm({})
        assert result["type"] == "create_entry"
        assert [address for address, _ in transport.writes] == [
            R.HEARTBEAT.address, R.CHARGING_RELEASE.address, R.CHARGING_CURRENT_EMS.address,
        ]
        await entry.runtime_data.client.async_close()

    asyncio.run(run())


def test_a_different_fault_during_confirmation_is_not_treated_as_recovered():
    async def run():
        hass, entry, transport = await build_runtime(bank=device_bank(error_code=200))
        original = transport.write_registers

        async def write(address, values, **kwargs):
            response = await original(address, values, **kwargs)
            if address == R.CHARGING_CURRENT_EMS.address:
                put(transport.bank, R.ERROR_CODE, 2011)
            return response

        transport.write_registers = write
        flow = await repairs.async_create_fix_flow(hass, "x", {"entry_id": entry.entry_id})
        flow.hass = hass
        flow.async_show_form = lambda **kwargs: {"type": "form", **kwargs}
        result = await flow.async_step_confirm({})
        assert result["errors"] == {"base": "different_error"}
        assert entry.runtime_data.diagnostics.recoveries == 1
        await entry.runtime_data.client.async_close()

    asyncio.run(run())
