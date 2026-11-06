from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from unittest.mock import patch

from homeassistant.components.recorder import get_instance
from homeassistant.setup import async_setup_component

from custom_components.mennekes_amtron_rtu.binary_sensor import async_setup_entry
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.enums import EvseState
from tests.ha_fakes import FakeHass, build_runtime, collect


async def _sensors(hass: FakeHass | None = None, **kwargs: object):
    hass, entry, _transport = await build_runtime(hass=hass, **kwargs)
    added: dict[str, object] = {}
    await async_setup_entry(hass, entry, lambda items: added.update(collect(items)))
    return added, entry


def test_states_are_derived_from_the_registers() -> None:
    async def run() -> None:
        sensors, entry = await _sensors()
        values = entry.runtime_data.coordinator.data.values
        values["cp_state"] = 10  # Idle and CP A1 consistently mean unplugged.
        assert sensors["vehicle_connected"].is_on is False
        assert sensors["charging"].is_on is False
        assert sensors["downgrade_active"].is_on is False
        assert sensors["error"].is_on is False
        assert sensors["master_lost_fallback"].is_on is False
        assert sensors["authorization_enabled"].is_on is False
        assert sensors["grid_imbalance_enabled"].is_on is True
        assert sensors["cable_lock_enabled"].is_on is False
        assert sensors["phase_switch_capable"].is_on is True

        values["evse_state"] = EvseState.CHARGING
        values["cp_state"] = 28
        values["downgrade_status"] = 2
        values["error_code"] = 200
        assert sensors["vehicle_connected"].is_on is True
        assert sensors["charging"].is_on is True
        assert sensors["downgrade_active"].is_on is True
        assert sensors["error"].is_on is True

    asyncio.run(run())


def test_missing_values_yield_an_unknown_state() -> None:
    async def run() -> None:
        sensors, entry = await _sensors()
        values = entry.runtime_data.coordinator.data.values
        for key in ("evse_state", "downgrade_status", "error_code", "grid_imbalance"):
            values[key] = None
        assert sensors["vehicle_connected"].is_on is None
        assert sensors["charging"].is_on is None
        assert sensors["downgrade_active"].is_on is None
        assert sensors["error"].is_on is None
        assert sensors["grid_imbalance_enabled"].is_on is None

    asyncio.run(run())


def test_capability_gating_hides_unsupported_binary_sensors() -> None:
    async def run() -> None:
        sensors, _entry = await _sensors(layout=R.LAYOUT_V01_00)
        assert "master_lost_fallback" not in sensors
        assert "phase_switch_capable" not in sensors
        assert "vehicle_connected" in sensors

    asyncio.run(run())


def test_heartbeat_uses_actual_health_and_records_only_transitions(tmp_path):
    from custom_components.mennekes_amtron_rtu.device.const import (
        CONTROL_MODE_MASTER,
        DOMAIN,
    )
    from tests.fakes import FakeModbusClient, device_bank, raise_connection_error
    from tests.test_home_assistant_smoke import _boot

    async def run():
        target = tmp_path / "config"
        transport = FakeModbusClient(device_bank())
        with patch(
            "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
            return_value=transport,
        ):
            hass = await _boot(target)
            from homeassistant.helpers.recorder import async_initialize_recorder

            async_initialize_recorder(hass)
            try:
                assert await async_setup_component(hass, "recorder", {
                    "recorder": {"db_url": f"sqlite:///{target}/recorder.db",
                                 "auto_purge": False, "commit_interval": 1},
                })
                flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
                flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {"next_step_id": "serial"})
                await hass.config_entries.flow.async_configure(flow["flow_id"], {
                    "port": "/dev/fake", "device_id": 50, "baudrate": 57600,
                    "frame": "8n2", "search": "off",
                })
                await hass.async_block_till_done()
                entity_id = "binary_sensor.mennekes_amtron_heartbeat"
                assert hass.states.get(entity_id) is None  # No sender in read-only mode.
                entry = hass.config_entries.async_entries(DOMAIN)[0]
                hass.config_entries.async_update_entry(entry, options={"control_mode": CONTROL_MODE_MASTER})
                await hass.async_block_till_done()
                heartbeat = entry.runtime_data.heartbeat
                await heartbeat.async_stop()
                ticks, sleeps = asyncio.Queue(), asyncio.Queue()

                async def sleep(delay):
                    sleeps.put_nowait(delay)
                    await ticks.get()

                heartbeat._sleep = sleep
                heartbeat.start()
                await sleeps.get()
                recorder = get_instance(hass)
                await recorder.async_block_till_done()

                def rows():
                    with closing(sqlite3.connect(target / "recorder.db")) as conn:
                        return conn.execute(
                            "SELECT count(*), count(DISTINCT attributes_id) FROM states "
                            "JOIN states_meta USING(metadata_id) WHERE states_meta.entity_id = ?",
                            (entity_id,),
                        ).fetchone()

                before, attributes = rows()
                assert hass.states.get(entity_id).state == "off"
                for _ in range(100):
                    ticks.put_nowait(None)
                    await sleeps.get()
                await recorder.async_block_till_done()
                assert rows() == (before, attributes)

                transport.read_exceptions[0x0100] = raise_connection_error()
                await entry.runtime_data.coordinator.async_refresh()
                assert hass.states.get(entity_id).state == "off"  # Poll failure is independent.
                heartbeat._deadline_handle.cancel()
                heartbeat._deadline_expired()
                assert hass.states.get(entity_id).state == "on"
                ticks.put_nowait(None)
                await sleeps.get()
                assert hass.states.get(entity_id).state == "off"
                await recorder.async_block_till_done()
                assert rows() == (before + 2, attributes)

                assert await hass.config_entries.async_unload(entry.entry_id)
                assert not heartbeat._listeners
                assert heartbeat._deadline_handle is None
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))
