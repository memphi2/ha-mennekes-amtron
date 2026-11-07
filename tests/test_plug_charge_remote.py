"""The optional grant contract crosses the real authenticated service boundary."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.mennekes_amtron_rtu.control import ControlOptions
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.data import (
    WallboxData,
    WriteDiagnostics,
)
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.remote import (
    RemoteClient,
    RemoteControl,
    ServiceConnectionError,
)
from tests.test_modbus_service import TOKEN, service_fixture

POLICY = "a" * 64


def remote_control(remote):
    return RemoteControl(
        remote, diagnostics=WriteDiagnostics(), options=ControlOptions(False, 16),
        state=lambda: WallboxData(values=remote.snapshot["values"]), request_refresh=remote.update,
    )


@pytest.mark.parametrize("available", [True, False])
def test_read_only_app_can_disable_restored_gate_without_bus_access(tmp_path, available):
    async def run():
        service, transport, _ = await service_fixture(tmp_path, read_only=True)
        try:
            # This is the durable policy restored after the app was changed to
            # read-only. It deliberately has no surviving charging grant.
            service.client.write_state.plug_charge.configure(True, POLICY)
            async with TestServer(create_app(service)) as server, ClientSession() as session:
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                control = remote_control(remote)
                with pytest.raises(ServiceValidationError) as error:
                    await control.async_configure_plug_charge(True, policy=POLICY)
                assert error.value.translation_key == "read_only_mode"
                if not available:
                    service._success = False
                    with pytest.raises(ServiceConnectionError):
                        await remote.update()
                reads, writes = list(transport.reads), list(transport.writes)
                await control.async_configure_plug_charge(False, policy=POLICY)
                assert not control.plug_charge["enabled"]
                assert service.client.write_state.plug_charge.hold
                assert not control.plug_charge["authorized"]
                assert transport.reads == reads
                assert transport.writes == writes
                assert remote.snapshot["available"] is available
        finally:
            await service.stop()

    asyncio.run(run())


def test_gate_grant_survives_ha_reconnect_but_not_unplug_and_disable_does_not_resume(tmp_path):
    async def run():
        service, transport, clock = await service_fixture(tmp_path)
        try:
            async with TestServer(create_app(service)) as server, ClientSession() as session:
                remote = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await remote.async_connect()
                control = remote_control(remote)
                assert control.supports_plug_charge
                await control.async_configure_plug_charge(True, policy=POLICY)
                assert transport.bank[R.CHARGING_RELEASE.address] == 0
                transport.bank[R.EVSE_STATE.address] = 2
                transport.bank[R.CP_STATE.address] = 11
                transport.bank[R.LOCK_EVSE.address] = 0
                transport.bank[R.AUTHORIZATION_ENABLED.address] = 0
                await service.refresh()
                await remote.update()
                token = control.plug_charge["session"]
                assert isinstance(token, str)
                clock[0] += 301
                await control.async_authorize_plug_charge(token, deadline=datetime.now(UTC) + timedelta(seconds=10))
                assert control.plug_charge["authorized"]
                assert transport.bank[R.CHARGING_RELEASE.address] == 1
                await remote.async_close()
                fresh = RemoteClient(session, str(server.make_url("/")), TOKEN)
                await fresh.async_connect()
                restored = remote_control(fresh)
                await restored.async_configure_plug_charge(True, policy=POLICY)
                assert restored.plug_charge["authorized"]
                assert restored.plug_charge["session"] == token
                transport.bank[R.EVSE_STATE.address] = 1
                transport.bank[R.CP_STATE.address] = 10
                clock[0] += 6
                await service.refresh()
                await service.control.async_sync_plug_charge()
                await fresh.update()
                assert restored.plug_charge["session"] is None
                assert not restored.plug_charge["authorized"]
                assert transport.bank[R.CHARGING_RELEASE.address] == 0
                with pytest.raises(HomeAssistantError):
                    await restored.async_authorize_plug_charge(token, deadline=datetime.now(UTC) + timedelta(seconds=10))
                await restored.async_configure_plug_charge(False, policy=POLICY)
                assert not restored.plug_charge["enabled"]
                assert transport.bank[R.CHARGING_RELEASE.address] == 0
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize(("key", "value"), [
    ("supports_plug_charge", 1),
    ("plug_charge", None),
    ("plug_charge", {}),
    ("plug_charge.enabled", 1),
    ("plug_charge.authorized", 1),
    ("plug_charge.policy", 1),
    ("plug_charge.policy", "x" * 64),
    ("plug_charge.policy", "a" * 63),
    ("plug_charge.session", 1),
    ("plug_charge.session", "x" * 32),
    ("plug_charge.session", "a" * 31),
    ("plug_charge.authorized", True),
    ("plug_charge.since", "2026-11-08T10:00:00+00:00"),
    *(("plug_charge", {
        "enabled": True, "policy": POLICY, "session": "b" * 32,
        "authorized": False, "since": invalid,
    }) for invalid in (None, 1, "invalid", "2026-11-08T10:00:00", "a" * 65)),
])
def test_invalid_gate_snapshot_never_becomes_authoritative(tmp_path, key, value):
    async def run():
        service, _, _ = await service_fixture(tmp_path)
        try:
            async with ClientSession() as session:
                remote = RemoteClient(session, "http://service:8765", TOKEN)
                valid = service.snapshot()
                remote.apply_snapshot(valid)
                bad = deepcopy(valid)
                parent = bad["control"]
                path = key.split(".")
                for part in path[:-1]:
                    parent = parent[part]
                parent[path[-1]] = value
                with pytest.raises(ServiceConnectionError, match="invalid service snapshot"):
                    remote.apply_snapshot(bad)
                assert remote.snapshot is valid
                assert not remote.connected
        finally:
            await service.stop()

    asyncio.run(run())


def test_older_app_remains_readable_but_cannot_claim_virtual_authorization(tmp_path):
    async def run():
        service, _, _ = await service_fixture(tmp_path)
        try:
            async with ClientSession() as session:
                remote = RemoteClient(session, "http://service:8765", TOKEN)
                old = service.snapshot()
                old["control"].pop("supports_plug_charge")
                old["control"].pop("plug_charge")
                remote.apply_snapshot(old)
                control = remote_control(remote)
                assert not control.supports_plug_charge
                assert control.plug_charge == {"enabled": False, "policy": "", "session": None, "since": None, "authorized": False}
                for action in (
                    control.async_configure_plug_charge(True, policy=POLICY),
                    control.async_authorize_plug_charge("a" * 32, deadline=datetime.now(UTC) + timedelta(seconds=10)),
                ):
                    with pytest.raises(ServiceValidationError) as error:
                        await action
                    assert error.value.translation_key == "plug_charge_not_supported"
        finally:
            await service.stop()

    asyncio.run(run())
