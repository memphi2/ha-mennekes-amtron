"""Exercise current-action authorization through the real HA service registry."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.auth import auth_manager_from_config
from homeassistant.auth.const import GROUP_ID_READ_ONLY, GROUP_ID_USER
from homeassistant.core import Context
from homeassistant.exceptions import Unauthorized, UnknownUser
from homeassistant.helpers import device_registry as dr

from custom_components.mennekes_amtron_rtu.const import CONTROL_MODE_MASTER, DOMAIN
from tests.fakes import FakeModbusClient, device_bank
from tests.test_home_assistant_smoke import _boot


@pytest.mark.parametrize("group", [GROUP_ID_READ_ONLY, GROUP_ID_USER, "unknown"])
@pytest.mark.parametrize("current", [0, 10])
def test_current_action_respects_user_permissions(
    tmp_path: Path, group: str, current: int
) -> None:
    async def run() -> None:
        clients: list[FakeModbusClient] = []

        def factory(*args, **kwargs):
            client = FakeModbusClient(device_bank())
            clients.append(client)
            return client

        with patch(
            "custom_components.mennekes_amtron_rtu.client.AsyncModbusSerialClient",
            side_effect=factory,
        ):
            hass = await _boot(tmp_path / "config")
            try:
                hass.auth = await auth_manager_from_config(hass, [], [])
                await hass.auth.async_create_user("Test owner")
                user = await hass.auth.async_create_user(
                    "Test user",
                    group_ids=[GROUP_ID_READ_ONLY if group == "unknown" else group],
                )
                flow = await hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": "user"}
                )
                await hass.config_entries.flow.async_configure(
                    flow["flow_id"],
                    {
                        "port": "/dev/fake",
                        "device_id": 50,
                        "baudrate": 57600,
                        "frame": "8n2",
                        "search": "off",
                    },
                )
                await hass.async_block_till_done()
                entry = hass.config_entries.async_entries(DOMAIN)[0]
                hass.config_entries.async_update_entry(
                    entry, options={"control_mode": CONTROL_MODE_MASTER}
                )
                await hass.async_block_till_done()
                device = dr.async_entries_for_config_entry(
                    dr.async_get(hass), entry.entry_id
                )[0]
                call = hass.services.async_call(
                    DOMAIN,
                    "set_charging_current",
                    {
                        "device_id": device.id,
                        "current": current,
                        "allow_unlimited": True,
                    },
                    blocking=True,
                    context=Context(
                        user_id="missing-user" if group == "unknown" else user.id
                    ),
                )
                if group == GROUP_ID_USER:
                    await call
                    if pending := entry.runtime_data.control._flush_task:
                        await pending
                else:
                    with pytest.raises(
                        UnknownUser if group == "unknown" else Unauthorized
                    ):
                        await call
                    if pending := entry.runtime_data.control._flush_task:
                        await pending
                writes = [
                    words
                    for client in clients
                    for address, words in client.writes
                    if address == 0x0302
                ]
                assert bool(writes) == (group == GROUP_ID_USER)
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), timeout=60))
