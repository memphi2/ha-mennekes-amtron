from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.mennekes_amtron import registers as R
from custom_components.mennekes_amtron import repairs
from tests.fakes import device_bank
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


def test_the_fix_flow_runs_the_recovery_sequence() -> None:
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
        assert [address for address, _ in transport.writes] == [
            R.HEARTBEAT.address,
            R.CHARGING_RELEASE.address,
            R.CHARGING_CURRENT_EMS.address,
        ]

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
        assert (await flow.async_step_confirm({}))["type"] == "create_entry"

    asyncio.run(run())
