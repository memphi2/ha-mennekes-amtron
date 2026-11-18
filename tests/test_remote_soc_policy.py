"""A service boot changes internal metadata even when every register is unchanged."""
from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest
from aiohttp import ClientSession

from custom_components.mennekes_amtron_rtu.service_client import ServiceClient
from custom_components.mennekes_amtron_rtu.service_control import ServiceControl
from custom_components.mennekes_amtron_rtu.service_errors import ServiceConnectionError
from tests.test_modbus_service import TOKEN, service_fixture


def test_cap_policy_loss_notifies_ha_without_register_changes(tmp_path):
    async def body():
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            async with ClientSession() as session:
                client = ServiceClient(session, "http://service:8765", TOKEN)
                control = ServiceControl(client,                                         state=lambda: None, request_refresh=lambda: None)
                payload = service.snapshot()
                payload["control"]["soc_maximum_required"] = True
                before = client.apply_snapshot(payload)
                assert not control.soc_configuration_required
                assert before == client.apply_snapshot(deepcopy(payload))
                payload["control"]["soc_maximum_required"] = None
                after = client.apply_snapshot(payload)
                assert before.values == after.values
                assert before != after
                assert control.soc_configuration_required
                retained = client.snapshot
                for invalid in [1, "false", {}, []]:
                    broken = deepcopy(payload)
                    broken["control"]["soc_maximum_required"] = invalid
                    with pytest.raises(ServiceConnectionError, match=r"control\.soc_maximum_required"):
                        client.apply_snapshot(broken)
                    assert client.snapshot is retained
        finally:
            await service.stop()
    asyncio.run(body())
