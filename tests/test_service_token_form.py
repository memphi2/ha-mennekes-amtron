"""The reconfigure form of the service connection never carries the stored token."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from custom_components.mennekes_amtron_rtu import config_flow
from custom_components.mennekes_amtron_rtu.service_client import (
    CONF_SERVER_TOKEN,
    CONF_SERVER_URL,
)

SECRET = "stored-" + "x" * 40


def test_the_reconfigure_form_shows_the_url_and_not_the_stored_token(monkeypatch):
    async def run():
        flow = config_flow.MennekesAmtronConfigFlow()
        flow.hass = SimpleNamespace()
        flow.context = {"source": "reconfigure", "entry_id": "e"}
        entry = SimpleNamespace(data={CONF_SERVER_URL: "http://svc:8765", CONF_SERVER_TOKEN: SECRET})
        monkeypatch.setattr(flow, "_get_reconfigure_entry", lambda: entry)

        async def none(_hass):
            return []

        monkeypatch.setattr(config_flow, "async_app_discoveries", none)
        result = await flow.async_step_server(None)
        suggested = {
            str(marker): (marker.description or {}).get("suggested_value")
            for marker in result["data_schema"].schema
        }
        assert suggested == {CONF_SERVER_URL: "http://svc:8765", CONF_SERVER_TOKEN: None}

    asyncio.run(run())
