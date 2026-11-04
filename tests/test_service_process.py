"""Run the actual service process over a virtual serial bus while HA restarts."""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import sys
from contextlib import suppress
from pathlib import Path

from aiohttp import ClientSession
from homeassistant.setup import async_setup_component

from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.remote import RemoteClient
from tests.fakes import device_bank
from tests.test_home_assistant_smoke import _boot, _start_wallbox
from tests.test_modbus_service import TOKEN
from tests.test_serial_loopback import _virtual_serial_link

ROOT = Path(__file__).resolve().parents[1]


def test_independent_process_keeps_pause_and_heartbeat_across_core_restart(
    tmp_path, request
):
    # Mirror the standalone wheel's package layout. Running inside the HA
    # platform directory shadows the stdlib select module on some Python builds.
    service_root = tmp_path / "service"
    shutil.copytree(
        ROOT / "custom_components" / DOMAIN / "device",
        service_root / "amtron_device",
    )

    async def run():
        device_port, client_port = _virtual_serial_link(request)
        wallbox, serving = await _start_wallbox(
            device_port, device_bank(charging_current=8)
        )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        config_path = tmp_path / "options.json"
        config_path.write_text(
            json.dumps(
                {
                    "serial_port": client_port,
                    "token": TOKEN,
                    "read_only": False,
                    "current_cap": 16,
                    "port": port,
                }
            )
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "amtron_device.server",
            "--config",
            str(config_path),
            "--state",
            str(tmp_path / "intent.json"),
            cwd=service_root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with ClientSession() as session:
                remote = RemoteClient(session, f"http://127.0.0.1:{port}", TOKEN)

                async def ready():
                    while True:
                        try:
                            await remote.async_connect()
                            if remote.snapshot["heartbeat"]["healthy"]:
                                return
                        except AmtronConnectionError:
                            if process.returncode is not None:
                                _out, error = await process.communicate()
                                raise AssertionError(error.decode()) from None
                        await asyncio.sleep(0.1)

                await asyncio.wait_for(ready(), 20)
                # The service's cold-start current floor is still enforced.
                await asyncio.sleep(5)
                config = tmp_path / "ha"
                hass = await _boot(config)
                assert await async_setup_component(hass, "network", {})
                try:
                    flow = await hass.config_entries.flow.async_init(
                        DOMAIN, context={"source": "user"}
                    )
                    flow = await hass.config_entries.flow.async_configure(
                        flow["flow_id"], {"next_step_id": "server"}
                    )
                    result = await hass.config_entries.flow.async_configure(
                        flow["flow_id"],
                        {
                            "server_url": f"http://127.0.0.1:{port}",
                            "server_token": TOKEN,
                        },
                    )
                    assert result["type"] == "create_entry"
                    await hass.async_block_till_done()
                    await hass.services.async_call(
                        "switch",
                        "turn_on",
                        {"entity_id": "switch.mennekes_amtron_charging_paused"},
                        blocking=True,
                    )
                    await remote.update()
                    assert remote.write_state.pause_requested is True
                    before = remote.snapshot["diagnostics"]["heartbeats_sent"]
                    commands = remote.snapshot["diagnostics"]["writes_sent"]
                finally:
                    await hass.async_stop()
                await asyncio.sleep(5.5)
                await remote.update()
                assert remote.snapshot["diagnostics"]["heartbeats_sent"] > before
                assert remote.snapshot["heartbeat"]["healthy"] is True
                assert remote.snapshot["values"]["charging_current_ems"] == 1
                assert process.returncode is None
                shutil.rmtree(config / "custom_components" / DOMAIN)
                hass = await _boot(config)
                assert await async_setup_component(hass, "network", {})
                try:
                    entry = hass.config_entries.async_entries(DOMAIN)[0]
                    assert await hass.config_entries.async_setup(entry.entry_id)
                    await hass.async_block_till_done()
                    assert (
                        hass.states.get("switch.mennekes_amtron_charging_paused").state
                        == "on"
                    )
                    await remote.update()
                    assert remote.snapshot["diagnostics"]["writes_sent"] == commands
                    assert remote.snapshot["diagnostics"]["heartbeats_late"] == 0
                    assert remote.snapshot["values"]["charging_current_ems"] == 1
                finally:
                    await hass.async_stop()
        finally:
            if process.returncode is None:
                process.terminate()
            try:
                await asyncio.wait_for(process.communicate(), 20)
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                await wallbox.shutdown()
                serving.cancel()
                with suppress(asyncio.CancelledError):
                    await serving
        assert process.returncode == 0

    asyncio.run(asyncio.wait_for(run(), 90))
