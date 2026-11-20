"""The reconfigure form of the service connection never carries the stored token."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

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
        # A failed attempt shows the typed address again, never the typed token.
        result = await flow.async_step_server({CONF_SERVER_URL: "http://other:8765", CONF_SERVER_TOKEN: "short"})
        assert result["errors"]
        suggested = {
            str(marker): (marker.description or {}).get("suggested_value")
            for marker in result["data_schema"].schema
        }
        assert suggested == {CONF_SERVER_URL: "http://other:8765", CONF_SERVER_TOKEN: None}

    asyncio.run(run())


@pytest.mark.parametrize("case", ["new_entry", "direct_connection"])
def test_a_form_without_a_stored_service_suggests_nothing(monkeypatch, case):
    async def run():
        flow = config_flow.MennekesAmtronConfigFlow()
        flow.hass = SimpleNamespace()
        if case == "new_entry":
            flow.context = {"source": "user"}
        else:
            # A direct serial entry that moves to the service.
            flow.context = {"source": "reconfigure", "entry_id": "e"}
            entry = SimpleNamespace(data={"port": "/dev/ttyUSB0", "serial_number": "1"})
            monkeypatch.setattr(flow, "_get_reconfigure_entry", lambda: entry)

        async def none(_hass):
            return []

        monkeypatch.setattr(config_flow, "async_app_discoveries", none)

        def suggested(result):
            return {
                str(marker): (marker.description or {}).get("suggested_value")
                for marker in result["data_schema"].schema
            }

        assert suggested(await flow.async_step_server(None)) == {CONF_SERVER_URL: None, CONF_SERVER_TOKEN: None}
        result = await flow.async_step_server({CONF_SERVER_URL: "http://other:8765", CONF_SERVER_TOKEN: "short"})
        assert result["errors"]
        assert suggested(result) == {CONF_SERVER_URL: "http://other:8765", CONF_SERVER_TOKEN: None}

    asyncio.run(run())
