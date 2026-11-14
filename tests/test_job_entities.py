"""Native controls address the app's shared draft, only in service mode."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.mennekes_amtron_rtu import button, datetime, number, sensor
from custom_components.mennekes_amtron_rtu.job_entities import JobNumber
from tests.ha_fakes import build_runtime, collect
from tests.test_service_orders import NOW, _with_app


def test_native_entities_share_the_real_app_draft(tmp_path):
    async def body(h, orders, _):
        hass, entry, _transport = await build_runtime()
        added = {}
        for platform in (button, datetime, number, sensor):
            await platform.async_setup_entry(
                hass, entry, lambda entities: added.update(collect(entities))
            )
        assert not any(key.startswith("job_") for key in added)
        orphan = JobNumber(entry, "current_a")
        with pytest.raises(ServiceValidationError):
            _ = orphan.orders
        entry.runtime_data.orders = orders
        added = {}
        for platform in (button, datetime, number, sensor):
            await platform.async_setup_entry(
                hass, entry, lambda entities: added.update(collect(entities))
            )
        entities = {
            key: value for key, value in added.items() if key.startswith("job_")
        }
        assert len(entities) == 10
        current, energy, reserve = (
            entities[f"job_{key}"] for key in ("current_a", "energy_kwh", "reserve_kwh")
        )
        finish, start = entities["job_finish_at"], entities["job_start_at"]
        status = entities["job_draft_status"]
        assert current.available
        assert status.available
        assert current.native_value == 6
        assert current.native_max_value == 16
        assert energy.native_max_value == 1000
        assert reserve.entity_registry_enabled_default is False
        assert start.entity_registry_enabled_default is False
        assert finish.native_value is None
        assert status.native_value == "needs_finish"
        await current.async_set_native_value(8)
        await energy.async_set_native_value(15)
        await finish.async_set_value(NOW + timedelta(hours=3))
        await start.async_set_value(NOW + timedelta(hours=1))
        assert current.native_value == 8
        assert finish.native_value == NOW + timedelta(hours=3)
        assert status.native_value == "ready"
        assert h.calls == []
        await entities["job_start"].async_press()
        assert h.jobs.job.energy.target_kwh == 15
        assert status.native_value == "job_exists"
        await entities["job_stop"].async_press()
        await entities["job_clear"].async_press()
        await entities["job_reset_draft"].async_press()
        assert h.jobs.job is None
        assert finish.native_value is None
        assert start.native_value is None
        assert current.native_value == 6
        # Read-only keeps the diagnostic status visible but disables edits.
        from dataclasses import replace

        control = entry.runtime_data.control
        control._options = replace(control._options, read_only=True)
        assert not current.available
        assert status.available
        control._options = replace(control._options, read_only=False)
        # Corrupt storage gives unknown values, with a recovery status.
        h.jobs.draft.storage_error = True
        await orders._refresh()
        assert current.native_value is None
        assert status.native_value == "job_storage_unavailable"
        orders._client.snapshot = {}
        assert not current.available
        assert status.native_value is None
        assert current.native_max_value == 6

    asyncio.run(_with_app(tmp_path, body))
