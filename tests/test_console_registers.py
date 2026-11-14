"""Console extensions preserve Ingress authorization and the single bus owner."""

from __future__ import annotations

import asyncio
from copy import deepcopy

from aiohttp.test_utils import TestClient, TestServer

from custom_components.mennekes_amtron_rtu.device import app_settings as ui
from custom_components.mennekes_amtron_rtu.device.client import (
    MennekesModbusClient,
    SerialConfig,
)
from custom_components.mennekes_amtron_rtu.device.console_http import (
    ServiceConsole,
    create_console_app,
)
from custom_components.mennekes_amtron_rtu.device.service_config import ServerConfig
from custom_components.mennekes_amtron_rtu.device.service_console_runtime import (
    ConsoleModbusService,
)
from tests.fakes import FakeModbusClient, device_bank
from tests.test_app_settings import BASE, HEADERS, TOKEN, FixtureSettings


class FixtureConsole(ServiceConsole):
    def __init__(self, service):
        super().__init__("isolated-supervisor", TOKEN, service=service)
        self.options, self.version, self.calls = deepcopy(BASE), "2026.11.0", []

    _request = FixtureSettings._request


async def console_fixture(tmp_path):
    transport = FakeModbusClient(device_bank(charging_current=8))
    client = MennekesModbusClient(SerialConfig("/dev/fake"), client_factory=lambda _: transport)
    service = ConsoleModbusService(ServerConfig(client.config, TOKEN, current_cap=16), tmp_path / "intent.json", client=client)
    await service._initialize()
    return service, transport


def test_console_read_only_route_and_packaged_resources(tmp_path, monkeypatch):
    async def run():
        service, transport = await console_fixture(tmp_path)
        try:
            settings = FixtureConsole(service)
            async with TestClient(TestServer(create_console_app(settings))) as client:
                assert (await client.get("/api/registers", headers=HEADERS)).status == 403
                monkeypatch.setattr(ui, "SUPERVISOR_PEER", "127.0.0.1")
                assert (await client.get("/api/registers")).status == 403
                before = (list(transport.reads), list(transport.writes))
                result = await client.get("/api/registers", headers=HEADERS)
                assert result.status == 200
                assert (await result.json())["schema"] == 1
                assert (transport.reads, transport.writes) == before
                assert result.headers["Cache-Control"] == "no-store"
                assert (await client.post("/api/registers", headers=HEADERS, json={})).status == 405
                assert (await client.get("/api/registers?refresh=1", headers=HEADERS)).status == 400
                asset = await client.get("/app_registers.js")
                assert asset.status == 200
                assert await asset.text()
                # The registers are part of the overview; their own page is gone.
                assert (await client.get("/registers")).status == 404
                assert 'id="register_rows"' in await (await client.get("/")).text()
                # Every console page gets the browser language; scripts read it.
                for path in ("/", "/runtime", "/jobs", "/guest"):
                    page = await client.get(path, headers={"Accept-Language": "fr-CH,fr;q=0.9,en;q=0.8"})
                    assert '<html lang="fr">' in await page.text()
                    fallback = await client.get(path, headers={"Accept-Language": "xx,<script>"})
                    assert '<html lang="en">' in await fallback.text()
                script = await client.get("/app_jobs.js", headers={"Accept-Language": "fr"})
                assert 'lang="fr"' not in await script.text()
                settings.service = None
                assert (await client.get("/api/registers", headers=HEADERS)).status == 503
        finally:
            await service.stop()
    asyncio.run(run())
